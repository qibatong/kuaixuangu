#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三端漂移核对: 本机 vs 远端 的 backend/app 逐文件 **行尾归一化 md5**。

为什么必须行尾归一化: 本机是 CRLF、远端是 LF, 直接比 md5 会 100% 误报。
归一化后仍不同 = **真差异**, 就是待部署清单。

用法:
  python _kx_prod_drift.py <host> <pass> [subdir]
      subdir 默认 backend/app

输出:
  DIFF  本机与远端内容不同(待部署)
  LOCAL_ONLY  仅本机有(远端缺, 需新建)
  REMOTE_ONLY 仅远端有(孤儿, 需人工确认)
  SAME  一致
"""
import sys, os, hashlib, posixpath
import paramiko

REMOTE_PROBE = r'''
import os, hashlib, sys
root = sys.argv[1]
out = []
for dp, dns, fns in os.walk(root):
    if "__pycache__" in dp:
        continue
    for fn in fns:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, root).replace("\\", "/")
        try:
            b = open(p, "rb").read()
        except Exception:
            continue
        b = b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        out.append("%s %s" % (hashlib.md5(b).hexdigest(), rel))
print("\n".join(sorted(out)))
'''


def connect(host, passwd):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, username="root", password=passwd, timeout=30)
    return c


def local_map(root):
    m = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            b = open(p, "rb").read()
            b = b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            m[rel] = hashlib.md5(b).hexdigest()
    return m


def remote_map(host, passwd, root):
    c = connect(host, passwd)
    sftp = c.open_sftp()
    with sftp.open("/root/_kx_drift_probe.py", "wb") as f:
        f.write(REMOTE_PROBE.encode())
    sftp.close()
    cmd = "python3 /root/_kx_drift_probe.py %s" % root
    _, out, err = c.exec_command(cmd)
    txt = out.read().decode(errors="replace")
    e = err.read().decode(errors="replace")
    c.close()
    if e.strip():
        print("=== REMOTE STDERR ===")
        print(e[:2000])
    m = {}
    for line in txt.splitlines():
        line = line.strip()
        if not line or " " not in line:
            continue
        h, rel = line.split(" ", 1)
        m[rel.strip()] = h.strip()
    return m


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(64)
    host, passwd = sys.argv[1], sys.argv[2]
    sub = sys.argv[3] if len(sys.argv) > 3 else "backend/app"
    rroot = "/opt/kuaixuan/" + sub.replace("\\", "/")

    L = local_map(sub)
    R = remote_map(host, passwd, rroot)

    diff, local_only, remote_only, same = [], [], [], []
    for rel in sorted(set(L) | set(R)):
        if rel not in R:
            local_only.append(rel)
        elif rel not in L:
            remote_only.append(rel)
        elif L[rel] != R[rel]:
            diff.append(rel)
        else:
            same.append(rel)

    print("本地 %d 文件 | 远端 %d 文件 | 一致 %d" % (len(L), len(R), len(same)))
    print()
    print("### DIFF %d (内容不同, 待部署)" % len(diff))
    for r in diff:
        print("  " + r)
    print()
    print("### LOCAL_ONLY %d (远端缺, 需新建)" % len(local_only))
    for r in local_only:
        print("  " + r)
    print()
    print("### REMOTE_ONLY %d (孤儿, 需人工确认)" % len(remote_only))
    for r in remote_only:
        print("  " + r)
    print()
    print("DEPLOY_LIST=%s" % ",".join(sorted(diff + local_only)))


if __name__ == "__main__":
    main()
