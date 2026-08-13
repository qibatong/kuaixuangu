# -*- coding: utf-8 -*-
"""
竞价多时点快照归档服务: 9:15 / 9:20 / 9:25 全市场快照每日自动采集
================================================================
- 工作日 9:15 / 9:20 / 9:25 三个时点, 后台线程自动抓取全市场行情 → snapshot_bid 表
- 每日积累 → 形成"历史多时点回放库"(短线侠式核心壁垒)
- 9:25 lock 时读取 9:20 快照, 计算涨幅加速度
"""
import threading
import time

from ..core import logger
from ..db import database
from . import fetcher, scorer

log = logger.get_logger(__name__)

# 时点 -> (开始分钟, 结束分钟) 北京时间(每 10 秒轮询, 窗口 1 分钟防漏)
# 9_24 用于"最后一分钟抢筹"计算(对标短线侠"最后1秒"的近似)
TIME_POINTS = {
    "9_15": (9 * 60 + 15, 9 * 60 + 16),
    "9_20": (9 * 60 + 20, 9 * 60 + 21),
    "9_24": (9 * 60 + 24, 9 * 60 + 25),
    "9_25": (9 * 60 + 25, 9 * 60 + 26),
}
DEFAULT_POINT = "9_20"     # 加速度计算使用的时点

_sched_lock = threading.Lock()
_sched_done = set()        # {(date, time_point)} 已抓取, 防重复
_sched_checked = set()     # {date} 已做采集盘点(9:31 后一次)


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _fetch_market_map():
    """抓取当前全市场(沪深/创业/科创)快照, 返回 {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv}}
    过滤异常涨幅(±30% 外, A股涨跌停上限20%/新股44%, 非交易时段字段可能异常)"""
    raw_all = {}
    for m in ("hs", "cyb", "kcb"):
        try:
            raw = fetcher.fetch_eastmoney(scorer.market_fs([m]))
        except Exception as e:
            log.warning("快照拉取失败 market=%s err=%s", m, e)
            continue
        for s in raw:
            code = s.get("f12")
            if not code:
                continue
            bc = scorer.get_bid_change(s)
            if bc < -30 or bc > 30:    # 明显异常数据(非交易时段字段污染)
                continue
            raw_all[code] = {
                "bid_change": bc,
                "bid_amt": scorer.get_bid_amt(s),
                "name": str(s.get("f14") or ""),          # 名称
                "bid_buy_amt": scorer.parse_float(s.get("f5")) / 10000,   # 委买额(万元)
                "float_mv": scorer.parse_float(s.get("f6")),              # 流通市值(元)
            }
    return raw_all


def snapshot_at(time_point):
    """抓取并归档某时点全市场快照, 返回入库数量; 失败返回 0"""
    if time_point not in TIME_POINTS:
        return 0
    date = _bj_date()
    raw_all = _fetch_market_map()
    if not raw_all:
        return 0
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(date, time_point, code, v["bid_change"], v["bid_amt"], v.get("name", ""),
              v.get("bid_buy_amt", 0), v.get("float_mv", 0), int(time.time()))
             for code, v in raw_all.items()])
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("快照落库失败 time=%s err=%s", time_point, e)
        return 0
    log.info("快照已存 date=%s time=%s 数量%d", date, time_point, len(raw_all))
    return len(raw_all)


def load_snapshot(date=None, time_point=DEFAULT_POINT):
    """读取某日某时点快照, 返回 {code: {bid_change, bid_amt}}; 无数据返回 {}"""
    date = date or _bj_date()
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, time_point)).fetchall()
        conn.close()
    except Exception:
        return {}
    return {r[0]: {"bid_change": r[1], "bid_amt": r[2]} for r in rows}


def query_snapshot(date, time_point, limit=50):
    """历史回放: 某日某时点全市场快照(按竞价涨幅降序, 带名称)"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt, name, ts FROM snapshot_bid "
            "WHERE date=? AND time_point=? ORDER BY bid_change DESC LIMIT ?",
            (date, time_point, min(limit, 500))).fetchall()
        conn.close()
    except Exception:
        return []
    return [{"code": r[0], "bid_change": r[1], "bid_amt": r[2], "name": r[3] or ""} for r in rows]


def _scheduler_loop():
    """后台调度: 工作日按时点窗口抓取一次, 每 10 秒轮询; 9:31 后盘点当日采集情况"""
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            hm = g.tm_hour * 60 + g.tm_min
            for tp, (start, end) in TIME_POINTS.items():
                key = (date, tp)
                if g.tm_wday < 5 and start <= hm <= end and key not in _sched_done:
                    with _sched_lock:
                        if key not in _sched_done:
                            if snapshot_at(tp):
                                _sched_done.add(key)
            # 9:31-9:35 盘点当日采集: 缺失时点告警(排查关键, 数据过了点无法补)
            if g.tm_wday < 5 and 9 * 60 + 31 <= hm <= 9 * 60 + 35 and date not in _sched_checked:
                missing = [tp for tp in TIME_POINTS if (date, tp) not in _sched_done]
                if missing:
                    log.warning("今日快照采集缺失时点: %s (date=%s), 相关功能(加速度/回放)会缺数据",
                                ",".join(missing), date)
                else:
                    log.info("今日快照采集完整: %s (date=%s)", ",".join(TIME_POINTS), date)
                _sched_checked.add(date)
        except Exception as e:
            log.error("快照调度异常 err=%s", e)
        time.sleep(10)


def start_scheduler():
    """main.py startup 调用: 启动后台抓取线程(单 worker 下唯一实例)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    log.info("竞价多时点快照调度已启动(9:15/9:20/9:25)")
