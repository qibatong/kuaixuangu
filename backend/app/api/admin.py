# -*- coding: utf-8 -*-
"""
管理端路由: 仅管理员可访问
==========================
- GET  /api/admin/users          用户列表(分页) + 统计
- POST /api/admin/users/expire   设置/续费账号到期时间
- GET  /api/admin/scoring        当前评分权重
- PUT  /api/admin/scoring        更新评分权重(保存后即时生效)
"""
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..core import logger
from ..services import scorer, settings, users
from .deps import client_ip, get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()

# 五项权重系数(key, 中文名, 说明)
W_KEYS = [
    ("w_bid", "竞价分", "竞价涨幅区间得分(如3%~5.5%为满分)"),
    ("w_activity", "活跃度", "竞价换手率/量比活跃度得分"),
    ("w_warn", "异动分", "封单/抢筹等异动信号得分"),
    ("w_market", "市值分", "流通市值越小分越高(小市值加分)"),
    ("w_yesterday", "昨日涨幅", "昨日涨幅处于健康区间得分"),
]
CONF_KEYS = [
    ("conf_warn_high", "强异动", "异动等级>=4 时置信度加成"),
    ("conf_turnover", "高换手", "竞价换手>=0.4 时置信度加成"),
    ("conf_bid", "温和竞价", "竞价涨幅2%~6.5%时置信度加成"),
]

# 盘中实时评分(spot) 权重系数(key, 中文名, 说明)
SPOT_W_KEYS = [
    ("w_chg", "实时涨幅", "盘中实时涨幅区间得分(3%~6%为满分, 过高=追高风险)"),
    ("w_vol_ratio", "量比", "量比放量确认得分(>=2 为满分)"),
    ("w_turnover", "换手率", "盘中换手活跃度得分(3%~15%为满分)"),
    ("w_seal", "封单强度", "涨停股封成比得分(>=2% 为满分; 非涨停=0分档)"),
    ("w_market", "市值分", "流通市值越小分越高(小市值加分)"),
    ("w_yesterday", "昨日涨幅", "昨日涨幅处于健康区间得分"),
]

# 盘中实时评分(spot) 置信度加成
SPOT_CONF_KEYS = [
    ("conf_seal_high", "强封单", "封成比>=2% 时置信度加成"),
    ("conf_vol_ratio", "显著放量", "量比>=2 时置信度加成"),
    ("conf_chg", "健康涨幅", "实时涨幅1.5%~6%时置信度加成"),
]


def get_admin(request: Request, uid: int = Depends(get_uid)):
    """管理员依赖: 未登录 401; 非管理员 403"""
    users.ensure_admin()   # 幂等初始化(ADMIN_USERNAME 或 id 最小用户)
    if not users.is_admin(uid):
        log.warning("非管理员访问管理端 uid=%s path=%s", uid, request.url.path)
        raise HTTPException(status_code=403, detail={"ok": False, "msg": "无管理员权限"})
    return uid


@router.get("/api/admin/users")
def api_admin_users(request: Request, uid: int = Depends(get_admin)):
    q = qs(request)
    try:
        page = max(1, int((q.get("page") or [1])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int((q.get("pageSize") or [20])[0])))
    except (TypeError, ValueError):
        page_size = 20
    keyword = (q.get("keyword") or [""])[0].strip()
    page_data = users.list_users_page(page, page_size, keyword)
    stats = users.user_stats()
    log.info("管理端用户列表 uid=%s page=%s total=%s", uid, page, page_data["total"])
    return jr({"ok": True, "stats": stats, **page_data})


# 时长档位(天): 周/月/季/年
EXPIRE_DURATIONS = {"week": 7, "month": 30, "quarter": 90, "year": 365}


@router.post("/api/admin/users/expire")
def api_admin_user_expire(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    """设置账号到期: 支持
    - {uid, duration: week|month|quarter|year} 从 max(现在,当前到期) 累加
    - {uid, days: N}                           累加 N 天(0=永久)
    - {uid, expire_at: "YYYY-MM-DD"}           直接设到期日期(北京当日 23:59:59)
    返回该用户最新到期时间戳。
    """
    target = int(body.get("uid") or 0)
    if target <= 0:
        return jr({"ok": False, "msg": "缺少 uid"}, 400)
    u = users.find_user_by_id(target)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)

    expire_at = body.get("expire_at")
    duration = body.get("duration")
    days = body.get("days")

    if expire_at:
        try:
            dt = datetime.strptime(str(expire_at), "%Y-%m-%d")
        except ValueError:
            return jr({"ok": False, "msg": "日期格式应为 YYYY-MM-DD"}, 400)
        bj = timezone(timedelta(hours=8))
        ts = int(dt.replace(tzinfo=bj).timestamp()) + 86399   # 北京当日 23:59:59
        users.set_expire(target, ts)
    elif duration in EXPIRE_DURATIONS:
        users.extend_expire(target, EXPIRE_DURATIONS[duration])
    elif days is not None:
        try:
            d = int(days)
        except (TypeError, ValueError):
            return jr({"ok": False, "msg": "days 应为整数"}, 400)
        if d <= 0:
            users.set_expire(target, 0)    # 0/负 = 永久
        else:
            users.extend_expire(target, d)
    else:
        return jr({"ok": False, "msg": "需要 duration / days / expire_at 之一"}, 400)

    row = users.find_user_by_id(target)
    log.info("管理端设置到期 uid=%s target=%s(%s) expire_at=%s",
             uid, target, u["username"], row.get("expire_at"))
    return jr({"ok": True, "msg": "已设置", "uid": target,
               "username": u["username"], "expire_at": row.get("expire_at")})


@router.post("/api/admin/users/reset-password")
def api_admin_user_reset_password(request: Request, body: dict = Body(...),
                                  uid: int = Depends(get_admin)):
    """管理员重置用户密码: {uid, password} 或 {username, password}
    密码至少 6 位; 不能重置自己的密码(防止误操作锁死管理员)。"""
    target = int(body.get("uid") or 0)
    username = str(body.get("username") or "").strip()
    new_pw = str(body.get("password") or "")

    if not new_pw or len(new_pw) < 6:
        return jr({"ok": False, "msg": "新密码至少 6 位"}, 400)
    if len(new_pw) > 64:
        return jr({"ok": False, "msg": "新密码过长(最多64位)"}, 400)

    if target > 0:
        u = users.find_user_by_id(target)
    elif username:
        u = users.find_user_by_login(username)
    else:
        return jr({"ok": False, "msg": "缺少 uid 或 username"}, 400)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    if int(u.get("id")) == uid:
        return jr({"ok": False, "msg": "不能重置自己的密码(请用修改密码功能)"}, 400)

    if not users.set_password(u["id"], new_pw):
        return jr({"ok": False, "msg": "重置失败"}, 500)
    log.info("管理端重置密码 uid=%s target=%s(%s) ip=%s", uid, u["id"], u["username"], client_ip(request))
    return jr({"ok": True, "msg": "密码已重置", "uid": u["id"], "username": u["username"]})


@router.get("/api/admin/scoring")
def api_admin_scoring_get(request: Request, uid: int = Depends(get_admin)):
    mode = (qs(request).get("mode") or ["auction"])[0]
    if mode not in ("auction", "spot"):
        return jr({"ok": False, "msg": "非法 mode"}, 400)
    cfg = scorer.get_scoring_cfg(mode=mode)
    if mode == "spot":
        return jr({"ok": True, "mode": "spot", "scoring": cfg,
                   "w_keys": SPOT_W_KEYS, "conf_keys": SPOT_CONF_KEYS})
    return jr({"ok": True, "mode": "auction", "scoring": cfg, "w_keys": W_KEYS, "conf_keys": CONF_KEYS})


@router.put("/api/admin/scoring")
def api_admin_scoring_put(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    mode = (qs(request).get("mode") or ["auction"])[0]
    if mode not in ("auction", "spot"):
        return jr({"ok": False, "msg": "非法 mode"}, 400)
    new = body.get("scoring")
    if not isinstance(new, dict) or not new:
        return jr({"ok": False, "msg": "缺少 scoring 配置"}, 400)
    err = _validate_scoring(new, mode)
    if err:
        return jr({"ok": False, "msg": err}, 400)
    if not settings.set("scoring_spot" if mode == "spot" else "scoring", new):
        return jr({"ok": False, "msg": "保存失败"}, 500)
    scorer.reload_scoring_cfg()
    log.info("管理端更新%s评分权重 uid=%s scoring=%s", "盘中" if mode == "spot" else "竞价", uid, new)
    return jr({"ok": True, "msg": "已保存并生效",
               "scoring": scorer.get_scoring_cfg(mode=mode)})


def _validate_scoring(new, mode="auction"):
    """校验权重/置信度/打分明细; mode=spot 用盘中因子表"""
    default_cfg = scorer.DEFAULT_SCORING if mode == "auction" else scorer.DEFAULT_SCORING_SPOT
    w_keys = W_KEYS if mode == "auction" else SPOT_W_KEYS
    conf_keys = CONF_KEYS if mode == "auction" else SPOT_CONF_KEYS
    w_sum = 0.0
    for k, _, _ in w_keys:
        try:
            v = float(new.get(k))
        except (TypeError, ValueError):
            return "权重 %s 必须是数字" % k
        if not (0 <= v <= 1):
            return "权重 %s 需在 0~1 之间" % k
        w_sum += v
    if abs(w_sum - 1.0) > 0.03:
        return "权重之和需约等于 1(当前 %.2f)" % w_sum
    for k, _, _ in conf_keys:
        try:
            v = float(new.get(k))
        except (TypeError, ValueError):
            return "置信度加成 %s 必须是数字" % k
        if not (0 <= v <= 30):
            return "置信度加成 %s 需在 0~30 之间" % k
    # 打分明细: 因子表, 每因子 buckets 为 [下限, 上限, 得分] 且 下限<上限, 得分 0~1
    factors = new.get("factors")
    if factors is not None:
        if not isinstance(factors, dict):
            return "打分明细格式错误"
        for fk, fv in factors.items():
            if fk not in default_cfg["factors"]:
                return "未知因子: %s" % fk
            if not isinstance(fv, dict) or not isinstance(fv.get("buckets"), list) or not fv["buckets"]:
                return "因子 %s 缺少有效的分档表" % fk
            try:
                dflt = float(fv.get("default", 0.1))
            except (TypeError, ValueError):
                return "因子 %s 的默认得分必须是数字" % fk
            if not (0 <= dflt <= 1):
                return "因子 %s 的默认得分需在 0~1 之间" % fk
            for b in fv["buckets"]:
                if not isinstance(b, (list, tuple)) or len(b) != 3:
                    return "因子 %s 分档必须为 [下限, 上限, 得分]" % fk
                try:
                    lo, hi, sc = float(b[0]), float(b[1]), float(b[2])
                except (TypeError, ValueError):
                    return "因子 %s 分档数值不合法" % fk
                if lo >= hi:
                    return "因子 %s 分档下限需小于上限" % fk
                if not (0 <= sc <= 1):
                    return "因子 %s 分档得分需在 0~1 之间" % fk
    return ""


# ---------- 全局默认筛选参数(管理员可调, 存 settings 表 key=default_filters) ----------
# 所有用户首次进入/未自定义偏好时使用的默认值(如默认竞价金额下限 1000万)
# 前端加载顺序: 后端默认值 > 用户偏好 > 前端内置默认
DEFAULT_FILTERS_DEFAULT = {
    "stSuspend": True, "limitUp": True, "bidGt": 7.0,
    "probLt": 65.0, "confLt": 65.0,
    "floatMvFloor": 30.0, "floatMvGt": 1000.0, "priceGt": 300.0,
    "bidAmtFloor": 1000.0,   # 诗人需求: 默认竞价金额下限 1000万(原3000)
}


def get_default_filters():
    """读取全局默认筛选参数(不存在则返回内置默认)"""
    cfg = settings.get("default_filters")
    if isinstance(cfg, dict):
        merged = dict(DEFAULT_FILTERS_DEFAULT)
        for k, v in cfg.items():
            if k in merged:
                merged[k] = v
        return merged
    return dict(DEFAULT_FILTERS_DEFAULT)


@router.get("/api/admin/defaults")
def api_admin_defaults_get(request: Request, uid: int = Depends(get_admin)):
    return jr({"ok": True, "defaults": get_default_filters()})


@router.put("/api/admin/defaults")
def api_admin_defaults_put(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    new = body.get("defaults")
    force = bool(body.get("force"))   # force=true: 保存后清除所有用户筛选偏好, 强制全量生效(保留主题)
    if not isinstance(new, dict) or not new:
        return jr({"ok": False, "msg": "缺少 defaults 配置"}, 400)
    # 只接受已知字段, 数字/布尔校验
    cur = get_default_filters()
    for k, v in new.items():
        if k not in cur:
            return jr({"ok": False, "msg": "未知字段: %s" % k}, 400)
        if isinstance(cur[k], bool) and not isinstance(v, bool):
            return jr({"ok": False, "msg": "%s 需为布尔值" % k}, 400)
        if isinstance(cur[k], (int, float)) and not isinstance(v, (int, float)):
            return jr({"ok": False, "msg": "%s 需为数字" % k}, 400)
        cur[k] = v
    if not settings.set("default_filters", cur):
        return jr({"ok": False, "msg": "保存失败"}, 500)
    cleared = 0
    msg = "全局默认筛选参数已更新"
    if force:
        cleared = users.clear_filter_prefs_all()
        msg += "，已强制重置 %d 个用户的筛选偏好" % cleared
    log.info("管理端更新全局默认筛选参数 uid=%s force=%s defaults=%s cleared=%d",
             uid, force, cur, cleared)
    return jr({"ok": True, "msg": msg, "defaults": cur, "forceCleared": cleared})
