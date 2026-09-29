# -*- coding: utf-8 -*-
"""auto_apply 9:26 自动应用服务测试"""
import time

import pytest

from app.services import auto_apply, history, scorer
from app.db import database


def test_user_auto_applied_today(client, first_user):
    """当天已有 auto_applied 批次 → True; 无 → False"""
    token, _, _ = first_user
    conn = database.get_conn()
    uid = conn.execute("SELECT id FROM users WHERE username=?", (first_user[1],)).fetchone()[0]
    bdate = auto_apply._today_bj()
    assert auto_apply._user_auto_applied_today(uid, bdate) is False
    # 插入一个 auto_applied 批次 (迁移列 user_id + 全部非空字段)
    conn.execute(
        "INSERT INTO batches (user_id, batch_date, batch_time, ts, action, markets, filters, stock_count, auto_applied) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (uid, bdate, "09:26", int(time.time()), "lock", "sh_sz", "{}", 0, 1))
    conn.commit()
    conn.close()
    assert auto_apply._user_auto_applied_today(uid, bdate) is True


def test_get_system_filter_uses_defaults(monkeypatch):
    """系统标准来自全局默认参数, 且补全 markets(2026-09-29 起含北交所 bj)"""
    f = auto_apply._get_system_filter()
    assert isinstance(f, dict)
    assert "markets" in f
    assert f["markets"] == ["hs", "cyb", "kcb", "bj"]


def test_system_filter_markets_must_be_lowercase():
    """回归保护(2026-09-08 实测事故): markets 必须是**小写** hs/cyb/kcb/bj。

    曾为 ["SH","SZ","BJ"] 大写, 而 scorer._in_markets 按代码前缀匹配小写键 →
    沪深创科**全部**返回 False → 9:26 自动应用恒出 0 只(实测批次#1578 count=0,
    同日系统批次用小写口径正常出 30 只)。大小写一错, 整个自动应用功能静默失效。
    (2026-09-29 北交所纳入后, 合法值集合加 bj —— 大写 "BJ" 同样必须被拒。)
    """
    from app.services import scorer
    f = auto_apply._get_system_filter()
    markets = f["markets"]
    for m in markets:
        assert m in ("hs", "cyb", "kcb", "bj"), "markets 只能是小写口径: %r" % (markets,)
    # 四个板各取一个真实代码, 必须全部通过市场过滤(北交所 920 段 一起纳入)
    for code in ("600127", "000759", "300454", "688111", "920267"):
        assert scorer._in_markets(code, markets), \
            "%s 被市场过滤掉 → 自动应用会出 0 只(markets=%r)" % (code, markets)


def test_is_user_active_missing():
    ok, why = auto_apply._is_user_active(99999999)
    assert ok is False and "不存在" in why


def test_is_user_active_admin(client, first_user):
    """管理员 → 跳过"""
    from app.db import database as db
    conn = db.get_conn()
    # 找管理员
    row = conn.execute("SELECT id FROM users WHERE is_admin=1 LIMIT 1").fetchone()
    conn.close()
    if not row:
        pytest.skip("无管理员用户")
    ok, why = auto_apply._is_user_active(row[0])
    assert ok is False and "管理员" in why


def test_is_user_active_expired(client, first_user):
    """过期用户 → 跳过"""
    conn = database.get_conn()
    uid = None
    try:
        row = conn.execute("SELECT id FROM users WHERE is_admin=0 LIMIT 1").fetchone()
        if not row:
            pytest.skip("无普通用户")
        uid = row[0]
        conn.execute("UPDATE users SET expire_at=? WHERE id=?", (int(time.time()) - 10, uid))
        conn.commit()
    finally:
        conn.close()
    try:
        ok, why = auto_apply._is_user_active(uid)
        assert ok is False and "过期" in why
    finally:
        # 🔴 必须还原: 被改的往往是**会话级 first_user**(多个文件的 API 用例共用),
        # 不还原会让后续 test_history / test_stocks 等文件全部收到 403「过期账号」
        # —— 表征为"单文件绿、多文件连跑红"的顺序耦合(2026-09-18 实测踩到)。
        conn = database.get_conn()
        conn.execute("UPDATE users SET expire_at=0 WHERE id=?", (uid,))
        conn.commit()
        conn.close()


def _no_list(*a, **k):
    """模拟名单源无数据(2026-09-09 起主链路 = picker.pipeline)"""
    class _Empty:
        items = []
        errors = ["名单源无数据(模拟)"]

        def summary(self):
            return "模拟空名单"
    return _Empty()


def _boom_lock(*a, **k):
    raise RuntimeError("boom")


def _lock_items():
    """锁仓链路返回的名单 item(字段齐套, 可直接落库)"""
    return [{"code": "600001", "name": "测001", "probability": 82, "confidence": 75,
             "bidChange": 4.0, "realChange": 4.2, "entityChange": 3.0,
             "bidTurnover": 1.5, "warnType": 0, "circulationMV": 40.0,
             "industry": "软件", "concept": "AI", "bidAmt": 8000.0,
             "bidRatio": 25.0, "qiangchou": 0}]


def _patch_lock(monkeypatch, items):
    """把 auto_apply 的选股入口(picker.lock.run_lock)换成桩。

    2026-09-11: 老链路退役后 auto_apply 只有 picker 一条路径; 原用例打桩在
    scorer.apply_filters / score_all_stocks 上, 早已不被调用(桩形同虚设)。
    """
    from app.services.picker import lock as plock

    def _run_lock(f, **kw):
        lr = plock.LockResult()
        lr.items = list(items)
        lr.errors = []
        return lr

    monkeypatch.setattr(plock, "run_lock", _run_lock)


def test_auto_apply_missing_cache(monkeypatch):
    """名单源无数据 → 返回 0 应用于 error(不再回退老链路)"""
    from app.services.picker import lock as plock
    monkeypatch.setattr(plock, "run_lock", _no_list)
    r = auto_apply.auto_apply_all_users()
    assert r["applied"] == 0
    assert "error" in r


def test_auto_apply_score_failure(monkeypatch):
    """选股链路异常 → 返回 0 应用于 error"""
    from app.services.picker import lock as plock
    monkeypatch.setattr(plock, "run_lock", _boom_lock)
    r = auto_apply.auto_apply_all_users()
    assert r["applied"] == 0 and "error" in r


def test_auto_apply_skips_inactive(client, first_user, second_user, monkeypatch):
    """跳过管理员/过期/已应用用户, 只 apply 有效候选"""
    _patch_lock(monkeypatch, _lock_items())
    res = auto_apply.auto_apply_all_users()
    # second_user 是非管理员普通用户 → 应被 apply
    assert res["total"] >= 1
    assert res["applied"] >= 1
    assert res["failed"] == 0
    # 再次调用 → 所有用户当天已有统一批次 → 全 skipped
    res2 = auto_apply.auto_apply_all_users()
    assert res2["applied"] == 0
    assert res2["skipped"] == res["total"]


def test_auto_apply_failed_user_isolated(client, first_user, monkeypatch):
    """单个用户落库失败不影响其他用户"""
    _patch_lock(monkeypatch, _lock_items())

    # 清空 auto_applied 批次, 隔离前序测试(同一 session DB)
    conn = database.get_conn()
    conn.execute("DELETE FROM batches WHERE auto_applied=1")
    conn.execute("DELETE FROM batch_stocks")
    conn.commit()
    conn.close()

    def flaky_save(user_id, action, result, f, **kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr(history, "save_batch", flaky_save)
    res = auto_apply.auto_apply_all_users()
    # 全部失败, 但程序不崩
    assert res["applied"] == 0
    assert res["total"] >= 1
    assert res["failed"] == res["total"]


def test_max_users_limit(client, first_user, second_user, monkeypatch):
    """max_users 限制候选数"""
    _patch_lock(monkeypatch, _lock_items())
    res = auto_apply.auto_apply_all_users(max_users=1)
    assert res["total"] == 1


def test_trigger_spawns_thread(monkeypatch):
    """trigger_auto_apply 返回后台线程, 但不实际跑服务(避免线程并发 DB)"""
    monkeypatch.setattr(auto_apply, "auto_apply_all_users", lambda: {"applied": 0})
    t = auto_apply.trigger_auto_apply()
    assert t.name == "auto_apply"
    assert t.join(timeout=10) is None  # join 返回 None 表示线程已结束(非阻塞等待)

def test_auto_apply_empty_result_skips_all_without_failed(client, first_user, monkeypatch):
    """2026-09-08 语义: 系统统一筛选为空(行情源故障/无票) → 空名单不落库, 且必须提前
    返回, 不能把每个候选用户都记一次 failed(否则"没票"被误报成"落库失败", 淹没真实告警)。

    2026-09-11: 空名单现在由 _pick_result 直接返回 error → 主流程提前返回 total=0
    (老实现是遍历用户后各自 skipped)。核心保护不变: **不得调用 save_batch、不得计 failed**。
    """
    from app.services.picker import lock as plock
    monkeypatch.setattr(plock, "run_lock", _no_list)

    called = {"n": 0}
    def _spy(*a, **k):
        called["n"] += 1
        return None
    monkeypatch.setattr(history, "save_batch", _spy)

    res = auto_apply.auto_apply_all_users()
    assert res["applied"] == 0
    assert res["failed"] == 0, "空名单不是落库失败, 不得计入 failed"
    assert called["n"] == 0, "空名单不得调用 save_batch"
    assert "error" in res, "空名单必须显式报错(降级可见), 而不是静默返回 0 应用"
