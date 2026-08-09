# -*- coding: utf-8 -*-
"""账号到期权限测试: 设置/续费/过期拦截/管理员豁免/管理端接口"""
import os
import sqlite3
import time
import uuid

import pytest

from app.services import users

DURATIONS = {"week": 7, "month": 30, "quarter": 90, "year": 365}


@pytest.fixture(scope="session", autouse=True)
def _expire_admin(first_user):
    """把 first_user 设为管理员(测试库确定性), 供管理端接口测试"""
    token, uname, _ = first_user
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("UPDATE users SET is_admin=1 WHERE username=?", (uname,))
    conn.execute("INSERT OR REPLACE INTO settings (key,value,updated_at) "
                 "VALUES ('admin_initialized','true',0)")
    conn.commit()
    conn.close()


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def _new_user(client, inv):
    uname = "exp_" + uuid.uuid4().hex[:8]
    r = client.post("/api/register", json={"username": uname, "password": "Test123456",
                                           "invite_code": inv})
    assert r.status_code == 200, r.text
    return r.json()["token"], uname


# ---------- 服务层 ----------
def test_extend_from_now():
    u = users.create_user("exp_a_" + uuid.uuid4().hex[:6], "Test123456")
    new = users.extend_expire(u, 7)
    assert abs(new - (int(time.time()) + 7 * 86400)) < 5


def test_extend_stacks(client, first_user):
    """续费叠加: 从当前到期时间累加, 而非从 now"""
    _, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    base = int(time.time()) + 30 * 86400
    users.set_expire(u, base)
    new = users.extend_expire(u, 30)
    assert abs(new - (base + 30 * 86400)) < 5


def test_set_expire_permanent(client, first_user):
    _, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    users.set_expire(u, 0)
    assert users.is_expired(u) is False
    # 曾过期 → 永久 → 不再过期
    users.set_expire(u, int(time.time()) - 100)
    assert users.is_expired(u) is True
    users.set_expire(u, 0)
    assert users.is_expired(u) is False


def test_is_expired_boundary():
    u = users.create_user("exp_b_" + uuid.uuid4().hex[:6], "Test123456")
    users.set_expire(u, int(time.time()) - 10)
    assert users.is_expired(u) is True
    users.set_expire(u, int(time.time()) + 86400)
    assert users.is_expired(u) is False


# ---------- 接口拦截 ----------
def test_expired_user_blocked(client, first_user):
    """普通用户过期后访问业务接口 → 403"""
    _, _, inv = first_user
    token, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    users.set_expire(u, int(time.time()) - 100)
    r = client.get("/api/stocks?action=ping", headers=hdrs(token))
    assert r.status_code == 403
    assert "过期" in r.json().get("detail", {}).get("msg", "")


def test_unexpired_user_ok(client, first_user):
    """未过期用户正常访问"""
    _, _, inv = first_user
    token, _ = _new_user(client, inv)
    r = client.get("/api/stocks?action=ping", headers=hdrs(token))
    assert r.status_code == 200


def test_admin_not_blocked(client, first_user):
    """管理员即使过期也不被拦(保证管理续费入口可用)"""
    token, _, _ = first_user
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("UPDATE users SET expire_at=? WHERE username=?", (int(time.time()) - 100, first_user[1]))
    conn.commit()
    conn.close()
    try:
        r = client.get("/api/admin/users", headers=hdrs(token))
        assert r.status_code == 200
    finally:
        conn = sqlite3.connect(os.environ["BID_DB_PATH"])
        conn.execute("UPDATE users SET expire_at=0 WHERE username=?", (first_user[1],))
        conn.commit()
        conn.close()


# ---------- 登录响应 ----------
def test_login_returns_expire(client, first_user):
    _, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    users.set_expire(u, int(time.time()) + 86400)
    r = client.post("/api/login", json={"login": uname, "password": "Test123456"})
    d = r.json()
    assert d.get("ok")
    assert d["expire_at"] > 0 and d["expired"] == 0


# ---------- 管理端接口 ----------
def test_admin_expire_duration(client, first_user):
    """管理端: duration=week → 到期 ≈ 现在+7天"""
    token, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    r = client.post("/api/admin/users/expire", json={"uid": u, "duration": "week"},
                    headers=hdrs(token))
    assert r.status_code == 200
    et = r.json().get("expire_at")
    assert abs(et - (int(time.time()) + 7 * 86400)) < 60


def test_admin_expire_days_zero_permanent(client, first_user):
    token, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    r = client.post("/api/admin/users/expire", json={"uid": u, "days": 0},
                    headers=hdrs(token))
    assert r.status_code == 200 and r.json()["expire_at"] == 0


def test_admin_expire_expire_at_date(client, first_user):
    token, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    r = client.post("/api/admin/users/expire", json={"uid": u, "expire_at": "2027-01-01"},
                    headers=hdrs(token))
    assert r.status_code == 200
    et = r.json()["expire_at"]
    # 北京 2027-01-01 23:59:59 → UTC 时间戳
    from datetime import datetime, timezone, timedelta
    expect = int(datetime(2027, 1, 1, tzinfo=timezone(timedelta(hours=8))).timestamp()) + 86399
    assert abs(et - expect) < 60


def test_admin_expire_bad_params(client, first_user):
    token, _, inv = first_user
    _, uname = _new_user(client, inv)
    u = users.find_user(uname)["id"]
    r = client.post("/api/admin/users/expire", json={"uid": u}, headers=hdrs(token))
    assert r.status_code == 400
    r = client.post("/api/admin/users/expire", json={"uid": u, "expire_at": "2027/01/01"},
                    headers=hdrs(token))
    assert r.status_code == 400
    r = client.post("/api/admin/users/expire", json={"uid": 999999, "days": 7},
                    headers=hdrs(token))
    assert r.status_code == 404


def test_admin_expire_requires_admin(client, second_user):
    token, _ = second_user
    r = client.post("/api/admin/users/expire", json={"uid": 1, "days": 7}, headers=hdrs(token))
    assert r.status_code == 403
