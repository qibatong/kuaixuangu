#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传本地文件到远端, 并强制转成 LF 行尾(远端执行铁律)。

本项目已知坑: 本地 Windows 文件常是 CRLF, 直接传到 Linux 会让 bash 报
    set: -: invalid option   /   syntax error near $'in\\r'
所以**所有**要远端执行的 .py/.sh 都必须过这个脚本。

用法:
  python _kx_put_lf.py <host> <pass> <local> <remote>
  python _kx_put_lf.py 121.196.230.80 'pw' scripts/foo.py /root/foo.py

实现要点(血的教训):
  - **不要** `open(p,'wb').write(open(p,'rb').read().replace(...))` —— `open(p,'wb')`
    会先把本地文件**截断成 0**, 再读就读到空, 文件直接没了(2026-09-22 踩过)。
  - 正确顺序: 先 **读全** 到内存 → 转换 → 再写远端(本地文件根本不动)。
"""
import sys, os, io, posixpath
import paramiko


def connect(host, passwd):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, username="root", password=passwd, timeout=30)
    return c


def to_lf(data: bytes) -> bytes:
    """CRLF / CR -> LF"""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def put_lf(host, passwd, local, remote, verbose=True):
    # 1) 先读全(绝不先打开写)
    with open(local, "rb") as f:
        raw = f.read()
    conv = to_lf(raw)
    changed = (conv != raw)

    # 2) 写远端
    c = connect(host, passwd)
    sftp = c.open_sftp()
    rdir = posixpath.dirname(remote)
    if rdir:
        parts = rdir.split("/")
        cur = ""
        for p in parts:
            if not p:
                continue
            cur += "/" + p
            try:
                sftp.stat(cur)
            except IOError:
                sftp.mkdir(cur)
    with sftp.open(remote, "wb") as rf:
        rf.write(conv)
    sftp.close()
    c.close()
    if verbose:
        print("up %-46s %7dB  CRLF->LF=%s" % (remote, len(conv), changed))
    return len(conv)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(64)
    put_lf(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
