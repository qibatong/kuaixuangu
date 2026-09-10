# -*- coding: utf-8 -*-
"""
认证路由: 登录 / 注册 / 修改密码 / 忘记密码(邮件) / 重置密码
============================================================
"""
import asyncio
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Body, Depends, Request

from ..core import config, logger
from ..db import database
from ..services import security, sms_verify, users
from .deps import client_ip, get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


@router.post("/api/login")
async def api_login(request: Request, body: dict = Body(...)):
    """2026-09-10 生产事故修复(全站无法登录 38 分钟):
    原为同步 def → FastAPI 放进 anyio 线程池执行(默认 40 槽/worker)。竞价结束大家
    集中刷新, 30s 级慢选股把 2 worker × 40 槽全占满, 登录只能在队列里排队(实测
    2,321,182ms ≈ 38 分钟), 表征就是"服务没挂但谁都登不进去"。
    改 async + asyncio.to_thread: 落到 event loop 的默认 executor, 与 anyio 池物理
    隔离 —— 慢请求再怎么堆积也挤不到登录, 且不用改动任何业务逻辑。
    ⚠️ 不要用 starlette 的 run_in_threadpool, 那个走的正是会被占满的 anyio 池。"""
    return await asyncio.to_thread(_login_sync, request, body)


def _login_sync(request: Request, body: dict):
    login = str(body.get("login") or body.get("username") or "").strip()
    password = str(body.get("password") or "")
    remember = bool(body.get("remember"))   # 前端「记住我」→ 30 天 token
    user = users.find_user_by_login(login)
    ok = user is not None and security.verify_password(password, user.get("password_hash") or "")
    if not ok:
        log.warning("登录失败 login=%s ip=%s", login, client_ip(request))
        return jr({"ok": False, "msg": "用户名或密码错误"}, 401)
    # 邮箱认证拦截(2026-08-17): 新注册未验证邮箱的账号禁止登录
    # 注意 email_verified=0 时不能用 `or 1` 兜底(0 是 falsy 会被当成 1)
    if user is not None and int(user.get("email_verified") or 0) != 1:
        log.warning("登录拦截-未验证邮箱 uid=%s login=%s", user["id"], login)
        return jr({"ok": False, "msg": "请先完成邮箱验证再登录",
                   "need_verify_email": True,
                   "uid": user["id"],
                   "email": user.get("email") or ""}, 401)
    # 单点登录: 密码校验通过后, 作废该用户所有旧 token, 强制只保留当前会话(防账号共享)
    revoked = security.revoke_user_tokens(user["id"])
    log.info("登录成功 uid=%s user=%s ip=%s remember=%s 已踢旧会话%d个",
             user["id"], user["username"], client_ip(request), remember, revoked)
    et = int(user.get("expire_at") or 0)
    token = security.issue_token(user["id"], remember)
    resp = jr({"ok": True, "token": token,
               "username": user["username"],
               "is_admin": 1 if user.get("is_admin") else 0,
               "expire_at": et,
               "member_level": users.get_member_level(user["id"]),
               "expired": 1 if (et and time.time() > et) else 0})
    # 同步写 cookie (2026-08-30 主人要求: 让浏览器直接地址栏访问 /aipick/* 也能鉴权, 不必依赖 ?token= URL)
    # Path=/ 保证 /aipick/ 也能带; HttpOnly 防 XSS 偷; SameSite=Lax 允许同站顶级 GET 导航
    # Max-Age 与 token 有效期一致: remember=30天, 否则 12小时
    resp.set_cookie(
        key="kx_token",
        value=token,
        max_age=(30 * 24 * 3600 if remember else 12 * 3600),
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,   # 兼容 HTTP 调试; 生产 HTTPS 浏览器也会发(同源时 Lax 放行 GET)
    )
    return resp


@router.post("/api/register")
def api_register(request: Request, body: dict = Body(...)):
    """注册已停止开放(合规要求 2026-08-25): 新用户仅能由管理员在后台开通。
    保留端点返回统一提示, 不创建任何用户。"""
    log.info("注册请求被拒绝(注册已停止) ip=%s", client_ip(request))
    return jr({"ok": False, "msg": "系统已停止开放注册，如需开通账号请联系管理员（微信 poet-1986）"}, 403)


@router.post("/api/verify-email")
async def api_verify_email(request: Request, body: dict = Body(...)):
    """同 api_login: 邮箱验证属登录链路(未验证账号正是靠它拿 token), 不能因慢请求
    堆积而卡死; 走独立 executor(详见 api_login 注释)"""
    return await asyncio.to_thread(_verify_email_sync, request, body)


def _verify_email_sync(request: Request, body: dict):
    """邮箱验证: 输入注册邮箱收到的 6 位验证码; 验证成功后直接返回 token(自动登录)"""
    uid = int(body.get("uid") or 0)
    code = str(body.get("code") or "").strip()
    if not uid or not code:
        return jr({"ok": False, "msg": "参数不完整"}, 400)
    ok, msg = users.verify_email_code(uid, code)
    if not ok:
        return jr({"ok": False, "msg": msg}, 400)
    u = users.find_user_by_id(uid)
    username = u["username"] if u else ""
    et = int(u.get("expire_at") or 0) if u else 0
    log.info("邮箱验证成功 uid=%s user=%s", uid, username)
    return jr({"ok": True, "msg": msg, "token": security.issue_token(uid),
               "username": username, "uid": uid,
               "email_verified": 1,
               "expire_at": et,
               "member_level": users.get_member_level(uid),
               "expired": 1 if (et and time.time() > et) else 0})


@router.post("/api/resend-verify")
async def api_resend_verify(request: Request, body: dict = Body(...)):
    """同 api_login: 属登录链路且要发邮件(慢 IO), 走独立 executor 更合适"""
    return await asyncio.to_thread(_resend_verify_sync, request, body)


def _resend_verify_sync(request: Request, body: dict):
    """重发邮箱验证码(5 分钟冷却, 每小时最多 3 次)"""
    uid = int(body.get("uid") or 0)
    user = users.find_user_by_id(uid) if uid else None
    if not user:
        return jr({"ok": False, "msg": "用户不存在"}, 400)
    if int(user.get("email_verified") or 0):
        return jr({"ok": True, "msg": "邮箱已验证，无需重复验证"})
    email = str(user.get("email") or "")
    if not email:
        return jr({"ok": False, "msg": "账号未绑定邮箱，请联系管理员"}, 400)
    ok, msg = security.verify_mail_allowed(email)
    if not ok:
        return jr({"ok": False, "msg": msg}, 429)
    vcode = users.gen_verify_code()
    users.set_email_verify_code(uid, vcode)
    mail_ok = users.send_verify_email(email, user["username"], vcode)
    if not mail_ok:
        return jr({"ok": False, "msg": "邮件发送失败，请联系管理员"}, 500)
    log.info("重发邮箱验证码 uid=%s to=%s", uid, email)
    return jr({"ok": True, "msg": "验证邮件已发送，请查收"})


@router.post("/api/change-password")
def api_change_password(request: Request, uid: int = Depends(get_uid),
                        body: dict = Body(...)):
    old_pw = str(body.get("old_password") or "")
    new_pw = str(body.get("new_password") or "")
    if len(new_pw) < 6:
        return jr({"ok": False, "msg": "新密码至少 6 位"}, 400)
    if old_pw == new_pw:
        return jr({"ok": False, "msg": "新密码不能与旧密码相同"}, 400)
    user = users.find_user_by_id(uid)
    if user is None or not security.verify_password(old_pw, user.get("password_hash") or ""):
        log.warning("改密失败: 旧密码错误 uid=%s", uid)
        return jr({"ok": False, "msg": "旧密码不正确"}, 400)
    conn = database.get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (security.hash_password(new_pw), uid))
    conn.commit()
    conn.close()
    # 改密后强制下线(所有会话失效, 需重新登录)
    security.revoke_user_tokens(uid)
    log.info("改密成功 uid=%s user=%s", uid, user.get("username"))
    return jr({"ok": True, "msg": "密码已修改，请重新登录"})


@router.post("/api/forgot")
def api_forgot(request: Request, body: dict = Body(...)):
    email = str(body.get("email") or "").strip().lower()
    if not users._is_email(email):
        return jr({"ok": False, "msg": "邮箱格式不正确"}, 400)
    if not users.smtp_configured():
        return jr({"ok": False, "msg": "邮件服务未配置，请联系管理员"}, 500)
    if not security.reset_mail_allowed(email):
        return jr({"ok": False, "msg": "请求过于频繁，请 1 小时后再试"}, 429)
    msg = "如果该邮箱已绑定账号，重置邮件已发送，请查收"
    user = users.find_user_by_email(email)
    if user is None:
        return jr({"ok": True, "msg": msg})
    now = time.time()
    token = secrets.token_urlsafe(32)
    conn = database.get_conn()
    conn.execute("INSERT INTO reset_tokens (user_id, token, created_at, expires_at, used) VALUES (?,?,?,?,0)",
                 (user["id"], token, int(now), int(now) + config.RESET_TTL))
    conn.commit()
    conn.close()
    scheme = "https" if (request.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
    host = request.headers.get("Host") or "127.0.0.1"
    reset_url = "%s://%s/?reset=%s" % (scheme, host, token)
    try:
        users.send_reset_email(email, reset_url, user["username"])
    except Exception as e:
        log.error("重置邮件发送失败 email=%s err=%s", email, e)
        return jr({"ok": False, "msg": "邮件发送失败：%s" % e}, 500)
    log.info("重置邮件已发送 email=%s user=%s", email, user["username"])
    return jr({"ok": True, "msg": msg})


@router.post("/api/reset")
def api_reset(request: Request, body: dict = Body(...)):
    token = str(body.get("token") or "").strip()
    password = str(body.get("password") or "")
    if len(password) < 6:
        return jr({"ok": False, "msg": "新密码至少 6 位"}, 400)
    conn = database.get_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM reset_tokens WHERE token=? AND used=0", (token,)).fetchone()
    if row is None:
        conn.close()
        log.warning("重置链接无效/已使用 ip=%s", client_ip(request))
        return jr({"ok": False, "msg": "重置链接无效或已使用，请重新申请"}, 400)
    if time.time() > row["expires_at"]:
        conn.execute("UPDATE reset_tokens SET used=1 WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()
        log.warning("重置链接已过期 user_id=%s", row["user_id"])
        return jr({"ok": False, "msg": "重置链接已过期，请重新申请"}, 400)
    user_id = row["user_id"]
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (security.hash_password(password), user_id))
    conn.execute("UPDATE reset_tokens SET used=1 WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    # 踢下线: 使该用户所有已签发 token 失效
    security.revoke_user_tokens(user_id)
    log.info("密码重置成功 user_id=%s", user_id)
    return jr({"ok": True, "msg": "密码已重置，请用新密码登录"})


@router.post("/api/forgot-phone/send")
def api_forgot_phone_send(request: Request, body: dict = Body(...)):
    """找回密码-短信验证码: 输入已绑定手机号 → 发送验证码(scene=forgot).
    未绑定手机号的账号不发送(省短信费), 提示联系管理员人工处理"""
    phone = str(body.get("phone") or "").strip()
    if not re.match(r"^1[3-9]\d{9}$", phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    user = users.find_user_by_phone(phone)
    if user is None:
        return jr({"ok": False, "msg": "该手机号未绑定账号，无法自助找回，请联系管理员（微信 poet-1986）"}, 404)
    ip = client_ip(request)
    allowed, reason = sms_verify.can_send(phone, ip, config.SMS_SEND_INTERVAL)
    if not allowed:
        return jr({"ok": False, "msg": reason}, 429)
    try:
        ok, msg = sms_verify.send_code(phone, scene="forgot",
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
    log.info("找回密码短信已发送 phone=%s user=%s", phone, user["username"])
    return jr({"ok": True, "msg": "验证码已发送，请查收"})


@router.post("/api/reset-by-phone")
def api_reset_by_phone(request: Request, body: dict = Body(...)):
    """找回密码-短信验证码校验+重置(一体). body: {phone, code, new_password}
    验证码由阿里云服务端闭环校验(scene=forgot, 5 分钟有效), 通过后直接改密 + 踢下线
    防重放: 校验通过后本地标记消费, 同验证码 5 分钟内不可二次使用"""
    phone = str(body.get("phone") or "").strip()
    code = str(body.get("code") or "").strip()
    new_pw = str(body.get("new_password") or "")
    if not re.match(r"^1[3-9]\d{9}$", phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    if not code or not code.isdigit():
        return jr({"ok": False, "msg": "验证码格式不正确"}, 400)
    if len(new_pw) < 6:
        return jr({"ok": False, "msg": "新密码至少 6 位"}, 400)
    user = users.find_user_by_phone(phone)
    if user is None:
        return jr({"ok": False, "msg": "该手机号未绑定账号"}, 404)
    if sms_verify.is_consumed(phone, "forgot"):
        return jr({"ok": False, "msg": "验证码已使用，请重新获取"}, 400)
    try:
        ok, msg = sms_verify.check_code(phone, code, scene="forgot")
    except sms_verify.SmsNotConfigured as e:
        log.warning("短信校验未配置: %s", e)
        return jr({"ok": False, "msg": "短信服务未配置, 请联系管理员"}, 503)
    except Exception as e:
        log.error("短信校验异常 phone=%s err=%s", phone, e)
        return jr({"ok": False, "msg": "校验失败, 请稍后再试"}, 500)
    if not ok:
        return jr({"ok": False, "msg": "验证码错误或已过期"}, 400)
    # 校验通过 → 改密 + 踢下线 + 标记验证码已消费(防重放)
    conn = database.get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (security.hash_password(new_pw), user["id"]))
    conn.commit()
    conn.close()
    security.revoke_user_tokens(user["id"])
    sms_verify.mark_consumed(phone, "forgot", ttl=config.SMS_VALID_MIN * 60)
    log.info("手机短信找回密码成功 uid=%s user=%s", user["id"], user["username"])
    return jr({"ok": True, "msg": "密码已重置，请用新密码登录"})


@router.post("/api/forgot/check")
def api_forgot_check(request: Request, body: dict = Body(...)):
    """忘记密码辅助: 输入用户名/手机号, 返回是否绑定了邮箱
    若未绑定邮箱 → 提示联系管理员(微信号 poet-1986)人工处理"""
    login = str(body.get("login") or "").strip()
    if not login:
        return jr({"ok": False, "msg": "请输入用户名或手机号"}, 400)
    user = users.find_user_by_login(login)
    if user is None:
        # 未找到账号: 也提示联系管理员(避免枚举账号存在性)
        return jr({"ok": False, "has_email": False,
                   "msg": "未找到该账号或未绑定邮箱，请联系管理员人工处理（管理员微信号：poet-1986）"}, 404)
    has_email = bool((user.get("email") or "").strip())
    if not has_email:
        return jr({"ok": False, "has_email": False,
                   "msg": "该账号未绑定邮箱，无法自助找回密码，请联系管理员人工处理（管理员微信号：poet-1986）"}, 400)
    # 已绑定邮箱: 返回脱敏邮箱提示用户确认
    email = user["email"]
    local, dom = email.split("@", 1) if "@" in email else (email, "")
    masked = (local[:2] + "***@" + dom) if len(local) > 2 else ("**@" + dom)
    return jr({"ok": True, "has_email": True, "email": masked,
               "msg": "该账号已绑定邮箱 %s，请在忘记密码页输入该邮箱" % masked})
