# -*- coding: utf-8 -*-
"""
开盘前昨比预热(2026-09-02 生产事故后新增)
========================================
背景: 东财对生产机 IP 限流常于盘中出现(8/31-9/2 连续三天事故源头)。昨比(昨日成交额)
是静态数据, 若等用户请求时才拉, 冷缓存全量拉取会触发限流 → 全站卡顿。

方案: 交易日后台分批拉全市场昨比填缓存, 用户请求直接命中。
- 昨比缓存是 web 进程级(fetcher._yesterday_cache), 预热必须挂 web 进程(main.py startup),
  不能放 kx-worker(独立进程, 缓存不共享)
- --workers 2 下两个 worker 各自预热(各拉一次, 早盘前压力小, 可接受)
- 分批 200 只/批, 每批走 fetch_yesterday_amounts(wait=True)(12s 超时 + 失败缓存),
  成功部分当日缓存有效, 失败部分由调度重试 + 用户请求异步补齐

2026-09-08 P5 补强(切流后分数稳定性前提):
  @9:05  盘中预热(跳过今天, T=前一交易日)  —— 覆盖 9:25-15:00 竞价/盘中
  @15:10 收盘刷新(收盘后 T=今天, 见 fetcher._after_close) —— 覆盖 15:00 后"昨日涨幅
         必须指今天"的语义(原实现只预热一次, 收盘后到午夜前的"昨日涨幅"整整滞后一天)
  重启补跑: 服务重启(_fired 进程级清空)错过 9:05 窗口 → 9:25~14:55 内自动补一轮,
         否则冷缓存下第一批用户请求会触发全量异步拉 → 分数在"缺值/命中"间跳
          (90↔93 漂移的窗口版)。
  _fired[(date, stage)] 记录各阶段是否**成功**(False 时 30s 后重试, 不再"失败即放弃")。
"""
import threading
import time

from ..core import logger
from . import fetcher, scorer

log = logger.get_logger(__name__)

PREWARM_AT = 9 * 60 + 5          # 9:05 北京时间触发(盘中预热)
WINDOW = 20                      # 触发窗口(±20 分钟, 防 systemd 拉起稍晚错过)
CLOSE_AT = 15 * 60 + 10          # 15:10 收盘刷新(给数据商 10 分钟落库缓冲)
CLOSE_WINDOW = 15                # ±15 分钟(15:10~15:25)
CATCHUP_UNTIL = 14 * 60 + 55     # 重启补跑截止 14:55(盘尾前拉好, 留时间给用户请求)
BATCH = 200                      # 每批 200 只(4 并发 12s 超时内可完成大部分)
MAX_SECONDS = 600                # 单轮预热最多跑 10 分钟

_fired = {}                      # (date, stage) -> bool(是否成功; False 可重试)


def _bj():
    g = time.gmtime(time.time() + 8 * 3600)
    date = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    return g, g.tm_wday, g.tm_hour * 60 + g.tm_min, date


def _persist_to_db(codes, date):
    """收盘后把本轮拉到的昨比批量落库(2026-09-10 新增)。

    落库只发生在收盘刷新(stage=close): 此时今天的 K 线已定格, T 日 = 今天,
    tdate 语义明确。次日盘中读取即为"昨日", 收盘后再覆盖为"今天"(见 database 建表注释)。
    盘中预热(stage=open)不落库 —— 那时 T=前一交易日, 写进去会污染。
    """
    tdate = date.replace("-", "")
    rows = []
    with fetcher._yesterday_lock:
        for c in codes:
            ent = fetcher._yesterday_cache.get(c)
            if not ent or ent[0] != date or ent[1] is None:
                continue                       # 未拉到/是失败的, 不落库
            pair = ent[1]
            if not pair or pair[0] is None:
                continue
            chg = ent[3] if len(ent) > 3 else None
            prev = pair[1] if len(pair) > 1 else None
            rows.append((c, tdate, pair[0], prev, chg))
    if not rows:
        log.warning("昨日成交额收盘落库: 无有效数据可写 date=%s", date)
        return 0
    n = fetcher.yday_db_put(rows)
    log.info("昨日成交额收盘落库 %d/%d 只 tdate=%s", n, len(rows), tdate)
    return n


def _prewarm_once(stage="open"):
    """执行一轮预热(后台线程): 分批拉全市场昨比写缓存; 返回是否成功(失败可重试)

    stage: "open"=盘中预热(9:05, T=前一交易日) / "close"=收盘刷新(15:10, T=今天)
    2026-09-10: 稳态下缓存直接由 yday_amount 库命中(fetcher._yday_hydrate_from_db),
    本轮几乎不发网络请求; 只有库里没有的(新股/停牌/任务未跑)才实时拉东财。
    stage="close" 时额外把结果落库, 供次日全天零网络使用。
    """
    g, wday, hm, date = _bj()
    try:
        fs = scorer.market_fs(["hs", "cyb", "kcb"])
        # 全市场行情 map(code -> quote)
        quote_map = fetcher.fetch_spot_quote_map(fs)
        codes = [c for c in quote_map.keys() if c]
        if not codes:
            log.warning("昨比预热 date=%s 全市场行情为空, 跳过(稍后重试)", date)
            return False
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
                log.info("昨比预热达 10 分钟上限, 已处理 %d/%d, 剩余由用户请求异步补齐",
                         done, total)
                if stage == "close":
                    _persist_to_db(codes[:done], date)
                return True      # 已达上限仍算成功(防当日重复), 剩余走异步补齐
        log.info("昨比预热完成 stage=%s date=%s 全市场%d只 昨涨命中=%d 耗时%.0fs",
                 stage, date, total, chg_ok, time.time() - t0)
        if stage == "close":
            _persist_to_db(codes, date)
        return True
    except Exception as e:
        log.warning("昨比预热异常 err=%s(稍后重试)", e)
        return False


def _scheduler_tick():
    """单次调度判定(抽成函数便于单测); 返回本次是否触发"""
    g, wday, hm, date = _bj()
    if wday >= 5:
        return False
    fired_open = _fired.get((date, "open"))
    fired_close = _fired.get((date, "close"))
    triggered = False

    # --- 盘中预热(9:05±20) + 重启补跑(9:25~14:55, 进程级 _fired 清空后自动恢复) ---
    if not fired_open:
        in_window = abs(hm - PREWARM_AT) <= WINDOW
        catchup = PREWARM_AT + WINDOW < hm <= CATCHUP_UNTIL
        if in_window or catchup:
            ok = _prewarm_once(stage="open")
            _fired[(date, "open")] = ok
            log.info("昨比盘中预热 %s hm=%d:%02d → %s",
                     "正常窗口" if in_window else "重启补跑", hm // 60, hm % 60,
                     "成功" if ok else "失败(30s后重试)")
            triggered = True

    # --- 收盘刷新(15:10~15:25): 收盘后 T 日推进到"今天"(fetcher._after_close) ---
    #    注意只往后开窗(不允许提前): 提前触发时还没收盘, _after_close=False → 拉到的是
    #    盘中语义(T=昨天), 缓存又被标成 close 已完成 → 15:05 后永远补不回今天的涨幅。
    if not fired_close:
        if CLOSE_AT <= hm <= CLOSE_AT + CLOSE_WINDOW:
            ok = _prewarm_once(stage="close")     # 收盘后额外落库(供次日全天零网络读取)
            _fired[(date, "close")] = ok
            log.info("昨比收盘刷新 hm=%d:%02d → %s", hm // 60, hm % 60,
                     "成功" if ok else "失败(30s后重试)")
            triggered = True
    return triggered


def _scheduler_loop():
    """轮询调度: 每 30s 检查一次(盘中预热/重启补跑/收盘刷新 三态)"""
    log.info("昨比预热调度已启动(9:05盘中预热 + 15:10收盘刷新 + 重启自动补跑)")
    while True:
        try:
            _scheduler_tick()
        except Exception as e:                             # noqa: BLE001
            log.warning("昨比预热调度异常 err=%s", e)
        time.sleep(30)


def start_prewarm_scheduler():
    """启动预热调度线程(main.py startup 调用; 每个 web worker 各跑一份)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="yday-prewarm-sched")
    t.start()
    log.info("昨比预热调度线程已启动(交易日 9:05/15:10, 每批%d只)", BATCH)
