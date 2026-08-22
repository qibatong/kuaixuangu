#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产机部署: 上传后端 kpl.py + 前端 dist 到 /opt/kuaixuan, 重启 web 服务
通过本机 127.0.0.1:18080 HTTP CONNECT 代理转发 SSH(SFTP) 到生产机
"""
import os, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
PROD = ("121.196.230.80", 22)
USER = os.environ.get("KX_PROD_USER", "root")
PASS = os.environ.get("KX_PROD_PASS")
if not PASS:
    sys.exit("[错误] 未设置 KX_PROD_PASS 环境变量(生产密码不入库, 用环境变量注入)")
# 部署目录(与测试机一致)
DEPLOY = "/opt/kuaixuan"

LOCAL_BACKEND_KPL = "/workspace/backend/app/api/kpl.py"
REMOTE_BACKEND_KPL = f"{DEPLOY}/backend/app/api/kpl.py"
LOCAL_DIST = "/workspace/frontend/dist"
REMOTE_DIST = f"{DEPLOY}/dist"


def proxy_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect(PROXY)
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    if b"200" not in resp:
        raise RuntimeError(f"proxy CONNECT failed: {resp.split(b'\r\n')[0]!r}")
    return s


def connect():
    sock = proxy_socket(*PROD)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(PROD[0], PROD[1], username=USER, password=PASS, sock=sock, timeout=20)
    return ssh


def sftp_mkdirs(sftp, remote_dir):
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


def upload_dist(sftp):
    """递归上传 dist/ 目录"""
    n = 0
    base = REMOTE_DIST
    for root, dirs, files in os.walk(LOCAL_DIST):
        rel = os.path.relpath(root, LOCAL_DIST)
        remote_dir = base if rel == "." else f"{base}/{rel.replace(os.sep, '/')}"
        sftp_mkdirs(sftp, remote_dir)
        for f in files:
            lp = os.path.join(root, f)
            rp = f"{remote_dir}/{f}"
            sftp.put(lp, rp)
            n += 1
    return n


def main():
    ssh = connect()
    sftp = ssh.open_sftp()

    # 1. 备份现有 dist
    print("[1/4] 备份现有生产 dist ...")
    ssh.exec_command(
        f"rm -rf {DEPLOY}/dist_bak && cp -a {DEPLOY}/dist {DEPLOY}/dist_bak 2>/dev/null; mkdir -p {DEPLOY}/dist"
    )[-1]

    # 2. 上传后端 kpl.py
    print("[2/4] 上传后端 kpl.py ...")
    sftp.put(LOCAL_BACKEND_KPL, REMOTE_BACKEND_KPL)

    # 3. 上传前端 dist
    print("[3/4] 上传前端 dist (递归) ...")
    n = upload_dist(sftp)
    print(f"      上传 {n} 个前端文件")

    # 4. chmod + 重启 web
    print("[4/4] chmod + 重启 kuaixuan.service ...")
    stdin, stdout, stderr = ssh.exec_command(
        f"chmod -R a+rX {DEPLOY}/dist && systemctl restart kuaixuan && echo RESTART_OK"
    )
    out = stdout.read().decode()
    err = stderr.read().decode()
    print("      ", out.strip(), err.strip())

    sftp.close()
    ssh.close()
    print("生产部署完成 ✓")


if __name__ == "__main__":
    main()