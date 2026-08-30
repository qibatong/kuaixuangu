"""用正确参数名再验证"""
import sys, json, ssl, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import security, fetcher, scorer, auction_snapshot

# 直接调 fetcher 看 raw(用 fetcher._parse_float)
fetcher._cache.clear()
fetcher._quote_map_cache.clear()
raw, err = fetcher.ensure_spot_cache("refresh", "m:1+t:2", True)
print(f"raw={len(raw)} err={err}")

# 过滤有效数据(f2>0)
active = [r for r in raw if fetcher._parse_float(r.get("f2")) > 0]
print(f"raw 中 f2>0 的活跃股: {len(active)}")

# 用对的参数名
# 30-100亿 / ≤7% / ≥3000万 竞价金额 / 排除 ST / 涨停 / prob/conf≥65%
f = scorer.validate_filters({
    "floatMvFloor": "30", "floatMvGt": "100",
    "bidGt": "7", "bidAmtFloor": "3000",
    "stSuspend": "1", "limitUp": "1",
    "probLt": "0", "confLt": "0",    # 不限 prob
    "priceGt": "100",
})

# 我没有 yesterday_amount, 但现货模式可能不强制(看 score_all_stocks 用法)
import inspect
print("score_all_stocks 是否需要 yesterday_map:")
sig = inspect.signature(scorer.score_all_stocks)
print(sig)

# 看看 score_all_stocks 是否要 yesterday_map
src = inspect.getsource(scorer.score_all_stocks)
# 找带 yesterday 的行
for line in src.split("\n"):
    if "yesterday" in line:
        print("  ", line.strip())

print()
yesterday_map = {}
snapshot_map = auction_snapshot.load_snapshot()
result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map)
print(f"\n[scorer 应用 30-100亿/≤7%/≥3000万 + prob/conf≥0 过滤] 结果: {len(result)} 只")
if result:
    for r in result[:5]:
        print(f"  {r['code']} {r['name']} 涨跌={r.get('bidChange')} mv={r.get('circulationMV')} prob={r['probability']} amt={r.get('bidAmt')}")
