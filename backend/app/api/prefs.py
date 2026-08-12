# -*- coding: utf-8 -*-
"""
筛选偏好路由: 账号级跨设备偏好 (GET/POST /api/prefs)
====================================================
"""
from fastapi import APIRouter, Body, Depends, Request

from ..core import logger
from ..services import users
from .deps import get_uid, jr
from .admin import get_default_filters

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/prefs/defaults")
def api_get_default_filters(request: Request, uid: int = Depends(get_uid)):
    """全局默认筛选参数(管理员可调, 所有用户未自定义时使用)"""
    return jr({"ok": True, "defaults": get_default_filters()})


@router.get("/api/prefs")
def api_get_prefs(request: Request, uid: int = Depends(get_uid)):
    prefs = users.get_prefs(uid)
    log.info("读取筛选偏好 uid=%s %s", uid, "有" if prefs else "空")
    return jr({"ok": True, "settings": prefs})


@router.post("/api/prefs")
def api_save_prefs(request: Request, uid: int = Depends(get_uid),
                   body: dict = Body(...)):
    settings = body.get("settings")
    if not isinstance(settings, dict):
        log.warning("偏好保存参数错误 uid=%s", uid)
        return jr({"ok": False, "msg": "参数错误"}, 400)
    try:
        users.save_prefs(uid, settings)
    except (TypeError, ValueError):
        log.warning("偏好保存序列化失败 uid=%s", uid)
        return jr({"ok": False, "msg": "参数错误"}, 400)
    log.info("保存筛选偏好 uid=%s 字段数%d", uid, len(settings))
    return jr({"ok": True, "msg": "筛选偏好已保存"})
