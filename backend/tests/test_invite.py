# -*- coding: utf-8 -*-
"""邀请与偏好测试"""


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def test_invite_info(client, first_user):
    token, username, invite_code = first_user
    assert invite_code and len(invite_code) == 8
    r = client.get("/api/invite", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["invite_code"] == invite_code
    # invited_count 与 invitees 名单长度一致(具体值可能被其他测试影响)
    assert d["invited_count"] == len(d["invitees"])
    assert isinstance(d["invitees"], list)


def test_invite_refresh(client, first_user):
    token, _, invite_code = first_user
    r = client.post("/api/invite/refresh", headers=hdrs(token))
    assert r.status_code == 200
    new_code = r.json().get("invite_code")
    assert new_code and new_code != invite_code   # 刷新后应变化


def test_register_with_invalid_invite(client):
    """注册已关闭(合规2026-08-25): 任何注册请求(含邀请码)一律 403"""
    r = client.post("/api/register", json={"username": "tester2", "password": "Test123456",
                                           "invite_code": "BADCODE1"})
    assert r.status_code == 403
    assert not r.json().get("ok")


def test_register_with_valid_invite(client, first_user):
    """注册已关闭(合规2026-08-25): 即使带有效邀请码, 注册也仍被拒, 邀请数不增长"""
    import uuid
    token, _, _ = first_user
    inv = client.get("/api/invite", headers=hdrs(token)).json()["invite_code"]
    uname = "invitee_" + uuid.uuid4().hex[:8]
    phone = "139" + str(uuid.uuid4().int % 100000000).zfill(8)
    email = uuid.uuid4().hex[:8] + "i@test.local"
    r = client.post("/api/register", json={"username": uname, "password": "Test123456",
                                           "invite_code": inv,
                                           "phone": phone, "email": email})
    assert r.status_code == 403
    assert not r.json().get("ok")
    # 邀请关系不生增长(新用户未被创建)
    r2 = client.get("/api/invite", headers=hdrs(token))
    assert all(u["username"] != uname for u in r2.json()["invitees"])


def test_prefs_save_load(client, first_user):
    token, _, _ = first_user
    # 保存
    r = client.post("/api/prefs", json={"settings": {"bidGt": 8, "probLt": 90}},
                    headers=hdrs(token))
    assert r.status_code == 200 and r.json().get("ok")
    # 读取
    r2 = client.get("/api/prefs", headers=hdrs(token))
    assert r2.status_code == 200
    s = r2.json().get("settings") or {}
    assert s.get("bidGt") == 8


def test_prefs_save_merge(client, first_user):
    """偏好合并保存: 第二次只提交部分字段时, 已有字段保留(筛选/主题互不覆盖)"""
    token, _, _ = first_user
    r = client.post("/api/prefs", json={"settings": {"bidGt": 8}}, headers=hdrs(token))
    assert r.json().get("ok")
    r = client.post("/api/prefs", json={"settings": {"theme": "blue"}}, headers=hdrs(token))
    assert r.json().get("ok")
    r2 = client.get("/api/prefs", headers=hdrs(token))
    s = r2.json().get("settings") or {}
    assert s.get("bidGt") == 8       # 已有字段保留
    assert s.get("theme") == "blue"  # 新字段写入
