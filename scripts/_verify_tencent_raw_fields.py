"""模拟交易时段数据, 测试腾讯兜底 → scorer 评分链路"""
import sys, json, ssl, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import security
tok = security.issue_token(49, remember=True)

req = urllib.request.Request(
    "https://127.0.0.1/api/stocks?mode=spot&action=lock",
    headers={"Authorization": "Bearer " + tok})
data = json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read())
print("ok:", data.get("ok"))
print("rows:", len(data.get("rows", [])))
spot_map = data.get("spotMap", {})
print("spotMap 条数:", len(spot_map))

# 检查 spotMap 中的 f616/成交额分布
nz = 0
for code, q in spot_map.items():
    amt = q.get("amount", q.get("turnover", 0))
    if amt and amt > 0:
        nz += 1
print(f"spotMap 成交额>0 的只数: {nz}")

# 看 market_brief
try:
    req2 = urllib.request.Request("https://127.0.0.1/api/market/brief",
                                   headers={"Authorization": "Bearer " + tok})
    data2 = json.loads(urllib.request.urlopen(req2, timeout=30, context=ctx).read())
    print("market_brief:", json.dumps(data2, ensure_ascii=False)[:200])
except Exception as e:
    print("market_brief ERR:", e)

# 关键: 让 fetcher refresh 后看 raw 字段
from app.services import fetcher
# 强制清缓存重 fetch
fetcher._cache.clear()
fetcher._quote_map_cache.clear()
raw, err = fetcher.ensure_spot_cache("refresh", "m:1+t:2", True)
print()
print(f"=== raw 字段抽样 (周日下午18:30非交易,成交额大都=0) ===")
print(f"raw 条数: {len(raw)}")
if raw:
    f2_ok = sum(1 for r in raw if r.get("f2") and float(r.get("f2") or 0) > 0)
    f21_ok = sum(1 for r in raw if r.get("f21") and float(r.get("f21") or 0) > 0)
    f615_ok = sum(1 for r in raw if r.get("f615") is not None)
    f616_nz = sum(1 for r in raw if r.get("f616") and float(r.get("f616") or 0) > 0)
    print(f"  f2现价>0: {f2_ok}/{len(raw)}")
    print(f"  f21流通市值>0: {f21_ok}/{len(raw)}")
    print(f"  f615竞价涨幅有值: {f615_ok}/{len(raw)}")
    print(f"  f616竞价金额>0(非交易时为0正常): {f616_nz}/{len(raw)}")
    # 检查字段类型是否符合预期
    sample = raw[0]
    for k in ["f2", "f3", "f21", "f615", "f616", "f8"]:
        v = sample.get(k)
        print(f"  raw[0].{k}={v!r} ({type(v).__name__})")
