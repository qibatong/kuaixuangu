# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3
from app.core import config
from app.services import kpl

today = "2026-08-24"
conn = sqlite3.connect(config.DB_FILE)
print("--- 皮阿诺 002853 今日各时点快照 ---")
for r in conn.execute("SELECT time_point, bid_amt, bid_change, bid_buy_amt, float_mv FROM snapshot_bid WHERE date=? AND code='002853' ORDER BY time_point", (today,)):
    print("  ", r)

# _merge_broken_bid_snap 对皮阿诺会算出什么
lst = [{"code":"002853","name":"皮阿诺","day":today}]
m = kpl._merge_broken_bid_snap(list(lst))
print("merge 结果:", m[0] if m else None)
conn.close()