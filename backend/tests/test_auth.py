# -*- coding: utf-8 -*-
"""认证相关测试: 注册(手机号+验证码)/登录/鉴权/改密/踢下线"""
import uuid


def test_register_requires_phone_and_code(client):
    """2026-09-21 放开注册: 手机号注册须带验证码。
    缺验证码 → 400 (不是 403, 注册本身已开放)"""
    phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8)
    r = client.post("/api/register", json={"phone": phone, "password": "Test123456"})
    assert r.status_code == 400
    assert "验证码" in r.json().get("msg", "")


def test_register_bad_phone_format(client):
    """手机号格式错 → 400"""
    r = client.post("/api/register", json={"phone": "12345", "code": "123456",
                                           "password": "Test123456"})
    assert r.status_code == 400
    assert "手机号" in r.json().get("msg", "")


def test_register_short_password(client):
    """密码 <6 位 → 400(在短信校验之前拦截, 不浪费短信)"""
    phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8)
    r = client.post("/api/register", json={"phone": phone, "code": "123456",
                                           "password": "123"})
    assert r.status_code == 400
    assert "6 位" in r.json().get("msg", "") or "6位" in r.json().get("msg", "")


def test_register_config_endpoint(client):
    """注册页配置: 开放状态 + 赠送天数(前端不硬编码)"""
    r = client.get("/api/register/config")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert "open" in j and "gift_days" in j
    assert j["gift_days"] >= 0


def test_invite_info_invalid_code(client):
    """邀请码预校验: 无效码 404"""
    r = client.get("/api/invite-info?code=ZZZZZZZZ")
    assert r.status_code == 404


# 旧行为(2026-08-25 起注册关闭)已由放开注册取代, 保留一个回归断言防止误关:
def test_register_not_hard_disabled(client):
    """注册端点不再无条件 403(避免回退到「停止注册」状态)"""
    r = client.post("/api/register", json={})
    assert r.status_code != 403


def test_login_ok(client, create_user_token):
    """正确密码登录(用独立建的用户,避免踢掉 first_user session token 影响其他测试)"""
    u = create_user_token()
    r = client.post("/api/login", json={"login": u["username"], "password": u["password"]})
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


def test_login_revokes_previous_token(client, create_user_token):
    """单点登录: 同一账号再次登录后, 旧 token 立即失效, 新 token 有效。"""
    u = create_user_token()
    r = client.post("/api/login", json={"login": u["username"], "password": u["password"]})
    assert r.status_code == 200
    token1 = r.json()["token"]
    # token1 当前有效
    assert client.get("/api/invite", headers=hdrs(token1)).status_code == 200
    # 再次登录(同账号同密码)
    r = client.post("/api/login", json={"login": u["username"], "password": u["password"]})
    assert r.status_code == 200
    d = r.json()
    token2 = d.get("token")
    assert token2 and token2 != token1
    # token1 失效(被踢)
    assert client.get("/api/invite", headers=hdrs(token1)).status_code == 401
    # token2 有效
    assert client.get("/api/invite", headers=hdrs(token2)).status_code == 200


def test_login_failed_does_not_revoke(client, create_user_token):
    """密码错误时不应踢掉该用户的旧会话(避免错误登录导致在线用户被误踢)"""
    u = create_user_token()
    token1 = u["token"]
    # 错误密码登录
    r = client.post("/api/login", json={"login": u["username"], "password": "WrongPass1"})
    assert r.status_code == 401
    # token1 仍然有效(没被踢)
    assert client.get("/api/invite", headers=hdrs(token1)).status_code == 200


def test_no_token_401(client):
    """无 token 访问受保护接口 401"""
    assert client.get("/api/stocks").status_code == 401
    assert client.get("/api/invite").status_code == 401


def test_change_password_flow(client, create_user_token):
    """改密: 旧密码错400 / 成功后踢下线 / 新密码可登录。"""
    u = create_user_token()
    token = u["token"]
    # 旧密码错误
    r = client.post("/api/change-password", json={
        "old_password": "wrong", "new_password": "NewPass456"}, headers=hdrs(token))
    assert r.status_code == 400
    # 新密码太短
    r = client.post("/api/change-password", json={
        "old_password": u["password"], "new_password": "123"}, headers=hdrs(token))
    assert r.status_code == 400
    # 正常改密
    r = client.post("/api/change-password", json={
        "old_password": u["password"], "new_password": "NewPass456"}, headers=hdrs(token))
    assert r.status_code == 200 and r.json().get("ok")
    # 旧 token 应失效(被踢)
    assert client.get("/api/invite", headers=hdrs(token)).status_code == 401
    # 新密码登录
    r = client.post("/api/login", json={"login": u["username"], "password": "NewPass456"})
    assert r.status_code == 200 and r.json().get("token")


def test_register_ip_limit_functions_healthy(create_user_token):
    """注册防刷函数健全性: register_allowed / register_ip_day_allowed 在正常 IP 下放行。
    2026-09-21 放开注册后这两个函数重新被注册接口调用, 需保证语义正确。"""
    from app.services import cache_store
    from app.services import security
    test_ip = "9.9.9.99_inv"
    cache_store.store.delete("reg:%s" % test_ip)
    cache_store.store.delete("regip:%s" % test_ip)
    assert security.register_allowed(test_ip) is True
    assert security.register_ip_day_allowed(test_ip) is True
    cache_store.store.delete("reg:%s" % test_ip)
    cache_store.store.delete("regip:%s" % test_ip)


def hdrs(token):
    return {"Authorization": "Bearer " + token}


# ---------- AI预测静态报告 Nginx auth_request 校验 (2026-08-30) ----------
def test_aipick_auth_check_no_token(client):
    """无 token → 401 (Nginx auth_request 拒发静态文件)"""
    r = client.get("/api/aipick/auth-check")
    assert r.status_code == 401


def test_aipick_auth_check_free_user(client, second_user):
    """免费试用用户(member_level=0) → 403 (仅限 VIP/付费)"""
    token, _ = second_user
    r = client.get("/api/aipick/auth-check", headers=hdrs(token))
    assert r.status_code == 403
    assert "仅限 VIP/付费" in r.json().get("msg", "")


def test_aipick_auth_check_vip(client, vip_user):
    """VIP 用户(member_level=2) → 200 放行"""
    token, _, _ = vip_user
    r = client.get("/api/aipick/auth-check", headers=hdrs(token))
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_aipick_auth_check_admin(client, create_user_token):
    """管理员 → 200 放行 (管理员豁免)"""
    u = create_user_token()
    from app.db import database
    conn = database.get_conn()
    conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["uid"],))
    conn.commit()
    conn.close()
    r = client.get("/api/aipick/auth-check", headers=hdrs(u["token"]))
    assert r.status_code == 200


def test_aipick_auth_check_query_token(client, vip_user):
    """query token 方式同样放行(分享链接 ?token=xxx 场景)"""
    token, _, _ = vip_user
    r = client.get("/api/aipick/auth-check?token=" + token)
    assert r.status_code == 200


def test_aipick_auth_check_xoriginal_headers(client, vip_user):
    """Nginx auth_request 子请求透传头场景(X-Original-Authorization / X-Original-URI):
    - Bearer token 经 X-Original-Authorization 透传 → 放行
    - ?token= 经 X-Original-URI 透传 → 放行
    - 两个头都无 token → 401
    """
    token, _, _ = vip_user
    # Bearer 经透传头
    r = client.get("/api/aipick/auth-check",
                   headers={"X-Original-Authorization": "Bearer " + token})
    assert r.status_code == 200
    # query token 经透传头
    r = client.get("/api/aipick/auth-check",
                   headers={"X-Original-URI": "/aipick/latest.html?token=" + token})
    assert r.status_code == 200
    # 无 token → 401
    r = client.get("/api/aipick/auth-check",
                   headers={"X-Original-URI": "/aipick/latest.html"})
    assert r.status_code == 401
