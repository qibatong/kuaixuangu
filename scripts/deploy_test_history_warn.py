#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试机部署(历史批次页异动一致性 + 首页异动显示)
- 上传:
    后端 db/database.py(加三列迁移) + services/history.py(save_batch/query_history 补字段)
    前端 src/components/StockTable.vue + src/views/HistoryView.vue + src/stores/stocks.js
- 重启 systemd kuaixuan.service
- 验证:
    1) batch_stocks 表含 auction_signal/seal_ratio/accel 列
    2) save_batch 落库后能读回三字段
    3) query_history 返回字段含三字段
    4) 服务健康
"""
import os, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
TEST = ("47.99.153.123", 22)
USER = "root"
DEPLOY = "/opt/kuaixuan"
PASS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KX_TEST_PASS")
if not PASS:
    sys.exit("[错误] 用法: python deploy_test_history_warn.py <root密码>  或  设置 KX_TEST_PASS 环境变量")

BACKEND_FILES = [
    ("/workspace/backend/app/db/database.py",      f"{DEPLOY}/backend/app/db/database.py"),
    ("/workspace/backend/app/services/history.py", f"{DEPLOY}/backend/app/services/history.py"),
]
FRONTEND_FILES = [
    ("/workspace/frontend/src/components/StockTable.vue", f"{DEPLOY}/frontend/src/components/StockTable.vue"),
    ("/workspace/frontend/src/views/HistoryView.vue",     f"{DEPLOY}/frontend/src/views/HistoryView.vue"),
    ("/workspace/frontend/src/stores/stocks.js",          f"{DEPLOY}/frontend/src/stores/stocks.js"),
]


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


def sftp_mkdirs(sftp, rdir):
    cur = ""
    for p in rdir.split("/"):
        if not p: continue
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


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

# 1. 备份 + 上传后端
print("[1/5] 备份 + 上传后端 ...")
ts = "$(date +%s)"
for local, remote in BACKEND_FILES:
    sh(ssh, f"cp {remote} {remote}.bak_{ts} 2>/dev/null; true")
for local, remote in BACKEND_FILES:
    sftp_mkdirs(sftp, os.path.dirname(remote))
    sftp.put(local, remote)
    print(f"  ✓ {remote}")

# 2. 上传前端
print("[2/5] 上传前端 ...")
for local, remote in FRONTEND_FILES:
    sh(ssh, f"cp {remote} {remote}.bak_{ts} 2>/dev/null; true")
    sftp_mkdirs(sftp, os.path.dirname(remote))
    sftp.put(local, remote)
    print(f"  ✓ {remote}")

# 3. 重启后端服务(触发 init_db 迁移加三列)
print("[3/5] 重启 kuaixuan.service (触发 DB 迁移) ...")
sh(ssh, "systemctl restart kuaixuan && sleep 3 && systemctl is-active kuaixuan")

# 4. 验证 DB 迁移 + 落库回读
print("[4/5] 验证 DB 迁移 + 落库回读 ...")
sh(ssh, (
    f"cd {DEPLOY}/backend && "
    f"python3 -c \""
    f"from app.db import database; "
    f"conn = database.get_conn(); "
    f"cols = [r[1] for r in conn.execute('PRAGMA table_info(batch_stocks)').fetchall()]; "
    f"print('cols含auction_signal:', 'auction_signal' in cols); "
    f"print('cols含seal_ratio:', 'seal_ratio' in cols); "
    f"print('cols含accel:', 'accel' in cols); "
    f"from app.services import history; "
    f"fake = [{{'code':'600001','name':'测试甲','probability':80,'confidence':70,'bidChange':3.2,'realChange':3.1,'entityChange':3.0,'bidTurnover':5.5,'warnType':3,'circulationMV':40.0,'industry':'软件','concept':'AI','bidAmt':2800.0,'bidRatio':14.0,'auctionSignal':4,'sealRatio':1.2,'accel':1.5}}]; "
    f"bid = history.save_batch(1, 'filter', fake, {{'markets':['sh_sz']}}); "
    f"print('save_batch bid:', bid); "
    f"b, stocks = history.get_batch(bid, 1); "
    f"s0 = stocks[0] if stocks else {{}}; "
    f"print('回读 auction_signal:', s0.get('auction_signal'), 'seal_ratio:', s0.get('seal_ratio'), 'accel:', s0.get('accel')); "
    f"conn.close()\""
))

# 5. 健康检查 + 前端构建验证(检查文件已更新)
print("[5/5] 健康检查 + 验证前端文件已更新 ...")
sh(ssh, (
    "curl -sS -o /dev/null -w 'be=%{http_code}\\n' http://127.0.0.1:8010/healthz --max-time 5; "
    "curl -sS -o /dev/null -w 'index=%{http_code}\\n' http://127.0.0.1/ --max-time 5"
))
sh(ssh, (
    f"grep -c 'auctionSignal' {DEPLOY}/frontend/src/components/StockTable.vue; "
    f"grep -c 'auction_signal' {DEPLOY}/frontend/src/views/HistoryView.vue; "
    f"grep -c 'auctionSignal' {DEPLOY}/frontend/src/stores/stocks.js"
))

# 触发前端 Vite 重建(如有 dev 服务或 build 脚本)
print("[附加] 触发前端重建 ...")
sh(ssh, f"cd {DEPLOY}/frontend && (npm run build 2>&1 | tail -5) || echo 'build skipped/failed(可能是 dev 模式)'")

sftp.close()
ssh.close()
print("测试机 部署完成 ✓")
