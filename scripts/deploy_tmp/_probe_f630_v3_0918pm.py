# -*- coding: utf-8 -*-
"""只读探针 v3: 全市场 clist 是否携带真实 f630(异动等级) —— 决定「定格采集时落库 f630」可行性

背景: 东财点查(ulist.np @push2.eastmoney.com)在测试机实测 RemoteDisconnected(永久封),
但全市场 clist(push2dycalc)正常且 config.FIELDS 里含 f630。若 clist 返回真值,
则可在 9_25 采集时把 f630 落进 snapshot_bid, 让定格链路拿到真实异动等级。
"""
import sys
sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import fetcher, scorer                          # noqa: E402

print("=" * 78)
print("采样: 全市场 clist (hs 板块) → f630 取值域")
print("=" * 78)

for board in ("hs",):
    try:
        fs = scorer.market_fs([board])
        print("  fs=%s" % fs)
        try:
            rows = fetcher._fetch_market_all_with_fallback(fs)
            tag = "all_with_fallback"
        except Exception as e:                                   # noqa: BLE001
            print("    all_with_fallback 异常 %s → 退回 _fetch_market_with_fallback" % e)
            rows = fetcher._fetch_market_with_fallback(fs)
            tag = "with_fallback"
        print("  [%s] 取到 %d 行" % (tag, len(rows or [])))
        if not rows:
            continue
        # f630 是否存在 / 取值分布
        keys = set()
        for s in rows[:50]:
            keys |= set(s.keys())
        print("  行内含 f630? %s" % ("f630" in keys))
        print("  样例字段(前 20): %s" % sorted(keys)[:20])
        from collections import Counter
        c = Counter()
        miss = 0
        for s in rows:
            if "f630" not in s:
                miss += 1
                continue
            c[s.get("f630")] += 1
        print("  缺 f630 行数 = %d" % miss)
        tops = c.most_common(15)
        print("  f630 取值分布 Top15: %s" % tops)
        nonzero = sum(n for v, n in c.items() if v not in (0, "0", None, "-", ""))
        print("  非 0 计数 = %d / %d (%.1f%%)" % (nonzero, len(rows),
                                                 100.0 * nonzero / max(1, len(rows))))
        print()
        print("  非 0 样例(最多 10 只):")
        shown = 0
        for s in rows:
            v = s.get("f630")
            if v not in (0, "0", None, "-", ""):
                print("    %-8s %-10s f630=%-5s f615=%-8s f3=%-7s f21=%s" % (
                    s.get("f12"), s.get("f14"), v, s.get("f615"), s.get("f3"),
                    s.get("f21")))
                shown += 1
                if shown >= 10:
                    break
    except Exception as e:                                       # noqa: BLE001
        import traceback
        print("  异常: %s" % e)
        traceback.print_exc()
print("DONE")
