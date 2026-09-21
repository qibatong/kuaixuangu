#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成待部署暂存区: 按线上行尾归一化 + 计算 md5。

输出: scripts/deploy_tmp/_kx_be/<rel>  (LF/CRLF 已对齐)
      scripts/deploy_tmp/_kx_files.txt  (rel:md5 空格分隔)

🔴 当前 SPEC 面向【生产机 121.196.230.80】(2026-09-20 全量同步)。
   测试机行尾不同(database/bid_strength=CRLF), 部署测试机前须改回。
"""
import hashlib
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend")
OUT = os.path.join(ROOT, "scripts", "deploy_tmp", "_kx_be")

# 测试机行尾现状(2026-09-21 实测): 见下, 其余一律 LF
CRLF = {
    "app/api/auth.py",
    "app/api/deps.py",
    "app/api/aipick.py",
    "app/api/admin.py",
    "app/core/config.py",
    "app/db/database.py",
    "app/main.py",
    "app/services/users.py",
}


def normalize(data: bytes, eol: str) -> bytes:
    # 先统一成 LF, 再按目标替换
    lf = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if eol == "\r\n":
        return lf.replace(b"\n", b"\r\n")
    return lf


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    items = []
    app_dir = os.path.join(BE, "app")
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, BE).replace(os.sep, "/")
            eol = "\r\n" if rel in CRLF else "\n"
            with open(full, "rb") as f:
                raw = f.read()
            out = normalize(raw, eol)
            dst = os.path.join(OUT, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(out)
            md5 = hashlib.md5(out).hexdigest()
            items.append("%s:%s" % (rel, md5))
            tag = "CRLF" if eol == "\r\n" else "LF"
            print("%-48s %s %s" % (rel, md5, tag))
    with open(os.path.join(ROOT, "scripts", "deploy_tmp", "_kx_files.txt"), "w") as f:
        f.write(" ".join(items))
    print()
    print("KX_FILES 已写入 scripts/deploy_tmp/_kx_files.txt (%d 个文件)" % len(items))


if __name__ == "__main__":
    main()
