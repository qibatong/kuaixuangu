# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3
from app.core import config

prev = "2026-08-21"
conn = sqlite3.connect(config.DB_FILE)
print("--- snapshot_bid 2026-08-21 时间点时间戳分布 ---")
for r in conn.execute("SELECT time_point, MIN(ts), MAX(ts), COUNT(*) FROM snapshot_bid WHERE date=? GROUP BY time_point ORDER BY time_point", (prev,)):
    print("  tp=%s ts_min=%s(%s) ts_max=%s(%s) cnt=%d" % (r[0], r[1], time.strftime('%m-%d %H:%M:%S', time.gmtime(r[1]+8*3600)) if r[1] else '-', r[2], time.strftime('%m-%d %H:%M:%S', time.gmtime(r[2]+8*3600)) if r[2] else '-', r[3]))

print("--- 5只竞换为空的股票在 9_15/9_20/9_25 快照 bid_amt(万) bid_buy_amt(元) bid_change ---")
for c in ("300716","001319","603378","605018","001203"):
    print("code=", c)
    for r in conn.execute("SELECT time_point, bid_amt, bid_change, bid_buy_amt, float_mv, ts FROM snapshot_bid WHERE date=? AND code=? ORDER BY time_point", (prev,c)):
        print("   %s bid_amt=%r bid_change=%r bid_buy_amt=%r float_mv=%r ts=%s" % (
            r[0], r[1], r[2], r[3], r[4], time.strftime('%m-%d %H:%M:%S', time.gmtime(r[5]+8*3600)) if r[5] else '-'))
conn.close()