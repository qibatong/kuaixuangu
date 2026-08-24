# -*- coding: utf-8 -*-
import json, urllib.request

def tencent_first(code_sec):
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + code_sec
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    node = d.get("data", {}).get(code_sec, {})
    table = node.get("data", {})
    if isinstance(table, dict):
        table = table.get("data")
    segs = table.split(";") if isinstance(table, str) else (table or [])
    out = []
    for row in segs[:4]:
        p = row.split()
        if len(p) >= 4:
            out.append((p[0], p[1], p[2], p[3]))
    return out

for name, sec in [("皮阿诺(今日空竞换)","sz002853"),
                  ("大中矿业(应正常)","sz001203"),
                  ("冀衡医药(今日0.03)","sz002742")]:
    try:
        print(name, tencent_first(sec))
    except Exception as e:
        print(name, "err", str(e)[:160])