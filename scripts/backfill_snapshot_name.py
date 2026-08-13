#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回填 snapshot_bid.name: 拉全市场代码→名称映射, 补齐历史快照缺失的名称
用途: 首次加 name 列后一次性回填; 之后新采集自动带名称, 此脚本可重复执行(幂等)
用法(测试机): /usr/local/python311/bin/python3 /opt/kuaixuan/scripts/backfill_snapshot_name.py
"""
import sys

sys.path.insert(0, "/opt/kuaixuan")
from backend.app.db import database
from backend.app.services import fetcher, scorer


def fetch_name_map():
    """全市场代码→名称(东财三板块全量分页)"""
    m = {}
    for market in ("hs", "cyb", "kcb"):
        try:
            # fetch_eastmoney_all: 全市场分页(fid=f12, 每页200, 共~20页), 覆盖所有股票
            for s in fetcher.fetch_eastmoney_all(scorer.market_fs([market])):
                code = s.get("f12")
                name = s.get("f14")
                if code and name:
                    m[code] = str(name)
        except Exception as e:
            print("[warn] 拉取失败 market=%s err=%s" % (market, e))
    return m


def main():
    print("== 拉取全市场名称映射 ==")
    nm = fetch_name_map()
    print("名称映射数量: %d" % len(nm))
    if not nm:
        print("[fail] 无名称数据, 终止")
        sys.exit(1)

    conn = database.get_conn()
    rows = conn.execute(
        "SELECT DISTINCT code FROM snapshot_bid WHERE name IS NULL OR name=''").fetchall()
    print("缺失名称 code 数: %d" % len(rows))
    n = 0
    for (code,) in rows:
        name = nm.get(code)
        if name:
            conn.execute(
                "UPDATE snapshot_bid SET name=? WHERE code=? AND (name IS NULL OR name='')",
                (name, code))
            n += 1
    conn.commit()
    conn.close()
    print("回填完成: %d 只" % n)


if __name__ == "__main__":
    main()
