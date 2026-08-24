# -*- coding: utf-8 -*-
import sys, os, json, urllib.request, urllib.parse
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
from app.services import fetcher

HOSTS = getattr(fetcher.config, "KLINE_HOSTS", ["https://push2.eastmoney.com"])
URL = "http://push2.eastmoney.com/api/qt/clist/get"

def q(code):
    secid = {"001203":"0.001203","605018":"1.605018"}.get(code, "0."+code if code[0]=="3" else "1."+code)
    params = {
        "fs": "b:"+secid,
        "fltt": 2, "invt": 2,
        "fields": "f2,f5,f6,f14,f21,f43,f616,f617,f620,f274",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    url = "http://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=10, context=fetcher._NO_VERIFY_CTX) as r:
            d = json.loads(r.read().decode("utf-8"))
        diff = (d.get("data") or {}).get("diff") or []
        for x in diff:
            print(code, x)
        if not diff:
            print(code, "-> no diff", str(d)[:300])
    except Exception as e:
        print(code, "err", e)

for c in ("001203", "605018"):
    q(c)