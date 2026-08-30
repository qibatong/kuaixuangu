# -*- coding: utf-8 -*-
"""测试机 nginx 修复: 重新生成 /aipick/ 鉴权配置 (修复 $ 变量被 bash 展开问题)"""
import io

p = "/etc/nginx/conf.d/kuaixuan.conf"
with io.open(p, "r", encoding="utf-8") as f:
    src = f.read()

# 先移除上一次插入的错误块
import re
pat = re.compile(r"    # AI竞价预测报告静态托管.*?location = /__aipick_auth \{.*?\n    \}\n", re.S)
src2 = pat.sub("", src)
if src2 == src and "aipick" in src:
    # 兜底: 手动切
    start = src.find("    # AI竞价预测报告静态托管")
    end = src.find("    # 后端 API")
    if start != -1 and end != -1 and end > start:
        src2 = src[:start] + src[end:]
if "aipick" in src2:
    print("ERROR: 仍有 aipick 残留")
    raise SystemExit(1)

block = """    # AI竞价预测报告静态托管(2026-08-30 主人需求: 放开静态访问但仅限 VIP/付费/管理员)
    location /aipick/ {
        auth_request /__aipick_auth;
        alias /opt/kuaixuan/aipick/output/;
        index latest.html;
        try_files $uri $uri/ /aipick/latest.html;
    }
    location = /__aipick_auth {
        internal;
        proxy_pass http://127.0.0.1:8010/api/aipick/auth-check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI $request_uri;
        proxy_set_header X-Original-Authorization $http_authorization;
        proxy_set_header Host $host;
    }
"""

marker = "    # 后端 API"
if marker not in src2:
    marker = "    location /api/ {"
assert marker in src2, "找不到锚点"

with io.open(p + ".bak2", "w", encoding="utf-8") as f:
    f.write(src2)
src2 = src2.replace(marker, block + marker, 1)
with io.open(p, "w", encoding="utf-8") as f:
    f.write(src2)
print("REWRITE_OK")
