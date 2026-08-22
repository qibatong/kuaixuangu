#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 HTTP CONNECT 代理连接测试机, 运行本地探测脚本 probe_kpl_hot.py"""
import socket, sys, io
import paramiko

PROXY = ("127.0.0.1", 18080)
TEST = ("47.99.153.123", 22)
USER = "root"

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
        raise RuntimeError(f"proxy CONNECT failed: {resp.split(b'\r\n')[0]!r}")
    return s

passwd = sys.argv[1] if len(sys.argv) > 1 else input("password:")
sock = proxy_socket(*TEST)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TEST[0], TEST[1], username=USER, password=passwd, sock=sock, timeout=25)

# 上传探测脚本
sftp = ssh.open_sftp()
local = "/workspace/scripts/probe_kpl_hot.py"
remote = "/tmp/probe_kpl_hot.py"
sftp.put(local, remote)
sftp.close()

print("=== 运行探测脚本(测试机) ===")
stdin, stdout, stderr = ssh.exec_command("python3 /tmp/probe_kpl_hot.py 2>&1")
out = stdout.read().decode()
err = stderr.read().decode()
print(out)
if err:
    print("[stderr]", err)

ssh.close()
print("done")