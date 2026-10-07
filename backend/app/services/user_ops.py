# -*- coding: utf-8 -*-
"""用户运营「第二批」(2026-10-06): 分层标签(A4) / 转化漏斗(A10) / 导出扩充(A7)
/ 会员价值回顾(U8) / 公告测试发送(A5)。

为什么单独起一个模块而不是继续堆 admin.py(已 1600+ 行):
    这批都是"读多写少 + 口径易变"的运营能力, 独立后便于单测与回滚, 也避免主路由文件继续膨胀。

🔴 三条纪律(每一条都来自本仓库的真实事故):
1. **SQLite 不要再自己锁自己**: 一个函数内若已持有写连接, 绝不能再开第二个连接写同一个库
   (2026-10-06 `notice_center.mark_read` 就是这么把自己锁死的: 干等两次 busy_timeout 共 10s,
   前端表现为"点了没反应")。本模块所有查询都在**同一个连接**内完成, 需要复用时显式传 conn。
2. **不许编数字**: 没有埋点的漏斗环节(如"咨询客服")一律返回 None, 由前端标注「无数据」。
   运营要拿这个看板做决策, 填一个估算值进去比空着危害大得多。
3. **fail-soft**: 运营看板挂了不能拖垮主流程, 所有查询包异常并返回空结构。
"""
import csv
import io
import json
import logging
import sqlite3
import time

from ..core import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 基础


def _conn():
    c = sqlite3.connect(config.DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _now():
    return int(time.time())


def _bj_date(ts=None):
    """北京自然日 YYYY-MM-DD —— 必须与 activity.py 的 usage_daily.date 格式一致"""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts or _now()) + 8 * 3600))


def _fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ts or 0) + 8 * 3600)) if ts else ""


def _csv_cell(v):
    return '"%s"' % str(v if v is not None else "").replace('"', '""')


# ---------------------------------------------------------------- A4 用户分层标签

#: 自动标签 —— 键 -> (中文名, 口径说明)。口径写在这里是为了让后台能直接把解释显示给运营。
TAG_DEFS = {
    "high_active_free": ("高活跃未付费", "近 7 天活跃 ≥3 天, 且仍是免费试用"),
    "trial_expiring":   ("即将到期", "会员剩余 ≤7 天(含已过期)"),
    "quota_blocked":    ("常撞免费墙", "近 7 天被配额拦截 ≥3 次"),
    "checkin_fan":      ("连续签到", "连续签到 ≥7 天"),
    "auction_only":     ("只用竞价", "近 30 天用过竞价异动但没用过 AI 选股"),
    "silent_7d":        ("沉默 7 天", "近 7 天无任何功能使用"),
    "new_3d":           ("新用户", "注册 ≤3 天"),
    "renewed":          ("已续费", "被延长过 ≥2 次到期日"),
}


def init_tables(conn=None):
    """建 user_tags 表。在 database.init_db() 里调用一次; 幂等。"""
    own = conn is None
    c = conn or _conn()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_tags (
                uid        INTEGER NOT NULL,
                tag        TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'auto',   -- auto=系统计算 / manual=运营手动
                created_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (uid, tag)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_user_tags_tag ON user_tags(tag)")
        if own:
            c.commit()
    except Exception as e:
        log.warning("user_tags 建表失败 err=%s", e)
    finally:
        if own:
            c.close()


def _streaks(c, since_date):
    """(同连接) 各用户从今天(或昨天)往前数的连续签到天数。

    🔴 连续口径: 允许"今天还没签" —— 若昨天断了就从昨天起算, 否则从今天起算。
       这样昨天签了、今天还没签的人不会被误判成"断签"。
    """
    rows = c.execute(
        "SELECT uid, date FROM user_checkin WHERE date>=? ORDER BY uid", (since_date,)).fetchall()
    by_uid = {}
    for r in rows:
        by_uid.setdefault(int(r["uid"]), set()).add(str(r["date"]))
    today = _bj_date()
    yday = _bj_date(_now() - 86400)
    out = {}
    for uid, days in by_uid.items():
        anchor = today if today in days else (yday if yday in days else None)
        n = 0
        if anchor:
            import datetime as _dt
            d = _dt.date.fromisoformat(anchor)
            while d.isoformat() in days:
                n += 1
                d -= _dt.timedelta(days=1)
        out[uid] = n
    return out


def refresh_auto_tags(conn=None):
    """重算全员自动标签。

    🔴 只覆盖 source='auto' 的行 —— 运营手动打的标签(source='manual')绝不能被冲掉。
    """
    own = conn is None
    c = conn or _conn()
    try:
        now = _now()
        d7 = _bj_date(now - 6 * 86400)
        d30 = _bj_date(now - 29 * 86400)

        # 近 7 天活跃天数
        act = {int(r["uid"]): int(r["n"] or 0) for r in c.execute(
            "SELECT uid, COUNT(DISTINCT date) n FROM usage_daily WHERE date>=? GROUP BY uid", (d7,))}
        # 近 7 天被拦截次数
        blk = {int(r["uid"]): int(r["n"] or 0) for r in c.execute(
            "SELECT uid, SUM(blocked_count) n FROM usage_daily WHERE date>=? "
            "GROUP BY uid HAVING SUM(blocked_count)>0", (d7,))}
        # 近 30 天功能使用
        feat = {}
        for r in c.execute(
                "SELECT uid, feature, SUM(count) n FROM usage_daily WHERE date>=? "
                "GROUP BY uid, feature", (d30,)):
            feat.setdefault(int(r["uid"]), {})[str(r["feature"])] = int(r["n"] or 0)
        # 续费次数(管理员延长过几次到期日)
        ext = {int(r["uid"]): int(r["n"] or 0) for r in c.execute(
            "SELECT target_uid uid, COUNT(*) n FROM admin_audit "
            "WHERE action='set_expire' AND target_uid IS NOT NULL GROUP BY target_uid")}
        # 签到连续
        streaks = _streaks(c, _bj_date(now - 60 * 86400))

        rows = c.execute(
            "SELECT id, COALESCE(member_level,0) level, COALESCE(expire_at,0) expire_at, "
            "COALESCE(created_at,0) created_at FROM users").fetchall()

        todo = []
        for u in rows:
            uid = int(u["id"])
            lvl = int(u["level"])
            et = int(u["expire_at"] or 0)
            tags = []
            a7 = act.get(uid, 0)
            f = feat.get(uid, {})
            if a7 >= 3 and lvl == 0:
                tags.append("high_active_free")
            if lvl > 0 and et > 0:
                left = (et - now) / 86400.0
                if left <= 7:                      # 含已过期(负数)
                    tags.append("trial_expiring")
            if blk.get(uid, 0) >= 3:
                tags.append("quota_blocked")
            if streaks.get(uid, 0) >= 7:
                tags.append("checkin_fan")
            if f.get("auction", 0) > 0 and f.get("aipick", 0) == 0:
                tags.append("auction_only")
            if a7 == 0 and (now - int(u["created_at"] or 0)) > 7 * 86400:
                tags.append("silent_7d")
            if (now - int(u["created_at"] or 0)) <= 3 * 86400:
                tags.append("new_3d")
            if ext.get(uid, 0) >= 2:
                tags.append("renewed")
            for t in tags:
                todo.append((uid, t, "auto", now))

        c.execute("DELETE FROM user_tags WHERE source='auto'")
        if todo:
            c.executemany(
                "INSERT OR REPLACE INTO user_tags (uid, tag, source, created_at) VALUES (?,?,?,?)",
                todo)
        if own:
            c.commit()
        return len(todo)
    except Exception as e:
        log.warning("自动标签重算失败 err=%s", e)
        return 0
    finally:
        if own:
            c.close()


def tag_summary():
    """后台用: 每个标签的人数(自动+手动合计) + 口径说明"""
    try:
        c = _conn()
        rows = c.execute("SELECT tag, COUNT(*) n FROM user_tags GROUP BY tag ORDER BY n DESC").fetchall()
        c.close()
    except Exception as e:
        log.warning("标签汇总失败 err=%s", e)
        return []
    out = []
    for r in rows:
        k = str(r["tag"])
        label, desc = TAG_DEFS.get(k, (k, "手动标签"))
        out.append({"tag": k, "label": label, "desc": desc, "count": int(r["n"] or 0)})
    # 已定义但当前无人的标签也要列出来, 否则运营以为标签不存在
    have = {o["tag"] for o in out}
    for k, (label, desc) in TAG_DEFS.items():
        if k not in have:
            out.append({"tag": k, "label": label, "desc": desc, "count": 0})
    return out


def tags_of(uid):
    try:
        c = _conn()
        rows = c.execute("SELECT tag, source FROM user_tags WHERE uid=? ORDER BY tag", (int(uid),)).fetchall()
        c.close()
        return [{"tag": str(r["tag"]), "source": str(r["source"]),
                 "label": TAG_DEFS.get(str(r["tag"]), (str(r["tag"]), ""))[0]} for r in rows]
    except Exception as e:
        log.warning("读取用户标签失败 uid=%s err=%s", uid, e)
        return []


def attach_tags(rows):
    """给一批用户行(dict 列表)附上 tags 字段 —— 后台列表一次批量查, 避免逐人查库。"""
    if not rows:
        return rows
    try:
        c = _conn()
        ids = [int(r.get("id")) for r in rows if r.get("id")]
        mp = {}
        if ids:
            ph = ",".join("?" * len(ids))
            for r in c.execute(
                    "SELECT uid, tag, source FROM user_tags WHERE uid IN (%s) ORDER BY tag" % ph, ids):
                mp.setdefault(int(r["uid"]), []).append(
                    {"tag": str(r["tag"]), "source": str(r["source"]),
                     "label": TAG_DEFS.get(str(r["tag"]), (str(r["tag"]), ""))[0]})
        c.close()
    except Exception as e:
        log.warning("批量取标签失败 err=%s", e)
        return rows
    for r in rows:
        r["tags"] = mp.get(int(r.get("id") or 0), [])
    return rows


def set_manual_tags(uid, tags):
    """手动标签**全量替换**(传空数组=清空)。自动标签不动。"""
    try:
        c = _conn()
        c.execute("DELETE FROM user_tags WHERE uid=? AND source='manual'", (int(uid),))
        now = _now()
        c.executemany(
            "INSERT OR REPLACE INTO user_tags (uid, tag, source, created_at) VALUES (?,?,'manual',?)",
            [(int(uid), str(t), now) for t in tags if str(t).strip()])
        c.commit()
        c.close()
        return True
    except Exception as e:
        log.warning("设置手动标签失败 uid=%s err=%s", uid, e)
        return False


def uids_by_tag(tag):
    """标签 → uid 列表(供公告定向用)"""
    try:
        c = _conn()
        rows = c.execute("SELECT uid FROM user_tags WHERE tag=?", (str(tag),)).fetchall()
        c.close()
        return [int(r["uid"]) for r in rows]
    except Exception as e:
        log.warning("按标签查人失败 tag=%s err=%s", tag, e)
        return []


# ---------------------------------------------------------------- A10 转化漏斗


def funnel(days=30):
    """注册 → 激活(用过任一功能) → 撞免费墙 → 付费 → 续费, 各阶段人数与转化率。

    🔴 「咨询客服」这一环没有埋点(客服是微信人工), 返回 None 让前端显示「无数据」——
        宁可空着也不编: 这个看板是给运营做决策用的。
    """
    empty = {"stages": [], "consulted": None, "days": int(days)}
    try:
        c = _conn()
        d0 = _bj_date(_now() - (max(1, int(days)) - 1) * 86400)
        registered = c.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_admin,0)=0").fetchone()[0]
        activated = c.execute(
            "SELECT COUNT(DISTINCT uid) FROM usage_daily WHERE date>=?", (d0,)).fetchone()[0]
        blocked = c.execute(
            "SELECT COUNT(DISTINCT uid) FROM usage_daily WHERE date>=? AND blocked_count>0",
            (d0,)).fetchone()[0]
        paid = c.execute(
            "SELECT COUNT(*) FROM users WHERE COALESCE(member_level,0)>0 "
            "AND COALESCE(is_admin,0)=0").fetchone()[0]
        renewed = c.execute(
            "SELECT COUNT(*) FROM (SELECT target_uid FROM admin_audit "
            "WHERE action='set_expire' AND target_uid IS NOT NULL "
            "GROUP BY target_uid HAVING COUNT(*)>=2)").fetchone()[0]
        c.close()
    except Exception as e:
        log.warning("转化漏斗统计失败 err=%s", e)
        return empty

    def _st(key, label, n, base):
        return {"key": key, "label": label, "count": int(n),
                "rate": round(float(n) / base * 100, 1) if base else 0.0}

    stages = [
        _st("registered", "注册用户", registered, registered),
        _st("activated", "用过核心功能", activated, registered),
        _st("blocked", "撞到免费墙", blocked, registered),
        _st("paid", "付费会员", paid, registered),
        _st("renewed", "续费过", renewed, paid or registered),
    ]
    return {"stages": stages, "consulted": None, "days": int(days)}


# ---------------------------------------------------------------- A7 导出扩充


def _user_rows(c, keyword="", member_tab="all", tag=""):
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
    if tag:
        cond += " AND EXISTS (SELECT 1 FROM user_tags t WHERE t.uid=u.id AND t.tag=?)"
        params.append(str(tag))
    return c.execute(
        "SELECT u.id, u.username, COALESCE(u.phone,'') phone, COALESCE(u.wx_name,'') wx_name, "
        "COALESCE(u.member_level,0) member_level, COALESCE(u.expire_at,0) expire_at, "
        "u.created_at, COALESCE(u.register_ip,'') register_ip, "
        "COALESCE(u.invited_by,0) invited_by, u.invite_code, "
        "(SELECT COUNT(*) FROM users x WHERE x.invited_by=u.id) invited_count, "
        "COALESCE(u.remark,'') remark, COALESCE(u.pay_remark,'') pay_remark "
        "FROM users u WHERE " + cond + " ORDER BY u.id DESC", params).fetchall()


def export_users(keyword="", member_tab="all", tag="", days=30):
    """用户导出(维度扩充版): 在原字段基础上补 最后登录 / 近30天活跃天数 / 各功能次数 /
    当前配额消耗 / 邀请人数 / 签到连续天数 / 标签。

    ★ 客服拿这份 CSV 当工作清单用, 字段越全沟通越准(A7 的原始动机)。
    """
    try:
        c = _conn()
        d0 = _bj_date(_now() - (max(1, int(days)) - 1) * 86400)
        rows = _user_rows(c, keyword, member_tab, tag)
        act = {int(r["uid"]): int(r["n"] or 0) for r in c.execute(
            "SELECT uid, COUNT(DISTINCT date) n FROM usage_daily WHERE date>=? GROUP BY uid", (d0,))}
        feat = {}
        for r in c.execute(
                "SELECT uid, feature, SUM(count) n FROM usage_daily WHERE date>=? "
                "GROUP BY uid, feature", (d0,)):
            feat.setdefault(int(r["uid"]), {})[str(r["feature"])] = int(r["n"] or 0)
        # 最后登录: 取该用户最近一条成功登录(来自登录流水表, 没有就空)
        last = {}
        try:
            for r in c.execute(
                    "SELECT uid, MAX(created_at) t FROM login_log WHERE result='success' GROUP BY uid"):
                last[int(r["uid"])] = int(r["t"] or 0)
        except Exception:
            pass        # 登录流水表不存在就留空, 不影响其他字段
        tags = {}
        for r in c.execute("SELECT uid, tag FROM user_tags ORDER BY tag"):
            tags.setdefault(int(r["uid"]), []).append(str(r["tag"]))
        blk = {int(r["uid"]): int(r["n"] or 0) for r in c.execute(
            "SELECT uid, SUM(blocked_count) n FROM usage_daily WHERE date>=? "
            "GROUP BY uid HAVING SUM(blocked_count)>0", (d0,))}
        streaks = _streaks(c, _bj_date(_now() - 60 * 86400))
        c.close()
    except Exception as e:
        log.warning("用户导出失败 err=%s", e)
        return ""

    now = _now()
    labels = {0: "免费试用", 1: "付费会员", 2: "VIP老师"}
    head = ["ID", "用户名", "手机号", "微信名", "会员等级", "到期时间", "剩余天数", "注册时间",
            "最后登录", "注册IP", "邀请人ID", "邀请码", "已邀请人数",
            "近%d天活跃天数" % int(days), "选股次数", "AI选股次数", "竞价次数",
            "被拦截次数", "连续签到", "标签", "备注", "付款备注"]
    lines = [",".join(head)]
    for r in rows:
        uid = int(r["id"])
        et = int(r["expire_at"] or 0)
        f = feat.get(uid, {})
        blocked = blk.get(uid, 0)
        cells = [
            uid, r["username"], r["phone"], r["wx_name"],
            labels.get(int(r["member_level"] or 0), ""),
            "永久" if not et else _fmt_ts(et)[:10],
            "永久" if not et else str(int((et - now) // 86400)),
            _fmt_ts(r["created_at"]), _fmt_ts(last.get(uid, 0)),
            r["register_ip"], r["invited_by"] or "", r["invite_code"] or "",
            r["invited_count"], act.get(uid, 0),
            f.get("picker", 0), f.get("aipick", 0), f.get("auction", 0),
            blocked, streaks.get(uid, 0),
            "|".join(tags.get(uid, [])), r["remark"], r["pay_remark"],
        ]
        lines.append(",".join(_csv_cell(x) for x in cells))
    return "\ufeff" + "\n".join(lines)


def export_churn(days=60):
    """流失用户导出: 到期后 N 天内未再登录/未再使用, 且当前非付费。

    口径(写清楚, 免得运营误解): 到期日落在 [now-N 天, now] 区间内 + 到期后至今无使用记录。
    """
    try:
        c = _conn()
        now = _now()
        since = now - max(1, int(days)) * 86400
        rows = c.execute(
            "SELECT u.id, u.username, COALESCE(u.phone,'') phone, "
            "COALESCE(u.wx_name,'') wx_name, COALESCE(u.expire_at,0) expire_at, "
            "u.created_at, (SELECT COUNT(*) FROM users x WHERE x.invited_by=u.id) invited_count "
            "FROM users u WHERE COALESCE(u.is_admin,0)=0 AND COALESCE(u.expire_at,0)>0 "
            "AND COALESCE(u.expire_at,0)<=? AND COALESCE(u.expire_at,0)>=? "
            "AND COALESCE(u.member_level,0)=0 ORDER BY u.expire_at DESC", (now, since)).fetchall()
        used = {int(r["uid"]) for r in c.execute(
            "SELECT uid FROM usage_daily WHERE date>=?", (_bj_date(since),))}
        c.close()
    except Exception as e:
        log.warning("流失用户导出失败 err=%s", e)
        return ""

    head = ["ID", "用户名", "手机号", "微信名", "到期时间", "已过期天数", "注册时间",
            "到期后是否还有使用", "已邀请人数"]
    lines = [",".join(head)]
    for r in rows:
        uid = int(r["id"])
        et = int(r["expire_at"] or 0)
        lines.append(",".join(_csv_cell(x) for x in [
            uid, r["username"], r["phone"], r["wx_name"], _fmt_ts(et),
            int((_now() - et) // 86400), _fmt_ts(r["created_at"]),
            "是" if uid in used else "否", r["invited_count"],
        ]))
    return "\ufeff" + "\n".join(lines)


def export_reach(nkey, limit=5000):
    """消息触达明细导出: 某条公告每个人 是否送达/已读/点击。"""
    try:
        c = _conn()
        row = c.execute("SELECT nkey, title, target_count, push_sent, push_failed, read_count, "
                        "click_count FROM notice_stats WHERE nkey=?", (str(nkey),)).fetchone()
        if not row:
            c.close()
            return ""
        # 已读/点击的人(只有落过回执的才查得到; 未读的人不在表里 ⇒ 输出"未读")
        # 🔴 回执表的时间列叫 read_at(不是 ts) —— 写错过一次, SQLite 会直接报
        #    "no such column: ts", 而本函数 fail-soft 把它吞成空 CSV ⇒ 前端只看到 404,
        #    看不出是列名写错。改这里时请以 PRAGMA table_info(notice_reads) 为准。
        reads = {int(r["user_id"]): int(r["read_at"] or 0) for r in c.execute(
            "SELECT user_id, read_at FROM notice_reads WHERE nkey=? AND deleted=0", (str(nkey),))}
        clicks = {}
        try:
            clicks = {int(r["uid"]): int(r["ts"] or 0) for r in c.execute(
                "SELECT uid, ts FROM notice_clicks WHERE nkey=?", (str(nkey),))}
        except Exception:
            pass
        users = {int(r["id"]): r["username"] for r in c.execute(
            "SELECT id, username FROM users").fetchall()}
        c.close()
    except Exception as e:
        log.warning("触达明细导出失败 nkey=%s err=%s", nkey, e)
        return ""

    lines = [",".join(["#公告", str(row["title"] or ""), "定向人数", str(row["target_count"] or 0),
                       "推送成功", str(row["push_sent"] or 0), "推送失败", str(row["push_failed"] or 0),
                       "已读", str(row["read_count"] or 0), "点击", str(row["click_count"] or 0)]),
             ",".join(["用户ID", "用户名", "是否已读", "已读时间", "是否点击", "点击时间"])]
    for uid, name in sorted(users.items()):
        rt = reads.get(uid, 0)
        ct = clicks.get(uid, 0)
        lines.append(",".join(_csv_cell(x) for x in [
            uid, name, "是" if rt else "否", _fmt_ts(rt), "是" if ct else "否", _fmt_ts(ct)]))
    return "\ufeff" + "\n".join(lines)


# ---------------------------------------------------------------- U8 会员价值回顾


def value_review(uid, days=30):
    """会员价值回顾: **只用真实数据**(usage_daily + 签到 + 到期日)。

    🔴 刻意**不**提供「选出多少只涨停」「帮你赚了多少」这类数字 —— 战绩无法归因到个人,
       拿它做续费话术就是编(2026-10-06 与主人确认的口径: 只讲"你实际用了多少")。
    """
    out = {"ok": False, "days": int(days), "days_used": 0, "actions": 0,
           "by_feature": [], "top_feature": "", "blocked": 0, "streak": 0,
           "member_days_left": None, "since": ""}
    try:
        c = _conn()
        d0 = _bj_date(_now() - (max(1, int(days)) - 1) * 86400)
        rows = c.execute(
            "SELECT feature, SUM(count) n, SUM(blocked_count) b FROM usage_daily "
            "WHERE uid=? AND date>=? GROUP BY feature", (int(uid), d0)).fetchall()
        days_used = c.execute(
            "SELECT COUNT(DISTINCT date) n FROM usage_daily WHERE uid=? AND date>=?",
            (int(uid), d0)).fetchone()[0]
        u = c.execute("SELECT COALESCE(member_level,0) level, COALESCE(expire_at,0) expire_at "
                      "FROM users WHERE id=?", (int(uid),)).fetchone()
        streaks = _streaks(c, _bj_date(_now() - 60 * 86400))
        c.close()
    except Exception as e:
        log.warning("价值回顾失败 uid=%s err=%s", uid, e)
        return out

    try:
        from .activity import FEATURES
    except Exception:
        FEATURES = {}

    by_feature, total, blocked = [], 0, 0
    for r in rows:
        f = str(r["feature"])
        n = int(r["n"] or 0)
        b = int(r["b"] or 0)
        total += n
        blocked += b
        if n:
            by_feature.append({"feature": f, "label": FEATURES.get(f, f), "count": n})
    by_feature.sort(key=lambda x: -x["count"])

    left = None
    if u and int(u["level"] or 0) > 0:
        et = int(u["expire_at"] or 0)
        left = None if not et else int((et - _now()) // 86400)

    out.update({"ok": True, "days_used": int(days_used or 0), "actions": total,
                "by_feature": by_feature,
                "top_feature": (by_feature[0]["label"] if by_feature else ""),
                "blocked": blocked, "streak": int(streaks.get(int(uid), 0)),
                "member_days_left": left, "since": d0})
    return out


# ---------------------------------------------------------------- A5 公告测试发送


def send_test_notice(admin_uid, title, body, level="info", category="system",
                     action_type="", action_value="", push=False):
    """「仅自己可见」的测试发送 —— 发布前的最后一道防误发闸门。

    · 走 scope_uid=<管理员自己> ⇒ fetch() 里 `scope_uid=0 OR scope_uid=uid`, 只有本人能看到
    · 默认**不发推送**(push=False): 测试的是"消息长什么样", 不该顺手把管理员自己推一遍
    · meta.test=1 便于后台识别与清理
    🔴 与正式发布的差别只在于定向人群, 其余字段完全一致 —— 否则测出来不是真的。
    """
    from . import notice_center as nc
    return nc.publish(title=title, body=body, level=level, target="all", category=category,
                      days=1, scope_uid=int(admin_uid),
                      push_key=("system" if push else ""),
                      action_type=action_type, action_value=action_value,
                      meta={"test": 1}, status="sent", created_by="admin-test",
                      skip_push=not push)


# ================= A9 运营日历(2026-10-07 v4.12.8) =================
def _bj_today():
    """北京时间今天。服务器时区是 UTC, 全站统一 +8h 口径(别用 localtime)。"""
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(time.time() + 8 * 3600).date()


def _bj_date(ts):
    """时间戳 → 北京日期串(Y-m-d); 0/空 → ''"""
    import datetime as _dt
    try:
        return _dt.datetime.utcfromtimestamp(int(ts or 0) + 8 * 3600).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _bj_date_start(d):
    """北京日期 d 的 00:00 对应 unix 时间戳(用于范围查询)。

    🔴 必须走 timegm(按 UTC 解释) 而不是 datetime.timestamp() —— 后者按**本机时区**解释同一个
       朴素 datetime: 服务器 UTC 下算出的是正确值, 换到 CST 的开发机上会整整差 8 小时
       ⇒ 同一个 DAY 在两台机器查出来的用户不一样。这种 bug 在生产上不报错, 只是静静地漏人。
    """
    import calendar as _cal
    import datetime as _dt
    return int(_cal.timegm(_dt.datetime(d.year, d.month, d.day).timetuple())) - 8 * 3600


def calendar(days=30):
    """按**北京日期**聚合未来 N 天的运营日程 —— 让运营在一个格子里看见"那天有什么事"。

    三类事件, 全部来自**既有表**, 不新增埋点、不新建表格:
      ① 公告上线/下线(notices.start_ts / end_ts): 运营自己排的, 最容易撞车(多条挤同一天)
      ② 会员到期(users.expire_at): 哪天多少人到期 —— 催续费排班就看这个
      ③ 新注册(users.created_at): 回看用, 判断"那天是不是做过推广/出现注册潮"

    🔴 刻意不把"使用量/活跃"这类连续指标放进日历: 它们每天都有值, 摊到格子里只会变成
       一片均匀的噪声, 反而淹没真正需要排班处理的离散事件(到期、上线、下线)。

    🔴 一律 fail-soft: 日历是锦上添花的视图, 查不动就返回空, 不该拖垮整个后台首页。
    """
    import datetime as _dt
    n = max(1, min(int(days or 30), 90))
    today = _bj_today()
    span = [today + _dt.timedelta(days=i) for i in range(n)]
    lo = _bj_date_start(today - _dt.timedelta(days=1))   # 含昨天, 便于看到"刚上线没多久的"
    hi = _bj_date_start(today + _dt.timedelta(days=n + 1))
    grid = {d.isoformat(): {"date": d.isoformat(), "notices": [], "expiring": 0,
                            "expiring_free": 0, "new_users": 0} for d in span}
    try:
        c = _conn()
        try:
            # 🔴 排除 created_by='system' 的**系统自动生成**通知(签到提醒/账户变动, category
            #    = system|account)：它们是每天一条的流水线, 测试机实测 349 条里占 100% ——
            #    放进日历会把"运营自己排的事"彻底淹没。日历要回答的是「要不要今天排班」,
            #    自动消息不需要排班 ⇒ 只收**人工发布**的公告(运营台即使不发也会自动存在)。
            rows = c.execute(
                "SELECT id, title, level, target, status, start_ts, end_ts, off_at FROM notices "
                "WHERE off_at=0 AND IFNULL(created_by,'') <> 'system' "
                "AND (start_ts BETWEEN ? AND ? OR end_ts BETWEEN ? AND ?)",
                (lo, hi, lo, hi)).fetchall()
            for r in rows:
                base = {"id": int(r["id"]), "title": r["title"] or "", "level": r["level"] or "info",
                        "target": r["target"] or "all", "status": r["status"] or ""}
                for field, ts, kind in (("start_ts", r["start_ts"], "上线"),
                                        ("end_ts", r["end_ts"], "下线")):
                    k = _bj_date(ts)
                    if k in grid and int(ts or 0) > 0:
                        grid[k]["notices"].append(dict(base, kind=kind))
            for r in c.execute(
                    "SELECT expire_at, member_level FROM users WHERE expire_at>0 "
                    "AND expire_at BETWEEN ? AND ?", (lo, hi)).fetchall():
                k = _bj_date(r["expire_at"])
                if k not in grid:
                    continue
                grid[k]["expiring" if int(r["member_level"] or 0) > 0 else "expiring_free"] += 1
            for r in c.execute(
                    "SELECT created_at FROM users WHERE created_at BETWEEN ? AND ?",
                    (lo, hi)).fetchall():
                k = _bj_date(r["created_at"])
                if k in grid:
                    grid[k]["new_users"] += 1
        finally:
            c.close()
    except Exception as e:
        log.warning("运营日历聚合失败 err=%s", e)
        return {"days": [], "total": {"notices": 0, "expiring": 0, "new_users": 0}}
    out = [grid[d.isoformat()] for d in span]
    return {"days": out, "total": {
        "notices": sum(len(d["notices"]) for d in out),
        "expiring": sum(d["expiring"] for d in out),
        "new_users": sum(d["new_users"] for d in out)}}
