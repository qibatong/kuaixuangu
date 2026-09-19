# -*- coding: utf-8 -*-
"""2026-09-19 只读诊断: 测试机"选股看不到"—— 逐环定位(全部只读, 不写库)"""
import sqlite3
import time

DB = "/opt/kuaixuan/kuaixuan.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def q(sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except Exception as e:
        print("   [!] %s -> %s" % (sql[:60], e))
        return []


print("=" * 78)
print("测试机 选股链路诊断  %s" % time.strftime("%F %T %A"))
print("=" * 78)

print("\n【1】settings 关键开关")
for r in q("SELECT key, value FROM settings WHERE key IN "
           "('pick_window_guard','use_bid_strength','precompute_read','precompute_write')"):
    print("   %-22s = %s" % (r["key"], r["value"]))

print("\n【2】batches 最近 12 条")
rows = q("SELECT id, batch_date, batch_time, action, stock_count, user_id, auto_applied, ts "
         "FROM batches ORDER BY id DESC LIMIT 12")
for r in rows:
    print("   #%-6s %s %-8s %-6s count=%-4s uid=%-5s auto=%s" %
          (r["id"], r["batch_date"], r["batch_time"], r["action"],
           r["stock_count"], r["user_id"], r["auto_applied"]))
print("   (空 = 库里一个批次都没有)")

print("\n【3】batches 按日聚合(最近 8 个交易日)")
for r in q("SELECT batch_date, COUNT(*) n, SUM(stock_count) tot, "
           "SUM(auto_applied) autos, MAX(id) maxid FROM batches "
           "GROUP BY batch_date ORDER BY batch_date DESC LIMIT 8"):
    print("   %s  批次=%-4s 股票合计=%-5s 自动应用=%-4s 最大id=%s" %
          (r["batch_date"], r["n"], r["tot"], r["autos"], r["maxid"]))

print("\n【4】snapshot_bid 按日(最近 10 天, 看定格有没有落)")
for r in q("SELECT date, COUNT(*) n, MIN(ts) t0, MAX(ts) t1 FROM snapshot_bid "
           "GROUP BY date ORDER BY date DESC LIMIT 10"):
    print("   %s  行数=%-6s ts=%s ~ %s" % (r["date"], r["n"], r["t0"], r["t1"]))

print("\n【5】今日(2026-09-19)各表是否有数据")
for t in ("batches", "snapshot_bid", "stock_score_daily"):
    r = q("SELECT COUNT(*) n FROM %s WHERE date = date('now','localtime')" % t
          if t != "batches" else
          "SELECT COUNT(*) n FROM batches WHERE batch_date = date('now','localtime')")
    print("   %-20s = %s 行" % (t, r[0]["n"] if r else "?"))

print("\n【6】用户总数 / 有选股历史的用户数")
for sql, label in (("SELECT COUNT(*) n FROM users", "users 总数"),
                   ("SELECT COUNT(DISTINCT user_id) n FROM batches WHERE user_id>0", "有批次的用户数")):
    r = q(sql)
    print("   %-20s = %s" % (label, r[0]["n"] if r else "?"))

c.close()
print("\n" + "=" * 78)
