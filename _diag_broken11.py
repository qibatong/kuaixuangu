# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3, json
from app.core import config
prev = "2026-08-21"
codes = ["001203","605018","001319","300716","603378"]
conn = sqlite3.connect(config.DB_FILE)
print("--- snapshot_lastsec %s (5 codes) ---" % prev)
try:
    for r in conn.execute("SELECT code, bid_amt, bid_change, ts FROM snapshot_lastsec WHERE date=?", (prev,)):
        if r[0] in codes:
            print("  ", r)
except Exception as e:
    print("  lastsec err", e)
print("--- auction_daily_history bid_net %s 中这5只 ---" % prev)
try:
    row = conn.execute("SELECT list FROM auction_daily_history WHERE date=? AND tab='bid_net'", (prev,)).fetchone()
    if row and row[0]:
        lst = json.loads(row[0])
        m = {x.get("code"): x for x in lst}
        for c in codes:
            x = m.get(c)
            print("  code=%s name=%s bidAmt=%s bidTurnover=%s bidNetAmt=%s floatMv=%s" % (
                c, (x.get("name") if x else None), (x.get("bidAmt") if x else None),
                (x.get("bidTurnover") if x else None), (x.get("bidNetAmt") if x else None),
                (x.get("floatMv") if x else None)))
        print("  bid_net total:", len(lst))
except Exception as e:
    print("  bid_net err", e)
conn.close()