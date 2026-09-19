# -*- coding: utf-8 -*-
"""2026-09-19 只读: 看今天批次的内容 + uid=6 是谁 + 最近交易日系统批次"""
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
print("uid=6 是谁 / 今天批次内容  %s" % time.strftime("%F %T"))
print("=" * 78)

print("\n【A】uid=6 / 211 / 213 用户信息")
for r in q("SELECT id, username, email, is_admin, member_level, expire_time FROM users WHERE id IN (6,211,213,0)"):
    exp = r["expire_time"]
    try:
        exp = time.strftime("%F %T", time.localtime(int(exp))) if exp else None
    except Exception:
        pass
    print("   uid=%-4s %-14s admin=%-3s level=%-3s 到期=%s" %
          (r["id"], r["username"], r["is_admin"], r["member_level"], exp))

print("\n【B】今天 4 个批次的筛选条件(filters)")
for r in q("SELECT id, batch_time, action, stock_count, filters FROM batches "
           "WHERE batch_date='2026-09-19' ORDER BY id"):
    print("   #%s %s %s count=%s" % (r["id"], r["batch_time"], r["action"], r["stock_count"]))
    print("      filters=%s" % (r["filters"] or "")[:300])

print("\n【C】batch 1684 明细(今天 6 只)")
for r in q("SELECT rank, code, name, probability, confidence, bid_change, real_change, "
           "bid_amt, bid_turnover, circulation_mv, warn_type, industry "
           "FROM batch_stocks WHERE batch_id=1684 ORDER BY rank"):
    print("   #%-2s %-8s %-8s prob=%-6s conf=%-6s 竞涨=%-7s 现涨=%-7s 竞额=%-10s 市值=%-8s warn=%s" %
          (r["rank"], r["code"], r["name"], r["probability"], r["confidence"],
           r["bid_change"], r["real_change"], r["bid_amt"], r["circulation_mv"], r["warn_type"]))

print("\n【D】最近交易日(09-18)的 uid=0 系统批次 + 各用户自动应用批次")
for r in q("SELECT id, batch_date, batch_time, action, stock_count, user_id, auto_applied "
           "FROM batches WHERE batch_date='2026-09-18' ORDER BY id"):
    print("   #%-6s %s %-8s %-6s count=%-4s uid=%-5s auto=%s" %
          (r["id"], r["batch_date"], r["batch_time"], r["action"], r["stock_count"],
           r["user_id"], r["auto_applied"]))

print("\n【E】今天 snapshot_bid 的 9_25 行(应为 0 → 直读判据会 False)")
for tp in ("9_15", "9_20", "9_24", "9_25"):
    r = q("SELECT COUNT(*) n, MAX(ts) mx FROM snapshot_bid WHERE date='2026-09-19' AND time_point=?",
          (tp,))
    print("   %s : %s 行 (max_ts=%s)" % (tp, r[0]["n"], r[0]["mx"]))

c.close()
print("\n" + "=" * 78)
