#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端 dist 部署到生产(nginx 根目录 /opt/kuaixuan/dist, **不是** frontend/dist)。

用法:
  python _kx_fe_deploy_prod.py pack    # 本地打包 frontend/dist -> frontend/_dist_upload.tar.gz
  python _kx_fe_deploy_prod.py stage   # 上传 + 解到暂存 + 断言(不动线上)
  python _kx_fe_deploy_prod.py apply   # 原子换盘 + nginx -t + 探活 + 新旧入口校验

铁律:
  - nginx 站点根目录 = /opt/kuaixuan/dist(配置 /etc/nginx/conf.d/kuaixuan.conf)
  - 换盘用 mv(原子), 先备份再换
  - 远端脚本必须 LF
"""
import sys, os, tarfile, posixpath
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paramiko
from _kx_put_lf import to_lf

HOST = "121.196.230.80"
PASS = "Xqhty@84313313"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(REPO, "frontend", "dist")
TAR = os.path.join(REPO, "frontend", "_dist_upload.tar.gz")

ENTRY = "index-BzMX48Pm.js"           # 新(2026-10-04 第11批: 视觉令牌收敛② — 补长尾色/标题字号 + 阴影3档 + 间距4px栅格)
OLD_ENTRY = "index-DOwMOVLJ.js"       # 生产线上当前(2026-10-04 第10批: 收敛①)
EXP_FILES = 771
EXP_ASSETS = 762

MUST_HAVE = ["activity/track", "usage-rank", "active-users", "login-log", "user-activity"]
MUST_NOT = ["activityUsage"]         # 已修复的死变量, 绝不能重现

REMOTE_TAR = "/root/_dist_upload.tar.gz"
STAGE_DIR = "/root/_kx_dist_new"
PROD_DIST = "/opt/kuaixuan/dist"

CHECK_PY = r'''
import os, sys
d = sys.argv[1]
entry, exp_files, exp_assets = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
must = sys.argv[5].split(",")
notmust = [x for x in sys.argv[6].split(",") if x]
bad = []
files = sum(len(f) for _, _, f in os.walk(d))
assets = os.path.join(d, "assets")
na = len(os.listdir(assets)) if os.path.isdir(assets) else -1
idx = os.path.join(d, "index.html")
have_entry = os.path.exists(idx) and (entry in open(idx, encoding="utf-8", errors="replace").read())
print("files=%d (期望 %d)  assets=%d (期望 %d)  entry_hit=%s" % (files, exp_files, na, exp_assets, have_entry))
if files != exp_files: bad.append("files")
if na != exp_assets: bad.append("assets")
if not have_entry: bad.append("entry")
blob = ""
for fn in sorted(os.listdir(assets)):
    if fn.endswith(".js"):
        blob += open(os.path.join(assets, fn), encoding="utf-8", errors="replace").read()
for p in must:
    hit = p in blob
    print("  %s must-have %s" % ("ok  " if hit else "MISS", p))
    if not hit: bad.append("must:" + p)
for p in notmust:
    hit = p in blob
    print("  %s must-NOT  %s" % ("ok  " if not hit else "HIT!", p))
    if hit: bad.append("mustnot:" + p)
print("CHECK_OK" if not bad else "CHECK_FAIL " + str(bad))
'''

APPLY_SH = r'''#!/bin/bash
set -e
NEW=/root/_kx_dist_new
D=/opt/kuaixuan/dist
STAMP=$(date +%Y%m%d-%H%M%S)
echo "--- 备份当前 ---"
mv "$D" "${D}_bak_${STAMP}"
echo "backup -> ${D}_bak_${STAMP} ($(find ${D}_bak_${STAMP} -type f | wc -l) files)"
echo "--- 换盘 ---"
mv "$NEW" "$D"
echo "--- 权限(🔴 必做: tar 解出的目录是 750, nginx 用户进不去 => 整站 403) ---"
find "$D" -type d -exec chmod 755 {} +
find "$D" -type f -exec chmod 644 {} +
ls -ld "$D" "$D/assets"
echo "--- nginx ---"
nginx -t
systemctl reload nginx
sleep 2
echo "--- 探活 ---"
curl -s -k -o /dev/null -w "root=%{http_code}\n" https://127.0.0.1/
echo "--- 入口校验 ---"
grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$D/index.html" | head -1
echo "NEWENTRY=$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' $D/index.html | head -1)"
curl -s -k -o /dev/null -w "new_entry=%{http_code}\n" "https://127.0.0.1/assets/__NEWENTRY__"
'''


def conn():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, username="root", password=PASS, timeout=60)
    return c


def sh(c, cmd, echo=True):
    _, out, err = c.exec_command(cmd)
    o = out.read().decode(errors="replace")
    e = err.read().decode(errors="replace")
    if echo and o.strip():
        print(o, end="")
    if e.strip():
        print("=== STDERR ===")
        print(e[:1500], end="")
    return o, e


def pack():
    n = 0
    with tarfile.open(TAR, "w:gz") as t:
        for root, dns, fns in os.walk(DIST):
            for fn in fns:
                p = os.path.join(root, fn)
                t.add(p, arcname=os.path.relpath(p, DIST).replace("\\", "/"))
                n += 1
    print("packed %d files -> %s (%d bytes)" % (n, TAR, os.path.getsize(TAR)))


def stage():
    c = conn()
    sftp = c.open_sftp()
    print("uploading tar ...")
    sftp.put(TAR, REMOTE_TAR)
    print("up %s (%dB)" % (REMOTE_TAR, os.path.getsize(TAR)))
    with sftp.open("/root/_kx_dist_check.py", "wb") as f:
        f.write(to_lf(CHECK_PY.encode()))
    sftp.close()
    sh(c, "rm -rf %s && mkdir -p %s && tar -xzf %s -C %s && echo 'extracted'" % (STAGE_DIR, STAGE_DIR, REMOTE_TAR, STAGE_DIR))
    o, _ = sh(c, "/opt/kuaixuan-venv/bin/python /root/_kx_dist_check.py %s %s %d %d %s %s"
              % (STAGE_DIR, ENTRY, EXP_FILES, EXP_ASSETS, ",".join(MUST_HAVE), ",".join(MUST_NOT)))
    c.close()
    print("\nSTAGE %s" % ("OK" if "CHECK_OK" in o else "FAILED — 线上未改动"))


def apply():
    c = conn()
    sftp = c.open_sftp()
    body = APPLY_SH.replace("__NEWENTRY__", ENTRY)
    with sftp.open("/root/_kx_dist_apply.sh", "wb") as f:
        f.write(to_lf(body.encode()))
    sftp.close()
    sh(c, "bash /root/_kx_dist_apply.sh")
    sh(c, "df -h / | tail -1")
    c.close()


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "?"
    {"pack": pack, "stage": stage, "apply": apply}.get(m, lambda: print(__doc__))()
