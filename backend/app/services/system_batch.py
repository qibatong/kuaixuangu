# -*- coding: utf-8 -*-
"""
系统自动批次服务 (2026-08-30 主人需求: 即使某天没点选股, 历史回看也要有当时系统推荐)
=================================================================================
在 9_25 竞价快照落库后, 自动用系统默认筛选条件跑一次全市场选股, 存为 system batch
(user_id=0, auto_applied=1), 供所有用户在「历史回看」页看到 9_25 时点的系统推荐。
历史回看 API (history.list_batches / query_history) 已合并 user_id=0 的批次。
"""
import threading
import time

from ..core import config, logger
from ..core import trade_calendar as tc
from . import filter_defaults, history, kpl

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
#
# ★ 2026-09-24 v4.11.46 二次修复(主人拍板) —— 上面那次"复用"只做到了一半:
#   本文件当时**又自带了一份 9 键副本**(缺 scoreFloor), 且合并用 `if k in merged`
#   白名单 → settings 表里的 scoreFloor(线上 60)**静默丢弃** → 实际吃的是
#   picker/lock._FILTER_DEFAULTS 的硬编码兜底 **50**。
#   实测(测试机 47.99.153.123): 同一时刻同一份 9:25 快照,
#   **系统批次 64 只 vs 首页左视图 27 只** —— 与本文档声称的"完全一致"不符。
#   (漏测原因: validate_filters 对缺键有默认值, 输出上看不出键是否存在, 只有跑库对拍才暴露。)
#   本次改为直接复用 services/filter_defaults(单一真相源), 本文件**不再保留任何副本**。
#   🚫 新增/修改默认筛选参数只改 filter_defaults.FILTER_DEFAULTS。
# =====================================================================

# 市场范围(兼容别名): 真相源见 services/filter_defaults.SYSTEM_MARKETS。
# 口径必须是**小写** hs/cyb/kcb — scorer._in_markets 按代码前缀匹配小写键,
# 传大写 ["SH","SZ","BJ"] 会让沪深创科全部返回 False → 名单恒空。
SYSTEM_MARKETS = list(filter_defaults.SYSTEM_MARKETS)

SYSTEM_TOP = 30          # 系统批次截取前 30(与 aipick 一致, 避免 batch_stocks 过大)


def _system_filter():
    """系统默认筛选条件 = 全局默认(管理员后台可调, 首页左视图 / 9:26 自动应用同源)

    ★ v4.11.46: 转调 filter_defaults.system_filters()。本函数此前自带一份 9 键副本
      (缺 scoreFloor)并用白名单合并 ⇒ settings 的 scoreFloor 被丢弃、系统批次口径
      悄悄落到 lock 的兜底 50。现在与 auto_apply._get_system_filter 共用同一实现。
    """
    return filter_defaults.system_filters()


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


def _do_run(time_point, now=None):
    """实际跑选股+落库(供 run_system_batch 调用)

    now: 时间注入(测试用), None = 当前时间。
        2026-09-12: 原来写死取 time.time(), 导致**周末跑测试必红**(tm_wday>=5 直接
        return, 业务断言根本没机会执行)。与 picker.pipeline.run(now=...) 同惯例。

    2026-09-11: 老链路(_run_legacy)与 settings `picker_lock` 回滚开关已删除 ——
    picker.pipeline 是**唯一**选股链路(模式层 → 定格快照 → 粗筛 → 评分 → 精筛),
    与首页选股同一条代码路径, 锁仓不再有自己的一套取数与过滤逻辑。
    """
    t0 = time.time() if now is None else float(now)
    g = time.gmtime(t0 + 8 * 3600)
    # 2026-09-25（中秋节）复盘: 原为裸 `g.tm_wday >= 5`，法定假日会照跑选股批次
    #   → 假日「系统自动选股」名单由旧快照生成, 与 AI 侧同为幽灵数据。改走交易日历。
    # ★ 保留说明(2026-09-26 归口改动时人工合并): 本次把默认筛选参数归口到
    #   services/filter_defaults 时, 曾以测试机版本为底 —— 而测试机那版**删掉了本行守卫**
    #   (`if g.tm_wday >= 5` = 只判周末), 若整文件照搬会重现 09-25 中秋幽灵名单事故。
    #   故此处**保留生产侧的交易日历守卫**, 与 filter_defaults 归口改动合并。
    if not tc.is_trade_day_of(g):  # 周末 / 法定休市日跳过
        log.info("system_batch[%s] 非交易日(周%d)跳过", time_point, g.tm_wday)
        return
    # 检查今日是否已存(防重复, 同日同 user_id 同 time_point 只一条)
    today_str = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)

    # 2026-09-12 P1-1: 选股**之前**先跑全市场预计算(写 stock_score_daily)。
    #   这样本次批跑与之后所有用户请求都能直接读物化表 —— 改筛选条件不必重跑取数与
    #   评分(毫秒级), 且名单天然幂等。开关默认关; 失败只记日志, 不影响批跑
    #   (读路径检测到物化表行数不足会静默回退原路径)。
    #   注意放在"今日已存则跳过"**之前**: 否则批跑跳过的日子也不会预计算。
    try:
        from .picker import precompute
        if precompute.write_enabled():
            # 昨涨停/连板名单必须**显式传入**: 物化表的 is_zt_yday 是前端本地筛选
            # 判"剔除昨涨停"的唯一依据, 不传则恒 0 → 本地名单会凭空多一批昨涨停票
            # (2026-09-12 P3 一致性修复)。取不到(None)时 precompute 内部降级 concept 匹配。
            zt = None
            try:
                from . import fetcher as _fetcher
                zt = _fetcher.get_yesterday_zt_codes()
            except Exception as e:                                # noqa: BLE001
                log.warning("system_batch[%s] 昨涨停名单加载失败(预计算降级 concept) err=%s",
                            time_point, e)
            pst = precompute.precompute_all(today_str, zt_codes=zt)
            log.info("system_batch[%s] 预计算: %s", time_point, pst)
    except Exception as e:                                        # noqa: BLE001
        log.error("system_batch[%s] 预计算异常(不影响批跑) err=%s", time_point, e)

    if _has_today_system_batch(today_str, time_point):
        log.info("system_batch[%s] 今日已存, 跳过", time_point)
        return
    f_raw = _system_filter()
    result = _run_picker(f_raw, time_point)
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


def _run_picker(f_raw, time_point):
    """唯一链路: picker.pipeline(名单只认 9:25 定格, 与首页选股同源)。"""
    from .picker import lock as plock
    lr = plock.run_lock(f_raw, top=SYSTEM_TOP,
                        log_tag="system_batch[%s]" % time_point)
    if lr.errors:
        log.warning("system_batch[%s] 新链路告警: %s", time_point,
                    "; ".join(lr.errors))
    return list(lr.items)


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
