#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v4.11.53 生产部署：26 文件整批对齐（修 yday 冻结 + 猫爪换源整批）
用法：KX_PROD_PASS=xxx python3 scripts/deploy_v41153_prod.py
"""
import os, socket, sys, time
import paramiko

PROXY = ("127.0.0.1", 18080)
PROD = ("121.196.230.80", 22)
USER = "root"
PASS = os.environ.get("KX_PROD_PASS")
if not PASS:
    sys.exit("[错误] 未设置 KX_PROD_PASS 环境变量")

DEPLOY = "/opt/kuaixuan"
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 26 个要上传的文件（相对 repo 根目录）──
FILES = [
    # API 层
    "backend/app/api/health.py",
    "backend/app/api/picker.py",
    "backend/app/api/stocks.py",
    # DB / core
    "backend/app/db/database.py",
    "backend/app/core/trade_calendar.py",
    # services 主链
    "backend/app/services/auction_snapshot.py",
    "backend/app/services/cache_store.py",
    "backend/app/services/fetcher.py",
    "backend/app/services/history.py",
    "backend/app/services/meoz_client.py",
    "backend/app/services/tickplus.py",
    "backend/app/services/yday_prewarm.py",
    # picker 子模块
    "backend/app/services/picker/contract.py",
    "backend/app/services/picker/filter.py",
    "backend/app/services/picker/mode.py",
    "backend/app/services/picker/pipeline.py",
    "backend/app/services/picker/precompute.py",
    "backend/app/services/picker/score.py",
    # picker/sources
    "backend/app/services/picker/sources/__init__.py",
    "backend/app/services/picker/sources/base.py",
    "backend/app/services/picker/sources/meoz.py",
    # contracts/ 新增目录（5 文件）
    "backend/app/services/contracts/__init__.py",
    "backend/app/services/contracts/fields.py",
    "backend/app/services/contracts/probe.py",
    "backend/app/services/contracts/registry.py",
    "backend/app/services/contracts/schema.py",
]

TS = time.strftime("%Y%m%d-%H%M%S")
BACKUP = f"{DEPLOY}/backend_bak_v41153_{TS}"


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


def run(ssh, cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err


def main():
    print(f"[0/5] 连接生产 {PROD[0]} ...")
    ssh = connect()
    sftp = ssh.open_sftp()
    print("      连接成功")

    # 1. 备份
    print(f"[1/5] 备份生产 backend/ → {BACKUP}/ ...")
    out, err = run(ssh, f"cp -a {DEPLOY}/backend {BACKUP} && echo BACKUP_OK")
    if "BACKUP_OK" not in out:
        print(f"      备份失败！{err}")
        sys.exit(1)
    print("      备份完成")

    # 2. 上传 26 文件
    print(f"[2/5] 上传 {len(FILES)} 个文件 ...")
    for rel in FILES:
        local = os.path.join(LOCAL_ROOT, rel)
        remote = f"{DEPLOY}/{rel}"
        if not os.path.exists(local):
            print(f"      ⚠ 本地不存在: {rel}")
            continue
        sftp_mkdirs(sftp, os.path.dirname(remote))
        sftp.put(local, remote)
        print(f"      ✓ {rel}")

    # 3. py_compile 检查
    print("[3/5] py_compile 语法检查 ...")
    files_str = " ".join(f"{DEPLOY}/{f}" for f in FILES if f.endswith(".py"))
    out, err = run(ssh, f"cd {DEPLOY}/backend && python3 -m py_compile {files_str} 2>&1 && echo COMPILE_OK")
    if "COMPILE_OK" not in out:
        print(f"      ✗ 语法错误！\n{err}")
        print(f"      ⚠ 正在回滚备份 ...")
        run(ssh, f"rm -rf {DEPLOY}/backend && cp -a {BACKUP} {DEPLOY}/backend")
        print("      已回滚。")
        sys.exit(1)
    print("      语法全部通过")

    # 4. 重启服务
    print("[4/5] 重启 kuaixuan + kx-worker ...")
    out, err = run(ssh, f"systemctl restart kuaixuan && systemctl restart kx-worker 2>/dev/null; sleep 2; systemctl is-active kuaixuan kx-worker")
    print(f"      {out}")

    # 5. 健康检查
    print("[5/5] 健康检查 ...")
    time.sleep(3)
    out, err = run(ssh, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health")
    print(f"      /api/health HTTP {out}")
    out2, err2 = run(ssh, "journalctl -u kuaixuan --since '30 seconds ago' --no-pager | grep -i error | tail -5")
    if out2:
        print(f"      ⚠ 近期 ERROR 日志：\n{out2}")
    else:
        print("      无近期 ERROR")

    sftp.close()
    ssh.close()
    print(f"\n✅ 部署完成。备份在 {BACKUP}")
    print(f"   回滚命令：rm -rf {DEPLOY}/backend && cp -a {BACKUP} {DEPLOY}/backend && systemctl restart kuaixuan kx-worker")


if __name__ == "__main__":
    main()
