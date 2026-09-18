# -*- coding: utf-8 -*-
"""只读探针 v2: f630 实测可达性 + 现状异动指纹 (2026-09-18 夜)

1) 实时实测: 东财点查(fetch_raw_by_codes)对固定样本是否返回真实 f630 —— 决定
   「改回东财异动」后 17% 因子到底是有值还是恒 default。
2) 日志: 东财点查失败 / 腾讯兜底 的频率(今日 + 近 3 个轮转文件)。
3) 现状指纹: batch_stocks.warn_type 按日分布(join batches 拿 batch_date)。
"""
import sys, json
sys.path.insert(0, "/opt/kuaixuan/backend")

from app.db import database                                     # noqa: E402
from app.services import fetcher                                # noqa: E402

print("=" * 78)
print("① 东财点查实测: f630 是否返回真实值")
print("=" * 78)

SAMPLE = ["600519", "300750", "000001", "601899", "300454", "002594",
          "600036", "000858", "601318", "300059", "600030", "002415"]
try:
    raw = fetcher.fetch_raw_by_codes(SAMPLE)
    if not raw:
        print("  ❌ fetch_raw_by_codes 返回空 → 东财点查不可用")
    else:
        print("  返回 %d 行" % len(raw))
        vals = []
        for s in raw[:12]:
            code = s.get("f12") or s.get("code")
            v = s.get("f630")
            vals.append(v)
            print("    %-8s f630=%-5s f615=%-8s f2=%-8s" % (
                code, v, s.get("f615"), s.get("f2")))
        print("  f630 取值: %s" % vals)
        print("  → 非零个数 %d / %d" % (sum(1 for v in vals if v not in (None, 0, "0", "-")),
                                        len(vals)))
except Exception as e:                                          # noqa: BLE001
    import traceback
    print("  ❌ 异常: %s" % e)
    traceback.print_exc()

print()
print("=" * 78)
print("② 日志: 点查降级频率")
print("=" * 78)
import subprocess                                                # noqa: E402
for pat in ("东财点查失败", "腾讯点查兜底成功", "东财全市场失败", "竞价强度",
            "开关已关闭"):
    try:
        out = subprocess.run(
            ["bash", "-lc",
             "cat /opt/kuaixuan/logs/app.log /opt/kuaixuan/logs/app.log.1 2>/dev/null "
             "| grep -c '%s'" % pat],
            capture_output=True, text=True, timeout=60)
        print("  %-22s 命中 %s" % (pat, out.stdout.strip()))
    except Exception as e:                                       # noqa: BLE001
        print("  %-22s 异常 %s" % (pat, e))

try:
    out = subprocess.run(
        ["bash", "-lc",
         "grep -h '东财点查失败' /opt/kuaixuan/logs/app.log.1 2>/dev/null | tail -3"],
        capture_output=True, text=True, timeout=60)
    if out.stdout.strip():
        print("  样例:")
        for ln in out.stdout.strip().split("\n"):
            print("    " + ln[:200])
except Exception:                                                # noqa: BLE001
    pass

print()
print("=" * 78)
print("③ batch_stocks.warn_type 按日分布(join batches)")
print("=" * 78)
conn = database.get_conn()
try:
    rows = conn.execute(
        "SELECT b.batch_date, bs.warn_type, COUNT(*) FROM batch_stocks bs "
        "JOIN batches b ON b.id=bs.batch_id "
        "WHERE b.batch_date >= '2026-09-14' "
        "GROUP BY b.batch_date, bs.warn_type ORDER BY b.batch_date DESC").fetchall()
    cur_d = None
    acc = []
    for d, w, n in rows:
        if d != cur_d:
            if cur_d:
                print("  %s  总=%-4s %s" % (cur_d, sum(x[1] for x in acc),
                                            " ".join("%s:%s" % x for x in acc)))
            cur_d, acc = d, []
        acc.append((w, n))
    if cur_d:
        print("  %s  总=%-4s %s" % (cur_d, sum(x[1] for x in acc),
                                    " ".join("%s:%s" % x for x in acc)))

    print()
    print("  近 8 条批次逐条:")
    for r in conn.execute(
            "SELECT id, batch_date, batch_time, action, stock_count, user_id, "
            "auto_applied FROM batches ORDER BY id DESC LIMIT 8").fetchall():
        print("    #%-6s %s %s %-6s n=%-4s uid=%-5s auto=%s" % tuple(r))
finally:
    conn.close()
print("DONE")
