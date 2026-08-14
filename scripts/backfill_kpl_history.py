# -*- coding: utf-8 -*-
"""
开盘啦板块历史回补(最近 3 个交易日)
====================================
开盘啦 doc42 (apiv=w41, apphis host) 实测保留期 = 最近 3 个交易日。
本脚本遍历最近 N 个日历日(取工作日)调用 record_today_top(source='kpl', date=...)
落库到 daily_sector_top(source='kpl')。

用法(测试机):
    /opt/bid-venv/bin/python /tmp/backfill_kpl_history.py [天数=7]
"""
import datetime
import json
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")
from app.core import logger
from app.db import database
from app.services import kpl, sector_rotation

log = logger.get_logger(__name__)


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _iter_recent_biz_dates(days):
    """从今天往前推, 取最多 N 个日历日(过滤周一~周五)"""
    today = datetime.date.today()
    out = []
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        if d.weekday() < 5:   # 0~4 = Mon~Fri
            out.append(d.strftime("%Y-%m-%d"))
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    today = _bj_date()
    dates = _iter_recent_biz_dates(days)
    print("候选日期: %s" % dates, flush=True)

    n_ok, n_empty = 0, 0
    for d in dates:
        try:
            boards = kpl.fetch_board_rank_by_date(d) or []
        except Exception as e:
            print("%s err=%s" % (d, e), flush=True)
            n_empty += 1
            continue
        if not boards:
            print("%s 开盘啦无数据(保留期外)", d, flush=True)
            n_empty += 1
            continue
        boards = boards[:10]
        # 直接落库(保留 doc42 拿到的原始字段, 不重抓)
        payload = []
        for i, b in enumerate(boards, start=1):
            payload.append({
                "rank": i,
                "boardCode": b.get("boardCode") or "",
                "name": b.get("name") or "",
                "strength": float(b.get("strength") or 0),
                "change": float(b.get("change") or 0),
                "amount": float(b.get("amount") or 0),
                "mainNet": float(b.get("mainNet") or 0),
                "volRatio": float(b.get("volRatio") or 0),
                "floatMv": float(b.get("floatMv") or 0),
            })
        try:
            conn = database.get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
                (d, "kpl", json.dumps(payload, ensure_ascii=False), int(time.time())))
            conn.commit()
            conn.close()
            n_ok += 1
            print("%s 已落 %d 个板块 Top1: %s 强度%s" % (d, len(payload), payload[0]["name"], payload[0]["strength"]), flush=True)
        except Exception as e:
            print("%s DB err=%s" % (d, e), flush=True)
            n_empty += 1

    print("完成: 落库 %d 天, 跳过/失败 %d 天 (今天=%s)" % (n_ok, n_empty, today), flush=True)


if __name__ == "__main__":
    main()