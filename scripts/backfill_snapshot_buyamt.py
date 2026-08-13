#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回填 snapshot_bid 的委买额/流通市值/名称: 拉全市场当前行情(f5/f6/f14)
注意: 只能补"当前"时刻的全市场委买额, 不同时点的真实历史委买额无法拉取
今日 9_15/9_20/9_25 三个时点的 buy_amt 都用当前值(明日新采集会覆盖)
用法: SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt /usr/local/python311/bin/python3 /opt/kuaixuan/scripts/backfill_snapshot_buyamt.py
"""
import sys
import time

sys.path.insert(0, "/opt/kuaixuan")
from backend.app.db import database
from backend.app.services import fetcher, scorer


def fetch_all_market():
    """全市场 code → {name, bid_buy_amt(万元), float_mv(元)}"""
    nm = {}
    for market in ("hs", "cyb", "kcb"):
        try:
            for s in fetcher.fetch_eastmoney_all(scorer.market_fs([market])):
                code = s.get("f12")
                if code:
                    nm[code] = {
                        "name": str(s.get("f14") or ""),
                        "bid_buy_amt": scorer.parse_float(s.get("f5")) / 10000,   # 元→万元
                        "float_mv": scorer.parse_float(s.get("f6")),                # 元
                    }
        except Exception as e:
            print("[warn] %s 拉取失败: %s" % (market, e))
    return nm


def main():
    today = time.strftime("%Y-%m-%d")
    print("== 拉取全市场(委买额+流通市值+名称) ==")
    nm = fetch_all_market()
    print("名称映射数量: %d" % len(nm))
    if not nm:
        print("[fail] 无数据, 终止")
        sys.exit(1)

    conn = database.get_conn()
    n_total = 0
    for code, v in nm.items():
        cur = conn.execute(
            "UPDATE snapshot_bid SET name=?, bid_buy_amt=?, float_mv=? WHERE date=? AND code=?",
            (v["name"], v["bid_buy_amt"], v["float_mv"], today, code))
        n_total += cur.rowcount
    conn.commit()
    conn.close()
    print("回填完成: %d 行" % n_total)


if __name__ == "__main__":
    main()