# -*- coding: utf-8 -*-
"""训练 v2 —— 读新基座(trainsetv2) + 滚动窗口评估 + 胜率/收益指标
=====================================================================================
与 train_model.py / train_lgbm.py 的关系
--------------------------------------------------------------------------------
* **不替换、不修改** 它们。本脚本是"换基座"这条线的独立入口：
      train_model.py / train_lgbm.py  → 读 aipick.db（34 天 / 18.9 万行）
      train_v2.py                     → 读 trainsetv2（1,620 天 / 772 万行）
  两者产物目录隔离，模型文件名由 `--save-model` 显式指定，**默认不落盘**。

* 特征集**刻意保持 6 个不变**（`bid_change/bid_amount/bid_turnover/circ_mv/yesterday_chg/price`）
  —— 这样"换基座"与"加特征"对结果的影响可以**分开归因**（加特征留到下一步）。

三个必须知道的修正（来自 build_trainset_v2.py，此处只消费不重算）
--------------------------------------------------------------------------------
1. `price` = `daily_auc.m_price`（**9:25 竞价撮合价**，实测 == `daily.open` 98.8~100%）
   —— 旧口径 `price ← 猫爪 close`，历史回溯下是**当日收盘价** ⇒ 标签泄漏。
2. `is_limit_up` = `close >= pricelimit.up_limit`（**精确涨停价**）
   —— 自动覆盖 主板 10% / 创业板·科创 20% / **ST 5%** / 北交所 30%。
   旧口径 `pct_chg >= 9.8/19.8` 每年错标 ~1,341 只 ST 涨停。
3. 新增可交易收益 `ret = close/m_price - 1`（9:25 竞价买入 → 收盘卖出，%）
   —— 这是"用户实际能拿到的收益"，用于评估与过滤器寻优（**不参与训练**）。

为什么评估必须用滚动窗口
--------------------------------------------------------------------------------
34 天的旧基座用**单次 80/20 切分**，测试集只有 6~7 天 ⇒ AUC 基本靠运气。
本脚本默认 **扩张窗口滚动评估**（训练=该日之前全部，测试=该日），并在末尾做
**池化 AUC**（把所有测试日的分数拼起来算一次），比"逐日 AUC 求平均"稳得多。

指标口径（★ 与用户可见的"胜率"对齐）
--------------------------------------------------------------------------------
* `auc_limit`  模型本行（是否涨停）的 AUC
* `auc_ret`    "当日是否赚钱(ret>0)"的 AUC —— 用来**证伪**"改目标会更准"的臆测
* `hit@N`      选中 N 只里的**打板命中率** = mean(is_limit_up)
* `win@N`      **正收益率** = mean(ret > 0)   ← 页面口径
* `ret@N`      **平均收益** = mean(ret)       ← 寻优主目标（高胜率低赔率会在这里露馅）
* `nxt@N`      次日开盘溢价 mean(next_open_chg)
* `lift@N`     `hit@N` 相对当日全市场涨停率的倍数（衡量"选得比随机好多少"）

用法
--------------------------------------------------------------------------------
    # 滚动窗口评估（默认：最后 60 个交易日、每 5 天一测、扩张窗口、两个算法对拍）
    /opt/kuaixuan-venv/bin/python train_v2.py eval --trainset data/trainsetv2

    # 顺带导出样本外预测，供 filter_search.py 寻优
    /opt/kuaixuan-venv/bin/python train_v2.py eval --trainset data/trainsetv2 \
        --dump-oos out/oos_xgb.npz --dump-oos-algo xgb

    # 全量训练并落盘（模型文件名显式给出，绝不默认覆盖线上模型）
    /opt/kuaixuan-venv/bin/python train_v2.py final --trainset data/trainsetv2 \
        --algo lgbm --save-model /opt/kuaixuan/aipick/models/model_lgb_v2.txt

    # 单年冒烟（只读 2026，几十秒出结果）
    /opt/kuaixuan-venv/bin/python train_v2.py eval --trainset data/trainsetv2 \
        --limit-year 2026 --test-days 20 --step 5 --algos xgb
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_trainset_v2 import load_trainset  # noqa: E402  复用基座读取器（含 manifest 解析）

# ---------------------------------------------------------------- 特征与标签
# ★ 必须与 train_model.py / train_lgbm.py / predict_daily.py / ai_prompt 逐字一致
FEATURES = ["bid_change", "bid_amount", "bid_turnover", "circ_mv", "yesterday_chg", "price"]
TARGET = "is_limit_up"
RET = "ret"                     # 可交易收益(%)：9:25 竞价买入 → 当日收盘卖出

# ---------------------------------------------------------------- 估计器参数
# ★ 默认值 = **线上现状**（train_model.py / train_lgbm.py 当前写死的值），
#   目的是让"换基座前后"的两个模型可逐位对照，而不是顺手换参。
XGB_BASE = dict(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8,
                colsample_bytree=0.8, eval_metric="auc", random_state=42)
LGB_BASE = dict(objective="binary", n_estimators=300, learning_rate=0.02, num_leaves=7,
                min_child_samples=20, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                reg_lambda=1.0, max_bin=255, importance_type="gain", random_state=42)

# ★ 可选容量网格：基座从 34 天 → 1,620 天（×47.6），**容量必须重新校准**。
#   旧基座下 num_leaves=31 会过拟合（train_lgbm.py 注释里的教训），大基座下未必。
#   `--grid` 打开后按此表扫；不打开则只用 `-BASE`。
PARAM_GRID = [
    ("xgb-d4-lr05",  "xgb", dict(max_depth=4, learning_rate=0.05)),   # 线上现状
    ("xgb-d6-lr05",  "xgb", dict(max_depth=6, learning_rate=0.05)),
    ("xgb-d8-lr03",  "xgb", dict(max_depth=8, learning_rate=0.03)),
    ("lgb-l7-lr02",  "lgb", dict(num_leaves=7,  learning_rate=0.02)),  # 线上现状(小数据校准值)
    ("lgb-l31-lr02", "lgb", dict(num_leaves=31, learning_rate=0.02)),
    ("lgb-l63-lr03", "lgb", dict(num_leaves=63, learning_rate=0.03)),
]

# ---------------------------------------------------------------- 默认过滤器
# 与 predict_daily.py 逐字一致（页面默认规则）
DEFAULT_FILTER = dict(mv_min=30.0, mv_max=100.0, bid_amt_min=3000.0,
                      bid_chg_max=7.0, min_prob=0.5, topn=5)
# 注：topn 默认取 5 而非页面的 30 —— 评估用"每天真正会买的那几只"才有意义；
#     30 只全买不现实。页面展示口径另在 filter_search.py 里单独报。

# 注：与 FEATURES 有重叠，用 dict.fromkeys 去重（否则 DataFrame 出现同名列、
#      `df["circ_mv"]` 会返回两列的 DataFrame → 后续布尔运算广播报错）。
COLS_NEEDED = list(dict.fromkeys(
    ["trade_date", "symbol", TARGET, RET, "next_open_chg", "next_close_chg",
     "bid_change", "bid_amount", "circ_mv", "is_st"] + FEATURES))


# ==================================================================== 数据装载
def load_base(trainset, limit_year=None, verbose=True):
    """读新基座 → 清洗后的 DataFrame（按 trade_date 升序、行号连续）。"""
    if os.path.isdir(trainset):
        df = load_trainset(trainset)
    elif trainset.endswith((".npz", ".parquet")):
        df = load_trainset(os.path.dirname(trainset))
    else:
        raise SystemExit("--trainset 必须是 trainsetv2 目录或分片文件: %s" % trainset)
    if df.empty:
        raise SystemExit("基座为空: %s" % trainset)

    for c in ("trade_date", "symbol"):
        df[c] = df[c].astype("int64")
    if limit_year:
        y = int(limit_year)
        lo, hi = y * 10000 + 101, y * 10000 + 1231
        df = df[(df["trade_date"] >= lo) & (df["trade_date"] <= hi)].copy()

    # ★ 与 train_model.py 同口径清洗（保证与旧链路可比）
    n0 = len(df)
    df = df.dropna(subset=FEATURES)
    df = df[(df["bid_change"].abs() < 30) & (df["bid_amount"] > 0)]
    df = df[df[TARGET].notna()]
    if verbose:
        print("基座装载: %s 行 / %d 天 / %s ~ %s"
              % (format(len(df), ","), df["trade_date"].nunique(),
                 int(df["trade_date"].min()), int(df["trade_date"].max())))
        print("  清洗剔除 %s 行（特征缺失 / |竞价涨幅|>=30 / 竞价额<=0）"
              % format(n0 - len(df), ","))
        print("  涨停率 %.3f%%   可交易收益 ret>0 %.2f%%   ret 中位 %+.3f%%"
              % (df[TARGET].mean() * 100, (df[RET] > 0).mean() * 100, df[RET].median()))
    # 按日期排序一次，后续用 searchsorted 切片（避免每窗口全表布尔掩码）
    df = df.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    return df


# ==================================================================== 模型
def fit_model(algo, Xtr, ytr, params, n_jobs):
    spw = float((ytr == 0).sum()) / max(1, int((ytr == 1).sum()))
    if algo == "xgb":
        import xgboost as xgb
        p = dict(XGB_BASE); p.update(params)
        p.pop("n_jobs", None)
        m = xgb.XGBClassifier(scale_pos_weight=spw, use_label_encoder=False,
                              verbosity=0, n_jobs=n_jobs, **p)
    else:
        import lightgbm as lgb
        p = dict(LGB_BASE); p.update(params)
        m = lgb.LGBMClassifier(scale_pos_weight=spw, **p)
    m.fit(Xtr, ytr)
    return m, spw


def predict_proba(model, X, algo):
    """★ 唯一的概率出口。LightGBM Booster.predict 本身已是正类概率，不能再取 [:,1]。"""
    if algo == "lgb":
        return np.asarray(model.predict(X), dtype=float).reshape(-1)
    return model.predict_proba(X)[:, 1]


def feature_importance(model, algo):
    try:
        v = model.feature_importances_
        s = float(np.sum(v)) or 1.0
        return {f: float(x) / s for f, x in zip(FEATURES, v)}
    except Exception:
        return {}


def roc_auc(y, s):
    """零依赖 AUC（等价 sklearn.roc_auc_score(Mann-Whitney U)），避免额外依赖。"""
    y = np.asarray(y, dtype=bool)
    s = np.asarray(s, dtype=float)
    ok = np.isfinite(s)
    y, s = y[ok], s[ok]
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    ss = s[order]
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0      # 平均秩（1-based）
        i = j + 1
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


# ==================================================================== 指标
def apply_filter(frame, f):
    """按页面默认规则过滤（frame 需含 mv/amt/chg/p 列）。"""
    m = ((frame["mv"] >= f["mv_min"]) & (frame["mv"] <= f["mv_max"])
         & (frame["amt"] >= f["bid_amt_min"]) & (frame["chg"] <= f["bid_chg_max"])
         & (frame["p"] >= f["min_prob"]))
    return frame[m]


def block_metrics(te, proba, topn, do_filter, f):
    """一个测试**区块**内的指标。

    ★ 必须**逐日**先过滤、再在筛后池里取 TopN（与线上 `predict_daily.py` 一致）；
      绝不能把整个区块摊平后取全局 TopN —— 那会变成"在 30 天里挑最猛的 5 只"，
      与"每天买 5 只"完全不是一回事。
      最后对**日等权**平均（不是按股数加权），避免票多的日子主导结论。
    """
    frame = pd.DataFrame({
        "d": te["trade_date"].to_numpy(),
        "p": proba,
        "y": te[TARGET].to_numpy(dtype=float),
        "r": te[RET].to_numpy(dtype=float),
        "no": te["next_open_chg"].to_numpy(dtype=float),
        "mv": te["circ_mv"].to_numpy(dtype=float),
        "amt": te["bid_amount"].to_numpy(dtype=float),
        "chg": te["bid_change"].to_numpy(dtype=float),
    })
    rows = []
    for _, g in frame.groupby("d", sort=True):
        pool = apply_filter(g, f) if do_filter else g
        if len(pool) == 0:
            continue
        sel = pool.nlargest(min(topn, len(pool)), "p")
        base_hit = float(g["y"].mean())            # 当日全市场涨停率（作 lift 基准）
        rows.append({
            "n_pool": int(len(pool)),
            "n_pick": int(len(sel)),
            "hit": float(sel["y"].mean()),
            "win": float((sel["r"] > 0).mean()),
            "ret": float(sel["r"].mean()),
            "med_ret": float(sel["r"].median()),
            "nxt": float(sel["no"].mean()) if sel["no"].notna().any() else float("nan"),
            "base_hit": base_hit,
            "lift": float(sel["y"].mean() / base_hit) if base_hit > 0 else float("nan"),
        })
    if not rows:
        return None
    out = {"n_days_pool": len(rows)}
    for k in ("n_pool", "n_pick", "hit", "win", "ret", "med_ret", "nxt", "lift"):
        v = np.array([r[k] for r in rows if np.isfinite(r[k])], dtype=float)
        out[k] = float(v.mean()) if len(v) else float("nan")
    return out


def _agg(rows, key):
    v = np.array([r[key] for r in rows if r.get(key) is not None and np.isfinite(r.get(key, np.nan))],
                 dtype=float)
    if len(v) == 0:
        return float("nan")
    return float(v.mean())


def _std(rows, key):
    v = np.array([r[key] for r in rows if r.get(key) is not None and np.isfinite(r.get(key, np.nan))],
                 dtype=float)
    return float(v.std(ddof=1)) if len(v) > 1 else float("nan")


# ==================================================================== 滚动评估
def rolling_eval(df, configs, test_days, step, min_train_days, train_window, topn,
                 do_filter, filt, n_jobs, dump_oos=None, dump_oos_cfg=None, algo_filter=None,
                 test_block=1):
    """滚动评估。

    ★ `test_block` > 1 时，**一次拟合覆盖连续 `test_block` 个测试日**（区块测试）。
      为什么必须这样：训练成本 ∝ 训练集行数，而若"每测一天就重训一次"，
      想拿到 500 个样本外交易日就要重训 500 次（7.7M 行基座上约 10 分钟/次 ⇒ 83 小时）。
      改区块后，同样的成本换来 `拟合点数 × test_block` 个样本外交易日：

          拟合点 = uniq[-test_days:][::step]        共 ceil(test_days/step) 个
          样本外交易日 ≈ 拟合点数 × test_block

      取 `step >= test_block` 时各区块**不重叠**，池化 AUC 与样本外文件都不会有重复日。
    """
    td = df["trade_date"].to_numpy()
    uniq = np.unique(td)
    test_grid = list(uniq[-test_days:][::step])
    blk = max(1, int(test_block))
    print("\n滚动窗口: %d 个拟合点（最后 %d 个交易日，每 %d 天一测）× 每点测试 %d 天"
          " ⇒ 样本外约 %d 个交易日"
          % (len(test_grid), test_days, step, blk, len(test_grid) * blk))
    print("  训练窗口: %s | 过滤: %s | 选股口径: TopN=%d"
          % ("扩张(全部历史)" if train_window <= 0 else "固定滚动 %d 天" % train_window,
             "页面默认规则" if do_filter else "不过滤", topn))
    if test_grid:
        print("  测试区间: %d ~ %d" % (test_grid[0], uniq[-1]))

    per_window = []            # [{date, cfg, auc_limit, auc_ret, ...}]
    pooled = {c[0]: {"y": [], "p": [], "yr": [], "pr": []} for c in configs}
    oos_rows = []
    blk = max(1, int(test_block))

    for wi, d in enumerate(test_grid, 1):
        lo = int(np.searchsorted(td, d, "left"))
        if lo < min_train_days:
            print("  [skip] %d 训练样本不足(%d)" % (d, lo))
            continue
        # 测试区块 = 从 d 起的 blk 个交易日
        pos = int(np.searchsorted(uniq, d, "left"))
        end_day = uniq[min(len(uniq) - 1, pos + blk - 1)]
        hi = int(np.searchsorted(td, end_day, "right"))

        tr_start = 0
        if train_window > 0:
            cut = uniq[max(0, pos - train_window)]
            tr_start = int(np.searchsorted(td, cut, "left"))
        tr = df.iloc[tr_start:lo]
        te = df.iloc[lo:hi]
        if len(te) < 50 or te[TARGET].nunique() < 2:
            print("  [skip] %d 测试区块样本异常(n=%d)" % (d, len(te)))
            continue

        Xtr = tr[FEATURES].to_numpy(dtype=np.float32)
        ytr = tr[TARGET].to_numpy(dtype=np.int8)
        Xte = te[FEATURES].to_numpy(dtype=np.float32)
        line = "  [%2d/%2d] %d~%d  train=%-9s test=%-7s" % (
            wi, len(test_grid), d, end_day, format(len(tr), ","), format(len(te), ","))
        t0 = time.time()
        for name, algo, over in configs:
            if algo_filter and algo not in algo_filter:
                continue
            m, _ = fit_model(algo, Xtr, ytr, over, n_jobs)
            p = predict_proba(m, Xte, algo)
            row = {"date": int(d), "date_end": int(end_day), "cfg": name, "algo": algo,
                   "auc_limit": roc_auc(te[TARGET].to_numpy(), p),
                   "auc_ret": roc_auc((te[RET].to_numpy() > 0).astype(float), p)}
            met = block_metrics(te, p, topn, do_filter, filt)
            if met:
                row.update(met)
            per_window.append(row)
            pooled[name]["y"].append(te[TARGET].to_numpy())
            pooled[name]["p"].append(p)
            pooled[name]["yr"].append((te[RET].to_numpy() > 0).astype(float))
            pooled[name]["pr"].append(p)
            line += "  %s=%.4f" % (name[:9], row["auc_limit"])
            if dump_oos is not None and name == dump_oos_cfg:
                sub = te[COLS_NEEDED].copy()
                sub["p"] = p
                oos_rows.append(sub)
        print(line + "  (%.1fs)" % (time.time() - t0))

    # ---- 汇总 ----
    summary = {}
    for name, algo, _ in configs:
        if algo_filter and algo not in algo_filter:
            continue
        rows = [r for r in per_window if r["cfg"] == name]
        if not rows:
            continue
        P = pooled[name]
        summary[name] = {
            "algo": algo, "n_windows": len(rows),
            "auc_limit_mean": _agg(rows, "auc_limit"), "auc_limit_std": _std(rows, "auc_limit"),
            "auc_limit_pooled": roc_auc(np.concatenate(P["y"]), np.concatenate(P["p"])),
            "auc_ret_mean": _agg(rows, "auc_ret"), "auc_ret_pooled":
                roc_auc(np.concatenate(P["yr"]), np.concatenate(P["pr"])),
            "hit_mean": _agg(rows, "hit"), "hit_std": _std(rows, "hit"),
            "win_mean": _agg(rows, "win"), "ret_mean": _agg(rows, "ret"),
            "med_ret_mean": _agg(rows, "med_ret"), "nxt_mean": _agg(rows, "nxt"),
            "lift_mean": _agg(rows, "lift"),
            "n_pool_mean": _agg(rows, "n_pool"), "n_pick_mean": _agg(rows, "n_pick"),
        }

    if dump_oos is not None and oos_rows:
        out = pd.concat(oos_rows, ignore_index=True)
        os.makedirs(os.path.dirname(os.path.abspath(dump_oos)) or ".", exist_ok=True)
        if dump_oos.endswith(".parquet"):
            out.to_parquet(dump_oos, index=False, compression="zstd")
        else:
            np.savez_compressed(dump_oos, cols=np.array(out.columns, dtype=object),
                                data=out.to_numpy(dtype=np.float64))
        print("\n样本外预测已导出: %s  (%s 行)" % (dump_oos, format(len(out), ",")))
    return per_window, summary


def print_summary(summary, topn, do_filter):
    if not summary:
        print("没有可用结果"); return
    print("\n" + "=" * 108)
    print("滚动窗口汇总  |  选股 TopN=%d  |  %s"
          % (topn, "页面默认过滤器" if do_filter else "不过滤"))
    print("=" * 108)
    hdr = ("%-14s %6s %9s %9s %9s %8s %8s %8s %8s %8s"
           % ("配置", "窗口", "AUC涨停", "池化AUC", "AUC赚钱", "命中率", "正收益率", "平均收益", "次日溢价", "候选/日"))
    print(hdr)
    print("-" * 108)
    for k, s in sorted(summary.items(), key=lambda x: -(x[1]["auc_limit_pooled"] or 0)):
        print("%-14s %6d %9.4f %9.4f %9.4f %7.1f%% %7.1f%% %+7.2f%% %+7.2f%% %8.0f"
              % (k, s["n_windows"],
                 s["auc_limit_mean"] or float("nan"), s["auc_limit_pooled"] or float("nan"),
                 s["auc_ret_mean"] or float("nan"),
                 (s["hit_mean"] or 0) * 100, (s["win_mean"] or 0) * 100,
                 s["ret_mean"] or 0, s["nxt_mean"] or 0, s["n_pool_mean"] or 0))
    print("-" * 108)

    # 对拍
    names = list(summary)
    xgbs = [n for n in names if summary[n]["algo"] == "xgb"]
    lgbs = [n for n in names if summary[n]["algo"] == "lgb"]
    if xgbs and lgbs:
        bx = max(xgbs, key=lambda n: summary[n]["auc_limit_pooled"] or 0)
        bl = max(lgbs, key=lambda n: summary[n]["auc_limit_pooled"] or 0)
        print("对拍: %s 池化AUC %.4f  vs  %s 池化AUC %.4f   → 差 %+.4f（%s 领先）"
              % (bx, summary[bx]["auc_limit_pooled"], bl, summary[bl]["auc_limit_pooled"],
                 (summary[bx]["auc_limit_pooled"] or 0) - (summary[bl]["auc_limit_pooled"] or 0),
                 bx if (summary[bx]["auc_limit_pooled"] or 0) >= (summary[bl]["auc_limit_pooled"] or 0) else bl))
        print("     平均收益: %s %+.2f%%  vs  %s %+.2f%%"
              % (bx, summary[bx]["ret_mean"] or 0, bl, summary[bl]["ret_mean"] or 0))
    print("提示: AUC赚钱 ≈0.5 说明「当日是否赚钱」本身不可由竞价特征预测（不是模型不行）；"
          "命中率/平均收益才是选股口径的真实成绩。")


# ==================================================================== 全量训练
def train_final(df, algo, over, n_jobs, save_model=None, report_path=None, topn=5,
                do_filter=True, filt=None, dump_oos=None, oos_cfg=None):
    X = df[FEATURES].to_numpy(dtype=np.float32)
    y = df[TARGET].to_numpy(dtype=np.int8)
    t0 = time.time()
    m, spw = fit_model(algo, X, y, over, n_jobs)
    print("全量训练完成: %s 行 / %d 天 / %.1fs  (scale_pos_weight=%.2f)"
          % (format(len(X), ","), df["trade_date"].nunique(), time.time() - t0, spw))
    rep = {
        "algo": algo, "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_train": int(len(X)), "n_days": int(df["trade_date"].nunique()),
        "date_min": int(df["trade_date"].min()), "date_max": int(df["trade_date"].max()),
        "features": FEATURES, "target": TARGET,
        "params": {**(XGB_BASE if algo == "xgb" else LGB_BASE), **over},
        "scale_pos_weight": spw,
        "limit_rate": float(df[TARGET].mean()),
        "feature_importance": feature_importance(m, algo),
        "base_note": "trainsetv2: price=daily_auc.m_price(9:25竞价价); is_limit_up=close>=pricelimit.up_limit",
    }
    if save_model:
        os.makedirs(os.path.dirname(os.path.abspath(save_model)) or ".", exist_ok=True)
        tmp = save_model + ".tmp"
        if algo == "lgb":
            m.booster_.save_model(tmp)
        else:
            m.save_model(tmp)
        os.replace(tmp, save_model)          # 原子替换，避免服务读到半截模型
        rep["model_path"] = save_model
        print("模型已保存: %s" % save_model)
    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("训练报告已保存: %s" % report_path)
    return rep


# ==================================================================== CLI
def build_parser():
    p = argparse.ArgumentParser(description="训练 v2：新基座 + 滚动窗口评估 + 胜率/收益指标")
    p.add_argument("mode", choices=["eval", "final"], help="eval=滚动窗口评估; final=全量训练")
    p.add_argument("--trainset", default=os.path.join(HERE, "data", "trainsetv2"))
    p.add_argument("--algo", default="xgb", choices=["xgb", "lgb", "lgbm"],
                   help="final 模式用（lgbm=lgb 别名）")
    p.add_argument("--algos", default="xgb,lgb", help="eval 模式对拍哪些算法（逗号分隔）")
    p.add_argument("--grid", action="store_true",
                   help="eval 模式扫容量网格（PARAM_GRID），否则只跑线上现状参数")
    p.add_argument("--out-dir", default=os.path.join(HERE, "output", "v2"))
    p.add_argument("--report-name", default=None)
    p.add_argument("--test-days", type=int, default=60, help="取最后 N 个交易日做测试")
    p.add_argument("--step", type=int, default=5, help="每 N 天一个拟合点（应 >= --test-block）")
    p.add_argument("--test-block", type=int, default=1,
                   help="每个拟合点连续测试 N 天（>1 时大幅提升样本外天数/成本比；与 --step 取 >= 避免重叠）")
    p.add_argument("--min-train-days", type=int, default=500)
    p.add_argument("--train-window", type=int, default=0, help="0=扩张窗口; >0=固定滚动窗口天数")
    p.add_argument("--topn", type=int, default=5)
    p.add_argument("--no-filter", action="store_true", help="评估时不套页面默认过滤器")
    p.add_argument("--n-jobs", type=int, default=2)
    p.add_argument("--limit-year", type=int, default=None, help="只读某一年（冒烟）")
    p.add_argument("--dump-oos", default=None, help="导出样本外预测（.npz/.parquet）供过滤器寻优")
    p.add_argument("--dump-oos-cfg", default=None, help="导出哪个配置的样本外预测（默认取最好的）")
    p.add_argument("--save-model", default=None, help="final 模式：模型落盘路径（默认不落盘）")
    # 过滤器参数（评估与寻优共用口径）
    p.add_argument("--mv-min", type=float, default=DEFAULT_FILTER["mv_min"])
    p.add_argument("--mv-max", type=float, default=DEFAULT_FILTER["mv_max"])
    p.add_argument("--bid-amt-min", type=float, default=DEFAULT_FILTER["bid_amt_min"])
    p.add_argument("--bid-chg-max", type=float, default=DEFAULT_FILTER["bid_chg_max"])
    p.add_argument("--min-prob", type=float, default=DEFAULT_FILTER["min_prob"])
    return p


def main():
    a = build_parser().parse_args()
    algo_filter = [x.strip() for x in a.algos.split(",") if x.strip()] if a.algos else None
    filt = dict(DEFAULT_FILTER, mv_min=a.mv_min, mv_max=a.mv_max, bid_amt_min=a.bid_amt_min,
                bid_chg_max=a.bid_chg_max, min_prob=a.min_prob, topn=a.topn)
    algo = "lgb" if a.algo in ("lgb", "lgbm") else "xgb"

    print("=" * 108)
    print("训练 v2 | mode=%s | 基座=%s" % (a.mode, a.trainset))
    print("=" * 108)
    df = load_base(a.trainset, limit_year=a.limit_year)
    if len(df) < 1000:
        print("⚠️ 基座样本不足，放弃"); return 1

    if a.mode == "final":
        os.makedirs(a.out_dir, exist_ok=True)
        rep = train_final(df, algo, {}, a.n_jobs, save_model=a.save_model,
                          report_path=os.path.join(a.out_dir, a.report_name or
                                                   ("train_report_v2_%s.json" % algo)),
                          topn=a.topn, do_filter=not a.no_filter, filt=filt)
        print(json.dumps({k: v for k, v in rep.items() if k != "feature_importance"},
                         ensure_ascii=False, indent=2))
        return 0

    # ---- eval ----
    if a.grid:
        configs = [(n, al, ov) for n, al, ov in PARAM_GRID
                   if (algo_filter is None or al in algo_filter)]
    else:
        configs = []
        if algo_filter is None or "xgb" in algo_filter:
            configs.append(("xgb-now", "xgb", {}))
        if algo_filter is None or "lgb" in algo_filter:
            configs.append(("lgb-now", "lgb", {}))
    if not configs:
        print("⚠️ 没有可用配置"); return 1

    dump_cfg = a.dump_oos_cfg
    if a.dump_oos and not dump_cfg:
        # 未指定 → 取第一个配置（与 --algos 顺序一致，通常就是 xgb）
        dump_cfg = configs[0][0]
        print("样本外预测将导出配置: %s" % dump_cfg)
    per_window, summary = rolling_eval(
        df, configs, a.test_days, a.step, a.min_train_days, a.train_window, a.topn,
        not a.no_filter, filt, a.n_jobs, dump_oos=a.dump_oos, dump_oos_cfg=dump_cfg,
        algo_filter=algo_filter, test_block=a.test_block)
    print_summary(summary, a.topn, not a.no_filter)

    os.makedirs(a.out_dir, exist_ok=True)
    rp = os.path.join(a.out_dir, a.report_name or "train_v2_eval.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump({
            "evaluated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "base": {"rows": int(len(df)), "days": int(df["trade_date"].nunique()),
                     "date_min": int(df["trade_date"].min()),
                     "date_max": int(df["trade_date"].max())},
            "setup": {"test_days": a.test_days, "step": a.step, "test_block": a.test_block,
                      "train_window": a.train_window, "topn": a.topn,
                      "use_filter": not a.no_filter, "filter": filt,
                      "features": FEATURES},
            "summary": summary, "per_window": per_window,
        }, f, ensure_ascii=False, indent=2)
    print("\n评估报告已保存: %s" % rp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
