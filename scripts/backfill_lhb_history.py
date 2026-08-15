# -*- coding: utf-8 -*-
"""人气榜/龙虎榜历史回补脚本
- 龙虎榜: 接口支持 Time 参数, 回补最近 N 个交易日到 lhb_history
- 人气榜: 接口不支持历史(GetHotPHB Time/Date 无效), 只能落当前;
  历史从部署之日起由 15:30 调度器积累
用法(测试机):
  /opt/bid-venv/bin/python /tmp/backfill_lhb_history.py [天数=7]
"""
import datetime
import json
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")
from app.db import database
from app.services import kpl


def _iter_recent_biz_dates(days):
    today = datetime.date.today()
    out = []
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    dates = _iter_recent_biz_dates(days)
    print("候选日期: %s" % dates, flush=True)
    conn = database.get_conn()
    n_ok, n_empty = 0, 0
    for d in dates:
        try:
            lst = kpl.fetch_lhb(d) or []
        except Exception as e:
            print("%s err=%s" % (d, e), flush=True)
            n_empty += 1
            continue
        if not lst:
            print("%s 龙虎榜无数据" % d, flush=True)
            n_empty += 1
            continue
        conn.execute(
            "INSERT OR REPLACE INTO lhb_history (date, list, ts) VALUES (?,?,?)",
            (d, json.dumps(lst, ensure_ascii=False), int(time.time())))
        n_ok += 1
        print("%s 已落 %d 条" % (d, len(lst)), flush=True)
    conn.commit()
    conn.close()
    print("完成: 落库 %d 天, 跳过 %d 天" % (n_ok, n_empty), flush=True)


if __name__ == "__main__":
    main()