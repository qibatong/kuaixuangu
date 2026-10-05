# -*- coding: utf-8 -*-
"""
消息中心服务层(2026-10-06)
==========================
把「站内消息 + WebPush 推送 + 已读回执 + 触达统计 + 订阅偏好 + 频次上限」
收进**一个**模块, 供 api/notices.py(用户端) / api/admin.py(管理端) / worker(定时投递)
共用。此前这些逻辑散在三个地方且互不通气(发布时顺手推一下、已读只认 id、无统计)。

🔴🔴 改动前必读(五条硬约束):

1. **业务唯一键是 `nkey`, 不是 `id`**。
   `notices.id` 是 `INTEGER PRIMARY KEY`(无 AUTOINCREMENT) ⇒ 行被 DELETE 后 id 会被
   新公告复用, 而 `notice_reads` 里的旧回执还在 ⇒ 全新公告被判成"已读", 红点永不亮
   (2026-10-04 生产事故)。**所有对外关联(回执/统计/删除)一律走 nkey**;
   id 只作内部主键。兼容期两列都写, 查询优先 nkey。

2. **消息分三类(category)**: system(系统/运营/互动) / account(账户与会员) /
   trade(交易时点: 竞价开始·名单就绪·尾盘)。运营与互动并进 system —— 主人 2026-10-05
   拍板"精简为三类, 减少用户认知负担"。

3. **account 类(会员到期)改为落表, 但必须自愈**。
   原设计是实时推导不落表(理由是"状态会变脏": 用户续费后那条"3 天后到期"清不掉)。
   落表是为了能统计"到期提醒有多少人看过"(原方案运营完全盲)。
   双保险: (a) 表行带 `meta.expire_at` 快照, 读取时校验与当前 expire_at 是否一致,
   不一致 ⇒ 当场丢弃并清理(即使忘了主动清理也不会显示脏数据);
   (b) 续费/改等级时调 `on_membership_changed()` 主动下架。

4. **推送频次硬上限写死在这里**: 同一用户同一天最多 `NOTICE_PUSH_DAILY_MAX`(3) 条,
   其中运营类最多 `NOTICE_PUSH_OPS_DAILY_MAX`(1) 条。
   快选是**时点型**工具(用户一天只在 9:15–9:35 用), 容忍阈值远低于同花顺/雪球;
   WebPush 是目前唯一的站外通道, 逼用户关一次就永久失效 ⇒ 超限直接不推(站内照发)。
   另有免打扰时段 `QUIET_HOURS`(推送默认不在该时段下发, 交易时点类豁免)。

5. **订阅偏好只管"推不推送", 不管"站内出不出"**。
   站内消息是"想去就能找到"的, 隐藏它会让用户找不到上次那条到期提醒;
   真正打扰人的是推送 ⇒ 偏好只作为推送侧的过滤条件。

端点/调用方一览:
  api/notices.py   → fetch() / mark_read() / delete_for_user() / get_prefs() / set_prefs()
  api/admin.py     → publish() / off() / edit() / stats_rows() / preview_count()
  api/deps.py      → notify_quota_exhausted()
  worker           → start_scheduler() 内跑 dispatch_due() + 竞价闹钟 + 名单就绪
"""
import json
import sqlite3
import time
import uuid

from ..core import config, logger
from . import quota as quota_svc
from . import users

log = logger.get_logger(__name__)

# ---------------------------------------------------------------- 常量

#: 消息分类(主人 2026-10-05 拍板三类)
CATEGORIES = ("system", "account", "trade")
CATEGORY_LABEL = {"system": "系统", "account": "账户会员", "trade": "交易时点"}

#: 级别
LEVELS = ("info", "warn", "urgent")

#: 定向(沿用旧口径) —— 'tag' 是 2026-10-06 第二批加的**标签定向**(A4),
#: 具体标签名存在 notices.target_tag, 人群由 user_ops.uids_by_tag() 现算。
TARGETS = ("all", "free", "member", "vip", "tag")
TARGET_LEVELS = {"all": None, "free": {0}, "member": {1, 2}, "vip": {2}}
ADMIN_LEVEL = 3  # 管理员永远可见全部广播, 否则自己发的公告自己看不到

#: 🔴 推送频次硬上限(主人 2026-10-05 拍板, 写死在代码里)
NOTICE_PUSH_DAILY_MAX = 3
NOTICE_PUSH_OPS_DAILY_MAX = 1
#: 免打扰时段(北京时间小时, 闭区间; 交易时点类豁免)
QUIET_HOURS = (23, 8)

#: 推送偏好项 → 默认开关 / 是否"运营类"(受 OPS 上限约束) / 说明
#: key 会写进 notices.push_key, 发送侧据此查用户偏好
PUSH_KEYS = {
    "system":    (True,  False, "系统公告与更新"),
    "ops":       (True,  True,  "活动与运营通知"),
    "expire":    (True,  False, "会员到期与续费提醒"),
    "quota":     (True,  False, "免费次数用尽提醒"),
    "invite":    (True,  True,  "邀请好友成功通知"),
    "checkin":   (True,  True,  "每日签到提醒"),
    "auction":   (True,  False, "竞价开始提醒(9:15)"),
    "picks":     (True,  False, "今日名单就绪提醒(9:26)"),
}
DEFAULT_PREFS = {k: v[0] for k, v in PUSH_KEYS.items()}

#: 消息有效期兜底: account 类 30 天, trade 类 3 天, system 类按发布时选的天数(0=长期)
DEFAULT_DAYS = {"system": 0, "account": 30, "trade": 3}


# ---------------------------------------------------------------- 基础


def _conn():
    c = sqlite3.connect(config.DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _now():
    return int(time.time())


def _bj_day(ts=None):
    """北京时间自然日 YYYYMMDD(配额/推送计数都按北京日切)"""
    return time.strftime("%Y%m%d", time.gmtime(int(ts or _now()) + 8 * 3600))


def _bj_hour(ts=None):
    return int(time.strftime("%H", time.gmtime(int(ts or _now()) + 8 * 3600)))


def _fmt_date(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts or 0) + 8 * 3600)) if ts else ""


def _new_key():
    return "nk_" + uuid.uuid4().hex[:20]


# ---------------------------------------------------------------- 表结构


def init_tables(conn=None):
    """建表/补列。在 database.init_db() 里调用一次; 幂等。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(config.DB_FILE, timeout=10)
    cur = conn.cursor()

    # --- notices 补列(老库逐列加, SQLite 不支持 IF NOT EXISTS 的 ADD COLUMN)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(notices)").fetchall()}
    for col, ddl in (
        ("nkey", "TEXT"),                                   # 🔴 业务唯一键(见文件头 1)
        ("category", "TEXT NOT NULL DEFAULT 'system'"),     # system/account/trade
        ("status", "TEXT NOT NULL DEFAULT 'sent'"),         # draft/scheduled/sent
        ("publish_at", "INTEGER NOT NULL DEFAULT 0"),       # 定时发送时刻(0=立即)
        ("scope_uid", "INTEGER NOT NULL DEFAULT 0"),        # 0=广播; >0 = 只给这个人看
        ("dedup_key", "TEXT NOT NULL DEFAULT ''"),          # 幂等键(同一用户同一天只发一次)
        ("push_key", "TEXT NOT NULL DEFAULT 'system'"),     # 对应 PUSH_KEYS 的偏好项
        ("action_type", "TEXT NOT NULL DEFAULT ''"),        # none/route/copy
        ("action_value", "TEXT NOT NULL DEFAULT ''"),       # 路由或待复制文本
        ("meta", "TEXT NOT NULL DEFAULT ''"),               # JSON 快照(用于 account 自愈)
        ("sent_at", "INTEGER NOT NULL DEFAULT 0"),          # 实际投递时刻
        # A4 标签定向(2026-10-06 第二批): target='tag' 时按 target_tag 过滤人群
        ("target_tag", "TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in cols:
            cur.execute("ALTER TABLE notices ADD COLUMN %s %s" % (col, ddl))

    # 迁移: 老公告补 nkey(生产公告数为 0, 但本地/测试库可能有)
    try:
        rows = cur.execute("SELECT id, created_at FROM notices WHERE nkey IS NULL OR nkey=''").fetchall()
        for r in rows:
            cur.execute("UPDATE notices SET nkey=? WHERE id=?", ("nk_legacy_%s_%s" % (r[0], r[1]), r[0]))
    except Exception as e:
        log.warning("notices.nkey 迁移失败 err=%s", e)

    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notices_nkey ON notices(nkey)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notices_due ON notices(status, publish_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notices_scope ON notices(scope_uid, off_at)")

    # --- notice_reads 补 nkey / deleted
    rcols = {r[1] for r in cur.execute("PRAGMA table_info(notice_reads)").fetchall()}
    if "nkey" not in rcols:
        cur.execute("ALTER TABLE notice_reads ADD COLUMN nkey TEXT")
    if "deleted" not in rcols:
        cur.execute("ALTER TABLE notice_reads ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    # 回填: 老回执按 notice_id 找回 nkey
    try:
        cur.execute("UPDATE notice_reads SET nkey=(SELECT nkey FROM notices WHERE notices.id=notice_reads.notice_id) "
                    "WHERE (nkey IS NULL OR nkey='') AND notice_id>0")
    except Exception as e:
        log.warning("notice_reads.nkey 回填失败 err=%s", e)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notice_reads_nkey ON notice_reads(user_id, nkey)")

    # --- 触达统计
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notice_stats (
            nkey         TEXT PRIMARY KEY,
            title        TEXT,
            level        TEXT,
            target       TEXT,
            category     TEXT,
            created_at   INTEGER,
            target_count INTEGER NOT NULL DEFAULT 0,
            push_sent    INTEGER NOT NULL DEFAULT 0,
            push_failed  INTEGER NOT NULL DEFAULT 0,
            read_count   INTEGER NOT NULL DEFAULT 0,
            click_count  INTEGER NOT NULL DEFAULT 0
        )
    """)

    # --- 点击明细(2026-10-06 第二批): 触达明细导出要按人看"谁点了", 计数表给不了;
    #     (uid,nkey) 做主键 ⇒ 同一个人点多次只记一次, 明细与计数永远一致。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notice_clicks (
            uid  INTEGER NOT NULL,
            nkey TEXT NOT NULL,
            ts   INTEGER NOT NULL,
            PRIMARY KEY (uid, nkey)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notice_clicks_nkey ON notice_clicks(nkey)")

    # --- 订阅偏好
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notice_prefs (
            user_id   INTEGER PRIMARY KEY,
            prefs     TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER
        )
    """)

    # --- 推送日计数(频次上限)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notice_push_daily (
            uid   INTEGER NOT NULL,
            day   TEXT NOT NULL,
            n     INTEGER NOT NULL DEFAULT 0,
            ops_n INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (uid, day)
        )
    """)

    # 🔴 无论连接是谁开的都要 commit: database.init_db() 会把自己的 conn 传进来,
    #    不提交的话建表只存在于事务里, 进程一退出就什么都没有(下一轮又重跑一遍迁移)。
    conn.commit()
    if own:
        conn.close()
    return True


# ---------------------------------------------------------------- 偏好


def get_prefs(uid):
    """用户推送偏好(缺省全开)。返回 dict。"""
    try:
        c = _conn()
        r = c.execute("SELECT prefs FROM notice_prefs WHERE user_id=?", (uid,)).fetchone()
        c.close()
        if r:
            try:
                p = json.loads(r[0] or "{}")
            except (TypeError, ValueError):
                p = {}
        else:
            p = {}
    except Exception as e:
        log.warning("读取推送偏好失败 uid=%s err=%s", uid, e)
        p = {}
    out = dict(DEFAULT_PREFS)
    out.update({k: bool(v) for k, v in p.items() if k in DEFAULT_PREFS})
    return out


def set_prefs(uid, patch):
    """局部更新偏好, 只认 PUSH_KEYS 里的键。返回合并后的完整偏好。"""
    cur = get_prefs(uid)
    for k, v in (patch or {}).items():
        if k in DEFAULT_PREFS:
            cur[k] = bool(v)
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO notice_prefs (user_id, prefs, updated_at) VALUES (?,?,?)",
                  (uid, json.dumps(cur, ensure_ascii=False), _now()))
        c.commit()
        c.close()
    except Exception as e:
        log.warning("保存推送偏好失败 uid=%s err=%s", uid, e)
    return cur


# ---------------------------------------------------------------- 频次上限


def _push_budget_ok(uid, push_key):
    """是否还能给这个人推一条。不做完就不推 —— 站内照发。"""
    is_ops = bool(PUSH_KEYS.get(push_key, (True, False, ""))[1])
    day = _bj_day()
    try:
        c = _conn()
        r = c.execute("SELECT n, ops_n FROM notice_push_daily WHERE uid=? AND day=?", (uid, day)).fetchone()
        if r is None:
            c.execute("INSERT INTO notice_push_daily (uid, day, n, ops_n) VALUES (?,?,0,0)", (uid, day))
            n, ops_n = 0, 0
        else:
            n, ops_n = int(r[0] or 0), int(r[1] or 0)
        if n >= NOTICE_PUSH_DAILY_MAX:
            c.close()
            return False, "已达每日上限(%s)" % NOTICE_PUSH_DAILY_MAX
        if is_ops and ops_n >= NOTICE_PUSH_OPS_DAILY_MAX:
            c.close()
            return False, "运营类已达每日上限(%s)" % NOTICE_PUSH_OPS_DAILY_MAX
        c.close()
        return True, ""
    except Exception as e:
        log.warning("推送额度检查失败 uid=%s err=%s", uid, e)
        return True, ""   # 检查失败 ⇒ 放行(宁可多发, 不要因为计数表坏了整条发不出去)


def _push_budget_take(uid, push_key):
    day = _bj_day()
    is_ops = bool(PUSH_KEYS.get(push_key, (True, False, ""))[1])
    try:
        c = _conn()
        c.execute("INSERT OR IGNORE INTO notice_push_daily (uid, day, n, ops_n) VALUES (?,?,0,0)", (uid, day))
        c.execute("UPDATE notice_push_daily SET n=n+1 WHERE uid=? AND day=?", (uid, day))
        if is_ops:
            c.execute("UPDATE notice_push_daily SET ops_n=ops_n+1 WHERE uid=? AND day=?", (uid, day))
        c.commit()
        c.close()
    except Exception as e:
        log.warning("推送计数失败 uid=%s err=%s", uid, e)


def _in_quiet_hours(push_key):
    """免打扰时段; 交易时点类(auction/picks)豁免 —— 竞价提醒晚一分钟就没意义了"""
    if push_key in ("auction", "picks"):
        return False
    h = _bj_hour()
    lo, hi = QUIET_HOURS
    return h >= lo or h < hi


# ---------------------------------------------------------------- 定向人群


def _target_uids(target, tag=""):
    """定向 → uid 列表。target=all 时返回 None(表示全员, 避免拉全表)

    target='tag' 时按标签取人(user_tags 表) —— A4 定向运营的前提:
    没有标签就只能"全部/免费/付费/VIP"四档, 没法对"免费但很活跃"这群最该转化的人单独说话。
    """
    try:
        c = _conn()
        if target == "tag":
            from . import user_ops
            c.close()
            return user_ops.uids_by_tag(tag)
        if target == "vip":
            rows = c.execute("SELECT id FROM users WHERE member_level=2").fetchall()
        elif target == "member":
            rows = c.execute("SELECT id FROM users WHERE member_level>=1").fetchall()
        elif target == "free":
            rows = c.execute("SELECT id FROM users WHERE member_level=0").fetchall()
        else:
            c.close()
            return None
        c.close()
        return [int(r[0]) for r in rows]
    except Exception as e:
        log.warning("定向人群查询失败 target=%s err=%s", target, e)
        return None


def preview_count(target, tag=""):
    """发布前预估人数(含"其中已开推送 N 人")。支持 target='tag' + tag 名。"""
    try:
        c = _conn()
        if target == "all":
            total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            pushed = c.execute("SELECT COUNT(DISTINCT user_id) FROM push_subscriptions").fetchone()[0]
        else:
            uids = _target_uids(target, tag) or []
            total = len(uids)
            pushed = 0
            if uids:
                ph = ",".join("?" * len(uids))
                pushed = c.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM push_subscriptions WHERE user_id IN (%s)" % ph,
                    uids).fetchone()[0]
        c.close()
        return {"total": int(total), "pushable": int(pushed)}
    except Exception as e:
        log.warning("人数预估失败 err=%s", e)
        return {"total": 0, "pushable": 0}


# ---------------------------------------------------------------- 发送


def _do_push(uids, title, body, url, push_key, nkey):
    """按偏好 + 频次上限 + 免打扰 逐人推送。返回 (成功数, 失败数)。

    🔴 uids=None 表示全员: 此时以 push_subscriptions 的 user_id 为人群(没订阅的人推不了)。
    """
    from ..services import webpush as wp

    if _in_quiet_hours(push_key):
        log.info("免打扰时段, 跳过推送 nkey=%s push_key=%s", nkey, push_key)
        return 0, 0

    try:
        c = _conn()
        if uids is None:
            rows = c.execute("SELECT user_id, endpoint, p256dh, auth FROM push_subscriptions").fetchall()
        elif not uids:
            c.close()
            return 0, 0
        else:
            ph = ",".join("?" * len(uids))
            rows = c.execute(
                "SELECT user_id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id IN (%s)" % ph,
                list(uids)).fetchall()
        c.close()
    except Exception as e:
        log.warning("推送订阅查询失败 err=%s", e)
        return 0, 0

    ok_n = 0
    fail_n = 0
    skipped = 0
    for r in rows:
        d = dict(r)
        uid = int(d.get("user_id") or 0)
        if uid <= 0:
            continue
        try:
            prefs = get_prefs(uid)
            if not prefs.get(push_key, True):
                skipped += 1
                continue
            ok, why = _push_budget_ok(uid, push_key)
            if not ok:
                skipped += 1
                continue
            sent, err = wp.send_one({"endpoint": d["endpoint"], "p256dh": d["p256dh"], "auth": d["auth"]},
                                    title, body, url)
            if sent:
                ok_n += 1
                _push_budget_take(uid, push_key)
                try:
                    c = _conn()
                    c.execute("UPDATE push_subscriptions SET last_ok_at=? WHERE endpoint=?",
                              (_now(), d["endpoint"]))
                    c.commit()
                    c.close()
                except Exception:
                    pass
            else:
                fail_n += 1
                if err and ("失效" in str(err) or "410" in str(err) or "404" in str(err)):
                    try:
                        c = _conn()
                        c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (d["endpoint"],))
                        c.commit()
                        c.close()
                    except Exception:
                        pass
        except Exception as e:
            fail_n += 1
            log.warning("推送异常 uid=%s err=%s", uid, e)
    if skipped:
        log.info("推送跳过(偏好关闭或已达上限) nkey=%s skip=%s", nkey, skipped)
    return ok_n, fail_n


def _touch_stats(nkey, conn=None, **kw):
    """写触达统计。

    🔴 `conn` 参数不是可选的优化, 是**必须**的(2026-10-06 事故):
       调用方若已经在一个**未提交**的连接上写了库(如 mark_read 先 INSERT notice_reads),
       这里再 `_conn()` 开第二个连接写同一个 SQLite 文件 ⇒ **自己把自己锁死**:
       连接 A 持有写锁未提交, 连接 B 写不进去, 干等 busy_timeout(5s) 后报
       "database is locked" ⇒ 一条已读回执要 **10 秒** 才返回, 且统计根本没写进去。
       修复: 事务进行中必须把当前连接传进来复用。
    """
    try:
        own = conn is None
        c = conn or _conn()
        c.execute("INSERT OR IGNORE INTO notice_stats (nkey, created_at) VALUES (?,?)", (nkey, _now()))
        if kw:
            sets = ", ".join("%s=%s+?" % (k, k) for k in kw)
            c.execute("UPDATE notice_stats SET %s WHERE nkey=?" % sets, list(kw.values()) + [nkey])
        c.commit()
        if own:
            c.close()
    except Exception as e:
        log.warning("触达统计写入失败 nkey=%s err=%s", nkey, e)


def publish(title, body, level="info", target="all", category="system", days=None,
            scope_uid=0, dedup_key="", push_key=None, action_type="", action_value="",
            meta=None, publish_at=0, status="sent", created_by="system", url="/messages",
            skip_push=False, tag=""):
    """统一发布入口。返回 {"ok", "nkey", "id", "pushed", "failed", "target_count"}

    · status='draft'     只存不投(后台草稿)
    · status='scheduled' 定时, 由 worker 的 dispatch_due() 到点投递
    · status='sent'      立即投递
    · dedup_key 非空时: 同一用户已存在同 dedup_key 的有效消息 ⇒ 不重复发(返回 ok=False, dup=True)
    """
    title = (title or "").strip()
    body = (body or "").strip()
    if not title:
        return {"ok": False, "msg": "标题不能为空"}
    if level not in LEVELS:
        level = "info"
    if target not in TARGETS:
        target = "all"
    if category not in CATEGORIES:
        category = "system"
    if days is None:
        days = DEFAULT_DAYS.get(category, 0)
    now = _now()
    nkey = _new_key()
    push_key = push_key or ("account" if category == "account" else ("system" if category == "system" else "picks"))

    conn = _conn()
    try:
        # 幂等: 同一用户同一 dedup_key 只发一次(配额用尽/到期提醒会反复触发)
        if dedup_key and scope_uid:
            r = conn.execute(
                "SELECT id FROM notices WHERE dedup_key=? AND scope_uid=? AND off_at=0 "
                "AND (end_ts=0 OR end_ts>?) LIMIT 1", (dedup_key, scope_uid, now)).fetchone()
            if r:
                return {"ok": False, "dup": True, "id": int(r[0])}

        start_ts = int(publish_at or 0) or now
        end_ts = start_ts + int(days) * 86400 if days and days > 0 else 0
        cur = conn.execute(
            "INSERT INTO notices (nkey, title, body, level, target, category, status, publish_at, "
            " start_ts, end_ts, scope_uid, dedup_key, push_key, action_type, action_value, meta, "
            " off_at, sent_at, created_by, created_at, target_tag) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?)",
            (nkey, title, body, level, target, category, status, int(publish_at or 0),
             start_ts, end_ts, int(scope_uid or 0), dedup_key or "", push_key,
             action_type or "", action_value or "",
             json.dumps(meta or {}, ensure_ascii=False), str(created_by), now,
             str(tag or "") if target == "tag" else ""))
        nid = cur.lastrowid
        # 🔴 自愈: id 复用导致旧回执错挂(见文件头 1)。哪怕走软删也顺手清一次, 一行 DELETE 可忽略。
        conn.execute("DELETE FROM notice_reads WHERE notice_id=? AND (nkey IS NULL OR nkey='')", (nid,))
        conn.commit()
    except Exception as e:
        log.warning("消息写入失败 err=%s", e)
        return {"ok": False, "msg": "写入失败"}
    finally:
        conn.close()

    if status != "sent":
        _touch_stats(nkey, title=title, level=level, target=target, category=category)
        return {"ok": True, "nkey": nkey, "id": nid, "pushed": 0, "failed": 0,
                "target_count": 0, "status": status}

    return _deliver(nid, nkey, title, body, level, target, category, push_key, url,
                    skip_push=skip_push, tag=tag)


def _deliver(nid, nkey, title, body, level, target, category, push_key, url="/messages",
             skip_push=False, tag=""):
    """实际投递(上架 + 推送 + 统计)。立即发布与定时到点都走这里。

    skip_push=True 时只上架、不下发推送 —— 供 A5「仅自己可见的测试发送」使用:
    测试的是"这条消息长什么样 / 排版对不对", 不该顺手把人推一遍。
    """
    now = _now()
    try:
        c = _conn()
        c.execute("UPDATE notices SET status='sent', sent_at=?, start_ts=? WHERE id=? AND status!='sent'",
                  (now, now, nid))
        c.commit()
        c.close()
    except Exception as e:
        log.warning("消息上架失败 nid=%s err=%s", nid, e)

    uids = None if target == "all" else (_target_uids(target, tag) or [])
    total = preview_count(target, tag).get("total", 0)
    ok_n, fail_n = 0, 0
    if skip_push:
        log.info("测试发送(仅自己可见) nid=%s ⇒ 跳过推送下发", nid)
    else:
        try:
            ok_n, fail_n = _do_push(uids, title, (body or "")[:80], url, push_key, nkey)
        except Exception as e:
            log.warning("推送下发异常 nid=%s err=%s", nid, e)

    try:
        c = _conn()
        c.execute("INSERT OR IGNORE INTO notice_stats (nkey, created_at) VALUES (?,?)", (nkey, now))
        c.execute("UPDATE notice_stats SET title=?, level=?, target=?, category=?, "
                  "target_count=?, push_sent=?, push_failed=? WHERE nkey=?",
                  (title, level, target, category, int(total), int(ok_n), int(fail_n), nkey))
        c.commit()
        c.close()
    except Exception as e:
        log.warning("触达统计初始化失败 nkey=%s err=%s", nkey, e)

    log.info("消息投递 nid=%s nkey=%s cat=%s target=%s 人群=%s 推送成功=%s 失败=%s",
             nid, nkey, category, target, total, ok_n, fail_n)
    return {"ok": True, "nkey": nkey, "id": nid, "pushed": ok_n, "failed": fail_n,
            "target_count": total}


# ---------------------------------------------------------------- 定时投递


def dispatch_due():
    """把到点的 scheduled 消息投出去。worker 每 30s 调一次。"""
    now = _now()
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, nkey, title, body, level, target, category, push_key, action_type, action_value, "
            " COALESCE(target_tag,'') target_tag "
            "FROM notices WHERE status='scheduled' AND off_at=0 AND publish_at>0 AND publish_at<=? "
            "ORDER BY publish_at ASC LIMIT 50", (now,)).fetchall()
        c.close()
    except Exception as e:
        log.warning("定时消息扫描失败 err=%s", e)
        return 0
    n = 0
    for r in rows:
        d = dict(r)
        url = d.get("action_value") if d.get("action_type") == "route" else "/messages"
        try:
            _deliver(int(d["id"]), d["nkey"], d["title"], d["body"], d.get("level") or "info",
                     d.get("target") or "all", d.get("category") or "system",
                     d.get("push_key") or "system", url,
                     tag=d.get("target_tag") or "")
            n += 1
        except Exception as e:
            log.warning("定时投递失败 nid=%s err=%s", d.get("id"), e)
    return n


def start_scheduler():
    """消息中心调度: 定时投递 + 竞价闹钟 + 名单就绪 + 到期提醒 + 签到提醒。

    只在 worker 进程启动(与既有 scheduler 同风格); 线程内自愈异常, 不因一次失败整体退出。
    """
    import threading

    def _loop_dispatch():
        while True:
            try:
                dispatch_due()
            except Exception as e:
                log.warning("dispatch 异常 err=%s", e)
            time.sleep(30)

    def _loop_tags():
        """每小时一次: 重算用户分层标签(A4)。

        标签是"谁该被定向运营"的依据, 依赖最近 7/30 天的活跃与撞墙数据 ⇒ 会随行为变化,
        必须定期重算。放在独立线程: 它要扫全表, 绝不能挂在 60s 的时点 job 里拖慢竞价闹钟。
        """
        while True:
            try:
                from . import user_ops
                n = user_ops.refresh_auto_tags()
                log.info("自动标签重算完成 rows=%s", n)
            except Exception as e:
                log.warning("自动标签重算失败 err=%s", e)
            time.sleep(3600)

    def _loop_jobs():
        """每分钟检查一次: 交易时点类 / 每日一次的到期与签到提醒"""
        fired = set()
        while True:
            try:
                _tick_jobs(fired)
            except Exception as e:
                log.warning("消息 job 异常 err=%s", e)
            time.sleep(60)

    threading.Thread(target=_loop_tags, name="user-tags", daemon=True).start()

    threading.Thread(target=_loop_dispatch, name="notice-dispatch", daemon=True).start()
    threading.Thread(target=_loop_jobs, name="notice-jobs", daemon=True).start()
    log.info("消息中心调度已启动(定时投递 30s + 时点/到期/签到 job 60s)")


def _tick_jobs(fired):
    """一分钟内到点的 job。fired 记录"今天已触发", 防止重复推送。"""
    from ..core import trade_calendar as tcal
    now = _now()
    day = _bj_day(now)
    hhmm = time.strftime("%H:%M", time.gmtime(now + 8 * 3600))

    try:
        is_td = bool(tcal.is_trade_day_now(now))
    except Exception:
        is_td = True   # 日历不可用 ⇒ 保守放行(宁可多发一条竞价提醒, 不要漏)

    def _once(key):
        k = "%s:%s" % (day, key)
        if k in fired:
            return False
        fired.add(k)
        # 只保留当天的键, 防止集合无限增长
        if len(fired) > 200:
            for old in list(fired):
                if not old.startswith(day + ":"):
                    fired.discard(old)
        return True

    # ① 竞价闹钟(只在交易日; 三档时点, 用户偏好 auction 控制)
    if is_td:
        for at, txt in (("09:15", "竞价开始了，9:15–9:25 可撤单，9:20 后不可撤"),
                        ("09:20", "竞价进入 9:20 后不可撤单阶段，9:25 定格"),
                        ("09:25", "竞价定格，名单即将生成")):
            if hhmm == at and _once("auction" + at):
                try:
                    broadcast_trade("竞价提醒 · %s" % at, txt, push_key="auction",
                                    dedup="auction:%s" % at, days=1)
                except Exception as e:
                    log.warning("竞价闹钟失败 %s err=%s", at, e)

        # ② 名单就绪(9:26)
        if hhmm == "09:26" and _once("picks0926"):
            try:
                job_picks_ready()
            except Exception as e:
                log.warning("名单就绪推送失败 err=%s", e)

    # ③ 到期提醒(每日 08:30, 落表 + 推送; 见文件头 3 的自愈约定)
    if hhmm == "08:30" and _once("expire0830"):
        try:
            job_expire_reminders()
        except Exception as e:
            log.warning("到期提醒 job 失败 err=%s", e)

    # ④ 签到提醒(每日 09:00, 运营类受 OPS 上限约束)
    if hhmm == "09:00" and _once("checkin0900"):
        try:
            job_checkin_reminder()
        except Exception as e:
            log.warning("签到提醒 job 失败 err=%s", e)


def broadcast_trade(title, body, push_key="picks", dedup="", days=1, level="info",
                    action_type="route", action_value="/auction"):
    """交易时点类广播(竞价闹钟/名单就绪)。dedup 按天, 避免重启 worker 重复推。"""
    return publish(title=title, body=body, level=level, target="all", category="trade",
                   days=days, dedup_key=("%s:%s" % (dedup, _bj_day())) if dedup else "",
                   push_key=push_key, action_type=action_type, action_value=action_value,
                   created_by="system")


def job_picks_ready():
    """9:26 名单就绪提醒。

    🔴 刻意**不写条数**: 名单条数要等 9:25 数据齐了才准, 而这个 job 在 9:26 就跑 ——
       取不到就说"已生成", 绝不编数字(项目一贯原则; 编了第二天用户就会拿来对质)。
       等哪天有可靠的条数口径了再补, 不要在推送里赌。
    """
    return broadcast_trade("今日名单已就绪", "今日竞价名单已生成，点击查看。",
                           push_key="picks", dedup="picks_ready", action_value="/")


def ensure_account_fresh(uid):
    """🔴 到期提醒的**读取侧兜底**(2026-10-06)。

    到期提醒从"实时推导"改成"落表"之后, 就依赖 worker 的 08:30 job 去生成。
    万一 worker 没启动/没部署 ⇒ 用户将**完全看不到**到期提醒(功能倒退)。
    所以在 GET /api/notices 时按需补一条: 只有"快到期且表里还没有"才写,
    有 dedup_key 保证不会重复。代价是一次主键查询。
    """
    try:
        u = users.find_user_by_id(uid) or {}
    except Exception:
        return False
    if u.get("is_admin"):
        return False
    et = int(u.get("expire_at") or 0)
    if not et:
        return False
    now = _now()
    days = int((et - now + 86399) // 86400)
    if now > et:
        bucket, level = "t0", "urgent"
    elif days <= 1:
        bucket, level = "t1", "warn"
    elif days <= 3:
        bucket, level = "t3", "warn"
    elif days <= 7:
        bucket, level = "t7", "info"
    else:
        return False

    dedup = "expire:%s:%s" % (bucket, _fmt_date(et))
    try:
        c = _conn()
        r = c.execute("SELECT 1 FROM notices WHERE dedup_key=? AND scope_uid=? AND off_at=0 LIMIT 1",
                      (dedup, int(uid))).fetchone()
        c.close()
        if r:
            return False
    except Exception:
        return False

    try:
        free_lim = int(quota_svc.limit_of(uid, "picker"))
    except Exception:
        free_lim = 0
    if bucket == "t0":
        title = "会员已到期"
        body = "到期日 %s。到期后每日免费次数降为 %s 次，续费后立即恢复。" % (_fmt_date(et), free_lim or "-")
    else:
        title = "会员将在 %d 天后到期" % max(days, 0)
        body = ("到期日 %s，到期后每日免费次数降为 %s 次。" % (_fmt_date(et), free_lim or "-")
                if bucket in ("t1", "t3")
                else "到期日 %s，可提前续费无缝衔接。" % _fmt_date(et))
    publish(title=title, body=body, level=level, target="all", category="account",
            scope_uid=uid, days=30, push_key="expire", dedup_key=dedup,
            action_type="route", action_value="/member",
            meta={"kind": "expire", "expire_at": et, "bucket": bucket},
            created_by="system")
    return True


def job_expire_reminders():
    """会员到期四级触达: T-7 / T-3 / T-1 / T+0(已过期)。

    🔴 落表(为了能统计已读率), 但带 meta.expire_at 快照做自愈(见文件头 3);
       且用 dedup_key 保证同一到期日同一档只发一次。
    """
    now = _now()
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, expire_at, member_level, is_admin FROM users "
            "WHERE expire_at>0 AND expire_at<=? ", (now + 7 * 86400,)).fetchall()
        c.close()
    except Exception as e:
        log.warning("到期扫描失败 err=%s", e)
        return 0

    n = 0
    for r in rows:
        d = dict(r)
        uid = int(d["id"])
        if d.get("is_admin"):
            continue
        et = int(d.get("expire_at") or 0)
        if not et:
            continue
        days = int((et - now + 86399) // 86400)
        if now > et:
            bucket, level = "t0", "urgent"
        elif days <= 1:
            bucket, level = "t1", "warn"
        elif days <= 3:
            bucket, level = "t3", "warn"
        elif days <= 7:
            bucket, level = "t7", "info"
        else:
            continue

        try:
            free_lim = int(quota_svc.limit_of(uid, "picker"))
        except Exception:
            free_lim = 0

        if bucket == "t0":
            title = "会员已到期"
            body = "到期日 %s。到期后每日免费次数降为 %s 次，续费后立即恢复。" % (_fmt_date(et), free_lim or "-")
        else:
            title = "会员将在 %d 天后到期" % max(days, 0)
            body = ("到期日 %s，到期后每日免费次数降为 %s 次。" % (_fmt_date(et), free_lim or "-")
                    if bucket in ("t1", "t3")
                    else "到期日 %s，可提前续费无缝衔接。" % _fmt_date(et))

        res = publish(title=title, body=body, level=level, target="all", category="account",
                      scope_uid=uid, days=30, push_key="expire",
                      dedup_key="expire:%s:%s" % (bucket, _fmt_date(et)),
                      action_type="route", action_value="/member",
                      meta={"kind": "expire", "expire_at": et, "bucket": bucket},
                      created_by="system")
        if res.get("ok"):
            n += 1
    if n:
        log.info("到期提醒已下发 %s 条", n)
    return n


def job_checkin_reminder():
    """签到提醒(仅未签到的非特权用户; 运营类, 受每日 1 条上限约束)"""
    day = _bj_day()
    try:
        c = _conn()
        rows = c.execute(
            "SELECT u.id FROM users u WHERE u.member_level=0 AND u.is_admin=0 AND NOT EXISTS "
            "(SELECT 1 FROM user_checkin k WHERE k.uid=u.id AND k.date=?)", (day,)).fetchall()
        c.close()
    except Exception as e:
        log.warning("签到提醒扫描失败 err=%s", e)
        return 0
    n = 0
    for r in rows:
        uid = int(r[0])
        res = publish(title="今日尚未签到", body="签到可领取额外的选股快照次数，每日一次。",
                      level="info", target="all", category="system", scope_uid=uid,
                      days=1, push_key="checkin", dedup_key="checkin:%s" % day,
                      action_type="route", action_value="/member",
                      meta={"kind": "checkin"}, created_by="system")
        if res.get("ok"):
            n += 1
    return n


# ---------------------------------------------------------------- 事件钩子


def notify_quota_exhausted(uid, feature, label, limit):
    """免费次数用尽 ⇒ 转化窗口。🔴 每天每个功能只发一条(dedup)。"""
    day = _bj_day()
    return publish(title="今日「%s」免费次数已用完" % (label or feature),
                   body="每日 %s 次，明天 0 点重置。开通会员不限次数，签到也可额外领取。" % (limit or "-"),
                   level="info", target="all", category="system", scope_uid=uid,
                   days=1, push_key="quota", dedup_key="quota:%s:%s" % (feature, day),
                   action_type="route", action_value="/member",
                   meta={"kind": "quota", "feature": feature}, created_by="system")


def notify_invite_reward(uid, invitee_name, days, expire_date):
    """邀请成功 ⇒ 裂变正反馈(原实现完全无通知)"""
    return publish(title="邀请成功，获得 %d 天会员" % int(days or 0),
                   body="%s 通过你的邀请码注册，你的会员已延长至 %s。"
                        % (invitee_name or "好友", expire_date or ""),
                   level="info", target="all", category="system", scope_uid=uid,
                   days=7, push_key="invite", action_type="route", action_value="/member",
                   meta={"kind": "invite"}, created_by="system")


def on_membership_changed(uid):
    """续费/改等级后调用: 下架该用户"已过时"的到期提醒(见文件头 3 的 (b))。

    🔴 更稳的兜底在读取侧(_valid 校验 meta.expire_at), 这里只是让列表尽快干净。
    """
    try:
        c = _conn()
        c.execute("UPDATE notices SET off_at=? WHERE scope_uid=? AND off_at=0 AND category='account' "
                  "AND meta LIKE '%\"kind\": \"expire\"%'", (_now(), int(uid)))
        c.commit()
        c.close()
    except Exception as e:
        log.warning("到期提醒下架失败 uid=%s err=%s", uid, e)


# ---------------------------------------------------------------- 用户侧读取


def _expire_snapshot_valid(meta, uid):
    """account 类自愈: meta.expire_at 与当前不一致 ⇒ 已续费/已改, 该条作废"""
    try:
        m = json.loads(meta or "{}")
    except (TypeError, ValueError):
        return True
    if m.get("kind") != "expire":
        return True
    try:
        cur_et = int((users.find_user_by_id(uid) or {}).get("expire_at") or 0)
    except Exception:
        return True
    return int(m.get("expire_at") or 0) == cur_et


def fetch(uid, level, limit=40):
    """用户消息列表 + 未读数。返回 (items, unread)

    items 字段: id/nkey/type/category/level/title/body/ts/read/action_type/action_value
    "type" 保留旧值 broadcast|account 以兼容前端; category 是新的三分类。
    """
    now = _now()
    items = []
    dead = []          # 自愈: 需要下架的过期 account 行
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, nkey, title, body, level, target, category, scope_uid, start_ts, end_ts, "
            " off_at, action_type, action_value, meta, push_key, target_tag FROM notices "
            "WHERE off_at=0 AND status='sent' AND start_ts<=? AND (scope_uid=0 OR scope_uid=?) "
            "ORDER BY start_ts DESC LIMIT ?", (now, int(uid), int(limit) * 2)).fetchall()
        read_rows = c.execute(
            "SELECT nkey FROM notice_reads WHERE user_id=? AND deleted=0 AND nkey IS NOT NULL",
            (int(uid),)).fetchall()
        del_rows = c.execute(
            "SELECT nkey FROM notice_reads WHERE user_id=? AND deleted=1 AND nkey IS NOT NULL",
            (int(uid),)).fetchall()
        c.close()
    except Exception as e:
        log.warning("消息读取失败 uid=%s err=%s", uid, e)
        return [], 0

    read_set = {r[0] for r in read_rows}
    del_set = {r[0] for r in del_rows}

    # 标签定向(A4): 同一个标签只查一次, 结果缓存在本次请求内 —— 不逐条消息查库。
    tag_cache = {}

    def _tag_set(name):
        if name not in tag_cache:
            try:
                from . import user_ops
                tag_cache[name] = set(user_ops.uids_by_tag(name))
            except Exception:
                tag_cache[name] = set()
        return tag_cache[name]

    for r in rows:
        d = dict(r)
        if d.get("end_ts") and now > int(d["end_ts"]):
            continue
        tgt = d.get("target") or "all"
        if tgt == "tag":
            if int(uid) not in _tag_set(d.get("target_tag") or ""):
                continue
        elif tgt != "all":
            allow = TARGET_LEVELS.get(tgt)
            if allow is not None and level not in allow:
                continue
        nkey = d.get("nkey")
        if not nkey or nkey in del_set:
            continue
        # account 类自愈(见文件头 3)
        cat = d.get("category") or "system"
        if cat == "account" and not _expire_snapshot_valid(d.get("meta"), uid):
            dead.append(int(d["id"]))
            continue
        items.append({
            "id": "n%s" % d["id"],
            "nid": int(d["id"]),
            "nkey": nkey,
            "type": "account" if cat == "account" else "broadcast",
            "category": cat,
            "level": d.get("level") or "info",
            "title": d.get("title") or "",
            "body": d.get("body") or "",
            "ts": int(d.get("start_ts") or 0),
            "read": 1 if nkey in read_set else 0,
            "action_type": d.get("action_type") or "",
            "action_value": d.get("action_value") or "",
        })

    if dead:
        try:
            c = _conn()
            c.executemany("UPDATE notices SET off_at=? WHERE id=?", [(_now(), i) for i in dead])
            c.commit()
            c.close()
        except Exception:
            pass

    items = items[:limit]

    # 🔴 红点规则(主人 2026-10-05 拍板): 只有"需要行动"的才亮红点 ——
    #   未读的 warn/urgent + account 类未读; info 级只进列表不亮红点(否则红点泛滥失效)。
    unread = sum(1 for it in items
                 if not it["read"] and (it["category"] == "account" or it["level"] in ("warn", "urgent")))
    return items, unread


def mark_read(uid, nkeys=None, all_=False):
    now = _now()
    n = 0
    try:
        c = _conn()
        if all_:
            rows = c.execute(
                "SELECT nkey FROM notices WHERE off_at=0 AND status='sent' AND nkey IS NOT NULL "
                "AND (scope_uid=0 OR scope_uid=?)", (int(uid),)).fetchall()
            nkeys = [r[0] for r in rows]
        for k in (nkeys or []):
            if not k:
                continue
            c.execute("INSERT OR IGNORE INTO notice_reads (user_id, notice_id, nkey, read_at, deleted) "
                      "VALUES (?,0,?,?,0)", (int(uid), k, now))
            c.execute("UPDATE notice_reads SET deleted=0 WHERE user_id=? AND nkey=?", (int(uid), k))
            n += 1
            # 🔴 必须复用当前连接 c: 上面已经在 c 上写了 notice_reads 且未提交,
            #    再开新连接写同库会自锁(等 5s busytimeout ⇒ 回执 10 秒才返回)。
            _touch_stats(k, conn=c, read_count=1)
        c.commit()
        c.close()
    except Exception as e:
        log.warning("已读回执失败 uid=%s err=%s", uid, e)
        return 0
    return n


def delete_for_user(uid, nkeys):
    """单条删除(对用户隐藏, 不删公告本体 —— 公告是站方资产, 别人还得看)"""
    n = 0
    try:
        c = _conn()
        for k in (nkeys or []):
            if not k:
                continue
            c.execute("INSERT OR IGNORE INTO notice_reads (user_id, notice_id, nkey, read_at, deleted) "
                      "VALUES (?,0,?,?,1)", (int(uid), k, _now()))
            c.execute("UPDATE notice_reads SET deleted=1 WHERE user_id=? AND nkey=?", (int(uid), k))
            n += 1
        c.commit()
        c.close()
    except Exception as e:
        log.warning("消息删除失败 uid=%s err=%s", uid, e)
    return n


def track_click(uid, nkey):
    """消息内行动按钮点击上报(触达漏斗的最后一环)。

    🔴 明细去重: (uid,nkey) 主键 ⇒ 同一个人反复点同一条只算一次, 计数也只 +1。
        否则"点了 5 次"会被当成 5 个人点了, 点击率直接失真。
    """
    try:
        c = _conn()
        cur = c.execute("INSERT OR IGNORE INTO notice_clicks (uid, nkey, ts) VALUES (?,?,?)",
                        (int(uid), str(nkey), _now()))
        hit = cur.rowcount
        c.commit()
        c.close()
        if hit:                       # 首次点击才计数
            _touch_stats(nkey, click_count=1)
    except Exception as e:
        log.warning("点击上报失败 uid=%s nkey=%s err=%s", uid, nkey, e)


def nkeys_by_ids(ids):
    """旧客户端传的是 notices.id ⇒ 换成 nkey 再操作。

    🔴 存在的原因: id 会被复用(见文件头 1), 直接拿 id 当回执键会把回执挂到新公告上。
       新客户端一律传 nkey, 这个只是兼容垫。
    """
    if not ids:
        return []
    try:
        c = _conn()
        ph = ",".join("?" * len(ids))
        rows = c.execute("SELECT nkey FROM notices WHERE id IN (%s) AND nkey IS NOT NULL" % ph,
                         [int(i) for i in ids]).fetchall()
        c.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        log.warning("id→nkey 转换失败 err=%s", e)
        return []


# ---------------------------------------------------------------- 管理端


def off(nkey_or_id):
    """撤回(软删)"""
    try:
        c = _conn()
        c.execute("UPDATE notices SET off_at=? WHERE nkey=? OR id=?",
                  (_now(), str(nkey_or_id), int(nkey_or_id or 0) if str(nkey_or_id).isdigit() else -1))
        c.commit()
        c.close()
        return True
    except Exception as e:
        log.warning("撤回失败 err=%s", e)
        return False


def edit(nkey, **fields):
    """编辑(仅未投递的 draft/scheduled 可改全部; 已投递的只允许改正文与有效期)"""
    allow_sent = {"body", "end_ts", "action_type", "action_value"}
    try:
        c = _conn()
        row = c.execute("SELECT id, status FROM notices WHERE nkey=?", (nkey,)).fetchone()
        if not row:
            c.close()
            return False, "公告不存在"
        st = row[1]
        sets, vals = [], []
        for k, v in fields.items():
            if k not in ("title", "body", "level", "target", "category", "days",
                         "publish_at", "action_type", "action_value", "status"):
                continue
            if st == "sent" and k not in allow_sent:
                continue
            if k == "days":
                continue
            sets.append("%s=?" % k)
            vals.append(v)
        if not sets:
            c.close()
            return False, "没有可修改的字段"
        vals.append(nkey)
        c.execute("UPDATE notices SET %s WHERE nkey=?" % ",".join(sets), vals)
        c.commit()
        c.close()
        return True, ""
    except Exception as e:
        log.warning("编辑公告失败 nkey=%s err=%s", nkey, e)
        return False, "编辑失败"


def admin_rows(limit=50):
    """公告列表 + 触达统计"""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT n.id, n.nkey, n.title, n.body, n.level, n.target, n.category, n.status, "
            " n.publish_at, n.start_ts, n.end_ts, n.off_at, n.created_by, n.created_at, n.action_type, "
            " n.action_value, s.target_count, s.push_sent, s.push_failed, s.read_count, s.click_count "
            "FROM notices n LEFT JOIN notice_stats s ON s.nkey=n.nkey "
            "ORDER BY n.id DESC LIMIT ?", (int(limit),)).fetchall()
        c.close()
    except Exception as e:
        log.warning("公告列表失败 err=%s", e)
        return []
    out = []
    for r in rows:
        d = dict(r)
        for k, v in (("target_count", 0), ("push_sent", 0), ("push_failed", 0),
                     ("read_count", 0), ("click_count", 0)):
            d[k] = int(d.get(k) or 0)
        d["read_rate"] = round(d["read_count"] * 100.0 / d["target_count"], 1) if d["target_count"] else 0.0
        d["date"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(d.get("created_at") or 0) + 8 * 3600))
        d["start_date"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(d.get("start_ts") or 0) + 8 * 3600))
        d["end_date"] = _fmt_date(d.get("end_ts")) if d.get("end_ts") else ""
        d["publish_date"] = time.strftime("%Y-%m-%d %H:%M",
                                         time.gmtime(int(d.get("publish_at") or 0) + 8 * 3600)) if d.get("publish_at") else ""
        out.append(d)
    return out


def expire_reminder_batch(uid_list):
    """后台「一键提醒」: 给指定用户补发一条到期提醒(无视 dedup 与时段, 因为是运营主动触发)。

    🔴 仍走频次上限(不能因为运营点了按钮就把用户推送炸掉)。
    """
    now = _now()
    n = 0
    for uid in (uid_list or []):
        try:
            u = users.find_user_by_id(int(uid)) or {}
        except Exception:
            continue
        et = int(u.get("expire_at") or 0)
        if not et:
            continue
        days = int((et - now + 86399) // 86400)
        if now > et:
            title, level, body_t = "会员已到期", "urgent", "到期日 %s，续费后立即恢复全部权益。"
        elif days <= 3:
            title, level, body_t = "会员将在 %d 天后到期" % max(days, 0), "warn", "到期日 %s，续费可无缝衔接。"
        else:
            title, level, body_t = "会员将在 %d 天后到期" % max(days, 0), "info", "到期日 %s，可提前续费。"
        res = publish(title=title, body=body_t % _fmt_date(et), level=level, target="all",
                      category="account", scope_uid=int(uid), days=30, push_key="expire",
                      action_type="route", action_value="/member",
                      meta={"kind": "expire", "expire_at": et, "bucket": "manual"},
                      created_by="admin")
        if res.get("ok"):
            n += 1
    return n
