#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取今日生成的连板天梯图 + dump ladder_history 原始数据样本(临时)"""
import json
import os
import sys
from deploy_prod import connect

if not os.environ.get("KX_PROD_PASS"):
    sys.exit("no KX_PROD_PASS")


def run(ssh, cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    return stdout.read().decode(), stderr.read().decode()


def main():
    ssh = connect()
    # 1) dump 原始数据样本
    cmd = (
        "cd /opt/kuaixuan/backend && /opt/kuaixuan-venv/bin/python -c '"
        "from app.services import kpl;"
        "d=kpl.query_ladder_history(\"2026-08-26\");"
        "import json;"
        "out={};"
        "for pid,lst in d.items():" 
        "  out[pid]=(lst or [])[:2];"
        "print(json.dumps(out,ensure_ascii=False,default=str))' 2>&1"
    )
    out, err = run(ssh, cmd)
    print("=== RAW 样本(每档取2只, 观察 ztCount/limitTime/ladder) ===")
    print(out.strip())
    if err.strip():
        print("ERR:", err.strip())

    # 2) 下载今日图片
    sftp = ssh.open_sftp()
    local = "/workspace/ladder_2026-08-26.png"
    sftp.get("/opt/kuaixuan/ladder_images/2026-08-26.png", local)
    sftp.close()
    print("=== 已下载图片:", local, os.path.getsize(local), "B ===")
    ssh.close()


if __name__ == "__main__":
    main()