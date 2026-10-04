# -*- coding: utf-8 -*-
"""
站内消息路由(2026-10-04)
========================
主人需求(原话): 「系统消息比如**系统更新提醒，会员到期提醒**，等等」

设计要点(🔴 改动前必读):

1. 消息分两类, **只有第一类落表**:
   · A 站方广播(`type='broadcast'`): 系统更新提醒 / 停服维护 / 新功能上线 —— 管理员在后台发布,
     存 `notices` 表, 支持按会员等级定向(target)与有效期(start_ts/end_ts)。
   · B 账户事件(`type='account'`): 会员到期 / 已过期 / 今日免费次数用尽 —— **实时推导、不落表**。
     🔴 为什么不落表: 它是"状态"不是"通知"。落库后用户续了费, 那条"3 天后到期"会变成永远清不掉的
     脏数据(2026-09 同类教训: 快照类数据落库即过期)。条件消失 ⇒ 事件自动消失, 无需清理任务。

2. 已读回执: 主人拍板**服务端记录**(换设备不重弹) ⇒ `notice_reads` 表, 复合主键天然幂等。
   🔴 只作用于 A 类: B 类是状态, 标"已读"没有意义(第二天还会再出现), 故 `read` 恒为 0,
      红点靠**事件是否还存在**来消, 不靠已读。

3. 红点计数 `unread` = A 类未读条数 + B 类中 warn/urgent 级事件数。
   B 类的 info 级(如"今日免费次数已用完")**不计红点** —— 否则免费用户每天一开门就常亮红点,
   红点就失去意义(狼来了)。

4. 🔴 与 services/notify.py 无关: 那是站方→微信/飞书**群**推送(选股结果), 是另一个东西, 别混。

端点一览:
  GET  /api/notices          聚合消息列表 + 未读数
  POST /api/notices/read     已读回执({ids:[...]} 或 {all:true})
"""
import sqlite3
import time

from fastapi import APIRouter, Body, Depends, Request

from ..core import config, logger
from ..services import quota as quota_svc
from ..services import users
from .deps import get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()

# 定向目标 → 允许的会员等级集合(users.get_member_level: 0=免费试用 1=付费会员 2=VIP)
TARGET_LEVELS = {
    "all": None,          # 全员
    "free": {0},          # 仅免费试用(催转化)
    "member": {1, 2},     # 仅付费/VIP
    "vip": {2},           # 仅 VIP
}
# 管理员(等级 3)永远可见全部广播 —— 否则管理员自己发的公告自己看不到, 没法验收
ADMIN_LEVEL = 3


def _conn():
    conn = sqlite3.connect(config.DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _free_limit(uid):
    """到期后的每日免费次数(取「竞价选股」额度; 取不到就只说"大幅下降", 不编数字)"""
    try:
        return int(quota_svc.limit_of(uid, "picker"))
    except Exception:
        return "-"


def _fmt_date(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + 8 * 3600))


def _broadcast_items(conn, level, now):
    """A 类: 有效期内 + 未被撤回 + 命中定向 的站方广播"""
    try:
        rows = conn.execute(
            "SELECT id, title, body, level, target, start_ts, end_ts, created_at "
            "FROM notices WHERE off_at=0 AND start_ts<=? ORDER BY start_ts DESC LIMIT 30",
            (now,)).fetchall()
    except Exception as e:
        log.warning("读取公告失败 err=%s", e)
        return []

    out = []
    for r in rows:
        d = dict(r)
        # 结束时间 0 = 长期有效
        if d.get("end_ts") and now > int(d["end_ts"]):
            continue
        tgt = d.get("target") or "all"
        # target=all 全员可见; 其余按等级集合过滤
        if tgt != "all":
            allow_set = TARGET_LEVELS.get(tgt)
            if allow_set is not None and level not in allow_set:
                continue
        out.append({
            "id": "n%s" % d["id"],
            "nid": int(d["id"]),
            "type": "broadcast",
            "level": d.get("level") or "info",
            "title": d.get("title") or "",
            "body": d.get("body") or "",
            "ts": int(d.get("start_ts") or d.get("created_at") or 0),
            "read": 0,
        })
    return out


def _account_items(uid, now):
    """B 类: 账户事件, 实时推导(不落表)"""
    out = []
    try:
        u = users.find_user_by_id(uid) or {}
        level = users.get_member_level(uid)
        privileged = quota_svc.is_privileged(uid)
        et = int(u.get("expire_at") or 0)
    except Exception as e:
        log.warning("账户事件推导失败 uid=%s err=%s", uid, e)
        return out

    # ① 会员已过期
    if et and now > et:
        out.append({
            "id": "acct:expired",
            "nid": 0,
            "type": "account",
            "level": "urgent",
            "title": "会员已到期",
            "body": "到期日 %s。到期后每日免费次数降为 %s 次，续费后自动恢复。"
                    % (_fmt_date(et), _free_limit(uid)),
            "ts": int(et),
            "read": 0,
        })
    elif et:
        days = max(0, int((et - now + 86399) // 86400))
        # ② 即将到期: ≤3 天 warn(会亮红点), ≤7 天 info(只出现在列表, 不红点)
        if days <= 3:
            out.append({
                "id": "acct:expiring",
                "nid": 0,
                "type": "account",
                "level": "warn",
                "title": "会员将在 %d 天后到期" % days,
                "body": "到期日 %s，到期后每日免费次数降为 %s 次。联系管理员续费可无缝衔接。"
                        % (_fmt_date(et), _free_limit(uid)),
                "ts": int(et),
                "read": 0,
            })
        elif days <= 7:
            out.append({
                "id": "acct:expiring",
                "nid": 0,
                "type": "account",
                "level": "info",
                "title": "会员将在 %d 天后到期" % days,
                "body": "到期日 %s，可提前联系管理员续费。" % _fmt_date(et),
                "ts": int(et),
                "read": 0,
            })

    # ③ 今日免费次数用尽(仅非特权用户; info 级, 不亮红点)
    if not privileged:
        try:
            for f in ("picker", "auction"):
                q = quota_svc.peek(uid, f)
                lim = int(q.get("limit") or 0)
                remain = int(q.get("remain") or 0)
                if lim > 0 and remain <= 0:
                    out.append({
                        "id": "acct:quota:%s" % f,
                        "nid": 0,
                        "type": "account",
                        "level": "info",
                        "title": "今日「%s」免费次数已用完" % (q.get("label") or f),
                        "body": "每日 %d 次，明天 0 点重置；签到可额外领取次数。" % lim,
                        "ts": int(now),
                        "read": 0,
                    })
        except Exception as e:
            log.warning("配额事件推导失败 uid=%s err=%s", uid, e)

    return out


@router.get("/api/notices")
def api_notices(request: Request, uid: int = Depends(get_uid)):
    """聚合消息: A 站方广播(读表) + B 账户事件(实时推导) + 未读数"""
    now = int(time.time())
    try:
        u = users.find_user_by_id(uid) or {}
        level = users.get_member_level(uid)
        if u.get("is_admin"):
            level = ADMIN_LEVEL
    except Exception:
        level = 0

    conn = _conn()
    try:
        items = _broadcast_items(conn, level, now)
        # 已读回执: 一次查询, 不逐条查(30 条上限, 但别养成 N+1)
        nids = [it["nid"] for it in items]
        read_set = set()
        if nids:
            ph = ",".join("?" * len(nids))
            rows = conn.execute(
                "SELECT notice_id FROM notice_reads WHERE user_id=? AND notice_id IN (%s)" % ph,
                [uid] + nids).fetchall()
            read_set = {int(r[0]) for r in rows}
        for it in items:
            it["read"] = 1 if it["nid"] in read_set else 0
    finally:
        conn.close()

    acct = _account_items(uid, now)
    items = acct + items          # 账户事件置顶: 它有时效性, 埋在广播下面等于没有

    # 红点: A 类未读 + B 类 warn/urgent(info 级不红点, 见文件头说明 3)
    unread = sum(1 for it in items
                 if (it["type"] == "broadcast" and not it["read"])
                 or (it["type"] == "account" and it["level"] in ("warn", "urgent")))

    return jr({"ok": True, "unread": unread, "items": items,
               "server_ts": now})


@router.post("/api/notices/read")
def api_notices_read(request: Request, uid: int = Depends(get_uid), payload: dict = Body(default={})):
    """已读回执: {ids:[nid,...]} 或 {all:true}。只作用于 A 类(B 类是状态, 不支持标记已读)"""
    now = int(time.time())
    conn = _conn()
    n = 0
    try:
        if payload.get("all"):
            cur = conn.execute(
                "INSERT OR IGNORE INTO notice_reads (user_id, notice_id, read_at) "
                "SELECT ?, id, ? FROM notices WHERE off_at=0", (uid, now))
            n = cur.rowcount or 0
        else:
            ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
            for nid in ids:
                conn.execute(
                    "INSERT OR IGNORE INTO notice_reads (user_id, notice_id, read_at) VALUES (?,?,?)",
                    (uid, nid, now))
                n += 1
        conn.commit()
    except Exception as e:
        log.warning("已读回执写入失败 uid=%s err=%s", uid, e)
        return jr({"ok": False, "msg": "已读状态保存失败"}, status=500)
    finally:
        conn.close()
    return jr({"ok": True, "marked": n})
