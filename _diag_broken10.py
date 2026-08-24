# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
from app.services import fetcher

for code in ("001203", "605018", "001319"):
    for period in ("minute", "day"):
        try:
            d = fetcher.fetch_stock_chart_robust(code, period)
            if not d:
                print(code, period, "empty"); continue
            print("=== %s %s ===" % (code, period))
            amt = d.get("amount") or []
            price = d.get("close") or []
            vol = d.get("volume") or []
            times = d.get("time") or []
            n = min(6, len(amt))
            for i in range(n):
                print("   %s close=%s vol=%s amt=%s" % (times[i] if i < len(times) else i, price[i] if i < len(price) else '?', vol[i] if i < len(vol) else '?', amt[i] if i < len(amt) else '?'))
        except Exception as e:
            print(code, period, "err", e)