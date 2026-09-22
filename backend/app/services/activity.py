# -*- coding: utf-8 -*-
"""
用户行为记录: 登录记录 + 功能使用记录(2026-09-22, v4.11.35)
==============================================================
需求(主人 2026-09-22): 管理员原先看不到「谁在什么时候登录过」「谁用了哪些功能」。
此前唯一的线索是系统日志(journald): 文本、会轮转、后台查不了。

★ 计数口径(主人拍板, 不要改):
    **用户主动操作一次 = 1 次** —— 例如「选股点一次『应用』记 1 次」。
    由前端在**动作回调**里显式上报(`POST /api/activity/track`), 不按接口请求数计。
    这样 30s 轮询、页面并发加载都不会污染统计(此前配额计数受此困扰)。

★ 两条铁律:
  1. **写入绝不抛异常**: 埋点在业务主路径上, 记录失败只能吞掉+打日志,
     绝不能因为「记日志」把用户的选股请求搞成 500。
  2. **聚合不存明细**: 见 db/database.py 里 usage_daily 的注释(18,798 请求/日,
     逐条存 = 570 万行/年, SQLite 单库扛不住)。

★ 与配额计数(kv_cache 的 quota:*)的关系: 二者互补, 不可互相替代。
    - quota:* 只有免费用户有、TTL 25 小时即删、只有 3 个功能 → 用途仅「限流」;
    - usage_daily 覆盖全部用户(含会员/管理员)、长期保留、8 个功能 → 用途「审计/运营」。
"""
import time

from ..core import config, logger
from ..db import database

log = logger.get_logger(__name__)

# 功能键 -> 中文名(与前端 router 的页面一一对应, 见 docs/admin-user-activity-log-plan.md §2.2)
FEATURES = {
    "picker": "选股",
    "aipick": "AI 选股",
    "auction": "竞价异动",
    "concept": "题材异动",
    "history": "历史回看",
    "ladder": "涨停梯队",
    "market": "市场雷达",
    "member": "会员中心",
}

# 登录结果 -> 中文(前端也直接用这套映射, 保证两端一致)
LOGIN_RESULT_LABEL = {
    "success": "登录成功",
    "fail": "登录失败",
    "reset": "重置密码",
    "kicked": "被顶出",
    "logout": "主动退出",
}

# 保留期(天): 登录明细按行增长(20~100 行/天), 使用聚合行数可控
RETENTION_LOGIN_DAYS = 180
RETENTION_USAGE_DAYS = 730

_MAX_UA = 200      # UA 截断长度(够区分浏览器/设备, 不至于把表撑大)
_MAX_LOGIN_TRY = 120


def bj_date(ts=None):
    """北京日期字符串 YYYY-MM-DD(服务器走 UTC, 与配额口径一致)"""
    return time.strftime("%Y-%m-%d", time.gmtime((ts or time.time()) + 8 * 3600))


def _conn():
    """带 Row 工厂的连接(本模块全部返回 dict, 便于直接 json 化)。
    ★ 不能用 database.get_conn(): 它没设 row_factory, 返回 tuple。"""
    import sqlite3
    conn = sqlite3.connect(config.DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- 写入

def record_login(uid=0, login_try="", result="success", ip="", ua="", remember=False):
    """写一条登录记录. 失败只打日志(见模块 docstring 铁律 1)。
    result: success / fail / reset / kicked / logout"""
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO login_log (uid, login_try, result, ip, ua, remember, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(uid or 0), str(login_try or "")[:_MAX_LOGIN_TRY], str(result or "success"),
                 str(ip or ""), str(ua or "")[:_MAX_UA], 1 if remember else 0, int(time.time())))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning("登录记录写入失败 uid=%s result=%s err=%s", uid, result, e)


def bump_usage(uid, feature, blocked=False, ts=None):
    """用户主动操作一次 → 计数 +1。blocked=True 计入「被拦截」而非「放行」。

    ★ 幂等性说明: 本函数**按调用次数累加**, 不做去重 —— 因为口径就是
      「点一次记一次」。要防止的是**前端误重放**(同一手势触发两次), 由
      前端在点击回调里只调一次来保证; 服务端不擅自合并, 否则「连点两次应用」
      就只记 1 次, 与口径不符。
    """
    f = str(feature or "").strip()
    if f not in FEATURES:
        return False
    try:
        t = int(ts or time.time())
        d = bj_date(t)
        inc = 0 if blocked else 1
        blk = 1 if blocked else 0
        conn = _conn()
        try:
            # 🔴 不能用 UPSERT(`ON CONFLICT ... DO UPDATE`): 测试机是 **CentOS 7 + SQLite 3.7.17**,
            #    该语法要 SQLite ≥ 3.24 —— 本机(3.53)/生产(3.26)能跑, 测试机直接
            #    `near "ON": syntax error`(2026-09-22 实测踩坑: 本机单测全绿, 上测试机才炸)。
            #    项目既有约定见 services/fetcher.py / mv_cache.py / settings.py: 统一用
            #    「INSERT OR IGNORE 兜底建行 + UPDATE 累加」两步, 在老 SQLite 上等价。
            conn.execute(
                "INSERT OR IGNORE INTO usage_daily "
                "(uid, date, feature, count, blocked_count, first_ts, last_ts) "
                "VALUES (?,?,?,0,0,0,0)", (int(uid or 0), d, f))
            conn.execute(
                "UPDATE usage_daily SET "
                "  count = count + ?, "
                "  blocked_count = blocked_count + ?, "
                "  first_ts = CASE WHEN first_ts = 0 THEN ? ELSE MIN(first_ts, ?) END, "
                "  last_ts = MAX(last_ts, ?) "
                "WHERE uid = ? AND date = ? AND feature = ?",
                (inc, blk, t, t, t, int(uid or 0), d, f))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        log.warning("使用计数写入失败 uid=%s feature=%s err=%s", uid, feature, e)
        return False


def upsert_absolute(rows):
    """回溯专用: 用**绝对值**写入(不是累加), 且重跑幂等(取 MAX)。

    rows: [(uid, date, feature, count, first_ts, last_ts), ...]
    ★ 为什么不能复用 bump_usage: 回溯是「一次性把历史日志折算成当日总数」,
      同一个日期可能因为日志分片被跑两遍; 累加会翻倍, 取 MAX 则重跑安全。
    ★ 只写 count, blocked_count 不动(历史上没有拦截信息, 保持 0)。
    """
    if not rows:
        return 0
    n = 0
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            for uid, d, f, c, first_ts, last_ts in rows:
                if f not in FEATURES or int(c or 0) <= 0:
                    continue
                u, dd, ff = int(uid), str(d), str(f)
                ft, lt = int(first_ts or 0), int(last_ts or 0)
                # 同上: 老 SQLite 没有 UPSERT, 走 INSERT OR IGNORE + UPDATE 两步
                cur.execute(
                    "INSERT OR IGNORE INTO usage_daily "
                    "(uid, date, feature, count, blocked_count, first_ts, last_ts) "
                    "VALUES (?,?,?,0,0,0,0)", (u, dd, ff))
                cur.execute(
                    "UPDATE usage_daily SET "
                    "  count = MAX(count, ?), "
                    "  first_ts = CASE WHEN first_ts = 0 THEN ? ELSE MIN(first_ts, ?) END, "
                    "  last_ts = MAX(last_ts, ?) "
                    "WHERE uid = ? AND date = ? AND feature = ?",
                    (int(c), ft, ft, lt, u, dd, ff))
                n += 1
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning("回溯写入失败 rows=%d err=%s", len(rows), e)
    return n


# ---------------------------------------------------------------- 查询

def _usernames(uids):
    """uid -> username 批量映射. 失败返回空 dict, 由调用方兜底显示 uid。"""
    uids = sorted({int(u) for u in uids if u})
    if not uids:
        return {}
    try:
        conn = _conn()
        try:
            marks = ",".join("?" * len(uids))
            rows = conn.execute(
                "SELECT id, username FROM users WHERE id IN (%s)" % marks, tuple(uids)).fetchall()
        finally:
            conn.close()
        return {r["id"]: r["username"] for r in rows}
    except Exception as e:
        log.warning("用户名批量查询失败 err=%s", e)
        return {}


def _fmt_ts(ts):
    ts = int(ts or 0)
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts + 8 * 3600))


def login_history(uid, days=30, limit=100):
    """某用户的登录记录(倒序). 返回 list[dict]"""
    since = int(time.time()) - max(1, int(days)) * 86400
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT id, uid, login_try, result, ip, ua, remember, created_at "
                "FROM login_log WHERE uid=? AND created_at>=? ORDER BY id DESC LIMIT ?",
                (int(uid), since, int(limit))).fetchall()
        finally:
            conn.close()
    except Exception as e:
        log.warning("登录记录查询失败 uid=%s err=%s", uid, e)
        return []
    out = []
    for r in rows:
        d = dict(r)
        d["result_label"] = LOGIN_RESULT_LABEL.get(d.get("result"), d.get("result") or "")
        d["time"] = _fmt_ts(d.get("created_at"))
        out.append(d)
    return out


def usage_today(uid):
    """某用户今日各功能次数: {feature: {count, blocked, first_ts, last_ts}}"""
    return _usage_range(uid, 1)["today"]


def _usage_range(uid, days):
    """内部: 近 N 天使用明细 + 今日汇总"""
    d0 = bj_date()
    since_date = bj_date(time.time() - (max(1, int(days)) - 1) * 86400)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT date, feature, count, blocked_count, first_ts, last_ts "
                "FROM usage_daily WHERE uid=? AND date>=? ORDER BY date DESC, count DESC",
                (int(uid), since_date)).fetchall()
        finally:
            conn.close()
    except Exception as e:
        log.warning("使用记录查询失败 uid=%s err=%s", uid, e)
        return {"days": [], "today": {}, "dates": []}
    days_map = {}
    today = {}
    for r in rows:
        d = dict(r)
        d["feature_label"] = FEATURES.get(d["feature"], d["feature"])
        days_map.setdefault(d["date"], []).append(d)
        if d["date"] == d0:
            today[d["feature"]] = {
                "feature_label": d["feature_label"], "count": int(d["count"] or 0),
                "blocked": int(d["blocked_count"] or 0),
                "first_ts": d["first_ts"], "last_ts": d["last_ts"],
            }
    dates = []
    for d in sorted(days_map.keys(), reverse=True):
        items = days_map[d]
        dates.append({
            "date": d,
            "total": sum(int(x["count"] or 0) for x in items),
            "blocked": sum(int(x["blocked_count"] or 0) for x in items),
            "items": items,
        })
    return {"days": dates, "today": today, "dates": dates}


def usage_summary(uid, days=30):
    """用户详情用: 今日各功能 + 近 N 天逐日矩阵(行=日期, 列=功能)"""
    rng = _usage_range(uid, days)
    # 功能维度合计(近 N 天)
    feat_total = {}
    for day in rng["days"]:
        for it in day["items"]:
            f = it["feature"]
            feat_total[f] = feat_total.get(f, 0) + int(it["count"] or 0)
    matrix = []
    for day in rng["days"]:
        row = {"date": day["date"], "total": day["total"], "blocked": day["blocked"]}
        for it in day["items"]:
            row[it["feature"]] = int(it["count"] or 0)
        matrix.append(row)
    return {
        "today": rng["today"],
        "days": rng["days"][:31],
        "matrix": matrix,
        "feature_total": feat_total,
        "feature_labels": FEATURES,
        "active_days": len(rng["days"]),
        "total": sum(int(x["count"] or 0) for d in rng["days"] for x in d["items"]),
        "blocked_total": sum(int(x["blocked_count"] or 0) for d in rng["days"] for x in d["items"]),
    }


def global_login_log(days=30, result="", kw="", limit=50, offset=0):
    """管理员: 全站登录流水(可按结果筛选 + 账号关键字)"""
    since = int(time.time()) - max(1, int(days)) * 86400
    cond, params = "WHERE created_at>=?", [since]
    if result:
        cond += " AND result=?"
        params.append(str(result))
    if kw:
        cond += " AND (login_try LIKE ? OR uid IN (SELECT id FROM users WHERE username LIKE ?))"
        params += ["%" + kw + "%", "%" + kw + "%"]
    try:
        conn = _conn()
        try:
            total = conn.execute("SELECT COUNT(*) n FROM login_log " + cond, params).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM login_log " + cond + " ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)]).fetchall()
        finally:
            conn.close()
    except Exception as e:
        log.warning("登录流水查询失败 err=%s", e)
        return {"total": 0, "rows": []}
    names = _usernames([r["uid"] for r in rows])
    out = []
    for r in rows:
        d = dict(r)
        d["result_label"] = LOGIN_RESULT_LABEL.get(d.get("result"), d.get("result") or "")
        d["time"] = _fmt_ts(d.get("created_at"))
        d["username"] = names.get(d.get("uid")) or ("uid=%d" % int(d.get("uid") or 0))
        out.append(d)
    return {"total": int(total or 0), "rows": out}


def usage_rank(date=None, feature=None, limit=20):
    """管理员: 某日功能使用排行(按人). 含用户名/是否管理员/等级"""
    d = date or bj_date()
    cond, params = "WHERE date=?", [d]
    if feature:
        cond += " AND feature=?"
        params.append(str(feature))
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT uid, SUM(count) c, SUM(blocked_count) b, "
                "MIN(CASE WHEN first_ts>0 THEN first_ts END) f, MAX(last_ts) l "
                "FROM usage_daily " + cond + " GROUP BY uid ORDER BY c DESC, uid ASC LIMIT ?",
                params + [int(limit)]).fetchall()
            by_feat = {r["feature"]: int(r["c"] or 0) for r in conn.execute(
                "SELECT feature, SUM(count) c FROM usage_daily " + cond + " GROUP BY feature",
                params).fetchall()}
        finally:
            conn.close()
    except Exception as e:
        log.warning("使用排行查询失败 err=%s", e)
        return {"date": d, "rows": [], "by_feature": {}, "total": 0, "users": 0}
    uids = [r["uid"] for r in rows]
    names = _usernames(uids)
    meta = _user_flags(uids)
    out = []
    for r in rows:
        uid = int(r["uid"])
        out.append({
            "uid": uid,
            "username": names.get(uid) or ("uid=%d" % uid),
            "count": int(r["c"] or 0),
            "blocked": int(r["b"] or 0),
            "first_ts": int(r["f"] or 0),
            "last_ts": int(r["l"] or 0),
            "is_admin": meta.get(uid, {}).get("is_admin", 0),
            "member_level": meta.get(uid, {}).get("member_level", 0),
        })
    return {"date": d, "rows": out, "by_feature": by_feat,
            "total": sum(by_feat.values()), "users": len(out)}


def _user_flags(uids):
    """uid -> {is_admin, member_level}(用于排行里区分内部流量)"""
    uids = sorted({int(u) for u in uids if u})
    if not uids:
        return {}
    try:
        conn = _conn()
        try:
            marks = ",".join("?" * len(uids))
            rows = conn.execute(
                "SELECT id, COALESCE(is_admin,0) is_admin, COALESCE(member_level,0) member_level "
                "FROM users WHERE id IN (%s)" % marks, tuple(uids)).fetchall()
        finally:
            conn.close()
        return {int(r["id"]): {"is_admin": int(r["is_admin"] or 0),
                               "member_level": int(r["member_level"] or 0)} for r in rows}
    except Exception as e:
        log.warning("用户标记查询失败 err=%s", e)
        return {}


def active_trend(days=30):
    """管理员: 近 N 天活跃趋势(按日去重用户数 / 操作次数 / 登录成功次数)"""
    days = max(1, min(90, int(days)))
    today = bj_date()
    out = []
    try:
        conn = _conn()
        try:
            for i in range(days - 1, -1, -1):
                d = bj_date(time.time() - i * 86400)
                r = conn.execute(
                    "SELECT COUNT(DISTINCT uid) u, COALESCE(SUM(count),0) c, "
                    "COALESCE(SUM(blocked_count),0) b FROM usage_daily WHERE date=?", (d,)).fetchone()
                L = conn.execute(
                    "SELECT COUNT(*) n, COUNT(DISTINCT uid) u FROM login_log "
                    "WHERE result='success' AND created_at>=? AND created_at<?",
                    (_day_start_ts(d), _day_start_ts(d) + 86400)).fetchone()
                out.append({"date": d,
                            "act_users": int(r["u"] or 0), "actions": int(r["c"] or 0),
                            "blocked": int(r["b"] or 0),
                            "login_users": int(L["u"] or 0), "logins": int(L["n"] or 0)})
        finally:
            conn.close()
    except Exception as e:
        log.warning("活跃趋势查询失败 err=%s", e)
    # 汇总(近 7 天 / 近 30 天)
    def _sum(n):
        seg = out[-n:]
        return {"act_users_avg": round(sum(x["act_users"] for x in seg) / max(1, len(seg)), 1),
                "actions": sum(x["actions"] for x in seg),
                "logins": sum(x["logins"] for x in seg)}
    return {"days": out, "today": (out[-1] if out else {}), "today_date": today,
            "sum7": _sum(7), "sum30": _sum(30)}


def _day_start_ts(date_str):
    """北京日期 → 当日 00:00 的 UTC 时间戳"""
    try:
        return int(time.mktime(time.strptime(date_str, "%Y-%m-%d"))) - 8 * 3600
    except Exception:
        return 0


def login_stats(date=None):
    """管理员看板: 某日登录概况(成功人数/次数, 失败次数, 被顶出)"""
    d = date or bj_date()
    t0 = _day_start_ts(d)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT result, COUNT(*) n, COUNT(DISTINCT uid) u FROM login_log "
                "WHERE created_at>=? AND created_at<? GROUP BY result",
                (t0, t0 + 86400)).fetchall()
        finally:
            conn.close()
    except Exception as e:
        log.warning("登录统计失败 err=%s", e)
        return {"date": d, "by_result": {}, "success": 0, "success_users": 0, "fail": 0}
    by = {}
    for r in rows:
        by[r["result"]] = {"count": int(r["n"] or 0), "users": int(r["u"] or 0)}
    return {"date": d, "by_result": by,
            "success": by.get("success", {}).get("count", 0),
            "success_users": by.get("success", {}).get("users", 0),
            "fail": by.get("fail", {}).get("count", 0)}


# ---------------------------------------------------------------- 清理

def purge(retention_login_days=None, retention_usage_days=None):
    """清理过期记录(挂 kx-worker 每天 03:30). 返回删除行数 dict。"""
    rl = int(retention_login_days or RETENTION_LOGIN_DAYS)
    ru = int(retention_usage_days or RETENTION_USAGE_DAYS)
    out = {"login": 0, "usage": 0}
    try:
        conn = _conn()
        try:
            cur = conn.execute("DELETE FROM login_log WHERE created_at < ?",
                               (int(time.time()) - rl * 86400,))
            out["login"] = cur.rowcount or 0
            cur = conn.execute("DELETE FROM usage_daily WHERE date < ?", (bj_date(time.time() - ru * 86400),))
            out["usage"] = cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning("行为记录清理失败 err=%s", e)
    if out["login"] or out["usage"]:
        log.info("行为记录清理完成 login=%d usage=%d", out["login"], out["usage"])
    return out
