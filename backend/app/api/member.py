# -*- coding: utf-8 -*-
"""
会员中心路由(2026-09-21)
=========================
用户端「我的会员」页所需的全部数据 + 签到 + 配额查询。

端点一览:
  GET  /api/member/overview    会员总览(等级/到期/剩余天数/配额/签到/邀请战绩)
  GET  /api/member/quota       配额状态(顶部常驻展示)
  POST /api/member/checkin     每日签到(送选股额度)
  GET  /api/member/checkin     签到状态(今日是否已签 + 连续天数)
  GET  /api/member/plans       会员套餐与权益对照(前端渲染权益表用)

设计说明:
  - 全部走 get_uid(登录即可访问), 不要求会员 —— 免费用户正是要靠这个页面看到
    「升级后能多什么」, 门禁过高会把这个页面本身变成付费点, 得不偿失。
  - 到期时间同时返回 ts 与可读天数, 前端倒计时用 days_left 即可, 不必自己算时区。
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import config, logger
from ..services import quota as quota_svc
from ..services import settings as settings_svc
from ..services import users
from .deps import get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()

#: 2026-10-06 新增: 套餐价格**后台可配**(settings 键 member_plans), 这里是默认值。
#: 背景: 价格原本写死在前端 MemberView.vue 的 PLANS_PRICE 常量里 ⇒ 改一次价要重新构建+换盘,
#:   运营完全没法做促销, 也没法上"年卡"(年卡是最直接的改善现金流的手段)。
#: ⚠️ 年卡 ¥2188 是**建议价**(按月卡 218 / 季卡 588 折合 196 每月, 年卡再打个折合 182/月),
#:   主人可随时在后台改。不写死在前端是因为它属于经营决策, 不该由发版来定。
DEFAULT_PLANS = [
    {"key": "month", "label": "月卡", "days": 30, "price": 218, "on": 1},
    {"key": "quarter", "label": "季卡", "days": 90, "price": 588, "on": 1},
    {"key": "year", "label": "年卡", "days": 365, "price": 2188, "on": 1},
]


def _plans():
    """套餐价格(后台配置优先; 脏数据/缺失 ⇒ 回落默认, 绝不让开通页白屏)"""
    saved = settings_svc.get("member_plans", None)
    if isinstance(saved, list) and saved:
        out = []
        for p in saved:
            if not isinstance(p, dict):
                continue
            try:
                out.append({
                    "key": str(p.get("key") or ""),
                    "label": str(p.get("label") or ""),
                    "days": int(p.get("days") or 0),
                    "price": int(p.get("price") or 0),
                    "on": 1 if int(p.get("on", 1)) else 0,
                })
            except (TypeError, ValueError):
                continue
        if out:
            return out
    return list(DEFAULT_PLANS)


def _checkin_feature():
    """签到奖励加到哪个功能(后台可配, 默认 picker)

    🔴 白名单兜底: 配错(写成 pickr / 空串)会让签到奖励凭空消失且很难排查 ——
       直接回落 picker, 至少和历史行为一致。
    """
    f = str(getattr(config, "QUOTA_CHECKIN_FEATURE", "picker") or "picker").strip()
    return f if f in ("picker", "aipick", "auction") else "picker"


def _fmt_date(ts):
    """时间戳 → 北京日期字符串"""
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + 8 * 3600))


def _member_payload(uid, user=None):
    """会员基础信息(等级/到期/剩余)"""
    u = user if user is not None else (users.find_user_by_id(uid) or {})
    level = users.get_member_level(uid)
    et = int(u.get("expire_at") or 0)
    now = time.time()
    days_left = 0
    if et:
        days_left = max(0, int((et - now + 86399) // 86400))
    return {
        "uid": uid,
        "username": u.get("username") or "",
        "member_level": level,
        "member_label": users.MEMBER_LEVEL_LABEL.get(level, "普通用户"),
        "is_admin": 1 if u.get("is_admin") else 0,
        "expire_at": et,
        "expire_date": _fmt_date(et),
        "days_left": days_left,
        "permanent": 1 if not et else 0,
        "expired": 1 if (et and now > et) else 0,
        "privileged": 1 if quota_svc.is_privileged(uid) else 0,
    }


@router.get("/api/member/overview")
def api_member_overview(request: Request, uid: int = Depends(get_uid)):
    """会员总览: 我的会员页一次性拿全所有数据"""
    user = users.find_user_by_id(uid)
    base = _member_payload(uid, user)

    quota_list = []
    for f in ("picker", "aipick", "auction"):
        quota_list.append(quota_svc.peek(uid, f))

    invitees = users.list_invitees(uid)
    invite_code = users.ensure_invite_code(uid)
    today = users.checkin_today(uid)

    invited = len(invitees)
    earned_days = invited * int(config.INVITE_REWARD_DAYS)

    return jr({
        "ok": True,
        "member": base,
        "quota": quota_list,
        "checkin": {
            "done_today": bool(today),
            "reward": int(getattr(config, "QUOTA_CHECKIN_BONUS", 3)),
            "streak": users.checkin_streak(uid),
        },
        "invite": {
            "code": invite_code or "",
            "invited_count": invited,
            "earned_days": earned_days,
            "reward_days": int(config.INVITE_REWARD_DAYS),
            "invitees": invitees[:20],
        },
    })


@router.get("/api/member/quota")
def api_member_quota(request: Request, uid: int = Depends(get_uid)):
    """配额状态(不消耗): 供页面顶部常驻展示剩余次数"""
    return jr({"ok": True, "quota": quota_svc.batch_peek(uid),
               "checkin": {"done_today": bool(users.checkin_today(uid)),
                           "reward": int(getattr(config, "QUOTA_CHECKIN_BONUS", 3))}})


@router.get("/api/member/checkin")
def api_checkin_status(request: Request, uid: int = Depends(get_uid)):
    """签到状态"""
    today = users.checkin_today(uid)
    return jr({"ok": True, "done_today": bool(today),
               "reward": int(getattr(config, "QUOTA_CHECKIN_BONUS", 3)),
               "streak": users.checkin_streak(uid),
               "history": users.checkin_recent(uid, 7) if hasattr(users, "checkin_recent") else []})


@router.post("/api/member/checkin")
def api_checkin(request: Request, uid: int = Depends(get_uid)):
    """每日签到(幂等): 送 config.QUOTA_CHECKIN_BONUS 次选股额度。
    会员/管理员也能签(拿不到额外意义, 但不报错, 保持交互一致)。"""
    ok, msg, reward, _first = users.do_checkin(uid)
    if not ok:
        return jr({"ok": False, "msg": msg,
                   "done_today": True,
                   "streak": users.checkin_streak(uid)}, 400)
    # 奖励加到**可配置**的功能上(2026-10-06): 原先写死 picker, 于是 aipick/auction 用户
    # 签到毫无收益 ⇒ 签到这个留存机制对他们形同虚设。默认仍是 picker, 行为不变。
    feat = _checkin_feature()
    if reward > 0:
        quota_svc.add_bonus(uid, feat, reward)
    q = quota_svc.peek(uid, feat)
    log.info("签到成功 uid=%s reward=%s feature=%s streak=%s", uid, reward, feat,
             users.checkin_streak(uid))
    return jr({"ok": True,
               "msg": "签到成功，%s +%d" % (quota_svc.FEATURE_LABEL.get(feat, feat), reward),
               "reward": reward,
               "feature": feat,
               "done_today": True,
               "streak": users.checkin_streak(uid),
               "quota": q})


@router.get("/api/member/plans")
def api_member_plans(request: Request):
    """会员权益对照表(前端渲染用, 不涉及登录)。
    权益数值全部来自配置, 后台改配置前端自动同步。"""
    free_limits = {f: quota_svc.base_limit(f) for f in ("picker", "aipick", "auction")}
    return jr({
        "ok": True,
        "new_user_days": int(config.NEW_USER_DAYS),
        "invite_reward_days": int(config.INVITE_REWARD_DAYS),
        "checkin_bonus": int(getattr(config, "QUOTA_CHECKIN_BONUS", 3)),
        "checkin_feature": _checkin_feature(),
        # 套餐价格(后台可配, 含年卡): 前端不再写死, 改价/促销不必发版
        "plans": [p for p in _plans() if p.get("on")],
        "free": {
            "label": "免费试用",
            "picker": free_limits.get("picker"),
            "aipick": free_limits.get("aipick"),
            "auction": free_limits.get("auction"),
        },
        "member": {
            "label": "付费会员",
            "picker": -1, "aipick": -1, "auction": -1,
        },
        "vip": {
            "label": "VIP 老师",
            "picker": -1, "aipick": -1, "auction": -1,
        },
    })
