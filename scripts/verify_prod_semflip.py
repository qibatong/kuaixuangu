# -*- coding: utf-8 -*-
"""生产机 post-deploy 验证: UI 静态标签 + 过滤行为 + DB 迁移标记"""
import sys, os, re
DEPLOY = "/opt/kuaixuan"
sys.path.insert(0, DEPLOY + "/backend")
os.environ.setdefault("BID_DB_PATH", DEPLOY + "/kuaixuan.db")

print("---UI 静态标签 (应无 '只看' 前缀)---")
matches = set()
paths = [DEPLOY+"/dist/index.html"] + [
    os.path.join(DEPLOY+"/dist/assets", f)
    for f in os.listdir(DEPLOY+"/dist/assets") if f.endswith(".js")
]
for p in paths:
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
        for m in re.findall(r"(?:只看)?ST/停牌|(?:只看)?昨涨停|(?:只看)?昨日涨停", txt):
            matches.add(m)
    except Exception: pass
for m in sorted(matches): print("  ", m)

from app.services.scorer import apply_filters
def mk(code,name,concept,st=False):
    dn = ("ST" if st else "") + name
    return dict(code=code,name=dn,concept=concept,
        probability=80,confidence=80,score=80,circulationMV=200,price=10,
        bidChange=3,bidAmt=5000,
        _raw=dict(f103=concept,f14=dn,f4=3.1,f5=10000))
pool = [mk("000001","甲股份","昨日涨停、光伏"),
        mk("000002","乙科技","光伏"),
        mk("000003","丙实业","昨日连板"),
        mk("000004","丁农业","",st=True)]
base = dict(bidGt=99,probLt=0,confLt=0,floatMvFloor=0,floatMvGt=999999,
            priceGt=9999,bidAmtFloor=0,markets=["hs","cyb","kcb"])
def run(label, **kw):
    f = dict(base, **kw)
    got = [x["name"] for x in apply_filters(pool, f)]
    print(f"  {label} -> {got}")
    return got
print("\n---过滤行为 (生产 apply_filters)---")
r1 = run("limitUp=True  (勾=只看昨涨停)", limitUp=True,  stSuspend=False)
r2 = run("limitUp=False (不勾=剔除昨涨停)", limitUp=False, stSuspend=False)
r3 = run("stSuspend=True  (勾=只看ST/停牌)", limitUp=False, stSuspend=True)
r4 = run("stSuspend=False (不勾=剔除ST/停牌)", limitUp=False, stSuspend=False)
ok = (
    "乙科技" in r1 and "ST丁农业" not in r1
    and r2 == ["乙科技"]
    and "ST丁农业" in r3 and "乙科技" in r3 and "甲股份" not in r3
    and r4 == ["乙科技"]
)
print(f"\n行为断言: {'PASS' if ok else 'FAIL'}")

import json
from app.db.database import get_conn
c = get_conn().cursor()
mig = c.execute("SELECT value FROM settings WHERE key='mig_filter_sem_flip_v2'").fetchone()
total = c.execute("select count(*) from users").fetchone()[0]
nLim = c.execute("select count(*) from users where filter_prefs like '%limitUp%'").fetchone()[0]
print("\n---迁移标记---")
print(f"  mig_filter_sem_flip_v2 = {mig[0] if mig else None}")
print(f"  users_total = {total}, 含 limitUp 偏好 = {nLim}")
