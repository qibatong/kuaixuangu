# -*- coding: utf-8 -*-
"""只读探针: 回答"用户端浏览器直连东财 clist 取前200" 的两个关键问题
1) CORS: 带 Origin 请求时东财是否回 Access-Control-Allow-Origin (决定浏览器能否直连)
2) 前200 样本里 f630 的密度/档位分布 (对比全市场 21.7% 非零)
"""
import json, ssl, urllib.request

CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
BASE = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
FIELDS = "f2,f3,f4,f5,f6,f8,f10,f12,f14,f15,f16,f17,f18,f20,f21,f615,f630,f100,f102,f103"

def fetch(pn, pz, fid="f3", po=1, fs="m:1+t:2,m:0+t:6", with_origin=True):
    url = ("%s?fs=%s&fltt=2&invt=2&fields=%s&fid=%s&po=%d&pn=%d&pz=%d&np=1&ut=%s"
           % (BASE, fs, FIELDS, fid, po, pn, pz, UT))
    hdr = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    if with_origin:
        hdr["Origin"] = "https://kuaixuangu.cn"
    req = urllib.request.Request(url, headers=hdr)
    r = urllib.request.urlopen(req, timeout=25, context=CTX)
    return r, json.loads(r.read().decode("utf-8"))

print("=" * 78)
print("【问题1】CORS —— 带 Origin: https://kuaixuangu.cn 请求, 东财回什么头?")
print("=" * 78)
r, d = fetch(1, 5)
for k, v in r.headers.items():
    if "access" in k.lower() or "origin" in k.lower() or "cross" in k.lower():
        print("  %-34s = %s" % (k, v))
print("  (以上为全部 CORS 相关头; 若为空 = 浏览器直连会被 CORS 拦截)")

print()
print("=" * 78)
print("【问题2】前200 (fid=f3&po=1 涨幅降序) 的 f630 密度 vs 全市场")
print("=" * 78)

def dist(rows):
    c = {}
    for s in rows:
        v = s.get("f630")
        v = 0 if v in ("-", None, "") else int(v)
        c[v] = c.get(v, 0) + 1
    return c

# 前200 第一页
_, d200 = fetch(1, 200, fid="f3", po=1)
rows200 = d200["data"]["diff"]
c200 = dist(rows200)
n200 = len(rows200)
nz200 = sum(v for k, v in c200.items() if k != 0)
print("  第一页 200 只: 总 %d, 非0 %d (%.1f%%)" % (n200, nz200, 100.0 * nz200 / max(n200, 1)))
print("  档位分布(降序): %s" % sorted(c200.items(), key=lambda x: -x[0]))
hi200 = sum(v for k, v in c200.items() if k >= 3)
print("  ≥3 档(真正高分档) 只数 = %d (%.1f%%)" % (hi200, 100.0 * hi200 / max(n200, 1)))

# 全市场分页
print()
tot, nzt, hit = 0, 0, 0
call = {}
for pn in range(1, 31):
    try:
        _, dp = fetch(pn, 200, fid="f12", po=0)
        rs = (dp.get("data") or {}).get("diff") or []
        if not rs:
            break
        tot += len(rs)
        for s in rs:
            v = s.get("f630")
            v = 0 if v in ("-", None, "") else int(v)
            if v:
                nzt += 1
            if v >= 3:
                hit += 1
        call[pn] = len(rs)
    except Exception as e:
        print("  pn=%d 失败: %s" % (pn, str(e)[:60]))
print("  全市场: 总 %d, 非0 %d (%.1f%%), ≥3档 %d (%.1f%%)"
      % (tot, nzt, 100.0 * nzt / max(tot, 1), hit, 100.0 * hit / max(tot, 1)))
print("  成功分页: %s" % sorted(call.items()))

print()
print("=" * 78)
print("【问题3】前200 用 fid=f615(竞价涨幅) 排序 vs fid=f3(当日涨跌幅) 排序 差异")
print("=" * 78)
try:
    _, d615 = fetch(1, 200, fid="f615", po=1)
    rows615 = (d615.get("data") or {}).get("diff") or []
    s3 = set(s["f12"] for s in rows200)
    s615 = set(s["f12"] for s in rows615)
    print("  fid=f615 前200 有数据: %d 只" % len(rows615))
    print("  两者交集: %d 只 / 并集: %d 只  ==> 差异 %d 只"
          % (len(s3 & s615), len(s3 | s615), len(s3 ^ s615)))
except Exception as e:
    print("  fid=f615 排序实测失败: %s" % str(e)[:100])
