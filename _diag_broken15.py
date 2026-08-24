# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3, json
from app.core import config
from app.services import kpl

today = "2026-08-24"
conn = sqlite3.connect(config.DB_FILE)
# 今天的 snapshot_bid 9_25 这5只
print("--- 今日(%s) snapshot 9_25 这5只竞换 ---" % today)
snap_today = kpl._snap25_map(today)
for c in ("001203","605018","001319","300716","603378"):
    s = snap_today.get(c, {})
    amt = s.get("bid_amt") or 0
    fmv = s.get("float_mv") or 0
    bt = round(amt*10000/fmv*100, 2) if amt>0 and fmv>0 else None
    print("  %s amt=%s(万) fmv=%s bid_change=%s -> bidTurnover=%s" % (c, amt, fmv, s.get("bid_change"), bt))

# 今天的 broken_today 是否含大中矿业
for tab in ("broken_today",):
    row = conn.execute("SELECT list FROM auction_daily_history WHERE date=? AND tab=?", (today, tab)).fetchone()
    if row and row[0]:
        lst = json.loads(row[0])
        print("--- 今日 %s total=%d, 含 001203? ---" % (tab, len(lst)))
        for it in lst:
            if it.get("code") in ("001203","605018","001319","300716","603378"):
                print("  ", it.get("code"), it.get("name"), "bidTurnover=", it.get("bidTurnover"), "bidAmt=", it.get("bidAmt"), "day=", it.get("day"))
conn.close()