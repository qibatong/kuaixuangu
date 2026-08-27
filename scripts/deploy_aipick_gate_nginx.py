#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产机 Nginx 更新: 关闭 /aipick/ 匿名静态暴露, 改为 404 (统一走后端 /api/aipick/* 鉴权)
经本机 127.0.0.1:18080 HTTP CONNECT 代理转发 SSH 到生产机。
先备份 conf, 正则替换, nginx -t 校验通过才 reload, 并验证 /aipick/latest.html 不再 200。
"""
import os, re, socket, sys
import paramiko

PROXY = ("127.0.0.1", 18080)
PROD = ("121.196.230.80", 22)
USER = os.environ.get("KX_PROD_USER", "root")
PASS = os.environ.get("KX_PROD_PASS")
if not PASS:
    sys.exit("[错误] 未设置 KX_PROD_PASS 环境变量")
CONF = "/etc/nginx/conf.d/kuaixuan.conf"

OLD_BLOCK = re.compile(
    r"location\s+/aipick/\s*\{\s*alias\s+[^;]+;\s*autoindex\s+on;\s*"
    r"autoindex_exact_size\s+off;\s*autoindex_localtime\s+on;\s*"
    r"add_header\s+Cache-Control\s+\"[^\"]*\";?\s*\}"
)

NEW_BLOCK = (
    "    # AI 竞价预测报告不再匿名静态暴露, 统一经后端 /api/aipick/* 鉴权(App 内查看, 仅付费/VIP)\n"
    "    location /aipick/ {\n"
    "        return 404;\n"
    "    }"
)


def proxy_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(15)
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


def run(ssh, cmd):
    _, so, se = ssh.exec_command(cmd, timeout=60)
    return (so.read().decode() or "") + (se.read().decode() or "")


def main():
    sock = proxy_socket(*PROD)
    ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(PROD[0], PROD[1], username=USER, password=PASS, sock=sock, timeout=20)
    try:
        old = run(ssh, f"cat {CONF}")
        print("[1/4] 备份原 conf ...")
        print(run(ssh, f"cp -a {CONF} {CONF}.bak.aipickgate && echo backed_up").strip())
        if not OLD_BLOCK.search(old):
            print("[中断] 未找到 /aipick/ 静态 alias 块, 可能已改过, 请人工检查:")
            print(old)
            return 1
        new = OLD_BLOCK.sub(NEW_BLOCK, old)
        # 用 base64 写入避免引号/转义问题
        import base64
        b64 = base64.b64encode(new.encode("utf-8")).decode()
        print("[2/4] 写入新 conf ...")
        print(run(ssh, f"echo {b64} | base64 -d > {CONF} && echo written").strip())
        print("[3/4] nginx -t ...")
        t = run(ssh, "nginx -t")
        print(t.strip())
        if "successful" not in t and "syntax is ok" not in t:
            print("[回滚] nginx -t 失败, 恢复备份 ...")
            print(run(ssh, f"cp -a {CONF}.bak.aipickgate {CONF} && nginx -t").strip())
            return 1
        print("[4/4] reload nginx + 验证 ...")
        print(run(ssh, "nginx -s reload && sleep 1 && echo reloaded").strip())
        code = run(ssh, "curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/aipick/latest.html --max-time 5").strip()
        print("本机 curl /aipick/latest.html ->", code)
        code2 = run(ssh, "curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/aipick/aipick_nonexist --max-time 5").strip()
        print("本机 curl /aipick/未登录任意文件 ->", code2)
        print("Nginx 更新完成(外部响应见 curl; 期望 404 而非 200/403 文件列表)")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()