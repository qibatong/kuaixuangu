# -*- coding: utf-8 -*-
"""推送提醒测试: 全 Mock 网络, 覆盖消息构造/渠道发送/失败隔离/去重/接口触发"""
import time

import pytest

from app.core import config
from app.services import notify, scorer

# 与 conftest MOCK_RAW 评分后结构一致的假结果
RESULT = [
    {"code": "600001", "name": "测试甲", "probability": 92.5, "confidence": 81.0,
     "bidChange": 3.2, "bidAmt": 2800.0, "circulationMV": 40.0,
     "industry": "软件服务", "concept": "AI概念"},
    {"code": "000002", "name": "测试乙", "probability": 88.0, "confidence": 79.0,
     "bidChange": 5.1, "bidAmt": 1600.0, "circulationMV": 50.0,
     "industry": "医药", "concept": "创新药"},
    {"code": "300003", "name": "测试丙", "probability": 76.3, "confidence": 70.0,
     "bidChange": 2.2, "bidAmt": 900.0, "circulationMV": 60.0,
     "industry": "半导体", "concept": "芯片"},
]
FILTERS = {"markets": ["sh_sz"], "bidMin": 0}


def _clean_cfg(monkeypatch):
    """清空所有推送渠道配置"""
    monkeypatch.setattr(config, "NOTIFY_FEISHU_WEBHOOK", "")
    monkeypatch.setattr(config, "NOTIFY_SERVERCHAN_KEY", "")
    monkeypatch.setattr(config, "NOTIFY_WECHAT_WEBHOOK", "")


# ---------- 消息构造 ----------
def test_build_message_contains_core_fields():
    text = notify.build_message(RESULT, FILTERS)
    assert "快选" in text and "竞价选股" in text
    assert "sh_sz" in text and "入选 3 只" in text
    assert "600001" in text and "测试甲" in text and "92.5%" in text
    assert "+3.20%" in text and "2800万" in text and "40亿" in text
    # 按概率降序展示
    assert text.index("600001") < text.index("000002") < text.index("300003")


def test_build_message_top_n_limit():
    text = notify.build_message(RESULT, FILTERS, limit=2)
    assert "展示 Top2" in text
    assert "300003" not in text  # 第 3 只被截断


def test_build_message_empty_result():
    text = notify.build_message([], FILTERS)
    assert "无符合策略" in text and "入选 0 只" in text


# ---------- 渠道配置与发送 ----------
def test_no_channel_configured_returns_empty(monkeypatch):
    _clean_cfg(monkeypatch)
    sent = {}
    monkeypatch.setattr(notify, "_post_json", lambda *a, **k: sent.setdefault("json", True))
    monkeypatch.setattr(notify, "_post_form", lambda *a, **k: sent.setdefault("form", True))
    r = notify.push_result(RESULT, FILTERS)
    assert r == {}
    assert sent == {}  # 未配置渠道不发任何请求


def test_feishu_payload(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_FEISHU_WEBHOOK", "https://open.feishu.cn/hook/fake")
    captured = {}

    def fake_post_json(url, payload, timeout=None):
        captured["url"] = url
        captured["payload"] = payload
        return 200, '{"errcode":0,"msg":"ok"}'

    monkeypatch.setattr(notify, "_post_json", fake_post_json)
    monkeypatch.setattr(notify, "_dedup", lambda *a, **k: False)
    r = notify.push_result(RESULT, FILTERS)
    assert r["feishu"]["ok"] is True
    assert captured["url"] == "https://open.feishu.cn/hook/fake"
    assert captured["payload"]["msg_type"] == "text"
    assert "快选" in captured["payload"]["content"]["text"]


def test_serverchan_payload(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_SERVERCHAN_KEY", "SCT_fake_key")
    captured = {}

    def fake_post_form(url, data, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return 200, '{"code":0,"message":"ok"}'

    monkeypatch.setattr(notify, "_post_form", fake_post_form)
    monkeypatch.setattr(notify, "_dedup", lambda *a, **k: False)
    r = notify.push_result(RESULT, FILTERS)
    assert r["serverchan"]["ok"] is True
    assert captured["url"] == "https://sctapi.ftqq.com/SCT_fake_key.send"
    assert "title" in captured["data"] and "desp" in captured["data"]
    assert "快选" in captured["data"]["desp"]


def test_wecom_payload(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_WECHAT_WEBHOOK", "https://qyapi.weixin.qq.com/hook/fake")
    captured = {}

    def fake_post_json(url, payload, timeout=None):
        captured["payload"] = payload
        return 200, '{"errcode":0,"errmsg":"ok"}'

    monkeypatch.setattr(notify, "_post_json", fake_post_json)
    monkeypatch.setattr(notify, "_dedup", lambda *a, **k: False)
    r = notify.push_result(RESULT, FILTERS)
    assert r["wecom"]["ok"] is True
    assert captured["payload"]["msgtype"] == "text"
    assert captured["payload"]["text"]["content"].startswith("【快选")


# ---------- 失败处理 ----------
def test_http_error_returns_fail(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_FEISHU_WEBHOOK", "https://open.feishu.cn/hook/fake")
    monkeypatch.setattr(notify, "_post_json", lambda *a, **k: (500, "boom"))
    monkeypatch.setattr(notify, "_dedup", lambda *a, **k: False)
    r = notify.push_result(RESULT, FILTERS)
    assert r["feishu"]["ok"] is False


def test_business_code_error_returns_fail(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_SERVERCHAN_KEY", "SCT_fake_key")
    monkeypatch.setattr(notify, "_post_form", lambda *a, **k: (200, '{"code":40001,"message":"bad"}'))
    monkeypatch.setattr(notify, "_dedup", lambda *a, **k: False)
    r = notify.push_result(RESULT, FILTERS)
    assert r["serverchan"]["ok"] is False


def test_exception_isolated_per_channel(monkeypatch):
    """一个渠道抛异常, 另一个渠道正常: 各渠道结果独立, 不向上抛"""
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_FEISHU_WEBHOOK", "https://open.feishu.cn/hook/fake")
    monkeypatch.setattr(config, "NOTIFY_SERVERCHAN_KEY", "SCT_fake_key")
    monkeypatch.setattr(notify, "_dedup", lambda *a, **k: False)

    def boom(*a, **k):
        raise RuntimeError("network down")

    def ok_form(*a, **k):
        return 200, '{"code":0}'

    monkeypatch.setattr(notify, "_post_json", boom)
    monkeypatch.setattr(notify, "_post_form", ok_form)
    r = notify.push_result(RESULT, FILTERS)
    assert r["feishu"]["ok"] is False and "network down" in r["feishu"]["msg"]
    assert r["serverchan"]["ok"] is True


# ---------- 去重 ----------
def test_dedup_same_content_skips(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_FEISHU_WEBHOOK", "https://open.feishu.cn/hook/fake")
    calls = {"n": 0}

    def fake_post_json(*a, **k):
        calls["n"] += 1
        return 200, '{"errcode":0}'

    monkeypatch.setattr(notify, "_post_json", fake_post_json)
    monkeypatch.setattr(notify, "_last_push", {})   # 清空历史
    # 第一次: 发送; 第二次(相同内容, 同一时刻): 去重跳过
    notify.push_result(RESULT, FILTERS)
    notify.push_result(RESULT, FILTERS)
    assert calls["n"] == 1


def test_dedup_differs_after_window(monkeypatch):
    _clean_cfg(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_FEISHU_WEBHOOK", "https://open.feishu.cn/hook/fake")
    monkeypatch.setattr(config, "NOTIFY_DEDUP_SECONDS", 120)
    calls = {"n": 0}

    def fake_post_json(*a, **k):
        calls["n"] += 1
        return 200, '{"errcode":0}'

    monkeypatch.setattr(notify, "_post_json", fake_post_json)
    monkeypatch.setattr(notify, "_last_push", {})
    t0 = time.time()
    notify.push_result(RESULT, FILTERS)          # now=t0
    notify._last_push[list(notify._last_push)[0]] = t0 - 200   # 模拟已过窗口
    notify.push_result(RESULT, FILTERS)
    assert calls["n"] == 2


# ---------- 接口触发 ----------
def test_lock_triggers_async_push(client, first_user, monkeypatch):
    token, _, _ = first_user
    calls = {"n": 0}

    def fake_push(result, filters=None):
        calls["n"] += 1
        assert len(result) > 0

    monkeypatch.setattr(notify, "push_result_async", fake_push)
    monkeypatch.setattr(scorer, "bj_now", lambda: ("2099-01-01", "09:25:00", True))
    r = client.get("/api/stocks?action=lock&markets=sh_sz", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200 and r.json().get("ok")
    assert calls["n"] == 1


def test_filter_does_not_trigger_push(client, first_user, monkeypatch):
    token, _, _ = first_user
    calls = {"n": 0}
    monkeypatch.setattr(notify, "push_result_async", lambda result, filters=None: calls.__setitem__("n", calls["n"] + 1))
    r = client.get("/api/stocks?action=filter&markets=sh_sz", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert calls["n"] == 0


def test_push_failure_does_not_break_lock(client, first_user, monkeypatch):
    """推送后台线程抛异常时, lock 接口仍返回 200"""
    token, _, _ = first_user

    def boom(result, filters=None):
        raise RuntimeError("thread boom")

    monkeypatch.setattr(notify, "push_result_async", boom)
    monkeypatch.setattr(scorer, "bj_now", lambda: ("2099-01-01", "09:25:00", True))
    r = client.get("/api/stocks?action=lock&markets=sh_sz", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200 and r.json().get("ok")


def test_lock_empty_result_no_push(client, first_user, monkeypatch):
    """无入选标的时不推送"""
    token, _, _ = first_user
    calls = {"n": 0}
    monkeypatch.setattr(notify, "push_result_async", lambda result, filters=None: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(scorer, "bj_now", lambda: ("2099-01-01", "09:25:00", True))
    # 用严格筛选(流通市值下限极高)让结果为空
    r = client.get("/api/stocks?action=lock&markets=sh_sz&floatMvFloor=5000", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert calls["n"] == 0
