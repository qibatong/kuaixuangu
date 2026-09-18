# -*- coding: utf-8 -*-
"""只读探针: 17% 因子「东财 f630」A/B 实证 (2026-09-18 夜, 测试机)

目的: 证明 `use_bid_strength=0` 之后**主链路真的改走 f630**, 而不是静默仍用竞价强度。
手法: 同一批候选上并列三条路 ——
  A 竞价强度层本身有多少数据(绕过开关直接 load; 证明强度层非空, 不是"没数据才为 0")
  B 主链路 _load_strength 是否短路返回 {} —— 开关生效的直接指纹
  C 跑一次完整 pipeline.run(), 看输出 warnType 来自哪条路
"""
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")

import types                                                        # noqa: E402

from app.db import database                                         # noqa: E402
from app.services import bid_strength, scorer, settings              # noqa: E402
from app.services.picker import pipeline as pl                       # noqa: E402
from app.services.picker.mode import bj_date                         # noqa: E402

date = bj_date()
print("日期 = %s   use_bid_strength = %r" % (date, settings.get("use_bid_strength")))

conn = database.get_conn()
codes = [r[0] for r in conn.execute(
    "SELECT code FROM snapshot_bid WHERE date=? AND time_point='9_25' LIMIT 400",
    (date,)).fetchall()]
conn.close()
print("候选样本 = %d 只(当日 9_25 定格前 400 只)" % len(codes))

print()
print("=" * 78)
print("A 竞价强度层本身的数据量(绕过 enabled() 直接 load —— 对照组)")
print("=" * 78)
st = bid_strength.load(codes, date=date)
sm = {c: v for c, v in bid_strength.score_map(st).items() if v is not None}
print("  load 取到 %d 只, 合成出分 %d 只" % (len(st), len(sm)))
if sm:
    vs = sorted(sm.values())
    print("  分值 中位 %.2f 最大 %.2f" % (vs[len(vs) // 2], vs[-1]))
    gear = {5: 0, 4: 0, 3: 0, 0: 0}
    for v in sm.values():
        gear[5 if v >= 0.85 else 4 if v >= 0.65 else 3 if v >= 0.40 else 0] += 1
    print("  ⇒ 若走竞价强度, 档位分布会是 %s" % gear)
print("  ⚠️ 这一点很关键: 强度层**有数据**, 所以下面输出若不含 3/4/5, 就是开关真的生效了")

print()
print("=" * 78)
print("B 主链路 _load_strength(开关指纹)")
print("=" * 78)
ctx = types.SimpleNamespace(strengths={}, date=date)
got = pl._load_strength(codes, ctx)
print("  _load_strength → %r  ({} = 已短路, 异动因子退回 f630)" % got)

print()
print("=" * 78)
print("C 完整 pipeline.run()(真实链路, 不落库)")
print("=" * 78)
f = scorer.validate_filters({"markets": ["hs,cyb,kcb"]})
t0 = time.time()
res = pl.run(f)
print("  耗时 %.1fs  mode=%s  sources=%s" % (time.time() - t0, res.mode, res.sources))
print("  universe=%s candidate=%s kept=%s" % (res.n_universe, res.n_candidate,
                                              len(res.items)))
print("  errors=%s  degraded=%s" % (res.errors, res.degraded))
print("  stats=%s" % res.stats)
print()
from collections import Counter                                               # noqa: E402
wc = Counter(it.get("warnType") for it in res.items)
print("  输出 warnType 分布 = %s" % wc.most_common())
print("  输出前 10 只:")
for it in res.items[:10]:
    print("    %-8s %-8s bid=%-7s warn=%-4s prob=%s"
          % (it.get("code"), it.get("name"), it.get("bidChange"),
             it.get("warnType"), it.get("probability")))
print()
print("  判读: 今日(9/18)定格表是**迁移前**采集的 → warn_type 全 0 →")
print("        输出 warnType 应全为 0 且名单会被 scoreFloor 压掉 —— 这正是")
print("        '定格表没有异动值' 的表现; 下周一 9:25 采集起才有真值。")
print("        若输出出现 3/4/5, 说明开关没生效(仍在用竞价强度)。")
print("DONE")
