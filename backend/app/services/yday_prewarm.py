# -*- coding: utf-8 -*-
"""
开盘前昨比预热(2026-09-02 生产事故后新增)
========================================
背景: 东财对生产机 IP 限流常于盘中出现(8/31-9/2 连续三天事故源头)。昨比(昨日成交额)
是静态数据, 若等用户请求时才拉, 冷缓存全量拉取会触发限流 → 全站卡顿。

方案: 交易日 9:05(北京时间)后台分批拉全市场昨比填缓存, 盘中用户请求直接命中。
- 昨比缓存是 web 进程级(fetcher._yesterday_cache), 预热必须挂 web 进程(main.py startup),
  不能放 kx-worker(独立进程, 缓存不共享)
- --workers 2 下两个 worker 各自预热(各拉一次, 9:05 早盘前压力小, 可接受)
- 分批 200 只/批, 每批走 fetch_yesterday_amounts(wait=True)(12s 超时 + 失败缓存),
  成功部分当日缓存有效, 失败部分 600s 重试窗口由盘中请求异步补齐
"""
import threading
import time

from ..core import logger
from . import fetcher, scorer

log = logger.get_logger(__name__)

PREWARM_AT = 9 * 60 + 5          # 9:05 北京时间触发
WINDOW = 20                      # 触发窗口(±20 分钟, 防 systemd 拉起稍晚错过)
BATCH = 200                      # 每批 200 只(4 并发 12s 超时内可完成大部分)
MAX_SECONDS = 600                # 单轮预热最多跑 10 分钟(9:05→9:15, 开盘前收尾)

_fired = {}                      # date -> date(防同一天重复触发)


def _bj():
    g = time.gmtime(time.time() + 8 * 3600)
    date = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    return g, g.tm_wday, g.tm_hour * 60 + g.tm_min, date


def _prewarm_once():
    """执行一轮预热(后台线程): 分批拉全市场昨比写缓存"""
    g, wday, hm, date = _bj()
    try:
        fs = scorer.market_fs(["hs", "cyb", "kcb"])
        # 全市场行情 map(code -> quote), 东财 clist 熔断时自动走腾讯兜底
        quote_map = fetcher.fetch_spot_quote_map(fs)
        codes = [c for c in quote_map.keys() if c]
        if not codes:
            log.warning("昨比预热 date=%s 全市场行情为空, 跳过", date)
            return
        total = len(codes)
        done = 0
        chg_ok = 0          # 2026-09-08: 涨跌幅命中数(成交额对成功也可能没涨幅)
        t0 = time.time()
        for i in range(0, total, BATCH):
            batch = codes[i:i + BATCH]
            fetcher.fetch_yesterday_amounts(batch, wait=True)
            chg_ok += sum(1 for c in batch
                          if not fetcher._chg_missing(fetcher._yesterday_cache.get(c) or []))
            done += len(batch)
            if time.time() - t0 > MAX_SECONDS:
                log.info("昨比预热达 10 分钟上限, 已处理 %d/%d, 剩余由盘中异步补齐", done, total)
                return
        log.info("昨比预热完成 date=%s 全市场%d只 昨涨命中=%d 耗时%.0fs",
                 date, total, chg_ok, time.time() - t0)
    except Exception as e:
        log.warning("昨比预热异常 err=%s", e)


def _scheduler_loop():
    """轮询调度: 每 30s 检查, 交易日 9:05 窗口内触发一次"""
    log.info("昨比预热调度已启动(交易日 9:05)")
    while True:
        try:
            g, wday, hm, date = _bj()
            if wday < 5 and abs(hm - PREWARM_AT) <= WINDOW and _fired.get(date) != date:
                threading.Thread(target=_prewarm_once, daemon=True,
                                 name="yday-prewarm").start()
                _fired[date] = date
        except Exception as e:
            log.warning("昨比预热调度异常 err=%s", e)
        time.sleep(30)


def start_prewarm_scheduler():
    """启动预热调度线程(main.py startup 调用; 每个 web worker 各跑一份)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="yday-prewarm-sched")
    t.start()
    log.info("昨比预热调度线程已启动(交易日 9:05, 每批%d只)", BATCH)
