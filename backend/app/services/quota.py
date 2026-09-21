# -*- coding: utf-8 -*-
"""
免费用户每日配额(2026-09-21)
==============================
方案 B: CacheStore 固定窗口原子自增。
  - key:  quota:{feature}:{uid}:{date}   (date 用北京日期, 每日 0 点自然重置)
  - TTL:  86400  + 少量冗余, 固定窗口不滑动(简单、可预期、对用户友好)
  - 会员(member_level>=1)/管理员: 直接放行, 不计数
  - 签到加成: 每个 feature 可叠加当日 bonus(key: quota:bonus:{feature}:{uid}:{date})

★ 关键设计:
  1. **不在请求内部抛 429 打断业务**, 由调用方用的 `quota_guard` 依赖统一抛, 保证
     响应结构一致(code=quota_exceeded), 前端可根据 code 弹开通引导。
  2. **去重**: 同一用户同 feature 在 QUOTA_DEDUP_SECONDS 内的重复请求只计一次。
     前端一次页面加载可能并发打多个接口(如快照 + 列表), 不去重会瞬间烧掉配额,
     这是"看起来只有 3 次却马上用完"的常见投诉来源。
  3. **Redis 与 SQLite 行为一致**: CacheStore.incr 对两种后端都是"不存在则建, 存在则 +1",
     SQLite 走事务加锁, Redis 走 INCR 原子命令, 语义一致。异常时按"不放行但也不计数"
     保守处理 —— 宁可短暂拦住免费用户, 也不能因存储故障把配额体系彻底放开。
"""
import time

from ..core import config, logger
from ..db import database
from . import security
from .cache_store import store

log = logger.get_logger(__name__)

# feature -> config 属性名(每日基础额度)
_FEATURE_LIMITS = {
    "picker": "QUOTA_PICKER_DAILY",
    "aipick": "QUOTA_AIPICK_DAILY",
    "auction": "QUOTA_AUCTION_DAILY",
}

# feature -> 中文名(给前端提示用)
FEATURE_LABEL = {
    "picker": "选股快照",
    "aipick": "AI 预测",
    "auction": "竞价异动",
}


def _bj_date(ts=None):
    """北京日期字符串 YYYY-MM-DD(服务器走 UTC)"""
    return time.strftime("%Y-%m-%d", time.gmtime((ts or time.time()) + 8 * 3600))


def _limit_key(feature, uid, date=None):
    return "quota:%s:%s:%s" % (feature, uid, date or _bj_date())


def _bonus_key(feature, uid, date=None):
    return "quota:bonus:%s:%s:%s" % (feature, uid, date or _bj_date())


def base_limit(feature):
    """该功能的每日基础额度(免费用户)"""
    attr = _FEATURE_LIMITS.get(feature)
    return int(getattr(config, attr, 0)) if attr else 0


def bonus_used(uid, feature, date=None):
    """当日签到等途径获得的额外额度"""
    try:
        v = store.get(_bonus_key(feature, uid, date))
        return int(v or 0)
    except Exception:
        return 0


def add_bonus(uid, feature, n, ttl=None, date=None):
    """给某功能加当日额度(签到奖励用). 返回加完后总额"""
    key = _bonus_key(feature, uid, date)
    try:
        for _ in range(int(n)):
            store.incr(key, ttl=ttl or 86400 + 3600)
        return bonus_used(uid, feature, date)
    except Exception as e:
        log.warning("配额加成失败 uid=%s feature=%s n=%s err=%s", uid, feature, n, e)
        return 0


def limit_of(uid, feature, date=None):
    """该用户该功能今日总额度 = 基础 + 加成"""
    return base_limit(feature) + bonus_used(uid, feature, date)


def used_of(uid, feature, date=None):
    """今日已用次数"""
    try:
        v = store.get(_limit_key(feature, uid, date))
        return int(v or 0)
    except Exception:
        return 0


def is_privileged(uid):
    """会员/管理员不受配额限制"""
    try:
        return security._is_privileged_user(uid)
    except Exception:
        return False


def peek(uid, feature):
    """查询配额状态(不消耗), 供前端展示剩余次数。
    返回 {feature, label, limit, used, remain, bonus, privileged}"""
    lbl = FEATURE_LABEL.get(feature, feature)
    if is_privileged(uid):
        return {"feature": feature, "label": lbl, "limit": -1, "used": 0,
                "remain": -1, "bonus": 0, "privileged": True}
    lim = limit_of(uid, feature)
    used = used_of(uid, feature)
    return {"feature": feature, "label": lbl, "limit": lim, "used": used,
            "remain": max(0, lim - used), "bonus": bonus_used(uid, feature),
            "privileged": False}


def consume(uid, feature, dedup=True):
    """消耗一次配额. 返回 (allowed, info)
    allowed=False 时 info 里带 limit/used, 调用方据此给 429。
    dedup=True 时同 feature 在 QUOTA_DEDUP_SECONDS 内重复调用不重复计数(返回当前值)。"""
    if is_privileged(uid):
        return True, {"feature": feature, "limit": -1, "used": 0, "remain": -1, "privileged": True}
    lim = limit_of(uid, feature)
    if lim <= 0:
        # 配置为 0 视为该功能不对免费用户开放
        return False, {"feature": feature, "limit": 0, "used": 0, "remain": 0, "privileged": False}
    dedup_sec = int(getattr(config, "QUOTA_DEDUP_SECONDS", 10))
    key = _limit_key(feature, uid)
    if dedup and dedup_sec > 0:
        try:
            # setnx 成功 = 本窗口第一条, 需要计数; 失败 = 窗口内已有请求, 取当前值即可
            first = store.setnx("quota:dedup:%s:%s" % (uid, feature), 1, ttl=dedup_sec)
        except Exception as e:
            log.warning("配额去重判断异常 uid=%s feature=%s err=%s", uid, feature, e)
            first = True
        if first:
            used = _incr(key)
        else:
            used = used_of(uid, feature)
    else:
        used = _incr(key)
    ok = used <= lim
    return ok, {"feature": feature, "limit": lim, "used": used,
                "remain": max(0, lim - used), "privileged": False}


def _incr(key):
    """自增当日计数. 失败时返回极大值(保守: 拦住)"""
    try:
        return int(store.incr(key, ttl=86400 + 3600))
    except Exception as e:
        log.warning("配额自增失败 key=%s err=%s", key, e)
        return 10 ** 9


def batch_peek(uid, features=None):
    """一次查询多个功能配额(前端顶部展示)"""
    feats = features or list(_FEATURE_LIMITS.keys())
    return {f: peek(uid, f) for f in feats}


def reset_user(uid, feature=None, bonus=True):
    """重置用户当日配额(管理端/测试用). bonus=False 则只清使用量不动加成"""
    feats = [feature] if feature else list(_FEATURE_LIMITS.keys())
    for f in feats:
        try:
            store.delete(_limit_key(f, uid))
            store.delete("quota:dedup:%s:%s" % (uid, f))
            if bonus:
                store.delete(_bonus_key(f, uid))
        except Exception as e:
            log.warning("重置配额失败 uid=%s feature=%s err=%s", uid, f, e)


def quota_stats(days=1):
    """管理端: 配额相关统计. 汇总今日签到给免费用户加了多少额度"""
    try:
        conn = database.get_conn()
        today = _bj_date()
        rows = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(reward),0) s FROM user_checkin WHERE date=?",
            (today,)).fetchall()
        conn.close()
        r = rows[0] if rows else (0, 0)
        return {"checkin_today": int(r[0] or 0), "bonus_granted_today": int(r[1] or 0),
                "limits": {f: base_limit(f) for f in _FEATURE_LIMITS},
                "checkin_bonus_per_day": int(getattr(config, "QUOTA_CHECKIN_BONUS", 3))}
    except Exception as e:
        log.warning("配额统计失败 err=%s", e)
        return {"checkin_today": 0, "bonus_granted_today": 0, "limits": {}, "checkin_bonus_per_day": 0}
