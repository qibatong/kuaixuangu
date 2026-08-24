# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3
from app.core import config
from app.services import kpl

prev = kpl._prev_trade_day()
print("prev trade day:", prev)

# 1) 昨炸板历史 broken_today
lst = kpl.query_auction_history(prev, "broken_today")
print("broken_today records:", len(lst) if lst else 0)
snap = kpl._snap25_map(prev)
print("snap25 date=%s count=%s" % (prev, len(snap)))

if lst:
    empty = sum(1 for it in lst if not it.get("bidTurnover"))
    miss = sum(1 for it in lst if not it.get("code") in snap)
    print("total:", len(lst), "empty bidTurnover:", empty, "code-not-in-snap:", miss)
    for it in lst[:15]:
        code = it.get("code")
        s = snap.get(code, {})
        print("  day=%s code=%s name=%s bidTurnover=%s floatMv=%s bidAmt=%s | snap bid_amt=%s float_mv=%s" % (
            it.get("day"), code, it.get("name"), it.get("bidTurnover"),
            it.get("floatMv"), it.get("bidAmt"), s.get("bid_amt"), s.get("float_mv")))

    # 2) 合并后什么样
    m = kpl._merge_broken_bid_snap([dict(x) for x in lst])
    miss2 = sum(1 for it in m if not it.get("bidTurnover"))
    print("after merge: empty bidTurnover:", miss2)

# 3) 该日 snapshot_bid 里这些 code 是否有 bid_amt / float_mv (直接查库)
conn = sqlite3.connect(config.DB_FILE)
print("--- auction_daily_history recent tabs/date ---")
for r in conn.execute("SELECT date, tab, length(list) FROM auction_daily_history ORDER BY date DESC LIMIT 25"):
    print(" ", r)
conn.close()