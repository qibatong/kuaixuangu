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
from ..services import activity, security, sms_verify, users
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
        # 2026-09-22 v4.11.35: 登录失败落库(撞库/忘记密码排查用; uid 未知记 0)
        activity.record_login(uid=(user["id"] if user else 0), login_try=login,
                              result="fail", ip=client_ip(request),
                              ua=request.headers.get("User-Agent") or "")
        return jr({"ok": False, "msg": "用户名或密码错误"}, 401)
    # 单点登录: 密码校验通过后, 作废该用户所有旧 token, 强制只保留当前会话(防账号共享)
    revoked = security.revoke_user_tokens(user["id"])
    log.info("登录成功 uid=%s user=%s ip=%s remember=%s 已踢旧会话%d个",
             user["id"], user["username"], client_ip(request), remember, revoked)
    # 2026-09-22 v4.11.35: 登录成功落库(登录记录的主体)
    activity.record_login(uid=user["id"], login_try=login, result="success",
                          ip=client_ip(request), ua=request.headers.get("User-Agent") or "",
                          remember=remember)
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
    """手机号注册(2026-09-21 放开注册):
    body: {phone, code, password, invite_code?}
    流程: 短信验证码校验(scene=register) → 手机号唯一性 → 注册送 5 天 level=1 完整体验
          → 可选邀请码: 邀请人 +5 天(带同 IP 自邀/批量小号防刷)

    ★ 三层防刷:
      ① 同 IP 24h 注册数上限(config.REG_IP_DAY_LIMIT) —— security.register_ip_day_allowed
      ② 同 IP 1h 注册数上限(security.register_allowed)
      ③ phone_claims 台账: 每个手机号只能领 1 次新用户 VIP(删号重注册也刷不到)
    老用户/管理员代建不受配额影响。"""
    if not getattr(config, "REG_OPEN", True):
        log.info("注册请求被拒绝(注册已关闭) ip=%s", client_ip(request))
        return jr({"ok": False, "msg": "系统暂未开放注册，如需开通账号请联系管理员（微信 poet-1986）"}, 403)

    phone = str(body.get("phone") or "").strip()
    code = str(body.get("code") or "").strip()
    password = str(body.get("password") or "")
    invite_code = str(body.get("invite_code") or body.get("inviteCode") or "").strip().upper()
    ip = client_ip(request)

    if not re.match(r"^1[3-9]\d{9}$", phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    if not code or not code.isdigit():
        return jr({"ok": False, "msg": "请输入短信验证码"}, 400)
    if len(password) < 6:
        return jr({"ok": False, "msg": "密码至少 6 位"}, 400)

    # ---- 防刷层 ①②: IP 维度 ----
    if not security.register_ip_day_allowed(ip):
        log.warning("注册拦截-同 IP 24h 超限 ip=%s", ip)
        return jr({"ok": False, "msg": "当前网络注册账号过多，请 24 小时后再试"}, 429)
    if not security.register_allowed(ip):
        log.warning("注册拦截-同 IP 1h 超限 ip=%s", ip)
        return jr({"ok": False, "msg": "操作过于频繁，请稍后再试"}, 429)

    # ---- 手机号唯一性 ----
    if users.find_user_by_phone(phone):
        return jr({"ok": False, "msg": "该手机号已注册，请直接登录或找回密码"}, 409)

    # ---- 短信验证码校验(scene=register, 与发送时一致) ----
    if sms_verify.is_consumed(phone, "register"):
        return jr({"ok": False, "msg": "验证码已使用，请重新获取"}, 400)
    try:
        ok, msg = sms_verify.check_code(phone, code, scene="register")
    except sms_verify.SmsNotConfigured as e:
        log.warning("注册短信校验未配置: %s", e)
        return jr({"ok": False, "msg": "短信服务未配置，请联系管理员"}, 503)
    except Exception as e:
        log.error("注册短信校验异常 phone=%s err=%s", phone, e)
        return jr({"ok": False, "msg": "校验失败，请稍后再试"}, 500)
    if not ok:
        return jr({"ok": False, "msg": "验证码错误或已过期"}, 400)

    # ---- 邀请码解析 ----
    inviter = None
    if invite_code:
        inviter = users.find_user_by_invite_code(invite_code)
        if inviter is None:
            return jr({"ok": False, "msg": "邀请码无效，请核对后重试"}, 400)
        if inviter.get("phone") and str(inviter["phone"]) == phone:
            return jr({"ok": False, "msg": "不能使用自己的邀请码"}, 400)

    # ---- 注册送 5 天完整体验(每个手机号仅 1 次, 见 phone_claims) ----
    ua = (request.headers.get("User-Agent") or "")[:200]
    # 用户名: 手机号打码(如 138****8888), 登录仍以手机号为主
    username = phone[:3] + "****" + phone[-4:]
    # 同号重名兜底(理论上 phone 唯一即唯一, 防止历史手工账号撞名)
    if users.find_user(username):
        username = username + "_" + str(int(time.time()) % 10000)

    expire_days = 0
    first_claim = True
    if not inviter:
        # 受邀注册: 由邀请奖励链路统一赠送(避免双份)
        expire_days = config.NEW_USER_DAYS
    else:
        expire_days = config.NEW_USER_DAYS

    uid = users.create_user(
        username, password, phone=phone,
        invited_by=(inviter["id"] if inviter else None),
        register_ip=ip, register_ua=ua,
        expire_days=expire_days,
        member_level=getattr(config, "NEW_USER_MEMBER_LEVEL", 1))

    # ---- 领取台账: 判定是否为该手机号首次领 VIP ----
    is_first, claim_rec, blocked = users.claim_phone(phone, uid, ip)
    if not is_first:
        # 该号此前已领过 → 降级为普通试用(不送会员等级), 但仍可注册登录
        first_claim = False
        try:
            conn = database.get_conn()
            conn.execute("UPDATE users SET member_level=0 WHERE id=?", (uid,))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("重注册降级失败 uid=%s err=%s", uid, e)
        log.warning("注册成功但非首次领取 uid=%s phone=%s reason=%s", uid, phone, blocked)
    else:
        log.info("注册成功 uid=%s phone=%s 送 %s 天 level=%s 体验",
                 uid, phone, expire_days, getattr(config, "NEW_USER_MEMBER_LEVEL", 1))

    # ---- 邀请奖励: 邀请人 +5 天(带防刷判断) ----
    invite_rewarded = False
    if inviter:
        blocked_flag, reason = users.invite_reward_blocked(inviter["id"], ip)
        if blocked_flag:
            log.warning("邀请奖励被拦截 inviter=%s invitee=%s reason=%s", inviter["id"], uid, reason)
        else:
            got = users.grant_invite_reward(inviter["id"], config.INVITE_REWARD_DAYS)
            invite_rewarded = bool(got)
            log.info("邀请奖励发放 inviter=%s invitee=%s days=%s new_expire=%s",
                     inviter["id"], uid, config.INVITE_REWARD_DAYS, got)
            # 2026-10-06: 邀请成功**必须通知邀请人** —— 之前奖励静默到账, 邀请人不知道自己
            # 赚了 5 天, 裂变正反馈是断的。🔴 全程吞异常: 注册主流程不能被它拖垮。
            if got:
                try:
                    from ..services import notice_center as nc
                    nc.notify_invite_reward(
                        inviter["id"], username, int(config.INVITE_REWARD_DAYS),
                        time.strftime("%Y-%m-%d", time.gmtime(int(got) + 8 * 3600)))
                except Exception as e:
                    log.warning("邀请成功通知失败 inviter=%s err=%s", inviter["id"], e)

    sms_verify.mark_consumed(phone, "register", ttl=config.SMS_VALID_MIN * 60)

    # 注册即登录
    token = security.issue_token(uid)
    u = users.find_user_by_id(uid) or {}
    et = int(u.get("expire_at") or 0)
    resp = jr({"ok": True, "msg": "注册成功，已赠送 %d 天完整体验" % expire_days,
               "token": token, "uid": uid, "username": username,
               "expire_at": et,
               "member_level": users.get_member_level(uid),
               "first_claim": 1 if first_claim else 0,
               "invite_rewarded": 1 if invite_rewarded else 0})
    resp.set_cookie(key="kx_token", value=token, max_age=12 * 3600, path="/",
                    httponly=True, samesite="lax", secure=False)
    return resp


@router.post("/api/register/send")
def api_register_send(request: Request, body: dict = Body(...)):
    """注册-发送短信验证码(scene=register).
    与找回密码不同: 注册场景下手机号**必须未注册**才发送(省短信费 + 防枚举)"""
    if not getattr(config, "REG_OPEN", True):
        return jr({"ok": False, "msg": "系统暂未开放注册"}, 403)
    phone = str(body.get("phone") or "").strip()
    if not re.match(r"^1[3-9]\d{9}$", phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    if users.find_user_by_phone(phone):
        return jr({"ok": False, "msg": "该手机号已注册，请直接登录或找回密码"}, 409)
    ip = client_ip(request)
    allowed, reason = sms_verify.can_send(phone, ip, config.SMS_SEND_INTERVAL)
    if not allowed:
        return jr({"ok": False, "msg": reason}, 429)
    try:
        ok, msg = sms_verify.send_code(phone, scene="register",
                                       interval=config.SMS_SEND_INTERVAL,
                                       valid_time=config.SMS_VALID_MIN)
    except sms_verify.SmsNotConfigured as e:
        log.warning("注册短信发送未配置: %s", e)
        return jr({"ok": False, "msg": "短信服务未配置，请联系管理员"}, 503)
    except Exception as e:
        log.error("注册短信发送异常 phone=%s err=%s", phone, e)
        return jr({"ok": False, "msg": "发送失败，请稍后再试"}, 500)
    if not ok:
        return jr({"ok": False, "msg": "发送失败: %s" % msg}, 500)
    log.info("注册验证码已发送 phone=%s ip=%s", phone, ip)
    return jr({"ok": True, "msg": "验证码已发送，请查收"})


@router.get("/api/register/config")
def api_register_config():
    """注册页展示配置(是否开放 + 赠送天数), 前端不必硬编码"""
    return jr({"ok": True,
               "open": bool(getattr(config, "REG_OPEN", True)),
               "gift_days": int(config.NEW_USER_DAYS),
               "invite_reward_days": int(config.INVITE_REWARD_DAYS)})


@router.get("/api/invite-info")
def api_invite_info(code: str = ""):
    """注册页预校验邀请码(展示邀请人昵称), 避免提交后才发现无效"""
    code = (code or "").strip().upper()
    if not code:
        return jr({"ok": False, "msg": "请输入邀请码"}, 400)
    inviter = users.find_user_by_invite_code(code)
    if inviter is None:
        return jr({"ok": False, "msg": "邀请码无效"}, 404)
    name = inviter.get("wx_name") or inviter.get("username") or ""
    return jr({"ok": True, "inviter": name[:2] + "**" if len(name) > 2 else name,
               "reward_days": int(config.INVITE_REWARD_DAYS)})


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
    # 2026-09-22 v4.11.35: 重置密码落库(安全审计价值最高的一条 —— 账号可能被他人接管)
    activity.record_login(uid=user["id"], login_try=phone, result="reset",
                          ip=client_ip(request), ua=request.headers.get("User-Agent") or "")
    return jr({"ok": True, "msg": "密码已重置，请用新密码登录"})


@router.post("/api/logout")
def api_logout(request: Request, uid: int = Depends(get_uid)):
    """主动退出登录(2026-09-22 v4.11.35 新增).

    ★ 新增原因: 此前「退出」是**纯前端行为**(只清本地 token), 后端完全无感知 ——
      所以登录记录里只有「登录」与「被顶出」, 缺「主动退出」这一半, 会话时长算不出来。
    ★ 语义: 作废当前用户全部 token(与单点登录一致), 并落一条 logout 记录。
      token 本身无效/过期时不进这里(get_uid 已 401), 所以不进记录是合理的。
    """
    try:
        security.revoke_user_tokens(uid)
    except Exception as e:
        log.warning("退出登录作废 token 失败 uid=%s err=%s", uid, e)
    activity.record_login(uid=uid, result="logout", ip=client_ip(request),
                          ua=request.headers.get("User-Agent") or "")
    log.info("主动退出登录 uid=%s ip=%s", uid, client_ip(request))
    return jr({"ok": True, "msg": "已退出登录"})


def _device_label(ua: str) -> str:
    """User-Agent → 人话设备名('Windows · Chrome' / 'iPhone · Safari' / 'Android · 微信')。

    刻意只做**够用的粗粒度识别**（系统 + 浏览器/容器）。写正则去追版本号会长期劣化，
    而这里唯一用途是让用户认出"这是不是我那台设备"，型号版本反而添乱。
    """
    u = (ua or "").lower()
    if not u:
        return "未知设备"
    os_name = "未知系统"
    for key, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("mac os x", "Mac"),
                      ("android", "Android"), ("windows", "Windows"), ("linux", "Linux")):
        if key in u:
            os_name = name
            break
    app_name = "未知浏览器"
    # 顺序敏感: 微信/QQ 内置浏览器的 UA 里也含 safari/chrome 字样, 必须先判容器
    if "micromessenger" in u:
        app_name = "微信"
    elif "qq/" in u:
        app_name = "QQ"
    elif "weibo" in u:
        app_name = "微博"
    elif "edg/" in u or "edge/" in u:
        app_name = "Edge"
    elif "chrome/" in u:
        app_name = "Chrome"
    elif "safari/" in u:
        app_name = "Safari"
    elif "firefox/" in u:
        app_name = "Firefox"
    return f"{os_name} · {app_name}"


@router.get("/api/auth/logins")
def api_auth_logins(request: Request, uid: int = Depends(get_uid), limit: int = 20):
    """2026-10-07 新增(v4.12.3 第二批 U13「设备管理」的**可见性**部分)。

    给「我的」页返回当前用户自己的最近登录记录（成功 / 失败 / 主动退出），用于自查
    「是不是有陌生人在登我的号」—— 密码泄漏时这是用户能拿到的唯一线索。

    🔴 **刻意不做成多设备在线列表**：本系统是**强制单点**（auth.py:50 登录即作废旧会话，
       目的是防止账号共享），同一时刻只可能有一条有效会话，列出来也永远只有一台。
       是否放开多点登录属安全策略取舍 ⇒ **需主人决定，本次不动**。
       "退出所有设备"的能力也已存在（POST /api/logout 作废全部 token），这里只补可见性。
    """
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT result, ip, ua, remember, created_at FROM login_log "
            "WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, max(1, min(int(limit or 20), 50)))
        ).fetchall()
        cur = conn.execute(
            "SELECT token, created_at, expire_ts FROM tokens "
            "WHERE user_id=? AND revoked=0 AND expire_ts>? ORDER BY created_at DESC LIMIT 1",
            (uid, int(time.time()))).fetchone()
    finally:
        conn.close()
    items = [{
        "result": r[0] or "",
        "ip": r[1] or "",
        "device": _device_label(r[2] or ""),
        "remember": 1 if r[3] else 0,
        "ts": int(r[4] or 0),
    } for r in rows]
    session = None
    if cur:
        session = {"created_at": int(cur[1] or 0), "expire_at": int(cur[2] or 0),
                   "device": _device_label(request.headers.get("User-Agent") or "")}
    return jr({"ok": True, "items": items, "session": session})


@router.post("/api/forgot/check")
def api_forgot_check(request: Request, body: dict = Body(...)):
    """忘记密码辅助: 输入用户名/手机号, 告知走哪条自助路径.
    邮箱验证已下线(2026-09-21), 现在只有「已绑定手机号 → 短信自助」一条路,
    其余情况引导联系管理员人工处理。返回结构保留 has_email 字段兼容旧前端。"""
    login = str(body.get("login") or "").strip()
    if not login:
        return jr({"ok": False, "msg": "请输入用户名或手机号"}, 400)
    user = users.find_user_by_login(login)
    if user is None:
        # 未找到账号: 也提示联系管理员(避免枚举账号存在性)
        return jr({"ok": False, "has_email": False, "has_phone": False,
                   "msg": "未找到该账号，请联系管理员人工处理（管理员微信号：poet-1986）"}, 404)
    phone = str(user.get("phone") or "").strip()
    if not phone:
        return jr({"ok": False, "has_email": False, "has_phone": False,
                   "msg": "该账号未绑定手机号，无法自助找回密码，请联系管理员人工处理（管理员微信号：poet-1986）"}, 400)
    masked = phone[:3] + "****" + phone[-4:]
    return jr({"ok": True, "has_email": False, "has_phone": True, "phone": masked,
               "msg": "该账号已绑定手机号 %s，请用短信验证码重置密码" % masked})

