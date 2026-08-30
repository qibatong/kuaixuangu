# -*- coding: utf-8 -*-
"""
短信验证码路由 (2026-08-30 阿里云号码认证·短信认证)
====================================================
  POST /api/sms/send    发送验证码 {phone, scene?}   → 限流(同号60s + 同IP)
  POST /api/sms/verify  校验验证码 {phone, code}      → 服务端闭环校验(系统生成码)

未配置 AK/签名/模板 时返回 503 + msg 提示, 不影响其他接口。
"""
import re

from fastapi import APIRouter, Depends, Request
from fastapi.params import Body

from ..core import config, logger
from ..db import database
from .deps import get_uid, jr
from ..services import sms_verify

log = logger.get_logger(__name__)
router = APIRouter()

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


@router.post("/api/sms/send")
def api_sms_send(request: Request, body: dict = Body(...), uid: int = Depends(get_uid)):
    """发送验证码. body: {phone, scene?('register'|'reset'|默认'')}
    返回 {ok, msg}; 同手机号 SMS_SEND_INTERVAL 秒内仅 1 次"""
    phone = str(body.get("phone") or "").strip()
    if not _PHONE_RE.match(phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    ip = request.client.host if request.client else ""
    allowed, reason = sms_verify.can_send(phone, ip, config.SMS_SEND_INTERVAL)
    if not allowed:
        return jr({"ok": False, "msg": reason}, 429)
    scene = str(body.get("scene") or "")[:20]
    try:
        ok, msg = sms_verify.send_code(phone, scene=scene,
                                       interval=config.SMS_SEND_INTERVAL,
                                       valid_time=config.SMS_VALID_MIN)
    except sms_verify.SmsNotConfigured as e:
        log.warning("短信发送未配置: %s", e)
        return jr({"ok": False, "msg": "短信服务未配置, 请联系管理员"}, 503)
    except Exception as e:
        log.error("短信发送异常 phone=%s err=%s", phone, e)
        return jr({"ok": False, "msg": "发送失败, 请稍后再试"}, 500)
    if not ok:
        return jr({"ok": False, "msg": "发送失败: %s" % msg}, 500)
    return jr({"ok": True, "msg": "验证码已发送"})


@router.post("/api/sms/verify")
def api_sms_verify(request: Request, body: dict = Body(...), uid: int = Depends(get_uid)):
    """校验验证码. body: {phone, code}
    返回 {ok, msg}; 仅校验系统自动生成的验证码(服务端闭环, 不本地存码)"""
    phone = str(body.get("phone") or "").strip()
    code = str(body.get("code") or "").strip()
    if not _PHONE_RE.match(phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    if not code or not code.isdigit():
        return jr({"ok": False, "msg": "验证码格式不正确"}, 400)
    try:
        ok, msg = sms_verify.check_code(phone, code)
    except sms_verify.SmsNotConfigured as e:
        log.warning("短信校验未配置: %s", e)
        return jr({"ok": False, "msg": "短信服务未配置, 请联系管理员"}, 503)
    except Exception as e:
        log.error("短信校验异常 phone=%s err=%s", phone, e)
        return jr({"ok": False, "msg": "校验失败, 请稍后再试"}, 500)
    if not ok:
        return jr({"ok": False, "msg": "验证码错误或已过期"}, 400)
    return jr({"ok": True, "msg": "验证通过"})
