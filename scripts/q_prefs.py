#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试机: 查 testuser 的 filter_prefs 内容"""
import subprocess
db = "/opt/kuaixuan/kuaixuan.db"
out = subprocess.check_output(
    f"sqlite3 {db} \"SELECT 'filter_prefs=',filter_prefs FROM users WHERE username='testuser';\"",
    shell=True).decode()
print(out.strip() or "(empty)")
print("done")