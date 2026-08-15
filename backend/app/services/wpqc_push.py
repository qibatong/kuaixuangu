# -*- coding: utf-8 -*-
"""
尾盘竞价抢筹推送: 工作日 14:57 拉取开盘啦尾盘抢筹榜并推送飞书/微信
====================================================================
- 14:57-15:01 窗口内只推一次(带去重)
- 抢筹数据源失败时静默跳过, 不影响主流程
"""
import threading
import time

from ..core import logger
from . import kpl, notify
from .cache_store import store

log = logger.get_logger(__name__)
# 去重标记已外置 CacheStore(跨进程): setnx("wpqc:done:date", 1天)
_PUSH_START = 14 * 60 + 55   # 14:55
_PUSH_END = 15 * 60 + 5      # 15:05
_TOP_N = 8


def _build_message(rows):
    """把尾盘抢筹榜构造成推送文本"""
    head = "【快选 · 尾盘竞价抢筹 %s】" % notify.bj_date_str()
    lines = [head, "尾盘竞价抢筹信号 Top%d:" % _TOP_N]
    if not rows:
        lines.append("今日尾盘无抢筹信号。")
        return "\n".join(lines)
    for i, r in enumerate(rows[:_TOP_N]):
        icon = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i]
        name = "%s %s" % (r.get("code", ""), r.get("name", ""))
        net = r.get("qcNet", 0) or 0
        lines.append("%s %s  抢筹净额%+.2f亿 强度%d 涨%+.2f%%" % (
            icon, name.strip(), net / 1e8, r.get("qcStrength", 0) or 0, r.get("change", 0) or 0))
        extra = []
        if r.get("limitBoards"):
            extra.append("%d连板" % r["limitBoards"])
        if r.get("concept"):
            extra.append(r["concept"])
        if extra:
            lines.append("   " + "  ".join(extra))
    return "\n".join(lines)


def push_once():
    """拉取尾盘抢筹并推送(幂等: 当日已推过则跳过)"""
    date = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    if not store.setnx("wpqc:done:" + date, 1, ttl=86400):
        return False
    try:
        rows = kpl.fetch_wpqc() or []
        text = _build_message(rows)
        notify.send_text(text)
        log.info("尾盘抢筹推送完成 date=%s 抢筹数=%d", date, len(rows))
        return True
    except Exception as e:
        log.error("尾盘抢筹推送异常 err=%s", e)
        return False


def _scheduler_loop():
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            hm = g.tm_hour * 60 + g.tm_min
            # 工作日 14:55-15:05 窗口, 抢筹数据 14:57 后才有
            if g.tm_wday < 5 and _PUSH_START <= hm <= _PUSH_END and hm >= 14 * 60 + 57:
                push_once()
        except Exception as e:
            log.error("尾盘抢筹调度异常 err=%s", e)
        time.sleep(20)


def start_scheduler():
    """main.py startup 调用: 启动后台尾盘抢筹推送线程"""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    log.info("尾盘竞价抢筹推送调度已启动(14:57)")
