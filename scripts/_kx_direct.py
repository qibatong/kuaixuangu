#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直连 SSH/SFTP 工具(不经 127.0.0.1:18080 代理)。

用法:
  python _kx_direct.py run  <host> <pass> <cmd>
  python _kx_direct.py put  <host> <pass> <local> <remote>
  python _kx_direct.py putm <host> <pass> <local_root> <remote_root> <rel1> <rel2> ...
       (批量上传, 保持相对路径; rel 用 / 分隔)
  python _kx_direct.py md5  <host> <pass> <remote> ...
"""
import sys, os, posixpath
import paramiko

HOST_KEY = None

def connect(host, passwd):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, username="root", password=passwd, timeout=30)
    return c

def do_run(host, passwd, cmd):
    c = connect(host, passwd)
    _, out, err = c.exec_command(cmd, get_pty=False)
    o = out.read().decode(errors="replace")
    e = err.read().decode(errors="replace")
    print(o, end="")
    if e:
        print("=== STDERR ===")
        print(e, end="")
    c.close()

def _mkdirs(sftp, rdir):
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

def do_put(host, passwd, local, remote):
    c = connect(host, passwd)
    sftp = c.open_sftp()
    _mkdirs(sftp, posixpath.dirname(remote))
    sftp.put(local, remote)
    print("up %s (%dB -> %s)" % (remote, os.path.getsize(local), local))
    sftp.close(); c.close()

def do_putm(host, passwd, lroot, rroot, rels):
    c = connect(host, passwd)
    sftp = c.open_sftp()
    for rel in rels:
        lp = os.path.join(lroot, rel.replace("/", os.sep))
        rp = posixpath.join(rroot, rel)
        _mkdirs(sftp, posixpath.dirname(rp))
        sftp.put(lp, rp)
        print("up %-60s %8dB" % (rel, os.path.getsize(lp)))
    sftp.close(); c.close()

def do_md5(host, passwd, remotes):
    c = connect(host, passwd)
    for r in remotes:
        _, out, _ = c.exec_command("md5sum %s" % r)
        print(out.read().decode(errors="replace").strip())
    c.close()

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "run":
        do_run(sys.argv[2], sys.argv[3], sys.argv[4])
    elif mode == "put":
        do_put(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif mode == "putm":
        do_putm(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6:])
    elif mode == "md5":
        do_md5(sys.argv[2], sys.argv[3], sys.argv[4:])
    else:
        print(__doc__); sys.exit(64)
