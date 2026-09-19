# -*- coding: utf-8 -*-
"""只读: 分板块核实 f630 覆盖率 —— "全市场 5856 只里 1268 非0" 与 "沪深主板 3487 只里 1268 非0"
两个口径撞同一个数, 必须拆开看。"""
import ssl, json, urllib.request

CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"
BASE = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"

SCOPES = [
    ("沪市主板", "m:1+t:2"),
    ("深市主板", "m:0+t:6"),
    ("创业板", "m:0+t:80"),
    ("科创板", "m:1+t:23"),
    ("北交所", "m:0+t:81+s:2048"),
    ("主板合并", "m:1+t:2,m:0+t:6"),
    ("全市场", "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"),
]

def scan(fs, label):
    tot, nz, hit, dash = 0, 0, 0, 0
    dist = {}
    for pn in range(1, 26):
        url = ("%s?fs=%s&fltt=2&invt=2&fields=f12,f14,f3,f630&fid=f12&po=0&pn=%d&pz=200&np=1&ut=%s"
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
            if v in ("-", None, ""):
                dash += 1
                v = 0
            v = int(v)
            dist[v] = dist.get(v, 0) + 1
            if v:
                nz += 1
            if v >= 3:
                hit += 1
    print("  %-8s 总=%-5d 非0=%-5d(%5.1f%%)  ≥3档=%-5d(%5.1f%%)  f630为'-'的=%-5d  分布=%s"
          % (label, tot, nz, 100.0 * nz / max(tot, 1), hit, 100.0 * hit / max(tot, 1), dash,
             sorted(dist.items(), key=lambda x: -x[0])[:8]))
    return tot, nz, dash

print("=" * 100)
print("f630 分板块覆盖率实测（2026-09-19 12:2x, 全量分页）")
print("=" * 100)
for label, fs in SCOPES:
    scan(fs, label)
