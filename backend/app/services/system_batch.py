# -*- coding: utf-8 -*-
"""
系统自动批次服务 (2026-08-30 主人需求: 即使某天没点选股, 历史回看也要有当时系统推荐)
=================================================================================
在 9_25 竞价快照落库后, 自动用系统默认筛选条件跑一次全市场选股, 存为 system batch
(user_id=0, auto_applied=1), 供所有用户在「历史回看」页看到 9_25 时点的系统推荐。
历史回看 API (history.list_batches / query_history) 已合并 user_id=0 的批次。
"""
import os
import threading
import time

from ..core import config, logger
from . import history, kpl, scorer
from .fetcher import ensure_cache, fetch_yesterday_amounts
from . import auction_snapshot

log = logger.get_logger(__name__)

SYSTEM_USER_ID = 0  # system batch 归属用户, 所有用户都能看到

# 系统默认筛选条件(选股核心与 stocks.py 一致; 不读用户自定义 filter_prefs)
DEFAULT_FILTER = {
    "markets": ["sh", "sz", "bj"],
    "mvMin": 30,      # 流通市值 ≥ 30 亿(2026-08-30 与 aipick 一致)
    "mvMax": 100,     # 流通市值 ≤ 100 亿
    "amtMin": 3000,   # 竞价金额 ≥ 3000 万
    "chgMax": 7,      # 竞价涨幅 ≤ 7%
    "excludeSt": 1,   # 排除 ST
    "excludeSuspended": 1,  # 排除停牌
    "excludeBoardFilter": [],  # 不限制板块
}


def run_system_batch(time_point="9_25", sync=False):
    """9_25 落库后调用: 用系统默认条件跑一次选股, save_batch(user_id=0, auto_applied=1)
    默认后台线程执行(不阻塞 auction_snapshot 调度), sync=True 用于手动测试
    """
    def _wrapped():
        try:
            _do_run(time_point)
        except Exception as e:
            log.error("system_batch[%s] 异常 err=%s", time_point, e, exc_info=True)
    if sync:
        _wrapped()
    else:
        threading.Thread(target=_wrapped, daemon=True, name=f"system_batch_{time_point}").start()


def _do_run(time_point):
    """实际跑选股+落库(供 run_system_batch 调用)"""
    t0 = time.time()
    g = time.gmtime(t0 + 8 * 3600)
    if g.tm_wday >= 5:  # 周六日跳过
        log.info("system_batch[%s] 非交易日跳过", time_point)
        return
    # 检查今日是否已存(防重复, 同日同 user_id 同 time_point 只一条)
    today_str = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    if _has_today_system_batch(today_str, time_point):
        log.info("system_batch[%s] 今日已存, 跳过", time_point)
        return
    # 拉全市场竞价 raw(用 ensure_cache 共享 TTL 缓存, 不会重复打东财)
    fs = scorer.market_fs(DEFAULT_FILTER["markets"])
    before930, _, _ = scorer.bj_now()
    raw, err = ensure_cache("lock", fs, before930)
    if err:
        log.warning("system_batch[%s] ensure_cache 失败 err=%s", time_point, err)
        return
    if not raw:
        log.warning("system_batch[%s] raw 为空, 跳过", time_point)
        return
    # 拉昨日成交额 + 9_20 快照(同 stocks.py)
    yesterday_map = fetch_yesterday_amounts([s.get("f12") for s in raw])
    snapshot_map = auction_snapshot.load_snapshot()
    # 评分(用 system 自己的 filter, 不用用户 filter_prefs)
    f = scorer.validate_filters({k: v for k, v in DEFAULT_FILTER.items() if k != "markets"})
    result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map)
    kpl.apply_board_concept(result, "system_batch")
    # 截取 top 30(避免 batch_stocks 太大, 与 aipick 保持一致)
    result = result[:30]
    # 落库为 system batch(user_id=0, auto_applied=1, action='lock')
    batch_id = history.save_batch(
        user_id=SYSTEM_USER_ID, action="lock", result=result, f=DEFAULT_FILTER,
        auto_applied=True)
    log.info("system_batch[%s] 完成: top=%d只 batch_id=%s 耗时%.0fms",
             time_point, len(result), batch_id, (time.time() - t0) * 1000)


def _has_today_system_batch(today_str, time_point):
    """检查今日是否已存 system batch(user_id=0, auto_applied=1)"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        # 当日的 batch_time 在 9:15/9:20/9:25 附近(±5min) + 9_25/9_20/9_15 关联
        # 简化: 查今日 user_id=0 + auto_applied=1 的 batch
        row = conn.execute(
            "SELECT COUNT(*) FROM batches WHERE user_id=? AND auto_applied=1 AND batch_date=?",
            (SYSTEM_USER_ID, today_str)).fetchone()
        conn.close()
        return (row[0] or 0) > 0
    except Exception:
        return False
