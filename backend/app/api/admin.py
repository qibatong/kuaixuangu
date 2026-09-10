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

from ..core import logger
from ..services import scorer, settings, users
from .deps import client_ip, get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()

# 五项权重系数(key, 中文名, 说明)
W_KEYS = [
    ("w_bid", "竞价分", "竞价涨幅区间得分(如3%~5.5%为满分)"),
    ("w_activity", "活跃度", "竞价换手率/量比活跃度得分"),
    ("w_warn", "异动分", "封单/抢筹等异动信号得分"),
    ("w_market", "市值分", "流通市值越小分越高(小市值加分)"),
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
        from ..db import database as _db
        conn = _db.get_conn()
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
    "probLt": 65.0, "confLt": 65.0,
    "floatMvFloor": 30.0, "floatMvGt": 1000.0, "priceGt": 300.0,
    "bidAmtFloor": 1000.0,   # 诗人需求: 默认竞价金额下限 1000万(原3000)
    "scoreFloor": 80.0,      # 2026-09-10 主人拍板: 评分低于 80 分的票不显示(全站默认)
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
