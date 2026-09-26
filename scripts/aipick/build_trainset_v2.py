# -*- coding: utf-8 -*-
"""造训练基座 v2 —— 方案 A：daily_auc 主干 + pricelimit 精确标签 + 1,620 天长历史
=================================================================================
背景（详见 lgbm-deploy/PLAN-训练基座切换.md）
--------------------------------------------------------------------------------
* 现状：生产实际只用了 **34 天 / 189,252 行**（`_select_training_frame` 只留真实采集日）
* 本脚本：`daily_auc`(2018–2026) ∩ `pricelimit`(2020–2026) = **1,620 天 / 774 万行**
* 三个修正：
 1. **`price` ← `daily_auc.m_price`**（9:25 竞价撮合价；实测与 `daily.open` 一致度 100%）
    —— 旧口径 `price ← 猫爪 close` 是**当日收盘价** → 历史回溯下为未来信息（泄漏）
 2. **标签 ← `pricelimit` 精确涨停价**：`is_limit_up = 1 if close >= up_limit - 1e-4`
    —— 自动覆盖主板 10% / 创业板科创板 20% / **ST 5%** / 北交所 30%，不再硬编码前缀
    （现状 `pct_chg>=9.8/19.8` 每年错标 ~1,341 只 ST 涨停）
 3. **新增可交易收益 `ret = close/m_price - 1`**（9:25 买入 → 收盘卖出），供评估/寻优用
* `yesterday_chg` 取**前一交易日** pct_chg（跨年用 carry 接续），无泄漏
* 本脚本**不引入任何新特征**（第一步刻意保持特征集不变，便于把"换基座"与"加特征"分开归因）

用法
--------------------------------------------------------------------------------
    /usr/bin/python3 build_trainset_v2.py --years 2020-2026 --out-dir data/trainsetv2
    /usr/bin/python3 build_trainset_v2.py --years 2026 --out-dir /tmp/smoke        # 冒烟

产出：`<out-dir>/trainsetv2_<年>.parquet`（有 pyarrow）或 `.npz`（否则），外加 `manifest.json`。
⚠️ 只读 `meoz-data/` 原始 ndjson.gz，不修改任何采集数据。
"""
import argparse
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, "..", "meoz-data"))

AUC_KEEP = ["m_price", "auc_pct_chg", "auc_amt", "auc_turnover", "name"]
AUC_RAW = {"name"}                       # 原样保留（字符串），不转 float
DAY_KEEP = ["close", "pct_chg", "open"]
PL_KEEP = ["up_limit"]
VAL_KEEP = ["circ_mv"]

# 退坡口径（仅用于 pricelimit 缺失的年份/个股）
LIMIT_MAIN, LIMIT_GEM, LIMIT_ST = 9.8, 19.8, 4.8
# 涨跌幅合理范围（与生产 meoz_source 同口径）
CHG_MIN, CHG_MAX = -35.0, 35.0

OUT_COLS = ["trade_date", "symbol", "bid_change", "bid_amount", "bid_turnover",
            "circ_mv", "price", "yesterday_chg", "is_limit_up",
            "close_chg", "ret", "next_open_chg", "next_close_chg", "is_st"]


def _n(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_days(kind, year, keep, raw=()):
    """→ {date: {symbol: tuple(values in `keep` order)}}；`raw` 中的字段原样保留字符串。"""
    out = {}
    p = os.path.join(BASE, kind, "%s-%s.ndjson.gz" % (kind, year))
    if not os.path.exists(p):
        return out
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            d = str(r.get("date") or r.get("tradedate") or "")
            fl, items = r.get("fields") or [], r.get("items") or []
            if not d or not items:
                continue
            try:
                idx = [fl.index(k) for k in keep]
                si = fl.index("symbol")
            except ValueError:
                continue
            israw = [k in raw for k in keep]
            day = {}
            for it in items:
                day[str(it[si])] = tuple(it[i] if israw[j] else _n(it[i])
                                         for j, i in enumerate(idx))
            out[d] = day
    return out


def build_year(year, carry):
    """返回 (DataFrame, 该年最后一个交易日的 {symbol: pct_chg})"""
    auc = load_days("daily_auc", year, AUC_KEEP, raw=AUC_RAW)
    day = load_days("daily", year, DAY_KEEP)
    pl = load_days("pricelimit", year, PL_KEEP)
    val = load_days("valuation", year, VAL_KEEP)
    if not auc:
        print("  [skip] %s 无 daily_auc" % year)
        return None, carry

    dates = sorted(d for d in auc if d in day)
    cols = {k: [] for k in OUT_COLS}
    prev = dict(carry)                       # symbol -> 前一交易日 pct_chg
    for d in dates:
        ad, dd, pd_, vd = auc[d], day[d], pl.get(d, {}), val.get(d, {})
        # 次日 open/close（相对**今日 close**），供"次日溢价"评估
        nxt = None
        i = dates.index(d)
        if i + 1 < len(dates):
            nxt = day.get(dates[i + 1], {})
        for s, (mp, ac, amt, trn, nm) in ad.items():
            dr = dd.get(s)
            if dr is None or mp is None or mp <= 0 or dr[0] is None:
                continue
            close, pchg, op = dr[0], dr[1], dr[2]
            v = vd.get(s)
            mv = v[0] / 1e8 if (v and v[0]) else None
            # ---- 标签：精确涨停价优先，缺失退化到代码前缀规则 ----
            u = pd_.get(s)
            up = u[0] if u else None
            if up is not None and up > 0:
                lab = 1 if close >= up - 1e-4 else 0
            else:
                lim = LIMIT_GEM if s.startswith(("30", "68")) else LIMIT_MAIN
                lab = 1 if (pchg is not None and pchg >= lim - 0.2) else 0
            nms = str(nm or "").upper()
            nr = nxt.get(s) if nxt else None
            cols["trade_date"].append(int(d))
            cols["symbol"].append(int(s) if s.isdigit() else -1)
            cols["bid_change"].append(ac)
            cols["bid_amount"].append(amt / 1e4 if amt is not None else None)
            cols["bid_turnover"].append(trn)
            cols["circ_mv"].append(mv)
            cols["price"].append(mp)                                   # ★修正：竞价价，不是收盘价
            cols["yesterday_chg"].append(prev.get(s))
            cols["is_limit_up"].append(lab)
            cols["close_chg"].append(pchg)
            cols["ret"].append((close / mp - 1) * 100)
            cols["next_open_chg"].append((nr[2] / close - 1) * 100 if (nr and nr[2]) else None)
            cols["next_close_chg"].append((nr[0] / close - 1) * 100 if (nr and nr[0]) else None)
            cols["is_st"].append(1 if nms.startswith(("*ST", "ST")) else 0)
        prev = {s: dr[1] for s, dr in dd.items() if dr[1] is not None}

    df = pd.DataFrame(cols)
    df["trade_date"] = df["trade_date"].astype("int32")
    df["symbol"] = df["symbol"].astype("int32")
    for c in OUT_COLS:
        if c not in ("trade_date", "symbol"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    df["is_limit_up"] = df["is_limit_up"].astype("int8")
    df["is_st"] = df["is_st"].astype("int8")

    # ---- 范围护栏（与生产 `meoz_source.to_features` 同口径，防脏值污染训练）----
    #  实测坑：新股上市首日/北交所会出现 yesterday_chg=683%、742% 这种脏值；
    #  生产侧 CHG_MIN/CHG_MAX = ±35，超范围整行丢弃 / yesterday_chg 置 NaN。
    n0 = len(df)
    chg_ok = df["bid_change"].between(CHG_MIN, CHG_MAX) | df["bid_change"].isna()
    pct_ok = df["close_chg"].between(CHG_MIN, CHG_MAX) | df["close_chg"].isna()
    df = df[chg_ok & pct_ok].copy()
    bad_y = ~(df["yesterday_chg"].between(CHG_MIN, CHG_MAX) | df["yesterday_chg"].isna())
    if bad_y.any():
        df.loc[bad_y, "yesterday_chg"] = np.nan
    if n0 - len(df):
        print("   [范围护栏] 丢弃越界行 %s（竞价涨幅/收盘涨幅 超出 ±%g）"
              % (format(n0 - len(df), ","), CHG_MAX))
    return df, prev


def save(df, out_dir, year):
    os.makedirs(out_dir, exist_ok=True)
    try:
        import pyarrow  # noqa: F401
        path = os.path.join(out_dir, "trainsetv2_%s.parquet" % year)
        df.to_parquet(path, index=False, compression="zstd")
    except Exception as e:                                   # noqa: BLE001
        print("   （无 pyarrow，降级 npz：%s）" % e)
        path = os.path.join(out_dir, "trainsetv2_%s.npz" % year)
        # ★ trade_date/symbol 用 int32（float32 存不下 20260924 这种 8 位数，会丢精度）
        np.savez_compressed(
            path,
            cols=np.array(OUT_COLS, dtype=object),
            trade_date=df["trade_date"].to_numpy(dtype="int32"),
            symbol=df["symbol"].to_numpy(dtype="int32"),
            data=df[[c for c in OUT_COLS if c not in ("trade_date", "symbol")]]
                 .to_numpy(dtype="float32"),
        )
    print("   → %s  (%.1f MB)" % (path, os.path.getsize(path) / 1e6))
    return path


def load_trainset(out_dir):
    """读回全部 trainsetv2_* 分片 → 单个 DataFrame（供训练脚本与校验复用）。"""
    import glob
    frames = []
    for p in sorted(glob.glob(os.path.join(out_dir, "trainsetv2_*"))):
        if p.endswith(".parquet"):
            frames.append(pd.read_parquet(p))
        elif p.endswith(".npz"):
            z = np.load(p, allow_pickle=True)
            cols = [c for c in list(z["cols"]) if c not in ("trade_date", "symbol")]
            d = pd.DataFrame(z["data"], columns=cols)
            d.insert(0, "trade_date", z["trade_date"])
            d.insert(1, "symbol", z["symbol"])
            frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def verify(out_dir):
    df = load_trainset(out_dir)
    if df.empty:
        print("没有可校验的分片"); return
    print("读回 %s 行 / %d 天 / %s ~ %s"
          % (format(len(df), ","), df["trade_date"].nunique(),
             df["trade_date"].min(), df["trade_date"].max()))
    print("  列: %s" % list(df.columns))
    f = df.dropna(subset=["bid_change", "bid_amount", "bid_turnover", "circ_mv",
                          "yesterday_chg", "price"])
    print("  6 特征齐全: %s 行 (%.1f%%)" % (format(len(f), ","), len(f) / len(df) * 100))
    print("  涨停率 %.2f%%   可交易收益 ret>0 占比 %.2f%%   ret 中位 %+.2f%%"
          % (df["is_limit_up"].mean() * 100, (df["ret"] > 0).mean() * 100, df["ret"].median()))
    print("  is_st 股占比 %.2f%%；其中涨停 %s 只"
          % (df["is_st"].mean() * 100, format(int(df.loc[df["is_st"] == 1, "is_limit_up"].sum()), ",")))
    print("\n  样例（最近 3 行）:")
    print(df.tail(3).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2020-2026")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "data", "trainsetv2"))
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args()
    if a.verify_only:
        verify(a.out_dir); return 0
    years = ([str(y) for y in range(int(a.years.split("-")[0]), int(a.years.split("-")[1]) + 1)]
             if "-" in a.years else [x.strip() for x in a.years.split(",")])
    print("窗口 %s → %s" % (years, a.out_dir))

    carry, manifest, tot = {}, [], 0
    for y in years:
        df, carry = build_year(y, carry)
        if df is None or df.empty:
            continue
        lab_src = "精确涨停价" if y >= "2020" else "退化规则"
        print("  %s: %s 行 / %d 天 | 涨停率 %5.2f%% (%s) | 涨停里 pct_chg<9.5%% 的占 %.0f%% (ST 类)"
              % (y, format(len(df), ","), df["trade_date"].nunique(),
                 df["is_limit_up"].mean() * 100, lab_src,
                 (df.loc[df["is_limit_up"] == 1, "close_chg"] < 9.5).mean() * 100))
        p = save(df, a.out_dir, y)
        manifest.append({"year": y, "rows": int(len(df)), "days": int(df["trade_date"].nunique()),
                         "file": os.path.basename(p)})
        tot += len(df)
    if not manifest:
        print("没有产出"); return 1
    with open(os.path.join(a.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"window": years, "total_rows": tot, "parts": manifest,
                   "features": ["bid_change", "bid_amount", "bid_turnover", "circ_mv",
                                "yesterday_chg", "price"],
                   "labels": ["is_limit_up", "close_chg", "ret",
                              "next_open_chg", "next_close_chg"],
                   "note": "price = daily_auc.m_price (9:25 竞价价); "
                           "is_limit_up = close >= pricelimit.up_limit"},
                  f, ensure_ascii=False, indent=2)
    print("\n合计 %s 行；manifest → %s" % (format(tot, ","),
                                           os.path.join(a.out_dir, "manifest.json")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
