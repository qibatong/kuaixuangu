# -*- coding: utf-8 -*-
"""v4.11.29 选股闸门 v4 端到端验证(只读)

验证三件事(全部用**当前真实数据库**):
  ① 闸门时间维: 竞价段(9:15~9:25:35)返回拦截, 盘前/9:25:36 后放行;
  ② 定格前批次不再被 refresh/回显复用(今日 #1674/#1675 是实证样本);
  ③ 定格来源日期透出(freezeDate / freezeIsToday)。
用法: cd /opt/kuaixuan/backend && PYTHONPATH=. python /tmp/_verify_gate_v4.py
"""
import calendar
import sys

from app.api import stocks as stocks_api
from app.db import database
from app.services import auction_snapshot, history
from app.services.picker import mode as pm

BJ = 8 * 3600
DAY = (2026, 9, 18)   # 交易日(周五)


def ts(hh, mm, ss=0):
    return calendar.timegm((DAY[0], DAY[1], DAY[2], hh, mm, ss, 0, 0, 0)) - BJ


def line(t, expect):
    got = pm.is_pick_open(ts(*t))
    reason = stocks_api._pick_blocked_reason(ts(*t))
    ok = "✅" if got == expect else "🔴"
    return "%s %02d:%02d:%02d is_pick_open=%-5s 期望=%-5s reason=%s" % (
        ok, t[0], t[1], t[2] if len(t) > 2 else 0, got, expect, reason or "-")


print("=" * 78)
print("① 闸门时间维(2026-09-18 周五, 交易日)")
print("=" * 78)
cases = [
    ((0, 30), True), ((8, 0), True), ((9, 0), True), ((9, 14, 59), True),
    ((9, 15, 0), False), ((9, 18, 0), False), ((9, 22, 38), False),
    ((9, 24, 59), False), ((9, 25, 0), False), ((9, 25, 35), False),
    ((9, 25, 36), True), ((9, 26, 0), True), ((10, 0), True), ((15, 30), True),
]
bad = 0
for t, exp in cases:
    s = line(t, exp)
    print("  " + s)
    if "🔴" in s:
        bad += 1
print("  → 时间维 %s" % ("全部符合 ✅" if bad == 0 else "有 %d 处不符 🔴" % bad))

print()
print("=" * 78)
print("② 定格前批次不再被复用(今日真实数据)")
print("=" * 78)
BDATE = "2026-09-18"
land = history._freeze_landing_ts(BDATE)
print("  当日 9_25 定格落库 ts = %s (%s)" % (
    land, "无(当日尚未落库 → 一切当日批次都判定格前)" if not land else "已落库"))
conn = database.get_conn()
rows = conn.execute(
    "SELECT id, batch_time, action, stock_count, user_id, auto_applied, ts FROM batches "
    "WHERE batch_date=? ORDER BY id", (BDATE,)).fetchall()
for r in rows:
    bid, btime, act, cnt, uid_, auto, rts = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
    ready = history._is_freeze_ready_batch({"ts": rts}, land)
    mark = "🔴 定格前(不得复用)" if not ready else "✅ 定格后(可复用)"
    print("  #%s %s %-6s n=%-3s uid=%-4s auto=%s ts=%-11s %s" % (
        bid, btime, act, cnt, uid_, auto, rts, mark))
conn.close()

print()
print("  实测选批(uid=211, 当前时间 10:00):")
fake_now = ts(10, 0)
for name, fn in (("find_today_reusable_batch", lambda: history.find_today_reusable_batch(211, {
        "markets": ["hs", "cyb", "kcb"], "stSuspend": True, "limitUp": True,
        "probLt": 70, "confLt": 65, "scoreFloor": 80, "floatMvFloor": 30,
        "floatMvGt": 200, "bidAmtFloor": 4000, "priceGt": 200, "bidGt": 10,
        "chgFloor": 0, "chgGt": 0, "volRatioFloor": 0, "turnoverFloor": 0,
        "turnoverGt": 0, "spotExcludeZT": False}, now_ts=fake_now)),
        ("find_today_system_batch", lambda: history.find_today_system_batch(now_ts=fake_now))):
    try:
        print("    %-28s → %s" % (name, fn()))
    except Exception as e:                                       # noqa: BLE001
        print("    %-28s → 异常 %s" % (name, e))

print()
print("  前端首屏判据(list_batches 透出 freeze_ready, 取最近 6 条):")
for b in history.list_batches(211, limit=200)[:6]:
    print("    #%-6s %s %-6s n=%-3s freeze_ready=%s" % (
        b.get("id"), b.get("batch_time"), b.get("action"),
        b.get("stock_count"), b.get("freeze_ready")))

print()
print("=" * 78)
print("③ 定格来源日期透出")
print("=" * 78)
print("  freeze_source_date('2026-09-18') =", auction_snapshot.freeze_source_date("2026-09-18"))
print("  _freeze_fields() =", stocks_api._freeze_fields())
print("  _pick_blocked_until(9:18) =", stocks_api._pick_blocked_until(ts(9, 18)))
print("  _pick_blocked_until(10:00) =", stocks_api._pick_blocked_until(ts(10, 0)),
      "(非拦截 → 兜底为 09:25:36)")
sys.exit(0)
