# -*- coding: utf-8 -*-
"""用户行为记录(登录记录 + 功能使用记录) —— 2026-09-22, v4.11.35

背景: 管理员原先看不到「谁在什么时候登录过」「谁用了哪些功能」。
      此前唯一线索是系统日志(journald), 文本、会轮转、后台查不了。
本文件给新增的 `services/activity.py` + `api/activity.py` + 4 个 admin 端点
上锁。三条最要紧的口径:

  1. **计数口径 = 用户主动操作一次 = 1 次**(主人 2026-09-22 拍板)。
     ⚠️ 意味着 `bump_usage` **必须按调用次数累加、不去重** —— 若照搬配额那套
     "10s 内重复请求只算一次"的去重逻辑, 用户连点两次「应用」就只记 1 次,
     与口径直接冲突。`test_click_counted_per_call` 就是钉这一条。
  2. **日期口径 = 北京日期**(与配额 key 一致)。`test_bump_usage_beijing_date_boundary`
     用 UTC 16:00 这条边界钉死 —— 写成 UTC 日期就会把 0 点后 8 小时的操作
     错记到前一天。🔬 这也是本文件的**变异靶点**(见文件末尾说明)。
  3. **会员/管理员也计数**(与配额相反: 配额对会员直接放行不计数)。
     `test_track_counts_for_member_and_admin` 钉这一条。

另: 所有写入路径**必须不抛异常** —— 埋点在业务主链路上, 不能因为"记日志失败"
把用户的选股请求搞成 500(`test_writes_never_raise`)。
"""
import calendar
import os
import sqlite3
import time

from app.services import activity as act

ADMIN_HDRS = {}


def _db():
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def _make_admin(u):
    conn = _db()
    conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["uid"],))
    conn.commit()
    conn.close()


def _admin_token(create_user_token):
    """每个用例自己建一个管理员(不依赖其他测试文件里的 session 夹具)"""
    u = create_user_token()
    _make_admin(u)
    return u


# ==================== 1. 登录记录 ====================

def test_record_login_success_and_fail(client, create_user_token):
    """成功与失败都要落库; 失败时若不认识账号则 uid=0(靠 login_try 排查撞库)"""
    u = create_user_token()
    act.record_login(uid=u["uid"], login_try=u["username"], result="success",
                     ip="1.2.3.4", ua="UA/1.0", remember=True)
    act.record_login(uid=0, login_try="ghost_user", result="fail", ip="5.6.7.8", ua="UA/2.0")
    rows = act.login_history(u["uid"], days=1)
    assert len(rows) == 1
    r = rows[0]
    assert r["result"] == "success" and r["result_label"] == "登录成功"
    assert r["ip"] == "1.2.3.4" and r["remember"] == 1
    assert r["time"], "应带可读时间"
    # 失败记录 uid=0, 不属于任何人 → 该用户的记录里不应出现
    assert all(x["result"] != "fail" for x in rows)


def test_login_history_desc_and_window(client, create_user_token):
    """倒序返回; 超出 days 窗口的历史不返回"""
    u = create_user_token()
    now = time.time()
    conn = _db()
    conn.execute("INSERT INTO login_log (uid,login_try,result,ip,ua,remember,created_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (u["uid"], u["username"], "success", "1.1.1.1", "UA", 0, int(now) - 100 * 86400))
    conn.commit()
    conn.close()
    act.record_login(uid=u["uid"], login_try=u["username"], result="logout", ip="2.2.2.2")
    rows = act.login_history(u["uid"], days=30)
    assert [r["result"] for r in rows] == ["logout"], "100 天前那条不该出现在 30 天窗口里"
    rows30 = act.login_history(u["uid"], days=200)
    assert len(rows30) == 2 and rows30[0]["result"] == "logout", "应倒序(最新在前)"


def test_login_result_labels_cover_all_five():
    """五种结果都有中文(前端直接用这套映射, 缺一个界面就会出现英文/空白)"""
    for k in ("success", "fail", "reset", "kicked", "logout"):
        assert act.LOGIN_RESULT_LABEL.get(k), "缺少 %s 的中文名" % k


# ==================== 2. 功能使用计数(核心口径) ====================

def test_click_counted_per_call(client, create_user_token):
    """🔴 口径: 点一次记一次。**连点两次 = 2 次**(不能像配额那样 10s 去重)"""
    u = create_user_token()
    uid = u["uid"]
    for _ in range(2):
        act.bump_usage(uid, "picker")
    t = act.usage_today(uid)
    assert t["picker"]["count"] == 2, "连点两次应用被去重了(与'点一次记一次'口径冲突)"


def test_usage_aggregates_same_day_with_first_last_ts(client, create_user_token):
    """同一人同一天同一功能 → 聚合成一行, first_ts/last_ts 分别记录首末时刻"""
    u = create_user_token()
    uid = u["uid"]
    t0 = int(time.time()) - 100
    act.bump_usage(uid, "aipick", ts=t0)
    act.bump_usage(uid, "aipick", ts=t0 + 50)
    conn = _db()
    row = conn.execute("SELECT * FROM usage_daily WHERE uid=? AND feature='aipick'", (uid,)).fetchone()
    conn.close()
    assert row is not None and row["count"] == 2
    assert row["first_ts"] == t0 and row["last_ts"] == t0 + 50
    assert row["blocked_count"] == 0


def test_blocked_counted_separately(client, create_user_token):
    """被拦截(配额不足 429 / 门禁 403)进 blocked_count, 不虚增 count"""
    u = create_user_token()
    uid = u["uid"]
    act.bump_usage(uid, "auction", blocked=False)
    act.bump_usage(uid, "auction", blocked=True)
    t = act.usage_today(uid)
    assert t["auction"]["count"] == 1
    assert t["auction"]["blocked"] == 1


def test_bump_usage_rejects_unknown_feature(client, create_user_token):
    """白名单外的功能键必须被拒绝(前端乱传/爬虫刷接口都不能污染统计)"""
    uid = create_user_token()["uid"]
    assert act.bump_usage(uid, "not_a_feature") is False
    assert act.bump_usage(uid, "") is False
    assert act.bump_usage(uid, None) is False
    assert act.usage_today(uid) == {}


def test_bump_usage_beijing_date_boundary(client, create_user_token):
    """🔴 北京日期边界: UTC 15:59:59 仍是当日, UTC 16:00:00 已进次日(BJ +8)。
    🔬 变异靶点: 把 `bj_date` 里的 `+ 8*3600` 去掉(改用 UTC 日期) → 本条立刻变红。"""
    uid = create_user_token()["uid"]
    t_before = calendar.timegm((2026, 9, 21, 15, 59, 59, 0, 0, 0))   # BJ 2026-09-21 23:59:59
    t_after = calendar.timegm((2026, 9, 21, 16, 0, 0, 0, 0, 0))      # BJ 2026-09-22 00:00:00
    assert act.bj_date(t_before) == "2026-09-21"
    assert act.bj_date(t_after) == "2026-09-22"
    act.bump_usage(uid, "picker", ts=t_before)
    act.bump_usage(uid, "picker", ts=t_after)
    conn = _db()
    got = {r["date"]: r["count"] for r in conn.execute(
        "SELECT date, count FROM usage_daily WHERE uid=? AND feature='picker'", (uid,)).fetchall()}
    conn.close()
    assert got == {"2026-09-21": 1, "2026-09-22": 1}, "日期未按北京时间分桶: %s" % got


def test_usage_summary_matrix_and_feature_total(client, create_user_token):
    """详情页矩阵: 逐日行 + 功能合计 + 活跃天数 + 拦截合计"""
    u = create_user_token()
    uid = u["uid"]
    act.bump_usage(uid, "picker")
    act.bump_usage(uid, "picker")
    act.bump_usage(uid, "member")
    act.bump_usage(uid, "concept", blocked=True)
    s = act.usage_summary(uid, days=30)
    assert s["total"] == 3 and s["blocked_total"] == 1
    assert s["active_days"] == 1
    assert s["feature_total"]["picker"] == 2 and s["feature_total"]["member"] == 1
    assert s["today"]["picker"]["count"] == 2
    assert s["matrix"][0]["date"] == act.bj_date()
    assert s["matrix"][0]["total"] == 3


def test_feature_keys_cover_eight_entries():
    """8 个功能键都在(与前端 router 页面对齐; 少一个后台就看不到那类使用)"""
    assert set(act.FEATURES) == {"picker", "aipick", "auction", "concept",
                                 "history", "ladder", "market", "member"}
    assert all(act.FEATURES.values()), "中文名不能为空"


# ==================== 3. 管理端查询 ====================

def test_usage_rank_orders_and_flags(client, create_user_token):
    """排行按用量降序; 带用户名 / is_admin / member_level(用于剔除内部流量)"""
    a = create_user_token(member_level=0)
    b = create_user_token(member_level=1)
    d = act.bj_date()
    for _ in range(3):
        act.bump_usage(a["uid"], "picker")
    act.bump_usage(b["uid"], "picker")
    out = act.usage_rank(date=d, feature="picker", limit=50)
    order = [r["uid"] for r in out["rows"]]
    assert order.index(a["uid"]) < order.index(b["uid"]), "降序不对: %s" % order
    rec = next(r for r in out["rows"] if r["uid"] == b["uid"])
    assert rec["username"] == b["username"]
    assert rec["member_level"] == 1
    assert out["by_feature"].get("picker", 0) >= 4
    assert out["users"] == len(out["rows"])


def test_usage_rank_date_isolation(client, create_user_token):
    """指定日期只统计那一天(跨日不能串)"""
    uid = create_user_token()["uid"]
    act.bump_usage(uid, "picker", ts=calendar.timegm((2026, 3, 2, 4, 0, 0, 0, 0, 0)))  # BJ 03-02 12:00
    out = act.usage_rank(date="2026-03-02")
    assert any(r["uid"] == uid and r["count"] == 1 for r in out["rows"])
    out2 = act.usage_rank(date="2026-03-03")
    assert all(r["uid"] != uid for r in out2["rows"]), "3-02 的数据串到 3-03 了"


def test_global_login_log_filter_and_kw(client, create_user_token):
    """全站流水: 结果筛选 + 关键字(账号) + 总数"""
    u = create_user_token()
    act.record_login(uid=u["uid"], login_try=u["username"], result="success", ip="9.9.9.9")
    act.record_login(uid=u["uid"], login_try=u["username"], result="kicked", ip="9.9.9.9")
    act.record_login(uid=0, login_try="bruteforce_" + u["username"], result="fail", ip="8.8.8.8")
    allr = act.global_login_log(days=1, kw=u["username"], limit=100)
    assert allr["total"] >= 3
    kicks = act.global_login_log(days=1, result="kicked", kw=u["username"], limit=100)
    assert kicks["total"] == 1 and kicks["rows"][0]["result_label"] == "被顶出"
    fails = act.global_login_log(days=1, result="fail", kw="bruteforce_", limit=100)
    assert fails["total"] == 1 and fails["rows"][0]["login_try"] == "bruteforce_" + u["username"]


def test_active_trend_distinct_users(client, create_user_token):
    """活跃趋势: 同一人多次操作只算 1 个活跃用户; 操作次数照实累加。
    ★ 用一个别的用例不会碰的历史日期(80 天前, 在 days=90 窗口内)做精确断言 ——
      测试库是 session 级共享的, 用"今天"会被其他用例写入的行干扰。"""
    u1 = create_user_token()
    u2 = create_user_token()
    fixed = "2026-07-04"          # BJ 日期, 距 2026-09-22 约 80 天
    ts = calendar.timegm((2026, 7, 4, 4, 0, 0, 0, 0, 0))   # BJ 12:00
    assert act.bj_date(ts) == fixed
    for _ in range(3):
        act.bump_usage(u1["uid"], "picker", ts=ts)
    act.bump_usage(u2["uid"], "picker", ts=ts)
    tr = act.active_trend(days=90)
    day = next((x for x in tr["days"] if x["date"] == fixed), None)
    assert day is not None, "80 天前的日期没出现在趋势里"
    assert day["act_users"] == 2, "去重用户数不对: %s" % day
    assert day["actions"] == 4
    assert tr["today_date"] == act.bj_date()
    assert tr["sum30"]["actions"] >= 0


def test_login_stats_today(client, create_user_token):
    """看板: 今日登录成功人数/次数 + 失败次数"""
    u = create_user_token()
    act.record_login(uid=u["uid"], login_try=u["username"], result="success")
    act.record_login(uid=0, login_try="nobody", result="fail")
    st = act.login_stats()
    assert st["date"] == act.bj_date()
    assert st["success"] >= 1 and st["success_users"] >= 1
    assert st["fail"] >= 1


def test_purge_removes_only_expired(client, create_user_token):
    """保留期清理: 过期的删、期内的留"""
    u = create_user_token()
    uid = u["uid"]
    old_ts = int(time.time()) - (act.RETENTION_LOGIN_DAYS + 10) * 86400
    fresh_ts = int(time.time()) - 86400
    conn = _db()
    conn.execute("INSERT INTO login_log (uid,login_try,result,ip,ua,remember,created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (uid, u["username"], "success", "1.1.1.1", "UA", 0, old_ts))
    conn.execute("INSERT INTO login_log (uid,login_try,result,ip,ua,remember,created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (uid, u["username"], "success", "1.1.1.1", "UA", 0, fresh_ts))
    conn.execute("INSERT INTO usage_daily (uid,date,feature,count,blocked_count,first_ts,last_ts) "
                 "VALUES (?,?,?,?,0,?,?)",
                 (uid, act.bj_date(time.time() - (act.RETENTION_USAGE_DAYS + 10) * 86400),
                  "picker", 5, old_ts, old_ts))
    conn.commit()
    conn.close()
    out = act.purge()
    assert out["login"] >= 1 and out["usage"] >= 1
    rows = act.login_history(uid, days=act.RETENTION_LOGIN_DAYS + 20, limit=100)
    assert all(r["created_at"] != old_ts for r in rows), "老登录记录没被清掉"
    assert any(r["created_at"] == fresh_ts for r in rows), "期内记录被误删"


# ==================== 4. 降级: 写入绝不抛异常 ====================

def test_writes_never_raise(monkeypatch):
    """埋点在业务主链路上 —— 数据库炸了也只能吞掉, 不能把用户请求变 500"""
    class Boom:
        def __init__(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(act, "_conn", Boom)
    assert act.record_login(uid=1, result="success") is None      # 不抛
    assert act.bump_usage(1, "picker") is False                    # 返回 False 而非抛
    assert act.login_history(1) == []
    assert act.purge() == {"login": 0, "usage": 0}


# ==================== 5. 接口层 ====================

def test_track_requires_auth(client):
    """未登录不能上报"""
    r = client.post("/api/activity/track", json={"feature": "picker"})
    assert r.status_code == 401


def test_track_counts_for_member_and_admin(client, create_user_token):
    """🔴 会员与管理员**也计数**(这是与配额最大的差别: 配额对他们直接放行不计数,
    结果导致'用得最多的付费用户反而没记录')"""
    free = create_user_token(member_level=0)
    mem = create_user_token(member_level=1)
    adm = _admin_token(create_user_token)
    for u in (free, mem, adm):
        r = client.post("/api/activity/track", json={"feature": "picker"}, headers=hdrs(u["token"]))
        assert r.status_code == 200 and r.json()["counted"] is True
    for u in (free, mem, adm):
        assert act.usage_today(u["uid"])["picker"]["count"] == 1, "uid=%s 没被计数" % u["uid"]


def test_track_ignores_bad_feature_without_error(client, create_user_token):
    """非法 feature 静默忽略(返回 200 counted=false), 不 4xx 不 5xx —— 埋点不该打扰用户"""
    u = create_user_token()
    r = client.post("/api/activity/track", json={"feature": "hack"}, headers=hdrs(u["token"]))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "counted": False, "feature": "hack"}
    assert act.usage_today(u["uid"]) == {}


def test_track_blocked_flag(client, create_user_token):
    """前端在收到 429/403 时可带 blocked=true → 进拦截计数(转化线索)"""
    u = create_user_token()
    r = client.post("/api/activity/track", json={"feature": "auction", "blocked": True},
                    headers=hdrs(u["token"]))
    assert r.status_code == 200
    t = act.usage_today(u["uid"])
    assert t["auction"]["count"] == 0 and t["auction"]["blocked"] == 1


def test_logout_endpoint_records_and_revokes(client, create_user_token):
    """主动退出: 作废旧 token + 落一条 logout 记录(此前'退出'是纯前端行为, 后端无感知)"""
    u = create_user_token()
    r = client.post("/api/logout", headers=hdrs(u["token"]))
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = act.login_history(u["uid"], days=1)
    assert any(x["result"] == "logout" for x in rows), "退出没落库: %s" % rows
    # token 已作废 → 再访问应 401
    assert client.post("/api/activity/track", json={"feature": "picker"},
                       headers=hdrs(u["token"])).status_code == 401


def test_admin_endpoints_require_admin(client, create_user_token):
    """4 个行为查询端点都只有管理员能进(普通用户 403; 未登录 401)"""
    free = create_user_token(member_level=0)
    # 显式压回非管理员: 避免 ensure_admin 在别的用例之前把 id 最小的用户提成管理员,
    # 让这条 403 断言变成假失败/假通过(测试库是 session 级共享的)
    conn = _db()
    conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (free["uid"],))
    conn.commit()
    conn.close()
    for path in ("/api/admin/user-activity?target_uid=1",
                 "/api/admin/login-log?days=1",
                 "/api/admin/usage-rank",
                 "/api/admin/active-users?days=1"):
        assert client.get(path).status_code == 401, path + " 未登录应 401"
        assert client.get(path, headers=hdrs(free["token"])).status_code == 403, path + " 非管理员应 403"


def test_admin_user_activity_returns_both_and_audits(client, create_user_token):
    """用户详情: 登录记录 + 使用记录都要有; 🔴 查看明细必须写审计留痕(IP/UA 属个人信息)"""
    target = create_user_token()
    adm = _admin_token(create_user_token)
    act.record_login(uid=target["uid"], login_try=target["username"], result="success", ip="3.3.3.3")
    act.bump_usage(target["uid"], "picker")
    r = client.get("/api/admin/user-activity?target_uid=%d&days=30" % target["uid"],
                   headers=hdrs(adm["token"]))
    assert r.status_code == 200
    d = r.json()
    assert d["target"]["id"] == target["uid"]
    assert len(d["logins"]) == 1 and d["logins"][0]["ip"] == "3.3.3.3"
    assert d["usage"]["today"]["picker"]["count"] == 1
    conn = _db()
    n = conn.execute("SELECT COUNT(*) c FROM admin_audit WHERE action='view_user_activity' "
                     "AND target_uid=? AND admin_uid=?", (target["uid"], adm["uid"])).fetchone()["c"]
    conn.close()
    assert n >= 1, "查看用户行为明细没有写审计留痕"


def test_admin_login_log_and_usage_rank_endpoints(client, create_user_token):
    """两个全局端点正常返回"""
    u = create_user_token()
    adm = _admin_token(create_user_token)
    act.record_login(uid=u["uid"], login_try=u["username"], result="success")
    act.bump_usage(u["uid"], "concept")
    r1 = client.get("/api/admin/login-log?days=1&kw=" + u["username"], headers=hdrs(adm["token"]))
    assert r1.status_code == 200 and r1.json()["total"] >= 1
    r2 = client.get("/api/admin/usage-rank?limit=50", headers=hdrs(adm["token"]))
    assert r2.status_code == 200
    assert r2.json()["by_feature"].get("concept", 0) >= 1
    r3 = client.get("/api/admin/active-users?days=2", headers=hdrs(adm["token"]))
    assert r3.status_code == 200 and len(r3.json()["days"]) == 2


def test_admin_dashboard_includes_activity_block(client, create_user_token):
    """看板 2 张新卡的数据来自 dashboard 的 activity 块(登录概况 + 使用 Top)"""
    adm = _admin_token(create_user_token)
    u = create_user_token()
    act.record_login(uid=u["uid"], login_try=u["username"], result="success")
    act.bump_usage(u["uid"], "ladder")
    r = client.get("/api/admin/dashboard", headers=hdrs(adm["token"]))
    assert r.status_code == 200
    a = r.json().get("activity")
    assert a and "login" in a and "usage" in a
    assert a["login"]["date"] == act.bj_date()
    assert any(x["uid"] == u["uid"] for x in a["usage"]["rows"]) or a["usage"]["total"] >= 1
    # 旧字段不能因为新增而消失
    assert "quota" in r.json() and "stats" in r.json()


def test_backfill_upsert_is_idempotent(client, create_user_token):
    """回溯写入用绝对值 + 取 MAX → 同一批日志跑两遍不会翻倍"""
    uid = create_user_token()["uid"]
    d = "2026-08-20"
    rows = [(uid, d, "picker", 7, 1000, 2000)]
    assert act.upsert_absolute(rows) == 1
    assert act.upsert_absolute(rows) == 1
    conn = _db()
    row = conn.execute("SELECT count, first_ts, last_ts FROM usage_daily "
                       "WHERE uid=? AND date=? AND feature='picker'", (uid, d)).fetchone()
    conn.close()
    assert row["count"] == 7, "重跑翻倍了: %s" % row["count"]
    assert row["first_ts"] == 1000 and row["last_ts"] == 2000


# ==================== 6. 老 SQLite 兼容(2026-09-22 实测踩坑) ====================

def test_no_upsert_syntax_for_old_sqlite(monkeypatch, create_user_token):
    """🔴 禁用 UPSERT(`ON CONFLICT ... DO UPDATE`): 测试机 CentOS 7 的 SQLite 是 3.7.17。

    踩坑经过(2026-09-22): 本机 SQLite 3.53、生产 3.26 都支持 UPSERT, 唯独**测试机 3.7.17**
    报 `near "ON": syntax error`。于是出现最坏组合 —— 本机 1229 用例全绿, 一上测试机
    第一次写计数就失败; 而 `bump_usage` 又有"写入失败只吞不抛"的铁律, 失败被伪装成
    正常的 `counted=false`, 从接口看毫无异常, 得去翻 app.log 才看到真因。

    项目里的既有约定(见 services/fetcher.py 59 行 / mv_cache.py 51 行 / settings.py 35 行):
    「兼容 CentOS 7 SQLite 3.7(不支持 ON CONFLICT) → 统一用 INSERT OR REPLACE」。
    本用例把这条约定钉在**执行路径**上: 用 `set_trace_callback` 抓真正执行的 SQL,
    而不是 grep 源码 —— 源码注释里可以随便提这个词, 但只要真去执行就变红。
    """
    uid = create_user_token()["uid"]
    seen = []
    real = act._conn

    def spy():
        c = real()
        c.set_trace_callback(seen.append)   # 每条真正执行的 SQL 都会进来
        return c

    monkeypatch.setattr(act, "_conn", spy)
    act.bump_usage(uid, "picker")                                    # 累加路径
    act.upsert_absolute([(uid, "2026-08-20", "picker", 3, 1, 2)])    # 回溯路径
    sql = " ".join(seen).upper()
    assert seen, "没抓到任何 SQL —— 间谍没生效, 本用例形同虚设"
    assert "ON CONFLICT" not in sql, \
        "使用了 UPSERT, 测试机(CentOS7/SQLite 3.7.17)会 `near \"ON\": syntax error`: %s" % sql
    assert "INSERT OR IGNORE" in sql, \
        "老 SQLite 兼容写法应为「INSERT OR IGNORE 建行 + UPDATE 累加」两步"
