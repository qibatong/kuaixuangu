# -*- coding: utf-8 -*-
"""9/17 auto_apply 扇出评估 —— **纯 SQL 只读**。"""
import sqlite3
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")
from app.core import config          # noqa: E402

DB = config.DB_FILE
now = int(time.time())
c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
g = time.gmtime(now + 8 * 3600)
today = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
print("DB =", DB, " today =", today)

q = lambda s, p=(): c.execute(s, p).fetchone()[0]

print("\n=== 用户口径 ===")
print("   总用户数                        :", q("select count(*) from users"))
print("   非管理员                        :", q("select count(*) from users where is_admin=0"))
print("   非管理员且未过期 = 扇出候选      :",
      q("select count(*) from users where is_admin=0 and (expire_at=0 or expire_at>=?)", (now,)))
print("   已过期(会被跳过)                 :",
      q("select count(*) from users where is_admin=0 and expire_at>0 and expire_at<?", (now,)))
print("   管理员(会被跳过)                 :", q("select count(*) from users where is_admin=1"))
print("   会员等级分布                     :",
      list(c.execute("select member_level, count(*) from users group by member_level")))

print("\n=== 今日已应用状态 ===")
print("   今日已有 auto_applied=1 的用户数 :",
      q("select count(distinct user_id) from batches where batch_date=? and auto_applied=1", (today,)))
print("   今日已有 auto_applied=1 的明细   :",
      list(c.execute("select user_id, count(*) from batches where batch_date=? and auto_applied=1 "
                     "group by user_id", (today,))))
print("   今日点过的用户数(手动)           :",
      q("select count(distinct user_id) from batches where batch_date=? and user_id>0 and auto_applied=0",
        (today,)))

print("\n=== 参照: 昨日(9/16)扇出结果 ===")
print("   9/16 auto_applied=1 用户数       :",
      q("select count(distinct user_id) from batches where batch_date='2026-09-16' and auto_applied=1"))
print("   9/16 系统批次(uid=0)             :",
      list(c.execute("select id, stock_count, auto_applied from batches where batch_date='2026-09-16' "
                     "and user_id=0 order by id")))
print("   9/16 单用户批次样例 stock_count  :",
      list(c.execute("select stock_count, count(*) from batches where batch_date='2026-09-16' "
                     "and auto_applied=1 group by stock_count order by 2 desc limit 5")))

c.close()
print("\nDONE")
