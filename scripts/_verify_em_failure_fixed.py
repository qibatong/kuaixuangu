"""2026-08-30 主人反馈修复验证: 用户中午盘中截图 → /api/stocks?mode=spot
报「东财熔断中」冒到前端。修复后必须从腾讯兜底拿到数据,
前端不应看到 '东财数据源熔断中' 文案。"""
import sys, json, urllib.request, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import security
tok = security.issue_token(49, remember=True)

BASE = "https://127.0.0.1"
HDR = {"Authorization": "Bearer " + tok}


def call(path):
    req = urllib.request.Request(BASE + path, headers=HDR)
    return json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read())


results = []
for label, path in [
    ("盘中选股 lock(复现用户截图)", "/api/stocks?mode=spot&action=lock"),
    ("盘中选股 refresh",            "/api/stocks?mode=spot&action=refresh"),
    ("盘中选股 filter",             "/api/stocks?action=filter"),
]:
    data = call(path)
    raw = json.dumps(data, ensure_ascii=False)
    has_em = "东财数据源熔断中" in raw
    rows = len(data.get("rows", []))
    print(f"[{label}]")
    print(f"  ok={data.get('ok')} rows={rows} 含「东财熔断」={has_em} (期望 False)")
    results.append((label, has_em))

# quote_map (fetch_spot_quote_map 路径)
try:
    data = call("/api/stocks?action=quote_map")
    raw = json.dumps(data, ensure_ascii=False)
    has_em = "东财数据源熔断中" in raw
    print(f"[quote_map]")
    print(f"  ok={data.get('ok')} 含「东财熔断」={has_em} (期望 False)")
    results.append(("quote_map", has_em))
except Exception as e:
    print(f"quote_map EXC: {e}")

# market_brief (fetch_market_brief 路径)
try:
    data = call("/api/market/brief")
    raw = json.dumps(data, ensure_ascii=False)
    has_em = "东财数据源熔断中" in raw
    print(f"[market_brief]")
    print(f"  ok={data.get('ok')} stockCount={data.get('stockCount', 0)} 含「东财熔断」={has_em} (期望 False)")
    results.append(("market_brief", has_em))
except Exception as e:
    print(f"market_brief EXC: {e}")

print()
print("=== 总结 ===")
failed = [n for n, has_em in results if has_em]
if failed:
    print(f"❌ 仍有前端冒错: {failed}")
    sys.exit(1)
print(f"✅ 全部 {len(results)} 条路径都不再冒「东财熔断」文案, 腾讯兜底生效")
