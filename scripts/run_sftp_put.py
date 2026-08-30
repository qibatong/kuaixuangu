#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sftp 上传本地文件到远程(经 127.0.0.1:18080 代理)。
用法: python run_sftp_put.py <host> <pass> <local> <remote>"""
import socket, sys, os, posixpath
import paramiko

PROXY = ("127.0.0.1", 18080)
host, passwd, local, remote = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

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
sftp = ssh.open_sftp()

def mkdirs(rdir):
    parts = rdir.split("/")
    cur = ""
    for p in parts:
        if not p:
            continue
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)

mkdirs(posixpath.dirname(remote))
sftp.put(local, remote)
print(f"up {remote} ({os.path.getsize(local)}B)")
sftp.close()
ssh.close()