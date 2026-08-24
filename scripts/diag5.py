# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, '/opt/kuaixuan/backend')

from app.db import database
conn = database.get_conn()
today = '2026-08-24'

row = conn.execute("SELECT list FROM auction_daily_history WHERE date=? AND tab='seal'", (today,)).fetchone()
if row and row[0]:
    lst = json.loads(row[0])
    print('seal today count:', len(lst))
    for it in lst[:5]:
        print('  ', it.get('code'), it.get('name'), 'bidChange=', it.get('bidChange'), 'bidSealAmt=', it.get('bidSealAmt'))
    print('  ...')
else:
    print('seal today: no data row')

# 昨天完整 seal 对比
row2 = conn.execute("SELECT list FROM auction_daily_history WHERE date='2026-08-21' AND tab='seal'").fetchone()
if row2 and row2[0]:
    lst2 = json.loads(row2[0])
    print('\nseal 2026-08-21 count:', len(lst2))
    for it in lst2[:5]:
        print('  ', it.get('code'), it.get('name'), 'bidChange=', it.get('bidChange'))
conn.close()