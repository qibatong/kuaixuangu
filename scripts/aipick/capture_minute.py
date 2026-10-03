# -*- coding: utf-8 -*-
"""每日留存猫爪分钟线（为"冲高卖/时间止损"精确回放积累数据）。

为什么必须现在做（2026-10-03 实测）：
  · 猫爪 `minute` **只提供最新交易日**（09-30 有 241 行；09-24/28/29 全 0）
  · 本地 fetcher 只有 minute(当日)/day/week/month ⇒ 无分钟历史
  ⇒ 分钟级卖出规则（冲高卖 +3.19pp/日 上界、时间止损）**只能靠每日留存**，过期不可追回。

留存范围（覆盖"研究炸板时点 + 卖出规则回放"两类需求）：
  ① pick_daily 当日全部名单票（四线）
  ② label_truth 当日全部涨停/炸板票

落库：新表 `minute_kline`（主键 trade_date+code+trademin，幂等 INSERT OR REPLACE）
用法：python capture_minute.py --db <aipick.db> [--date 20260930] [--sleep 0.25]
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_kline (
  trade_date TEXT NOT NULL, code TEXT NOT NULL, trademin TEXT NOT NULL,
  time_ms INTEGER, o REAL, h REAL, l REAL, c REAL, vol REAL, amount REAL,
  src TEXT,
  PRIMARY KEY (trade_date, code, trademin));
CREATE INDEX IF NOT EXISTS idx_minute_code ON minute_kline(code, trade_date);
"""


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--date', default='')
    ap.add_argument('--sleep', type=float, default=0.25)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    from app.services import meoz_client as M
    c = sqlite3.connect(a.db)
    c.executescript(SCHEMA)
    samples = c.execute("SELECT trade_date FROM features ORDER BY trade_date DESC LIMIT 1").fetchone()
    tgt8 = norm8(a.date) or norm8(samples[0])
    tgt = samples[0] if (not a.date and '-' in str(samples[0])) else (
        '%s-%s-%s' % (tgt8[:4], tgt8[4:6], tgt8[6:]) if '-' in str(samples[0]) else tgt8)

    codes = set()
    for (cd,) in c.execute("SELECT DISTINCT code FROM pick_daily WHERE trade_date=?", (tgt,)):
        codes.add(str(cd).zfill(6))
    for (cd,) in c.execute("SELECT DISTINCT code FROM label_truth WHERE trade_date=?", (tgt,)):
        codes.add(str(cd).zfill(6))
    codes = sorted(codes)
    if a.limit:
        codes = codes[:a.limit]
    print("留存 %s 的分钟线：%d 只票（pick_daily + label_truth）" % (tgt8, len(codes)), flush=True)

    t0, tot, fail = time.time(), 0, 0
    for i, code in enumerate(codes, 1):
        try:
            rows = M.minute_rows(code, date=tgt8) or []
        except Exception:
            rows = []
        recs = []
        for r in rows:
            tm = str(r.get('trademin') or '')
            if not tm:
                continue
            recs.append((tgt, code, tm, r.get('time'), r.get('open'), r.get('high'),
                         r.get('low'), r.get('close'), r.get('vol'), r.get('amount'),
                         'meoz_minute'))
        if recs:
            c.executemany("INSERT OR REPLACE INTO minute_kline VALUES "
                          "(?,?,?,?,?,?,?,?,?,?,?)", recs)
            c.commit()
            tot += len(recs)
        else:
            fail += 1
        if i % 25 == 0 or i == len(codes):
            print("  %d/%d 累计 %d 分钟行(失败 %d) %.0fs" % (i, len(codes), tot, fail, time.time() - t0),
                  flush=True)
        time.sleep(a.sleep)
    n, nd = c.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM minute_kline WHERE trade_date=?",
                      (tgt,)).fetchone()
    print("\nminute_kline[%s]: %d 行 / %d 只票（失败 %d 只 = 当日无分钟数据/停牌）" % (tgt, n, nd, fail))
    c.close()
    print("总耗时 %.0fs" % (time.time() - t0))


if __name__ == '__main__':
    main()
