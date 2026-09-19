# -*- coding: utf-8 -*-
"""切换后验证: 走真实链路(不注入 strengths), uid=49 名单是否恢复 (2026-09-19)"""
import sys, json, time, sqlite3, logging

sys.path.insert(0, "/opt/kuaixuan/backend")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

from app.services.picker import pipeline          # noqa: E402
from app.services import scorer, bid_strength     # noqa: E402

DB = "/opt/kuaixuan/kuaixuan.db"
KEYS = ("scoreFloor", "probLt", "confLt", "bidAmtFloor", "floatMvFloor", "floatMvGt",
        "priceGt", "bidGt", "bidLt", "limitUp", "stSuspend")


def prefs_of(uid):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT username, filter_prefs FROM users WHERE id=?", (uid,)).fetchone()
        raw = r["filter_prefs"] if r else None
        try:
            return (r["username"] if r else None), (json.loads(raw) if raw else None)
        except Exception:
            return (r["username"] if r else None), None
    finally:
        c.close()


def main():
    print("=" * 78)
    print("开关状态: use_bid_strength enabled() = %s" % bid_strength.enabled())
    print("=" * 78)

    ctx = pipeline.load_context()
    print("上下文: date=%s  定格=%d只" % (ctx.date, len(ctx.day_bid_change)))
    print()

    for uid in (49, 6):
        name, p = prefs_of(uid)
        q = {"markets": ["hs,cyb,kcb"]}
        for k in KEYS:
            if p and p.get(k) is not None:
                q[k] = [str(p[k])]
        f = scorer.validate_filters(q)
        t0 = time.time()
        res = pipeline.run(f, ctx=ctx)
        print("uid=%-3s %-12s scoreFloor=%-5s → 全市场=%-5d 候选=%-4d 入选=%-3d 剔除=%s  (%.0fms)"
              % (uid, name, f.get("scoreFloor"), res.n_universe, res.n_candidate,
                 len(res.items), json.dumps(res.stats, ensure_ascii=False), (time.time() - t0) * 1000))
        if res.items:
            print("      名单:")
            for it in res.items[:10]:
                print("        %-7s %-7s 分=%-5s 异动档=%-3s 竞额=%-9s 行业=%s"
                      % (it.get("code"), it.get("name"), it.get("probability"),
                         it.get("warnType"),
                         it.get("bidAmt"), (it.get("industry") or "-")[:8]))
        print()


if __name__ == "__main__":
    main()
