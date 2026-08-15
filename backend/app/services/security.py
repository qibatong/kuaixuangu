# -*- coding: utf-8 -*-
"""
安全服务: 密码哈希 / Token 签发校验 / 接口限流 / 注册防刷
=========================================================
"""
import hashlib
import secrets
import threading
import time
from collections import deque

from ..core import config
from ..db.database import get_conn

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
        conn.execute("INSERT INTO tokens (token, user_id, expire_ts, created_at) VALUES (?,?,?,?)",
                     (t, user_id, int(time.time()) + ttl, int(time.time())))
        conn.commit()
    finally:
        conn.close()
    return t


def valid_token(t):
    """校验 token 是否有效, 返回 user_id; 过期/不存在返回 None。"""
    if not t:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT user_id, expire_ts FROM tokens WHERE token=?", (t,)).fetchone()
        if not row:
            return None
        uid, exp = row
        if time.time() > exp:
            conn.execute("DELETE FROM tokens WHERE token=?", (t,))
            conn.commit()
            return None
        return uid
    finally:
        conn.close()


def revoke_user_tokens(user_id):
    """使某用户所有已签发 token 失效(改密/重置/新登录踢旧会话)。返回被踢掉的 token 数。"""
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


# ---------- 接口限流: 每 IP 每分钟 N 次 ----------
_hits = {}


def rate_allow(ip):
    now = time.time()
    dq = _hits.setdefault(ip, deque())
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= config.RATE_LIMIT_PER_MIN:
        return False
    dq.append(now)
    return True


# ---------- 注册防刷: 同 IP 10 分钟最多 5 次 ----------
_register_hits = {}


def register_allowed(ip):
    now = time.time()
    hits = _register_hits.setdefault(ip, deque())
    while hits and now - hits[0] > 600:
        hits.popleft()
    if len(hits) >= 5:
        return False
    hits.append(now)
    return True


# ---------- 重置邮件防刷: 每邮箱每小时 N 次 ----------
_reset_hits = {}


def reset_mail_allowed(email):
    now = time.time()
    hits = _reset_hits.setdefault(email, deque())
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= config.RESET_RATE_LIMIT:
        return False
    hits.append(now)
    return True
