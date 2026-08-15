# -*- coding: utf-8 -*-
"""连板梯队历史落库/回看/API date 测试"""
import json
import os
import sqlite3

from app.services import kpl


def test_save_query_ladder_history(client, monkeypatch):
    """连板梯队落库 + 按日期回看"""
    fake_all = {1: [{"code": "600487", "name": "亨通光电", "limitTime": 100}],
                2: [{"code": "600519", "name": "贵州茅台", "limitTime": 200}],
                3: [], 4: [], 5: []}
    monkeypatch.setattr(kpl, "fetch_ladder_all", lambda: fake_all)
    n = kpl.save_ladder_history("2026-08-13")
    assert n == 2
    d = kpl.query_ladder_history("2026-08-13")
    assert d.get(1) and d[1][0]["name"] == "亨通光电"
    assert d.get(2) and d[2][0]["code"] == "600519"
    # 无数据日期返回空 dict
    assert kpl.query_ladder_history("2026-01-01") == {}


def test_save_ladder_empty(client, monkeypatch):
    """抓取为空不落库"""
    monkeypatch.setattr(kpl, "fetch_ladder_all", lambda: {1: [], 2: [], 3: [], 4: [], 5: []})
    assert kpl.save_ladder_history("2026-08-13") == 0


def test_api_ladder_date(client, first_user, monkeypatch):
    """API ladder?date= 读历史 + 周末对齐"""
    token, _, _ = first_user
    fake_all = {1: [{"code": "600487", "name": "亨通光电", "limitTime": 100}], 2: [], 3: [], 4: [], 5: []}
    monkeypatch.setattr(kpl, "fetch_ladder_all", lambda: fake_all)
    kpl.save_ladder_history("2026-08-14")
    # 预置 daily_sector_top 用于日期对齐
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("DELETE FROM daily_sector_top")
    conn.execute("INSERT INTO daily_sector_top (date, source, boards, ts) VALUES ('2026-08-14','kpl',?,0)",
                 (json.dumps([{"rank": 1, "name": "算力"}]),))
    conn.commit()
    conn.close()
    # 指定日期
    r = client.get("/api/kpl/ladder?date=2026-08-14",
                   headers={"Authorization": "Bearer " + token})
    d = r.json()
    # JSON 序列化后 pid key 为字符串 "1"
    assert d.get("ok") and d.get("ladder", {}).get("1", [])[0]["name"] == "亨通光电"
    # 周六自动对齐 8.14
    r2 = client.get("/api/kpl/ladder?date=2026-08-15",
                    headers={"Authorization": "Bearer " + token})
    d2 = r2.json()
    assert d2.get("date") == "2026-08-14"
    assert d2.get("ladder", {}).get("1", [])[0]["name"] == "亨通光电"