# -*- coding: utf-8 -*-
"""只读探针: 钉死"东财 clist 分页失败"的真实性质。

生产日志铁证: 9/18 全天 9332 条 `全市场拉取分页失败`, 错误类型**清一色**
「东方财富接口返回异常」(= rc!=0 或 diff 为空, 即 HTTP 200 + 空体),
且**失败页号只出现在 19~30, page 1~18 零失败** —— 完美按页号分界。

两种假设:
  H1「频率/软限流」: 失败应与页号无关(随机), 且重试/降速后能成功
  H2「深分页到底/越界」: 每个 fs 有真实总页数, 超出即返回空 —— 属正常语义,
                        代码却把它当异常抛, 于是每次调用都白打 12 页

判据: 逐页串行(慢速, 排除频率因素) 请求, 记录每页 rows / total / rc。
     若每个 fs 的"空页"起点 = ceil(total/200)+1 且尾部连续全空 → 坐实 H2。
"""
import json
import ssl
import time
import urllib.parse
import urllib.request

CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
BASE = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
FIELDS = "f2,f3,f12,f14,f21,f615,f630"


def one(fs, pn, pz=200):
    qs = urllib.parse.urlencode({
        "fs": fs, "fltt": 2, "invt": 2, "fields": FIELDS,
        "fid": "f12", "po": 1, "pn": pn, "pz": pz, "np": 1, "ut": UT,
    })
    req = urllib.request.Request(BASE + "?" + qs, headers={
        "User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    try:
        r = urllib.request.urlopen(req, timeout=12, context=CTX)
        raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        rc = d.get("rc")
        data = d.get("data") or {}
        diff = data.get("diff")
        if diff is None:
            return {"kind": "EMPTY", "rc": rc, "total": data.get("total"), "raw": raw[:150]}
        return {"kind": "OK", "rc": rc, "total": data.get("total"), "rows": len(diff),
                "first": diff[0].get("f12") if diff else None}
    except Exception as e:                                   # noqa: BLE001
        return {"kind": "EXC", "err": "%s: %s" % (type(e).__name__, str(e)[:70])}


SCENES = [
    # (标签, fs, 探测页数)  —— 慢速串行, 每页间隔 150ms, 排除频率因素
    ("四板全市场(当前主力 fs)", "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", 33),
    ("沪深主板(9/18 失败最多)", "m:1+t:2,m:0+t:6", 22),
    ("创业板单板", "m:0+t:80", 10),
    ("科创板单板", "m:1+t:23", 10),
]

print("=" * 88)
print("东财 clist 逐页深度实测 (串行 150ms 间隔, 2026-09-19)")
print("=" * 88)
for label, fs, npages in SCENES:
    print()
    print("--- %s ---" % label)
    print("    fs = %s" % fs)
    first_empty = None
    tail_all_empty = True
    for p in range(1, npages + 1):
        r = one(fs, p)
        if r["kind"] == "OK":
            if first_empty is not None:
                tail_all_empty = False
            flag = "  "
            print("    p%-3d %s rows=%-4d total=%s first=%s rc=%s"
                  % (p, flag, r["rows"], r["total"], r["first"], r["rc"]))
        else:
            if first_empty is None:
                first_empty = p
            print("    p%-3d %s %s %s" % (p, "XX", r["kind"],
                                          r.get("raw") or r.get("err")))
        time.sleep(0.15)
    print("    ==> 首个非 OK 页 = %s ; 其后是否全为非 OK = %s" % (first_empty, tail_all_empty))
