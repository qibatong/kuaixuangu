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
        # 合并保存: 只覆盖本次提交的字段, 保留已有偏好(筛选/主题互不覆盖)
        merged = dict(users.get_prefs(uid) or {})
        merged.update(settings)
        users.save_prefs(uid, merged)
    except (TypeError, ValueError):
        log.warning("偏好保存序列化失败 uid=%s", uid)
        return jr({"ok": False, "msg": "参数错误"}, 400)
    log.info("保存筛选偏好 uid=%s 字段数%d", uid, len(merged))
    return jr({"ok": True, "msg": "筛选偏好已保存"})


# ---------- 个人资料 ----------
@router.get("/api/profile")
def api_get_profile(request: Request, uid: int = Depends(get_uid)):
    """个人资料: 手机号/邮箱/微信名/备注(脱敏邮箱前缀)"""
    u = users.find_user_by_id(uid)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    # 邮箱脱敏: abc***@domain.com(避免接口层泄露完整邮箱)
    email = u.get("email") or ""
    if email and "@" in email:
        local, dom = email.split("@", 1)
        email = (local[:2] + "***@" + dom) if len(local) > 2 else ("**@" + dom)
    return jr({"ok": True, "profile": {
        "username": u.get("username"),
        "phone": u.get("phone") or "",
        "email": email,
        "wx_name": u.get("wx_name") or "",
        "remark": u.get("remark") or "",
        "member_level": u.get("member_level") or 0,
        "expire_at": u.get("expire_at") or 0,
    }})


@router.post("/api/profile")
def api_update_profile(request: Request, uid: int = Depends(get_uid),
                       body: dict = Body(...)):
    """修改个人资料: 手机号/邮箱/微信名/备注(均可选, 只更新提供的字段)"""
    phone = body.get("phone")
    email = body.get("email")
    wx_name = body.get("wx_name")
    remark = body.get("remark")
    ok, msg = users.update_profile(uid, phone=phone, email=email,
                                   wx_name=wx_name, remark=remark)
    if not ok:
        return jr({"ok": False, "msg": msg}, 400)
    log.info("更新个人资料 uid=%s phone=%s email=%s wx=%s", uid,
             bool(phone), bool(email), bool(wx_name))
    return jr({"ok": True, "msg": msg})
