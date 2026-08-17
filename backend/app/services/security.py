# -*- coding: utf-8 -*-
"""
安全服务: 密码哈希 / Token 签发校验 / 接口限流 / 注册防刷
=========================================================
"""
import hashlib
import secrets
import threading
import time

from ..core import config
from ..db.database import get_conn
from .cache_store import store

# ---------- 密码哈希 (PBKDF2-SHA256) ----------
def hash_password(password, salt=None, iterations=None):
    if salt is None:
        salt = secrets.token_hex(8)
    if iterations is None:
        iterations = config.PBKDF2_ITERS
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt.encode("utf-8"), iterations).hex()
    return "%s$%d$%s" % (salt, iterations, digest)


def verify_password(password, stored):
    """兼容新格式(salt$iter$digest)与旧格式(salt$digest, 按 10 万次迭代验证)"""
    parts = stored.split("$")
    try:
        if len(parts) == 3:
            salt, iterations, digest = parts
            iterations = int(iterations)
        elif len(parts) == 2:
            salt, digest = parts
            iterations = 100000
        else:
            return False
    except (ValueError, TypeError):
        return False
    return hash_password(password, salt, iterations).split("$", 2)[2] == digest


# ---------- Token (持久化到 DB, 进程重启不失效; 支持「记住我」长有效期) ----------
_tokens_lock = threading.Lock()


def issue_token(user_id, remember=False):
    """签发新 token。remember=True → 30 天有效期(前端「记住我」), 否则 12 小时。"""
    t = secrets.token_hex(16)
    ttl = config.TOKEN_TTL_REMEMBER if remember else config.TOKEN_TTL
    conn = get_conn()
    try:
        conn.execute("INSERT INTO tokens (token, user_id, expire_ts, created_at, revoked) VALUES (?,?,?,?,0)",
                     (t, user_id, int(time.time()) + ttl, int(time.time())))
        conn.commit()
    finally:
        conn.close()
    return t


def _token_status(t):
    """查询 token 状态: 返回 (status, user_id)
    status: ok / expired / revoked(被新登录顶出) / missing(不存在或伪造)"""
    if not t:
        return "missing", None
    conn = get_conn()
    try:
        row = conn.execute("SELECT user_id, expire_ts, revoked FROM tokens WHERE token=?", (t,)).fetchone()
        if not row:
            return "missing", None
        uid, exp, revoked = row
        if revoked:
            return "revoked", uid
        if time.time() > exp:
            conn.execute("DELETE FROM tokens WHERE token=?", (t,))
            conn.commit()
            return "expired", uid
        return "ok", uid
    finally:
        conn.close()


def valid_token(t):
    """校验 token 是否有效, 返回 user_id; 过期/不存在/被踢返回 None(保持原签名兼容)"""
    status, uid = _token_status(t)
    return uid if status == "ok" else None


def revoke_user_tokens(user_id):
    """使某用户所有已签发 token 失效(改密/重置/新登录踢旧会话), 返回被踢掉的 token 数。
    (2026-08-17: 不再 DELETE, 改为标记 revoked=1 — 旧 token 再次访问可识别为
     「被另一设备顶出」, 前端据此弹"账号已在另一设备登录"通知)"""
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE tokens SET revoked=1, expire_ts=0 WHERE user_id=? AND revoked=0",
                           (user_id,))
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


# ---------- 接口限流: 每 IP 每分钟 N 次 (跨进程共享, CacheStore 固定窗口) ----------
def rate_allow(ip):
    n = store.incr("rate:%s" % ip, ttl=60)
    return n <= config.RATE_LIMIT_PER_MIN


# ---------- 注册防刷: 同 IP 1 小时最多 10 次 ----------
# (2026-08-16 放宽: 10 分钟 5 次 → 1 小时 10 次; 格式错请求已前置校验不计次,
#  避免用户改正几次格式就被锁. 安全兜底仍依赖手机号+邮箱唯一性)
def register_allowed(ip):
    n = store.incr("reg:%s" % ip, ttl=3600)
    return n <= 10


# ---------- 同 IP 24h 注册数上限(2026-08-17, 防批量刷号) ----------
def register_ip_day_allowed(ip):
    """同 IP 24h 内最多注册 config.REG_IP_DAY_LIMIT 个新账号(默认 5)"""
    if not ip:
        return True
    n = store.incr("regip:%s" % ip, ttl=86400)
    limit = int(getattr(config, "REG_IP_DAY_LIMIT", 5))
    return n <= limit


# ---------- 重置邮件防刷: 每邮箱每小时 N 次 ----------
def reset_mail_allowed(email):
    n = store.incr("reset:%s" % email, ttl=3600)
    return n <= config.RESET_RATE_LIMIT


# ---------- 邮箱验证码防刷: 每邮箱 5 分钟 1 次, 每小时 3 次 ----------
def verify_mail_allowed(email):
    """返回 (ok, msg); 5 分钟冷却 + 每小时最多 3 次"""
    cool = store.get("vmail:%s" % email)
    if cool:
        return False, "验证邮件发送过于频繁，请 5 分钟后再试"
    hour = store.incr("vmailh:%s" % email, ttl=3600)
    if hour > 3:
        return False, "今日验证邮件发送次数过多，请稍后再试"
    store.set("vmail:%s" % email, 1, ttl=300)
    return True, ""
