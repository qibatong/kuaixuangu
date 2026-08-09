# -*- coding: utf-8 -*-
"""
邀请路由: 查看/刷新邀请码
=========================
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import users
from .deps import get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/invite")
def api_invite(request: Request, uid: int = Depends(get_uid)):
    code = users.ensure_invite_code(uid)
    if code is None:
        log.error("邀请码生成失败 uid=%s", uid)
        return jr({"ok": False, "msg": "邀请码生成失败, 请重试"}, 500)
    invitees = users.list_invitees(uid)
    log.info("邀请信息查询 uid=%s 已邀请%d人", uid, len(invitees))
    return jr({"ok": True, "invite_code": code,
               "invited_count": len(invitees), "invitees": invitees})


@router.post("/api/invite/refresh")
def api_invite_refresh(request: Request, uid: int = Depends(get_uid)):
    code = users.refresh_invite_code(uid)
    if code is None:
        log.error("邀请码刷新失败 uid=%s", uid)
        return jr({"ok": False, "msg": "邀请码生成失败, 请重试"}, 500)
    log.info("邀请码刷新 uid=%s 新码=%s", uid, code)
    return jr({"ok": True, "invite_code": code})
