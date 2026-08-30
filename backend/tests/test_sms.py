# -*- coding: utf-8 -*-
"""短信验证码 (2026-08-30 阿里云号码认证·短信认证)"""
import uuid

from app.services import security, sms_verify, users


def test_sms_verify_phone_regex():
    """手机号校验: 11 位大陆号"""
    from app.api import sms as sms_api
    assert sms_api._PHONE_RE.match("13800138000")
    assert not sms_api._PHONE_RE.match("12345")
    assert not sms_api._PHONE_RE.match("23800138000")


def test_sms_send_not_configured(client, first_user, monkeypatch):
    """未配置 AK/签名/模板 → 503 提示(不影响其他接口)"""
    monkeypatch.delenv("ALIYUN_AK_ID", raising=False)
    monkeypatch.delenv("ALIYUN_AK_SECRET", raising=False)
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "SMS_SIGN_NAME", "")
    monkeypatch.setattr(cfg, "SMS_TEMPLATE_CODE", "")
    token, _, _ = first_user
    r = client.post("/api/sms/send", json={"phone": "13800138000"},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 503
    assert "未配置" in r.json().get("msg", "")


def test_sms_send_bad_phone(client, first_user):
    """手机号格式错误 → 400"""
    token, _, _ = first_user
    r = client.post("/api/sms/send", json={"phone": "123"},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 400


def test_sms_send_ok(client, first_user, monkeypatch):
    """配置就绪 + mock SDK → 发送成功"""
    monkeypatch.setenv("ALIYUN_AK_ID", "test_ak")
    monkeypatch.setenv("ALIYUN_AK_SECRET", "test_sk")
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "SMS_SIGN_NAME", "签名")
    monkeypatch.setattr(cfg, "SMS_TEMPLATE_CODE", "TPL001")
    monkeypatch.setattr(sms_verify, "send_code", lambda *a, **k: (True, "OK"))
    token, _, _ = first_user
    r = client.post("/api/sms/send", json={"phone": "13800138001", "scene": "register"},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert r.json().get("ok")


def test_sms_verify_ok(client, first_user, monkeypatch):
    """校验通过"""
    monkeypatch.setattr(sms_verify, "check_code", lambda *a, **k: (True, "OK"))
    token, _, _ = first_user
    r = client.post("/api/sms/verify", json={"phone": "13800138000", "code": "123456"},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert r.json().get("ok")


def test_sms_verify_bad_code(client, first_user, monkeypatch):
    """校验失败 → 400"""
    monkeypatch.setattr(sms_verify, "check_code", lambda *a, **k: (False, "验证码错误"))
    token, _, _ = first_user
    r = client.post("/api/sms/verify", json={"phone": "13800138000", "code": "000000"},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 400


def test_sms_can_send_rate_limit(client, monkeypatch):
    """限流: 同手机号 60s 内仅 1 次; 同 IP 60s 10 次"""
    from app.core import config
    monkeypatch.setattr(config, "SMS_SEND_INTERVAL", 60)
    ok1, _ = sms_verify.can_send("13900139000", "1.2.3.4", 60)
    assert ok1
    ok2, reason = sms_verify.can_send("13900139000", "1.2.3.4", 60)
    assert not ok2 and "频繁" in reason
    # 同 IP 不同号 10 次后拦截
    for i in range(12):
        sms_verify.can_send("138%08d" % i, "9.9.9.9", 60)
    ok, reason = sms_verify.can_send("13999999999", "9.9.9.9", 60)
    assert not ok and "过多" in reason


def test_sms_send_code_custom(client, first_user, monkeypatch):
    """自定义验证码场景: send_code 收到 code 参数"""
    captured = {}
    def fake_send(phone, scene="", code=None, interval=60, valid_time=5, out_id=""):
        captured["phone"] = phone
        captured["code"] = code
        return True, "OK"
    monkeypatch.setattr(sms_verify, "send_code", fake_send)
    token, _, _ = first_user
    r = client.post("/api/sms/send", json={"phone": "13800138002", "scene": "reset"},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert captured.get("phone") == "13800138002"


# ---------- 找回密码-短信验证码 (2026-08-30) ----------
# 注意: reset 用例会改密码/消费验证码标记, 必须用 create_user_token 独立用户
# 避免污染 session 级 first_user(共享手机号)导致用例间相互影响

def test_forgot_phone_send_unbound(client, first_user):
    """未绑定手机号 → 404 不发送(省短信费)"""
    r = client.post("/api/forgot-phone/send", json={"phone": "13800001111"})
    assert r.status_code == 404
    assert "未绑定" in r.json().get("msg", "")


def test_forgot_phone_send_bad_phone(client, first_user):
    """手机号格式错误 → 400"""
    r = client.post("/api/forgot-phone/send", json={"phone": "12345"})
    assert r.status_code == 400


def test_forgot_phone_send_ok(client, create_user_token, monkeypatch):
    """已绑定手机号 + mock send_code → 发送成功(scene=forgot)"""
    captured = {}
    def fake_send(phone, scene="", code=None, interval=60, valid_time=5, out_id=""):
        captured["phone"] = phone
        captured["scene"] = scene
        return True, "OK"
    monkeypatch.setattr(sms_verify, "send_code", fake_send)
    u = create_user_token()   # 独立用户(随机手机号, 已绑定)
    r = client.post("/api/forgot-phone/send", json={"phone": u["phone"]})
    assert r.status_code == 200
    assert r.json().get("ok")
    assert captured.get("phone") == u["phone"]
    assert captured.get("scene") == "forgot"


def test_reset_by_phone_ok(client, create_user_token, monkeypatch):
    """短信校验通过 → 密码重置成功 + 旧密码失效 + 会话踢下线"""
    monkeypatch.setattr(sms_verify, "check_code", lambda *a, **k: (True, "OK"))
    u = create_user_token()
    r = client.post("/api/reset-by-phone",
                    json={"phone": u["phone"], "code": "123456", "new_password": "NewPass123"})
    assert r.status_code == 200
    assert r.json().get("ok")
    db_u = users.find_user_by_phone(u["phone"])
    assert security.verify_password("NewPass123", db_u["password_hash"])
    assert not security.verify_password("Test123456", db_u["password_hash"])


def test_reset_by_phone_bad_code(client, create_user_token, monkeypatch):
    """验证码错误 → 400 不改密"""
    monkeypatch.setattr(sms_verify, "check_code", lambda *a, **k: (False, "验证码错误"))
    u = create_user_token()
    r = client.post("/api/reset-by-phone",
                    json={"phone": u["phone"], "code": "000000", "new_password": "NewPass123"})
    assert r.status_code == 400
    db_u = users.find_user_by_phone(u["phone"])
    assert security.verify_password("Test123456", db_u["password_hash"])


def test_reset_by_phone_unbound(client, first_user):
    """未绑定手机号 → 404"""
    r = client.post("/api/reset-by-phone",
                    json={"phone": "13800009999", "code": "123456", "new_password": "NewPass123"})
    assert r.status_code == 404


def test_reset_by_phone_replay(client, create_user_token, monkeypatch):
    """防重放: 同一验证码消费标记后不可再次重置"""
    monkeypatch.setattr(sms_verify, "check_code", lambda *a, **k: (True, "OK"))
    u = create_user_token()
    r1 = client.post("/api/reset-by-phone",
                     json={"phone": u["phone"], "code": "123456", "new_password": "NewPass123"})
    assert r1.status_code == 200
    r2 = client.post("/api/reset-by-phone",
                     json={"phone": u["phone"], "code": "123456", "new_password": "Another123"})
    assert r2.status_code == 400
    assert "已使用" in r2.json().get("msg", "")
