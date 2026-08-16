# -*- coding: utf-8 -*-
"""
9:26 自动应用服务 (2026-08-16)
============================
背景: 诗人反馈用户"打开着应用但没点应用按钮", 当天历史为空。
方案: 9:26 抢筹快照落库后, 自动给所有非管理员用户跑一次选股
      并以 auto_applied=True 标记写入 batches 表。
      用户主动 lock/filter 触发的批次 auto_applied=False, 优先展示。

设计要点:
- 串行执行(避免并发压垮 KPL 配额)
- 单用户失败不影响其他人
- 跳过当天已应用的用户(避免覆盖主动选择)
- 过期/管理员/VIP 老师(member_level=2 且 expire_at=0) 自动跳过
- 用户偏好 filter_prefs 解析失败时回退全局默认
"""
import time

from ..core import logger
from ..services import auction_snapshot, fetcher, history, scorer, users
from ..api import admin as admin_api   # 用 get_default_filters
log = logger.get_logger(__name__)


def _today_bj():
    """北京当日 YYYY-MM-DD"""
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _user_already_applied_today(uid, bdate):
    """用户当天是否已有批次记录(无论主动/自动); 用于跳过避免重复"""
    conn = history._conn() if hasattr(history, "_conn") else None
    # 直接查库
    from ..db import database
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM batches WHERE user_id=? AND batch_date=?",
            (uid, bdate)).fetchone()
        return (row[0] or 0) > 0
    finally:
        conn.close()


def _get_user_filter(uid):
    """解析用户偏好; 失败/为空回退全局默认"""
    prefs = users.get_prefs(uid) or {}
    defaults = admin_api.get_default_filters()
    # 只取筛选字段(避免 markets 等被 prefs 错误覆盖)
    for k in defaults:
        if k == "markets":
            continue
        if k in prefs and prefs[k] is not None:
            defaults[k] = prefs[k]
    # markets 不在 prefs 里, 用默认 ["SH", "SZ", "BJ"]
    defaults.setdefault("markets", ["SH", "SZ", "BJ"])
    return defaults


def _is_user_active(uid):
    """用户是否活跃 (未过期 且 非管理员 且 非 VIP 老师自动跳过)"""
    u = users.find_user_by_id(uid)
    if not u:
        return False, "用户不存在"
    if int(u.get("is_admin") or 0):
        return False, "管理员"
    expire = int(u.get("expire_at") or 0)
    if expire and expire < int(time.time()):
        return False, "账号已过期"
    return True, ""


def auto_apply_one_user(uid, raw, yesterday_map, snapshot_map, bdate):
    """给单个用户跑一次选股 + 落库(标记 auto_applied=True)
    raw/yesterday_map/snapshot_map 由调用方预热(避免每用户重复拉)
    返回 batch_id 或 None"""
    ok, reason = _is_user_active(uid)
    if not ok:
        log.info("auto_apply 跳过 uid=%s 原因=%s", uid, reason)
        return None
    if _user_already_applied_today(uid, bdate):
        log.info("auto_apply 跳过 uid=%s 原因=今天已有批次", uid)
        return None
    f = _get_user_filter(uid)
    try:
        result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map)
    except Exception as e:
        log.warning("auto_apply 评分失败 uid=%s err=%s", uid, e)
        return None
    bid = history.save_batch(uid, "lock", result, f, auto_applied=True)
    log.info("auto_apply 完成 uid=%s 返回%d只 batch=%s", uid, len(result), bid)
    return bid


def auto_apply_all_users(max_users=None):
    """9:26 抢筹快照落库后调用: 给所有活跃用户自动应用一次
    max_users: 限制本次处理用户数 (调试/分批用, 默认 None=不限)
    返回 {applied: int, skipped: int, failed: int}"""
    t0 = time.time()
    bdate = _today_bj()
    # 复用当前行情缓存(9:25 撮合时已拉取)
    raw, err = fetcher.ensure_cache("filter", ("hs", "bj"), before930=True)
    if not raw:
        log.warning("auto_apply 行情缓存缺失 err=%s, 跳过本轮", err)
        return {"applied": 0, "skipped": 0, "failed": 0, "error": str(err)}
    snapshot_map = auction_snapshot.load_snapshot() or {}
    yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw]) or {}
    # 候选用户: 活跃 + 当天未应用
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
    for uid in user_ids:
        try:
            r = auto_apply_one_user(uid, raw, yesterday_map, snapshot_map, bdate)
            if r:
                applied += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            log.warning("auto_apply 单用户失败 uid=%s err=%s", uid, e)
    cost = (time.time() - t0) * 1000
    log.info("auto_apply 结束 applied=%d skipped=%d failed=%d 耗时%.0fms",
             applied, skipped, failed, cost)
    return {"applied": applied, "skipped": skipped, "failed": failed,
            "total": len(user_ids), "cost_ms": int(cost)}
