# -*- coding: utf-8 -*-
"""选股接口测试: mock 数据源, 不依赖外部网络"""
import time

import pytest

from app.services import fetcher, scorer
from conftest import MOCK_RAW


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def test_ping(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=ping", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and "before930" in d


def test_filter_returns_stocks(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=filter&markets=sh_sz&bid_min=0", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert len(d.get("list", [])) > 0
    # 每条记录必含核心字段
    for s in d["list"]:
        assert s["code"] and s["name"]
        assert s["probability"] >= 0
        assert "bidChange" in s and "realChange" in s


def test_filter_invalid_action(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=hack", headers=hdrs(token))
    assert r.status_code == 400


def test_lock_after_930_rejected(client, first_user, monkeypatch):
    """9:30 后 lock 应被拒(403)"""
    token, _, _ = first_user

    def fake_bj_now():
        return ("2099-01-01", "15:00:00", False)

    monkeypatch.setattr(scorer, "bj_now", fake_bj_now)
    r = client.get("/api/stocks?action=lock&markets=sh_sz", headers=hdrs(token))
    assert r.status_code == 403


def test_lock_before_930_ok(client, first_user, monkeypatch):
    """9:30 前 lock 成功"""
    token, _, _ = first_user

    def fake_bj_now():
        return ("2099-01-01", "09:25:00", True)

    monkeypatch.setattr(scorer, "bj_now", fake_bj_now)
    r = client.get("/api/stocks?action=lock&markets=sh_sz", headers=hdrs(token))
    assert r.status_code == 200
    assert r.json().get("ok")


def test_filter_with_ratio(client, first_user, monkeypatch):
    """昨日成交额 map 有值时(竞价窗口内), 竞价/昨比应算出"""
    token, _, _ = first_user
    # 手动让 fetch_yesterday_amounts 返回 [T日, T-1日] 有值 pair, 且处于竞价窗口
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    import tests.conftest as ct
    orig = fetcher.fetch_yesterday_amounts
    fetcher.fetch_yesterday_amounts = lambda codes: {s["f12"]: [10000.0, 8000.0] for s in MOCK_RAW}
    try:
        r = client.get("/api/stocks?action=filter&markets=sh_sz", headers=hdrs(token))
        assert r.status_code == 200
        for s in r.json()["list"]:
            assert s["bidRatio"] is not None and s["bidRatio"] >= 0
    finally:
        fetcher.fetch_yesterday_amounts = orig
