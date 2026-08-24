# -*- coding: utf-8 -*-
import sys, time
print('utc  time.gmtime:', time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()))
print('local time.localtime:', time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime()))
# 模拟 kpl.api 里的 _is_auction_hours/_is_intraday (用 gmtime)
t = time.gmtime()
def is_ah():
    if t.tm_wday >= 5: return False
    return t.tm_hour == 9 and 15 <= t.tm_min <= 30
def is_id():
    if t.tm_wday >= 5: return False
    if t.tm_hour < 9 or t.tm_hour > 15: return False
    if t.tm_hour == 9 and t.tm_min < 30: return False
    if t.tm_hour == 15 and t.tm_min > 0: return False
    return True
print('_is_auction_hours (gmtime):', is_ah())
print('_is_intraday (gmtime):', is_id())

# 北京时间(UTC+8) 下的判断, 对比
def bj_gmtime(): return time.gmtime(time.time() + 8*3600)
t2 = bj_gmtime()
def is_ah_bj():
    if t2.tm_wday >= 5: return False
    return t2.tm_hour == 9 and 15 <= t2.tm_min <= 30
print('BJ local is_auction_hours (+8h):', is_ah_bj())