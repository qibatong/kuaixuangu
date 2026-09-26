# -*- coding: utf-8 -*-
"""竞价额阈值体检 —— `bid_amount ≥ 3000 万` 这条过滤到底筛掉了什么
=====================================================================================
背景
--------------------------------------------------------------------------------
`predict_daily.py` 的页面默认过滤器里有 `bid_amount >= 3000`（万元）。
实测这条只覆盖 **~1.5%** 的股票（`auc_amt` 原始单位是**元**，全市场中位仅 ~34 万元），
即每天把 5,000+ 只压到 ~20 只 —— **它对结果的支配力比模型本身还大**。

本脚本在 7 年基座(1,620 天 / 772 万行)上体检这条阈值：
1. `bid_amount` 的真实分布（分位）；
2. 各候选阈值的**覆盖率**（有多少股票、多少交易日还能有票）；
3. 阈值与**结果**的关系：越高的竞价额，涨停率 / 正收益率 / 平均收益是变好还是变差；
4. 逐年稳定性（防止某一年特殊）。

用法
--------------------------------------------------------------------------------
    /usr/bin/python3 _audit_amt.py --trainset data/trainsetv2
⚠️ 只读基座，不修改任何数据。
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from train_v2 import load_base, roc_auc  # noqa: E402

THRESHOLDS = [0, 100, 300, 500, 1000, 2000, 3000, 5000, 10000, 20000, 50000]
BANDS = [0, 100, 300, 500, 1000, 2000, 3000, 5000, 10000, 20000, 100000, np.inf]


def pct(v):
    return "%.2f%%" % (v * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trainset", default=os.path.join(HERE, "data", "trainsetv2"))
    ap.add_argument("--limit-year", type=int, default=None)
    a = ap.parse_args()

    df = load_base(a.trainset, limit_year=a.limit_year)
    amt = df["bid_amount"].to_numpy(dtype=np.float64)
    y = df["is_limit_up"].to_numpy(dtype=np.float64)
    r = df["ret"].to_numpy(dtype=np.float64)
    pos = (r > 0).astype(np.float64)
    n = len(df)

    print("\n" + "=" * 96)
    print("一、bid_amount（竞价额，万元）真实分布      n = %s 行 / %d 天"
          % (format(n, ","), df["trade_date"].nunique()))
    print("=" * 96)
    qs = [0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100]
    v = np.nanpercentile(amt, qs)
    print("  分位:  " + "  ".join("p%g=%.0f" % (q, x) for q, x in zip(qs, v)))
    print("  均值 %.0f 万元 | 中位 %.0f 万元（≈%.1f 万元）| 最大 %.0f 万元"
          % (np.nanmean(amt), np.nanmedian(amt), np.nanmedian(amt), np.nanmax(amt)))
    print("\n  ★ 3000 万 = 第 %.1f 百分位" % (np.mean(amt < 3000) * 100))
    for t in (300, 500, 1000):
        print("     %5d 万 = 第 %.1f 百分位" % (t, np.mean(amt < t) * 100))

    print("\n" + "=" * 96)
    print("二、各阈值的覆盖率 与「筛出来的票结果好不好」")
    print("=" * 96)
    print("%9s %10s %10s %10s %10s %12s %12s"
          % ("阈值(万)", "留存股票", "涨停率", "正收益率", "平均收益", "涨停股捕获率", "覆盖交易日"))
    print("-" * 96)
    n_day = df["trade_date"].nunique()
    n_limit = float(y.sum())
    _duniq, _dinv = np.unique(df["trade_date"].to_numpy(), return_inverse=True)
    for t in THRESHOLDS:
        m = amt >= t
        nc = int(m.sum())
        if nc == 0:
            continue
        # 覆盖交易日：该阈值下每天还能剩 >=1 只票的天数
        day_ok = np.bincount(_dinv[m], minlength=len(_duniq))
        cov_day = float((day_ok > 0).sum() / len(_duniq)) if len(_duniq) else 0.0
        capture = float(y[m].sum() / n_limit) if n_limit > 0 else 0.0
        print("%9d %9s %10s %10s %+10.2f%% %11s %11s"
              % (t, format(nc, ","), pct(y[m].mean()), pct(pos[m].mean()),
                 r[m].mean(), pct(capture), pct(cov_day)))

    print("\n" + "=" * 96)
    print("三、分档看「竞价额越高，结果是否越好」")
    print("=" * 96)
    print("%22s %10s %10s %10s %10s" % ("竞价额档(万元)", "股票数", "涨停率", "正收益率", "平均收益"))
    print("-" * 96)
    for lo, hi in zip(BANDS[:-1], BANDS[1:]):
        m = (amt >= lo) & (amt < hi)
        if m.sum() == 0:
            continue
        lab = "[%g, %s)" % (lo, "∞" if np.isinf(hi) else "%g" % hi)
        print("%22s %10s %10s %10s %+10.2f%%"
              % (lab, format(int(m.sum()), ","), pct(y[m].mean()), pct(pos[m].mean()), r[m].mean()))

    print("\n" + "=" * 96)
    print("四、逐年覆盖率（防止某一年特殊）")
    print("=" * 96)
    yr = (df["trade_date"].to_numpy() // 10000)
    years = sorted(set(yr.tolist()))
    hdr = "%6s %9s" % ("年份", "股票数")
    for t in (300, 1000, 3000):
        hdr += " %10s" % ("≥%d万" % t)
    print(hdr)
    print("-" * 96)
    for yv in years:
        m = yr == yv
        line = "%6d %9s" % (yv, format(int(m.sum()), ","))
        for t in (300, 1000, 3000):
            line += " %10s" % pct(np.mean(amt[m] >= t))
        print(line)

    print("\n" + "=" * 96)
    print("五、竞价额单独作为「排序/打分」信号的能力（AUC，越高越有信息）")
    print("=" * 96)
    print("  AUC(竞价额 → 是否涨停)   = %.4f" % roc_auc(y, amt))
    print("  AUC(竞价额 → 是否赚钱)   = %.4f   ← 若 ≈0.5 说明它对'赚钱'没有排序能力"
          % roc_auc(pos, amt))
    print("  AUC(竞价额 → 可交易收益) = %.4f" % roc_auc((r > np.nanmedian(r)).astype(float), amt))
    print("\n  参考: 模型特征里 bid_turnover 对涨停的 AUC ≈ 0.8054、bid_amount ≈ 0.7744")
    print("        ⇒ 竞价额**有信息**，但「3000 万」这个**绝对阈值**筛剩 1.5% 是极端的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
