#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.services import kpl

date = "2026-08-21"
for tab in ["seal", "bid_net", "boom", "broken_today"]:
    d = kpl.query_auction_history(date, tab)
    print(f"\n[{tab}] len={len(d)}")
    if not d:
        continue
    sample = d[:1]
    print("  before:", [(it.get("code"), it.get("realChange"), it.get("change")) for it in sample])
    n = kpl.fill_close_change_from_kline(d, date)
    print("  after :", [(it.get("code"), it.get("realChange"), it.get("change")) for it in sample], "filled=%d" % n)
# 直接查库确认该日已有收盘涨幅
import sqlite3
from app.core import config
c = sqlite3.connect(config.DB_FILE)
print("\nDB close_change_history 8/21 count=", c.execute(
    "SELECT COUNT(*) FROM close_change_history WHERE date=?", (date,)).fetchone()[0])
print("DB sample:", c.execute(
    "SELECT * FROM close_change_history WHERE date=? AND code IN ('000007','000020','600519')", (date,)).fetchall())