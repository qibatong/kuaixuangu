# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, '/opt/kuaixuan/backend')
import app.api.kpl as api
print('BJ now:', time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time()+8*3600)))
print('is_auction_hours():', api._is_auction_hours())
print('is_intraday():', api._is_intraday())