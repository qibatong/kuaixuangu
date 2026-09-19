# -*- coding: utf-8 -*-
"""2026-09-19 只读: 为什么 uid=49 名单被 score_floor 切光"""
import json
import sqlite3
import time

c = sqlite3.connect("/opt/kuaixuan/kuaixuan.db")
c.row_factory = sqlite3.Row


def q(sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except Exception as e:
        print("   [!] %s" % e)
        return []


print("=" * 78)
print("uid=49 名单为空 —— 逐环定位  %s" % time.strftime("%F %T"))
print("=" * 78)

print("\n【1】uid=49 保存的筛选偏好(prefs)")
for r in q("SELECT * FROM prefs WHERE user_id=49"):
    d = dict(r)
    for k, v in d.items():
        if k == "filters" and v:
            try:
                print("   filters=%s" % json.dumps(json.loads(v), ensure_ascii=False, sort_keys=True))
            except Exception:
                print("   filters=%s" % v)
        else:
            print("   %s=%s" % (k, v))

print("\n【2】uid=6 保存的筛选偏好(prefs) 对比")
for r in q("SELECT * FROM prefs WHERE user_id=6"):
    d = dict(r)
    for k, v in d.items():
        if k == "filters" and v:
            try:
                print("   filters=%s" % json.dumps(json.loads(v), ensure_ascii=False, sort_keys=True))
            except Exception:
                print("   filters=%s" % v)
        else:
            print("   %s=%s" % (k, v))

print("\n【3】9/18 定格快照的 warn_type 分布(直接决定 f630 因子)")
for r in q("SELECT warn_type, COUNT(*) n FROM snapshot_bid "
           "WHERE date='2026-09-18' AND time_point='9_25' GROUP BY warn_type ORDER BY warn_type"):
    print("   warn_type=%-4s %s 行" % (r["warn_type"], r["n"]))

print("\n【4】9/18 物化评分表 probability 分布(评分被压低的程度)")
for r in q("SELECT "
           "COUNT(*) n, MAX(probability) pmax, "
           "SUM(CASE WHEN probability>=80 THEN 1 ELSE 0 END) ge80, "
           "SUM(CASE WHEN probability>=75 THEN 1 ELSE 0 END) ge75, "
           "SUM(CASE WHEN probability>=70 THEN 1 ELSE 0 END) ge70, "
           "SUM(CASE WHEN probability>=65 THEN 1 ELSE 0 END) ge65 "
           "FROM stock_score_daily WHERE date='2026-09-18'"):
    print("   全市场=%s 最高分=%s | ≥80:%s  ≥75:%s  ≥70:%s  ≥65:%s" %
          (r["n"], r["pmax"], r["ge80"], r["ge75"], r["ge70"], r["ge65"]))

print("\n【5】对照: 9/17(东财未熔断前的定格) warn_type 分布")
for r in q("SELECT warn_type, COUNT(*) n FROM snapshot_bid "
           "WHERE date='2026-09-17' AND time_point='9_25' GROUP BY warn_type ORDER BY warn_type"):
    print("   warn_type=%-4s %s 行" % (r["warn_type"], r["n"]))

print("\n【6】对照: 9/17 评分分布")
for r in q("SELECT COUNT(*) n, MAX(probability) pmax, "
           "SUM(CASE WHEN probability>=80 THEN 1 ELSE 0 END) ge80, "
           "SUM(CASE WHEN probability>=65 THEN 1 ELSE 0 END) ge65 "
           "FROM stock_score_daily WHERE date='2026-09-17'"):
    print("   全市场=%s 最高分=%s | ≥80:%s  ≥65:%s" % (r["n"], r["pmax"], r["ge80"], r["ge65"]))

c.close()
print("\n" + "=" * 78)
