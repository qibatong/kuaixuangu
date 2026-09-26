# -*- coding: utf-8 -*-
"""
连板天梯盘后生成服务(后台 worker 执行)
================================================
- 交易日 15:30 执行一次
- 流程: 拉取当日连板梯队全档并落库 ladder_history → 生成当日连板天梯 PNG
- 周末/节假日跳过; 失败静默, 不阻塞主流程
"""
import threading
import time

from ..core import logger
from ..core import trade_calendar as tc
from . import kpl, ladder_image

log = logger.get_logger(__name__)

# 盘后生成时间点(北京时间, 分钟)
GEN_AT = 15 * 60 + 30
# 宽松窗口(分钟), 保证 systemd 拉起稍晚也能命中
WINDOW = 10


def _bj():
    g = time.gmtime(time.time() + 8 * 3600)
    return g, g.tm_hour * 60 + g.tm_min


def _bj_date(g=None):
    g = g or time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def run_daily(force=False):
    """执行一轮盘后天梯生成; force=True 跳过交易日+时间窗检查(手动/测试触发)
    返回 (status, msg)"""
    g, hm = _bj()
    date = _bj_date(g)
    if not force:
        # 2026-09-25 复盘: 原为裸 `g.tm_wday >= 5`，法定假日会基于旧数据重出天梯图
        if not tc.is_trade_day_of(g):
            return "skip", f"周末/节假日 {date} 跳过"
        if abs(hm - GEN_AT) > WINDOW:
            return "skip", f"非盘后时间窗 {g.tm_hour:02d}:{g.tm_min:02d} 跳过"
    log.info("========== 连板天梯生成 开始 date=%s time=%02d:%02d ==========",
             date, g.tm_hour, g.tm_min)
    total = kpl.save_ladder_history(date)
    if total == 0:
        log.warning("连板天梯 date=%s 拉取为空(可能未开市/数据源异常), 跳过", date)
        return "empty", f"{date} 无数据"
    img = ladder_image.generate_for_date(date)
    if not img:
        return "err", f"{date} 图片生成失败"
    log.info("========== 连板天梯生成 完成 date=%s 股票=%d 图片=%s ==========",
             date, total, img)
    return "ok", f"{date} 完成, {total} 家, 图片已生成"


# ---------- 调度(后台线程, 由 worker.py 启动) ----------
_fired = None   # 记录已执行过的 YYYYMMDD+窗口, 防空跑


def _scheduler_loop():
    """轮询调度: 每 30s 检查一次, 到点执行一次"""
    global _fired
    log.info("连板天梯盘后生成 调度已启动(交易日 15:30)")
    while True:
        try:
            g, hm = _bj()
            date = _bj_date(g)
            if tc.is_trade_day_of(g) and abs(hm - GEN_AT) <= WINDOW and _fired != date:
                threading.Thread(target=run_daily, args=(), daemon=True,
                                 name="ladder-daily").start()
                _fired = date
            # 跨天/换日 清上次执行记录
            if _fired is not None and date != _fired and hm < GEN_AT - WINDOW - 5:
                _fired = None
        except Exception as e:
            log.warning("连板天梯调度异常 err=%s", e)
        time.sleep(30)


def start_scheduler():
    """启动后台天梯生成线程(worker.py 调用)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="ladder-daily-sched")
    t.start()
    log.info("连板天梯盘后生成 调度线程已启动(交易日 15:30)")