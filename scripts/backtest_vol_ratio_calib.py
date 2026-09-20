#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
官方量比字段口径实证 (2026-09-20)
================================================================================
openapi.json 官方定义(已查证):
  auc_to_pre_auc_vol_ratio = 竞价成交量 ÷ 昨日竞价成交量   (竞昨量比, 倍数, 示例1.42)
  auc_to_pre_vol_pct       = 竞价成交量 ÷ 昨日全天成交量×100 (竞昨成交比%)
  auc_vol_ratio            = 竞价成交量 ÷ 近5日平均每分钟成交量 (竞价量比, 示例2.18)
  auc_vol                  = 竞价量(手)
  auc_amt                  = 竞价金额(元)

疑点: 回测显示官方竞昨量比(成交量比) 系统性高于自算(今额/昨额金额比) 均值+3.1倍,
理论上金额比≈成交量比×(1+竞价涨幅), 不该差3倍。本脚本实证:
  A. 官方竞昨量比  vs  自算成交量比(今auc_vol/昨auc_vol)  → 若一致, 官方=真成交量比
  B. 自算金额比    vs  自算成交量比                        → 金额比与成交量比真实差距
  C. 官方竞昨量比  vs  自算金额比                          → 换源实际引入的差距
仅对比"昨额≥100万"(自算可用)的票, 排除阈值干扰。
"""
import datetime
import json
import os
import statistics
import sys
import urllib.error
import urllib.request

APIKEY = os.environ.get("MEOZ_APIKEY", "")
LINES = (
    os.environ.get("MEOZ_LINE_SZ", "http://sz.numcat.net:8866/api"),
    os.environ.get("MEOZ_LINE_SH", "http://sh.numcat.net:8866/api"),
)
TIMEOUT = 20
_FIELDS = ("tradedate,symbol,name,m_price,auc_pct_chg,open_bid_pct,auc_vol,"
           "auc_amt,um_vol,auc_vol_ratio,auc_turnover,auc_to_pre_vol_pct,"
           "auc_to_pre_auc_vol_ratio")
MIN_YDAY_AMT_YUAN = 1_000_000


def call_daily_auc(date):
    payload = {"apikey": APIKEY, "apiname": "daily_auc", "fields": _FIELDS,
               "params": {"trademin": "0925", "tradedate": date.replace("-", "")}}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for url in LINES:
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Accept": "application/json", "Content-Type": "application/json; charset=UTF-8"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            if data.get("code") == 1002:
                return None
            if isinstance(data.get("code"), int) and data.get("code") != 200:
                return None
            dd = data.get("data") or {}
            cols = dd.get("fields") or []
            items = dd.get("items") or []
            if not cols or not isinstance(items, list):
                return None
            ki = cols.index("symbol")
            out = {}
            for row in items:
                if not isinstance(row, (list, tuple)) or len(row) <= ki:
                    continue
                sym = str(row[ki] or "")
                if sym:
                    out[sym] = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
            return out or None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError):
            continue
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def main():
    if not APIKEY:
        print("未设置 MEOZ_APIKEY")
        sys.exit(1)
    days = []
    d = datetime.date.today()
    while len(days) < 2:
        ds = d.strftime("%Y-%m-%d")
        rows = call_daily_auc(ds)
        if rows:
            days.append((ds, rows))
        d -= datetime.timedelta(days=1)
    if len(days) < 2:
        print("交易日不足")
        sys.exit(1)
    (newer_s, newer), (older_s, older) = days[1], days[0]  # newer 更近 = 今日

    rows = []
    for sym, t in newer.items():
        y = older.get(sym)
        if not y:
            continue
        v_off = _f(t.get("auc_to_pre_auc_vol_ratio"))          # 官方竞昨量比
        amt_t = _f(t.get("auc_amt"))
        amt_y = _f(y.get("auc_amt"))
        vol_t = _f(t.get("auc_vol"))
        vol_y = _f(y.get("auc_vol"))
        if amt_y is None or amt_y < MIN_YDAY_AMT_YUAN:
            continue                                            # 只比"昨额≥100万"
        ratio_amt = amt_t / amt_y if (amt_t and amt_y) else None   # 金额比
        ratio_vol = vol_t / vol_y if (vol_t and vol_y) else None   # 成交量比
        if v_off is not None and v_off > 0 and ratio_amt and ratio_vol:
            rows.append((v_off, ratio_amt, ratio_vol))

    print("=" * 68)
    print("口径实证  %s(今) vs %s(昨)  样本(昨额≥100万) %d 只" % (
        newer_s, older_s, len(rows)))
    print("=" * 68)
    vo = [r[0] for r in rows]
    ra = [r[1] for r in rows]
    rv = [r[2] for r in rows]

    print("官方竞昨量比   : 中位 %.3f  均值 %.3f  P90 %.3f  max %.1f" % (
        statistics.median(vo), statistics.mean(vo),
        sorted(vo)[int(len(vo) * 0.9)], max(vo)))
    print("自算金额比     : 中位 %.3f  均值 %.3f  P90 %.3f  max %.1f" % (
        statistics.median(ra), statistics.mean(ra),
        sorted(ra)[int(len(ra) * 0.9)], max(ra)))
    print("自算成交量比   : 中位 %.3f  均值 %.3f  P90 %.3f  max %.1f" % (
        statistics.median(rv), statistics.mean(rv),
        sorted(rv)[int(len(rv) * 0.9)], max(rv)))
    print()
    print("相关性:")
    print("  官方竞昨量比 vs 自算金额比   : r = %.4f" % (corr(vo, ra) or -9))
    print("  官方竞昨量比 vs 自算成交量比 : r = %.4f" % (corr(vo, rv) or -9))
    print("  自算金额比   vs 自算成交量比 : r = %.4f" % (corr(ra, rv) or -9))
    print()
    # 差值分析
    d_off_amt = [o - a for o, a in zip(vo, ra)]
    d_off_vol = [o - v for o, v in zip(vo, rv)]
    d_amt_vol = [a - v for a, v in zip(ra, rv)]
    print("官方竞昨量比 - 自算金额比    : 中位 %+.3f  均值 %+.3f" % (
        statistics.median(d_off_amt), statistics.mean(d_off_amt)))
    print("官方竞昨量比 - 自算成交量比  : 中位 %+.3f  均值 %+.3f" % (
        statistics.median(d_off_vol), statistics.mean(d_off_vol)))
    print("自算金额比 - 自算成交量比    : 中位 %+.3f  均值 %+.3f" % (
        statistics.median(d_amt_vol), statistics.mean(d_amt_vol)))
    print()
    # 量比≥3 判据一致性
    def ge3(xs):
        return sum(1 for x in xs if x >= 3)
    print("量比≥3 票数: 官方 %d / 自算金额比 %d / 自算成交量比 %d" % (
        ge3(vo), ge3(ra), ge3(rv)))
    print("=" * 68)


if __name__ == "__main__":
    main()
