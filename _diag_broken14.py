# -*- coding: utf-8 -*-
import sys, os, json, urllib.request
sys.path.insert(0, "/workspace/backend")

def tencent_first(code_sec):
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + code_sec
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    node = d.get("data", {}).get(code_sec, {})
    table = node.get("data", {})
    # 每行: "0930 价 累计量 累计额"
    if isinstance(table, dict):
        table = table.get("data")
    if isinstance(table, str):
        rows = table.split(";")
    else:
        rows = table or []
    xs = []
    for row in (rows or [])[:5]:
        if isinstance(row, str):
            parts = row.split()
            if len(parts) >= 4:
                xs.append((parts[0], parts[1], parts[2], parts[3]))
    return xs

for name, sec in [("康希诺(应正常)","sh688185"), ("大中矿业(存疑)","sz001203"),
                  ("长华集团(存疑)","sh605018"), ("铭科精技(存疑)","sz001319")]:
    try:
        print(name, sec, "->", tencent_first(sec))
    except Exception as e:
        print(name, "err", str(e)[:160])