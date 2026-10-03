# -*- coding: utf-8 -*-
"""辅助数据表回补（猫爪新接口，幂等，只写本地/副本库）。

主人 2026-10-02 同意启用官网新接口。四张表：

  price_limit   ← apiname `pricelimit`：**官方涨停价** up_limit / down_limit / pre_close
                  （前复权可比昨收口径, 与 stock_kline 的前复权 close 同尺度）
                  🔴 用途：标签归一化 = `close_qfq >= up_limit`，摆脱自算（自算召回仅 83.7%）
  suspend_tbl   ← apiname `suspend`：停牌/复牌（含盘中时段 09:30-10:15）⇒ 回填与评估跳过
  new_share_tbl ← apiname `new_share`：上市日期 listed_date / market(SH/SZ/BJ) ⇒ 新股首日
  limit_event   ← apiname `limit_event_v2_history`：异动事件（**仅最近 60 交易日**）
                  event/servertime/last_price/limit_price/fd_amount/**fd_decrease_12s**/limit_times
                  🔴 用途：成交概率分级 + 撤单特征（方案第 4/7 条）

⚠️ 实测：`pricelimit` **不带 symbols 查全市场会超时/内部报错** ⇒ 必须按 symbols 分批。
用法：
    python fetch_support_tables.py --db /tmp/x.db --price                 # 只做官方涨停价
    python fetch_support_tables.py --db /tmp/x.db --price --batch 400 --start 20260318 --end 20260930
    python fetch_support_tables.py --db /tmp/x.db --suspend --newshare --events 60
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_limit (
  trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
  pre_close REAL, up_limit REAL, down_limit REAL, src TEXT,
  PRIMARY KEY (trade_date, code));
CREATE TABLE IF NOT EXISTS suspend_tbl (
  trade_date TEXT NOT NULL, code TEXT NOT NULL,
  suspend_type TEXT, suspend_timing TEXT, src TEXT,
  PRIMARY KEY (trade_date, code));
CREATE TABLE IF NOT EXISTS new_share_tbl (
  code TEXT PRIMARY KEY, name TEXT, market TEXT,
  listed_date TEXT, issue_price REAL, src TEXT);
CREATE TABLE IF NOT EXISTS limit_event (
  trade_date TEXT NOT NULL, code TEXT NOT NULL, time_ms INTEGER NOT NULL,
  event INTEGER NOT NULL, servertime TEXT, last_price REAL, limit_price REAL,
  fd_amount REAL, fd_decrease_12s REAL, fd_decrease_rate_12s REAL,
  trade_amount_3s REAL, turnover_rate REAL, limit_times INTEGER, name TEXT, src TEXT,
  PRIMARY KEY (trade_date, code, time_ms, event));
"""


def rows_of(r):
    d = (r or {}).get('data') or {}
    cols = d.get('fields') or []
    return [dict(zip(cols, it)) for it in (d.get('items') or [])]


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--price', action='store_true')
    ap.add_argument('--suspend', action='store_true')
    ap.add_argument('--newshare', action='store_true')
    ap.add_argument('--events', type=int, default=0, help='回补最近 N 个交易日异动')
    ap.add_argument('--batch', type=int, default=400, help='pricelimit 每批股票数')
    ap.add_argument('--start', default='')
    ap.add_argument('--end', default='')
    ap.add_argument('--sleep', type=float, default=0.25)
    a = ap.parse_args()
    from app.services import meoz_client as M

    conn = sqlite3.connect(a.db)
    conn.executescript(SCHEMA)
    _s = conn.execute("SELECT trade_date FROM features LIMIT 1").fetchone()
    _dash = bool(_s) and '-' in str(_s[0])

    def to_db(x):
        y = norm8(x)
        return '%s-%s-%s' % (y[:4], y[4:6], y[6:]) if _dash else y

    t0 = time.time()
    # ---------------- ① 官方涨停价 ----------------
    if a.price:
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM features ORDER BY code")]
        dates = [to_db(r[0]) for r in conn.execute("SELECT DISTINCT trade_date FROM features ORDER BY trade_date")]
        d_lo = norm8(a.start) or norm8(dates[0])
        d_hi = norm8(a.end) or norm8(dates[-1])
        print("pricelimit: %d 只 × %s~%s，每批 %d 只" % (len(codes), d_lo, d_hi, a.batch), flush=True)
        tot = 0
        for i in range(0, len(codes), a.batch):
            chunk = codes[i:i + a.batch]
            try:
                r = M.call('pricelimit',
                           params={"symbols": ','.join(chunk), "startdate": d_lo, "enddate": d_hi},
                           fields="tradedate,symbol,name,pre_close,up_limit,down_limit", timeout=60)
                rs = rows_of(r)
            except Exception as e:
                print("  批 %d 失败: %s" % (i // a.batch + 1, str(e)[:80]), flush=True)
                rs = []
            recs = [(to_db(x.get('tradedate')), str(x.get('symbol')).zfill(6), x.get('name'),
                     x.get('pre_close'), x.get('up_limit'), x.get('down_limit'), 'meoz_pricelimit')
                    for x in rs if x.get('symbol')]
            if recs:
                conn.executemany("INSERT OR REPLACE INTO price_limit VALUES (?,?,?,?,?,?,?)", recs)
                conn.commit()
            tot += len(recs)
            print("  批 %d/%d 累计 %d 行 (%.0fs)"
                  % (i // a.batch + 1, (len(codes) + a.batch - 1) // a.batch, tot, time.time() - t0), flush=True)
            time.sleep(a.sleep)
        print("price_limit 共 %d 行" % conn.execute("SELECT COUNT(*) FROM price_limit").fetchone()[0])

    # ---------------- ② 停牌 ----------------
    if a.suspend:
        dates = [to_db(r[0]) for r in conn.execute("SELECT DISTINCT trade_date FROM features ORDER BY trade_date")]
        d_lo = norm8(a.start) or norm8(dates[0])
        d_hi = norm8(a.end) or norm8(dates[-1])
        rs = rows_of(M.call('suspend', params={"startdate": d_lo, "enddate": d_hi},
                            fields="symbol,tradedate,suspend_type,suspend_timing", timeout=60))
        recs = [(to_db(x.get('tradedate')), str(x.get('symbol')).zfill(6),
                 x.get('suspend_type'), x.get('suspend_timing'), 'meoz_suspend') for x in rs if x.get('symbol')]
        if recs:
            conn.executemany("INSERT OR REPLACE INTO suspend_tbl VALUES (?,?,?,?,?)", recs)
            conn.commit()
        print("suspend_tbl 共 %d 行（拉回 %d）" % (conn.execute("SELECT COUNT(*) FROM suspend_tbl").fetchone()[0], len(rs)))

    # ---------------- ③ 新股 ----------------
    if a.newshare:
        rs = rows_of(M.call('new_share', params={},
                            fields="symbol,name,market,listed_date,issue_price", timeout=60))
        recs = [(str(x.get('symbol'))[:6].zfill(6), x.get('name'), x.get('market'),
                 x.get('listed_date'), x.get('issue_price'), 'meoz_new_share') for x in rs if x.get('symbol')]
        if recs:
            conn.executemany("INSERT OR REPLACE INTO new_share_tbl VALUES (?,?,?,?,?,?)", recs)
            conn.commit()
        print("new_share_tbl 共 %d 行（拉回 %d）" % (conn.execute("SELECT COUNT(*) FROM new_share_tbl").fetchone()[0], len(rs)))

    # ---------------- ④ 异动事件（近 N 日）----------------
    if a.events:
        dates = [to_db(r[0]) for r in conn.execute("SELECT DISTINCT trade_date FROM features ORDER BY trade_date")]
        use = dates[-a.events:]
        print("limit_event: 回补最近 %d 个交易日 %s~%s" % (len(use), norm8(use[0]), norm8(use[-1])), flush=True)
        tot = 0
        for i, d in enumerate(use, 1):
            try:
                rs = rows_of(M.call('limit_event_v2_history', params={"tradedate": norm8(d)},
                                    fields="tradedate,symbol,name,time,event,servertime,last_price,limit_price,"
                                           "fd_amount,fd_decrease_12s,fd_decrease_rate_12s,trade_amount_3s,"
                                           "turnover_rate,limit_times", timeout=60))
            except Exception as e:
                print("  %s 失败: %s" % (d, str(e)[:70]), flush=True)
                rs = []
            recs = [(d, str(x.get('symbol'))[:6].zfill(6), int(x.get('time') or 0), int(x.get('event') or 0),
                     x.get('servertime'), x.get('last_price'), x.get('limit_price'), x.get('fd_amount'),
                     x.get('fd_decrease_12s'), x.get('fd_decrease_rate_12s'), x.get('trade_amount_3s'),
                     x.get('turnover_rate'), x.get('limit_times'), x.get('name'), 'meoz_limit_event')
                    for x in rs if x.get('symbol')]
            if recs:
                conn.executemany("INSERT OR REPLACE INTO limit_event VALUES (%s)"
                                 % ','.join('?' * 15), recs)
                conn.commit()
            tot += len(recs)
            if i % 10 == 0 or i == len(use):
                print("  %d/%d 累计事件 %d 行 (%.0fs)" % (i, len(use), tot, time.time() - t0), flush=True)
            time.sleep(a.sleep)
        print("limit_event 共 %d 行" % conn.execute("SELECT COUNT(*) FROM limit_event").fetchone()[0])
    conn.close()
    print("总耗时 %.0fs" % (time.time() - t0))


if __name__ == '__main__':
    main()
