#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产机修复: 扫描 auction_daily_history boom 中 floatMv 异常过小(<1e7元)的行,
用当日 9_25 快照的 float_mv 覆盖, 重算 bidTurnover, 写回落库。"""
import sqlite3, json

db = '/opt/kuaixuan/kuaixuan.db'
c = sqlite3.connect(db)
rows = c.execute("SELECT date,list FROM auction_daily_history WHERE tab='boom' ORDER BY date").fetchall()
repaired = []
for date, lst in rows:
    lst = json.loads(lst)
    snaps = {code: (amt or 0, fmv or 0)
             for code, amt, fmv in c.execute(
                 "SELECT code,bid_amt,float_mv FROM snapshot_bid "
                 "WHERE date=? AND time_point='9_25'", (date,))}
    changed = False
    for it in lst:
        fmv = it.get('floatMv') or 0
        if fmv and fmv < 1e7:                      # 字段错位: 流通市值不足1000万 → 损坏
            s = snaps.get(it['code'])
            if s and s[1]:
                it['floatMv'] = s[1]
                it['bidTurnover'] = round(s[0] * 10000 / s[1] * 100, 2)
                changed = True
                repaired.append((date, it['code'], it['name'], s[1], it['bidTurnover']))
    if changed:
        c.execute("UPDATE auction_daily_history SET list=? WHERE date=? AND tab='boom'",
                  (json.dumps(lst, ensure_ascii=False), date))
c.commit()
print("修复条数:", len(repaired))
for r in repaired:
    date, code, name, fmv, bt = r
    print(f"  {date} {code} {name} floatMv={fmv:.0f} bidTurnover={bt}%")
c.close()