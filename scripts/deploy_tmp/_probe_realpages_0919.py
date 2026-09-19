# -*- coding: utf-8 -*-
"""只读验证: 若按"真实页数 = ceil(total/pz)"采集(即修复越界误判后的语义),
cyb/kcb/hs 各能拿到多少只、f630 覆盖多少。

对照现状: cyb/kcb 因越界页触发「过半失败」→ 整批放弃 → 走腾讯兜底 → f630 恒 0。
"""
import json
import math
import ssl
import time
import urllib.parse
import urllib.request

CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
BASE = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
FIELDS = "f2,f3,f12,f14,f21,f615,f630"
PZ = 200


def page(fs, pn):
    qs = urllib.parse.urlencode({"fs": fs, "fltt": 2, "invt": 2, "fields": FIELDS,
                                 "fid": "f12", "po": 1, "pn": pn, "pz": PZ, "np": 1, "ut": UT})
    req = urllib.request.Request(BASE + "?" + qs, headers={
        "User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    r = urllib.request.urlopen(req, timeout=12, context=CTX)
    d = json.loads(r.read().decode("utf-8", "replace"))
    if d.get("rc") == 102 or not d.get("data"):
        return None, None          # 翻到底
    data = d["data"]
    return data.get("diff") or [], data.get("total")


SCENES = [("hs 沪深主板", "m:1+t:2,m:0+t:6"),
          ("cyb 创业板", "m:0+t:80"),
          ("kcb 科创板", "m:1+t:23")]

print("=" * 86)
print("按真实页数采集(修复后语义) —— 各分区可得数量与 f630 覆盖")
print("=" * 86)
grand = {"tot": 0, "nz": 0, "hi": 0}
for label, fs in SCENES:
    diff0, total = page(fs, 1)
    if diff0 is None:
        print("%-12s 首页即空(异常)" % label)
        continue
    pages = int(math.ceil(total / float(PZ)))
    codes, dist = set(), {}
    for p in range(1, pages + 1):
        diff = diff0 if p == 1 else page(fs, p)[0]
        if not diff:
            continue
        for s in diff:
            c = s.get("f12")
            if not c:
                continue
            codes.add(c)
            v = s.get("f630")
            v = 0 if v in ("-", None, "") else int(v)
            dist[v] = dist.get(v, 0) + 1
        time.sleep(0.12)
    nz = sum(cnt for k, cnt in dist.items() if k)
    hi = sum(cnt for k, cnt in dist.items() if k >= 3)
    grand["tot"] += len(codes)
    grand["nz"] += nz
    grand["hi"] += hi
    print("%-12s total=%-5d 页数=%-3d 实得=%-5d f630非0=%-5d(%5.1f%%) ≥3档=%-4d(%5.1f%%)"
          % (label, total, pages, len(codes), nz, 100.0 * nz / max(len(codes), 1),
             hi, 100.0 * hi / max(len(codes), 1)))
    print("             档位分布: %s" % sorted(dist.items(), key=lambda x: -x[0])[:8])

print()
print("合计: %d 只, f630 非0 %d (%.1f%%), ≥3档 %d"
      % (grand["tot"], grand["nz"], 100.0 * grand["nz"] / max(grand["tot"], 1), grand["hi"]))
print()
print("对照现状: cyb+kcb 共 2073 只走腾讯兜底 → f630 恒 0 → 17%% 异动因子全员落 default")
