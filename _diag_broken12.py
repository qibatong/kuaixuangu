# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/workspace/backend")
os.chdir("/workspace/backend")
from app.services import fetcher

for code in ("001203",):
    for period in ("minute", "day"):
        try:
            d = fetcher.fetch_stock_chart_robust(code, period)
            if not d:
                print(code, period, "empty"); continue
            print("=== %s %s source? ===" % (code, period))
            amt = d.get("amount") or []
            price = d.get("close") or []
            vol = d.get("volume") or []
            times = d.get("time") or []
            print("  n_amt=%d times[-3:]=" % len(amt), times[-3:] if times else [])
            for i in range(min(5, len(amt))):
                print("   [%d] %s close=%s vol=%s amt=%s" % (i, times[i] if i < len(times) else '?', price[i] if i < len(price) else '?', vol[i] if i < len(vol) else '?', amt[i]))
            if period == "day" and len(times) >= 2:
                print("  LAST DAY: %s close=%s" % (times[-1], price[-1] if price else '?'))
        except Exception as e:
            print(code, period, "err", str(e)[:200])