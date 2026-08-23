#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.core import config
from app.services import fetcher

k = fetcher.fetch_stock_chart("600519", "day")
print("bars=", len(k.get("time", [])))
if k.get("time"):
    print("last=", k["time"][-1], "nclose=", k["close"][-1])
import re
src = open(os.path.join(os.path.dirname(config.__file__) and "", "app/services/fetcher.py")).read() if False else ""
try:
    s = open("/opt/kuaixuan/backend/app/services/fetcher.py").read()
    print("cooldown=", re.findall(r"_HOST_COOLDOWN\s*=\s*(\d+)", s))
except Exception as e:
    print("cd err", e)