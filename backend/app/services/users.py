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


def set_password(uid, new_password):
    """重置用户密码(管理员操作), 返回是否成功"""
    try:
        conn = _conn()
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (security.hash_password(new_password), uid))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# ---------- 会员等级 ----------
# member_level: 0=免费试用 1=付费会员 2=VIP老师(管理后台指定, 永久权限)
MEMBER_LEVEL_LABEL = {0: "免费试用", 1: "付费会员", 2: "VIP老师"}


def get_member_level(uid):
    """读取用户会员等级(默认 0 免费试用)"""
    row = find_user_by_id(uid)
    if not row:
        return 0
    try:
        return int(row.get("member_level") or 0)
    except (TypeError, ValueError):
        return 0


def set_member_level(uid, level):
    """设置会员等级: 0=免费试用 1=付费会员 2=VIP老师; 非法值拒绝"""
    try:
        lv = int(level)
    except (TypeError, ValueError):
        return False
    if lv not in (0, 1, 2):
        return False
    try:
        conn = _conn()
        conn.execute("UPDATE users SET member_level=? WHERE id=?", (lv, uid))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def set_pay_remark(uid, pay_remark):
    """设置会员专属付款备注 (管理员调用, 用于月费用户的付款时间/方式/凭证记录)
    pay_remark=None 清空"""
    try:
        conn = _conn()
        conn.execute("UPDATE users SET pay_remark=? WHERE id=?", (pay_remark, uid))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_user(uid):
    """删除用户 + 级联清理(tokens / 选股记录 batches / 重置链接)
    注意: 调用方需先校验目标不能是管理员/自己"""
    try:
        conn = _conn()
        conn.execute("DELETE FROM tokens WHERE user_id=?", (uid,))
        # batches/batch_stocks 用 ON DELETE CASCADE 时会自动清, 否则显式删
        conn.execute("DELETE FROM batch_stocks WHERE batch_id IN (SELECT id FROM batches WHERE user_id=?)", (uid,))
        conn.execute("DELETE FROM batches WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM reset_tokens WHERE user_id=?", (uid,))
        # 用户被删后其 invited_by 指向失效 -> 置空(避免后续展示孤儿)
        conn.execute("UPDATE users SET invited_by=NULL WHERE invited_by=?", (uid,))
        cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok
    except Exception as e:
        log.warning("delete_user 失败 uid=%s err=%s", uid, e)
        return False


def grant_invite_reward(inviter_id, days=5):
    """邀请奖励: 邀请人每成功邀请一个新注册用户 +days 天使用时间
    永久会员(expire_at=0)或 VIP老师(member_level=2)已是永久, 不再叠加(避免把永久变有限)
    返回新到期时间戳(跳过/失败返回 0)"""
    if not inviter_id:
        return 0
    try:
        inviter = find_user_by_id(inviter_id)
        if not inviter:
            return 0
        # 永久会员(expire_at=0)或 VIP老师: 已是永久权限, 跳过奖励
        if not int(inviter.get("expire_at") or 0):
            return 0
        if int(inviter.get("member_level") or 0) == 2:
            return 0
        return extend_expire(inviter_id, days)
    except Exception as e:
        log.warning("邀请奖励发放失败 inviter=%s days=%s err=%s", inviter_id, days, e)
        return 0


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
        "u.phone, u.email, u.wx_name, u.remark, u.pay_remark, u.expire_at, u.member_level, "
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


def create_user(username, password, invited_by=None, invite_code=None, phone=None, email=None,
                expire_days=5):
    """创建用户. expire_days>0 注册即送 N 天会员(默认 5 天试用); 0 表示永久"""
    now = int(__import__("time").time())
    expire_at = now + int(expire_days) * 86400 if (expire_days or 0) > 0 else 0
    conn = database.get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, password_hash, created_at, expire_at, invited_by, invite_code, phone, email) VALUES (?,?,?,?,?,?,?,?)",
                (username, security.hash_password(password), now, expire_at,
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


# ---------- 全局默认筛选参数强制覆盖 ----------
# 筛选偏好字段(与前端 defaultFilterSettings + 盘中参数一致); 主题字段(bg/font) 不在此集合
FILTER_PREFS_KEYS = {
    "stSuspend", "markets", "limitUp", "bidGt", "probLt", "confLt",
    "floatMvFloor", "floatMvGt", "priceGt", "bidAmtFloor",
    "chgFloor", "chgGt", "volRatioFloor", "turnoverFloor", "turnoverGt",
    "spotExcludeZT",
}


def clear_filter_prefs_all():
    """强制覆盖: 清除所有用户 filter_prefs 中的筛选字段(保留 bg/font 等主题字段),
    使所有用户回到"未自定义偏好"状态 → 前端下次加载自动使用最新全局默认。
    返回受影响(清除了筛选字段)的用户数; 无 prefs / 仅有主题字段的用户不受影响。
    注: 本地 localStorage 的锁定(9:30 竞价名单锁定)属于浏览器端状态, 服务端无法清除。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT id, filter_prefs FROM users WHERE filter_prefs IS NOT NULL AND filter_prefs != ''"
    ).fetchall()
    affected = 0
    for uid, raw in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        removed = [k for k in FILTER_PREFS_KEYS if k in data]
        if not removed:
            continue
        for k in removed:
            data.pop(k, None)
        if data:
            conn.execute("UPDATE users SET filter_prefs=? WHERE id=?",
                         (json.dumps(data, ensure_ascii=False), uid))
        else:
            conn.execute("UPDATE users SET filter_prefs=NULL WHERE id=?", (uid,))
        affected += 1
    conn.commit()
    conn.close()
    return affected


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
    # 发件显示名: 品牌「快选股」(2026-08-16 用户指定); SMTP_FROM 是发件地址
    msg["From"] = formataddr((str(Header("快选股", "utf-8")), config.SMTP_FROM or config.SMTP_USER))
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


def update_profile(uid, phone=None, email=None, wx_name=None, remark=None):
    """用户修改个人资料(手机号/邮箱/微信名/备注), 返回 (ok, msg)
    手机号/邮箱改动时校验唯一性; 返回码: ok=True 或 (False, 错误信息)"""
    if phone is not None:
        phone = phone.strip() or None
        if phone and not _is_phone(phone):
            return False, "手机号格式不正确"
    if email is not None:
        email = email.strip() or None
        if email and not _is_email(email):
            return False, "邮箱格式不正确"
    if wx_name is not None:
        wx_name = wx_name.strip() or None
        if wx_name and len(wx_name) > 40:
            return False, "微信名过长(限40字)"
    if remark is not None:
        remark = remark.strip() or None
        if remark and len(remark) > 200:
            return False, "备注过长(限200字)"
    # 唯一性: 手机号/邮箱已被其他用户占用则拒绝
    if phone:
        other = find_user_by_phone(phone)
        if other and other["id"] != uid:
            return False, "该手机号已绑定其他账号"
    if email:
        other = find_user_by_email(email)
        if other and other["id"] != uid:
            return False, "该邮箱已绑定其他账号"
    sets, vals = [], []
    for col, v in (("phone", phone), ("email", email), ("wx_name", wx_name), ("remark", remark)):
        if v is None:
            continue
        sets.append("%s=?" % col)
        vals.append(v)
    if not sets:
        return True, "无改动"
    vals.append(uid)
    try:
        conn = _conn()
        conn.execute("UPDATE users SET %s WHERE id=?" % ",".join(sets), vals)
        conn.commit()
        conn.close()
        return True, "已保存"
    except Exception as e:
        log.warning("更新个人资料失败 uid=%s err=%s", uid, e)
        return False, "保存失败: %s" % e
