# -*- coding: utf-8 -*-
import sys, os, json, urllib.request
sys.path.insert(0, "/workspace/backend")
os.chdir("/workspace/backend")
from app.services import fetcher

# 腾讯分时: index 价格/量/额
def tencent_minute(code):
    secid = "sz001203" if code == "001203" else "sh" + ("605018" if code=="605018" else "003000")
    secidmap = {"001203":"sz001203","605018":"sh605018"}
    s = secidmap.get(code, "sz" + code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + s
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        data = d["data"][s]["data"]["data"] if d.get("data",{}).get(s,{}).get("data",{}).get("data") else (d["data"][s].get("data") if d.get("data",{}).get(s) else None)
        print(code, "raw keys:", list(d.get("data",{}).get(s,{}).keys()) if d.get("data",{}).get(s) else "none")
        return data
    except Exception as e:
        print(code, "tencent minute err", str(e)[:200])
        return None

# 广发/腾讯每分钟: "093000 价格 累计量 累计额"
for code in ("001203",):
    data = tencent_minute(code)
    if data:
        rows = data.split(";") if isinstance(data, str) else data
        print("rows count:", len(rows) if rows else 0)
        for i, row in enumerate((rows or [])[:3]):
            print("  first row:", row)
            break
        # 找到 09:25 附近的累计额
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, str) and row.startswith(("0925","0930","09:25","09:30")):
                    print("  at open:", row)
                    break