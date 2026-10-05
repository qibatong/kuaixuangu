# -*- coding: utf-8 -*-
"""
站内消息路由(2026-10-04 建立 / 2026-10-06 重构到消息中心)
=========================================================
主人需求(原话): 「系统消息比如**系统更新提醒，会员到期提醒**，等等」

🔴 2026-10-06 重构: 所有消息逻辑已收进 `services/notice_center.py`, 本文件**只剩路由**。
   改动前请先读 notice_center.py 的文件头(五条硬约束), 尤其是:
   · 业务唯一键是 nkey 不是 id(id 会被复用, 历史因此出过"红点永不亮")
   · 消息三分类 system/account/trade(主人 2026-10-05 拍板)
   · 会员到期提醒**已改为落表**(为了能统计已读率), 靠 meta.expire_at 快照自愈
   · 推送有硬上限(每天 3 条 / 运营 1 条), 写死在 notice_center 常量里

与旧版的**行为差异**(前端需同步):
   1. items 里新增 `nkey` / `category` / `action_type` / `action_value` 字段
   2. 「标记已读」「删除」改传 **nkey 数组**(不再是数字 id 数组) —— id 会复用, 不能用
   3. 新增「单条删除」与「推送偏好」两个端点
   4. 未读红点规则收紧: 只有 account 类未读 或 warn/urgent 未读 才计数

端点一览:
  GET  /api/notices              消息列表 + 未读数
  POST /api/notices/read         已读回执({nkeys:[...]} 或 {all:true}); 兼容旧 {ids:[...]}
  POST /api/notices/delete       单条删除({nkeys:[...]})
  POST /api/notices/click        消息内 CTA 点击上报({nkey})
  GET  /api/notices/prefs        推送偏好
  POST /api/notices/prefs        设置推送偏好({auction:false, ...})
"""
import time

from fastapi import APIRouter, Body, Depends, Request

from ..core import logger
from ..services import notice_center as nc
from ..services import users
from .deps import get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()


def _uid_level(uid):
    try:
        u = users.find_user_by_id(uid) or {}
        level = users.get_member_level(uid)
        if u.get("is_admin"):
            level = nc.ADMIN_LEVEL
        return level
    except Exception:
        return 0


@router.get("/api/notices")
def api_notices(request: Request, uid: int = Depends(get_uid)):
    """消息列表 + 未读数"""
    # 🔴 读取侧兜底: 到期提醒改成落表后依赖 worker 的 08:30 job, 万一 worker 没起
    #    ⇒ 用户会完全看不到到期提醒(功能倒退)。这里按需补一条, 有 dedup_key 不会重复。
    try:
        nc.ensure_account_fresh(uid)
    except Exception as e:
        log.warning("到期提醒补发失败 uid=%s err=%s", uid, e)
    level = _uid_level(uid)
    items, unread = nc.fetch(uid, level)
    return jr({"ok": True, "unread": unread, "items": items, "server_ts": int(time.time())})


@router.post("/api/notices/read")
def api_notices_read(request: Request, uid: int = Depends(get_uid), payload: dict = Body(default={})):
    """已读回执: {nkeys:[...]} 或 {all:true}。旧版 {ids:[数字]} 仍兼容(转 nkey)。"""
    keys = [str(x) for x in (payload.get("nkeys") or []) if x]
    # 兼容旧客户端: ids 是 notices.id, 需在库里换成 nkey(绝不能直接当 nkey 用)
    legacy_ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
    if legacy_ids:
        keys += nc.nkeys_by_ids(legacy_ids)
    all_ = bool(payload.get("all"))
    n = nc.mark_read(uid, keys or None, all_=all_)
    return jr({"ok": True, "marked": n})


@router.post("/api/notices/delete")
def api_notices_delete(request: Request, uid: int = Depends(get_uid), payload: dict = Body(default={})):
    """单条删除: {nkeys:[...]}。只对本用户隐藏, 不删公告本体(别人还得看)。"""
    keys = [str(x) for x in (payload.get("nkeys") or []) if x]
    legacy_ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
    if legacy_ids:
        keys += nc.nkeys_by_ids(legacy_ids)
    if not keys:
        return jr({"ok": False, "msg": "缺少 nkey"}, status=400)
    n = nc.delete_for_user(uid, keys)
    return jr({"ok": True, "deleted": n})


@router.post("/api/notices/click")
def api_notices_click(request: Request, uid: int = Depends(get_uid), payload: dict = Body(default={})):
    """消息内行动按钮点击上报(触达漏斗的 click 一环)"""
    k = str(payload.get("nkey") or "").strip()
    if k:
        nc.track_click(uid, k)
    return jr({"ok": True})


@router.get("/api/notices/prefs")
def api_notices_prefs(request: Request, uid: int = Depends(get_uid)):
    """推送偏好 + 每项说明(供设置页渲染)"""
    return jr({"ok": True, "prefs": nc.get_prefs(uid),
               "keys": [{"key": k, "label": v[2], "ops": v[1]} for k, v in nc.PUSH_KEYS.items()],
               "daily_max": nc.NOTICE_PUSH_DAILY_MAX, "ops_max": nc.NOTICE_PUSH_OPS_DAILY_MAX})


@router.post("/api/notices/prefs")
def api_notices_prefs_set(request: Request, uid: int = Depends(get_uid), payload: dict = Body(default={})):
    """设置推送偏好(局部更新)。🔴 只影响"推不推送", 站内消息始终可见可查。"""
    return jr({"ok": True, "prefs": nc.set_prefs(uid, payload or {})})
