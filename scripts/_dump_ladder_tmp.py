#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump ladder_history 样本(临时)"""
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
    code = (
        "from app.services import kpl;"
        "import json;"
        "d=kpl.query_ladder_history('2026-08-26');"
        "sub={pid:(lst or [])[:3] for pid,lst in d.items()};"
        "print(json.dumps(sub,ensure_ascii=False,default=str))"
    )
    cmd = f"cd /opt/kuaixuan/backend && /opt/kuaixuan-venv/bin/python -c \"{code}\" 2>&1"
    out, err = run(ssh, cmd)
    print("=== RAW 样本(每档取3) ===")
    print(out.strip())
    if err.strip():
        print("ERR:", err.strip())
    ssh.close()


if __name__ == "__main__":
    main()