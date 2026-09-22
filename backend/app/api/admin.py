# -*- coding: utf-8 -*-
"""
管理端路由: 仅管理员可访问
==========================
- GET  /api/admin/users          用户列表(分页) + 统计
- POST /api/admin/users/expire   设置/续费账号到期时间
- GET  /api/admin/scoring        当前评分权重
- PUT  /api/admin/scoring        更新评分权重(保存后即时生效)
"""
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..core import config, logger
from ..services import scorer, settings, users
from .deps import client_ip, get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


def _conn():
    """管理端专用连接: 必须设 row_factory=sqlite3.Row。
    ★ 不能用 database.get_conn() —— 它未设 row_factory, 返回 tuple,
      导致本文件大量 `dict(r)` 抛
      TypeError: cannot convert dictionary update sequence element #0 to a sequence。
      (2026-09-21 T175 回归实测 /api/admin/risk、/api/admin/invite-rank 500)
    与 users.py / stats.py / history.py 的 _conn() 保持同一语义。
    """
    conn = sqlite3.connect(config.DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

# 五项权重系数(key, 中文名, 说明)
W_KEYS = [
    ("w_bid", "竞价分", "竞价涨幅区间得分(如3%~5.5%为满分)"),
    ("w_activity", "活跃度", "竞价换手率/量比活跃度得分"),
    ("w_warn", "异动分", "封单/抢筹等异动信号得分"),
    ("w_market", "市值分", "自由流通市值越小分越高(小市值加分)"),
    ("w_yesterday", "昨日涨幅", "昨日涨幅处于健康区间得分"),
]
CONF_KEYS = [
    ("conf_warn_high", "强异动", "异动等级>=4 时置信度加成"),
    ("conf_turnover", "高换手", "竞价换手>=0.4 时置信度加成"),
    ("conf_bid", "温和竞价", "竞价涨幅2%~6.5%时置信度加成"),
]

def get_admin(request: Request, uid: int = Depends(get_uid)):
    """管理员依赖: 未登录 401; 非管理员 403"""
    users.ensure_admin()   # 幂等初始化(ADMIN_USERNAME 或 id 最小用户)
    if not users.is_admin(uid):
        log.warning("非管理员访问管理端 uid=%s path=%s", uid, request.url.path)
        raise HTTPException(status_code=403, detail={"ok": False, "msg": "无管理员权限"})
    return uid


@router.get("/api/admin/users")
def api_admin_users(request: Request, uid: int = Depends(get_admin)):
    q = qs(request)
    try:
        page = max(1, int((q.get("page") or [1])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int((q.get("pageSize") or [20])[0])))
    except (TypeError, ValueError):
        page_size = 20
    keyword = (q.get("keyword") or [""])[0].strip()
    member_tab = (q.get("memberTab") or ["all"])[0].strip()
    if member_tab not in ("all", "member", "paid", "vip", "normal", "admin"):
        member_tab = "all"
    page_data = users.list_users_page(page, page_size, keyword, member_tab=member_tab)
    stats = users.user_stats()
    log.info("管理端用户列表 uid=%s page=%s member_tab=%s total=%s",
             uid, page, member_tab, page_data["total"])
    return jr({"ok": True, "stats": stats, **page_data})


# 时长档位(天): 周/月/季/年
EXPIRE_DURATIONS = {"week": 7, "month": 30, "quarter": 90, "year": 365}


@router.post("/api/admin/users/expire")
def api_admin_user_expire(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    """设置账号到期: 支持
    - {uid, duration: week|month|quarter|year} 从 max(现在,当前到期) 累加
    - {uid, days: N}                           累加 N 天(0=永久)
    - {uid, expire_at: "YYYY-MM-DD"}           直接设到期日期(北京当日 23:59:59)
    返回该用户最新到期时间戳。
    """
    target = int(body.get("uid") or 0)
    if target <= 0:
        return jr({"ok": False, "msg": "缺少 uid"}, 400)
    u = users.find_user_by_id(target)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)

    expire_at = body.get("expire_at")
    duration = body.get("duration")
    days = body.get("days")

    if expire_at:
        try:
            dt = datetime.strptime(str(expire_at), "%Y-%m-%d")
        except ValueError:
            return jr({"ok": False, "msg": "日期格式应为 YYYY-MM-DD"}, 400)
        bj = timezone(timedelta(hours=8))
        ts = int(dt.replace(tzinfo=bj).timestamp()) + 86399   # 北京当日 23:59:59
        users.set_expire(target, ts)
    elif duration in EXPIRE_DURATIONS:
        users.extend_expire(target, EXPIRE_DURATIONS[duration])
    elif days is not None:
        try:
            d = int(days)
        except (TypeError, ValueError):
            return jr({"ok": False, "msg": "days 应为整数"}, 400)
        if d <= 0:
            users.set_expire(target, 0)    # 0/负 = 永久
        else:
            users.extend_expire(target, d)
    else:
        return jr({"ok": False, "msg": "需要 duration / days / expire_at 之一"}, 400)

    row = users.find_user_by_id(target)
    users.audit(uid, "set_expire", target,
                {"duration": duration, "days": days, "expire_at": expire_at,
                 "new_expire_at": row.get("expire_at")}, client_ip(request))
    log.info("管理端设置到期 uid=%s target=%s(%s) expire_at=%s",
             uid, target, u["username"], row.get("expire_at"))
    return jr({"ok": True, "msg": "已设置", "uid": target,
               "username": u["username"], "expire_at": row.get("expire_at")})


@router.get("/api/admin/user-invites")
def api_admin_user_invites(request: Request, uid: int = Depends(get_admin),
                           target_uid: int = 0):
    """邀请关系链: 指定用户被谁邀请 + 邀请了谁(含注册 IP, 便于管理员识别同 IP 刷号)"""
    if not target_uid:
        return jr({"ok": False, "msg": "缺少 target_uid"}, 400)
    u = users.find_user_by_id(target_uid)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    inviter = users.find_user_by_id(u.get("invited_by") or 0) if u.get("invited_by") else None
    # 被邀请人列表(管理端视角: 含注册 IP/UA)
    import sqlite3 as _sq
    conn = None
    invitees = []
    try:
        conn = _conn()
        invitees = [dict(r) for r in conn.execute(
            "SELECT username, created_at, COALESCE(register_ip,'') AS register_ip "
            "FROM users WHERE invited_by=? ORDER BY created_at DESC", (target_uid,)).fetchall()]
    finally:
        if conn is not None:
            conn.close()
    log.info("管理端查看邀请链 uid=%s target=%s(%s) 被邀%d人",
             uid, target_uid, u["username"], len(invitees))
    return jr({"ok": True, "username": u["username"],
               "invited_by_username": (inviter["username"] if inviter else ""),
               "invite_code": u.get("invite_code") or "",
               "invited_count": len(invitees),
               "invitees": invitees})


@router.post("/api/admin/users/expire-batch")
def api_admin_user_expire_batch(request: Request, body: dict = Body(...),
                                uid: int = Depends(get_admin)):
    """批量设置到期(与单个 /expire 同语义): {uids: [..], duration|days|expire_at}
    2026-08-17 主人需求: 批量给用户设置会员时间
    返回 {ok, success, total, failed: [{uid, msg}...]}"""
    uids = [int(x) for x in (body.get("uids") or []) if str(x).isdigit()]
    if not uids:
        return jr({"ok": False, "msg": "缺少 uids"}, 400)
    expire_at = body.get("expire_at")
    duration = body.get("duration")
    days = body.get("days")
    ok_n = 0
    failed = []
    for target in uids:
        try:
            u = users.find_user_by_id(target)
            if not u:
                failed.append({"uid": target, "msg": "用户不存在"})
                continue
            if expire_at:
                try:
                    dt = datetime.strptime(str(expire_at), "%Y-%m-%d")
                except ValueError:
                    failed.append({"uid": target, "msg": "日期格式错"})
                    continue
                bj = timezone(timedelta(hours=8))
                ts = int(dt.replace(tzinfo=bj).timestamp()) + 86399   # 北京当日 23:59:59
                users.set_expire(target, ts)
            elif duration in EXPIRE_DURATIONS:
                users.extend_expire(target, EXPIRE_DURATIONS[duration])
            elif days is not None:
                d = int(days)
                if d <= 0:
                    users.set_expire(target, 0)    # 0/负 = 永久
                else:
                    users.extend_expire(target, d)
            else:
                failed.append({"uid": target, "msg": "缺少时长参数"})
                continue
            ok_n += 1
        except Exception as e:
            failed.append({"uid": target, "msg": str(e)[:60]})
    log.info("管理端批量设置到期 admin_uid=%s 成功%d/%d 失败%d",
             uid, ok_n, len(uids), len(failed))
    return jr({"ok": True, "msg": "批量设置完成: 成功 %d/%d" % (ok_n, len(uids)),
               "success": ok_n, "total": len(uids), "failed": failed})


@router.post("/api/admin/users/profile")
def api_admin_user_profile(request: Request, body: dict = Body(...),
                           uid: int = Depends(get_admin)):
    """管理员代编辑用户资料: {uid, phone?, email?, wx_name?, remark?, pay_remark?}
    只更新提供的字段; 复用 update_profile 的格式+唯一性校验。
    pay_remark 是会员专属付款备注(管理员可改, 用户自己改不动)。
    例如 {uid: 5, wx_name: "北棠", remark: "8月新用户", pay_remark: "8-16微信月付"}"""
    target = int(body.get("uid") or 0)
    if target <= 0:
        return jr({"ok": False, "msg": "缺少 uid"}, 400)
    u = users.find_user_by_id(target)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    # 用户自助字段: 走 update_profile (含格式/唯一性校验)
    user_fields = {}
    for k in ("phone", "email", "wx_name", "remark"):
        if k in body and body.get(k) is not None:
            user_fields[k] = body.get(k)
    if user_fields:
        ok, msg = users.update_profile(target, **user_fields)
        if not ok:
            return jr({"ok": False, "msg": msg}, 400)
    # 会员付款备注: 仅管理员可改, 无格式校验(自由文本)
    if "pay_remark" in body and body.get("pay_remark") is not None:
        pay_remark = (str(body.get("pay_remark")) or "").strip() or None
        if pay_remark and len(pay_remark) > 500:
            return jr({"ok": False, "msg": "付款备注过长(限500字)"}, 400)
        users.set_pay_remark(target, pay_remark)
    if not user_fields and "pay_remark" not in body:
        return jr({"ok": False, "msg": "没有要更新的字段(支持 phone/email/wx_name/remark/pay_remark)"}, 400)
    row = users.find_user_by_id(target)
    log.info("管理端编辑资料 uid=%s target=%s(%s) fields=%s",
             uid, target, u["username"],
             list(user_fields.keys()) + (["pay_remark"] if "pay_remark" in body else []))
    return jr({"ok": True, "msg": "资料已更新", "uid": target, "username": u["username"],
               "phone": row.get("phone"), "email": row.get("email"),
               "wx_name": row.get("wx_name"), "remark": row.get("remark"),
               "pay_remark": row.get("pay_remark")})


@router.post("/api/admin/users/create")
def api_admin_user_create(request: Request, body: dict = Body(...),
                          uid: int = Depends(get_admin)):
    """管理员代创建账号(开通会员): {username, password, phone, email,
        member_level?, expire_at?, invite_code?, wx_name?, remark?, pay_remark?}
    - member_level: 0=免费试用 1=付费会员 2=VIP老师 (默认 1)
    - expire_at: 'YYYY-MM-DD' 或 'forever' 或 'days:N' 或 省略 (默认 30 天后到期)
    - invite_code: 邀请码(管理员账号可填自己的, 也可空)
    - wx_name/remark/pay_remark: 初始化资料
    """
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    phone = str(body.get("phone") or "").strip()
    email = str(body.get("email") or "").strip()
    if not re.match(r"^[\u4e00-\u9fa5a-zA-Z0-9_]{2,20}$", username):
        return jr({"ok": False, "msg": "用户名需 2-20 位，支持中英文/数字/下划线"}, 400)
    if len(password) < 6:
        return jr({"ok": False, "msg": "密码至少 6 位"}, 400)
    if not users._is_phone(phone):
        return jr({"ok": False, "msg": "手机号格式不正确"}, 400)
    if not users._is_email(email):
        return jr({"ok": False, "msg": "邮箱格式不正确"}, 400)
    if users.find_user(username):
        return jr({"ok": False, "msg": "用户名已存在"}, 409)
    if phone and users.find_user_by_phone(phone):
        return jr({"ok": False, "msg": "该手机号已绑定其他账号"}, 409)
    if email and users.find_user_by_email(email):
        return jr({"ok": False, "msg": "该邮箱已绑定其他账号"}, 409)
    # 邀请码: 可选(管理员代建时通常留空)
    invite_code = str(body.get("invite_code") or "").strip().upper()
    invited_by = None
    if invite_code:
        inv = users.find_user_by_invite_code(invite_code)
        if inv is None:
            return jr({"ok": False, "msg": "邀请码无效"}, 400)
        invited_by = inv["id"]
    my_code = users.gen_unique_invite_code()
    try:
        new_uid = users.create_user(username, password, invited_by=invited_by,
                                    invite_code=my_code, phone=phone or None,
                                    email=email or None, email_verified=1)
    except sqlite3.IntegrityError as e:
        log.warning("管理员创建账号冲突 username=%s err=%s", username, e)
        return jr({"ok": False, "msg": "用户名或手机号/邮箱已被占用"}, 409)
    # 会员等级
    member_level = int(body.get("member_level") or 1)
    if not users.set_member_level(new_uid, member_level):
        log.warning("管理员创建账号非法等级 uid=%s level=%s", new_uid, member_level)
    # 到期时间: 默认 30 天后; 支持多种格式
    expire_cfg = body.get("expire_at")
    days_cfg = body.get("days")
    if expire_cfg == "forever" or days_cfg == 0 or days_cfg == "0":
        users.set_expire(new_uid, 0)
    elif isinstance(expire_cfg, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", expire_cfg):
        try:
            dt = datetime.strptime(expire_cfg, "%Y-%m-%d")
            bj = timezone(timedelta(hours=8))
            ts = int(dt.replace(tzinfo=bj).timestamp()) + 86399
            users.set_expire(new_uid, ts)
        except ValueError:
            users.extend_expire(new_uid, 30)
    elif days_cfg is not None:
        try:
            d = int(days_cfg)
            users.extend_expire(new_uid, d if d > 0 else 30)
        except (TypeError, ValueError):
            users.extend_expire(new_uid, 30)
    else:
        users.extend_expire(new_uid, 30)
    # 资料字段
    user_fields = {}
    for k in ("wx_name", "remark"):
        if k in body and body.get(k) is not None:
            v = str(body.get(k) or "").strip() or None
            if v:
                user_fields[k] = v
    if user_fields:
        users.update_profile(new_uid, **user_fields)
    pay_remark = body.get("pay_remark")
    if pay_remark is not None:
        v = (str(pay_remark) or "").strip() or None
        if v and len(v) > 500:
            return jr({"ok": False, "msg": "付款备注过长(限500字)"}, 400)
        users.set_pay_remark(new_uid, v)
    row = users.find_user_by_id(new_uid)
    log.info("管理端创建账号 uid=%s new_uid=%s(%s) level=%s expire_at=%s pay_remark=%s",
             uid, new_uid, username, member_level, row.get("expire_at"), pay_remark)
    return jr({"ok": True, "msg": "账号已创建", "uid": new_uid, "username": username,
               "phone": row.get("phone"), "email": row.get("email"),
               "member_level": row.get("member_level"),
               "expire_at": row.get("expire_at"),
               "wx_name": row.get("wx_name"), "remark": row.get("remark"),
               "pay_remark": row.get("pay_remark"),
               "password": password})   # 返回初始密码方便告知用户


@router.post("/api/admin/users/delete")
def api_admin_user_delete(request: Request, body: dict = Body(...),
                          uid: int = Depends(get_admin)):
    """管理员删除用户: {uid} 或 {username}
    安全检查: 不能删除自己; 不能删除其他管理员; 会级联清理 tokens/选股记录等。
    """
    target = int(body.get("uid") or 0)
    username = str(body.get("username") or "").strip()
    if target > 0:
        u = users.find_user_by_id(target)
    elif username:
        u = users.find_user_by_login(username)
    else:
        return jr({"ok": False, "msg": "缺少 uid 或 username"}, 400)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    if int(u["id"]) == uid:
        return jr({"ok": False, "msg": "不能删除自己(防误操作)"}, 400)
    if int(u.get("is_admin") or 0):
        return jr({"ok": False, "msg": "不能删除其他管理员账号"}, 400)
    ok = users.delete_user(u["id"])
    if not ok:
        return jr({"ok": False, "msg": "删除失败"}, 500)
    log.warning("管理端删除账号 uid=%s target=%s(%s) ip=%s", uid, u["id"], u["username"], client_ip(request))
    return jr({"ok": True, "msg": "账号已删除", "uid": u["id"], "username": u["username"]})


@router.post("/api/admin/users/member-level")
def api_admin_user_member_level(request: Request, body: dict = Body(...),
                                uid: int = Depends(get_admin)):
    """设置用户会员等级: {uid, level: 0|1|2}
    0=免费试用 1=付费会员 2=VIP老师(永久权限, 永不拦截)
    注: VIP老师 建议同时 expire_at 设为 0(永久); 等级只是标识, 实际拦截仍看 expire_at"""
    target = int(body.get("uid") or 0)
    if target <= 0:
        return jr({"ok": False, "msg": "缺少 uid"}, 400)
    u = users.find_user_by_id(target)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    level = body.get("level")
    if not users.set_member_level(target, level):
        return jr({"ok": False, "msg": "等级非法(仅支持 0/1/2)"}, 400)
    log.info("管理端设置会员等级 uid=%s target=%s(%s) level=%s", uid, target, u["username"], level)
    return jr({"ok": True, "msg": "会员等级已更新",
               "uid": target, "username": u["username"], "member_level": int(level),
               "label": users.MEMBER_LEVEL_LABEL.get(int(level), "")})


@router.post("/api/admin/users/reset-password")
def api_admin_user_reset_password(request: Request, body: dict = Body(...),
                                  uid: int = Depends(get_admin)):
    """管理员重置用户密码: {uid, password} 或 {username, password}
    密码至少 6 位; 不能重置自己的密码(防止误操作锁死管理员)。"""
    target = int(body.get("uid") or 0)
    username = str(body.get("username") or "").strip()
    new_pw = str(body.get("password") or "")

    if not new_pw or len(new_pw) < 6:
        return jr({"ok": False, "msg": "新密码至少 6 位"}, 400)
    if len(new_pw) > 64:
        return jr({"ok": False, "msg": "新密码过长(最多64位)"}, 400)

    if target > 0:
        u = users.find_user_by_id(target)
    elif username:
        u = users.find_user_by_login(username)
    else:
        return jr({"ok": False, "msg": "缺少 uid 或 username"}, 400)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    if int(u.get("id")) == uid:
        return jr({"ok": False, "msg": "不能重置自己的密码(请用修改密码功能)"}, 400)

    if not users.set_password(u["id"], new_pw):
        return jr({"ok": False, "msg": "重置失败"}, 500)
    log.info("管理端重置密码 uid=%s target=%s(%s) ip=%s", uid, u["id"], u["username"], client_ip(request))
    return jr({"ok": True, "msg": "密码已重置", "uid": u["id"], "username": u["username"]})


@router.get("/api/admin/scoring")
def api_admin_scoring_get(request: Request, uid: int = Depends(get_admin)):
    # 2026-09-09 命名消歧(与 /api/stocks 同步): 策略参数 mode → strategy ——
    # 策略=用哪套因子表(2026-09-09 起仅 auction 竞价; spot 盘中已下线), 与内部时段 PickMode 是两回事;
    # 旧参数 mode 保留为兼容别名(前端 dist 缓存/书签仍在传), 下版本移除。
    strategy = (qs(request).get("strategy") or qs(request).get("mode") or ["auction"])[0]
    if strategy != "auction":
        return jr({"ok": False, "msg": "非法 strategy(盘中实时选股已下线)"}, 400)
    cfg = scorer.get_scoring_cfg(strategy=strategy)
    return jr({"ok": True, "strategy": "auction", "mode": "auction",
               "scoring": cfg, "w_keys": W_KEYS, "conf_keys": CONF_KEYS})


@router.put("/api/admin/scoring")
def api_admin_scoring_put(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    # 2026-09-09 命名消歧: mode → strategy(旧 mode 兼容; body 与 query 都认)
    strategy = str(body.get("strategy") or body.get("mode")
                   or (qs(request).get("strategy") or qs(request).get("mode") or ["auction"])[0]
                   or "auction")
    if strategy != "auction":
        return jr({"ok": False, "msg": "非法 strategy(盘中实时选股已下线)"}, 400)
    new = body.get("scoring")
    if not isinstance(new, dict) or not new:
        return jr({"ok": False, "msg": "缺少 scoring 配置"}, 400)
    err = _validate_scoring(new, strategy)
    if err:
        return jr({"ok": False, "msg": err}, 400)
    if not settings.set("scoring", new):
        return jr({"ok": False, "msg": "保存失败"}, 500)
    scorer.reload_scoring_cfg()
    log.info("管理端更新评分权重 uid=%s scoring=%s", uid, new)
    return jr({"ok": True, "msg": "已保存并生效", "strategy": strategy, "mode": strategy,
               "scoring": scorer.get_scoring_cfg(strategy=strategy)})


def _validate_scoring(new, strategy="auction"):
    """校验权重/置信度/打分明细(2026-09-09 spot 下线后只校验竞价因子表)"""
    default_cfg = scorer.DEFAULT_SCORING
    w_keys = W_KEYS
    conf_keys = CONF_KEYS
    w_sum = 0.0
    for k, _, _ in w_keys:
        try:
            v = float(new.get(k))
        except (TypeError, ValueError):
            return "权重 %s 必须是数字" % k
        if not (0 <= v <= 1):
            return "权重 %s 需在 0~1 之间" % k
        w_sum += v
    if abs(w_sum - 1.0) > 0.03:
        return "权重之和需约等于 1(当前 %.2f)" % w_sum
    for k, _, _ in conf_keys:
        try:
            v = float(new.get(k))
        except (TypeError, ValueError):
            return "置信度加成 %s 必须是数字" % k
        if not (0 <= v <= 30):
            return "置信度加成 %s 需在 0~30 之间" % k
    # 打分明细: 因子表, 每因子 buckets 为 [下限, 上限, 得分] 且 下限<上限, 得分 0~1
    factors = new.get("factors")
    if factors is not None:
        if not isinstance(factors, dict):
            return "打分明细格式错误"
        for fk, fv in factors.items():
            if fk not in default_cfg["factors"]:
                return "未知因子: %s" % fk
            if not isinstance(fv, dict) or not isinstance(fv.get("buckets"), list) or not fv["buckets"]:
                return "因子 %s 缺少有效的分档表" % fk
            try:
                dflt = float(fv.get("default", 0.1))
            except (TypeError, ValueError):
                return "因子 %s 的默认得分必须是数字" % fk
            if not (0 <= dflt <= 1):
                return "因子 %s 的默认得分需在 0~1 之间" % fk
            for b in fv["buckets"]:
                if not isinstance(b, (list, tuple)) or len(b) != 3:
                    return "因子 %s 分档必须为 [下限, 上限, 得分]" % fk
                try:
                    lo, hi, sc = float(b[0]), float(b[1]), float(b[2])
                except (TypeError, ValueError):
                    return "因子 %s 分档数值不合法" % fk
                if lo >= hi:
                    return "因子 %s 分档下限需小于上限" % fk
                if not (0 <= sc <= 1):
                    return "因子 %s 分档得分需在 0~1 之间" % fk
    return ""


# ---------- 全局默认筛选参数(管理员可调, 存 settings 表 key=default_filters) ----------
# 所有用户首次进入/未自定义偏好时使用的默认值(如默认竞价金额下限 1000万)
# 前端加载顺序: 后端默认值 > 用户偏好 > 前端内置默认
# 2026-08-25 语义改为正逻辑: limitUp/stSuspend = True → "只看这类票", False → "剔除这类票".
#   默认 False 等价于旧默认(勾上=剔除ST/剔除昨涨停), 实际过滤结果一致但 UI 直觉正确.
DEFAULT_FILTERS_DEFAULT = {
    "stSuspend": False, "limitUp": False, "bidGt": 7.0,
    "probLt": 50.0, "confLt": 50.0,
    "floatMvFloor": 30.0, "floatMvGt": 1000.0, "priceGt": 300.0,
    "bidAmtFloor": 1000.0,   # 诗人需求: 默认竞价金额下限 1000万(原3000)
    "scoreFloor": 50.0,      # 2026-09-20 主人拍板: 评分低于 50 分的票不显示(全站默认)
}


def get_default_filters():
    """读取全局默认筛选参数(不存在则返回内置默认)"""
    cfg = settings.get("default_filters")
    if isinstance(cfg, dict):
        merged = dict(DEFAULT_FILTERS_DEFAULT)
        for k, v in cfg.items():
            if k in merged:
                merged[k] = v
        return merged
    return dict(DEFAULT_FILTERS_DEFAULT)


@router.get("/api/admin/defaults")
def api_admin_defaults_get(request: Request, uid: int = Depends(get_admin)):
    return jr({"ok": True, "defaults": get_default_filters()})


@router.put("/api/admin/defaults")
def api_admin_defaults_put(request: Request, body: dict = Body(...), uid: int = Depends(get_admin)):
    new = body.get("defaults")
    force = bool(body.get("force"))   # force=true: 保存后清除所有用户筛选偏好, 强制全量生效(保留主题)
    if not isinstance(new, dict) or not new:
        return jr({"ok": False, "msg": "缺少 defaults 配置"}, 400)
    # 只接受已知字段, 数字/布尔校验
    cur = get_default_filters()
    for k, v in new.items():
        if k not in cur:
            return jr({"ok": False, "msg": "未知字段: %s" % k}, 400)
        if isinstance(cur[k], bool) and not isinstance(v, bool):
            return jr({"ok": False, "msg": "%s 需为布尔值" % k}, 400)
        if isinstance(cur[k], (int, float)) and not isinstance(v, (int, float)):
            return jr({"ok": False, "msg": "%s 需为数字" % k}, 400)
        cur[k] = v
    if not settings.set("default_filters", cur):
        return jr({"ok": False, "msg": "保存失败"}, 500)
    cleared = 0
    msg = "全局默认筛选参数已更新"
    if force:
        cleared = users.clear_filter_prefs_all()
        msg += "，已强制重置 %d 个用户的筛选偏好" % cleared
    log.info("管理端更新全局默认筛选参数 uid=%s force=%s defaults=%s cleared=%d",
             uid, force, cur, cleared)
    return jr({"ok": True, "msg": msg, "defaults": cur, "forceCleared": cleared})


# ==================== 会员体系增强(2026-09-21) ====================
# 运营看板 / 到期预警 / 风控视图 / 审计日志 / 导出 / 邀请战绩 /
# 短信用量 / 批量导入 / 权益配置 / 用户详情 / 配额重置 / 一键续期
# 设计: 全部只读聚合或配置读写, 不引入新表(复用 users/phone_claims/user_checkin/admin_audit)

@router.get("/api/admin/dashboard")
def api_admin_dashboard(request: Request, uid: int = Depends(get_admin)):
    """运营看板: 用户/会员/邀请/签到/风控 一屏聚合(北京时间口径)"""
    from ..services import activity as act_svc
    from ..services import quota as quota_svc

    conn = _conn()
    try:
        now = int(time.time())
        d1, d7, d30 = now - 86400, now - 7 * 86400, now - 30 * 86400

        def one(sql, args=()):
            try:
                return conn.execute(sql, args).fetchone()[0] or 0
            except Exception:
                return 0

        total = one("SELECT COUNT(*) FROM users")
        admins = one("SELECT COUNT(*) FROM users WHERE COALESCE(is_admin,0)=1")
        paid = one("SELECT COUNT(*) FROM users WHERE COALESCE(member_level,0)=1 AND COALESCE(is_admin,0)=0")
        vip = one("SELECT COUNT(*) FROM users WHERE COALESCE(member_level,0)=2 AND COALESCE(is_admin,0)=0")
        perm = one("SELECT COUNT(*) FROM users WHERE COALESCE(expire_at,0)=0 AND COALESCE(is_admin,0)=0")
        expired = one("SELECT COUNT(*) FROM users WHERE expire_at>0 AND expire_at<?", (now,))
        active7 = one("SELECT COUNT(DISTINCT user_id) FROM batches WHERE created_at>=?", (d7,))
        # 今日零点(北京时间)对应的 UTC 时间戳
        today0 = int(time.mktime(time.strptime(
            time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600)), "%Y-%m-%d"))) - 8 * 3600
        new_today = one("SELECT COUNT(*) FROM users WHERE created_at>=?", (today0,))
        new_1d = one("SELECT COUNT(*) FROM users WHERE created_at>=?", (d1,))
        new_7d = one("SELECT COUNT(*) FROM users WHERE created_at>=?", (d7,))
        new_30d = one("SELECT COUNT(*) FROM users WHERE created_at>=?", (d30,))
        invited = one("SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL")
        exp3 = one("SELECT COUNT(*) FROM users WHERE expire_at>? AND expire_at<=? AND COALESCE(is_admin,0)=0",
                   (now, now + 3 * 86400))
        exp7 = one("SELECT COUNT(*) FROM users WHERE expire_at>? AND expire_at<=? AND COALESCE(is_admin,0)=0",
                   (now, now + 7 * 86400))
        claims = one("SELECT COUNT(*) FROM phone_claims")
        claims_recent = one("SELECT COUNT(*) FROM phone_claims WHERE first_claim>=?", (d7,))
        checkin_today = one("SELECT COUNT(*) FROM user_checkin WHERE date=?",
                            (time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600)),))
    finally:
        conn.close()

    # 近 14 天注册趋势
    trend = []
    conn = _conn()
    try:
        base = now + 8 * 3600
        for i in range(13, -1, -1):
            day = time.strftime("%Y-%m-%d", time.gmtime(base - i * 86400))
            n = conn.execute(
                "SELECT COUNT(*) FROM users WHERE date(created_at+8*3600,'unixepoch')=?",
                (day,)).fetchone()[0] or 0
            trend.append({"date": day, "count": n})
    except Exception:
        pass
    finally:
        conn.close()

    return jr({"ok": True, "stats": {
        "total": total, "admins": admins, "paid": paid, "vip": vip,
        "member": paid + vip, "permanent": perm, "expired": expired,
        "active_7d": active7, "invited": invited,
        "new_today": new_today, "new_1d": new_1d, "new_7d": new_7d, "new_30d": new_30d,
        "expiring_3d": exp3, "expiring_7d": exp7,
        "phone_claims": claims, "phone_claims_7d": claims_recent,
        "checkin_today": checkin_today,
    }, "trend": trend, "quota": quota_svc.quota_stats(),
        # 2026-09-22 v4.11.35 看板 2 张新卡: 今日登录概况 + 今日功能使用 Top5
        "activity": {"login": act_svc.login_stats(), "usage": act_svc.usage_rank(limit=5)}})


@router.get("/api/admin/expiring")
def api_admin_expiring(request: Request, uid: int = Depends(get_admin)):
    """到期预警列表: ?days=N 窗口天数; ?tab=expiring|expired|all"""
    q = qs(request)
    try:
        days = min(90, max(1, int((q.get("days") or [7])[0])))
    except (TypeError, ValueError):
        days = 7
    tab = (q.get("tab") or ["all"])[0].strip()
    now = int(time.time())
    cond, params = "COALESCE(is_admin,0)=0 AND expire_at>0", []
    if tab == "expiring":
        cond += " AND expire_at>=? AND expire_at<=?"
        params += [now, now + days * 86400]
    elif tab == "expired":
        cond += " AND expire_at<?"
        params += [now]
    else:
        cond += " AND expire_at<=?"
        params += [now + days * 86400]
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, username, phone, wx_name, member_level, expire_at, invited_by, "
            "(SELECT username FROM users x WHERE x.id=u.invited_by) inviter_name "
            "FROM users u WHERE " + cond + " ORDER BY expire_at ASC LIMIT 500", params).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        et = int(d.get("expire_at") or 0)
        d["expire_date"] = time.strftime("%Y-%m-%d", time.gmtime(et + 8 * 3600))
        d["days_left"] = int((et - now) // 86400)
        d["expired"] = 1 if et < now else 0
        out.append(d)
    log.info("管理端到期预警 uid=%s tab=%s days=%s n=%d", uid, tab, days, len(out))
    return jr({"ok": True, "rows": out, "count": len(out), "days": days, "tab": tab})


@router.get("/api/admin/risk")
def api_admin_risk(request: Request, uid: int = Depends(get_admin)):
    """风控视图: 同 IP 批量注册 / 同 IP 邀请 / 手机号重复领取 / 邀请榜"""
    conn = _conn()
    try:
        ip_reg = [dict(r) for r in conn.execute(
            "SELECT COALESCE(register_ip,'') ip, COUNT(*) n, MIN(created_at) first_at, "
            "GROUP_CONCAT(username, ',') names FROM users "
            "WHERE COALESCE(register_ip,'')!='' GROUP BY register_ip HAVING n>=2 "
            "ORDER BY n DESC LIMIT 30").fetchall()]
        ip_inv = [dict(r) for r in conn.execute(
            "SELECT inviter.username inviter, COUNT(*) n, COALESCE(x.register_ip,'') ip "
            "FROM users x JOIN users inviter ON inviter.id=x.invited_by "
            "WHERE COALESCE(x.register_ip,'')!='' AND x.register_ip=inviter.register_ip "
            "GROUP BY x.invited_by ORDER BY n DESC LIMIT 30").fetchall()]
        top_inv = [dict(r) for r in conn.execute(
            "SELECT u.id, u.username, u.member_level, u.expire_at, COUNT(*) n "
            "FROM users x JOIN users u ON u.id=x.invited_by "
            "GROUP BY x.invited_by ORDER BY n DESC LIMIT 30").fetchall()]
        dup_claim = [dict(r) for r in conn.execute(
            "SELECT phone, claim_count, first_uid, last_ip, first_claim FROM phone_claims "
            "WHERE claim_count>1 ORDER BY claim_count DESC LIMIT 30").fetchall()]
        recent_claim = [dict(r) for r in conn.execute(
            "SELECT p.phone, p.first_uid, u.username, p.first_claim, p.last_ip "
            "FROM phone_claims p LEFT JOIN users u ON u.id=p.first_uid "
            "ORDER BY p.first_claim DESC LIMIT 50").fetchall()]
    finally:
        conn.close()
    for r in ip_reg:
        if r.get("first_at"):
            r["first_date"] = time.strftime("%Y-%m-%d %H:%M",
                                            time.gmtime(int(r["first_at"]) + 8 * 3600))
    for r in recent_claim:
        if r.get("first_claim"):
            r["first_date"] = time.strftime("%Y-%m-%d %H:%M",
                                            time.gmtime(int(r["first_claim"]) + 8 * 3600))
    log.info("管理端风控视图 uid=%s ip注册%d组 同IP邀请%d组 重复领取%d条",
             uid, len(ip_reg), len(ip_inv), len(dup_claim))
    return jr({"ok": True, "ip_register": ip_reg, "same_ip_invite": ip_inv,
               "top_inviters": top_inv, "dup_claims": dup_claim,
               "recent_claims": recent_claim})


@router.get("/api/admin/invite-rank")
def api_admin_invite_rank(request: Request, uid: int = Depends(get_admin)):
    """邀请战绩榜: 邀请人数 / 已获奖励天数"""
    q = qs(request)
    try:
        limit = min(200, max(1, int((q.get("limit") or [50])[0])))
    except (TypeError, ValueError):
        limit = 50
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT u.id, u.username, u.phone, u.wx_name, u.member_level, u.expire_at, "
            "COUNT(x.id) n, COALESCE(SUM(CASE WHEN x.expire_at>0 THEN 1 ELSE 0 END),0) active_n "
            "FROM users u JOIN users x ON x.invited_by=u.id "
            "GROUP BY u.id ORDER BY n DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    from ..core import config as _cfg
    reward_days = int(getattr(_cfg, "INVITE_REWARD_DAYS", 5))
    out = []
    for r in rows:
        d = dict(r)
        d["earned_days"] = int(d.get("n") or 0) * reward_days
        out.append(d)
    return jr({"ok": True, "rows": out, "reward_days": reward_days})


@router.get("/api/admin/sms-usage")
def api_admin_sms_usage(request: Request, uid: int = Depends(get_admin)):
    """短信用量: 从 kv_cache 汇总 sms 计数键。
    注意: 计数键带 TTL, 只反映仍在有效期内的窗口, 不是长期账单。"""
    conn = _conn()
    rows = []
    try:
        cur = conn.execute("SELECT key, value, expire_at FROM kv_cache "
                           "WHERE key LIKE 'sms:%' ORDER BY key LIMIT 2000")
        for r in cur.fetchall():
            rows.append({"key": r[0], "value": r[1], "expire_at": r[2]})
    except Exception as e:
        log.info("短信用量读取失败(可能非 sqlite 后端): %s", e)
    finally:
        conn.close()
    scenes, ip_keys, phone_keys = {}, 0, 0
    for r in rows:
        k = r["key"]
        if k.startswith("sms:used:"):
            parts = k.split(":")
            sc = parts[2] if len(parts) > 2 else "unknown"
            scenes[sc] = scenes.get(sc, 0) + 1
        elif k.startswith("sms:ip:"):
            ip_keys += 1
        elif k.startswith("sms:phone:"):
            phone_keys += 1
    return jr({"ok": True, "scenes": scenes, "phone_limited": phone_keys,
               "ip_limited": ip_keys, "raw_count": len(rows),
               "note": "计数键带 TTL，仅统计仍在有效期内的窗口"})


@router.get("/api/admin/audit")
def api_admin_audit(request: Request, uid: int = Depends(get_admin)):
    """操作审计日志(分页)"""
    q = qs(request)
    try:
        page = max(1, int((q.get("page") or [1])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        ps = min(100, max(1, int((q.get("pageSize") or [30])[0])))
    except (TypeError, ValueError):
        ps = 30
    action = (q.get("action") or [""])[0].strip()
    data = users.audit_list(page, ps, action)
    return jr({"ok": True, **data})


@router.get("/api/admin/audit/actions")
def api_admin_audit_actions(request: Request, uid: int = Depends(get_admin)):
    """审计动作种类(供筛选下拉)"""
    conn = _conn()
    try:
        rows = conn.execute("SELECT action, COUNT(*) n FROM admin_audit "
                            "GROUP BY action ORDER BY n DESC").fetchall()
    finally:
        conn.close()
    return jr({"ok": True, "actions": [dict(r) for r in rows]})


@router.get("/api/admin/users/export")
def api_admin_users_export(request: Request, uid: int = Depends(get_admin)):
    """导出用户 CSV。★ 带 UTF-8 BOM, 否则 Excel 打开中文乱码。"""
    from fastapi.responses import Response
    q = qs(request)
    keyword = (q.get("keyword") or [""])[0].strip()
    member_tab = (q.get("memberTab") or ["all"])[0].strip()
    conn = _conn()
    try:
        cond, params = "1=1", []
        if keyword:
            kw = "%" + keyword + "%"
            cond += (" AND (u.username LIKE ? OR COALESCE(u.phone,'') LIKE ? "
                     "OR COALESCE(u.wx_name,'') LIKE ? OR COALESCE(u.remark,'') LIKE ?)")
            params += [kw, kw, kw, kw]
        if member_tab == "member":
            cond += " AND COALESCE(u.member_level,0)>0 AND COALESCE(u.is_admin,0)=0"
        elif member_tab == "paid":
            cond += " AND COALESCE(u.member_level,0)=1 AND COALESCE(u.is_admin,0)=0"
        elif member_tab == "vip":
            cond += " AND COALESCE(u.member_level,0)=2 AND COALESCE(u.is_admin,0)=0"
        elif member_tab == "normal":
            cond += " AND COALESCE(u.member_level,0)=0 AND COALESCE(u.is_admin,0)=0"
        rows = conn.execute(
            "SELECT u.id, u.username, COALESCE(u.phone,'') phone, COALESCE(u.wx_name,'') wx_name, "
            "COALESCE(u.member_level,0) member_level, COALESCE(u.expire_at,0) expire_at, "
            "u.created_at, COALESCE(u.register_ip,'') register_ip, "
            "COALESCE(u.invited_by,0) invited_by, u.invite_code, "
            "(SELECT COUNT(*) FROM users x WHERE x.invited_by=u.id) invited_count, "
            "COALESCE(u.remark,'') remark, COALESCE(u.pay_remark,'') pay_remark "
            "FROM users u WHERE " + cond + " ORDER BY u.id DESC", params).fetchall()
    finally:
        conn.close()

    now = int(time.time())
    labels = {0: "免费试用", 1: "付费会员", 2: "VIP老师"}
    lines = ["ID,用户名,手机号,微信名,会员等级,到期时间,剩余天数,注册时间,注册IP,邀请人ID,邀请码,已邀请人数,备注,付款备注"]
    for r in rows:
        et = int(r["expire_at"] or 0)
        exp_s = "永久" if not et else time.strftime("%Y-%m-%d", time.gmtime(et + 8 * 3600))
        dleft = "永久" if not et else str(int((et - now) // 86400))
        cr_s = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(r["created_at"] or 0) + 8 * 3600))
        cells = [r["id"], r["username"], r["phone"], r["wx_name"],
                 labels.get(int(r["member_level"] or 0), ""), exp_s, dleft, cr_s,
                 r["register_ip"], r["invited_by"] or "", r["invite_code"] or "",
                 r["invited_count"], r["remark"], r["pay_remark"]]
        cells = ['"%s"' % str(c).replace('"', '""') for c in cells]
        lines.append(",".join(cells))
    body = "\ufeff" + "\n".join(lines)
    users.audit(uid, "export_users", None, {"count": len(rows), "keyword": keyword},
                client_ip(request))
    log.info("管理端导出用户 uid=%s 行数=%d", uid, len(rows))
    fn = "kuaixuan_users_%s.csv" % time.strftime("%Y%m%d_%H%M")
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % fn})


@router.post("/api/admin/users/import")
def api_admin_users_import(request: Request, body: dict = Body(...),
                           uid: int = Depends(get_admin)):
    """批量导入用户(CSV 文本). body: {csv: "用户名,手机号,天数[,邀请码]", default_password?}
    规则: 每行 3~4 列; 空行/# 开头忽略; 用户名或手机号已存在则跳过; 天数 0 = 永久。"""
    from ..core import config as _cfg
    raw = str(body.get("csv") or "")
    default_pw = str(body.get("default_password") or "Kx123456")
    if not raw.strip():
        return jr({"ok": False, "msg": "缺少 csv 内容"}, 400)
    if len(raw) > 512 * 1024:
        return jr({"ok": False, "msg": "CSV 过大(>512KB)，请分批导入"}, 400)

    created, skipped, failed = [], [], []
    for i, line in enumerate(raw.splitlines(), 1):
        s = line.strip().lstrip("\ufeff")
        if not s or s.startswith("#"):
            continue
        cols = [c.strip().strip('"') for c in re.split(r"[,\t]", s)]
        if len(cols) < 3:
            failed.append({"line": i, "msg": "列数不足(需 用户名,手机号,天数[,邀请码])", "raw": s[:80]})
            continue
        uname, phone, days_s = cols[0], cols[1], cols[2]
        inv_code = cols[3].upper() if len(cols) > 3 and cols[3] else ""
        if not uname:
            failed.append({"line": i, "msg": "用户名为空", "raw": s[:80]})
            continue
        if phone and not re.match(r"^1[3-9]\d{9}$", phone):
            failed.append({"line": i, "msg": "手机号格式不正确: %s" % phone, "raw": s[:80]})
            continue
        try:
            days = int(float(days_s))
        except (TypeError, ValueError):
            failed.append({"line": i, "msg": "天数不是数字: %s" % days_s, "raw": s[:80]})
            continue
        if users.find_user(uname):
            skipped.append({"line": i, "msg": "用户名已存在", "name": uname})
            continue
        if phone and users.find_user_by_phone(phone):
            skipped.append({"line": i, "msg": "手机号已注册", "name": uname, "phone": phone})
            continue
        inviter = users.find_user_by_invite_code(inv_code) if inv_code else None
        try:
            new_uid = users.create_user(
                uname, default_pw, phone=(phone or None),
                invited_by=(inviter["id"] if inviter else None),
                invite_code=users.gen_unique_invite_code(),
                expire_days=days, register_ip="admin-import", register_ua="admin-import",
                email_verified=1,
                member_level=(int(getattr(_cfg, "NEW_USER_MEMBER_LEVEL", 1)) if days > 0 else 0))
            created.append({"line": i, "uid": new_uid, "name": uname, "days": days})
        except Exception as e:
            failed.append({"line": i, "msg": str(e)[:80], "name": uname})
    users.audit(uid, "import_users", None,
                {"created": len(created), "skipped": len(skipped), "failed": len(failed)},
                client_ip(request))
    log.info("管理端批量导入 uid=%s 成功%d 跳过%d 失败%d",
             uid, len(created), len(skipped), len(failed))
    return jr({"ok": True, "msg": "导入完成: 成功 %d / 跳过 %d / 失败 %d" % (
        len(created), len(skipped), len(failed)),
        "created": created, "skipped": skipped, "failed": failed,
        "default_password": default_pw})


# ---- 会员权益配置(settings 表存取, 毫秒级生效, 无需重启) ----
MEMBER_CONF_KEYS = {
    "new_user_days": ("新用户赠送天数", int),
    "new_user_member_level": ("赠送期间会员等级(1=完整体验)", int),
    "invite_reward_days": ("邀请奖励天数", int),
    "quota_picker_daily": ("免费用户选股次数/日", int),
    "quota_aipick_daily": ("免费用户AI预测次数/日", int),
    "quota_auction_daily": ("免费用户竞价异动次数/日", int),
    "quota_checkin_bonus": ("签到赠送选股额度", int),
    "reg_open": ("开放注册", bool),
    "reg_ip_day_limit": ("同IP 24h 注册上限", int),
    "invite_same_ip_limit": ("同IP邀请奖励上限", int),
}


def get_member_conf():
    """读取会员配置(settings 表覆盖 config 默认)"""
    from ..core import config as _cfg
    defaults = {
        "new_user_days": int(getattr(_cfg, "NEW_USER_DAYS", 5)),
        "new_user_member_level": int(getattr(_cfg, "NEW_USER_MEMBER_LEVEL", 1)),
        "invite_reward_days": int(getattr(_cfg, "INVITE_REWARD_DAYS", 5)),
        "quota_picker_daily": int(getattr(_cfg, "QUOTA_PICKER_DAILY", 3)),
        "quota_aipick_daily": int(getattr(_cfg, "QUOTA_AIPICK_DAILY", 1)),
        "quota_auction_daily": int(getattr(_cfg, "QUOTA_AUCTION_DAILY", 1)),
        "quota_checkin_bonus": int(getattr(_cfg, "QUOTA_CHECKIN_BONUS", 3)),
        "reg_open": bool(getattr(_cfg, "REG_OPEN", True)),
        "reg_ip_day_limit": int(getattr(_cfg, "REG_IP_DAY_LIMIT", 5)),
        "invite_same_ip_limit": int(getattr(_cfg, "INVITE_SAME_IP_LIMIT", 3)),
    }
    saved = settings.get("member_conf", None)
    if isinstance(saved, dict):
        for k in defaults:
            if k in saved:
                try:
                    defaults[k] = bool(saved[k]) if isinstance(defaults[k], bool) else int(saved[k])
                except (TypeError, ValueError):
                    pass
    return defaults


def apply_member_conf(conf):
    """把配置写回运行时 config(改完立即生效)。进程重启后由 get_member_conf 重新加载。"""
    from ..core import config as _cfg
    mapping = {
        "new_user_days": "NEW_USER_DAYS",
        "new_user_member_level": "NEW_USER_MEMBER_LEVEL",
        "invite_reward_days": "INVITE_REWARD_DAYS",
        "quota_picker_daily": "QUOTA_PICKER_DAILY",
        "quota_aipick_daily": "QUOTA_AIPICK_DAILY",
        "quota_auction_daily": "QUOTA_AUCTION_DAILY",
        "quota_checkin_bonus": "QUOTA_CHECKIN_BONUS",
        "reg_open": "REG_OPEN",
        "reg_ip_day_limit": "REG_IP_DAY_LIMIT",
        "invite_same_ip_limit": "INVITE_SAME_IP_LIMIT",
    }
    for k, attr in mapping.items():
        if k in conf:
            try:
                setattr(_cfg, attr, conf[k])
            except Exception:
                pass


@router.get("/api/admin/member-conf")
def api_admin_member_conf_get(request: Request, uid: int = Depends(get_admin)):
    """会员权益配置(读取)"""
    return jr({"ok": True, "conf": get_member_conf(),
               "meta": {k: {"label": v[0], "type": v[1].__name__}
                        for k, v in MEMBER_CONF_KEYS.items()}})


@router.put("/api/admin/member-conf")
def api_admin_member_conf_put(request: Request, body: dict = Body(...),
                              uid: int = Depends(get_admin)):
    """会员权益配置(保存 + 即时生效)。
    ★ 保存后清一次 is_priv 缓存, 否则配额判断要等 60s 才跟上新等级规则。"""
    new = body.get("conf")
    if not isinstance(new, dict) or not new:
        return jr({"ok": False, "msg": "缺少 conf 配置"}, 400)
    cur = get_member_conf()
    for k, v in new.items():
        if k not in MEMBER_CONF_KEYS:
            return jr({"ok": False, "msg": "未知配置项: %s" % k}, 400)
        typ = MEMBER_CONF_KEYS[k][1]
        try:
            cur[k] = bool(v) if typ is bool else int(v)
        except (TypeError, ValueError):
            return jr({"ok": False, "msg": "%s 类型不正确" % k}, 400)
    if not (0 <= cur.get("new_user_days", 0) <= 3650):
        return jr({"ok": False, "msg": "新用户赠送天数超出范围(0~3650)"}, 400)
    for k in MEMBER_CONF_KEYS:
        if k.startswith("quota_") and k != "quota_checkin_bonus" and cur.get(k, 0) < 0:
            return jr({"ok": False, "msg": "%s 不能为负" % MEMBER_CONF_KEYS[k][0]}, 400)
    if not settings.set("member_conf", cur):
        return jr({"ok": False, "msg": "保存失败"}, 500)
    apply_member_conf(cur)
    users.audit(uid, "update_member_conf", None, cur, client_ip(request))
    log.info("管理端更新会员配置 uid=%s conf=%s", uid, cur)
    return jr({"ok": True, "msg": "会员配置已更新并即时生效", "conf": cur})


@router.get("/api/admin/user-detail")
def api_admin_user_detail(request: Request, uid: int = Depends(get_admin),
                          target_uid: int = 0):
    """用户详情(抽屉): 基础信息 + 邀请链 + 签到 + 手机号领取 + 最近审计"""
    if not target_uid:
        return jr({"ok": False, "msg": "缺少 target_uid"}, 400)
    u = users.find_user_by_id(target_uid)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    conn = _conn()
    try:
        inviter = None
        if u.get("invited_by"):
            inviter = conn.execute("SELECT id, username FROM users WHERE id=?",
                                   (u["invited_by"],)).fetchone()
        invitees = [dict(r) for r in conn.execute(
            "SELECT id, username, created_at, COALESCE(register_ip,'') register_ip "
            "FROM users WHERE invited_by=? ORDER BY created_at DESC LIMIT 100",
            (target_uid,)).fetchall()]
        checkins = [dict(r) for r in conn.execute(
            "SELECT date, reward FROM user_checkin WHERE uid=? ORDER BY date DESC LIMIT 30",
            (target_uid,)).fetchall()]
        claims = []
        if u.get("phone"):
            claims = [dict(r) for r in conn.execute(
                "SELECT * FROM phone_claims WHERE phone=?", (u["phone"],)).fetchall()]
        audits = [dict(r) for r in conn.execute(
            "SELECT action, detail, created_at FROM admin_audit WHERE target_uid=? "
            "ORDER BY id DESC LIMIT 20", (target_uid,)).fetchall()]
    finally:
        conn.close()
    now = int(time.time())
    et = int(u.get("expire_at") or 0)
    return jr({"ok": True, "user": {
        "id": u["id"], "username": u["username"], "phone": u.get("phone") or "",
        "email": u.get("email") or "", "wx_name": u.get("wx_name") or "",
        "remark": u.get("remark") or "", "pay_remark": u.get("pay_remark") or "",
        "is_admin": 1 if u.get("is_admin") else 0,
        "member_level": int(u.get("member_level") or 0),
        "expire_at": et,
        "expire_date": time.strftime("%Y-%m-%d", time.gmtime(et + 8 * 3600)) if et else "",
        "days_left": max(0, int((et - now) // 86400)) if et else -1,
        "permanent": 1 if not et else 0,
        "invite_code": u.get("invite_code") or "",
        "register_ip": u.get("register_ip") or "",
        "created_at": u.get("created_at") or 0,
        "created_date": time.strftime("%Y-%m-%d %H:%M",
                                      time.gmtime(int(u.get("created_at") or 0) + 8 * 3600)),
    }, "inviter": (dict(inviter) if inviter else None), "invitees": invitees,
        "checkins": checkins, "claims": claims, "audits": audits})


@router.post("/api/admin/users/reset-quota")
def api_admin_reset_quota(request: Request, body: dict = Body(...),
                          uid: int = Depends(get_admin)):
    """重置指定用户的当日配额(客服场景: 用户误操作烧完额度)"""
    from ..services import quota as quota_svc
    target = int(body.get("uid") or 0)
    if target <= 0:
        return jr({"ok": False, "msg": "缺少 uid"}, 400)
    feature = (body.get("feature") or "").strip() or None
    quota_svc.reset_user(target, feature, bonus=bool(body.get("reset_bonus", False)))
    users.audit(uid, "reset_quota", target, {"feature": feature}, client_ip(request))
    log.info("管理端重置配额 admin=%s target=%s feature=%s", uid, target, feature)
    return jr({"ok": True, "msg": "配额已重置", "quota": quota_svc.batch_peek(target)})


@router.post("/api/admin/users/extend-plus")
def api_admin_extend_plus(request: Request, body: dict = Body(...),
                          uid: int = Depends(get_admin)):
    """一键续期快捷按钮. body: {uids: [..], days: 30, set_level: 1?}"""
    uids = [int(x) for x in (body.get("uids") or []) if str(x).isdigit()]
    if not uids:
        return jr({"ok": False, "msg": "缺少 uids"}, 400)
    try:
        days = int(body.get("days") or 30)
    except (TypeError, ValueError):
        return jr({"ok": False, "msg": "days 应为整数"}, 400)
    set_level = body.get("set_level")
    ok_n, failed = 0, []
    for t in uids:
        try:
            if not users.find_user_by_id(t):
                failed.append({"uid": t, "msg": "用户不存在"})
                continue
            users.extend_expire(t, days)
            if set_level is not None:
                users.set_member_level(t, int(set_level))
            ok_n += 1
        except Exception as e:
            failed.append({"uid": t, "msg": str(e)[:60]})
    users.audit(uid, "extend_plus", None,
                {"uids": uids[:50], "days": days, "set_level": set_level, "ok": ok_n},
                client_ip(request))
    log.info("管理端一键续期 admin=%s days=%s 成功%d/%d", uid, days, ok_n, len(uids))
    return jr({"ok": True, "msg": "已为 %d/%d 个账号 +%d 天" % (ok_n, len(uids), days),
               "success": ok_n, "total": len(uids), "failed": failed})


# ============================================================================
# 用户行为记录: 登录记录 + 功能使用记录(2026-09-22, v4.11.35)
# ----------------------------------------------------------------------------
# 数据源见 services/activity.py。计数口径 = **用户主动操作一次 = 1 次**
# (主人 2026-09-22 拍板), 由前端在动作回调里上报, 不是接口请求数。
# 🔴 IP / UA / 设备属个人信息: **查某个具体用户的明细时必须写 admin_audit 留痕**
#    (全局流水不写, 否则审计表会被翻页刷爆)。
# ============================================================================

@router.get("/api/admin/user-activity")
def api_admin_user_activity(request: Request, uid: int = Depends(get_admin),
                            target_uid: int = 0, days: int = 30):
    """某用户的登录记录 + 功能使用记录(用户详情抽屉用)"""
    from ..services import activity as act_svc

    if not target_uid:
        return jr({"ok": False, "msg": "缺少 target_uid"}, 400)
    days = max(1, min(180, int(days or 30)))
    u = users.find_user_by_id(target_uid)
    if not u:
        return jr({"ok": False, "msg": "用户不存在"}, 404)
    # 🔴 查询留痕: 谁在什么时候看了谁的行为明细
    users.audit(uid, "view_user_activity", target_uid, {"days": days}, client_ip(request))
    log.info("管理端查看用户行为 admin=%s target=%s days=%s", uid, target_uid, days)
    return jr({"ok": True,
               "target": {"id": u["id"], "username": u["username"]},
               "logins": act_svc.login_history(target_uid, days=days, limit=100),
               "usage": act_svc.usage_summary(target_uid, days=days),
               "result_labels": act_svc.LOGIN_RESULT_LABEL})


@router.get("/api/admin/login-log")
def api_admin_login_log(request: Request, uid: int = Depends(get_admin)):
    """全站登录流水: ?days=&result=success|fail|reset|kicked|logout&kw=&limit=&offset="""
    from ..services import activity as act_svc

    q = qs(request)

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int((q.get(key) or [default])[0])))
        except (TypeError, ValueError):
            return default

    return jr({"ok": True, "result_labels": act_svc.LOGIN_RESULT_LABEL,
               **act_svc.global_login_log(
                   days=_int("days", 30, 1, 365),
                   result=(q.get("result") or [""])[0].strip(),
                   kw=(q.get("kw") or [""])[0].strip(),
                   limit=_int("limit", 50, 1, 200),
                   offset=_int("offset", 0, 0, 1000000))})


@router.get("/api/admin/usage-rank")
def api_admin_usage_rank(request: Request, uid: int = Depends(get_admin)):
    """某日功能使用排行: ?date=YYYY-MM-DD&feature=&limit="""
    from ..services import activity as act_svc

    q = qs(request)
    try:
        limit = max(1, min(100, int((q.get("limit") or [20])[0])))
    except (TypeError, ValueError):
        limit = 20
    return jr({"ok": True, **act_svc.usage_rank(
        date=(q.get("date") or [""])[0].strip() or None,
        feature=(q.get("feature") or [""])[0].strip() or None, limit=limit),
        "feature_labels": act_svc.FEATURES})


@router.get("/api/admin/active-users")
def api_admin_active_users(request: Request, uid: int = Depends(get_admin)):
    """活跃趋势: ?days=30 → 按日去重用户数 / 操作次数 / 登录成功数"""
    from ..services import activity as act_svc

    q = qs(request)
    try:
        days = max(1, min(90, int((q.get("days") or [30])[0])))
    except (TypeError, ValueError):
        days = 30
    return jr({"ok": True, **act_svc.active_trend(days=days),
               "feature_labels": act_svc.FEATURES})
