#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远端目录全量 md5(用于跨机产物一致性比对)。

用法(远端执行): python _kx_dist_md5.py <dir>
输出: "<md5> <relpath>" 每行一条, 按 relpath 排序
"""
import os, sys, hashlib

root = sys.argv[1]
out = []
for dp, dns, fns in os.walk(root):
    for fn in fns:
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, root).replace("\\", "/")
        try:
            h = hashlib.md5(open(p, "rb").read()).hexdigest()
        except Exception:
            continue
        out.append("%s %s" % (h, rel))
print("\n".join(sorted(out)))
