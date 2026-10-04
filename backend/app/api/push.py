# -*- coding: utf-8 -*-
"""
WebPush 订阅路由（2026-10-04，手机端「真推送」）
================================================

三个接口 + 一个内部发送入口：

· `GET  /api/push/vapid-public-key`  前端订阅前要拿服务端公钥（applicationServerKey）
· `GET  /api/push/status`            当前设备/账号是否已订阅（用于开关回显）
· `POST /api/push/subscribe`         保存订阅（同一 endpoint 重复上报 ⇒ 幂等更新）
· `POST /api/push/unsubscribe`       退订（删行）

内部：`notify_user(uid, ...)` / `notify_all(...)` 供**站内消息发布**等场景调用
（见 `services/webpush.py` 的加密实现）。

🔴 设计约束（改动前必读）：
1. **订阅行按 endpoint 唯一**（不是按用户唯一）：一个人可能有手机壳 + 电脑浏览器 + 平板，
   全都要能收到 ⇒ 主键用 endpoint，user_id 只是归属。
2. **发送失败要自愈**：推送服务返回 404/410 = 订阅已失效（用户清了数据/卸载），
   必须**当场删掉这行**，否则每次群发都白打一遍（这也是所有 WebPush 实现的通病）。
3. **推送失败绝不能影响主流程**：发布消息是主流程，推送是附赠 ⇒ 全程 try/except，
   失败只记日志。
"""

import sqlite3
import time

from fastapi import APIRouter, Body, Depends

from ..core import config, logger
from ..services import webpush as wp
from .deps import get_uid, jr

router = APIRouter()


def _conn():
    c = sqlite3.connect(config.DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ------------------------------------------------------------------ 订阅管理


@router.get("/api/push/vapid-public-key")
def api_push_vapid_key(uid: int = Depends(get_uid)):
    try:
        return jr({"ok": True, "data": {"key": wp.vapid_public_key()}})
    except Exception as e:
        logger.warning("推送公钥获取失败: %s", e)
        return jr({"ok": False, "msg": "推送公钥获取失败"})


@router.get("/api/push/status")
def api_push_status(uid: int = Depends(get_uid)):
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", (uid,)).fetchone()[0]
        c.close()
        return jr({"ok": True, "data": {"subscribed": n > 0, "devices": n}})
    except Exception as e:
        logger.warning("推送状态查询失败: %s", e)
        return jr({"ok": True, "data": {"subscribed": False, "devices": 0}})


@router.post("/api/push/subscribe")
def api_push_subscribe(uid: int = Depends(get_uid), payload: dict = Body(default={})):
    """payload = { endpoint, keys: { p256dh, auth }, ua? }"""
    try:
        endpoint = (payload.get("endpoint") or "").strip()
        keys = payload.get("keys") or {}
        p256dh = (keys.get("p256dh") or "").strip()
        auth = (keys.get("auth") or "").strip()
        if not endpoint.startswith("https://") or not p256dh or not auth:
            return jr({"ok": False, "msg": "订阅信息不完整"})
        if len(endpoint) > 1024:
            return jr({"ok": False, "msg": "endpoint 过长"})

        c = _conn()
        # 🔴 SQLite 只有 **3.7.17**(实测), 不支持 UPSERT(需 3.24+) ⇒ 用 INSERT OR REPLACE:
        #    endpoint 是主键, 重复上报同一设备 == 覆盖, 天然幂等(浏览器每次 subscribe 都可能
        #    给到相同 endpoint, 不能让它插成两行否则会重复推送)。
        c.execute(
            """INSERT OR REPLACE INTO push_subscriptions
               (user_id, endpoint, p256dh, auth, ua, created_at, last_ok_at)
               VALUES (?,?,?,?,?,?,?)""",
            (uid, endpoint, p256dh, auth, (payload.get("ua") or "")[:200], int(time.time()), int(time.time())),
        )
        c.commit()
        c.close()
        return jr({"ok": True, "msg": "已开启推送"})
    except Exception as e:
        logger.warning("推送订阅失败: %s", e)
        return jr({"ok": False, "msg": "订阅失败"})


@router.post("/api/push/unsubscribe")
def api_push_unsubscribe(uid: int = Depends(get_uid), payload: dict = Body(default={})):
    try:
        endpoint = (payload.get("endpoint") or "").strip()
        c = _conn()
        if endpoint:
            c.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?", (endpoint, uid))
        else:
            # 不传 endpoint = 该账号**全部设备**退订
            c.execute("DELETE FROM push_subscriptions WHERE user_id=?", (uid,))
        n = c.total_changes
        c.commit()
        c.close()
        return jr({"ok": True, "msg": "已关闭推送", "data": {"removed": n}})
    except Exception as e:
        logger.warning("推送退订失败: %s", e)
        return jr({"ok": False, "msg": "退订失败"})


# ------------------------------------------------------------------ 发送


def _rows_for(uid=None):
    c = _conn()
    if uid:
        rows = c.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?", (uid,)).fetchall()
    else:
        rows = c.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions").fetchall()
    c.close()
    return [dict(r) for r in rows]


def _drop(endpoint: str):
    """订阅失效 ⇒ 删行（避免以后每次群发都白打）"""
    try:
        c = _conn()
        c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        c.commit()
        c.close()
    except Exception as e:
        logger.warning("清理失效订阅失败: %s", e)


def notify_user(uid: int, title: str, body: str, url: str = "/messages") -> int:
    """给指定用户的所有设备推送，返回成功条数。失败不影响调用方。"""
    return _notify(_rows_for(uid), title, body, url)


def notify_all(title: str, body: str, url: str = "/messages") -> int:
    """全员推送（站方广播）。"""
    return _notify(_rows_for(None), title, body, url)


def notify_by_target(target: str, title: str, body: str, url: str = "/messages") -> int:
    """按公告的定向范围推送（与站内消息 `target` 同一套口径）。

    free   = member_level 0（免费试用）
    member = member_level >= 1（付费会员）
    vip    = member_level 2（VIP / 永久）
    all    = 不筛选
    """
    try:
        c = _conn()
        if target == "vip":
            sql = ("SELECT s.endpoint, s.p256dh, s.auth FROM push_subscriptions s "
                   "JOIN users u ON u.id = s.user_id WHERE u.member_level = 2")
        elif target == "member":
            sql = ("SELECT s.endpoint, s.p256dh, s.auth FROM push_subscriptions s "
                   "JOIN users u ON u.id = s.user_id WHERE u.member_level >= 1")
        elif target == "free":
            sql = ("SELECT s.endpoint, s.p256dh, s.auth FROM push_subscriptions s "
                   "JOIN users u ON u.id = s.user_id WHERE u.member_level = 0")
        else:
            sql = "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        rows = [dict(r) for r in c.execute(sql).fetchall()]
        c.close()
        return _notify(rows, title, body, url)
    except Exception as e:
        logger.warning("定向推送失败: %s", e)
        return 0


def _notify(subs, title: str, body: str, url: str) -> int:
    ok_n = 0
    for s in subs:
        try:
            ok, why = wp.send_one(s, title, body, url)
            if ok:
                ok_n += 1
                try:
                    c = _conn()
                    c.execute("UPDATE push_subscriptions SET last_ok_at=? WHERE endpoint=?", (int(time.time()), s["endpoint"]))
                    c.commit()
                    c.close()
                except Exception:
                    pass
            else:
                logger.info("推送失败(%s) %s", why, s["endpoint"][:60])
                if "失效" in str(why):
                    _drop(s["endpoint"])
        except Exception as e:
            logger.warning("推送异常: %s", e)
    return ok_n
