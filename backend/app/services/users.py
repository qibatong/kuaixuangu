# -*- coding: utf-8 -*-
"""
用户服务: 用户查询 / 创建 / 邀请码 / 密码重置邮件
================================================
"""
import json
import re
import secrets
import smtplib
import sqlite3
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from ..core import config
from ..db import database
from . import security

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _conn():
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _is_phone(s):
    return bool(re.match(r"^1[3-9]\d{9}$", s))


def _is_email(s):
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", s))


def find_user(username):
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_user_by_phone(phone):
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_user_by_email(email):
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_user_by_id(uid):
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_user_by_invite_code(code):
    if not code:
        return None
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE invite_code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_user_by_login(login):
    """登录识别: 手机号 / 邮箱 / 用户名 三种方式都支持"""
    login = (login or "").strip()
    if not login:
        return None
    if _is_phone(login):
        return find_user_by_phone(login)
    if _is_email(login):
        return find_user_by_email(login)
    return find_user(login)


def count_users():
    conn = database.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n


def gen_unique_invite_code():
    """生成一个库中不存在的 8 位邀请码"""
    for _ in range(50):
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
        if find_user_by_invite_code(code) is None:
            return code
    return None


def create_user(username, password, invited_by=None, invite_code=None, phone=None, email=None):
    conn = database.get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, password_hash, created_at, invited_by, invite_code, phone, email) VALUES (?,?,?,?,?,?,?)",
                (username, security.hash_password(password), int(__import__("time").time()),
                 invited_by, invite_code, phone, email))
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def ensure_invite_code(uid):
    """确保用户有邀请码, 没有则生成"""
    user = find_user_by_id(uid)
    if user and user.get("invite_code"):
        return user["invite_code"]
    code = gen_unique_invite_code()
    if code is None:
        return None
    conn = database.get_conn()
    conn.execute("UPDATE users SET invite_code=? WHERE id=?", (code, uid))
    conn.commit()
    conn.close()
    return code


def list_invitees(uid):
    """返回被该用户邀请注册的人: [{username, created_at}, ...]"""
    conn = _conn()
    rows = conn.execute(
        "SELECT username, created_at FROM users WHERE invited_by=? ORDER BY created_at DESC",
        (uid,)).fetchall()
    conn.close()
    return [{"username": r["username"], "created_at": r["created_at"]} for r in rows]


def refresh_invite_code(uid):
    code = gen_unique_invite_code()
    if code is None:
        return None
    conn = database.get_conn()
    conn.execute("UPDATE users SET invite_code=? WHERE id=?", (code, uid))
    conn.commit()
    conn.close()
    return code


def get_prefs(uid):
    user = find_user_by_id(uid)
    raw = (user or {}).get("filter_prefs")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def save_prefs(uid, settings):
    raw = json.dumps(settings, ensure_ascii=False)
    conn = database.get_conn()
    conn.execute("UPDATE users SET filter_prefs=? WHERE id=?", (raw, uid))
    conn.commit()
    conn.close()


# ---------- 邮件(忘记密码) ----------
def smtp_configured():
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS)


def send_reset_email(to_email, reset_url, username):
    """发送密码重置邮件(纯标准库 smtplib)"""
    body = (
        "你好 %s：\n\n"
        "我们收到了重置密码的请求。请点击下面的链接设置新密码"
        "（30 分钟内有效，仅可使用一次）：\n\n%s\n\n"
        "如果这不是你本人的操作，请忽略本邮件，你的密码不会改变。\n\n"
        "—— 快选系统"
    ) % (username, reset_url)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header("快选 - 重置密码", "utf-8")
    msg["From"] = formataddr((str(Header("快选", "utf-8")), config.SMTP_FROM or config.SMTP_USER))
    msg["To"] = to_email
    if config.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        server.starttls()
    try:
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.SMTP_FROM or config.SMTP_USER, [to_email], msg.as_string())
    finally:
        server.quit()
