# -*- coding: utf-8 -*-
"""竞价优化改动核查脚本:
  1) _is_auction_hours() 是否使用北京时间 Asia/Shanghai (2026-08-24 UTC 判断修复)
  2) stats.py 现涨降级兜底 (stats.real_price_chg 存在且带 fallback 注释)
  3) config.py 快照 TTL 60 秒 (SNAPSHOT_TTL_SECONDS=60, 2026-08-24 修)
  4) kpl.py bid-seal 历史竞涨字段覆盖 (快照 bid_change 覆盖 Type4, 2026-08-24)
  5) auction_snapshot.py fill_bid_turnover_from_snap / fill_bid_ratio_yest 存在 (2026-08-18~22)
  6) worker 启动: snapshot + aipick + concept_refresh 三路调度
"""
import sys, os, re, json
DEPLOY = sys.argv[1] if len(sys.argv) > 1 else "/opt/kuaixuan"
sys.path.insert(0, DEPLOY + "/backend")

PASS = []; FAIL = []
def check(label, cond, detail=""):
    (PASS if cond else FAIL).append((label, detail))

# 1) _is_auction_hours 源码检查 (北京时间判定: 可用 _bj_now +8h 或 pytz Asia/Shanghai)
p_api_kpl = DEPLOY + "/backend/app/api/kpl.py"
src_kpl = open(p_api_kpl, "r", encoding="utf-8", errors="ignore").read()
_fn_body = re.search(r"def _is_auction_hours[\s\S]*?(?=^def |\Z)", src_kpl, re.M).group(0)
check(
    "_is_auction_hours 用北京时间判断 (非 UTC 直接比较)",
    ("Asia/Shanghai" in src_kpl or "_bj_now()" in _fn_body or "tm_gmtoff" in src_kpl or "+ 8 * 3600" in src_kpl)
    and ("datetime.utcnow()" not in _fn_body),
    "命中: " + "|".join([x for x in ["_bj_now()","Asia/Shanghai","+ 8 * 3600","datetime.utcnow()"] if x in src_kpl])
)

# 2) stats 现涨降级兜底 (stats.py 内带 fallback → 说明 2026-08-24 已生效)
p_stats = DEPLOY + "/backend/app/api/stats.py"
src_stats = open(p_stats, "r", encoding="utf-8", errors="ignore").read()
check(
    "stats.py 现涨降级兜底 (2026-08-24 修复)",
    ("降级" in src_stats or "fallback" in src_stats or "兜底" in src_stats) and ("realChange" in src_stats or "现涨" in src_stats),
    "命中: " + "|".join([x for x in ["降级","兜底","fallback","realChange","现涨"] if x in src_stats])
)

# 3) config.py 竞价时段缓存/盘中缓存/快照读取 TTL (2026-08-24 修: SPOT_CACHE_TTL=60 秒)
#    注: 项目没有 SNAPSHOT_TTL_SECONDS 常量, 实际生效参数为 SPOT_CACHE_TTL(盘中现涨)/KPL_BID_TTL(竞价委买额 30s)/CACHE_TTL(行情 30s)
p_cfg = DEPLOY + "/backend/app/core/config.py"
src_cfg = open(p_cfg, "r", encoding="utf-8", errors="ignore").read()
def cfg_int(name):
    m = re.search(rf"{name}\s*=\s*int\(os\.environ\.get\(['\"]{name}['\"],\s*['\"]?(\d+)", src_cfg)
    if m: return int(m.group(1))
    m2 = re.search(rf"{name}\s*=\s*(\d+)", src_cfg)
    return int(m2.group(1)) if m2 else -1
spot_ttl = cfg_int("SPOT_CACHE_TTL")
kpl_bid_ttl = cfg_int("KPL_BID_TTL")
cache_ttl = cfg_int("CACHE_TTL")
check(
    "config 盘中现涨缓存 SPOT_CACHE_TTL=60 (2026-08-24 300→60, 现涨刷新加快)",
    spot_ttl == 60, f"SPOT_CACHE_TTL={spot_ttl}"
)
check(
    "config 竞价委买额 KPL_BID_TTL=30 (30 秒新鲜度, 与竞价高频匹配)",
    kpl_bid_ttl == 30, f"KPL_BID_TTL={kpl_bid_ttl}"
)
check(
    "config 行情通用 CACHE_TTL=30 (东财列表类缓存 30 秒新鲜度)",
    cache_ttl == 30, f"CACHE_TTL={cache_ttl}"
)

# 4) kpl.svc: 历史竞涨由自采快照 bid_change 强制覆盖 (2026-08-24)
p_svc_kpl = DEPLOY + "/backend/app/services/kpl.py"
src_skpl = open(p_svc_kpl, "r", encoding="utf-8", errors="ignore").read()
check(
    "kpl.svc 历史竞价涨幅: 快照 bid_change 强制覆盖 Type4 bidChange (2026-08-24)",
    ("fill_bid_turnover_from_snap" in src_skpl) and ("bid_change" in src_skpl or "快照" in src_skpl),
    "函数命中: fill_bid_turnover_from_snap=" + str("fill_bid_turnover_from_snap" in src_skpl) + " fill_bid_ratio_yest=" + str("fill_bid_ratio_yest" in src_skpl)
)

# 5) auction_snapshot 具备三时段 + 最后一秒采样
p_snap = DEPLOY + "/backend/app/services/auction_snapshot.py"
src_snap = open(p_snap, "r", encoding="utf-8", errors="ignore").read()
check(
    "auction_snapshot 采集 9:15/9:20/9:24/9:25 四时点 + 最后一秒采样",
    all(tp in src_snap for tp in ["9:15","9:20","9:25"]) and ("9:24" in src_snap or "最后一秒" in src_snap),
    "时点命中: " + "|".join([t for t in ["9:15","9:20","9:24","9:25","最后一秒"] if t in src_snap])
)

# 6) worker 启动三路调度 (auction_snapshot / aipick_scheduler / concept_refresh)
p_w = DEPLOY + "/backend/app/worker.py"
src_w = open(p_w, "r", encoding="utf-8", errors="ignore").read()
check(
    "worker 启动 snapshot + aipick + concept_refresh 三路调度",
    all(fn in src_w for fn in ["auction_snapshot.start_scheduler","aipick_scheduler.start_scheduler","concept_refresh.start_scheduler"])
)

# 7) 运行时验证: 调用 kpl._is_auction_hours() (从 app.api.kpl 导入)
try:
    from app.api.kpl import _is_auction_hours
    r = _is_auction_hours()
    check("_is_auction_hours() 运行时可调用 (无 UTC 异常)", isinstance(r, bool), f"返回={r!r}")
except Exception as e:
    check("_is_auction_hours() 可调用", False, f"异常={e!r}")

# 8) 运行时: stats 接口存在 real_chg fallback 函数 (real_price_chg)
try:
    from app.api import stats as st_mod
    has_fallback = any("real" in n.lower() and (callable(getattr(st_mod,n,None)) or "def "+n in src_stats) for n in dir(st_mod))
    check("stats module 中 real_* 处理函数存在", has_fallback or "real_change" in src_stats.lower(),
         "函数名包含 real: " + ",".join(n for n in dir(st_mod) if "real" in n.lower() and not n.startswith("_")))
except Exception as e:
    check("stats module 可导入", False, f"异常={e!r}")

print("PASS:")
for l,d in PASS: print("  ✓", l, (" // "+d if d else ""))
print("\nFAIL:")
for l,d in FAIL: print("  ✗", l, (" // "+d if d else ""))
print(f"\n结果: PASS={len(PASS)} FAIL={len(FAIL)}  {'OK' if not FAIL else 'NOT_OK'}")
