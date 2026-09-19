# -*- coding: utf-8 -*-
"""只读预演: 若改用「竞价强度」口径, 主人账号(uid=49)能否出票? (2026-09-19)

不改任何配置/不写库。手法:
  A 组 = 现状(不注入 strengths → _load_strength 因开关关闭返回 {} → 走 f630, 全员 0)
  B 组 = 注入真实竞价强度(ctx.strengths) → compute_score 用 strength 替代 warn 因子
条件完全一致, 只切异动口径, 对比 候选/入选/剔除/分数天花板。
"""
import sys, json, copy, time, sqlite3, logging

logging.disable(logging.WARNING)
sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services.picker import pipeline          # noqa: E402
from app.services import scorer, bid_strength     # noqa: E402

DB = "/opt/kuaixuan/kuaixuan.db"
# validate_filters 认得的门槛键(其余 UI 哑设置无意义)
KEYS = ("scoreFloor", "probLt", "confLt", "bidAmtFloor", "floatMvFloor", "floatMvGt",
        "priceGt", "bidGt", "bidLt", "limitUp", "stSuspend")


def prefs_of(uid):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT id, username, filter_prefs FROM users WHERE id=?", (uid,)).fetchone()
        if not r:
            return None, None
        raw = r["filter_prefs"]
        try:
            return r["username"], (json.loads(raw) if raw else None)
        except Exception:
            return r["username"], None
    finally:
        c.close()


def q_of(p):
    q = {"markets": ["hs,cyb,kcb"]}
    for k in KEYS:
        if p and p.get(k) is not None:
            q[k] = [str(p[k])]
    return q


def main():
    name, p = prefs_of(49)
    print("=" * 78)
    print("uid=49 (%s) 保存条件" % name)
    print("=" * 78)
    if not p:
        print("   (无保存条件 → 全走 validate_filters 默认值, scoreFloor=80)")
    else:
        for k in KEYS:
            if p.get(k) is not None:
                print("   %-14s = %s" % (k, p.get(k)))
    q = q_of(p)
    filters = scorer.validate_filters(q)
    print()
    print("→ 生效 filters: " + json.dumps(filters, ensure_ascii=False))
    print()

    ctx = pipeline.load_context()
    print("上下文: date=%s  定格=%d只  昨涨停=%d只  抢筹=%d只"
          % (ctx.date, len(ctx.day_bid_change),
             len(ctx.zt_codes or []), len(ctx.qiangchou_codes or [])))

    # ---------- A 组: 现状 ----------
    print()
    print("=" * 78)
    print("A 组 · 现状(f630 异动等级 · 开关关闭 → 全员 warn=0)")
    print("=" * 78)
    ctx_a = copy.deepcopy(ctx)
    ctx_a.strengths = {}
    t0 = time.time()
    ra = pipeline.run(filters, ctx=ctx_a)
    print("   全市场=%d  候选=%d  入选=%d  (%.0fms)"
          % (ra.n_universe, ra.n_candidate, len(ra.items), (time.time() - t0) * 1000))
    print("   剔除=%s  源=%s  degraded=%s" % (json.dumps(ra.stats, ensure_ascii=False), ra.sources, ra.degraded))

    # ---------- B 组: 补异动 ----------
    print()
    print("=" * 78)
    print("B 组 · 竞价强度口径(注入真实强度, 对东财免疫)")
    print("=" * 78)
    st = bid_strength.load(None, date=ctx.date)
    sm = bid_strength.score_map(st)
    strengths = {c: v for c, v in sm.items() if v is not None}
    print("   强度覆盖: %d/%d 只 (%.1f%%)" % (len(strengths), len(st), 100.0 * len(strengths) / max(1, len(st))))
    ctx_b = copy.deepcopy(ctx)
    ctx_b.strengths = strengths
    t0 = time.time()
    rb = pipeline.run(filters, ctx=ctx_b)
    print("   全市场=%d  候选=%d  入选=%d  (%.0fms)"
          % (rb.n_universe, rb.n_candidate, len(rb.items), (time.time() - t0) * 1000))
    print("   剔除=%s  源=%s  degraded=%s" % (json.dumps(rb.stats, ensure_ascii=False), rb.sources, rb.degraded))

    # ---------- 对照 ----------
    print()
    print("=" * 78)
    print("对照结论")
    print("=" * 78)
    print("   A 组(现状)   入选 = %d 只" % len(ra.items))
    print("   B 组(补异动) 入选 = %d 只" % len(rb.items))
    print()
    if rb.items:
        print("   B 组名单(前 12):")
        for it in rb.items[:12]:
            code = it.get("code")
            print("      %-8s %-8s 分=%-6s 现涨=%-7s 竞涨=%-7s 强度=%.3f"
                  % (code, it.get("name"), it.get("probability"),
                     it.get("real_change"), it.get("bid_change"), strengths.get(code, -1)))


if __name__ == "__main__":
    main()
