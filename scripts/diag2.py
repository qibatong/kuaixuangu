# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database

conn = database.get_conn()
today = '2026-08-24'

print('=== auction_daily_history seal raw ===')
rows = conn.execute(
    "SELECT date, tab, length(data), substr(data,1,300) FROM auction_daily_history WHERE date=? AND tab='seal'",
    (today,)).fetchall()
for x in rows:
    print('  ', list(x))

print('=== columns of auction_daily_history ===')
rows = conn.execute("PRAGMA table_info(auction_daily_history)").fetchall()
for x in rows:
    print('  ', x[1], x[2])

conn.close()