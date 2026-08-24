# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.api import kpl as api_mod

print('handler filedir is kpl api; check _is_auction_hours/_is_intraday from module')
try:
    from app.api.kpl import _is_auction_hours, _is_intraday
    print('utc now:', time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()))
    print('is_auction_hours:', _is_auction_hours())
    print('is_intraday:', _is_intraday())
except Exception as e:
    print('import err', repr(e))