# -*- coding: utf-8 -*-
"""只读: 验证东财 clist 的 CORS 是"回显任意 Origin"还是"白名单"—— 决定用户端本地取数是否真的可行"""
import ssl, urllib.request

CTX = ssl._create_unverified_context()
BASE = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"
URL = (BASE + "?fs=m:1+t:2&fltt=2&invt=2&fields=f12,f14,f3,f630&fid=f3&po=1&pn=1&pz=3&np=1"
       "&ut=c92c50e6b0fab2c17cd5e276e9a79c42")

ORIGINS = ["https://kuaixuangu.cn", "https://www.kuaixuangu.cn", "http://localhost:5173",
           "https://evil-example.com", "null"]
for o in ORIGINS:
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": UA, "Origin": o,
                                                  "Referer": "https://quote.eastmoney.com/"})
        r = urllib.request.urlopen(req, timeout=20, context=CTX)
        allow = r.headers.get("access-control-allow-origin")
        cred = r.headers.get("access-control-allow-credentials")
        print("  Origin=%-28s -> ACAO=%s  ACAC=%s" % (o, allow, cred))
    except Exception as e:
        print("  Origin=%-28s -> 失败 %s" % (o, str(e)[:70]))

print()
print("--- 无 Origin (普通请求) 时 ---")
try:
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    r = urllib.request.urlopen(req, timeout=20, context=CTX)
    print("  ACAO=%s" % r.headers.get("access-control-allow-origin"))
except Exception as e:
    print("  失败 %s" % str(e)[:70])

print()
print("--- 同一票 3 次连打, 看 f630/f3 是否稳定(判断盘中是否会漂) ---")
import json, time
for i in range(3):
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    d = json.loads(urllib.request.urlopen(req, timeout=20, context=CTX).read().decode("utf-8"))
    rows = [(s["f12"], s.get("f3"), s.get("f630")) for s in d["data"]["diff"]]
    print("  #%d %s" % (i + 1, rows))
    time.sleep(1.5)
