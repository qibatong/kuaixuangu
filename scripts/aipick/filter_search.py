# -*- coding: utf-8 -*-
"""过滤器重寻优 —— 滚动回测，目标函数 = 命中率 × 平均收益
=====================================================================================
为什么必须重寻
--------------------------------------------------------------------------------
现状页面默认过滤器（`predict_daily.py`）：
    circ_mv ∈ [30, 100] 亿  ·  bid_amount ≥ 3000 万  ·  bid_change ≤ 7%  ·  ai_prob ≥ 0.5  ·  TopN=30

实测两个问题：
1. **`bid_amount ≥ 3000` 只覆盖 ~1.5% 的股票**（`auc_amt` 单位是**元**，全市场中位仅 ~34 万元）
   ⇒ 每天候选被压到 ~20 只，**这条阈值对结果的支配力比模型还大**。
2. **纯按"命中率"寻优会踩「高胜率低赔率」坑**：套过滤后取竞价涨最高的 1%，
   `P(赚钱)=60.7%` 但**平均收益 −0.60%**（追高买在最高点，涨停了也亏）。

所以目标函数用 **命中率 × 平均收益**（同时报两个分量，避免只看一个被带偏）。

方法（避免把网格搜出的噪声当结论）
--------------------------------------------------------------------------------
1. 输入是 `train_v2.py --dump-oos` 导出的**样本外预测**（滚动窗口，模型没见过这些天）；
2. 全部计算用 `np.bincount` 按日聚合 —— **每日等权**（一天一票），不是按样本数加权，
   否则样本多的日子会主导结果；
3. `--holdout-frac 0.5` 默认把 OOS 日期**按时间前后对半切**：前半段寻优、后半段验证，
   报告两个数。**只有两段同向的组合才可信**（防止把噪声当规律）。

用法
--------------------------------------------------------------------------------
    /opt/kuaixuan-venv/bin/python filter_search.py --oos out/oos_xgb.npz --top 25
    /opt/kuaixuan-venv/bin/python filter_search.py --oos out/oos_lgb.npz --objective ret
"""
import argparse
import itertools
import json
import os
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 搜索网格（粗网格，防过拟合）
GRID = {
    "mv_min":      [0.0, 20.0, 30.0, 50.0, 80.0],
    "mv_max":      [80.0, 100.0, 200.0, 1e9],
    "bid_amt_min": [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0, 10000.0],
    "bid_chg_max": [3.0, 5.0, 7.0, 9.5, 100.0],
    "min_prob":    [0.0, 0.3, 0.5, 0.6, 0.7],
    "topn":        [3, 5, 10, 20, 30],       # 30 = 线上 predict_daily.py 的 head(30)
}
# 线上现状（predict_daily.py:288-293 逐字摘录）：
#   mv ∈ [30, 100] 闭区间 | bid_amount ≥ 3000 | bid_change ≤ 7 | ai_prob ≥ 0.5 | head(30)
# 注：`ai_prob >= 0.5` 在脚本里是**硬编码**的（未参数化）；前端可通过 json 的 `all`
#     字段用自定义规则覆盖，故 `min_prob` 仍是可调旋钮，但要改默认值需动前端。
CURRENT = dict(mv_min=30.0, mv_max=100.0, bid_amt_min=3000.0, bid_chg_max=7.0,
               min_prob=0.5, topn=30)


# ==================================================================== 装载
def load_oos(path):
    """读样本外预测 → dict of numpy arrays（已按日期排序）。"""
    if path.endswith(".parquet"):
        import pandas as pd
        df = pd.read_parquet(path)
    else:
        z = np.load(path, allow_pickle=True)
        cols = [str(c) for c in list(z["cols"])]
        import pandas as pd
        df = pd.DataFrame(z["data"], columns=cols)
    need = ["trade_date", "p", "is_limit_up", "ret", "bid_amount", "circ_mv", "bid_change"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise SystemExit("样本外文件缺列: %s（实际列 %s）" % (miss, list(df.columns)))
    # 防御：样本外文件若含重复列名（列清单由 COLS_NEEDED 拼出，曾漏去重），
    # 会让 df["circ_mv"] 返回多列 DataFrame → 布尔运算广播炸掉。这里先折叠。
    df = df.loc[:, ~df.columns.duplicated()]
    if "next_open_chg" not in df.columns:
        df["next_open_chg"] = np.nan
    df = df.dropna(subset=["p", "is_limit_up", "ret", "bid_amount", "circ_mv", "bid_change"])
    # npz 往返会把 trade_date 变成 float64（20260105.0）→ 取整，便于阅读与排序
    df["trade_date"] = df["trade_date"].astype("int64")
    df = df.sort_values(["trade_date", "p"], ascending=[True, False]).reset_index(drop=True)
    print("样本外: %s 行 / %d 天 / %s ~ %s"
          % (format(len(df), ","), df["trade_date"].nunique(),
             int(df["trade_date"].min()), int(df["trade_date"].max())))
    return df


def prep(df):
    """预计算日期索引与「每日首行的全局位置」（一次），后续网格只做向量化掩码 + bincount。"""
    import pandas as pd
    dcode, dunits = pd.factorize(df["trade_date"].to_numpy())
    day_first = np.searchsorted(dcode, np.arange(len(dunits)), side="left")
    return {
        "d": dcode, "n_days": len(dunits), "dates": dunits, "day_first": day_first,
        "p": df["p"].to_numpy(dtype=np.float64),
        "y": df["is_limit_up"].to_numpy(dtype=np.float64),
        "r": df["ret"].to_numpy(dtype=np.float64),
        "pos": (df["ret"].to_numpy(dtype=np.float64) > 0).astype(np.float64),
        "no": df["next_open_chg"].to_numpy(dtype=np.float64),
        "mv": df["circ_mv"].to_numpy(dtype=np.float64),
        "amt": df["bid_amount"].to_numpy(dtype=np.float64),
        "chg": df["bid_change"].to_numpy(dtype=np.float64),
    }


def evaluate(A, mask, topn, idx=None):
    """每日等权聚合。

    ★ 选股口径必须与线上 `predict_daily.py::predict()` 逐字一致：
          先按过滤器筛 → **在筛完的池子里**按概率降序取前 TopN。
      （不是"全市场前 TopN ∩ 过滤器" —— 那会让过滤器变成"从已选出的票里再剔"，
        过滤越紧越选不出票，与线上行为完全不同。）
      实现：输入行序已按 (日期, 概率降序) 排好，故「筛后池内的名次」=
            (当日掩码累计计数 − 该日之前的掩码累计计数)。O(n) 向量化。
    idx: 日期子集（用于前后半段验证）
    """
    mask = np.asarray(mask, dtype=bool)
    cum = np.cumsum(mask)
    base = (cum[A["day_first"]] - mask[A["day_first"]])[A["d"]]
    sel = mask & ((cum - base) <= topn)          # 筛后池内 TopN
    d = A["d"]
    if idx is not None:
        sel = sel & np.isin(d, idx)
    cnt = np.bincount(d[sel], minlength=A["n_days"]).astype(np.float64)
    ok = cnt > 0
    if not ok.any():
        return None
    n_cand = np.bincount(d[mask], minlength=A["n_days"]).astype(np.float64)
    sy = np.bincount(d[sel], weights=A["y"][sel], minlength=A["n_days"])
    sp = np.bincount(d[sel], weights=A["pos"][sel], minlength=A["n_days"])
    sr = np.bincount(d[sel], weights=A["r"][sel], minlength=A["n_days"])
    no_ok = np.isfinite(A["no"][sel])
    if no_ok.any():
        sn = np.bincount(d[sel][no_ok], weights=A["no"][sel][no_ok], minlength=A["n_days"])
        cn = np.bincount(d[sel][no_ok], minlength=A["n_days"]).astype(np.float64)
        nxt = sn[ok] / np.maximum(cn[ok], 1)
    else:
        nxt = np.full(int(ok.sum()), np.nan)
    hit = sy[ok] / cnt[ok]
    win = sp[ok] / cnt[ok]
    ret = sr[ok] / cnt[ok]
    return {
        "days": int(ok.sum()), "coverage": float(ok.sum() / A["n_days"]),
        "picks_per_day": float(cnt[ok].mean()),
        "cand_per_day": float(n_cand[ok].mean()),
        "hit": float(hit.mean()), "win": float(win.mean()), "ret": float(ret.mean()),
        "ret_std": float(ret.std(ddof=1)) if len(ret) > 1 else float("nan"),
        "nxt": float(nxt.mean()) if len(nxt) else float("nan"),
        # ★ 用户要求的寻优目标
        "score": float(hit.mean() * ret.mean()),
        # 日频夏普式指标（每日等权、无风险利率取 0）：ret/波动
        "sharpe": float(ret.mean() / ret.std(ddof=1)) if len(ret) > 1 and ret.std(ddof=1) > 0 else float("nan"),
    }


def search(A, grid, objective, min_coverage=0.8):
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print("网格组合: %s = %s 条" % (" × ".join("%s(%d)" % (k, len(grid[k])) for k in keys),
                                    format(len(combos), ",")))
    out = []
    for i, vals in enumerate(combos, 1):
        cfg = dict(zip(keys, vals))
        if cfg["mv_max"] <= cfg["mv_min"]:
            continue
        # ★ 上界用 `<=`：必须与线上 predict_daily.py 的过滤器（以及本函数外的基线口径）
        #   完全一致，否则「寻优结果 vs 现状基线」不可比。
        mask = ((A["mv"] >= cfg["mv_min"]) & (A["mv"] <= cfg["mv_max"])
                & (A["amt"] >= cfg["bid_amt_min"]) & (A["chg"] <= cfg["bid_chg_max"])
                & (A["p"] >= cfg["min_prob"]))
        res = evaluate(A, mask, cfg["topn"])
        if res is None or res["coverage"] < min_coverage:
            continue          # 覆盖不了多数交易日 = 空跑多，直接淘汰
        res = dict(cfg, **res)
        out.append(res)
        if i % 400 == 0:
            print("  ...%s/%s" % (format(i, ","), format(len(combos), ",")), flush=True)
    return out


def show(rows, title, n=20):
    print("\n" + "=" * 132)
    print(title)
    print("=" * 132)
    print("%-3s %7s %7s %7s %7s %7s %6s %7s %7s %8s %8s %7s"
          % ("#", "市值下限", "市值上限", "竞价额", "涨幅上限", "概率下限", "TopN",
             "候选/日", "命中率", "正收益率", "平均收益", "得分"))
    print("-" * 132)
    for i, r in enumerate(rows[:n], 1):
        print("%-3d %8.0f %8s %8.0f %8.1f %8.2f %6d %8.1f %6.1f%% %7.1f%% %+8.2f%% %+7.3f"
              % (i, r["mv_min"],
                 ("∞" if r["mv_max"] >= 1e8 else "%.0f" % r["mv_max"]),
                 r["bid_amt_min"], r["bid_chg_max"], r["min_prob"], r["topn"],
                 r.get("cand_per_day", float("nan")),
                 r["hit"] * 100, r["win"] * 100, r["ret"], r["score"]))
    print("-" * 132)


def main():
    ap = argparse.ArgumentParser(description="过滤器重寻优（滚动回测，命中率 × 平均收益）")
    ap.add_argument("--oos", required=True, help="train_v2.py --dump-oos 导出的样本外预测")
    ap.add_argument("--objective", default="score", choices=["score", "ret", "hit", "sharpe"],
                    help="寻优目标：score=命中率×平均收益（默认）| ret=平均收益 | hit=命中率 | sharpe")
    ap.add_argument("--top", type=int, default=20, help="展示前 N 个组合")
    ap.add_argument("--holdout-frac", type=float, default=0.5,
                    help="按时间前后切分比例：前半寻优、后半验证；0=不做验证")
    ap.add_argument("--min-coverage", type=float, default=0.8)
    ap.add_argument("--out", default=None, help="结果 JSON 落盘路径")
    a = ap.parse_args()

    df = load_oos(a.oos)
    A = prep(df)
    dates = list(A["dates"])
    print("日期数: %d" % A["n_days"])

    # ---- 分段（按时间前后）----
    if a.holdout_frac and 0 < a.holdout_frac < 1:
        k = int(A["n_days"] * a.holdout_frac)
        idx_a = np.arange(0, k)          # 前半：寻优
        idx_b = np.arange(k, A["n_days"])  # 后半：验证
        print("寻优段 %s ~ %s (%d 天) | 验证段 %s ~ %s (%d 天)"
              % (dates[0], dates[k - 1], k, dates[k], dates[-1], A["n_days"] - k))
    else:
        idx_a = np.arange(0, A["n_days"]); idx_b = None
        print("不做前后切分（全场寻优）")

    # ---- 现状基线 ----
    print("\n[基线] 线上现状过滤器（市值 30~100 亿 / 竞价额≥3000 万 / 涨幅≤7% / 概率≥0.5）")
    base_mask = ((A["mv"] >= CURRENT["mv_min"]) & (A["mv"] <= CURRENT["mv_max"])
                 & (A["amt"] >= CURRENT["bid_amt_min"]) & (A["chg"] <= CURRENT["bid_chg_max"])
                 & (A["p"] >= CURRENT["min_prob"]))
    for tn in (5, 20, 30):
        r_all = evaluate(A, base_mask, tn)
        r_a = evaluate(A, base_mask, tn, idx_a)
        r_b = evaluate(A, base_mask, tn, idx_b) if idx_b is not None else None
        if r_all:
            print("  TopN=%-3d 覆盖 %.0f%% 日 | 筛后候选 %.0f 只/日 | 命中率 %.1f%% | 正收益率 %.1f%% | "
                  "平均收益 %+.2f%% | 得分 %+.3f%s"
                  % (tn, r_all["coverage"] * 100, r_all["cand_per_day"], r_all["hit"] * 100,
                     r_all["win"] * 100, r_all["ret"], r_all["score"],
                     ("   [前半 %+.2f%% / 后半 %+.2f%%]"
                      % (r_a["ret"] if r_a else float("nan"), r_b["ret"] if r_b else float("nan")))
                     if r_a and r_b else ""))

    # ---- 网格搜索 ----
    rows_a = search(A, GRID, a.objective, a.min_coverage)
    if not rows_a:
        print("⚠️ 没有满足覆盖率的组合"); return 1

    # 在验证段上补算
    for r in rows_a:
        mask = ((A["mv"] >= r["mv_min"]) & (A["mv"] <= r["mv_max"])
                & (A["amt"] >= r["bid_amt_min"]) & (A["chg"] <= r["bid_chg_max"])
                & (A["p"] >= r["min_prob"]))
        rb = evaluate(A, mask, r["topn"], idx_b) if idx_b is not None else None
        ra = evaluate(A, mask, r["topn"], idx_a)
        r["_holdout"] = rb
        r["_insample"] = ra
        r["ret_holdout"] = rb["ret"] if rb else float("nan")
        r["hit_holdout"] = rb["hit"] if rb else float("nan")
        r["score_holdout"] = rb["score"] if rb else float("nan")

    key = {"score": "score", "ret": "ret", "hit": "hit", "sharpe": "sharpe"}[a.objective]
    rows_a.sort(key=lambda r: -(r[key] if np.isfinite(r[key]) else -9e9))
    show(rows_a, "寻优段 Top%d（按 %s 排序）" % (a.top, a.objective), a.top)

    # ★ 只有两段同向才可信
    if idx_b is not None:
        both = [r for r in rows_a[:max(a.top * 3, 60)]
                if np.isfinite(r["ret_holdout"]) and r["ret_holdout"] > 0 and r["ret"] > 0
                and r["hit_holdout"] > 0]
        both.sort(key=lambda r: -r["score_holdout"])
        print("\n" + "=" * 122)
        print("★ 两段同向（寻优段与验证段平均收益均为正）Top%d —— 只有这些才值得考虑" % a.top)
        print("=" * 122)
        if not both:
            print("  ⚠️ 没有任何组合在两段上都为正 → 说明网格里的优势**不可复现**，"
                  "结论应是「保持现状 + 只调整明显失准的阈值」，不要照抄最优组合。")
        else:
            print("%-3s %6s %7s %7s %7s %7s %7s %6s %6s %7s %7s %7s"
                  % ("#", "市值下限", "市值上限", "竞价额", "涨幅上限", "概率下限", "TopN",
                     "命中率", "收益率", "平均收益", "验证段收益", "验证段命中"))
            print("-" * 122)
            for i, r in enumerate(both[:a.top], 1):
                print("%-3d %7.0f %8s %8.0f %8.1f %8.2f %7d %5.1f%% %6.1f%% %+7.2f%% %+8.2f%% %8.1f%%"
                      % (i, r["mv_min"], ("∞" if r["mv_max"] >= 1e8 else "%.0f" % r["mv_max"]),
                         r["bid_amt_min"], r["bid_chg_max"], r["min_prob"], r["topn"],
                         r["hit"] * 100, r["win"] * 100, r["ret"], r["ret_holdout"],
                         r["hit_holdout"] * 100))
        print("-" * 122)

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "oos": a.oos, "objective": a.objective, "grid": GRID,
                       "current": CURRENT,
                       "baseline_per_topn": {
                           str(tn): evaluate(A, base_mask, tn) for tn in (3, 5, 10, 20, 30)},
                       "top": [{k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                                for k, v in r.items() if not k.startswith("_")}
                               for r in rows_a[:200]]},
                      f, ensure_ascii=False, indent=2)
        print("\n结果已保存: %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
