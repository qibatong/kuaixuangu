# -*- coding: utf-8 -*-
"""历史特征回补：把 aipick.db 的 features **往前扩展**（实测猫爪可回溯到 2024-06）。

动机（2026-10-03）：影子模型在 walk-forward 演练中因**训练窗太短**（仅 134 天，且演练前期只有 50 天）
未能晋级 ⇒ 需要更长历史。实测：
  · `screening(tradedate)` 在 2025-12/2025-09/2025-06 均可用（5380~5432 行/日）
  · `auc_open_bid` 回溯到 2024-06（5025 行）、`limit_pool_map` 同
  · `pricelimit` 支持 `startdate/enddate` 区间（单次 ≈12 交易日）⇒ 按区间批量拉，配额很省

口径复用（与现网逐字一致，避免 train-serve skew）：
  · 竞价行：`meoz_source.fetch_market` + `to_features`（与 collector 同日口径）
  · 派生列：`db.attach_derived`（mv_rank/amt_rank/rank_diff/price_inv/yday_*/prev_mkt_zt，无泄漏）
  · 标签：**官方涨停价** `pricelimit.up_limit` 与前复权 K 线 close 比较（两者已实证同尺度）
  · 次日：本地前复权日K（stock_kline 覆盖 2023-11 起）⇒ next_open_chg / next_close_chg

用法：
    python backfill_features_hist.py --db <aipick.db> --start 20250901 --end 20250903   # 试跑 3 天
    python backfill_features_hist.py --db <aipick.db> --start 20250601 --limit-days 244
幂等：INSERT OR REPLACE（同输入重跑结果一致；已存在的更晚日期不受影响）。
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))
sys.path.insert(0, '/opt/kuaixuan/aipick/scripts')
KDB = '/opt/kuaixuan/kuaixuan.db'


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def dash(d8):
    return '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:])


def trade_days(start8, end8):
    """用现网交易日历（core.trade_calendar）取区间内交易日；不可用则退化为工作日。"""
    try:
        from app.core import trade_calendar as tc
        days, d = [], start8
        import datetime as dt
        cur = dt.date(int(start8[:4]), int(start8[4:6]), int(start8[6:]))
        last = dt.date(int(end8[:4]), int(end8[4:6]), int(end8[6:]))
        while cur <= last:
            s = cur.strftime('%Y%m%d')
            if tc.is_trade_day(cur.strftime('%Y-%m-%d')):
                days.append(s)
            cur += dt.timedelta(days=1)
        for fn in ('is_tradedate', 'is_trade_date'):
            pass
        return days
    except Exception:
        import datetime as dt
        days, cur = [], dt.date(int(start8[:4]), int(start8[4:6]), int(start8[6:]))
        last = dt.date(int(end8[:4]), int(end8[4:6]), int(end8[6:]))
        while cur <= last:
            if cur.weekday() < 5:
                days.append(cur.strftime('%Y%m%d'))
            cur += dt.timedelta(days=1)
        return days


def klines_map(kdb, codes):
    out = {}
    c = sqlite3.connect(kdb)
    q = ",".join("?" * len(codes)) if len(codes) < 900 else None
    if q:
        cur = c.execute("SELECT code, day_data FROM stock_kline WHERE code IN (%s)" % q, list(codes))
    else:
        cur = c.execute("SELECT code, day_data FROM stock_kline")
    for code, dd in cur:
        code = str(code).zfill(6)
        if code not in codes or not dd:
            continue
        try:
            o = json.loads(dd) if isinstance(dd, str) else dd
            t = [str(x)[:10] for x in (o.get('time') or [])]
            cl, op = o.get('close') or [], o.get('open') or []
            out[code] = {t[i]: (cl[i], op[i] if i < len(op) else None)
                         for i in range(len(t)) if i < len(cl) and cl[i]}
        except Exception:
            pass
    c.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--kdb', default=KDB)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', default='')
    ap.add_argument('--limit-days', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=0.3)
    a = ap.parse_args()
    from app.services import meoz_client as M
    import meoz_source as MZ
    import db as DB

    conn = sqlite3.connect(a.db)
    earliest = conn.execute("SELECT MIN(trade_date) FROM features").fetchone()[0]
    e8 = norm8(earliest)
    end8 = norm8(a.end) or e8
    days = trade_days(norm8(a.start), end8)
    if a.limit_days:
        days = days[:a.limit_days]
    print("回补 %d 个交易日：%s ~ %s（现有最早 %s）" % (len(days), days[0], days[-1], earliest), flush=True)

    # 已有列
    have = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    base_cols = ['trade_date', 'code', 'name', 'bid_change', 'bid_amount', 'bid_turnover',
                 'warn_type', 'price', 'circ_mv', 'yesterday_chg', 'industry', 'concept',
                 'close_chg', 'next_open_chg', 'next_close_chg',
                 'mv_rank', 'amt_rank', 'rank_diff', 'price_inv', 'yday_zt', 'yday_lb',
                 'prev_mkt_zt', 'is_limit_up_v2', 'close_chg_v2', 'label_ver',
                 'exclude_reason', 'label_note', 'is_limit_up_v3', 'label_src_v3', 'exclude_v3']
    base_cols = [c for c in base_cols if c in have]
    # ★ 配额优化：官方涨停价按**区间**批量拉（单次 ≈12 交易日），写入 price_limit 表；
    #   逐日循环只读表 ⇒ 从 11 批/日 降到 ~1 批/日（244 天：2684 → ~230 请求）
    def prefetch_price(days_list, all_codes):
        SEG = 12
        for k in range(0, len(days_list), SEG):
            seg = days_list[k:k + SEG]
            s_lo, s_hi = seg[0], seg[-1]
            got = 0
            for j in range(0, len(all_codes), 500):
                try:
                    r = M.call('pricelimit', params={"symbols": ','.join(all_codes[j:j + 500]),
                                                     "startdate": s_lo, "enddate": s_hi},
                               fields="tradedate,symbol,name,pre_close,up_limit,down_limit",
                               timeout=90)
                    dd = (r or {}).get('data') or {}
                    cols = dd.get('fields') or []
                    recs = []
                    for it in (dd.get('items') or []):
                        row = dict(zip(cols, it))
                        if not row.get('symbol'):
                            continue
                        t8 = norm8(row.get('tradedate'))
                        recs.append((dash(t8), str(row['symbol']).zfill(6), row.get('name'),
                                     row.get('pre_close'), row.get('up_limit'), row.get('down_limit'),
                                     'meoz_pricelimit'))
                    if recs:
                        conn.executemany("INSERT OR REPLACE INTO price_limit VALUES (?,?,?,?,?,?,?)", recs)
                        conn.commit()
                        got += len(recs)
                except Exception as e:
                    print("    [prefetch] %s~%s 批失败: %s" % (s_lo, s_hi, str(e)[:50]))
                time.sleep(a.sleep)
            print("  [prefetch] %s~%s ⇒ %d 行" % (s_lo, s_hi, got), flush=True)

    # ⚠️ snapshot_bid 在主库(kuaixuan.db)不在 aipick.db ⇒ 直接用 features 的票集（≈全市场）
    all_codes = [str(r[0]).zfill(6) for r in conn.execute("SELECT DISTINCT code FROM features")]
    conn.executescript("CREATE TABLE IF NOT EXISTS price_limit (trade_date TEXT NOT NULL, "
                       "code TEXT NOT NULL, name TEXT, pre_close REAL, up_limit REAL, "
                       "down_limit REAL, src TEXT, PRIMARY KEY (trade_date, code));")
    print("预拉官方涨停价：%d 只票 × %d 天（%d 段）" % (len(all_codes), len(days), (len(days) + 11) // 12), flush=True)
    prefetch_price(days, all_codes)
    DATES = []
    t0, tot, sk = time.time(), 0, 0
    for i, d8 in enumerate(days, 1):
        d = dash(d8)
        raw = MZ.fetch_market(d, quiet=True)                      # {} = 非交易日/无数据
        if not raw:
            sk += 1
            print("  %s 无数据（非交易日/上游缺）跳过" % d8, flush=True)
            continue
        recs = MZ.to_features(raw, d)                             # 与 collector 同日口径
        codes = [str(r['code']).zfill(6) for r in recs]
        DB.attach_derived(recs, d)                                # 派生列（无泄漏）
        # 官方涨停价：从 price_limit 表读（已预拉，零 API 调用）
        up = {str(cd).zfill(6): u for cd, u in
              conn.execute("SELECT code, up_limit FROM price_limit WHERE trade_date=?", (d,))
              if u is not None}
        # 前复权 K 线（标签 + 次日）
        km = klines_map(a.kdb, set(codes))
        # 次日交易日（用于 next_*）
        nxt8 = days[i] if i < len(days) else None
        nxtd = dash(nxt8) if nxt8 else None
        rows_out = []
        for r in recs:
            code = str(r['code']).zfill(6)
            m = km.get(code) or {}
            cur = m.get(d)
            lab, src, ex = None, None, None
            if r.get('name') and 'ST' in str(r['name']).upper().replace(' ', ''):
                ex = 'st'
            elif code.startswith(('8', '4', '920')):
                ex = 'bj'
            if cur:
                cl = cur[0]
                u = up.get(code)
                if u is not None:
                    lab, src = (1 if cl >= u - 0.005 else 0), 'official'
                else:
                    src = 'no_up_limit'
                nx = m.get(nxtd) if nxtd else None
                n_o = round((nx[1] / cl - 1) * 100, 4) if (nx and nx[1]) else None
                n_c = round((nx[0] / cl - 1) * 100, 4) if (nx and nx[0]) else None
            else:
                ex = ex or 'no_data'
                n_o = n_c = None
                src = 'no_kline'
            rows_out.append((
                d, code, r.get('name'), r.get('bid_change'), r.get('bid_amount'),
                r.get('bid_turnover'), r.get('warn_type'), r.get('price'), r.get('circ_mv'),
                r.get('yesterday_chg'), r.get('industry'), r.get('concept'),
                r.get('close_chg'), n_o, n_c,
                r.get('mv_rank'), r.get('amt_rank'), r.get('rank_diff'), r.get('price_inv'),
                r.get('yday_zt'), r.get('yday_lb'), r.get('prev_mkt_zt'),
                None, None, None, ex, None, lab, src, ex))
        cols = ','.join(base_cols)
        conn.executemany("INSERT OR REPLACE INTO features (%s) VALUES (%s)"
                         % (cols, ','.join('?' * len(base_cols))), rows_out)
        conn.commit()
        tot += len(rows_out)
        if i % 5 == 0 or i == len(days):
            print("  %d/%d 累计 %d 行（跳过 %d 天）%.0fs"
                  % (i, len(days), tot, sk, time.time() - t0), flush=True)
        time.sleep(a.sleep)
    n, mn, mx = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM features").fetchone()
    print("\nfeatures 现 %d 行，%s ~ %s | 本次新增/更新 %d 行" % (n, mn, mx, tot))
    conn.close()


if __name__ == '__main__':
    main()
