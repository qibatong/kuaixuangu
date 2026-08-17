# -*- coding: utf-8 -*-
"""
认证路由: 登录 / 注册 / 修改密码 / 忘记密码(邮件) / 重置密码
============================================================
"""
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Body, Depends, Request

from ..core import config, logger
from ..db import database
from ..services import security, users
from .deps import client_ip, get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


@router.post("/api/login")
def api_login(request: Request, body: dict = Body(...)):
    login = str(body.get("login") or body.get("username") or "").strip()
    password = str(body.get("password") or "")
    remember = bool(body.get("remember"))   # 前端「记住我」→ 30 天 token
    user = users.find_user_by_login(login)
    ok = user is not None and security.verify_password(password, user.get("password_hash") or "")
    if not ok:
        log.warning("登录失败 login=%s ip=%s", login, client_ip(request))
        return jr({"ok": False, "msg": "用户名或密码错误"}, 401)
    # 邮箱认证拦截(2026-08-17): 新注册未验证邮箱的账号禁止登录
    if user is not None and not int(user.get("email_verified") or 1):
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
    return jr({"ok": True, "token": security.issue_token(user["id"], remember),
               "username": user["username"],
               "is_admin": 1 if user.get("is_admin") else 0,
               "expire_at": et,
               "member_level": users.get_member_level(user["id"]),
               "expired": 1 if (et and time.time() > et) else 0})


@router.post("/api/register")
def api_register(request: Request, body: dict = Body(...)):
    ip = client_ip(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    invite_code = str(body.get("invite_code") or "").strip().upper()
    phone = str(body.get("phone") or "").strip()
    email = str(body.get("email") or "").strip()
    # 基础格式校验前置: 无效请求(用户名/密码/手机/邮箱格式错)
    # 不计入防刷计数, 避免用户改几次格式就被误锁(2026-08-16 用户反馈)
    if not re.match(r"^[\u4e00-\u9fa5a-zA-Z0-9_]{2,20}$", username):
        return jr({"ok": False, "msg": "用户名需 2-20 位，支持中英文/数字/下划线"}, 400)
    if len(password) < 6:
        return jr({"ok": False, "msg": "密码至少 6 位"}, 400)
    # 防滥用(2026-08-16): 手机号+邮箱都必填, 堵"反复纯用户名注册绕过付费"漏洞
    if not phone:
        return jr({"ok": False, "msg": "请填写手机号"}, 400)
    if not email:
        return jr({"ok": False, "msg": "请填写邮箱"}, 400)
    if not users._is_phone(phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    if not users._is_email(email):
        return jr({"ok": False, "msg": "邮箱格式不正确"}, 400)
    # 基础格式校验通过后再限流 — 错误格式不计次防误锁
    if not security.register_allowed(ip):
        return jr({"ok": False, "msg": "注册过于频繁，请稍后再试"}, 429)
    # 同 IP 24h 注册数上限(防同 IP 批量刷号, 2026-08-17)
    if not security.register_ip_day_allowed(ip):
        return jr({"ok": False, "msg": "同一网络今日注册账号过多，请明天再试"}, 429)
    inviter = None
    if invite_code:
        inviter = users.find_user_by_invite_code(invite_code)
        if inviter is None:
            return jr({"ok": False, "msg": "邀请码无效，请找邀请你的人获取"}, 400)
    # 邀请码非必填: 不填直接注册(无邀请关系); 填了才校验有效性
    invited_by = inviter["id"] if inviter else None
    if users.find_user(username):
        return jr({"ok": False, "msg": "用户名已存在"}, 409)
    if phone and users.find_user_by_phone(phone):
        return jr({"ok": False, "msg": "该手机号已绑定其他账号"}, 409)
    if email and users.find_user_by_email(email):
        return jr({"ok": False, "msg": "该邮箱已绑定其他账号"}, 409)
    my_code = users.gen_unique_invite_code()
    # 注册环境: IP + UA(截断存储, 供同 IP 自邀识别/管理端追溯)
    ua = str(request.headers.get("user-agent") or "")[:200]
    try:
        uid = users.create_user(username, password, invited_by=invited_by,
                                invite_code=my_code, phone=phone or None,
                                email=email or None, register_ip=ip, register_ua=ua)
    except sqlite3.IntegrityError:
        log.warning("注册冲突 username=%s ip=%s", username, client_ip(request))
        return jr({"ok": False, "msg": "用户名或手机号/邮箱已被占用"}, 409)
    log.info("注册成功 uid=%s user=%s invited_by=%s ip=%s", uid, username, invited_by or "-", client_ip(request))
    # 邀请奖励: 每成功邀请一个新用户, 邀请人 +7 天使用时间(永久/VIP 老师跳过)
    # 防同 IP 小号刷: 被邀人与邀请人同 IP / 同 IP 已邀超限 → 不发奖励
    if invited_by:
        blocked, why = users.invite_reward_blocked(invited_by, ip)
        if blocked:
            log.warning("邀请奖励拦截 uid=%s inviter=%s ip=%s reason=%s", uid, invited_by, ip, why)
        else:
            new_et = users.grant_invite_reward(invited_by, days=config.INVITE_REWARD_DAYS)
            log.info("邀请奖励 uid=%s inviter=%s +%d天 新到期=%s", uid, invited_by,
                     config.INVITE_REWARD_DAYS, new_et or "-")
    # 邮箱认证(2026-08-17): 新注册强制验证, 验证通过后才能登录
    # SMTP 未配置时降级跳过(避免测试/未配邮件环境新用户卡死); 配置后自动强制
    email_verified = 1
    if users.smtp_configured():
        vcode = users.gen_verify_code()
        users.set_email_verify_code(uid, vcode)
        mail_ok = users.send_verify_email(email, username, vcode)
        log.info("邮箱验证邮件 uid=%s sent=%s to=%s", uid, mail_ok, email)
        email_verified = 0
    else:
        log.warning("SMTP 未配置, 跳过邮箱验证 uid=%s", uid)
    # 新用户默认 7 天会员试用
    u = users.find_user_by_id(uid)
    et = int(u.get("expire_at") or 0) if u else 0
    return jr({"ok": True, "token": security.issue_token(uid), "username": username,
               "uid": uid,
               "email_verified": email_verified,
               "expire_at": et,
               "member_level": users.get_member_level(uid),
               "expired": 1 if (et and time.time() > et) else 0,
               "msg": "注册成功，请查收邮箱完成验证后再登录"})


@router.post("/api/verify-email")
def api_verify_email(request: Request, body: dict = Body(...)):
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
def api_resend_verify(request: Request, body: dict = Body(...)):
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
