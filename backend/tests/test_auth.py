# -*- coding: utf-8 -*-
"""认证相关测试: 注册/登录/鉴权/改密/踢下线"""


def test_register_first_user(client, first_user):
    """首用户注册: first_user 是整套测试中第一个注册的用户(免邀请码)"""
    token, username, invite = first_user
    assert token and username
    assert invite and len(invite) == 8


def test_register_duplicate_username(client, first_user):
    """重复用户名注册应 409(用有效邀请码绕过首用户校验)"""
    token, username, invite = first_user
    r = client.post("/api/register", json={"username": username, "password": "Other123",
                                           "invite_code": invite})
    assert r.status_code == 409
    assert not r.json().get("ok")


def test_login_ok(client, first_user):
    """正确密码登录(用独立注册的用户,避免踢掉 first_user session token 影响其他测试)"""
    import uuid
    _, _, invite = first_user
    uname = "login_" + uuid.uuid4().hex[:8]
    r = client.post("/api/register", json={"username": uname, "password": "Test123456",
                                           "invite_code": invite})
    assert r.status_code == 200
    r = client.post("/api/login", json={"login": uname, "password": "Test123456"})
    assert r.status_code == 200
    assert r.json().get("token")


def test_login_wrong_password(client, first_user):
    """错误密码 401"""
    _, username, _ = first_user
    r = client.post("/api/login", json={"login": username, "password": "wrongpass"})
    assert r.status_code == 401


def test_login_nonexist_user(client):
    """不存在用户 401"""
    r = client.post("/api/login", json={"login": "nobody", "password": "x"})
    assert r.status_code == 401


def test_login_revokes_previous_token(client, first_user):
    """单点登录: 同一账号再次登录后, 旧 token 立即失效, 新 token 有效。
    用独立注册用户避免污染 first_user/second_user session。"""
    import uuid
    _, _, invite = first_user
    uname = "sso_" + uuid.uuid4().hex[:8]
    r = client.post("/api/register", json={"username": uname, "password": "Test123456",
                                           "invite_code": invite})
    assert r.status_code == 200
    token1 = r.json()["token"]
    # token1 当前有效
    assert client.get("/api/invite", headers=hdrs(token1)).status_code == 200
    # 再次登录(同账号同密码)
    r = client.post("/api/login", json={"login": uname, "password": "Test123456"})
    assert r.status_code == 200
    d = r.json()
    token2 = d.get("token")
    assert token2 and token2 != token1
    # token1 失效(被踢)
    assert client.get("/api/invite", headers=hdrs(token1)).status_code == 401
    # token2 有效
    assert client.get("/api/invite", headers=hdrs(token2)).status_code == 200


def test_login_failed_does_not_revoke(client, first_user):
    """密码错误时不应踢掉该用户的旧会话(避免错误登录导致在线用户被误踢)"""
    import uuid
    _, _, invite = first_user
    uname = "fakelogin_" + uuid.uuid4().hex[:8]
    r = client.post("/api/register", json={"username": uname, "password": "Test123456",
                                           "invite_code": invite})
    assert r.status_code == 200
    token1 = r.json()["token"]
    # 错误密码登录
    r = client.post("/api/login", json={"login": uname, "password": "WrongPass1"})
    assert r.status_code == 401
    # token1 仍然有效(没被踢)
    assert client.get("/api/invite", headers=hdrs(token1)).status_code == 200


def test_no_token_401(client):
    """无 token 访问受保护接口 401"""
    assert client.get("/api/stocks").status_code == 401
    assert client.get("/api/invite").status_code == 401


def test_change_password_flow(client, first_user):
    """改密: 旧密码错400 / 成功后踢下线 / 新密码可登录。
    用独立注册的用户(避免踢掉共享 first_user 的 token)"""
    import uuid
    _, _, invite = first_user
    uname = "cp_" + uuid.uuid4().hex[:8]
    r = client.post("/api/register", json={"username": uname, "password": "Test123456",
                                           "invite_code": invite})
    assert r.status_code == 200
    token = r.json()["token"]
    # 旧密码错误
    r = client.post("/api/change-password", json={
        "old_password": "wrong", "new_password": "NewPass456"}, headers=hdrs(token))
    assert r.status_code == 400
    # 新密码太短
    r = client.post("/api/change-password", json={
        "old_password": "Test123456", "new_password": "123"}, headers=hdrs(token))
    assert r.status_code == 400
    # 正常改密
    r = client.post("/api/change-password", json={
        "old_password": "Test123456", "new_password": "NewPass456"}, headers=hdrs(token))
    assert r.status_code == 200 and r.json().get("ok")
    # 旧 token 应失效(被踢)
    assert client.get("/api/invite", headers=hdrs(token)).status_code == 401
    # 新密码登录
    r = client.post("/api/login", json={"login": uname, "password": "NewPass456"})
    assert r.status_code == 200 and r.json().get("token")


def hdrs(token):
    return {"Authorization": "Bearer " + token}
