#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产机最小补部署: 只上传 stocks.py(spotMap 瘦身) + 重启 kuaixuan/kx-worker + 重校验 MD5."""
import os, socket, sys, subprocess
import paramiko

PROXY = ("127.0.0.1", 18080)
PROD  = ("121.196.230.80", 22)
DEPLOY = "/opt/kuaixuan"
PASS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KX_PROD_PASS")
if not PASS:
    sys.exit("[错] 用法: python scripts/deploy_prod_stocks_spotmap.py <root密码> 或 设置 KX_PROD_PASS")

ONLY_FILES = [
    ("/workspace/backend/app/api/stocks.py", f"{DEPLOY}/backend/app/api/stocks.py"),
]

def proxy_socket(h,p):
    s = socket.socket(); s.settimeout(20); s.connect(PROXY)
    s.sendall(f"CONNECT {h}:{p} HTTP/1.1\r\nHost: {h}:{p}\r\n\r\n".encode())
    r=b""
    while b"\r\n\r\n" not in r:
        c=s.recv(4096)
        if not c: break
        r+=c
    if b"200" not in r: raise RuntimeError(r.split(b"\r\n")[0])
    return s

sock = proxy_socket(*PROD)
ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(PROD[0], PROD[1], username="root", password=PASS, sock=sock, timeout=25)
sftp = ssh.open_sftp()
def sh(c):
    i,o,e=ssh.exec_command(c); print(o.read().decode().strip()); err=e.read().decode().strip()
    if err: print("STDERR:",err[:400])

# 上传
for lp, rp in ONLY_FILES:
    sftp.put(lp, rp)
    print("[UP]", rp, os.path.getsize(lp), "B")

# 重启双服务
print("[RESTART] kuaixuan + kx-worker")
sh(f"systemctl restart kuaixuan kx-worker; sleep 3; "
   f"echo active_kx=$(systemctl is-active kuaixuan) active_wk=$(systemctl is-active kx-worker)")

# 重校验 MD5
print("[VERIFY] 对比远端 MD5 vs 本地")
expected = dict()
for lp, rp in ONLY_FILES:
    out = subprocess.check_output(["md5sum", lp]).decode().split()[0]
    expected[rp] = out
for rp, local_md5 in expected.items():
    i,o,e = ssh.exec_command(f"md5sum {rp}")
    remote_md5 = o.read().decode().split()[0]
    ok = "OK" if remote_md5 == local_md5 else "MISMATCH"
    print(f"  {ok:<8} {rp}  local={local_md5} remote={remote_md5}")

sftp.close(); ssh.close()
print("生产机 stocks.py spotMap 瘦身补部署 ✓")