# -*- coding: utf-8 -*-
"""auto_apply 9:26 自动应用服务测试"""
import time

import pytest

from app.services import auto_apply, fetcher, history, scorer, auction_snapshot
from app.db import database


def _mk_raw(code="600001", chg=4.0):
    return {
        "f12": code, "f14": "测" + code[-3:], "f2": 18.5, "f3": chg, "f4": 3.1,
        "f5": 150000.0, "f6": 2800.0, "f8": 5.5, "f10": 1.8, "f17": 18.9,
        "f18": 17.9, "f20": 5.0e10, "f21": 4.0e9, "f100": "软件", "f102": "广东",
        "f103": "AI", "f615": chg, "f616": 5.0e7, "f617": 300.0, "f618": 400.0,
        "f630": 3,
    }


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
    """系统标准来自全局默认参数, 且补全 markets"""
    f = auto_apply._get_system_filter()
    assert isinstance(f, dict)
    assert "markets" in f
    assert f["markets"] == ["SH", "SZ", "BJ"]


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
    try:
        row = conn.execute("SELECT id FROM users WHERE is_admin=0 LIMIT 1").fetchone()
        if not row:
            pytest.skip("无普通用户")
        uid = row[0]
        conn.execute("UPDATE users SET expire_at=? WHERE id=?", (int(time.time()) - 10, uid))
        conn.commit()
    finally:
        conn.close()
    ok, why = auto_apply._is_user_active(uid)
    assert ok is False and "过期" in why


def test_auto_apply_missing_cache(monkeypatch):
    """行情缓存缺失 → 返回 0 应用于 error"""
    monkeypatch.setattr(fetcher, "ensure_cache", lambda *a, **k: (None, "no cache"))
    r = auto_apply.auto_apply_all_users()
    assert r["applied"] == 0
    assert "error" in r


def test_auto_apply_score_failure(monkeypatch):
    """评分异常 → 返回 0 应用于 error"""
    monkeypatch.setattr(fetcher, "ensure_cache", lambda *a, **k: ([_mk_raw()], None))
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda *a, **k: {})
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(scorer, "score_all_stocks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = auto_apply.auto_apply_all_users()
    assert r["applied"] == 0 and "error" in r


def test_auto_apply_skips_inactive(client, first_user, second_user, monkeypatch):
    """跳过管理员/过期/已应用用户, 只 apply 有效候选"""
    monkeypatch.setattr(fetcher, "ensure_cache", lambda *a, **k: ([_mk_raw()], None))
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda *a, **k: {})
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda *a, **k: {})
    # 统一过滤后保留 1 只
    monkeypatch.setattr(scorer, "apply_filters", lambda scored, f: scored)
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
    monkeypatch.setattr(fetcher, "ensure_cache", lambda *a, **k: ([_mk_raw()], None))
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda *a, **k: {})
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(scorer, "apply_filters", lambda scored, f: scored)

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
    monkeypatch.setattr(fetcher, "ensure_cache", lambda *a, **k: ([_mk_raw()], None))
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda *a, **k: {})
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(scorer, "apply_filters", lambda scored, f: scored)
    res = auto_apply.auto_apply_all_users(max_users=1)
    assert res["total"] == 1


def test_trigger_spawns_thread(monkeypatch):
    """trigger_auto_apply 返回后台线程, 但不实际跑服务(避免线程并发 DB)"""
    monkeypatch.setattr(auto_apply, "auto_apply_all_users", lambda: {"applied": 0})
    t = auto_apply.trigger_auto_apply()
    assert t.name == "auto_apply"
    assert t.join(timeout=10) is None  # join 返回 None 表示线程已结束(非阻塞等待)

def test_auto_apply_empty_result_skips_all_without_failed(client, first_user, monkeypatch):
    """2026-09-08: 系统统一筛选为空(行情源故障) → 空名单不落库, 且必须提前返回,
    不能把每个候选用户都记一次 failed(否则"没票"被误报成"落库失败", 淹没真实告警)"""
    monkeypatch.setattr(fetcher, "ensure_cache", lambda *a, **k: ([_mk_raw()], None))
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda *a, **k: {})
    monkeypatch.setattr(fetcher, "fetch_yesterday_changes", lambda *a, **k: {})
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change", lambda *a, **k: {})
    monkeypatch.setattr(scorer, "score_all_stocks", lambda *a, **k: [])
    monkeypatch.setattr(scorer, "apply_filters", lambda scored, f: [])   # 筛选后 0 只

    called = {"n": 0}
    def _spy(*a, **k):
        called["n"] += 1
        return None
    monkeypatch.setattr(history, "save_batch", _spy)

    res = auto_apply.auto_apply_all_users()
    assert res["applied"] == 0
    assert res["failed"] == 0, "空名单不是落库失败, 不得计入 failed"
    assert res["skipped"] == res["total"], "全部候选应计为 skipped"
    assert res["total"] >= 1
    assert called["n"] == 0, "空名单不得调用 save_batch"
