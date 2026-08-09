# -*- coding: utf-8 -*-
"""
管理端路由: 仅管理员可访问
==========================
- GET  /api/admin/users    用户列表(分页) + 统计
- GET  /api/admin/scoring  当前评分权重
- PUT  /api/admin/scoring  更新评分权重(保存后即时生效)
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..core import logger
from ..services import scorer, settings, users
from .deps import get_uid, jr, qs

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


@router.get("/api/admin/scoring")
def api_admin_scoring_get(request: Request, uid: int = Depends(get_admin)):
    cfg = scorer.get_scoring_cfg()
    return jr({"ok": True, "scoring": cfg, "w_keys": W_KEYS, "conf_keys": CONF_KEYS})


@router.put("/api/admin/scoring")
def api_admin_scoring_put(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    new = body.get("scoring")
    if not isinstance(new, dict) or not new:
        return jr({"ok": False, "msg": "缺少 scoring 配置"}, 400)
    # 五项权重: 0~1 且合计≈1; 置信度加成: 0~30
    err = _validate_scoring(new)
    if err:
        return jr({"ok": False, "msg": err}, 400)
    if not settings.set("scoring", new):
        return jr({"ok": False, "msg": "保存失败"}, 500)
    scorer.reload_scoring_cfg()
    log.info("管理端更新评分权重 uid=%s scoring=%s", uid, new)
    return jr({"ok": True, "msg": "已保存并生效", "scoring": scorer.get_scoring_cfg()})


def _validate_scoring(new):
    w_sum = 0.0
    for k, _, _ in W_KEYS:
        try:
            v = float(new.get(k))
        except (TypeError, ValueError):
            return "权重 %s 必须是数字" % k
        if not (0 <= v <= 1):
            return "权重 %s 需在 0~1 之间" % k
        w_sum += v
    if abs(w_sum - 1.0) > 0.03:
        return "五项权重之和需约等于 1(当前 %.2f)" % w_sum
    for k, _, _ in CONF_KEYS:
        try:
            v = float(new.get(k))
        except (TypeError, ValueError):
            return "置信度加成 %s 必须是数字" % k
        if not (0 <= v <= 30):
            return "置信度加成 %s 需在 0~30 之间" % k
    return ""
