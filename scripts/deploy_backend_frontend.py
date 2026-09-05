#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署 spotMap 回滚 + 前端 filter-source 修复 到 测试/生产机:
  清空远端 dist/assets -> 上传 stocks.py + 前端 dist -> 重启 kuaixuan + kx-worker -> MD5 校验
用法: python scripts/deploy_backend_frontend.py <test|prod> <root密码>
      或用环境变量 KX_TEST_PASS / KX_PROD_PASS 传密码(参数缺省时)。"""
import os, socket, sys, subprocess
import paramiko

PROXY = ("127.0.0.1", 18080)
HOSTS = {
    "test": ("47.99.153.123", 22, "KX_TEST_PASS"),
    "prod": ("121.196.230.80", 22, "KX_PROD_PASS"),
}
DEPLOY = "/opt/kuaixuan"
FRONT = "/workspace/frontend/dist"

which = sys.argv[1] if len(sys.argv) > 1 else ""
if which not in HOSTS:
    sys.exit("[错] 用法: python scripts/deploy_backend_frontend.py <test|prod> [root密码] 或设 KX_TEST_PASS/KX_PROD_PASS")
HOST, PORT, ENVV = HOSTS[which]
PASS = sys.argv[2] if len(sys.argv) > 2 else os.environ.get(ENVV, "")
if not PASS:
    sys.exit(f"[错] 缺少密码: 参数或环境变量 {ENVV}")

def proxy_socket(h, p):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(20); s.connect(PROXY)
    s.sendall(f"CONNECT {h}:{p} HTTP/1.1\r\nHost: {h}:{p}\r\n\r\n".encode())
    r = b""
    while b"\r\n\r\n" not in r:
        c = s.recv(4096)
        if not c: break
        r += c
    if b"200" not in r: raise RuntimeError(r.split(b"\r\n")[0])
    return s

sock = proxy_socket(HOST, PORT)
ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, PORT, username="root", password=PASS, sock=sock, timeout=25)
sftp = ssh.open_sftp()
def sh(c, echo=True):
    i, o, e = ssh.exec_command(c)
    out = o.read().decode().strip()
    if echo and out: print(out)
    err = e.read().decode().strip()
    if err: print("STDERR:", err[:300])

# 0. 清空远端旧 dist assets(留备份), 避免碎片堆积
sh(f"cp -a {DEPLOY}/dist {DEPLOY}/dist_bak 2>/dev/null; rm -rf {DEPLOY}/dist/assets && mkdir -p {DEPLOY}/dist/assets")

# 1. 后端文件
print("[BACKEND] stocks.py")
sh(f"mkdir -p {DEPLOY}/backend/app/api")
sftp.put("/workspace/backend/app/api/stocks.py", f"{DEPLOY}/backend/app/api/stocks.py")
print("  up", f"{DEPLOY}/backend/app/api/stocks.py", os.path.getsize("/workspace/backend/app/api/stocks.py"), "B")

# 2. 前端 dist
print("[FRONTEND] dist")
def mkdirs(rd):
    cur = ""
    for p in rd.split("/"):
        if not p: continue
        cur += "/" + p
        try: sftp.stat(cur)
        except IOError: sftp.mkdir(cur)
def up(local, remote):
    mkdirs(os.path.dirname(remote)); sftp.put(local, remote)
for root, dirs, files in os.walk(FRONT):
    rel = os.path.relpath(root, FRONT)
    for fn in files:
        lr = os.path.join(root, fn)
        rr = f"{DEPLOY}/dist/{rel}" if rel != "." else f"{DEPLOY}/dist"
        rr = rr.rstrip("/") + "/" + fn
        up(lr, rr)
print("  dist 上传完成")
sh(f"chmod -R a+rX {DEPLOY}/dist")

# 3. 重启双服务
print("[RESTART] kuaixuan + kx-worker")
sh(f"systemctl restart kuaixuan kx-worker; sleep 3; "
   f"echo active_kx=$(systemctl is-active kuaixuan) active_wk=$(systemctl is-active kx-worker)")

# 4. MD5 校验后端
local_md5 = subprocess.check_output(["md5sum", "/workspace/backend/app/api/stocks.py"]).decode().split()[0]
i, o, e = ssh.exec_command(f"md5sum {DEPLOY}/backend/app/api/stocks.py")
remote_md5 = o.read().decode().split()[0]
print("[VERIFY]", "OK " if local_md5 == remote_md5 else "MISMATCH",
      f"stocks.py local={local_md5} remote={remote_md5}")
# index.html 校验(判断前端是否到位)
i, o, e = ssh.exec_command(f"md5sum {DEPLOY}/dist/index.html {DEPLOY}/dist/assets/*.js 2>/dev/null | wc -l")
print(f"[VERIFY] 远端 dist 文件数(index.html + js): {o.read().decode().strip()}")

sftp.close(); ssh.close()
print(f"{which} 机部署完成 ✓ (stocks.py 回滚 + 前端 filter 修复)")