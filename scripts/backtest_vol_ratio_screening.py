#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screening(实时选股) vs daily_auc 竞价金额口径对比 (2026-09-20)
================================================================================
主人提示: 猫爪「股票数据-实时选股(screening)」有「竞价金额(元)」字段。
本脚本实证:
  A. screening.auc_amt(竞价金额元) vs daily_auc.auc_amt(竞价金额元) 是否一致
  B. screening.volume_ratio(量比) 到底是什么口径(对比 自算量比 / 竞昨量比)
  C. 用 screening 回溯的竞价金额自算量比, 与 daily_auc 回溯的自算量比 是否一致
拉 9-17(今) + 9-16(昨) 两天。
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
SCR_FIELDS = "tradedate,symbol,name,auc_pct_chg,auc_amt,auc_vol,auc_turnover,volume_ratio,turnover_rate_f"
DAU_FIELDS = ("tradedate,symbol,name,m_price,auc_pct_chg,auc_vol,auc_amt,um_vol,"
              "auc_vol_ratio,auc_turnover,auc_to_pre_vol_pct,auc_to_pre_auc_vol_ratio")


def call(apiname, date, fields):
    payload = {"apikey": APIKEY, "apiname": apiname, "fields": fields,
               "params": {"tradedate": date.replace("-", "")}}
    if apiname == "daily_auc":
        payload["params"] = {"trademin": "0925", "tradedate": date.replace("-", "")}
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
                print("  [err] %s %s code=%s msg=%s" % (
                    apiname, date, data.get("code"),
                    (data.get("message") or data.get("msg") or "")[:80]))
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
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
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
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx * vy) ** 0.5 if vx > 0 and vy > 0 else None


def main():
    if not APIKEY:
        print("未设置 MEOZ_APIKEY")
        sys.exit(1)
    days = []
    d = datetime.date.today()
    while len(days) < 2:
        ds = d.strftime("%Y-%m-%d")
        r = call("daily_auc", ds, DAU_FIELDS)
        if r:
            days.append(ds)
        d -= datetime.timedelta(days=1)
    newer, older = days[0], days[1]   # newer 更近 = 今日
    print("交易日: 今=%s 昨=%s" % (newer, older))

    scr_new = call("screening", newer, SCR_FIELDS)
    scr_old = call("screening", older, SCR_FIELDS)
    dau_new = call("daily_auc", newer, DAU_FIELDS)
    dau_old = call("daily_auc", older, DAU_FIELDS)
    print("覆盖: screening今=%d 昨=%d | daily_auc今=%d 昨=%d" % (
        len(scr_new or {}), len(scr_old or {}), len(dau_new or {}), len(dau_old or {})))

    # A. screening.auc_amt vs daily_auc.auc_amt (同一天)
    print("\n[A] 竞价金额(元) screening.auc_amt vs daily_auc.auc_amt (今日):")
    xs, ys = [], []
    for sym, s in (scr_new or {}).items():
        dd = (dau_new or {}).get(sym)
        if not dd:
            continue
        a, b = _f(s.get("auc_amt")), _f(dd.get("auc_amt"))
        if a and b:
            xs.append(a)
            ys.append(b)
    if xs:
        print("  样本 %d | 相关 r=%.6f | screening均值 %.0f / daily_auc均值 %.0f | 比值中位 %.4f" % (
            len(xs), corr(xs, ys), statistics.mean(xs), statistics.mean(ys),
            statistics.median([a / b for a, b in zip(xs, ys)])))

    # B. screening.volume_ratio 口径
    print("\n[B] screening.volume_ratio(量比) 口径探查 (今日, 昨额≥100万):")
    vr, ra, vo = [], [], []
    for sym, s in (scr_new or {}).items():
        dd = (dau_new or {}).get(sym)
        do = (dau_old or {}).get(sym)
        if not dd or not do:
            continue
        v = _f(s.get("volume_ratio"))
        amt_t = _f(dd.get("auc_amt"))
        amt_y = _f(do.get("auc_amt"))
        off = _f(dd.get("auc_to_pre_auc_vol_ratio"))
        if amt_y is None or amt_y < 1_000_000:
            continue
        if v is not None and amt_t and amt_y:
            vr.append(v)
            ra.append(amt_t / amt_y)
            if off is not None and off > 0:
                vo.append(off)
    if vr:
        print("  volume_ratio 中位 %.3f 均值 %.3f 相关(自算量比)=%.4f" % (
            statistics.median(vr), statistics.mean(vr),
            corr(vr, ra) if len(ra) == len(vr) else None))

    # C. 用 screening 回溯自算量比 vs daily_auc 回溯自算量比
    print("\n[C] 自算量比(今额/昨额): screening 回溯 vs daily_auc 回溯 (昨额≥100万):")
    c_s, c_d = [], []
    for sym, s in (scr_new or {}).items():
        so = (scr_old or {}).get(sym)
        dn = (dau_new or {}).get(sym)
        do = (dau_old or {}).get(sym)
        if not so or not dn or not do:
            continue
        st, sy = _f(s.get("auc_amt")), _f(so.get("auc_amt"))
        dt, dy = _f(dn.get("auc_amt")), _f(do.get("auc_amt"))
        if sy is None or sy < 1_000_000:
            continue
        if st and dt and dy:
            c_s.append(st / sy)
            c_d.append(dt / dy)
    if c_s:
        print("  样本 %d | screening自算 中位 %.3f / daily_auc自算 中位 %.3f | 相关 r=%.6f" % (
            len(c_s), statistics.median(c_s), statistics.median(c_d),
            corr(c_s, c_d)))


if __name__ == "__main__":
    main()
