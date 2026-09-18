# -*- coding: utf-8 -*-
"""只读探针: 评估「17% 异动因子改回东财 f630」的可达性 (2026-09-18 夜)

三问:
  ① 当前开关状态(use_bid_strength / pick_window_guard / precompute_read / use_snapshot_pool)
  ② 关掉竞价强度后会退回什么 —— snapshot_bid 有没有 f630 列 / 定格链路能否取到 f630
  ③ 现状指纹 —— stock_score_daily.warn_type 与 batch_stocks.warn_type 的按日分布
"""
import sys
sys.path.insert(0, "/opt/kuaixuan/backend")

from app.db import database                                     # noqa: E402
from app.services import settings as S                          # noqa: E402

print("=" * 78)
print("① 关键开关")
print("=" * 78)
for k in ("use_bid_strength", "pick_window_guard", "precompute_read",
          "use_snapshot_pool", "frontend_local_filter"):
    try:
        print("  %-22s = %r" % (k, S.get(k)))
    except Exception as e:                                      # noqa: BLE001
        print("  %-22s = 异常 %s" % (k, e))

try:
    from app.services import bid_strength
    print("  bid_strength.enabled() = %s" % bid_strength.enabled())
except Exception as e:                                          # noqa: BLE001
    print("  bid_strength.enabled() = 异常 %s" % e)

conn = database.get_conn()
try:
    print()
    print("=" * 78)
    print("② snapshot_bid 结构(找 f630 / warn 列)")
    print("=" * 78)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(snapshot_bid)").fetchall()]
    print("  列: %s" % ", ".join(cols))
    print("  → 含 f630/warn 类列? %s" % [c for c in cols
                                        if "f630" in c.lower() or "warn" in c.lower()
                                        or "异动" in c])

    print()
    print("  近 6 个有 9_25 的交易日 + 行数:")
    for d, n in conn.execute(
            "SELECT date, COUNT(*) FROM snapshot_bid WHERE time_point='9_25' "
            "GROUP BY date ORDER BY date DESC LIMIT 6").fetchall():
        print("    %s  n=%s" % (d, n))

    print()
    print("=" * 78)
    print("③ 现状指纹: stock_score_daily.warn_type 按日取值域")
    print("=" * 78)
    for d in [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM stock_score_daily ORDER BY date DESC LIMIT 5").fetchall()]:
        dist = conn.execute(
            "SELECT warn_type, COUNT(*) FROM stock_score_daily WHERE date=? "
            "GROUP BY warn_type ORDER BY COUNT(*) DESC", (d,)).fetchall()
        print("  %s  总=%s  %s" % (d, sum(x[1] for x in dist),
                                   " ".join("%s:%s" % (a, b) for a, b in dist)))

    print()
    print("=" * 78)
    print("③' batch_stocks.warn_type 按日取值域")
    print("=" * 78)
    for d in [r[0] for r in conn.execute(
            "SELECT DISTINCT batch_date FROM batch_stocks ORDER BY batch_date DESC "
            "LIMIT 5").fetchall()]:
        dist = conn.execute(
            "SELECT warn_type, COUNT(*) FROM batch_stocks WHERE batch_date=? "
            "GROUP BY warn_type ORDER BY COUNT(*) DESC", (d,)).fetchall()
        print("  %s  总=%s  %s" % (d, sum(x[1] for x in dist),
                                   " ".join("%s:%s" % (a, b) for a, b in dist)))

    print()
    print("  近 10 条批次(看 batch_time / action / stock_count / user_id):")
    for r in conn.execute(
            "SELECT id, batch_date, batch_time, action, stock_count, user_id, "
            "auto_applied FROM batches ORDER BY id DESC LIMIT 10").fetchall():
        print("    #%-6s %s %s %-6s n=%-4s uid=%-5s auto=%s" % tuple(r))
finally:
    conn.close()

print()
print("=" * 78)
print("④ 东财点查是否可用(日志中最近的成功/失败计数)")
print("=" * 78)
import subprocess                                                # noqa: E402
for pat in ("东财点查失败", "腾讯点查兜底成功", "竞价强度", "开关已关闭"):
    try:
        out = subprocess.run(
            ["bash", "-lc",
             "grep -c '%s' /opt/kuaixuan/logs/app.log 2>/dev/null || echo 0" % pat],
            capture_output=True, text=True, timeout=30)
        print("  app.log  %-16s 命中 %s" % (pat, out.stdout.strip()))
    except Exception as e:                                       # noqa: BLE001
        print("  %-16s 异常 %s" % (pat, e))
print("DONE")
