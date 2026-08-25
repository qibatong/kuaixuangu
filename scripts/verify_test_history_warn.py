#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证测试机部署: 后端 API + 前端文件"""
import os, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
TEST = ("47.99.153.123", 22)
USER = "root"
DEPLOY = "/opt/kuaixuan"
PASS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KX_TEST_PASS")


def proxy_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(20)
    s.connect(PROXY)
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk: break
        resp += chunk
    if b"200" not in resp:
        raise RuntimeError(resp.split(b'\r\n')[0])
    return s


def sh(ssh, cmd):
    i, o, e = ssh.exec_command(cmd)
    out = o.read().decode()
    err = e.read().decode()
    if out: print("  STDOUT:", out.strip().replace("\n", "\n          "))
    if err: print("  STDERR:", err.strip().replace("\n", "\n          "))
    return out, err


sock = proxy_socket(*TEST)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TEST[0], TEST[1], username=USER, password=PASS, sock=sock, timeout=25)

print("[1] 找后端健康端点 ...")
for p in ["/healthz", "/api/health", "/health", "/api/"]:
    sh(ssh, f"curl -sS -o /dev/null -w '{p}=%{{http_code}}\\n' http://127.0.0.1:8010{p} --max-time 3")

print("[2] systemd 服务状态 + 日志尾 ...")
sh(ssh, "systemctl is-active kuaixuan; systemctl status kuaixuan --no-pager | head -10")

print("[3] 前端构建方式探测 ...")
sh(ssh, f"ls -la {DEPLOY}/frontend/dist 2>/dev/null | head -5; ls {DEPLOY}/frontend/package.json; which node; which pnpm; which yarn; which npm 2>/dev/null")
sh(ssh, f"cat {DEPLOY}/frontend/package.json | head -40")

print("[4] 前端 dist 是否含 auctionSignal/历史 auction_signal ...")
sh(ssh, f"grep -c 'auctionSignal' {DEPLOY}/frontend/dist/assets/*.js 2>/dev/null | head -3")
sh(ssh, f"grep -c 'auction_signal' {DEPLOY}/frontend/dist/assets/*.js 2>/dev/null | head -3")

print("[5] nginx 配置(前端服务方式) ...")
sh(ssh, "cat /etc/nginx/conf.d/*.conf 2>/dev/null | head -30; cat /etc/nginx/sites-enabled/* 2>/dev/null | head -30")

ssh.close()
