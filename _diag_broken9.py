# -*- coding: utf-8 -*-
import sys, os, json, urllib.request, urllib.parse
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
from app.services import fetcher

def q(code):
    secid = "0.001203" if code=="001203" else "1.605018"
    params = {
        "secid": secid, "invt": 2, "fltt": 2,
        "fields": "f2,f3,f5,f6,f14,f17,f18,f20,f21,f43,f44,f45,f46,f47,f48,f49,f57,f58,f100,f102,f117,f273,f274,f615,f616,f617,f618,f620,f621",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    url = "http://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=10, context=fetcher._NO_VERIFY_CTX) as r:
            d = json.loads(r.read().decode("utf-8"))
        data = d.get("data")
        print("=== ", code, "===")
        if data:
            for k in ("f57","f58","f2","f3","f5","f6","f17","f18","f21","f43","f46","f48","f273","f615","f616","f617","f618","f620"):
                print("  %s=%s" % (k, data.get(k)))
            # f616 竞价额(元) 和 f620 估算
        else:
            print("  no data", str(d)[:200])
    except Exception as e:
        print(code, "err", e)

q("001203"); q("605018")