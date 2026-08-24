import sys, os
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.api import kpl as kplapi

lst, d = kplapi._read_auction_fast("seal")
print("seal(bid-seal fast) date:", d, "count:", len(lst))
print("非涨停股中 现涨==竞涨 的样例 head12:")
same = [it for it in lst
        if (it.get('bidChange') is not None and it.get('bidChange') < 9.5
            and it.get('realChange') is not None
            and abs(it.get('realChange', 0) - it.get('bidChange', 0)) < 0.05)]
for it in same[:12]:
    print(" ", it.get('code'), it.get('name'),
          "| realChange=", it.get('realChange'),
          "| bidChange=", it.get('bidChange'))
print("非涨停且现涨==竞涨 数量:", len(same), "/", len(lst))
print("--- 前8 全样(realChange vs bidChange) ---")
for it in lst[:8]:
    print(" ", it.get('code'), it.get('name'),
          "| realChange=", it.get('realChange'),
          "| bidChange=", it.get('bidChange'))