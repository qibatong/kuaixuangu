# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
import sqlite3, json
from app.core import config
from app.services import kpl

today = "2026-08-24"
conn = sqlite3.connect(config.DB_FILE)
# 今日实时 broken (非历史) 会走 fetch_broken_zt()/resync, 但历史快照只存 15:30. 盘中实时数据需调接口.
# 先看今日 broken_today 落库
row = conn.execute("SELECT list, ts FROM auction_daily_history WHERE date=? AND tab='broken_today'", (today,)).fetchone()
if row:
    print("今日 broken_today 落库 ts:", row[1])
    lst = json.loads(row[0])
    empty = [it for it in lst if not it.get("bidTurnover")]
    print("total:", len(lst), "empty bidTurnover:", len(empty))
    for it in lst:
        print("  ", it.get("code"), it.get("name"), "bidTurnover=", it.get("bidTurnover"), "bidAmt=", it.get("bidAmt"), "bidChange=", it.get("bidChange"), "day=", it.get("day"))
else:
    print("今日 broken_today 无落库")
    # 看今日实时 broken 接口
    try:
        lst = kpl.fetch_broken_zt() or []
        print("fetch_broken_zt 实时 total:", len(lst))
        for it in lst[:30]:
            print("  ", it.get("code"), it.get("name"), "change=", it.get("change"), "day=", it.get("day"))
    except Exception as e:
        print("fetch_broken_zt err", e)

conn.close()