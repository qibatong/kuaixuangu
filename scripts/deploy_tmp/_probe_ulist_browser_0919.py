# -*- coding: utf-8 -*-
"""只读: 服务端点查(ulist.np)长期 RemoteDisconnected —— 换"浏览器完整特征"再试, 判断
是"接口被封"还是"客户端特征被杀"。这直接决定"补丁源能否也搬到用户端"。"""
import ssl, json, urllib.request

CTX = ssl._create_unverified_context()
BROWSER_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Referer": "https://quote.eastmoney.com/",
    "Origin": "https://quote.eastmoney.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}
PLAIN_HDR = {"User-Agent": "python-urllib/3.11"}

CASES = [
    ("ulist.np 点查(浏览器特征)", "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
     "&fields=f2,f3,f8,f12,f14,f21,f615,f630&secids=1.600000,0.000001,0.300750&ut=c92c50e6b0fab2c17cd5e276e9a79c42", BROWSER_HDR),
    ("ulist.np 点查(裸特征)", "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
     "&fields=f2,f3,f8,f12,f14,f21,f615,f630&secids=1.600000,0.000001,0.300750&ut=c92c50e6b0fab2c17cd5e276e9a79c42", PLAIN_HDR),
    ("clist 前200(浏览器特征)", "https://push2dycalc.eastmoney.com/api/qt/clist/get?fs=m:1+t:2&fltt=2&invt=2"
     "&fields=f2,f3,f8,f12,f14,f21,f615,f630&fid=f3&po=1&pn=1&pz=200&np=1&ut=c92c50e6b0fab2c17cd5e276e9a79c42", BROWSER_HDR),
]

for name, url, hdr in CASES:
    try:
        req = urllib.request.Request(url, headers=hdr)
        r = urllib.request.urlopen(req, timeout=20, context=CTX)
        raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        data = d.get("data") or {}
        rows = data.get("diff") or data.get("list") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        print("  [OK] %-26s HTTP %s  行数=%d" % (name, r.status, len(rows)))
        for s in rows[:3]:
            print("        %s f2=%s f3=%s f8=%s f21=%s f615=%s f630=%s"
                  % (s.get("f12"), s.get("f2"), s.get("f3"), s.get("f8"),
                     s.get("f21"), s.get("f615"), s.get("f630")))
    except Exception as e:
        print("  [FAIL] %-24s %s: %s" % (name, type(e).__name__, str(e)[:80]))

print()
print("--- 点查带完整 secid 前缀格式对比 ---")
for sid in ["1.600000", "0.000001", "1.688469"]:
    try:
        url = ("https://push2.eastmoney.com/api/qt/stock/get?fltt=2&invt=2&fields=f43,f57,f58,f630,f615,f8,f21"
               "&secid=%s&ut=c92c50e6b0fab2c17cd5e276e9a79c42" % sid)
        req = urllib.request.Request(url, headers=BROWSER_HDR)
        r = urllib.request.urlopen(req, timeout=15, context=CTX)
        d = json.loads(r.read().decode("utf-8", "replace"))
        dd = d.get("data") or {}
        print("  [OK] stock/get secid=%s name=%s f43=%s f630=%s" % (sid, dd.get("f58"), dd.get("f43"), dd.get("f630")))
    except Exception as e:
        print("  [FAIL] stock/get secid=%s %s: %s" % (sid, type(e).__name__, str(e)[:70]))
