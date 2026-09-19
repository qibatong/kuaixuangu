# -*- coding: utf-8 -*-
"""只读: 拿 f630 的完整分布(全市场 / 主板), 用于校正文档里的占比表述"""
import ssl, json, urllib.request

CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"
BASE = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
SC = [("全市场", "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"),
      ("沪深主板", "m:1+t:2,m:0+t:6")]

def scan(fs, label):
    dist, tot = {}, 0
    for pn in range(1, 35):
        url = ("%s?fs=%s&fltt=2&invt=2&fields=f12,f630&fid=f12&po=0&pn=%d&pz=200&np=1&ut=%s"
               % (BASE, fs, pn, UT))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
            d = json.loads(urllib.request.urlopen(req, timeout=25, context=CTX).read().decode("utf-8"))
        except Exception as e:
            print("    pn=%d 失败 %s" % (pn, str(e)[:50]))
            continue
        rs = (d.get("data") or {}).get("diff") or []
        if not rs:
            break
        for s in rs:
            tot += 1
            v = s.get("f630")
            v = 0 if v in ("-", None, "") else int(v)
            dist[v] = dist.get(v, 0) + 1
    nz = tot - dist.get(0, 0)
    print("  【%s】总 %d, 非0 %d (%.1f%%)" % (label, tot, nz, 100.0 * nz / max(tot, 1)))
    print("    完整档位分布: %s" % sorted(dist.items(), key=lambda x: -x[1]))
    for k in [0, 1, 2, 3, 4, 5, 9, 10]:
        c = dist.get(k, 0)
        print("      f630=%-3d : %-5d (%5.2f%%)" % (k, c, 100.0 * c / max(tot, 1)))
    print()

for label, fs in SC:
    scan(fs, label)
