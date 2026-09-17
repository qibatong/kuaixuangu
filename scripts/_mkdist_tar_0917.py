# -*- coding: utf-8 -*-
"""打包 frontend/dist 为部署 tar(权限归一 dir 755 / file 644)。

为什么不用 shell tar:
  Git Bash 下 `tar -f C:/...` 会把盘符当远程主机; 且 Windows 侧文件 mode 带 666,
  解压到生产后 nginx 因"组/其他可写"拒绝服务 → 这里在打包阶段就写死 mode。

产物: scripts/deploy_tmp/<name>  (tar 内首层为 dist/)

用法: python scripts/_mkdist_tar_0917.py [输出文件名, 默认 _dist_pg_v2.tar.gz]
"""
import os
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "dist")
NAME = (sys.argv[1] if len(sys.argv) > 1 else "_dist_pg_v2.tar.gz")
OUT = os.path.join(ROOT, "scripts", "deploy_tmp", NAME)


def main():
    if not os.path.isdir(SRC):
        print("MISSING: %s" % SRC)
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    nf = nd = 0
    with tarfile.open(OUT, "w:gz") as tf:
        for dirpath, dirnames, filenames in os.walk(SRC):
            dirnames.sort()
            rel = os.path.relpath(dirpath, SRC)            # "." for root
            arc = "dist" if rel == "." else "dist/" + rel.replace(os.sep, "/")
            for d in dirnames:
                ti = tarfile.TarInfo(arc + "/" + d)
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                ti.mtime = 1700000000
                tf.addfile(ti)
                nd += 1
            for f in sorted(filenames):
                p = os.path.join(dirpath, f)
                ti = tf.gettarinfo(p, arcname=arc + "/" + f)
                ti.mode = 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = "root"
                ti.mtime = 1700000000
                with open(p, "rb") as fh:
                    tf.addfile(ti, fh)
                nf += 1
    print("OK  %s" % OUT)
    print("    files=%d dirs=%d size=%d" % (nf, nd, os.path.getsize(OUT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
