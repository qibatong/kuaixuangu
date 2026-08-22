#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署改动到测试机: 清空远端 dist/assets 旧资源 -> 上传前端 dist + 后端文件 -> 重启 kuaixuan"""
import os, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
TEST = ("47.99.153.123", 22)
USER = "root"
DEPLOY = "/opt/kuaixuan"

def proxy_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(20)
    s.connect(PROXY)
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    if b"200" not in resp:
        raise RuntimeError(resp.split(b'\r\n')[0])
    return s

passwd = sys.argv[1]
sock = proxy_socket(*TEST)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TEST[0], TEST[1], username=USER, password=passwd, sock=sock, timeout=25)
sftp = ssh.open_sftp()

def mkdirs(remote_dir):
    parts = remote_dir.split("/")
    cur = ""
    for p in parts:
        if not p:
            continue
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)

def up(local, remote):
    mkdirs(os.path.dirname(remote))
    sftp.put(local, remote)
    print(f"  up {remote}")

# 0. 清空远端 dist 内旧 index.html + assets, 避免旧碎片资源堆积/*
i, o, e = ssh.exec_command(f"rm -rf {DEPLOY}/dist_bak && cp -a {DEPLOY}/dist {DEPLOY}/dist_bak 2>/dev/null; rm -rf {DEPLOY}/dist/assets && mkdir -p {DEPLOY}/dist/assets")
o.read(); e.read()

# 1. 后端文件
up("/workspace/backend/app/services/kpl.py", f"{DEPLOY}/backend/app/services/kpl.py")
up("/workspace/backend/app/api/kpl.py", f"{DEPLOY}/backend/app/api/kpl.py")

# 2. 前端 dist 递归
base = f"{DEPLOY}/dist"
for root, dirs, files in os.walk("/workspace/frontend/dist"):
    rel = os.path.relpath(root, "/workspace/frontend/dist")
    remote_dir = base if rel == "." else f"{base}/{rel.replace(os.sep, '/')}"
    for f in files:
        up(os.path.join(root, f), f"{remote_dir}/{f}")

# 3. 重启服务
i, o, e = ssh.exec_command(f"chmod -R a+rX {DEPLOY}/dist; systemctl restart kuaixuan; sleep 2; systemctl is-active kuaixuan")
print("  后处理输出:", o.read().decode())
err = e.read().decode()
if err:
    print("  后处理ERR:", err)

sftp.close()
ssh.close()
print("部署到测试机完成 ✓")