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
- 全市场评分只跑一次 + 系统标准过滤一次 (score_all_stocks 拆分)
- 后台守护线程执行, 不阻塞 auction_snapshot 调度循环
- 单用户落库失败不影响其他人
- 管理员/过期账号跳过; 当天已有统一批次的用户跳过(防重复)
- 2026-08-18 主人需求变更: 手动 lock 不再跳过 — 统一批次对所有人生效(数据一致)
"""
import threading
import time

from ..core import logger
from ..services import auction_snapshot, fetcher, history, kpl, scorer, users
from ..api import admin as admin_api   # 用 get_default_filters
log = logger.get_logger(__name__)


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
    用户手动筛选时才按个人偏好单独进行)"""
    f = admin_api.get_default_filters()
    # 2026-09-08 P4 实测修复: 原为 ["SH", "SZ", "BJ"] 大写形态, 而
    # scorer._in_markets 只认小写 hs/cyb/kcb(按代码前缀判定) → 沪深创科**全部**
    # 返回 False → 9:26 自动应用**恒出 0 只**。
    # 实锤: 2026-09-08 批次#1578(user=213, auto_applied=1) count=0; 同日系统批次
    # #1577 用 SYSTEM_MARKETS 小写口径 → 正常 30 只。
    # 北交所不在 UI 选项(与 market_fs 口径一致), 不纳入。
    f.setdefault("markets", ["hs", "cyb", "kcb"])
    return f


def _load_strengths(raw):
    """竞价强度 map(替代失活的 f630 异动等级), settings `use_bid_strength=1` 才启用。
    默认关闭 → 空 dict → 行为零变化。异常一律吞掉退回 f630。

    口径统一收敛到 bid_strength.load_scores(system_batch / 本处共用)。
    """
    from . import bid_strength
    return bid_strength.load_scores([s.get("f12") for s in (raw or [])])


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


def _picker_lock_on():
    """settings `picker_lock` 控制走哪条链路。

    P5 切流后**默认走新链路**(未配置 = 新链路); 显式设为 0/false/off 才回退老链路
    —— 回滚只需改 settings, 不需要改代码重新部署。
    """
    try:
        from . import settings
        from .picker import lock as plock
        return plock.enabled_default_on(settings.get("picker_lock"))
    except Exception:                                          # noqa: BLE001
        return True


def _pick_result():
    """算出"系统统一名单"(所有用户共享同一份), 返回 (result, error)。

    P4: 开关 picker_lock=1 走 picker.pipeline(与首页选股同一条代码路径, 名单只认
    9:25 定格); 否则走老链路(score_all_stocks + apply_filters)。两条路径都返回
    老链路同构的 item 列表, 落库/推送逻辑无感。
    """
    if _picker_lock_on():
        from .picker import lock as plock
        try:
            lr = plock.run_lock(_get_system_filter(), log_tag="auto_apply")
            if lr.items:
                return lr.items, ""
            # 新链路没出票(含"竞价未结束/盘前"这类主动拒绝) → 回退老链路, 不让用户空窗
            log.warning("auto_apply 新链路无结果(%s) → 回退老链路",
                        "; ".join(lr.errors) or lr.summary())
        except Exception as e:                                 # noqa: BLE001
            log.warning("auto_apply 新链路异常, 回退老链路 err=%s", e)
    return _legacy_result()


def _legacy_result():
    """老链路: 全市场行情 → 评分 → 系统统一过滤(改造前实现, 保留用于回退/对拍)
    返回 (result, error) — error 非空表示本轮无法产出名单(行情缺失/评分失败)。"""
    # 与 9:25 撮合同样的默认市场范围(前端默认 hs+cyb+kcb), 确保命中同一份行情缓存
    fs = scorer.market_fs(["hs", "cyb", "kcb"])
    raw, err = fetcher.ensure_cache("filter", fs, before930=True)
    if not raw:
        log.warning("auto_apply 行情缓存缺失 err=%s, 跳过本轮", err)
        return [], str(err)
    snapshot_map = auction_snapshot.load_snapshot() or {}
    # 2026-09-08 竞涨定格: 调度若延迟越过 9:30(窗口外)东财 f615 退化为 "-", bidChange
    # 以当日 9:25 定格竞价涨幅为准(防评分用现价涨幅, 名单漂移/失真)
    bid_chg_map = auction_snapshot.load_day_bid_change()
    # wait=True: 自动锁定需完整昨比(后台任务, 可等待; 用户请求路径走异步不阻塞)
    yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw], wait=True) or {}
    # 2026-09-08 昨日涨幅真实化: wait=True 已同步拉完日K, 直接读缓存(零额外请求)
    yesterday_chg_map = fetcher.fetch_yesterday_changes([s.get("f12") for s in raw]) or {}
    # 全市场评分一次
    try:
        # 2026-09-01 抢筹口径: 命中右视图竞价异动"竞价抢筹"代码集才打抢筹标
        qc_detail = kpl.get_qiangchou_detail()
        scored = scorer.score_all_stocks(raw, yesterday_map, snapshot_map, qiangchou_detail=qc_detail,
                                         day_bid_change=bid_chg_map,
                                         yesterday_chg_map=yesterday_chg_map,
                                         strengths=_load_strengths(raw))
    except Exception as e:
        log.error("auto_apply 全市场评分失败 err=%s", e, exc_info=True)
        return [], str(e)
    # 系统统一标准过滤一次(所有用户共享同一份结果, 2026-08-16 产品决策)
    f = _get_system_filter()
    result = scorer.apply_filters(scored, f)
    for it in result:
        it.pop("_raw", None)
    log.info("auto_apply 系统统一筛选完成 评分池=%d只 筛选后=%d只",
             len(scored), len(result))
    return result, ""


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
    log.info("auto_apply 结束 applied=%d skipped=%d failed=%d 耗时%.0fms",
             applied, skipped, failed, cost)
    return {"applied": applied, "skipped": skipped, "failed": failed,
            "total": len(user_ids), "cost_ms": int(cost)}


def trigger_auto_apply():
    """9:26 抢筹落库后由 auction_snapshot 调用: 后台线程执行, 立即返回"""
    return _run_in_background(auto_apply_all_users)
