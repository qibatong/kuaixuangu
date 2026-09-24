# -*- coding: utf-8 -*-
"""
9:26 自动应用服务 (2026-08-16)
============================
背景: 诗人反馈用户"打开着应用但没点应用按钮", 当天历史为空。
方案: 9:26 抢筹快照落库后, 系统用统一标准(全局默认筛选参数)筛选一次,
      把同一份结果推给所有非管理员用户, 以 auto_applied=True 标记写入各自批次。
      用户当天手动筛选(lock/filter)则跳过自动应用, 手动优先。

产品决策 (2026-08-16 北棠确认):
- 自动应用 = 系统统一筛选 → 所有用户历史一致(同一份"系统当日推荐")
- 用户手动筛选时才按个人偏好单独进行
- 不做"每用户按 filter_prefs 个性化", 保证复盘/对比/推送统一

设计要点:
- 全市场评分只跑一次 + 系统标准过滤一次 (picker 链路单次执行)
- 后台守护线程执行, 不阻塞 auction_snapshot 调度循环
- 单用户落库失败不影响其他人
- 管理员/过期账号跳过; 当天已有统一批次的用户跳过(防重复)
- 2026-08-18 主人需求变更: 手动 lock 不再跳过 — 统一批次对所有人生效(数据一致)
"""
import threading
import time

from ..core import logger
from ..services import auction_snapshot, fetcher, history, kpl, scorer, users
from . import filter_defaults           # 系统筛选标准(单一真相源)
from .cache_store import store as _store
log = logger.get_logger(__name__)


# ---- 9:26 自动应用的**幂等 / 重试**守卫 (2026-09-17 v4.11.27 修复) ----
# 缺陷: auction_snapshot 原用
#     store.setnx("sched:auto_apply:" + date, 1, ttl=86400)
# 作为**每日一次性**锁, 但它在**成功之前**就被消费 —— `_pick_result()` 无票时提前
# return error, 锁却已烧掉 → 9:26-9:30 剩余 ~24 轮(10s 一轮)全部被 setnx 挡掉 →
# **当日系统统一批次永久缺失** → 用户刷新直读只能跨日回退到**上一个交易日**的名单。
# 这是 2026-09-17 早盘「9:30 后出来的数据好像是昨天的」事故的放大部分。
#
# 修复: 拆成两把键, 语义分离 ——
#   · done 键: **成功算出非空名单后**才置, 当日幂等(防重复扇出);
#   · try  键: TTL 节流(不是每日锁), 失败后节流过期即可重试。
_AUTO_APPLY_DONE_KEY = "sched:auto_apply:done:"
_AUTO_APPLY_TRY_KEY = "sched:auto_apply:try:"
AUTO_APPLY_RETRY_SEC = 60      # 9:26-9:30 共 5 分钟 → 最多约 5 次尝试


def already_done(bdate):
    """当日是否已成功跑过一次(幂等位)"""
    return bool(_store.get(_AUTO_APPLY_DONE_KEY + bdate))


def mark_done(bdate):
    """标记当日已成功(只在算出非空名单后调用)"""
    _store.set(_AUTO_APPLY_DONE_KEY + bdate, 1, ttl=86400)


def try_acquire(bdate):
    """占**节流**位(不是每日锁) —— 返回 True 表示本轮可以跑"""
    return bool(_store.setnx(_AUTO_APPLY_TRY_KEY + bdate, 1, ttl=AUTO_APPLY_RETRY_SEC))


def should_trigger(bdate):
    """9:26-9:30 调度分支的**唯一判据**: 未成功 且 不在节流窗口内。

    抽成函数是为了可测(见 tests/test_auto_apply_retry.py), 调用点只做时间窗判断。
    """
    return (not already_done(bdate)) and try_acquire(bdate)


def _today_bj():
    """北京当日 YYYY-MM-DD"""
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _user_auto_applied_today(uid, bdate):
    """用户当天是否已有系统统一批次(auto_applied=1) — 仅防重复执行
    2026-08-18 主人需求变更: 不再"手动优先"跳过(原 _user_already_applied_today) —
    9:26 统一批次对**所有**活跃用户生效, 即使当天手动 lock 过也要统一,
    保证所有用户在竞价选股页看到同一份结果"""
    from ..db import database
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM batches WHERE user_id=? AND batch_date=? AND auto_applied=1",
            (uid, bdate)).fetchone()
        return (row[0] or 0) > 0
    finally:
        conn.close()


def _get_system_filter():
    """系统统一筛选标准: 全局默认参数(管理后台可调), 不读用户偏好
    (2026-08-16 产品决策: 自动应用 = 系统筛选一次推给所有用户,
    用户手动筛选时才按个人偏好单独进行)

    2026-09-08 P4 实测修复(历史): 原为 ["SH","SZ","BJ"] 大写形态, 而
    scorer._in_markets 只认小写 hs/cyb/kcb(按代码前缀判定) → 沪深创科**全部**
    返回 False → 9:26 自动应用**恒出 0 只**。
    实锤: 2026-09-08 批次#1578(user=213, auto_applied=1) count=0; 同日系统批次
    #1577 用小写口径 → 正常 30 只。

    ★ v4.11.46: 改调 filter_defaults.system_filters() —— 与 system_batch 共用
      同一实现。此前本函数读 admin.get_default_filters()(含 scoreFloor, 随管理员
      调整), 而 system_batch 读自己那份 9 键副本(缺 scoreFloor) ⇒ 两条锁仓链路
      口径不同。现在两处同一份。
    """
    return filter_defaults.system_filters()


def _is_user_active(uid):
    """用户是否可自动应用: 存在 且 非管理员 且 未过期"""
    u = users.find_user_by_id(uid)
    if not u:
        return False, "用户不存在"
    if int(u.get("is_admin") or 0):
        return False, "管理员"
    expire = int(u.get("expire_at") or 0)
    if expire and expire < int(time.time()):
        return False, "账号已过期"
    return True, ""


def _run_in_background(func, *args, **kwargs):
    """后台守护线程执行, 不阻塞调度主循环; 异常全部吞掉只记日志"""
    def _wrapped():
        try:
            func(*args, **kwargs)
        except Exception as e:
            log.error("auto_apply 后台任务异常 err=%s", e, exc_info=True)
    t = threading.Thread(target=_wrapped, daemon=True, name="auto_apply")
    t.start()
    log.info("auto_apply 后台线程已启动(%s)", t.name)
    return t


def _pick_result():
    """算出"系统统一名单"(所有用户共享同一份), 返回 (result, error)。

    2026-09-09 起与首页选股**同一条**链路(picker.pipeline), 不再回退老链路 ——
    双轨的代价是: 新链路没出票时静默走老链路, 缺陷永远暴露不出来(首页竞价窗口
    恒返回 0 只跑了一整天无人发现, 正是这条路径在"兜底")。现在没出票就是没出票,
    返回 error 让调度跳过本轮并告警, 而不是用另一条链路的结果掩盖。
    """
    from .picker import lock as plock
    try:
        lr = plock.run_lock(_get_system_filter(), log_tag="auto_apply")
    except Exception as e:                                     # noqa: BLE001
        log.error("auto_apply 选股异常 err=%s", e, exc_info=True)
        return [], str(e)
    if not lr.items:
        msg = "; ".join(lr.errors) or lr.summary()
        log.warning("auto_apply 本轮无名单(跳过应用) %s", msg)
        return [], msg or "名单源无数据"
    return lr.items, ""


def auto_apply_all_users(max_users=None):
    """给所有活跃用户自动应用一次 (应在后台线程调用)
    系统统一标准(全局默认筛选)过滤一次 -> 同一份结果推给所有用户 -> 各自落库
    用户当天已手动筛选(lock/filter)则跳过, 手动优先
    max_users: 限制本次处理用户数 (调试用, 默认 None=不限)
    返回 {applied, skipped, failed, total, cost_ms}"""
    t0 = time.time()
    bdate = _today_bj()
    result, err = _pick_result()
    f = _get_system_filter()      # 落库存的就是这份条件(新/老链路共用同一份系统标准)
    if err:
        return {"applied": 0, "skipped": 0, "failed": 0, "total": 0,
                "cost_ms": int((time.time() - t0) * 1000), "error": str(err)}
    # 候选用户: 非管理员 + 未过期
    from ..db import database
    conn = database.get_conn()
    try:
        user_ids = [r[0] for r in conn.execute(
            "SELECT id FROM users WHERE is_admin=0 AND "
            "(expire_at=0 OR expire_at>=?) ORDER BY id",
            (int(time.time()),)).fetchall()]
    finally:
        conn.close()
    if max_users is not None:
        user_ids = user_ids[:max_users]
    applied = skipped = failed = 0
    log.info("auto_apply 开始 候选=%d 当日=%s", len(user_ids), bdate)
    # 2026-09-08: 空名单不落库(系统统一筛选为空 → 所有用户同一份结果都为空)。
    # 必须在此提前返回, 否则每个候选用户都会被记一次 failed, 把"行情源故障/无票"
    # 误报成"落库失败", 淹没真实告警。
    if not result:
        cost0 = (time.time() - t0) * 1000
        log.warning("auto_apply 系统统一筛选为空(行情源故障或条件过严) 候选=%d "
                    "空名单不落库 → 全部跳过", len(user_ids))
        return {"applied": 0, "skipped": len(user_ids), "failed": 0,
                "total": len(user_ids), "cost_ms": int(cost0),
                "error": "空名单不落库"}
    for uid in user_ids:
        try:
            # 每个用户: 存在性/过期/当天已应用 三重跳过 (手动筛选优先)
            ok, reason = _is_user_active(uid)
            if not ok:
                log.info("auto_apply 跳过 uid=%s 原因=%s", uid, reason)
                skipped += 1
                continue
            if _user_auto_applied_today(uid, bdate):
                log.info("auto_apply 跳过 uid=%s 原因=今天已有系统统一批次", uid)
                skipped += 1
                continue
            bid = history.save_batch(uid, "lock", result, f, auto_applied=True)
            if bid:
                applied += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            log.warning("auto_apply 单用户失败 uid=%s err=%s", uid, e)
    cost = (time.time() - t0) * 1000
    # 2026-09-17 (v4.11.27): 走到这里说明**名单已算出且非空** → 本轮有效, 置幂等位收工。
    # 只有这一处置 done —— 上面两个 error 分支(无票 / 空名单)**刻意不置**, 好让
    # 9:26-9:30 的后续轮次在节流过期后重试, 而不是当天彻底没有系统批次。
    mark_done(bdate)
    log.info("auto_apply 结束 applied=%d skipped=%d failed=%d 耗时%.0fms (已置 done=%s)",
             applied, skipped, failed, cost, bdate)
    return {"applied": applied, "skipped": skipped, "failed": failed,
            "total": len(user_ids), "cost_ms": int(cost)}


def trigger_auto_apply():
    """9:26 抢筹落库后由 auction_snapshot 调用: 后台线程执行, 立即返回"""
    return _run_in_background(auto_apply_all_users)
