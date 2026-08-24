#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import socket, sys
import paramiko
hpc = ("121.196.230.80", "root", "Xqhty@84313313")
hp, user, pw = hpc
s = socket.socket(); s.settimeout(30); s.connect(("127.0.0.1", 18080))
s.sendall(f"CONNECT {hp}:22 HTTP/1.1\r\nHost: {hp}:22\r\n\r\n".encode())
resp = b""
while b"\r\n\r\n" not in resp:
    c = s.recv(4096)
    if not c: break
    resp += c
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(hp, 22, username=user, password=pw, sock=s, timeout=30)
sftp = cli.open_sftp()
if sys.argv[1] == "-u":
    sftp.put(sys.argv[2], sys.argv[3]); print("UPLOADED", sys.argv[3])
else:
    _in, out, err = cli.exec_command(" ".join(sys.argv[1:]), timeout=180)
    res = b""
    while True:
        c = out.channel.recv(65536)
        if not c: break
        res += c
    print(res.decode(errors="replace"))
    e = err.read().decode(errors="replace")
    if e: print("[stderr]", e[:2000])
sftp.close(); cli.close()