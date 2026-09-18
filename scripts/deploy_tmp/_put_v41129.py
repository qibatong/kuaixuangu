#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v4.11.29 批量上传(测试机/生产机通用) —— 只传本次改动涉及的文件。

用法:
    python _put_v41129.py test            # 传后端 + 测试
    python _put_v41129.py test dist       # 追加传前端构建产物
    python _put_v41129.py prod

⚠️ 不做备份/重启, 只负责 put —— 部署动作由调用方显式执行(避免误碰生产)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _ssh_exec import connect  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BE = "/opt/kuaixuan/backend"

# (本地相对路径, 远端绝对路径)
BACKEND = [
    ("backend/app/services/picker/mode.py",           BE + "/app/services/picker/mode.py"),
    ("backend/app/services/history.py",               BE + "/app/services/history.py"),
    ("backend/app/services/auction_snapshot.py",      BE + "/app/services/auction_snapshot.py"),
    ("backend/app/api/stocks.py",                     BE + "/app/api/stocks.py"),
    ("backend/tests/test_freeze_guard_0918.py",       BE + "/tests/test_freeze_guard_0918.py"),
    ("backend/tests/test_pick_window_guard.py",       BE + "/tests/test_pick_window_guard.py"),
    ("backend/tests/test_stocks_refresh_fallback.py", BE + "/tests/test_stocks_refresh_fallback.py"),
]

# 前端: 只上传 dist(线上跑的是构建产物); 源码另传一份供对拍用例读取
FRONTEND_SRC = [
    ("frontend/src/utils/time.js",     BE + "/../frontend/src/utils/time.js"),
    ("frontend/src/utils/time.test.js", BE + "/../frontend/src/utils/time.test.js"),
    ("frontend/src/stores/stocks.js",  BE + "/../frontend/src/stores/stocks.js"),
    ("frontend/src/views/StockView.vue", BE + "/../frontend/src/views/StockView.vue"),
]


def put_dist(ssh, local_dist, remote_dist):
    sftp = ssh.open_sftp()
    n = 0
    try:
        for dirpath, _dirs, files in os.walk(local_dist):
            rel = os.path.relpath(dirpath, local_dist).replace("\\", "/")
            rdir = remote_dist if rel == "." else remote_dist + "/" + rel
            try:
                sftp.stat(rdir)
            except IOError:
                sftp.mkdir(rdir)
            for fn in files:
                sftp.put(os.path.join(dirpath, fn), rdir + "/" + fn)
                n += 1
    finally:
        sftp.close()
    return n


def _mkdirs(sftp, path):
    """递归建目录(已存在则忽略)。"""
    parts = path.split("/")
    cur = ""
    for p in parts:
        if not p:
            cur = "/"
            continue
        cur = (cur.rstrip("/") + "/" + p) if cur != "/" else "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            try:
                sftp.mkdir(cur)
            except IOError:
                pass


def main():
    which = sys.argv[1]
    with_dist = len(sys.argv) > 2 and sys.argv[2] == "dist"
    ssh = connect(which)
    try:
        sftp = ssh.open_sftp()
        pairs = list(BACKEND) + list(FRONTEND_SRC)
        for rel, remote in pairs:
            lp = os.path.join(ROOT, rel.replace("/", os.sep))
            if not os.path.exists(lp):
                print("SKIP(missing) %s" % rel)
                continue
            _mkdirs(sftp, remote.rsplit("/", 1)[0])
            sftp.put(lp, remote)
            print("PUT %-52s -> %s" % (rel, remote))
        sftp.close()
        if with_dist:
            n = put_dist(ssh, os.path.join(ROOT, "frontend", "dist"), "/opt/kuaixuan/dist")
            print("PUT dist: %d files -> /opt/kuaixuan/dist" % n)
    finally:
        ssh.close()
    print("DONE")


if __name__ == "__main__":
    main()
