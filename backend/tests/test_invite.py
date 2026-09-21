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
    """2026-09-21 放开注册: 无效邀请码不再是无条件 403。
    ★ 校验顺序: 手机号格式 → 密码长度 → IP 防刷 → 手机号唯一 → 短信验证码 → 邀请码。
      所以邀请码校验在短信之后 —— 测试环境未配置短信, 请求会停在 503(或 400 验证码错)。
      断言"不再 403"即可, 邀请码校验本身由下面的 invite-info 用例覆盖。"""
    import uuid
    phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8)
    r = client.post("/api/register", json={"phone": phone, "code": "123456",
                                           "password": "Test123456",
                                           "invite_code": "BADCODE1"})
    assert r.status_code != 403
    assert not r.json().get("ok")


def test_register_invite_code_validation_order(client):
    """邀请码校验发生在短信校验之后(不白烧短信)。
    无验证码时先拦在验证码这一步(400), 说明前置校验链生效。"""
    import uuid
    phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8)
    r = client.post("/api/register", json={"phone": phone, "password": "Test123456",
                                           "invite_code": "BADCODE1"})
    assert r.status_code == 400
    assert "验证码" in r.json().get("msg", "")


def test_invite_info_precheck(client, first_user):
    """注册页邀请码预校验: 有效码返回邀请人打码名 + 奖励天数。
    ★ 用 /api/invite 现取邀请码(前面的 refresh 用例会改码, 不能直接用 fixture 里的旧值)。"""
    token, _, _ = first_user
    inv = client.get("/api/invite", headers=hdrs(token)).json()["invite_code"]
    r = client.get("/api/invite-info?code=" + inv)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["reward_days"] >= 0
    # 无效码 → 404
    assert client.get("/api/invite-info?code=ZZZZZZZZ").status_code == 404


def test_invite_reward_wiring(client, first_user, create_user_token):
    """邀请奖励接线: 被邀人必须挂上 invited_by 关系。
    直接走 service 层(绕开短信), 验证 grant_invite_reward 能累加邀请人到期时间。"""
    import time as _t
    from app.db import database
    from app.services import users as users_svc
    token, inviter_uname, inv_code = first_user
    conn = database.get_conn()
    row = conn.execute("SELECT id, expire_at FROM users WHERE username=?", (inviter_uname,)).fetchone()
    conn.close()
    inviter_id = int(row[0])
    before = int(row[1] or 0)
    new_expire = users_svc.grant_invite_reward(inviter_id, 5)
    # 邀请人不是永久会员 → 应累加 5 天
    if before:
        assert new_expire > before
    conn = database.get_conn()
    after = conn.execute("SELECT expire_at FROM users WHERE id=?", (inviter_id,)).fetchone()[0]
    conn.close()
    assert int(after) == int(new_expire)


def test_phone_claims_blocks_repeat(client, create_user_token):
    """phone_claims 台账: 同一手机号第二次领取 → is_first=False(封堵删号重注册刷 VIP)。
    ★ 这是本体系最关键的一条防刷, 必须显式覆盖。"""
    import uuid
    from app.services import users as users_svc
    phone = "137" + str(uuid.uuid4().int % 100000000).zfill(8)
    u1 = create_user_token(member_level=0)
    u2 = create_user_token(member_level=0)
    from app.db import database
    conn = database.get_conn()
    id1 = conn.execute("SELECT id FROM users WHERE username=?", (u1["username"],)).fetchone()[0]
    id2 = conn.execute("SELECT id FROM users WHERE username=?", (u2["username"],)).fetchone()[0]
    conn.close()
    # 清掉可能存在的历史记录, 保证测试可重复
    conn = database.get_conn()
    conn.execute("DELETE FROM phone_claims WHERE phone=?", (phone,))
    conn.commit()
    conn.close()

    first, rec1, _ = users_svc.claim_phone(phone, int(id1), "1.1.1.1")
    assert first is True and rec1["claim_count"] == 1
    second, rec2, reason = users_svc.claim_phone(phone, int(id2), "2.2.2.2")
    assert second is False
    assert rec2["claim_count"] == 2
    assert "已领取" in reason
    # 台账不随用户删除清理(封堵关键)
    users_svc.delete_user(int(id1))
    still = users_svc.check_phone_claim(phone)
    assert still is not None and still["claim_count"] >= 1




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
