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
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from ..core import config, logger
from ..db import database
from . import security, settings

log = logger.get_logger(__name__)
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _conn():
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_admin():
    """确保至少一个管理员(幂等, 只初始化一次):
    优先 ADMIN_USERNAME 指定用户, 否则取 id 最小的用户(种子账号)。
    首次调用后写标记, 后续不覆盖用户手动调整的管理员。
    """
    if settings.get("admin_initialized", False):
        return
    conn = _conn()
    try:
        names = [n.strip() for n in (config.ADMIN_USERNAME or "").split(",") if n.strip()]
        if names:
            cur = conn.execute(
                "UPDATE users SET is_admin=1 WHERE username IN (%s) AND is_admin=0"
                % ",".join("?" * len(names)), names)
        else:
            cur = conn.execute(
                "UPDATE users SET is_admin=1 WHERE id=(SELECT MIN(id) FROM users) AND is_admin=0")
        if cur.rowcount:
            conn.commit()
            log.info("已初始化管理员: %s", names or "id 最小用户")
    except Exception as e:
        log.error("初始化管理员失败 err=%s", e)
    finally:
        conn.close()
    settings.set("admin_initialized", True)


def is_admin(uid):
    row = find_user_by_id(uid)
    return bool(row and row.get("is_admin"))


# ---------- 账号到期(使用权限) ----------
def set_expire(uid, expire_ts):
    """直接设置到期时间戳(0 或 None 表示永久), 返回是否成功"""
    try:
        conn = _conn()
        conn.execute("UPDATE users SET expire_at=? WHERE id=?", (int(expire_ts or 0), uid))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def extend_expire(uid, days):
    """从 max(现在, 当前到期) 累加 days 天(续费可叠加), 返回新到期时间戳"""
    row = find_user_by_id(uid)
    cur = int(row.get("expire_at") or 0) if row else 0
    base = max(int(time.time()), cur)
    new = base + int(days) * 86400
    set_expire(uid, new)
    return new


def is_expired(uid):
    """是否已过期: expire_at>0 且 当前时间 > expire_at"""
    row = find_user_by_id(uid)
    if not row:
        return False
    et = int(row.get("expire_at") or 0)
    return bool(et) and time.time() > et


def list_users_page(page=1, page_size=20, keyword=""):
    """管理端用户列表(分页), 附带每个用户的基础统计"""
    conn = _conn()
    cond = ""
    params = []
    if keyword:
        cond = " AND (username LIKE ? OR COALESCE(phone,'') LIKE ? OR COALESCE(email,'') LIKE ?)"
        kw = "%" + keyword + "%"
        params = [kw, kw, kw]
    total = conn.execute(
        "SELECT COUNT(*) FROM users WHERE 1=1" + cond, params).fetchone()[0]
    rows = conn.execute(
        "SELECT u.id, u.username, u.created_at, u.is_admin, u.invite_code, u.invited_by, "
        "u.phone, u.email, u.expire_at, "
        "(SELECT COUNT(*) FROM users x WHERE x.invited_by=u.id) AS invited_count, "
        "(SELECT COUNT(*) FROM batches b WHERE b.user_id=u.id) AS batch_count "
        "FROM users u WHERE 1=1" + cond + " ORDER BY u.id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size]).fetchall()
    conn.close()
    return {"total": total, "page": page, "pageSize": page_size, "rows": [dict(r) for r in rows]}


def user_stats():
    """管理端用户统计: 总数/今日注册/邀请关系/活跃(有选股记录)用户"""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    # 今日注册(北京时间, 与服务器时区无关): created_at 是 UTC 时间戳, +8h 后按 UTC 显示即北京日期
    today = conn.execute(
        "SELECT COUNT(*) FROM users WHERE date(created_at+8*3600,'unixepoch') = date('now','+8 hours')"
    ).fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM batches").fetchone()[0]
    invited = conn.execute(
        "SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL").fetchone()[0]
    expired = conn.execute(
        "SELECT COUNT(*) FROM users WHERE expire_at>0 AND expire_at<?", (int(time.time()),)).fetchone()[0]
    top_inviter = conn.execute(
        "SELECT u.username, COUNT(*) n FROM users x JOIN users u ON u.id=x.invited_by "
        "GROUP BY x.invited_by ORDER BY n DESC LIMIT 1").fetchone()
    conn.close()
    return {
        "total": total, "today": today, "active": active, "invited": invited,
        "expired": expired,
        "top_inviter": (dict(top_inviter) if top_inviter else None),
    }


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
