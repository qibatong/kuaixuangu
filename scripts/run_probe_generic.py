#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import socket, sys
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
        raise RuntimeError(resp.split(b'\r\n')[0])
    return s

passwd = sys.argv[1]
script_local = sys.argv[2]
script_remote = "/tmp/" + sys.argv[2].split("/")[-1]

sock = proxy_socket(*TEST)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TEST[0], TEST[1], username=USER, password=passwd, sock=sock, timeout=25)
sftp = ssh.open_sftp()
sftp.put(script_local, script_remote)
sftp.close()
stdin, stdout, stderr = ssh.exec_command(f"python3 {script_remote} 2>&1")
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print("[stderr]", err)
ssh.close()
print("done")