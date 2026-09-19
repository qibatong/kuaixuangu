# -*- coding: utf-8 -*-
"""2026-09-19 只读确诊: uid=49 名单为空 = scoreFloor 一刀切光(因 9/18 定格无 f630 全员被压低)"""
import json
import sqlite3
import time

logging_ok = True
try:
    from app.services import scorer, users
    from app.services.picker import pipeline
except Exception as e:
    print("导入失败: %s" % e)
    logging_ok = False

c = sqlite3.connect("/opt/kuaixuan/kuaixuan.db")
c.row_factory = sqlite3.Row


def prefs_of(uid):
    r = c.execute("SELECT id, username, filter_prefs FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        return None, None
    raw = r["filter_prefs"]
    try:
        return r["username"], (json.loads(raw) if raw else None)
    except Exception:
        return r["username"], None


print("=" * 78)
print("确诊: 测试环境选股为空的原因  %s" % time.strftime("%F %T"))
print("=" * 78)

if not logging_ok:
    raise SystemExit(1)

for uid in (6, 49):
    name, p = prefs_of(uid)
    print("\n【uid=%s %s 的保存条件】" % (uid, name))
    if not p:
        print("   (无保存条件 → 走默认值 scoreFloor=80)")
    else:
        for k in ("scoreFloor", "probLt", "confLt", "markets", "bidAmtFloor",
                  "floatMvFloor", "bidGt", "bidLt", "priceGt"):
            print("   %-14s = %s" % (k, p.get(k)))

print("\n" + "-" * 78)
print("用「同一份 9/18 定格数据」跑 pipeline, 只改 scoreFloor 看入选数")
print("-" * 78)
base = {"markets": ["hs,cyb,kcb"]}
for uid in (49, 6):
    name, p = prefs_of(uid)
    if p:
        base.setdefault("_u", {})[uid] = p

# 先拿一次全市场候选(scoreFloor=0 不砍)看评分天花板
for floor in (0, 60, 65, 70, 75, 80):
    q = {"markets": ["hs,cyb,kcb"], "scoreFloor": [str(floor)],
         "bidAmtFloor": ["2000"], "bidGt": ["7"], "floatMvFloor": ["30"]}
    f = scorer.validate_filters(q)
    t0 = time.time()
    try:
        res = pipeline.run(f)
        scores = sorted([float(it.get("probability") or 0) for it in res.items], reverse=True)
        msg = "scoreFloor=%-3s → 入选=%-4d" % (floor, len(res.items))
        if scores:
            msg += " 最高分=%.1f 最低分=%.1f" % (scores[0], scores[-1])
        print("   %s  stats=%s  %.0fms" % (msg, res.stats, (time.time() - t0) * 1000))
    except Exception as e:
        print("   scoreFloor=%-3s → 异常 %s: %s" % (floor, type(e).__name__, str(e)[:80]))

print("\n" + "-" * 78)
print("关键对照: f630 有值 vs 无值 对同一只票的评分差(取当前候选首只做 A/B)")
print("-" * 78)
q = {"markets": ["hs,cyb,kcb"], "scoreFloor": ["0"], "bidAmtFloor": ["2000"],
     "bidGt": ["7"], "floatMvFloor": ["30"]}
f = scorer.validate_filters(q)
res = pipeline.run(f)
print("   候选池=%d 只(scoreFloor=0)" % len(res.items))
top = sorted(res.items, key=lambda x: -float(x.get("probability") or 0))[:5]
for it in top:
    print("   %-8s %-8s 评分=%-6s warn_type=%s" %
          (it.get("code"), it.get("name"), it.get("probability"), it.get("warn_type")))

c.close()
print("\n" + "=" * 78)
