#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程执行命令(经 127.0.0.1:18080 CONNECT 代理)。用法:
    python run_remote.py <host> <pass> '<cmd>'
host: 47.99.153.123(测试) / 121.196.230.80(生产)"""
import socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
host, passwd, cmd = sys.argv[1], sys.argv[2], sys.argv[3]

def proxy_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(25)
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

sock = proxy_socket(host, 22)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, 22, username="root", password=passwd, sock=sock, timeout=25)
stdin, stdout, stderr = ssh.exec_command(cmd, get_pty=False)
out = stdout.read().decode()
err = stderr.read().decode()
print("=== STDOUT ===")
print(out)
if err:
    print("=== STDERR ===")
    print(err)
ssh.close()