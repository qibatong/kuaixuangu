#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读复核测试机选股闸门的运行态(开关/快照维/时段维/前端产物)。

在测试机 /opt/kuaixuan/backend 下用 /opt/bid-venv/bin/python 运行。
"""
import datetime as dt
import glob
import os

from app.services import auction_snapshot as a
from app.services import settings
from app.services.picker import mode as M

print("=" * 56)
print("1) 开关  pick_window_guard")
v = settings.get("pick_window_guard", None)
print("   settings.get('pick_window_guard') =", repr(v), "  (None → 代码内默认 1=开启)")
print("   is_pick_open() 当前时点 =", M.is_pick_open())

print("=" * 56)
print("2) 时段维常量")
print("   T_PICK_BLOCK_FROM =", M.T_PICK_BLOCK_FROM, "(分钟, 9:00)")
print("   T_PICK_OPEN       =", M.T_PICK_OPEN, "(分钟, 9:26)")
print("   文案 TIME =", M.PICK_BLOCK_MSG_TIME)
print("   文案 SNAP =", M.PICK_BLOCK_MSG_SNAP)

print("=" * 56)
print("3) 快照维  has_today_snapshot")
today = dt.date.today().isoformat()
print("   today =", today, "->", a.has_today_snapshot(today))
print("   2026-09-16 ->", a.has_today_snapshot("2026-09-16"))

print("=" * 56)
print("4) 前端产物")
assets = sorted(glob.glob("/opt/kuaixuan/dist/assets/*.js"))
hit = []
for p in assets:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            s = f.read()
    except Exception as e:
        print("   read fail", p, e)
        continue
    if "pickBlocked" in s:
        hit.append(os.path.basename(p))
print("   assets 总数 =", len(assets))
print("   含 pickBlocked 的 chunk =", hit)
print("=" * 56)
