#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成待部署暂存区: 按线上行尾归一化 + 计算 md5。

输出: scripts/deploy_tmp/_kx_be/<rel>  (LF/CRLF 已对齐)
      scripts/deploy_tmp/_kx_files.txt  (rel:md5 空格分隔)
"""
import hashlib, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend")
OUT = os.path.join(ROOT, "scripts", "deploy_tmp", "_kx_be")

# rel -> 目标行尾 ("\n" / "\r\n")
SPEC = {
    "app/db/database.py": "\r\n",
    "app/services/auction_snapshot.py": "\n",
    "app/services/bid_strength.py": "\r\n",
    "app/services/meoz_client.py": "\n",
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
    for rel, eol in SPEC.items():
        src = os.path.join(BE, rel.replace("/", os.sep))
        with open(src, "rb") as f:
            raw = f.read()
        out = normalize(raw, eol)
        dst = os.path.join(OUT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(out)
        md5 = hashlib.md5(out).hexdigest()
        items.append("%s:%s" % (rel, md5))
        tag = "CRLF" if eol == "\r\n" else "LF"
        print("%-45s %s %s" % (rel, md5, tag))
    with open(os.path.join(ROOT, "scripts", "deploy_tmp", "_kx_files.txt"), "w") as f:
        f.write(" ".join(items))
    print()
    print("KX_FILES 已写入 scripts/deploy_tmp/_kx_files.txt (%d 个文件)" % len(items))

main()
