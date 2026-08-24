# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, '/opt/kuaixuan/backend')

# 模拟 api/kpl.py 的 bid-seal 即时路径
from app.api import kpl as api

# 直接调底层
from app.services import kpl as kservices

today = '2026-08-24'
print('=== _read_auction_fast(seal) ===')
d, d_str = api._read_auction_fast('seal')
print('  count:', len(d), 'date:', d_str)
if d:
    print('  first:', d[0].get('code'), d[0].get('name'))

print('=== _is_auction_hours() ===')
from app.api.kpl import _is_auction_hours
print('  ', _is_auction_hours())

print('=== direct fetch_bid_seal() ===')
lst = kservices.fetch_bid_seal()
print('  count:', len(lst))