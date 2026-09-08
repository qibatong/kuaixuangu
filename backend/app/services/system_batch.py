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
from . import history, kpl, scorer, settings
from .fetcher import ensure_cache, fetch_yesterday_amounts, fetch_yesterday_changes
from . import auction_snapshot

log = logger.get_logger(__name__)

SYSTEM_USER_ID = 0  # system batch 归属用户, 所有用户都能看到

# =====================================================================
# 2026-08-31 修复(主人反馈: 历史回看自动批次"锁的是AI预测数据, 应该是首页左视图竞价选股"):
# 原 DEFAULT_FILTER 键名(mvMin/mvMax/amtMin/chgMax/excludeSt)与 scorer.validate_filters
# 期望的键(floatMvFloor/floatMvGt/bidAmtFloor/bidGt/stSuspend/limitUp)完全不匹配
# → 所有自定义条件静默落回 validate_filters 后端默认
#   (stSuspend=True 不剔除ST / floatMvGt=100 亿 / priceGt=30 元 → 中小盘小票池 ≈ aipick 候选池,
#    与首页左视图的 1000 亿/300 元大票池完全不同, 用户看到后误认为锁的是 AI 预测数据)
# 修复: 复用管理员后台全局默认(admin.DEFAULT_FILTERS_DEFAULT + settings 表 default_filters),
#       与首页左视图(前端同样读取该默认)完全一致; 键名与 validate_filters 对齐。
# =====================================================================
DEFAULT_FILTERS_DEFAULT = {
    "stSuspend": False, "limitUp": False, "bidGt": 7.0,
    "probLt": 65.0, "confLt": 65.0,
    "floatMvFloor": 30.0, "floatMvGt": 1000.0, "priceGt": 300.0,
    "bidAmtFloor": 1000.0,   # 与 admin.py 全局默认一致(默认竞价金额下限 1000万)
}

# 市场范围: 与首页左视图一致(沪深创科, 不读用户自定义 filter_prefs)
# 口径必须是**小写** hs/cyb/kcb — scorer._in_markets 按代码前缀匹配小写键,
# 传大写 ["SH","SZ","BJ"] 会让沪深创科全部返回 False → 名单恒空。
SYSTEM_MARKETS = ["hs", "cyb", "kcb"]

SYSTEM_TOP = 30          # 系统批次截取前 30(与 aipick 一致, 避免 batch_stocks 过大)


def _system_filter():
    """系统默认筛选条件 = 管理员全局默认(管理员后台可调, 首页左视图同样读取)"""
    merged = dict(DEFAULT_FILTERS_DEFAULT)
    cfg = settings.get("default_filters")
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k in merged:
                merged[k] = v
    merged["markets"] = SYSTEM_MARKETS
    return merged


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


def _load_strengths(raw):
    """竞价强度 map(替代失活的 f630 异动等级)。未启用/异常 → 空 dict → 退回 f630。

    2026-09-08 P4: 此前 system_batch 是**唯一没接竞价强度**的选股入口(方案A 只改了
    api/stocks 与 auto_apply) → 历史回看里的系统批次仍在用恒为 0 的 f630, 与首页
    同一只票两个分。此处补齐, 与 auto_apply 共用 bid_strength.load_scores 同一口径。
    """
    from . import bid_strength
    return bid_strength.load_scores([s.get("f12") for s in (raw or [])])


def _do_run(time_point):
    """实际跑选股+落库(供 run_system_batch 调用)

    2026-09-08 P4: 拆成老/新两条链路, 由 settings `picker_lock` 切换
    (P5 切流后**默认新链路**, 显式 0/false/off 才回退老链路)。
    新链路走 picker.pipeline(模式层 → 定格快照 → 粗筛 → 评分 → 精筛), 与首页选股
    同一条代码路径 —— 锁仓不再有自己的一套取数与过滤逻辑。
    """
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
    f_raw = _system_filter()
    if _picker_lock_on():
        result = _run_new(f_raw, time_point)
    else:
        result = _run_legacy(f_raw, time_point)
    kpl.apply_board_concept(result, "system_batch")
    # 截取 top 30(避免 batch_stocks 太大, 与 aipick 保持一致)
    result = result[:SYSTEM_TOP]
    # 落库为 system batch(user_id=0, auto_applied=1, action='lock')
    # 2026-09-08: 空名单不落库 — 行情源故障时系统批次为 0 只, 落库会污染历史并可能被
    # 当作有效批次直读(页面空白事故)。batch_id=None 即本次未产出有效名单, 下次调度重跑。
    batch_id = None
    if result:
        batch_id = history.save_batch(
            user_id=SYSTEM_USER_ID, action="lock", result=result, f=f_raw,
            auto_applied=True)
    log.info("system_batch[%s] 完成: top=%d只 batch_id=%s 耗时%.0fms",
             time_point, len(result), batch_id, (time.time() - t0) * 1000)


def _picker_lock_on():
    """settings `picker_lock` 控制走哪条链路。

    P5 切流后**默认走新链路**(未配置 = 新链路); 显式设为 0/false/off 才回退老链路
    —— 回滚只需改 settings, 不需要改代码重新部署。
    """
    try:
        from .picker import lock as plock
        return plock.enabled_default_on(settings.get("picker_lock"))
    except Exception:                                          # noqa: BLE001
        return True


def _run_new(f_raw, time_point):
    """新链路: picker.pipeline(名单只认 9:25 定格, 与首页选股同源)。"""
    from .picker import lock as plock
    lr = plock.run_lock(f_raw, top=SYSTEM_TOP,
                        log_tag="system_batch[%s]" % time_point)
    if lr.errors:
        log.warning("system_batch[%s] 新链路告警: %s", time_point,
                    "; ".join(lr.errors))
    return list(lr.items)


def _run_legacy(f_raw, time_point):
    """老链路(改造前的实现, 保留用于回退与对拍)。"""
    # 拉全市场竞价 raw(用 ensure_cache 共享 TTL 缓存, 不会重复打东财)
    fs = scorer.market_fs(f_raw["markets"])
    before930, _, _ = scorer.bj_now()
    raw, err = ensure_cache("lock", fs, before930)
    if err:
        log.warning("system_batch[%s] ensure_cache 失败 err=%s", time_point, err)
        return []
    if not raw:
        log.warning("system_batch[%s] raw 为空, 跳过", time_point)
        return []
    # 拉昨日成交额 + 9_20 快照(同 stocks.py); wait=True: 系统批次需完整昨比(后台任务可等待)
    yesterday_map = fetch_yesterday_amounts([s.get("f12") for s in raw], wait=True)
    # 2026-09-08 昨日涨幅真实化: wait=True 已同步拉完日K, 此处直接读缓存(零额外请求)
    yesterday_chg_map = fetch_yesterday_changes([s.get("f12") for s in raw])
    snapshot_map = auction_snapshot.load_snapshot()
    # 2026-09-03 竞额定格 map(9_25 快照): 系统批次落库 bidAmt 用当日定格竞价额
    bid_amt_map = auction_snapshot.load_day_bid_amt()
    # 2026-09-08 竞涨定格 map: 东财 f615 收盘后为 "-", bidChange 以 9:25 定格竞价涨幅为准
    bid_chg_map = auction_snapshot.load_day_bid_change()
    # 评分(用系统默认过滤 = 管理员全局默认, 与首页左视图一致; 不用用户 filter_prefs)
    # 转 query 形态 {key: [str]}(与 stocks.py 的 qs() 一致; 标量会被 `(q.get(k) or [..])[0]`
    #  下标截断: "False"[0]="F"/"1000.0"[0]="1", 布尔必须 "False"/"True" 完整字符串)
    f = scorer.validate_filters({
        k: [str(v)]
        for k, v in f_raw.items() if k != "markets"})
    # 2026-09-01 抢筹口径: 命中右视图竞价异动"竞价抢筹"代码集才打抢筹标
    qc_codes = kpl.get_qiangchou_codes()
    result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map,
                                       qiangchou_codes=qc_codes,
                                       day_bid_amt=bid_amt_map,
                                       day_bid_change=bid_chg_map,
                                       yesterday_chg_map=yesterday_chg_map,
                                       strengths=_load_strengths(raw))
    return result or []


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
