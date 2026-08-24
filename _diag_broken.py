# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
from app.services import kpl

prev = kpl._prev_trade_day()
print("prev trade day:", prev)
lst = kpl.query_auction_history(prev, "broken_today")
print("broken_today records:", len(lst) if lst else 0)
if lst:
    print("样例 day/code/bidChange/bidTurnover/floatMv 前6:")
    for it in lst[:6]:
        print(" ", it.get("day"), it.get("code"), it.get("name"),
              "bidChange=", it.get("bidChange"),
              "bidTurnover=", it.get("bidTurnover"),
              "floatMv=", it.get("floatMv"))
# 看 prev 那天快照全市场竞换
snap = kpl._snap25_map(prev)
print("snap25 全市场数:", len(snap), "date=", prev)