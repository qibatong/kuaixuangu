"""看 score_all_stocks 内部, 为什么 0 行"""
import sys, json, ssl, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import fetcher, scorer

fetcher._cache.clear()
raw, err = fetcher.ensure_spot_cache("refresh", "m:1+t:2", True)
print(f"raw={len(raw)}")

# 看 raw 抽样字段
print()
print("=== raw[0] 关键字段 ===")
sample = raw[0] if raw else {}
for k in ["f2", "f3", "f8", "f10", "f12", "f14", "f18", "f21", "f100", "f615", "f616", "f617", "f630"]:
    print(f"  {k} = {sample.get(k)!r}")
print()

# 跑 score_all_stocks(不应用过滤, 看 prob 分布)
from app.services import auction_snapshot
snapshot_map = auction_snapshot.load_snapshot()
yesterday_map = {}
scored = scorer.score_all_stocks(raw, yesterday_map, snapshot_map)
print(f"=== score_all_stocks 输出 {len(scored)} 只 ===")
# 看 prob 分布
prob_zero = sum(1 for s in scored if s.get("probability", 0) == 0)
prob_low = sum(1 for s in scored if s.get("probability", 0) < 60)
print(f"  probability=0: {prob_zero}/{len(scored)}")
print(f"  probability<60: {prob_low}/{len(scored)}")

# 看一只样本
if scored:
    print()
    print(f"=== scored[0] 关键字段 ===")
    for k, v in list(scored[0].items())[:20]:
        if k != "_raw":
            print(f"  {k} = {v!r}")
    # factors
    if scored[0].get("factors"):
        print(f"  factors = {scored[0]['factors']}")
    # _raw 关键
    raw_data = scored[0].get("_raw", {})
    for k in ["f2", "f3", "f8", "f18", "f21", "f100", "f615", "f616", "f617"]:
        print(f"  _raw.{k} = {raw_data.get(k)!r}")
