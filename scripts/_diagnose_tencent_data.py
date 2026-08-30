"""2026-08-30 深度诊断: 腾讯兜底返回的 raw 数据, 看 scorer 过滤后为什么 rows=0"""
import sys, json, urllib.request, ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import security
tok = security.issue_token(49, remember=True)

req = urllib.request.Request(
    "https://127.0.0.1/api/stocks?mode=spot&action=lock",
    headers={"Authorization": "Bearer " + tok})
data = json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read())
print("=== /api/stocks 接口返回(spot+lock) ===")
print("ok:", data.get("ok"))
print("msg:", data.get("msg", "")[:200])
print("rows:", len(data.get("rows", [])))
print("total:", data.get("total"))
print("=== spotMap 抽样(看腾讯兜底返回字段) ===")
spot_map = data.get("spotMap", {})
print("spotMap 总条数:", len(spot_map))
for code in list(spot_map.keys())[:3]:
    print(f"  {code}: {json.dumps(spot_map[code], ensure_ascii=False)}")

# 直接调 fetcher 看 raw(debug)
print()
print("=== 直接调 fetcher 看 raw 和过滤结果 ===")
from app.services import fetcher, scorer, auction_snapshot
raw, err = fetcher.ensure_spot_cache("refresh", "m:1+t:2", True)
print("raw 条数:", len(raw))
print("err:", err)
if raw:
    print("raw[0] keys:", list(raw[0].keys())[:20])
    print("raw[0] f21 (流通市值):", raw[0].get("f21"))
    print("raw[0] f21 类型:", type(raw[0].get("f21")).__name__)
    # 跑默认打分过滤
    f = scorer.validate_filters({"mvMin": 30, "mvMax": 100, "amtMin": 3000, "chgMax": 7, "excludeSt": 1})
    yesterday_map = {}
    snapshot_map = auction_snapshot.load_snapshot()
    result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map)
    print(f"scorer filter 结果: {len(result)} 只")
    for r in result[:3]:
        print(f"  {r['code']} {r['name']} 涨跌={r.get('bidChange')} mv={r.get('circulationMV')}")

print()
print("=== fetch_yesterday_amounts 真实行为 ===")
codes = [r.get("f12") for r in raw[:50] if r.get("f12")]
y_map = fetcher.fetch_yesterday_amounts(codes)
print(f"  入参 {len(codes)} 只代码, 返回 {len(y_map)} 条昨比, 命中率 {len(y_map)*100/len(codes):.0f}%")
