# -*- coding: utf-8 -*-
"""只读探针: 「17% 异动改回东财 f630」部署后验证 (2026-09-18 夜, 测试机)

四段:
  A 采集侧实测: _fetch_market_map(full=True) 的 warn_type(=f630) 分布 —— 证明全市场
    clist 的异动等级真的进了采集结果(这是整条链的源头)。
  B 读库侧: load_snapshot_full 透出 warn_type(今日定格表是迁移前采集的 → 全 0)。
  C 评分侧: 用真实模块算 f630 各档位对 17% 因子的贡献(0.85/0.6/0.18)。
  D 真实接口: uid=211 的 refresh 响应 + 开关指纹。
"""
import json
import sys
import urllib.request

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import auction_snapshot, bid_strength, settings  # noqa: E402
from app.services.picker.contract import QuoteRow                   # noqa: E402
from app.services.picker.score import compute_score                 # noqa: E402
from app.services import scorer                                     # noqa: E402

print("=" * 78)
print("D-0 开关指纹")
print("=" * 78)
print("  use_bid_strength      = %r" % settings.get("use_bid_strength"))
print("  bid_strength.enabled()= %s   (False = 17%% 因子走东财 f630)" % bid_strength.enabled())
print("  pick_window_guard     = %r" % settings.get("pick_window_guard"))

print()
print("=" * 78)
print("A 采集侧: 全市场 clist 的 f630 是否随行进入 raw_all")
print("=" * 78)
try:
    raw = auction_snapshot._fetch_market_map(full=True)
    print("  采集到 %d 只" % len(raw))
    from collections import Counter
    c = Counter(v.get("warn_type") for v in raw.values())
    print("  warn_type 分布 Top12: %s" % c.most_common(12))
    nz = sum(n for k, n in c.items() if k)
    print("  非 0 只数 = %d / %d (%.1f%%)" % (nz, len(raw), 100.0 * nz / max(1, len(raw))))
    miss = sum(1 for v in raw.values() if "warn_type" not in v)
    print("  缺 warn_type 键的行数 = %d (应为 0)" % miss)
    shown = 0
    for code, v in raw.items():
        if v.get("warn_type"):
            print("    样例 %-8s warn_type=%s bid_change=%s" % (code, v["warn_type"],
                                                              v.get("bid_change")))
            shown += 1
            if shown >= 5:
                break
except Exception as e:                                              # noqa: BLE001
    import traceback
    print("  ❌ 异常: %s" % e)
    traceback.print_exc()

print()
print("=" * 78)
print("B 读库侧: load_snapshot_full 透出 warn_type")
print("=" * 78)
snap = auction_snapshot.load_snapshot_full()
print("  定格行数 = %d" % len(snap))
keys = set()
for v in list(snap.values())[:5]:
    keys |= set(v.keys())
print("  单行键集 = %s" % sorted(keys))
from collections import Counter                                                # noqa: E402
c2 = Counter(v.get("warn_type") for v in snap.values())
print("  warn_type 取值域 = %s" % c2.most_common(8))
print("  说明: 今日(9/18)定格表是**迁移前**采集的 → 全 0; 下周一 9:25 采集起为真值")

print()
print("=" * 78)
print("C 评分侧: f630 档位对 17% 因子的实际贡献(真实模块)")
print("=" * 78)
cfg = scorer.get_scoring_cfg()


def _row(w):
    return QuoteRow(code="600001", name="测试甲", bid_change=3.5, bid_amt=5.0e7,
                    float_mv=4.0e9, prev_close=10.0, yesterday_change=1.0, warn_type=w)


for w in (0, 3, 4, 5):
    sr = compute_score(_row(w), cfg, None)
    print("  f630=%-2s → warn 因子分 %.2f (权重 %.2f) ⇒ 概率 %s"
          % (w, sr.parts["warn"]["score"], sr.parts["warn"]["weight"], sr.probability))
sr_s = compute_score(_row(4), cfg, 0.6)
print("  对照(注入竞价强度 0.6) → warn 因子分 %.2f, value=%r"
      % (sr_s.parts["warn"]["score"], sr_s.parts["warn"]["value"]))

print()
print("=" * 78)
print("D 真实接口: /api/stocks?action=refresh (uid=211)")
print("=" * 78)
try:
    from app.services import security
    tok = security.issue_token(211)
    req = urllib.request.Request(
        "http://127.0.0.1:8010/api/stocks?action=refresh&markets=hs,cyb,kcb",
        headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8"))
    meta = {k: d[k] for k in d if k not in ("list", "spotMap", "pool")}
    print("  响应元信息: %s" % meta)
    lst = d.get("list") or []
    print("  名单 %d 只:" % len(lst))
    for s in lst[:8]:
        print("    %-8s %-8s bidChange=%-8s warnType=%-4s prob=%s"
              % (s.get("code"), s.get("name"), s.get("bidChange"),
                 s.get("warnType"), s.get("probability")))
except Exception as e:                                              # noqa: BLE001
    import traceback
    print("  ❌ 异常: %s" % e)
    traceback.print_exc()
print("DONE")
