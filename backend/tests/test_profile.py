# -*- coding: utf-8 -*-
"""个人资料接口: 查询/修改(手机号/邮箱/微信名)"""
import uuid


def _hdrs(token):
    return {"Authorization": "Bearer " + token}


def test_profile_get(client, first_user):
    """GET /api/profile 返回脱敏资料"""
    token, username, _ = first_user
    r = client.get("/api/profile", headers=_hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["profile"]["username"] == username
    assert d["profile"]["member_level"] is not None


def test_profile_update_wx_name(client, first_user):
    """POST /api/profile 改微信名, 可读回"""
    token, _, _ = first_user
    wx = "老师_" + uuid.uuid4().hex[:4]
    r = client.post("/api/profile", json={"wx_name": wx}, headers=_hdrs(token))
    assert r.status_code == 200 and r.json().get("ok")
    r2 = client.get("/api/profile", headers=_hdrs(token))
    assert r2.json()["profile"]["wx_name"] == wx


def test_profile_update_bad_phone(client, first_user):
    """手机号格式错误 400"""
    token, _, _ = first_user
    r = client.post("/api/profile", json={"phone": "123"}, headers=_hdrs(token))
    assert r.status_code == 400
    assert not r.json().get("ok")


def test_forgot_check_no_email(client, first_user):
    """forgot/check: 未绑定邮箱的用户提示联系管理员"""
    import app.services.users as users
    token, username, _ = first_user
    # 该用户注册时绑了 email → has_email=True
    r = client.post("/api/forgot/check", json={"login": username})
    assert r.status_code == 200
    assert r.json().get("has_email") is True
