#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直连 SSH/SFTP 工具(不经 127.0.0.1:18080 代理)。

用法:
  python _kx_direct.py run  <host> <pass> <cmd>
  python _kx_direct.py put  <host> <pass> <local> <remote>
  python _kx_direct.py putm <host> <pass> <local_root> <remote_root> <rel1> <rel2> ...
       (批量上传, 保持相对路径; rel 用 / 分隔)
  python _kx_direct.py get  <host> <pass> <remote> <local>
       (下载单文件; 目录请先在远端 tar 打包再 get)
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
    _chmod_uploaded(sftp, remote)
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
        _chmod_uploaded(sftp, rp)
        print("up %-60s %8dB" % (rel, os.path.getsize(lp)))
    sftp.close(); c.close()

def _local_md5(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _chmod_uploaded(sftp, rpath, is_dir=False):
    """🔴 上传后必须显式给权限 —— 2026-09-30 生产事故根因。

    SFTP `put` 新建的文件权限由**远端 sshd 的 umask** 决定: 生产机(umask 027)产出
    `-rw-r----- root:root` ⇒ nginx(worker 用户 nginx) **读不到** ⇒ 入口 chunk 403、
    整站白屏(真实用户已撞上, 见 nginx error.log `Permission denied`)。
    而覆盖已有文件时权限不变 ⇒ 只有**新文件**中招(42 个新块全部 403)。
    约定: 目录 755 / 文件 644(与 AGENTS.md 里 tar 打包阶段写死权限的约定一致)。
    """
    try:
        sftp.chmod(rpath, 0o755 if is_dir else 0o644)
    except IOError:
        pass


def do_putdir(host, passwd, lroot, rroot):
    """递归上传目录(保持子目录结构)。**按内容 md5 比对**: 远端已有同名同内容 ⇒ 跳过。

    ⚠️ 不要用"同名 + 大小相同"当判据 —— `index.html` 换 chunk hash 时字节数常常完全一样
       (2026-09-30 实测踩坑: index-DCd1Ls_P.js → index-monJoibN.js 长度相同, 结果 index.html
        没被更新, 站点仍在跑旧前端)。
    """
    c = connect(host, passwd)
    _, out, _ = c.exec_command("cd %s && find . -type f -exec md5sum {} + 2>/dev/null" % rroot)
    remote = {}
    for line in out.read().decode(errors="replace").splitlines():
        fld = line.split(None, 1)
        if len(fld) == 2:
            remote[fld[1].strip().lstrip("./")] = fld[0]
    print("远端清单: %d 个文件" % len(remote))
    sftp = c.open_sftp()
    ups, skips, nbytes, ups_list = 0, 0, 0, []
    _mkdirs(sftp, rroot)
    for root, dirs, files in os.walk(lroot):
        rel = os.path.relpath(root, lroot)
        relu = "" if rel in (".", "") else rel.replace(os.sep, "/")
        rdir = rroot if not relu else posixpath.join(rroot, relu)
        _mkdirs(sftp, rdir)
        for d in sorted(dirs):
            _mkdirs(sftp, posixpath.join(rdir, d))
        for f in sorted(files):
            lp = os.path.join(root, f)
            relp = posixpath.join(relu, f) if relu else f
            lh = _local_md5(lp)
            if remote.get(relp) == lh:
                skips += 1
                continue
            rp = posixpath.join(rdir, f)
            sftp.put(lp, rp)
            _chmod_uploaded(sftp, rp)                  # 🔴 必须: 否则新文件 640 ⇒ nginx 403
            ups += 1
            nbytes += os.path.getsize(lp)
            ups_list.append(relp)
    print("putdir 完成: 上传 %d 个 (%.2f MB), 内容一致跳过 %d 个 -> %s"
          % (ups, nbytes / 1048576.0, skips, rroot))
    for p in ups_list[:40]:
        print("   up %s" % p)
    if len(ups_list) > 40:
        print("   ... 其余 %d 个" % (len(ups_list) - 40))
    sftp.close(); c.close()


def do_get(host, passwd, remote, local):
    c = connect(host, passwd)
    sftp = c.open_sftp()
    ldir = os.path.dirname(local)
    if ldir and not os.path.isdir(ldir):
        os.makedirs(ldir)
    sftp.get(remote, local)
    print("down %s -> %s (%dB)" % (remote, local, os.path.getsize(local)))
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
    elif mode == "putdir":
        do_putdir(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif mode == "get":
        do_get(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        print(__doc__); sys.exit(64)
