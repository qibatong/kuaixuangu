# -*- coding: utf-8 -*-
"""只读探针: 交叉分析推断东财 f630 的语义。

手段: 把 f630 与「名称前缀(N=新股首日/C=次新/其他)」「涨跌幅档」交叉,
     并列出每个 f630 取值的样例票 —— 用来判断它到底是
       (a) 异动等级 1~5(评分配置假设的语义),
       (b) 异动类型枚举(含新股/次新等类别码),
       (c) 其它。
纯只读: 只发 GET。
"""
import collections
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import fetcher            # noqa: E402

SECTORS = [
    ("深主板+中小", "m:0+t:6,m:0+t:80"),
    ("沪主板+科创", "m:1+t:2,m:1+t:23"),
    ("北交所",      "m:0+t:81+s:2048"),
]

rows = []
for name, fs in SECTORS:
    try:
        for pg in (1, 2, 3):                     # 每板块 3 页 ≈ 600 只
            rows.extend(fetcher._fetch_clist_page(fs, pg, "f12"))
    except Exception as e:                       # noqa: BLE001
        print("[%s] 抓取失败: %s" % (name, e))

print("总样本 n=%d   时刻=%s" % (len(rows), time.strftime("%F %T")))

# ---- 1) f630 × 名称前缀 ----
pref = collections.defaultdict(collections.Counter)
for x in rows:
    k = str(x.get("f630"))
    nm = str(x.get("f14") or "")
    p = nm[0] if nm and nm[0] in ("N", "C", "U", "W") else ("*" if nm.startswith("*") else "普通")
    pref[k][p] += 1
print("\n=== f630 × 名称前缀 (N=新股首日 C=次新 U/W=特殊) ===")
for k in sorted(pref, key=lambda s: (len(s), s)):
    print("  f630=%-3s n=%-4d %s" % (k, sum(pref[k].values()), dict(pref[k])))

# ---- 2) f630 × 涨跌幅档 ----
print("\n=== f630 × 最新涨跌幅 f3 档 ===")
buck = collections.defaultdict(collections.Counter)
for x in rows:
    try:
        c = float(x.get("f3"))
    except (TypeError, ValueError):
        c = None
    if c is None:
        b = "无"
    elif c >= 9.8:
        b = "涨停(>=9.8)"
    elif c >= 5:
        b = "5~9.8"
    elif c > 0:
        b = "0~5"
    elif c == 0:
        b = "平"
    else:
        b = "跌"
    buck[str(x.get("f630"))][b] += 1
for k in sorted(buck, key=lambda s: (len(s), s)):
    print("  f630=%-3s %s" % (k, dict(buck[k])))

# ---- 3) 每个 f630 取值的样例票 ----
print("\n=== 每个 f630 取值的样例(最多3只) ===")
sample = collections.defaultdict(list)
for x in rows:
    k = str(x.get("f630"))
    if len(sample[k]) < 3:
        sample[k].append("%s %s (涨幅%r 换手%r 量比%r 流通%r亿)" % (
            x.get("f12"), x.get("f14"), x.get("f3"), x.get("f8"),
            x.get("f10"), x.get("f21")))
for k in sorted(sample, key=lambda s: (len(s), s)):
    print("  f630=%-3s :" % k)
    for s in sample[k]:
        print("      " + s)
