# -*- coding: utf-8 -*-
"""人气热榜多数据源服务测试"""
import json
import os
import sqlite3

from app.services import hot_rank, kpl


def test_ths_hot_rank_parses(monkeypatch):
    """同花顺热榜解析: 正常数据返回 code/name/change/rank"""
    fake = {"status_code": 0, "data": {"stock_list": [
        {"market": 17, "code": "600487", "rate": "478995.0", "rise_and_fall": 10.0087, "name": "亨通光电"},
        {"market": 17, "code": "600519", "rate": "123.0", "rise_and_fall": -0.98, "name": "贵州茅台"},
    ]}}
    monkeypatch.setattr(hot_rank, "_get_json", lambda *a, **k: fake)
    d = hot_rank.fetch_ths_hot_rank(10)
    assert len(d) == 2
    assert d[0]["code"] == "600487" and d[0]["name"] == "亨通光电"
    assert d[0]["change"] == 10.01 and d[0]["rank"] == 1
    assert d[1]["change"] == -0.98


def test_ths_hot_rank_empty(monkeypatch):
    """同花顺热榜空数据返回 []"""
    monkeypatch.setattr(hot_rank, "_get_json", lambda *a, **k: {"status_code": 0, "data": {}})
    assert hot_rank.fetch_ths_hot_rank() == []


def test_em_hot_rank_parses(monkeypatch):
    """东财人气榜: emappdata + ulist 拼行情"""
    fake_rank = {"data": [{"sc": "SH600487", "rk": 1, "rc": 0}, {"sc": "SZ300017", "rk": 2, "rc": 1}]}
    fake_quotes = {"data": {"diff": [
        {"f12": "600487", "f14": "亨通光电", "f3": 10.01},
        {"f12": "300017", "f14": "网宿科技", "f3": 20.01},
    ]}}
    monkeypatch.setattr(hot_rank, "_get_json",
                        lambda *a, **k: fake_rank if "stockrank" in str(a[0]) else fake_quotes)
    d = hot_rank.fetch_em_hot_rank(10)
    assert len(d) == 2
    assert d[0]["code"] == "600487" and d[0]["name"] == "亨通光电"
    assert d[0]["change"] == 10.01 and d[0]["rank"] == 1
    assert d[1]["hisRankChange"] == 1


def test_fetch_hot_rank_kpl_default(monkeypatch):
    """默认 source=kpl 走 kpl.fetch_hot_rank"""
    monkeypatch.setattr(kpl, "fetch_hot_rank", lambda: [{"code": "1", "name": "A", "change": 1.0, "rank": 1}])
    d = hot_rank.fetch_hot_rank()
    assert len(d) == 1 and d[0]["code"] == "1"


def test_fetch_hot_rank_invalid_source():
    """未知数据源返回 []"""
    assert hot_rank.fetch_hot_rank("xxx") == []


def test_api_hot_rank_source(client, first_user, monkeypatch):
    """API /api/kpl/hot-rank?source= 三源支持"""
    token, _, _ = first_user
    monkeypatch.setattr(hot_rank, "fetch_hot_rank", lambda src="kpl": [
        {"code": "600487", "name": "亨通光电", "change": 10.01, "rank": 1}])
    for src in ("kpl", "em", "ths"):
        r = client.get("/api/kpl/hot-rank?source=" + src,
                       headers={"Authorization": "Bearer " + token})
        assert r.status_code == 200
        d = r.json()
        assert d.get("ok") and d.get("source") == src
        assert d.get("list") and d["list"][0]["name"] == "亨通光电"


def test_hot_rank_history_save_and_query(client, monkeypatch):
    """人气榜历史落库 + 按日期回看"""
    monkeypatch.setattr(hot_rank, "fetch_hot_rank", lambda src="kpl", top_n=50: [
        {"code": "600487", "name": "亨通光电", "change": 10.01, "rank": 1}])
    n = hot_rank.save_hot_rank_history("2026-08-13", source="kpl")
    assert n == 1
    d = hot_rank.query_hot_rank_history("2026-08-13", source="kpl")
    assert len(d) == 1 and d[0]["code"] == "600487"
    # 不同 source 隔离
    d2 = hot_rank.query_hot_rank_history("2026-08-13", source="em")
    assert d2 == []


def test_api_hot_rank_history(client, first_user, monkeypatch):
    """API hot-rank?date= 读历史表"""
    token, _, _ = first_user
    monkeypatch.setattr(hot_rank, "fetch_hot_rank", lambda src="kpl", top_n=50: [
        {"code": "600487", "name": "亨通光电", "change": 10.01, "rank": 1}])
    hot_rank.save_hot_rank_history("2026-08-12", source="kpl")
    r = client.get("/api/kpl/hot-rank?date=2026-08-12",
                   headers={"Authorization": "Bearer " + token})
    d = r.json()
    assert d.get("ok") and d.get("date") == "2026-08-12"
    assert d.get("list") and d["list"][0]["name"] == "亨通光电"
    # 无历史日期返回空
    r2 = client.get("/api/kpl/hot-rank?date=2026-01-01",
                    headers={"Authorization": "Bearer " + token})
    assert r2.json().get("list") == []