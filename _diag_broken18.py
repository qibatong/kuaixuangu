# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/workspace/backend")
os.chdir("/workspace/backend")
from app.services import fetcher, scorer
import json, urllib.request, urllib.parse

# 用东财 stock/get 接口(生产机走不通, 这里用本地沙箱测, 若能通就拿到真实的 f616/f6)
def em_stock(code):
    secid = "0.002853" if code=="002853" else ("0."+code if code[0]=="3" else "1."+code)
    params = {
        "secid": secid, "invt": 2, "fltt": 2,
        "fields": "f2,f3,f5,f6,f12,f14,f17,f18,f21,f43,f44,f45,f48,f49,f57,f58,f100,f102,f615,f616,f617,f620,f621",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    url = "http://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=10, context=fetcher._NO_VERIFY_CTX) as r:
        d = json.loads(r.read().decode("utf-8"))
    data = d.get("data") or {}
    o = {}
    for k in ("f12","f14","f2","f3","f5","f6","f17","f18","f21","f43","f46","f48","f104","f105","f615","f616","f617","f620","f621"):
        o[k] = data.get(k)
    return o

for c in ("002853","001203"):
    try:
        print(c, em_stock(c))
    except Exception as e:
        print(c, "err", str(e)[:160])