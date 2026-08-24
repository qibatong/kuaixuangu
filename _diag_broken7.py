# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, "/opt/kuaixuan/backend")
os.chdir("/opt/kuaixuan/backend")
from app.services import fetcher, scorer

# 直接拉东财 clist, 每次单个市场太多, 用小接口试试
# 尝试按代码排序取包含 001203 的一页
try:
    diff = fetcher._fetch_clist_page("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048", 1, "f12")
    print("page1 count:", len(diff))
    for s in diff:
        if s.get("f12") in ("001203", "605018", "300716"):
            print("FOUND", s.get("f12"), s.get("f14"), "f616=", s.get("f616"), "f6=", s.get("f6"),
                  "f5=", s.get("f5"), "f21=", s.get("f21"), "f43=", s.get("f43"))
except Exception as e:
    print("clist err:", e)