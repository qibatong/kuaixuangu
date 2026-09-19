# -*- coding: utf-8 -*-
"""2026-09-19 只读验证: 采集侧(full=True)现在能否产出 f630 异动等级

这是"周一 9:25 定格会不会有异动值"的最终前置证明:
  fetch_eastmoney_all(修复后) → _fetch_market_map(full=True) → {code: {..., warn_type}}
只读, 不写库(snapshot_at 才是写库那一步, 本探针不调用)。
"""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from app.services import auction_snapshot as A  # noqa: E402
from app.services import scorer  # noqa: E402

print("=" * 78)
print("采集侧 full=True 验证（%s）" % time.strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 78)

for mk in (["hs"], ["cyb"], ["kcb"], ["hs", "cyb", "kcb"]):
    print("  分区 %-16s fs=%s" % ("+".join(mk), scorer.market_fs(mk)))
print()

t0 = time.time()
m = A._fetch_market_map(full=True)
ms = (time.time() - t0) * 1000

miss = sum(1 for v in m.values() if "warn_type" not in v)
nz = sum(1 for v in m.values() if int(v.get("warn_type") or 0) != 0)
hi = sum(1 for v in m.values() if int(v.get("warn_type") or 0) >= 3)
dist = {}
for v in m.values():
    k = int(v.get("warn_type") or 0)
    dist[k] = dist.get(k, 0) + 1

print("采集结果: 只数=%d 耗时=%.0fms" % (len(m), ms))
print("  缺 warn_type 键的行 = %d（应为 0）" % miss)
print("  warn_type 非 0       = %d (%.1f%%)  -> 17%% 异动因子的有效样本"
      % (nz, 100.0 * nz / max(len(m), 1)))
print("  warn_type >=3 档     = %d (%.1f%%)  -> 真正拿高权重(0.6~1.0)的票"
      % (hi, 100.0 * hi / max(len(m), 1)))
print("  档位分布(前 8): %s" % sorted(dist.items(), key=lambda x: -x[1])[:8])
print()
print("判定: %s" % ("采集侧已能产出 f630" if nz else "仍拿不到 f630"))
