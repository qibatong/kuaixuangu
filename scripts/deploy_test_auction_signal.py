#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试机部署(方案B: warn 因子替换为竞价异动综合分)
- 上传: 后端 scorer.py + admin.py (标签更新)
- 重启 systemd kuaixuan.service
- 验证: 服务健康 + _compute_auction_signal 存在 + auctionSignal 字段输出
"""
import os, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
TEST = ("47.99.153.123", 22)
USER = "root"
DEPLOY = "/opt/kuaixuan"
PASS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KX_TEST_PASS")
if not PASS:
    sys.exit("[错误] 用法: python deploy_test_auction_signal.py <root密码>  或  设置 KX_TEST_PASS 环境变量")

BACKEND_FILES = [
    ("/workspace/backend/app/services/scorer.py", f"{DEPLOY}/backend/app/services/scorer.py"),
    ("/workspace/backend/app/api/admin.py",       f"{DEPLOY}/backend/app/api/admin.py"),
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
print("[1/4] 备份 + 上传后端改动文件 ...")
sh(ssh, f"cp {DEPLOY}/backend/app/services/scorer.py {DEPLOY}/backend/app/services/scorer.py.bak_$(date +%s) 2>/dev/null; true")
for local, remote in BACKEND_FILES:
    sftp_mkdirs(sftp, os.path.dirname(remote))
    sftp.put(local, remote)
    print(f"  ✓ {remote}")

# 2. 重启服务
print("[2/4] 重启 kuaixuan.service ...")
sh(ssh, "systemctl restart kuaixuan && sleep 3 && systemctl is-active kuaixuan")

# 3. 验证代码生效
print("[3/4] 验证代码生效 ...")
sh(ssh, (
    f"cd {DEPLOY}/backend && "
    f"python3 -c \"from app.services import scorer; "
    f"print('_compute_auction_signal:', hasattr(scorer, '_compute_auction_signal')); "
    f"sig = scorer._compute_auction_signal(3.5, 55, 2.5); "
    f"print('signal(3.5,55,2.5)=', sig); "
    f"cfg = scorer.get_scoring_cfg(); "
    f"w = cfg['factors']['warn']; "
    f"print('warn label:', w['label'], 'buckets:', len(w['buckets']), 'default:', w['default'])\""
))

# 4. 健康检查 + API 验证
print("[4/4] 健康检查 + API 验证 ...")
sh(ssh, (
    "curl -sS -o /dev/null -w 'be=%{http_code}\\n' http://127.0.0.1:8010/healthz --max-time 5; "
    "curl -sS -o /dev/null -w 'index=%{http_code}\\n' http://127.0.0.1/ --max-time 5"
))
# 验证 API 返回 auctionSignal 字段
sh(ssh, (
    f"cd {DEPLOY}/backend && "
    f"python3 -c \""
    f"import app.services.scorer as sc; "
    f"raw = [{{'f12':'600001','f14':'测试甲','f3':4.0,'f4':3.0,'f5':150000.0,'f6':2800.0,'f8':5.5,'f10':1.8,'f21':4e9,'f615':3.5,'f616':5e7,'f630':2}}]; "
    f"scored = sc.score_all_stocks(raw); "
    f"s0 = scored[0]; "
    f"print('auctionSignal:', s0.get('auctionSignal')); "
    f"print('sealRatio:', s0.get('sealRatio')); "
    f"print('factors.warn.label:', s0['factors']['warn']['label']); "
    f"print('factors.warn.value:', s0['factors']['warn']['value']); "
    f"print('probability:', s0['probability'])\""
))

sftp.close()
ssh.close()
print("测试机 方案B(竞价异动综合分) 部署完成 ✓")
