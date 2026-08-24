# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3
from app.core import config
from app.services import kpl

prev = kpl._prev_trade_day()
lst = kpl.query_auction_history(prev, "broken_today")
snap = kpl._snap25_map(prev)

print("prev:", prev, "records:", len(lst) if lst else 0)
empties = [it for it in lst if not it.get("bidTurnover")]
print("empty count:", len(empties))
for it in empties:
    code = it.get("code"); s = snap.get(code, {})
    raw = list(it.items())
    print("  code=%s name=%s bidTurnover=%r floatMv=%r" % (code, it.get("name"), it.get("bidTurnover"), it.get("floatMv")))
    print("    rawkeys:", raw)
    print("    snap: bid_amt=%r float_mv=%r" % (s.get("bid_amt"), s.get("float_mv")))

# merged 再看
m = kpl._merge_broken_bid_snap([dict(x) for x in lst])
em2 = [it for it in m if not it.get("bidTurnover")]
print("after merge empty:", len(em2))
for it in em2:
    code = it.get("code"); s = snap.get(code, {})
    amt = s.get("bid_amt") or 0; fmv = s.get("float_mv") or 0
    print("  code=%s name=%s -> bidTurnover=%r floatMv=%r | amt=%r fmv=%r computed=%s" % (
        code, it.get("name"), it.get("bidTurnover"), it.get("floatMv"),
        amt, fmv, (round(amt*10000/fmv*100, 2) if amt>0 and fmv>0 else "n/a")))