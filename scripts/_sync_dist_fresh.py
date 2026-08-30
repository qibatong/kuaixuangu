#!/usr/bin/env python3
"""轻量级 dist 同步: 仅对比文件名, 差异上传 (不下载文件, 无 tmp)."""
import os, paramiko, posixpath, hashlib

LOCAL_DIST = r"C:/Users/Yanci/WorkBuddy/2026-08-14-20-38-01/kuaixuan/frontend/dist"
REMOTE_DIST = "/opt/kuaixuan/dist"

SERVERS = [
    ("测试机", "47.99.153.123", "Admin@123."),
    ("生产机", "121.196.230.80", "Xqhty@84313313"),
]


def hash_file(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_remote(sftp, base):
    """返回远端 base 下所有相对路径集合 (空目录也包含)."""
    out = set()
    stack = [base]
    while stack:
        d = stack.pop()
        for attr in sftp.listdir_attr(d):
            p = f"{d}/{attr.filename}"
            if attr.st_mode & 0o170000 == 0o040000:
                stack.append(p)
            elif attr.st_mode & 0o170000 == 0o100000:
                rel = posixpath.relpath(p, base)
                out.add(rel)
    return out


def ensure_remote_dir(sftp, path):
    parts = path.split("/")
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}" if cur else p
        try:
            sftp.stat(cur)
        except IOError:
            try: sftp.mkdir(cur)
            except: pass


def walk_local(base):
    out = set()
    for root, _, files in os.walk(base):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, base).replace("\\", "/")
            out.add(rel)
    return out


def sync(ssh, sftp, name):
    local_set = walk_local(LOCAL_DIST)
    remote_set = walk_remote(sftp, REMOTE_DIST)
    print(f"[{name}] 本地 {len(local_set)} 文件 / 远端 {len(remote_set)} 文件")

    # 本地新增
    new_files = local_set - remote_set
    print(f"[{name}] 需新增上传: {len(new_files)}")
    n_ok = 0
    for rel in new_files:
        local_path = os.path.join(LOCAL_DIST, rel).replace("\\", "/")
        remote_path = f"{REMOTE_DIST}/{rel}"
        try:
            ensure_remote_dir(sftp, posixpath.dirname(remote_path))
            sftp.put(local_path, remote_path)
            n_ok += 1
        except Exception as ex:
            print(f"  [fail] {rel}: {ex}")
    print(f"[{name}] 新增上传: {n_ok}/{len(new_files)}")

    # 强制覆盖无 hash 的入口文件 (index.html 永远同名但内容会变)
    n_overwrite = 0
    for rel in ("index.html",):
        local_path = os.path.join(LOCAL_DIST, rel).replace("\\", "/")
        if not os.path.exists(local_path):
            continue
        remote_path = f"{REMOTE_DIST}/{rel}"
        try:
            sftp.put(local_path, remote_path)
            n_overwrite += 1
            print(f"[{name}] 强制覆盖: {rel}")
        except Exception as ex:
            print(f"  [fail-ow] {rel}: {ex}")

    # 清理孤儿 chunk: 远端 assets/ 有、本地没有 = 旧版本残留 (Vite hash 文件名, 新版不再引用)
    # 仅清理 assets 目录内文件, 不碰 index.html/favicon/logo 等根文件
    orphan = [r for r in remote_set if r.startswith("assets/") and r not in local_set]
    if orphan:
        n_del = 0
        for rel in orphan:
            try:
                sftp.remove(f"{REMOTE_DIST}/{rel}")
                n_del += 1
            except Exception:
                pass
        print(f"[{name}] 清理孤儿 chunk: {n_del}/{len(orphan)}")

    # 关键 chunk hash 校验 (LoginView + index)
    for key in ("LoginView", "index"):
        i, o, e = ssh.exec_command(f"ls {REMOTE_DIST}/assets/{key}-*.js 2>/dev/null | xargs -I{{}} md5sum {{}} 2>/dev/null")
        out = o.read().decode().strip()
        # 拿本地的 key 前缀所有 js
        local_keys = [p for p in local_set if p.startswith(f"assets/{key}-") and p.endswith(".js")]
        if local_keys:
            for lk in local_keys:
                local_md5 = hash_file(os.path.join(LOCAL_DIST, lk).replace("\\", "/"))
                print(f"[{name}] 本地 {lk} MD5={local_md5[:8]}")

    # 强制刷新 index.html mtime (让 nginx 推新 Last-Modified)
    ssh.exec_command(f"touch {REMOTE_DIST}/index.html")
    # 生产机: chmod
    if "生产" in name:
        ssh.exec_command(f"chmod -R a+rX {REMOTE_DIST}")
    print(f"[{name}] ✅ 同步完成")


for name, host, pwd in SERVERS:
    print(f"\n===== {name} {host} =====")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, 22, username="root", password=pwd, timeout=20)
    sftp = ssh.open_sftp()
    try:
        sync(ssh, sftp, name)
    finally:
        sftp.close()
        ssh.close()

print("\n全部完成。")
