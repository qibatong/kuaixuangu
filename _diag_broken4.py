# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
from app.services import kpl

prev = kpl._prev_trade_day()
lst = kpl.query_auction_history(prev, "broken_today")
empty = [it for it in lst if not it.get("bidTurnover")]
codes = [it.get("code") for it in empty]
print("prev:", prev, "empty codes:", codes)

# 1) KPL 竞价涨停委买额(seal) 里这些 code 是否有? 那是实时接口, 当前时间可能非竞价.
# 看 KPL seal 历史落库 auction_daily_history[prev][seal]
seal_hist = kpl.query_auction_history(prev, "seal")
print("seal hist records:", len(seal_hist) if seal_hist else 0)
if seal_hist:
    m = {x.get("code"): x for x in seal_hist}
    for c in codes:
        x = m.get(c)
        print("  code=%s in-seal-hist=%s bidSealAmt=%s bidTurnover=%s bidAmt=%s floatMv=%s" % (
            c, bool(x), x.get("bidSealAmt") if x else None,
            x.get("bidTurnover") if x else None, x.get("bidAmt") if x else None,
            x.get("floatMv") if x else None))

# 2) seal 历史里这些 code 的 snapshot_bid 9_25 bid_amt(万元) 与 float_mv
import sqlite3
from app.core import config
conn = sqlite3.connect(config.DB_FILE)
for c in codes:
    r = conn.execute("SELECT bid_amt, float_mv, bid_buy_amt, bid_change FROM snapshot_bid WHERE date=? AND time_point='9_25' AND code=?",
                     (prev, c)).fetchone()
    print("  snap code=%s bid_amt=%r float_mv=%r bid_buy_amt=%r bid_change=%r" % ((c,) + tuple(r if r else (None,None,None,None))))
conn.close()