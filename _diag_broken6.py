# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3
from app.core import config
prev = "2026-08-21"
conn = sqlite3.connect(config.DB_FILE)
# 昨炸板全列表: code + 9:25 bid_amt(万) + bid_change + float_mv, 看哪些有值哪些是0
print("--- 昨炸板全部股票 9:25 竞价额(bid_amt 万) ---")
codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM snapshot_bid WHERE date=? AND time_point='9_25'", (prev,))]
for r in conn.execute("SELECT code, bid_amt, bid_change, float_mv FROM snapshot_bid WHERE date=? AND time_point='9_25' AND code IN (SELECT code FROM (SELECT DISTINCT code FROM snapshot_bid))", (prev,)):
    pass
conn.close()
# 重新从 broken_today 历史取昨炸板
from app.services import kpl
lst = kpl.query_auction_history(prev, "broken_today")
snap = kpl._snap25_map(prev)
print("total %d" % (len(lst) if lst else 0))
# 按 bid_amt 排序展示 9:25 竞价额
rows = []
for it in lst:
    s = snap.get(it.get("code"), {})
    amt = s.get("bid_amt") or 0
    rows.append((amt, it.get("code"), it.get("name"), s.get("bid_change"), it.get("bidTurnover")))
rows.sort()
print("9:25竞价额(万)最小->最大:")
for amt, c, n, bc, bt in rows[:8]:
    print("  amt=%10.3f code=%s name=%s bid_change=%s bidTurnover=%s" % (amt, c, n, bc, bt))
print("...")
for amt, c, n, bc, bt in rows[-8:]:
    print("  amt=%10.3f code=%s name=%s bid_change=%s bidTurnover=%s" % (amt, c, n, bc, bt))