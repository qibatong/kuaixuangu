#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量比换源异常根因探查 (2026-09-20)
================================================================================
回测发现: 官方量比(成交量比)覆盖率 97% vs 自算(金额比, 昨额<100万过滤)仅 32%,
导致换源后 66% 票"升档"、满分票(量比≥3)从日均 110 暴增到 849。
本脚本聚焦验证: 官方量比≥3 的"新满分票"到底是真实放量, 还是"昨竞价量极小→失真虚高"。

关键拆解(排除 100 万阈值这一干扰变量):
  A. 昨额 ≥ 100万 的票(自算可用): 官方量比 vs 自算量比 纯口径对比
     → 若两者高度一致, 说明"金额比 vs 成交量比"口径差异很小, 差异全来自阈值;
     → 若仍显著偏离, 说明官方量比口径本身不同。
  B. 昨额 < 100万 的票(自算过滤掉): 官方量比的分布
     → 若这些票官方量比大面积 ≥3, 则官方量比在"微量竞价"上失真虚高,
       换源会引入大量假满分票。
"""
import datetime
import json
import os
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


def _pct(x, base):
    return 100.0 * x / base if base else 0.0


def main():
    if not APIKEY:
        print("未设置 MEOZ_APIKEY")
        sys.exit(1)
    # 拉最近两个交易日
    days = []
    d = datetime.date.today()
    while len(days) < 2:
        ds = d.strftime("%Y-%m-%d")
        rows = call_daily_auc(ds)
        if rows:
            days.append((ds, rows))
            print("交易日 %s 覆盖 %d" % (ds, len(rows)))
        d -= datetime.timedelta(days=1)
    if len(days) < 2:
        print("交易日不足")
        sys.exit(1)
    (today_s, trows), (prev_s, prows) = days[1], days[0]  # today 更近

    # 分层
    both_ok = []        # 昨额≥100万 且 官方有值: 纯口径对比
    off_only = []       # 昨额<100万 但官方有值: 官方量比是否虚高
    off_ge3_small = 0   # 昨额<100万 且 官方≥3
    off_ge3_big = 0     # 昨额≥100万 且 官方≥3
    calc_ge3 = 0        # 自算≥3
    both_ge3 = 0        # 官方≥3 且 自算≥3 (昨额≥100万内)
    big_cnt = 0
    small_cnt = 0
    off_missing = 0

    diffs = []          # (官方-自算) 相对差, 仅 both_ok
    for sym, t in trows.items():
        v_off = _f(t.get("auc_to_pre_auc_vol_ratio"))
        tamt = _f(t.get("auc_amt"))
        yamt = _f((prows.get(sym) or {}).get("auc_amt"))

        big = (yamt is not None and yamt >= MIN_YDAY_AMT_YUAN)
        if big:
            big_cnt += 1
        else:
            small_cnt += 1

        # 自算
        v_calc = None
        if tamt and tamt > 0 and big:
            v_calc = tamt / yamt
        if v_calc is not None and v_calc >= 3:
            calc_ge3 += 1

        if v_off and v_off > 0:
            if big:
                off_ge3_big += 1 if v_off >= 3 else 0
                if v_calc is not None:
                    both_ok.append((v_off, v_calc))
                    diffs.append(v_off - v_calc)
                    if v_off >= 3 and v_calc >= 3:
                        both_ge3 += 1
            else:
                off_ge3_small += 1 if v_off >= 3 else 0
        else:
            off_missing += 1

    print("\n" + "=" * 68)
    print("根因探查  %s (今) vs %s (昨)" % (today_s, prev_s))
    print("=" * 68)
    print("【覆盖】全市场 %d 只 | 官方量比缺失 %d (%.1f%%)" % (
        len(trows), off_missing, _pct(off_missing, len(trows))))
    print("【昨竞价额分层】≥100万 %d (%.1f%%) | <100万 %d (%.1f%%)" % (
        big_cnt, _pct(big_cnt, len(trows)), small_cnt, _pct(small_cnt, len(trows))))
    print()
    print("【A. 昨额≥100万(自算可用) 纯口径对比】样本 %d 只" % len(both_ok))
    if both_ok:
        import statistics
        d_mean = statistics.mean(diffs)
        d_med = statistics.median(diffs)
        print("  官方-自算 差值: 均值 %+.3f | 中位 %+.3f" % (d_mean, d_med))
        # 分档级别对比
        def bkt(v):
            for lo, hi, sc in (("3","9999",3),("2","3",2),("1.5","2",1.5),
                               ("1.0","1.5",1),("0.6","1.0",0.6),("0","0.6",0)):
                if float(lo) <= v < float(hi):
                    return sc
            return 0.0
        up = down = same = 0
        for vo, vc in both_ok:
            if bkt(vo) > bkt(vc):
                up += 1
            elif bkt(vo) < bkt(vc):
                down += 1
            else:
                same += 1
        print("  分档: 官方更高 %d (%.1f%%) | 更低 %d (%.1f%%) | 同档 %d (%.1f%%)" % (
            up, _pct(up, len(both_ok)), down, _pct(down, len(both_ok)),
            same, _pct(same, len(both_ok))))
    print()
    print("【B. 量比≥3(满分档) 拆解】")
    print("  官方≥3 总数: %d (昨额≥100万 %d + 昨额<100万 %d)" % (
        off_ge3_big + off_ge3_small, off_ge3_big, off_ge3_small))
    print("  自算≥3 总数: %d (仅昨额≥100万内)" % calc_ge3)
    print("  昨额≥100万内 官方≥3 ∩ 自算≥3: %d" % both_ge3)
    print()
    print("【结论判读】")
    if off_ge3_small > off_ge3_big * 2:
        print("  ⚠️ 官方满分票里, 昨额<100万的占比 %.1f%% 远超昨额≥100万" % (
            _pct(off_ge3_small, off_ge3_small + off_ge3_big)))
        print("     → 官方量比在「昨竞价量极小」的票上存在失真虚高, 换源会引入大量假满分票")
    else:
        print("  官方满分票主体来自昨额≥100万(真实放量), 昨额<100万占比 %.1f%%" % (
            _pct(off_ge3_small, off_ge3_small + off_ge3_big)))
    print("=" * 68)


if __name__ == "__main__":
    main()
