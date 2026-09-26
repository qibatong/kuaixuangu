# -*- coding: utf-8 -*-
"""竞价额绝对阈值体检 —— 某条 `bid_amount >= X` 过滤到底筛掉了什么
=====================================================================================
背景
--------------------------------------------------------------------------------
`predict_daily.py` 的页面默认过滤器里有 `bid_amount >= 3000`（万元）。
实测这条只覆盖 **~1.5%** 的股票（`auc_amt` 原始单位是**元**，全市场中位仅 ~34 万元），
即每天把 5,000+ 只压到 ~20 只 —— **它对结果的支配力比模型本身还大**。

本脚本在基座（7 年 / 1,620 天 / 772 万行）上体检**任意**绝对阈值：

1. `bid_amount` 的真实分布（分位），以及关注阈值落在第几百分位；
2. 各候选阈值的**覆盖率**（留存股票数、还能有票的交易日占比）+ **涨停股捕获率**；
3. 阈值与**结果**的关系：留存票的涨停率 / 正收益率 / 平均收益越好还是越差；
4. 逐年覆盖率（防止某一年特殊）；
5. 竞价额**单独作为排序信号**的能力（AUC）——用于区分「有信息」与「绝对阈值合理」。

用法
--------------------------------------------------------------------------------
    python audit_amt.py --trainset data/trainsetv2
    python audit_amt.py --trainset data/trainsetv2 --focus 1000 --thresholds 0,300,1000,3000
    python audit_amt.py --trainset data/trainsetv2 --out /tmp/amt_audit.txt

⚠️ **只读基座，不修改任何数据**（脚本内无任何写库语句）。

由来
--------------------------------------------------------------------------------
2026-09-26 由生产机一次性脚本 `aipick/scripts/_audit_amt.py`（md5 `97a65aca…`）
参数化收编而来，原件归档在 `ops/archive/prod/aipick/scripts/_audit_amt.py`。
原件的阈值/关注值写死在源码里，现改为命令行参数，成为可复用工具
（依 `.gitignore` L166-172 先例：「只放行通用工具」）。
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from train_v2 import load_base, roc_auc  # noqa: E402

# 默认候选阈值（万元）—— 覆盖「不设限」到「极端苛刻」
DEFAULT_THRESHOLDS = [0, 100, 300, 500, 1000, 2000, 3000, 5000, 10000, 20000, 50000]
# 默认分档边界（万元），用于看「竞价额越高结果是否越好」
DEFAULT_BANDS = [0, 100, 300, 500, 1000, 2000, 3000, 5000, 10000, 20000, 100000, np.inf]
# 默认关注阈值（万元）—— 第 1 节的「落在第几百分位」与第 4 节的逐年列
DEFAULT_FOCUS = 3000
# 默认逐年列（万元）
DEFAULT_YEAR_THRESHOLDS = [300, 1000, 3000]


def pct(v):
    return "%.2f%%" % (v * 100)


def _parse_floats(s, cast=float):
    """`"0,300,inf"` -> [0.0, 300.0, inf]；允许 inf/-inf。"""
    out = []
    for tok in str(s).split(","):
        tok = tok.strip()
        if not tok:
            continue
        low = tok.lower()
        if low in ("inf", "+inf", "infinity"):
            out.append(np.inf)
        elif low in ("-inf", "-infinity"):
            out.append(-np.inf)
        else:
            out.append(cast(tok))
    return out


class _Tee(object):
    """把 stdout 同时写进文件（用于留存体检证据）。"""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def main():
    ap = argparse.ArgumentParser(
        description="竞价额绝对阈值体检（只读基座，不修改任何数据）")
    ap.add_argument("--trainset", default=os.path.join(HERE, "data", "trainsetv2"),
                    help="基座目录（默认 <脚本目录>/data/trainsetv2）")
    ap.add_argument("--limit-year", type=int, default=None,
                    help="只取最近 N 年的数据（调试/提速用）")
    ap.add_argument("--focus", type=float, default=DEFAULT_FOCUS,
                    help="关注阈值(万元)，用于第1节百分位与第4节逐年列（默认 %d）"
                         % DEFAULT_FOCUS)
    ap.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
                    help="第2节候选阈值列表(万元, 逗号分隔)")
    ap.add_argument("--year-thresholds",
                    default=",".join(str(t) for t in DEFAULT_YEAR_THRESHOLDS),
                    help="第4节逐年覆盖率列(万元, 逗号分隔)")
    ap.add_argument("--bands", default=",".join(
                        "inf" if np.isinf(b) else ("%g" % b) for b in DEFAULT_BANDS),
                    help="第3节分档边界(万元, 逗号分隔, 允许 inf)")
    ap.add_argument("--out", default=None,
                    help="可选：把完整报告同时写入该文件（留存证据）")
    a = ap.parse_args()

    thresholds = _parse_floats(a.thresholds)
    year_thresholds = _parse_floats(a.year_thresholds)
    bands = _parse_floats(a.bands)
    focus = float(a.focus)

    fh = open(a.out, "w", encoding="utf-8") if a.out else None
    old_stdout = sys.stdout
    if fh is not None:
        sys.stdout = _Tee(old_stdout, fh)
    try:
        return _run(a.trainset, a.limit_year, focus, thresholds, year_thresholds,
                    bands, a.out)
    finally:
        sys.stdout = old_stdout
        if fh is not None:
            fh.close()


def _run(trainset, limit_year, focus, THRESHOLDS, YEAR_THRESHOLDS, BANDS, out):
    df = load_base(trainset, limit_year=limit_year)
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
    print("\n  ★ %g 万 = 第 %.1f 百分位" % (focus, np.mean(amt < focus) * 100))
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
        print("%9g %9s %10s %10s %+10.2f%% %11s %11s"
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
    for t in YEAR_THRESHOLDS:
        hdr += " %10s" % ("≥%g万" % t)
    print(hdr)
    print("-" * 96)
    for yv in years:
        m = yr == yv
        line = "%6d %9s" % (yv, format(int(m.sum()), ","))
        for t in YEAR_THRESHOLDS:
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
    print("        ⇒ 竞价额**有信息**，但「%g 万」这类**绝对阈值**若只筛剩几个百分点就是极端的。"
          % focus)
    if out:
        print("\n  （本报告已同时写入 %s）" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
