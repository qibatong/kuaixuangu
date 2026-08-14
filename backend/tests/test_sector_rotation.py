# -*- coding: utf-8 -*-
"""板块轮动历史服务测试: 抓取落库 + 查询历史 + 多窗口排名"""
import json
import os

import pytest

from app.services import kpl, sector_rotation


SAMPLE_BOARDS = [
    {"boardCode": "BK0001", "name": "医药",      "strength": 9969, "change": 2.3, "amount": 1.2e11, "mainNet": 5.0e8,  "volRatio": 1.5, "floatMv": 2.0e12},
    {"boardCode": "BK0002", "name": "算力",      "strength": 5895, "change": 1.8, "amount": 9.5e10, "mainNet": 4.2e8,  "volRatio": 1.3, "floatMv": 1.8e12},
    {"boardCode": "BK0003", "name": "并购重组",  "strength": 4070, "change": 1.2, "amount": 7.8e10, "mainNet": 2.1e8,  "volRatio": 1.1, "floatMv": 1.5e12},
    {"boardCode": "BK0004", "name": "AI应用",    "strength": 3763, "change": 0.9, "amount": 6.5e10, "mainNet": 1.8e8,  "volRatio": 1.0, "floatMv": 1.4e12},
    {"boardCode": "BK0005", "name": "芯片",      "strength": 2744, "change": 0.5, "amount": 5.0e10, "mainNet": 9.0e7,  "volRatio": 0.9, "floatMv": 1.0e12},
    {"boardCode": "BK0006", "name": "通信",      "strength": 2216, "change": 0.3, "amount": 4.2e10, "mainNet": 5.0e7,  "volRatio": 0.8, "floatMv": 9.0e11},
    {"boardCode": "BK0007", "name": "电力",      "strength": 1912, "change": 0.1, "amount": 3.5e10, "mainNet": 3.0e7,  "volRatio": 0.7, "floatMv": 8.0e11},
    {"boardCode": "BK0008", "name": "机器人概念", "strength": 1711, "change": -0.2, "amount": 3.0e10, "mainNet": 1.0e7, "volRatio": 0.7, "floatMv": 7.5e11},
    {"boardCode": "BK0009", "name": "股权转让",  "strength": 1363, "change": -0.4, "amount": 2.5e10, "mainNet": -5.0e7, "volRatio": 0.6, "floatMv": 6.0e11},
    {"boardCode": "BK0010", "name": "地产链",    "strength": 1114, "change": -0.5, "amount": 2.0e10, "mainNet": -1.0e8, "volRatio": 0.6, "floatMv": 5.5e11},
]


def test_record_today_top_saves_to_db(client, monkeypatch):
    """抓取当日板块 Top10 落库"""
    # mock kpl.fetch_board_rank 直接返回固定数据, 避免非交易时段拿不到
    monkeypatch.setattr(kpl, "fetch_board_rank", lambda: list(SAMPLE_BOARDS))
    n = sector_rotation.record_today_top(top_n=10)
    assert n == 10
    # 验证落库内容
    import sqlite3
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    row = conn.execute(
        "SELECT date, boards FROM daily_sector_top ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    date, raw = row
    boards = json.loads(raw)
    assert len(boards) == 10
    assert boards[0]["rank"] == 1
    assert boards[0]["name"] == "医药"
    assert boards[0]["strength"] == 9969
    assert boards[9]["rank"] == 10
    assert boards[9]["name"] == "地产链"


def test_record_today_top_handles_empty(monkeypatch):
    """抓取失败(返回空) 不报错也不入库"""
    monkeypatch.setattr(kpl, "fetch_board_rank", lambda: [])
    n = sector_rotation.record_today_top()
    assert n == 0


def test_query_rotation_returns_descending(client, monkeypatch):
    """查询历史: 按日期降序, dates 字段升序"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]

    # 落 3 天数据(模拟)
    import sqlite3
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("DELETE FROM daily_sector_top")
    for d in dates:
        payload = [{"rank": i + 1, "name": f"板块{i+1}", "strength": 100 - i * 10}
                   for i in range(5)]
        conn.execute("INSERT INTO daily_sector_top (date, boards, ts) VALUES (?,?,?)",
                     (d, json.dumps(payload), 0))
    conn.commit(); conn.close()

    out = sector_rotation.query_rotation(days=5)
    assert "days" in out and "dates" in out
    # dates 升序(便于前端左到右展示)
    assert out["dates"] == sorted(out["dates"])
    # days 按日期降序
    returned_dates = [x["date"] for x in out["days"]]
    assert returned_dates == sorted(returned_dates, reverse=True)
    assert len(out["days"]) == 3


def test_query_window_ranking_aggregates(client, monkeypatch):
    """多窗口排名: 聚合近 N 日板块平均强度(按名加权)"""
    # 落 30 天不同板块数据(模拟, 让"医药"和"算力"反复出现 Top3)
    import sqlite3
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("DELETE FROM daily_sector_top")
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    for i in range(30):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        # 让医药/算力在大多数日子排前 3, AI应用第 4-8
        boards = [
            {"rank": 1, "name": "医药", "strength": 9000 + i},
            {"rank": 2, "name": "算力", "strength": 8000 + i},
            {"rank": 3, "name": "并购重组", "strength": 7000 + i},
            {"rank": 4, "name": "AI应用", "strength": 6000 + i},
            {"rank": 5, "name": "芯片", "strength": 5000 + i},
        ]
        conn.execute("INSERT INTO daily_sector_top (date, boards, ts) VALUES (?,?,?)",
                     (d, json.dumps(boards), 0))
    conn.commit(); conn.close()

    out = sector_rotation.query_window_ranking((10, 20, 30), top_k=3)
    assert len(out["windows"]) == 3
    # 每个窗口都有 top
    for w in out["windows"]:
        assert "window" in w and "top" in w
    # 10 日窗口 Top 必有医药+算力(权重最高)
    win10 = [x for x in out["windows"] if x["window"] == 10][0]
    names10 = [x["name"] for x in win10["top"]]
    assert "医药" in names10
    assert "算力" in names10


def test_api_sector_rotation(client, first_user):
    """API /api/kpl/sector-rotation 返回结构"""
    token, _, _ = first_user
    # 注入一些数据
    import sqlite3
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("DELETE FROM daily_sector_top")
    payload = [{"rank": 1, "name": "医药", "strength": 9969, "amount": 1.2e11}]
    conn.execute("INSERT INTO daily_sector_top (date, boards, ts) VALUES ('2026-08-14', ?, 0)",
                 (json.dumps(payload),))
    conn.commit(); conn.close()

    r = client.get("/api/kpl/sector-rotation?days=10", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert "rotation" in d and "windows" in d and "dates" in d
    assert len(d["dates"]) >= 1