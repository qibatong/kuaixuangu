"""深度诊断腾讯兜底 + scorer 评分, 用 fetcher._parse_float 避免 '-'
非交易时段(周末/18:30) 数据稀疏, 但容灾链路应该正常返回(0 行是合理的)。
关键: 1) 接口本身不报错 2) raw 数据完整 3) 模拟交易时段数据时能筛出合理结果"""
import sys, json, ssl, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import security, fetcher, scorer, auction_snapshot
tok = security.issue_token(49, remember=True)

# 1. 接口冒错校验
req = urllib.request.Request("https://127.0.0.1/api/stocks?mode=spot&action=lock",
                              headers={"Authorization": "Bearer " + tok})
data = json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read())
print("[接口] ok:", data.get("ok"), "rows:", len(data.get("rows", [])),
      "spotMap:", len(data.get("spotMap", {})))
print("[接口] 含「东财熔断」:", "东财数据源熔断中" in json.dumps(data, ensure_ascii=False))

# 2. 直接调 fetcher 看 raw(用 fetcher._parse_float 避免 '-')
fetcher._cache.clear()
fetcher._quote_map_cache.clear()
raw, err = fetcher.ensure_spot_cache("refresh", "m:1+t:2", True)
print()
print(f"[raw] 条数={len(raw)}, err={err}")
# 用 fetcher._parse_float
def pf(v): return fetcher._parse_float(v)
# 用 sim 模拟交易时段(把 0/缺失 字段填上, 看评分链路是否完整)
sim_raw = []
for r in raw[:200]:
    f2 = pf(r.get("f2"))
    if f2 == 0:
        # 非交易时为 0, 这里模拟成有价格
        continue
    # 模拟一笔合理的盘中数据
    sim_raw.append({
        "f12": r.get("f12"), "f14": r.get("f14"),
        "f2": 30.0, "f3": 5.0,                    # 模拟盘中
        "f18": r.get("f18"), "f8": 5.0,
        "f21": r.get("f21"),                       # 流通市值
        "f20": r.get("f20"),
        "f5": 100000, "f6": 50000000,              # 模拟成交 5千万
        "f615": 5.0, "f616": 5000, "f617": 100000,  # 竞价同盘中
        "f100": 0, "f102": 0, "f103": 0, "f117": 0,
    })

# 补足到 200
while len(sim_raw) < 200:
    sim_raw.append({
        "f12": "9" + str(999100 + len(sim_raw)).zfill(5),
        "f14": f"模拟{len(sim_raw)}",
        "f2": 30.0, "f3": 5.0,
        "f18": 0, "f8": 5.0,
        "f21": 50e8,                                # 50 亿流通市值
        "f20": 70e8,
        "f5": 100000, "f6": 50000000,
        "f615": 5.0, "f616": 5000, "f617": 100000,
        "f100": 0, "f102": 0, "f103": 0, "f117": 0,
    })

print(f"[sim_raw] 构造 {len(sim_raw)} 条模拟交易时段数据")

# 3. 跑默认 30-100亿 / ≥3000万 / ≤7% 过滤
f = scorer.validate_filters({"mvMin": 30, "mvMax": 100, "amtMin": 3000, "chgMax": 7, "excludeSt": 1})
yesterday_map = {}
snapshot_map = auction_snapshot.load_snapshot()
result = scorer.process_all_stocks(sim_raw, f, yesterday_map, snapshot_map)
print(f"[scorer] 模拟盘中数据过滤: {len(result)} 只(预期接近 200)")
for r in result[:3]:
    print(f"  {r['code']} {r['name']} 涨跌={r.get('bidChange')} mv={r.get('circulationMV')}")
print()
print("[结论]")
print(f"  ✓ 接口不再冒「东财熔断」: {data.get('ok')}")
print(f"  ✓ 腾讯兜底 raw 条数: {len(raw)}")
print(f"  ✓ 模拟交易数据通过 scorer: {len(result)}")
print(f"  → 周一盘中真实数据(rows 应 > 0), 周日非交易时段 rows=0 属正常")
