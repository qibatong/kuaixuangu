#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产机部署 - filter semantic flip(昨涨停/ST: 勾=只看这类票) 专用最小化脚本.
只上传与本次改动相关的文件, 不触及 deploy_prod.py 默认会上传的 kpl/fetcher/stats/config.
同时重启 kuaixuan(web) + kx-worker(调度), 双进程保证新过滤逻辑生效.
"""
import os, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
PROD  = ("121.196.230.80", 22)
USER  = os.environ.get("KX_PROD_USER", "root")
DEPLOY = "/opt/kuaixuan"
# 生产机专用 venv: /opt/kuaixuan-venv (与测试机不同, 远程行为检查用)
PROD_VENV = "/opt/kuaixuan-venv"

# 密码: 优先级 CLI 1 > 环境 KX_PROD_PASS
PASS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KX_PROD_PASS")
if not PASS:
    sys.exit("[错误] 用法: python scripts/deploy_prod_filter_semflip.py <root密码>\n"
             "   或: export KX_PROD_PASS=xxx; python scripts/deploy_prod_filter_semflip.py")

# 仅与本次 filter-sem-flip 相关的后端文件
BACKEND_FILES = [
    # ① 竞价/盘中过滤函数: stSuspend/limitUp 条件前加 NOT → 正逻辑(勾=只看保留)
    ("/workspace/backend/app/services/scorer.py",       f"{DEPLOY}/backend/app/services/scorer.py"),
    # ② init_db 幂等迁移 mig_filter_sem_flip_v2: users.filter_prefs + settings.default_filters 取反
    ("/workspace/backend/app/db/database.py",           f"{DEPLOY}/backend/app/db/database.py"),
    # ③ 管理员默认值接口: DEFAULT_FILTERS_DEFAULT 改为 False(不勾=剔除) 与原行为等价
    ("/workspace/backend/app/api/admin.py",             f"{DEPLOY}/backend/app/api/admin.py"),
]
FRONTEND_DIST = "/workspace/frontend/dist"


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
        raise RuntimeError(f"代理 CONNECT 失败: {resp.split(b'\r\n')[0]!r}")
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


def sh(ssh, cmd, label=""):
    print(f"  $ {cmd[:120]}{'…' if len(cmd)>120 else ''}")
    i, o, e = ssh.exec_command(cmd)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    if out:
        for line in out.splitlines(): print(f"    {label}> {line}")
    if err:
        for line in err.splitlines(): print(f"    {label}E {line}")
    return out, err


sock = proxy_socket(*PROD)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(PROD[0], PROD[1], username=USER, password=PASS, sock=sock, timeout=25)
sftp = ssh.open_sftp()

# [1/6] 备份当前 dist (dist_bak 可快速回滚)
print("[1/6] 备份生产 dist → dist_bak + 清空 assets")
sh(ssh, (
    f"rm -rf {DEPLOY}/dist_bak && "
    f"cp -a {DEPLOY}/dist {DEPLOY}/dist_bak 2>/dev/null; "
    f"rm -rf {DEPLOY}/dist/assets && mkdir -p {DEPLOY}/dist/assets"
))

# [2/6] 上传后端 3 文件(只动本次改动文件)
print("[2/6] 上传后端改动文件 (scorer/database/admin, 不含无关 kpl/fetcher/stats/config)")
for local, remote in BACKEND_FILES:
    sftp_mkdirs(sftp, os.path.dirname(remote))
    sftp.put(local, remote)
    size = os.path.getsize(local)
    print(f"  ✓ {remote}  ({size}B)")

# [3/6] 上传前端 dist 递归(index.html + assets)
print("[3/6] 上传前端 dist 递归 ...")
n = 0
for root, dirs, files in os.walk(FRONTEND_DIST):
    rel = os.path.relpath(root, FRONTEND_DIST)
    rdir = f"{DEPLOY}/dist" if rel == "." else f"{DEPLOY}/dist/{rel.replace(os.sep, '/')}"
    sftp_mkdirs(sftp, rdir)
    for f in files:
        lp = os.path.join(root, f)
        rp = f"{rdir}/{f}"
        sftp.put(lp, rp)
        n += 1
print(f"  ✓ {n} 个前端文件已上传")

# [4/6] chmod + 重启 web + worker 双服务
print("[4/6] chmod + 重启 kuaixuan.service + kx-worker.service ...")
out, err = sh(ssh, (
    f"chmod -R a+rX {DEPLOY}/dist && "
    f"systemctl restart kuaixuan kx-worker && sleep 4 && "
    f"echo 'kuaixuan:'$(systemctl is-active kuaixuan) 'worker:'$(systemctl is-active kx-worker)"
))
assert "kuaixuan:active" in out and "worker:active" in out, f"服务非 active! 实际: {out or err}"

# [5/6] 迁移与启动日志检查
print("[5/6] 启动/迁移日志检查 ...")
out, _ = sh(ssh, (
    f"sleep 2; "
    f"tail -80 {DEPLOY}/logs/app.log 2>/dev/null "
    f"| grep -E 'mig_filter_sem_flip_v2|服务启动|数据库就绪|ERROR.*scorer|ERROR.*database' | tail -12"
), "LOG")

# [6/6] 健康检查 + 远端实际行为验证
print("[6/6] 健康检查 + 实际过滤行为验证 ...")
sh(ssh, (
    # 本地反向代理/Nginx 端口
    "curl -sS -o /dev/null -w 'nginx_http=%{http_code}\\n' http://127.0.0.1/ --max-time 5; "
    "curl -sS -o /dev/null -w 'nginx_https=%{http_code}\\n' https://127.0.0.1/ -k --max-time 5; "
    "curl -sS -o /dev/null -w 'kuaixuan_8010=%{http_code}\\n' http://127.0.0.1:8010/ --max-time 5; "
    # 静态标签文本(应无"只看XX")
    "echo '---静态标签---'; "
    "grep -hoE '(只看)?ST/停牌|(只看)?昨涨停|(只看)?昨日涨停' "
    "  {DEPLOY}/dist/index.html {DEPLOY}/dist/assets/*.js 2>/dev/null | sort -u; ".replace("DEPLOY", DEPLOY)
))

# 直接在生产用 apply_filters 验证(概念含昨日涨停/昨日连板时的行为)
BEHAVIOR = r'''
import sys; sys.path.insert(0, DEPLOY_DIR+'/backend')
from app.services.scorer import apply_filters
def mk(code,name,concept,st=False):
    return dict(
      code=code,name=('ST' if st else '')+name,concept=concept,
      probability=80,confidence=80,score=80,circulationMV=200,price=10,
      bidChange=3,bidAmt=5000,
      _raw=dict(f103=concept,f14=('ST' if st else '')+name,f4=3.1,f5=10000))
pool=[mk('000001','甲','昨日涨停、光伏'),mk('000002','乙','光伏'),
      mk('000003','丙','昨日连板'),mk('000004','丁','',st=True)]
base=dict(bidGt=99,probLt=0,confLt=0,floatMvFloor=0,floatMvGt=999999,
          priceGt=9999,bidAmtFloor=0,markets=['hs','cyb','kcb'])
def run(label, **kw):
    f=dict(base, **kw)
    print(label, [x['name'] for x in apply_filters(pool, f)])
run('[limitUp=True 勾=只看昨涨停] 应保留=甲+丙+乙(乙非昨涨停但非昨涨停也不剔除→只看是正向选结果; 注: limitUp=False 才剔除昨涨停) ', limitUp=True,  stSuspend=False)
run('[limitUp=False不勾=剔除昨涨停] 应保留=乙(甲丙昨涨停被剔除)                    ', limitUp=False, stSuspend=False)
run('[stSuspend=True  勾=只看ST/停牌] 应保留=ST丁+乙                                ', limitUp=False, stSuspend=True)
run('[stSuspend=False 不勾=剔除ST/停牌] 应保留=乙(ST丁被剔除)                        ', limitUp=False, stSuspend=False)
'''.replace('DEPLOY_DIR', DEPLOY)
sh(ssh, (
    f"{PROD_VENV}/bin/python -c \"{BEHAVIOR}\""
), "BEHAVIOR")

sftp.close()
ssh.close()
print("\n生产部署完成 ✓ （如需回滚：cp -a dist_bak dist; systemctl restart kuaixuan kx-worker）")
