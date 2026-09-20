#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞价量比「换源」历史回测 (2026-09-20)
================================================================================
目标: 量化「竞价量比」从「自算(今竞价额 ÷ 昨竞价额)」换成「猫爪官方
       auc_to_pre_auc_vol_ratio(竞昨量比)」后, 对 bid_strength 层①分值、
       进而对总分(异动 17% 因子)的实际影响。

方法(逐交易日):
  1. daily_auc(date) → 官方竞昨量比 auc_to_pre_auc_vol_ratio + 今竞价额 auc_amt
  2. daily_auc(prev) → 昨竞价额 auc_amt
  3. 每票两个量比:
       自算 v_calc = 今额 ÷ 昨额                (昨额 < 100万元 → 判不可用)
       官方 v_off  = auc_to_pre_auc_vol_ratio    (0/缺失 → 回退自算)
  4. 分档(与线上 bid_strength._bucket 完全一致, 左闭右开顺序首命中):
       旧方案 s_old = bucket(v_calc)
       新方案 s_new = bucket(v_off) 若 v_off>0 否则 bucket(v_calc)
  5. 量比层对总分(百分制)贡献系数 = 100 × w_warn(0.17) × w_vol_ratio(0.45) = 7.65
       换源影响(分) = 7.65 × (s_new - s_old)

口径与源码对齐(不可擅自改, 线上改档须同步):
  * VOL_BUCKETS / VOL_DEFAULT ← backend/app/services/scorer.py
      DEFAULT_SCORING.factors.bid_strength.buckets / default
  * 昨额下限 100 万元 ← backend/app/services/bid_strength.py MIN_YDAY_BID_AMT_WAN
      (线上单位为万元, 本脚本直接用元相除, 昨额阈值 = 100万元 = 1_000_000 元)
  * 官方值优先 / 0缺回退自算 ← bid_strength._fill_snapshot

用法(在测试机 /opt/kuaixuan 下, 需能访问猫爪专线):
  export MEOZ_APIKEY=xxx
  PYTHONIOENCODING=utf-8 /opt/bid-venv/bin/python backtest_vol_ratio.py [N]
    N = 回测交易日数(默认 15)
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

APIKEY = os.environ.get("MEOZ_APIKEY", "")
LINES = (
    os.environ.get("MEOZ_LINE_SZ", "http://sz.numcat.net:8866/api"),
    os.environ.get("MEOZ_LINE_SH", "http://sh.numcat.net:8866/api"),
)
TIMEOUT = 20

# ---- 与线上一致的分档配置(见模块 docstring 来源) ----
VOL_BUCKETS = [
    ["3", "9999", 1.0], ["2", "3", 0.85], ["1.5", "2", 0.7],
    ["1.0", "1.5", 0.55], ["0.6", "1.0", 0.4], ["0", "0.6", 0.25],
]
VOL_DEFAULT = 0.22
MIN_YDAY_AMT_YUAN = 1_000_000          # 昨竞价额下限: 100 万元
W_WARN = 0.17                           # 异动因子权重
W_VOL = 0.45                            # bid_strength 层①子权重(已归一)
COEF = 100.0 * W_WARN * W_VOL           # 量比层 → 总分(分) 系数 = 7.65

# daily_auc 字段(与线上 daily_auc_amt 完全一致, 避免字段裁剪触发 422)
_FIELDS = ("tradedate,symbol,name,m_price,auc_pct_chg,open_bid_pct,auc_vol,"
           "auc_amt,um_vol,auc_vol_ratio,auc_turnover,auc_to_pre_vol_pct,"
           "auc_to_pre_auc_vol_ratio")


def bucket(value):
    for lo, hi, sc in VOL_BUCKETS:
        if float(lo) <= value < float(hi):
            return sc
    return VOL_DEFAULT


def call_daily_auc(date):
    """POST daily_auc, 返回 rows={symbol:{auc_amt, auc_to_pre_auc_vol_ratio}};
    无数据(非交易日/1002)返回 None。"""
    payload = {"apikey": APIKEY, "apiname": "daily_auc",
               "fields": _FIELDS,
               "params": {"trademin": "0925", "tradedate": date.replace("-", "")}}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    last_err = None
    for url in LINES:
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            code = data.get("code")
            if code == 1002:                     # 无数据(非交易日)
                return None
            if isinstance(code, int) and code != 200:
                print("  [warn] date=%s code=%s msg=%s" % (
                    date, code, data.get("message") or data.get("msg")))
                return None
            dd = data.get("data") or {}
            cols = dd.get("fields") or []
            items = dd.get("items") or []
            if not cols or not isinstance(items, list):
                return None
            try:
                ki = cols.index("symbol")
            except ValueError:
                return None
            out = {}
            for row in items:
                if not isinstance(row, (list, tuple)) or len(row) <= ki:
                    continue
                sym = str(row[ki] or "")
                if not sym:
                    continue
                d = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
                out[sym] = d
            return out if out else None
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                OSError, ValueError) as e:
            last_err = e
            continue
    print("  [error] date=%s 全部线路失败: %s" % (date, last_err))
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def collect_trading_days(n):
    """从今天往回探测, 收集最近 n+1 个交易日(多一个做"昨日")。
    返回 [{date, rows}] 升序。"""
    out = []
    d = datetime.date.today()
    guard = 0
    while len(out) < n + 1 and guard < 90:
        ds = d.strftime("%Y-%m-%d")
        rows = call_daily_auc(ds)
        if rows:
            out.append({"date": ds, "rows": rows})
            print("  [ok]  %s  交易日#%d  覆盖 %d 只" % (ds, len(out), len(rows)))
        else:
            print("  [--]  %s  (非交易日/无数据)" % ds)
        d -= datetime.timedelta(days=1)
        guard += 1
    out.reverse()          # 升序: 最旧在前
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    if not APIKEY:
        print("未设置 MEOZ_APIKEY 环境变量, 退出")
        sys.exit(1)
    print("=" * 72)
    print("竞价量比换源历史回测  N=%d 交易日" % n)
    print("旧=自算(今额/昨额)  新=猫爪官方 auc_to_pre_auc_vol_ratio")
    print("量比层→总分系数 COEF=%.2f 分/档位" % COEF)
    print("=" * 72)

    days = collect_trading_days(n)
    if len(days) < 2:
        print("交易日不足, 退出")
        sys.exit(1)

    # 汇总累加器
    agg = {"days": 0, "covered": 0, "off_avail": 0, "calc_avail": 0,
           "up": 0, "down": 0, "same": 0, "both_default": 0,
           "delta_sum": 0.0, "delta_abs_sum": 0.0, "delta_max": 0.0,
           "top_full_hit": 0, "top_full_old": 0, "top_full_new": 0,
           "topn_hit": 0, "topn_old": 0, "topn_new": 0}
    topn = 200

    print("\n" + "-" * 72)
    for i in range(1, len(days)):
        today, prev = days[i], days[i - 1]
        trows, prows = today["rows"], prev["rows"]
        d_up = d_down = d_same = d_both_default = 0
        d_delta = 0.0
        d_delta_abs = 0.0
        d_delta_max = 0.0
        off_avail = calc_avail = 0
        # 满分票(量比≥3 档位=1.0)新旧集合
        old_full, new_full = set(), set()
        # TopN 排序(按量比档位分降序, 用于重合度参考)
        old_score, new_score = {}, {}

        for sym, t in trows.items():
            v_off = _f(t.get("auc_to_pre_auc_vol_ratio"))
            tamt = _f(t.get("auc_amt"))
            yamt = _f((prows.get(sym) or {}).get("auc_amt"))

            # 旧方案: 纯自算
            s_old = VOL_DEFAULT
            if tamt and tamt > 0 and yamt and yamt >= MIN_YDAY_AMT_YUAN:
                s_old = bucket(tamt / yamt)
                calc_avail += 1
            # 新方案: 官方优先, 0/缺失回退自算
            if v_off and v_off > 0:
                s_new = bucket(v_off)
                off_avail += 1
            else:
                s_new = s_old

            if s_old == VOL_DEFAULT and s_new == VOL_DEFAULT:
                d_both_default += 1
            elif s_new > s_old + 1e-9:
                d_up += 1
            elif s_new < s_old - 1e-9:
                d_down += 1
            else:
                d_same += 1

            delta = COEF * (s_new - s_old)
            d_delta += delta
            d_delta_abs += abs(delta)
            if abs(delta) > abs(d_delta_max):
                d_delta_max = delta

            if s_old >= 1.0:
                old_full.add(sym)
            if s_new >= 1.0:
                new_full.add(sym)
            old_score[sym] = s_old
            new_score[sym] = s_new

        # TopN 重合度(按量比档位分降序取前 topn)
        def _top(score_map):
            return set(sorted(score_map, key=lambda s: score_map[s], reverse=True)[:topn])

        o_top, n_top = _top(old_score), _top(new_score)
        hit = len(o_top & n_top)
        covered = len(trows)
        changed = d_up + d_down

        print("[%s] 覆盖 %d 只 | 官方有值 %d | 自算可用 %d | "
              "升档 %d / 降档 %d / 不变 %d | 双缺失 %d" % (
                  today["date"], covered, off_avail, calc_avail,
                  d_up, d_down, d_same, d_both_default))
        print("       总分影响(分): 均值 %+.4f | 绝对均值 %.4f | 极值 %+.2f" % (
              d_delta / covered if covered else 0,
              d_delta_abs / covered if covered else 0, d_delta_max))
        print("       满分票(量比≥3): 旧 %d / 新 %d / 交集 %d | "
              "Top%d 重合 %d/%d" % (
                  len(old_full), len(new_full), len(old_full & new_full),
                  topn, hit, len(o_top)))

        agg["days"] += 1
        agg["covered"] += covered
        agg["off_avail"] += off_avail
        agg["calc_avail"] += calc_avail
        agg["up"] += d_up
        agg["down"] += d_down
        agg["same"] += d_same
        agg["both_default"] += d_both_default
        agg["delta_sum"] += d_delta
        agg["delta_abs_sum"] += d_delta_abs
        agg["delta_max"] = max(abs(agg["delta_max"]), abs(d_delta_max))
        agg["top_full_hit"] += len(old_full & new_full)
        agg["top_full_old"] += len(old_full)
        agg["top_full_new"] += len(new_full)
        agg["topn_hit"] += hit
        agg["topn_old"] += len(o_top)
        agg["topn_new"] += len(n_top)

    # 汇总
    d = max(agg["days"], 1)
    print("-" * 72)
    print("【汇总】%d 个交易日" % d)
    print("  官方量比有值率   : %.1f%% (日均 %d / %d 只)" % (
        100.0 * agg["off_avail"] / max(agg["covered"], 1),
        agg["off_avail"] // d, agg["covered"] // d))
    print("  自算量比可用率   : %.1f%% (昨额≥100万)" % (
        100.0 * agg["calc_avail"] / max(agg["covered"], 1)))
    print("  档位变化(日均)   : 升档 %d / 降档 %d / 不变 %d / 双缺失 %d" % (
        agg["up"] // d, agg["down"] // d, agg["same"] // d, agg["both_default"] // d))
    print("  档位变化占比     : %.2f%% (升 %.2f%% / 降 %.2f%%)" % (
        100.0 * (agg["up"] + agg["down"]) / max(agg["covered"], 1),
        100.0 * agg["up"] / max(agg["covered"], 1),
        100.0 * agg["down"] / max(agg["covered"], 1)))
    print("  总分影响均值     : %+.4f 分 (绝对 %.4f 分)" % (
        agg["delta_sum"] / max(agg["covered"], 1),
        agg["delta_abs_sum"] / max(agg["covered"], 1)))
    print("  总分影响单票极值 : %.2f 分 (升或降)" % agg["delta_max"])
    print("  满分票(量比≥3)   : 旧日均 %d / 新日均 %d / 交集日均 %d (重合 %.1f%%)" % (
        agg["top_full_old"] // d, agg["top_full_new"] // d,
        agg["top_full_hit"] // d,
        100.0 * agg["top_full_hit"] / max(agg["top_full_old"], 1)))
    print("  Top%d 重合度(量比档位排序): 日均 %.1f%%" % (
        topn, 100.0 * agg["topn_hit"] / max(agg["topn_old"], 1)))
    print("=" * 72)


if __name__ == "__main__":
    main()
