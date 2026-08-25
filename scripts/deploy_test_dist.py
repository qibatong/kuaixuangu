#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传本机构建的前端 dist 到测试机 /opt/kuaixuan/dist
- 备份旧 dist → dist.bak_<ts>
- 清空旧 dist/assets
- 上传 index.html + favicon + assets/*
"""
import os, socket, sys, time
import paramiko

PROXY = ("127.0.0.1", 18080)
TEST = ("47.99.153.123", 22)
USER = "root"
DEPLOY = "/opt/kuaixuan"
LOCAL_DIST = "/workspace/frontend/dist"
PASS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KX_TEST_PASS")
if not PASS:
    sys.exit("[错误] 用法: python deploy_test_dist.py <root密码>  或  设置 KX_TEST_PASS 环境变量")


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


def sftp_walk_put(sftp, local_dir, remote_dir):
    """递归上传目录"""
    try:
        sftp.stat(remote_dir)
    except IOError:
        sftp.mkdir(remote_dir)
    for entry in os.listdir(local_dir):
        local_path = os.path.join(local_dir, entry)
        remote_path = remote_dir + "/" + entry
        if os.path.isdir(local_path):
            sftp_walk_put(sftp, local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)
            print(f"  ✓ {remote_path}")


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
sftp = ssh.open_sftp()

ts = time.strftime("%Y%m%d_%H%M%S")

# 1. 备份旧 dist
print(f"[1/3] 备份旧 dist → dist.bak_{ts} ...")
sh(ssh, f"cp -r {DEPLOY}/dist {DEPLOY}/dist.bak_{ts}")
# 清空旧 assets(hash 文件名, 不清理会堆积)
sh(ssh, f"rm -rf {DEPLOY}/dist/assets && mkdir -p {DEPLOY}/dist/assets")

# 2. 上传新 dist
print("[2/3] 上传新 dist ...")
for entry in os.listdir(LOCAL_DIST):
    local_path = os.path.join(LOCAL_DIST, entry)
    remote_path = f"{DEPLOY}/dist/{entry}"
    if os.path.isdir(local_path):
        sftp_walk_put(sftp, local_path, remote_path)
    else:
        sftp.put(local_path, remote_path)
        print(f"  ✓ {remote_path}")

# 3. 验证
print("[3/3] 验证 dist 已更新 ...")
sh(ssh, (
    f"grep -c 'auction_signal' {DEPLOY}/dist/assets/HistoryView-*.js 2>/dev/null | head -3; "
    f"grep -c 'auctionSignal' {DEPLOY}/dist/assets/StockView-*.js 2>/dev/null | head -3; "
    f"ls {DEPLOY}/dist/assets/ | head -5; "
    f"curl -sS -o /dev/null -w 'index=%{{http_code}}\\n' http://127.0.0.1/ --max-time 5"
))

sftp.close()
ssh.close()
print("测试机 前端 dist 部署完成 ✓")
