# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database
from app.services import kpl
from app.core import config

today = '2026-08-24'
conn = database.get_conn()

print('=== snapshot_bid today ===')
try:
    rows = conn.execute(
        "SELECT time_point, COUNT(*), MAX(bid_amt) FROM snapshot_bid WHERE date=? GROUP BY time_point ORDER BY time_point",
        (today,)).fetchall()
    for x in rows:
        print('  ', list(x))
except Exception as e:
    print('snapshot err', repr(e))

print('=== auction_daily_history seal today ===')
try:
    r = conn.execute("SELECT COUNT(*) FROM auction_daily_history WHERE date=? AND tab='seal'", (today,)).fetchone()
    print('  count:', r[0] if r else None)
except Exception as e:
    print('hist err', repr(e))

print('=== recent auction_daily dates ===')
try:
    rows = conn.execute("SELECT date, tab, COUNT(*) c FROM auction_daily_history GROUP BY date, tab ORDER BY date DESC LIMIT 12").fetchall()
    for x in rows:
        print('  ', list(x))
except Exception as e:
    print('dates err', repr(e))

conn.close()

print('=== fetch_bid_seal direct ===')
try:
    d = kpl.fetch_bid_seal()
    print('  result len:', 0 if d is None else len(d))
except Exception as e:
    print('  fetch err', repr(e))