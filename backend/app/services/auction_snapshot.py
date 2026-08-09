# -*- coding: utf-8 -*-
"""
9:20 竞价时点快照服务: 定时抓取全市场竞价数据 + 存储/查询
==========================================================
- 工作日 9:20 前后, 后台线程自动抓取全市场行情(沪深/创业/科创) → snapshot_920 表
- 9:25 lock 时读取该快照, 与当前竞价对比计算"涨幅加速度"(最后5分钟资金抢筹信号)
"""
import threading
import time

from ..core import config, logger
from ..db import database
from . import fetcher, scorer

log = logger.get_logger(__name__)

_sched_lock = threading.Lock()
_sched_done_date = ""       # 已抓取的日期(防同一天重复)


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def snapshot_all():
    """抓取当前全市场快照并落库, 返回入库数量; 失败返回 0"""
    date = _bj_date()
    raw_all = {}
    for m in ("hs", "cyb", "kcb"):
        try:
            raw = fetcher.fetch_eastmoney(scorer.market_fs([m]))
        except Exception as e:
            log.warning("9:20快照拉取失败 market=%s err=%s", m, e)
            continue
        for s in raw:
            code = s.get("f12")
            if code:
                raw_all[code] = {
                    "bid_change": scorer.get_bid_change(s),
                    "bid_amt": scorer.get_bid_amt(s),
                }
    if not raw_all:
        return 0
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_920 (date, code, bid_change, bid_amt, ts) VALUES (?,?,?,?,?)",
            [(date, code, v["bid_change"], v["bid_amt"], int(time.time()))
             for code, v in raw_all.items()])
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("9:20快照落库失败 err=%s", e)
        return 0
    log.info("9:20快照已存 date=%s 数量%d", date, len(raw_all))
    return len(raw_all)


def load_snapshot(date=None):
    """读取某日 9:20 快照, 返回 {code: {bid_change, bid_amt}}; 无数据返回 {}"""
    date = date or _bj_date()
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt FROM snapshot_920 WHERE date=?",
            (date,)).fetchall()
        conn.close()
    except Exception:
        return {}
    return {r[0]: {"bid_change": r[1], "bid_amt": r[2]} for r in rows}


def _scheduler_loop():
    """后台调度: 工作日 9:20:00-9:20:30 之间抓取一次, 每 10 秒检查"""
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            hm = g.tm_hour * 60 + g.tm_min
            if g.tm_wday < 5 and 9 * 60 + 20 <= hm <= 9 * 60 + 30 and _sched_done_date != date:
                with _sched_lock:
                    if _sched_done_date != date:
                        if snapshot_all():
                            _sched_done_date = date
        except Exception as e:
            log.error("9:20快照调度异常 err=%s", e)
        time.sleep(10)


def start_scheduler():
    """main.py startup 调用: 启动后台抓取线程(单 worker 下唯一实例)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    log.info("9:20快照调度已启动")
