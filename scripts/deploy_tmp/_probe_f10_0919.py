# -*- coding: utf-8 -*-
"""只读: 钉死 clist 里 f8/f10/f20/f21 的真实语义(原版把 f10 当流通市值用, 需核实)"""
import json, ssl, urllib.request
CTX = ssl._create_unverified_context()
URL = ("https://push2dycalc.eastmoney.com/api/qt/clist/get?fs=m:1+t:2&fltt=2&invt=2"
       "&fields=f2,f3,f5,f8,f10,f12,f14,f20,f21,f615,f630,f100&fid=f12&po=0&pn=1&pz=8&np=1"
       "&ut=c92c50e6b0fab2c17cd5e276e9a79c42")
req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
d = json.loads(urllib.request.urlopen(req, timeout=20, context=CTX).read().decode("utf-8"))
rows = d["data"]["diff"]
hdr = ("code", "name", "f2价", "f8换手", "f10", "f20总市值", "f21流通市值", "f5量(手)", "f615", "f630")
print("%-8s %-8s %10s %10s %12s %16s %16s %12s %8s %6s" % hdr)
for s in rows:
    print("%-8s %-8s %10s %10s %12s %16s %16s %12s %8s %6s" % (
        s.get("f12"), s.get("f14"), s.get("f2"), s.get("f8"), s.get("f10"),
        s.get("f20"), s.get("f21"), s.get("f5"), s.get("f615"), s.get("f630")))
print()
print("--- 自洽: 换手率 f8(%) 应 ≈ f5*100股*f2/f21流通市值*100 ---")
for s in rows:
    try:
        f5, f2, f21, f8 = float(s["f5"]), float(s["f2"]), float(s["f21"]), float(s["f8"])
        calc = f5 * 100 * f2 / f21 * 100
        print("  %-8s 实测f8=%-9s 算得=%.4f 比值=%.2f   总市值/流通=%.2f" % (
            s["f12"], f8, calc, (calc / f8) if f8 else 0,
            (float(s["f20"]) / f21) if f21 else 0))
    except Exception as e:
        print("  %-8s 跳过 %s" % (s.get("f12"), e))
