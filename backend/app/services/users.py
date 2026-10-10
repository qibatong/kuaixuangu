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
# member_level: 0=免费试用 1=付费会员 2=VIP(永久权限, 管理后台指定)
MEMBER_LEVEL_LABEL = {0: "免费试用", 1: "付费会员", 2: "VIP"}


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
        # 2026-10-12: 邀请人累计封顶10天(2人x5天), 第3个邀请起不再发奖
        try:
            _c = database.get_conn().execute(
                "SELECT COUNT(*) FROM users WHERE invited_by=?", (inviter_id,)).fetchone()[0]
            if int(_c or 0) >= 2:
                log.info("邀请人奖励封顶停发 inviter=%s 已邀%d人(累计10天)", inviter_id, _c)
                return 0
        except Exception:
            pass
        return extend_expire(inviter_id, days)
    except Exception as e:
        log.warning("邀请奖励发放失败 inviter=%s days=%s err=%s", inviter_id, days, e)
        return 0


def invite_reward_blocked(inviter_id, invitee_ip=""):
    """防同 IP 小号刷奖励: 返回 (blocked, reason)
    - 被邀请人与邀请人注册 IP 相同(自邀特征) → 拦截
    - 邀请人同 IP 已成功邀请 ≥ config.INVITE_SAME_IP_LIMIT 人 → 拦截(同 IP 批量小号)
    只做拦截判断, 不发奖励(被邀请人注册仍成功, 试用期照常)"""
    if not inviter_id or not invitee_ip:
        return False, ""
    try:
        inviter = find_user_by_id(inviter_id)
        if inviter and inviter.get("register_ip") and \
                str(inviter.get("register_ip")) == str(invitee_ip):
            return True, "被邀请人与邀请人注册 IP 相同(疑似自邀), 不发奖励"
        conn = _conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM users WHERE invited_by=? AND register_ip=?",
            (inviter_id, str(invitee_ip))).fetchone()[0]
        conn.close()
        if n >= int(getattr(config, "INVITE_SAME_IP_LIMIT", 3)):
            return True, "邀请人同 IP 已邀 %d 人, 超出限制(%d), 不发奖励" % (
                n, int(getattr(config, "INVITE_SAME_IP_LIMIT", 3)))
    except Exception as e:
        log.warning("邀请奖励防刷判断失败 inviter=%s ip=%s err=%s", inviter_id, invitee_ip, e)
    return False, ""


def extend_expire(uid, days):
    """从 max(现在, 当前到期) 累加 days 天(续费可叠加), 返回新到期时间戳"""
    row = find_user_by_id(uid)
    cur = int(row.get("expire_at") or 0) if row else 0
    base = max(int(time.time()), cur)
    new = base + int(days) * 86400
    set_expire(uid, new)
    return new


# ---------- 注册手机号领取台账(2026-09-21) ----------
def check_phone_claim(phone):
    """查询该手机号是否已领取过「新用户注册礼」.
    返回 dict(首次领取信息) 或 None(从未领取).
    ★ 该表不随 users 删除清理, 这是封堵「删号→同号重注册」无限刷 VIP 的关键。"""
    if not phone:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM phone_claims WHERE phone=?", (str(phone),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def claim_phone(phone, uid, ip=""):
    """登记手机号领取新用户礼(幂等). 返回 (is_first, record, blocked_reason)
    is_first=False 表示该号此前已领过 -> 本次不发 VIP, 但仍允许注册
    防刷: 同号已有 first_uid, 且当前 ip 与上次不同而 first_uid 失败重注册, 一律按非首次处理"""
    if not phone:
        return False, None, "缺少手机号"
    now = int(time.time())
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM phone_claims WHERE phone=?", (str(phone),)).fetchone()
        if row:
            conn.execute(
                "UPDATE phone_claims SET claim_count=claim_count+1, last_claim=?, last_ip=? WHERE phone=?",
                (now, str(ip or ""), str(phone)))
            conn.commit()
            rec = dict(row)
            rec["claim_count"] = int(rec.get("claim_count") or 0) + 1
            rec["last_claim"] = now
            rec["last_ip"] = str(ip or "")
            return False, rec, "该手机号已领取过新用户会员体验"
        conn.execute(
            "INSERT INTO phone_claims (phone, first_uid, first_claim, claim_count, last_claim, last_ip) "
            "VALUES (?,?,?,1,?,?)",
            (str(phone), uid, now, now, str(ip or "")))
        conn.commit()
        return True, {"phone": str(phone), "first_uid": uid, "first_claim": now,
                      "claim_count": 1, "last_claim": now, "last_ip": str(ip or "")}, ""
    except Exception as e:
        log.warning("phone_claims 落库失败 phone=%s uid=%s err=%s", phone, uid, e)
        # 落库异常时保守处理: 按已领取对待, 避免异常路径被刷
        return False, None, "领取登记异常, 本号按已领取处理"
    finally:
        conn.close()


def phone_claim_stats(days=30):
    """管理端: 近期手机号领取统计(用于风控视图)"""
    since = int(time.time()) - int(days) * 86400
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM phone_claims").fetchone()[0]
        recent = conn.execute(
            "SELECT COUNT(*) FROM phone_claims WHERE first_claim>=?", (since,)).fetchone()[0]
        # 同一 IP 短期内领多个号: 风控关注点
        rows = conn.execute(
            "SELECT COALESCE(last_ip,'') ip, COUNT(*) n FROM phone_claims "
            "WHERE first_claim>=? AND COALESCE(last_ip,'')!='' "
            "GROUP BY last_ip HAVING n>1 ORDER BY n DESC LIMIT 20", (since,)).fetchall()
        return {"total": total, "recent": recent, "multi_ip": [dict(r) for r in rows]}
    finally:
        conn.close()


# ---------- 每日签到(2026-09-21) ----------
def checkin_today(uid, date=None):
    """今日是否已签到"""
    d = date or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM user_checkin WHERE uid=? AND date=?", (uid, d)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def do_checkin(uid, reward=None):
    """签到(幂等, (uid,date) 主键天然防重). 返回 (ok, msg, reward, first_today)"""
    d = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    reward = int(getattr(config, "QUOTA_CHECKIN_BONUS", 3) if reward is None else reward)
    conn = _conn()
    try:
        if conn.execute("SELECT 1 FROM user_checkin WHERE uid=? AND date=?", (uid, d)).fetchone():
            return False, "今日已签到", 0, False
        conn.execute("INSERT INTO user_checkin (uid, date, reward, created_at) VALUES (?,?,?,?)",
                     (uid, d, reward, int(time.time())))
        conn.commit()
        return True, "签到成功", reward, True
    except sqlite3.IntegrityError:
        return False, "今日已签到", 0, False
    except Exception as e:
        log.warning("签到失败 uid=%s err=%s", uid, e)
        return False, "签到失败，请稍后重试", 0, False
    finally:
        conn.close()


def checkin_streak(uid, max_days=60):
    """连续签到天数(含今天; 今天没签则从昨天往前算)"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT date FROM user_checkin WHERE uid=? ORDER BY date DESC LIMIT ?",
            (uid, int(max_days))).fetchall()
    finally:
        conn.close()
    dates = {r[0] for r in rows}
    base = time.time() + 8 * 3600
    today = time.strftime("%Y-%m-%d", time.gmtime(base))
    if today not in dates:
        base -= 86400
    n = 0
    for i in range(int(max_days)):
        d = time.strftime("%Y-%m-%d", time.gmtime(base - i * 86400))
        if d in dates:
            n += 1
        else:
            break
    return n


def checkin_stats(days=30):
    """管理端: 签到统计"""
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - int(days) * 86400))
    conn = _conn()
    try:
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        n_today = conn.execute("SELECT COUNT(*) FROM user_checkin WHERE date=?", (today,)).fetchone()[0]
        n_range = conn.execute("SELECT COUNT(*) FROM user_checkin WHERE date>=?", (since,)).fetchone()[0]
        n_users = conn.execute("SELECT COUNT(DISTINCT uid) FROM user_checkin WHERE date>=?", (since,)).fetchone()[0]
        return {"today": n_today, "range_days": int(days), "range_total": n_range, "range_users": n_users}
    finally:
        conn.close()


def checkin_recent(uid, days=7):
    """最近 N 天签到情况(倒序), 供「我的会员」页日历展示"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT date, reward FROM user_checkin WHERE uid=? ORDER BY date DESC LIMIT ?",
            (uid, int(days))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------- 后台操作审计(2026-09-21) ----------
def audit(admin_uid, action, target_uid=None, detail="", ip=""):
    """记录一次管理员操作(失败不抛异常, 不阻塞主流程)"""
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO admin_audit (admin_uid, action, target_uid, detail, ip, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (int(admin_uid), str(action), (int(target_uid) if target_uid else None),
             json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail,
             str(ip or ""), int(time.time())))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("审计日志写入失败 action=%s err=%s", action, e)


def audit_list(page=1, page_size=30, action=""):
    """管理端: 审计日志分页"""
    conn = _conn()
    cond, params = "", []
    if action:
        cond = " WHERE a.action LIKE ?"
        params.append("%" + action + "%")
    total = conn.execute("SELECT COUNT(*) FROM admin_audit a" + cond, params).fetchone()[0]
    rows = conn.execute(
        "SELECT a.*, (SELECT username FROM users WHERE id=a.admin_uid) admin_name, "
        "(SELECT username FROM users WHERE id=a.target_uid) target_name "
        "FROM admin_audit a" + cond + " ORDER BY a.id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size]).fetchall()
    conn.close()
    return {"total": total, "page": page, "pageSize": page_size, "rows": [dict(r) for r in rows]}


def is_expired(uid):
    """是否已过期: expire_at>0 且 当前时间 > expire_at"""
    row = find_user_by_id(uid)
    if not row:
        return False
    et = int(row.get("expire_at") or 0)
    return bool(et) and time.time() > et


def list_users_page(page=1, page_size=20, keyword="", member_tab="all", tag=""):
    """管理端用户列表(分页), 附带每个用户的基础统计
    member_tab:
      - 'all'    不限(默认)
      - 'member' member_level > 0 且非管理员(付费会员+VIP)
      - 'paid'   member_level = 1 且非管理员(付费会员)
      - 'vip'    member_level = 2 且非管理员(VIP)
      - 'normal' member_level = 0 且非管理员(普通/试用)
      - 'admin'  is_admin = 1
    tag: 分层标签(A4, 2026-10-06) —— 传了就只返回带该标签的人"""
    conn = _conn()
    cond = ""
    params = []
    if keyword:
        cond += (" AND (u.username LIKE ? OR COALESCE(u.phone,'') LIKE ? OR COALESCE(u.email,'') LIKE ?"
                 " OR COALESCE(u.wx_name,'') LIKE ? OR COALESCE(u.remark,'') LIKE ?"
                 " OR COALESCE(u.pay_remark,'') LIKE ?)")
        kw = "%" + keyword + "%"
        params += [kw, kw, kw, kw, kw, kw]
    if member_tab == "member":
        cond += " AND COALESCE(u.member_level,0) > 0 AND COALESCE(u.is_admin,0) = 0"
    elif member_tab == "paid":
        cond += " AND COALESCE(u.member_level,0) = 1 AND COALESCE(u.is_admin,0) = 0"
    elif member_tab == "vip":
        cond += " AND COALESCE(u.member_level,0) = 2 AND COALESCE(u.is_admin,0) = 0"
    elif member_tab == "normal":
        cond += " AND COALESCE(u.member_level,0) = 0 AND COALESCE(u.is_admin,0) = 0"
    elif member_tab == "admin":
        cond += " AND COALESCE(u.is_admin,0) = 1"
    if tag:
        cond += " AND EXISTS (SELECT 1 FROM user_tags t WHERE t.uid=u.id AND t.tag=?)"
        params.append(str(tag))
    total = conn.execute(
        "SELECT COUNT(*) FROM users u WHERE 1=1" + cond, params).fetchone()[0]
    rows = conn.execute(
        "SELECT u.id, u.username, u.created_at, u.is_admin, u.invite_code, u.invited_by, "
        "u.phone, u.email, u.wx_name, u.remark, u.pay_remark, u.expire_at, u.member_level, "
        "(SELECT username FROM users WHERE id = u.invited_by) AS invited_by_username, "
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
                expire_days=None, register_ip=None, register_ua=None, email_verified=1,
                member_level=None):
    """创建用户. expire_days>0 注册即送 N 天会员(默认 config.NEW_USER_DAYS 天试用); 0 表示永久
    register_ip/register_ua: 注册时的 IP 与 UA(用于同 IP 防刷/自邀识别)
    email_verified: 默认 1(已放弃邮箱验证, 老链路统一视为已验证); 如需强制留 0
    member_level: 注册赠送期间的会员等级, 默认 config.NEW_USER_MEMBER_LEVEL(1=完整体验),
                  传 0 则仅延长试用期不给会员权益"""
    if expire_days is None:
        expire_days = config.NEW_USER_DAYS
    if member_level is None:
        member_level = getattr(config, "NEW_USER_MEMBER_LEVEL", 1)
    now = int(__import__("time").time())
    expire_at = now + int(expire_days) * 86400 if (expire_days or 0) > 0 else 0
    conn = database.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash, created_at, expire_at, invited_by, invite_code, "
        "phone, email, register_ip, register_ua, email_verified, member_level) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (username, security.hash_password(password), now, expire_at,
         invited_by, invite_code, phone, email, register_ip, register_ua,
         1 if email_verified else 0, int(member_level or 0)))
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
    """发送密码重置邮件(纯标准库 smtplib)

    2026-10-08 主人定名：「网站和 app 名称都是快选股」⇒ 正文落款与邮件主题统一补「股」。
    （发件显示名早在 2026-08-16 就已指定为「快选股」，只有这两处漏了。）
    """
    body = (
        "你好 %s：\n\n"
        "我们收到了重置密码的请求。请点击下面的链接设置新密码"
        "（30 分钟内有效，仅可使用一次）：\n\n%s\n\n"
        "如果这不是你本人的操作，请忽略本邮件，你的密码不会改变。\n\n"
        "—— 快选股"
    ) % (username, reset_url)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header("快选股 - 重置密码", "utf-8")
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
