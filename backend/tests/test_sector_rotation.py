# -*- coding: utf-8 -*-
"""板块轮动历史服务测试: 抓取落库 + 查询历史 + 多窗口排名 + 多数据源"""
import json
import os

import pytest

from app.services import kpl, sector_rotation


SAMPLE_KPL = [
    {"boardCode": "BK0001", "name": "医药",      "strength": 9969, "change": 2.3, "amount": 1.2e11, "mainNet": 5.0e8,  "volRatio": 1.5, "floatMv": 2.0e12},
    {"boardCode": "BK0002", "name": "算力",      "strength": 5895, "change": 1.8, "amount": 9.5e10, "mainNet": 4.2e8,  "volRatio": 1.3, "floatMv": 1.8e12},
    {"boardCode": "BK0003", "name": "并购重组",  "strength": 4070, "change": 1.2, "amount": 7.8e10, "mainNet": 2.1e8,  "volRatio": 1.1, "floatMv": 1.5e12},
    {"boardCode": "BK0004", "name": "AI应用",    "strength": 3763, "change": 0.9, "amount": 6.5e10, "mainNet": 1.8e8,  "volRatio": 1.0, "floatMv": 1.4e12},
    {"boardCode": "BK0005", "name": "芯片",      "strength": 2744, "change": 0.5, "amount": 5.0e10, "mainNet": 9.0e7,  "volRatio": 0.9, "floatMv": 1.0e12},
    {"boardCode": "BK0006", "name": "通信",      "strength": 2216, "change": 0.3, "amount": 4.2e10, "mainNet": 5.0e7,  "volRatio": 0.8, "floatMv": 9.0e11},
    {"boardCode": "BK0007", "name": "电力",      "strength": 1912, "change": 0.1, "amount": 3.5e10, "mainNet": 3.0e7,  "volRatio": 0.7, "floatMv": 8.0e11},
    {"boardCode": "BK0008", "name": "机器人概念", "strength": 1711, "change": -0.2, "amount": 3.0e10, "mainNet": 1.0e7,  "volRatio": 0.7, "floatMv": 7.5e11},
    {"boardCode": "BK0009", "name": "股权转让",  "strength": 1363, "change": -0.4, "amount": 2.5e10, "mainNet": -5.0e7, "volRatio": 0.6, "floatMv": 6.0e11},
    {"boardCode": "BK0010", "name": "地产链",    "strength": 1114, "change": -0.5, "amount": 2.0e10, "mainNet": -1.0e8, "volRatio": 0.6, "floatMv": 5.5e11},
]

SAMPLE_EM = [  # em 数据(用涨跌幅%×100 作 strength)
    {"boardCode": "BK1675", "name": "半导体", "strength": 320.0, "change": 3.20, "amount": 5.0e10, "mainNet": 0.0, "volRatio": 0.0, "floatMv": 0.0},
    {"boardCode": "BK1036", "name": "芯片",   "strength": 280.0, "change": 2.80, "amount": 4.5e10, "mainNet": 0.0, "volRatio": 0.0, "floatMv": 0.0},
]


def _raw_conn():
    import sqlite3
    return sqlite3.connect(os.environ["BID_DB_PATH"])


def test_record_today_top_saves_to_db(client, monkeypatch):
    """抓取当日板块 Top10 落库(默认 source=kpl)"""
    monkeypatch.setattr(kpl, "fetch_board_rank", lambda: list(SAMPLE_KPL))
    n = sector_rotation.record_today_top(top_n=10)
    assert n == 10
    conn = _raw_conn()
    row = conn.execute(
        "SELECT date, source, boards FROM daily_sector_top WHERE source='kpl' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    _, src, raw = row
    assert src == "kpl"
    boards = json.loads(raw)
    assert len(boards) == 10
    assert boards[0]["rank"] == 1
    assert boards[0]["name"] == "医药"
    assert boards[0]["strength"] == 9969


def test_record_today_top_handles_empty(monkeypatch):
    """抓取失败(返回空) 不报错也不入库"""
    monkeypatch.setattr(kpl, "fetch_board_rank", lambda: [])
    n = sector_rotation.record_today_top()
    assert n == 0


def test_record_today_top_source_em(client, monkeypatch):
    """record_today_top 支持 source 参数, em 数据独立落库"""
    monkeypatch.setattr(sector_rotation, "fetch_em_board_rank", lambda: list(SAMPLE_EM))
    n = sector_rotation.record_today_top(top_n=2, source="em")
    assert n == 2
    conn = _raw_conn()
    row = conn.execute(
        "SELECT date, source, boards FROM daily_sector_top WHERE source='em' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    _, src, raw = row
    assert src == "em"
    boards = json.loads(raw)
    assert boards[0]["name"] == "半导体"
    assert boards[0]["strength"] == 320.0


def test_query_rotation_filters_by_source(client, monkeypatch):
    """query_rotation 按 source 过滤: kpl 和 em 数据互不干扰"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for i, src in enumerate(("kpl", "em")):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        payload = [{"rank": 1, "name": "板块-" + src, "strength": 100 - i * 10}]
        conn.execute(
            "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, src, json.dumps(payload), 0))
    conn.commit(); conn.close()

    out_kpl = sector_rotation.query_rotation(days=5, source="kpl")
    out_em = sector_rotation.query_rotation(days=5, source="em")
    assert out_kpl["source"] == "kpl" and out_em["source"] == "em"
    assert "板块-kpl" not in [b["name"] for d in out_em["days"] for b in d["boards"]]
    assert "板块-em" not in [b["name"] for d in out_kpl["days"] for b in d["boards"]]


def test_query_window_ranking_filters_by_source(client, monkeypatch):
    """query_window_ranking 按 source 过滤"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for i in range(10):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for src, name in (("kpl", "医药"), ("em", "半导体")):
            boards = [{"rank": 1, "name": name, "strength": 100}]
            conn.execute(
                "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
                (d, src, json.dumps(boards), 0))
    conn.commit(); conn.close()

    out_kpl = sector_rotation.query_window_ranking((10,), top_k=3, source="kpl")
    out_em = sector_rotation.query_window_ranking((10,), top_k=3, source="em")
    kpl_names = {n for w in out_kpl["windows"] for n in [x["name"] for x in w["top"]]}
    em_names = {n for w in out_em["windows"] for n in [x["name"] for x in w["top"]]}
    assert "医药" in kpl_names and "半导体" not in kpl_names
    assert "半导体" in em_names and "医药" not in em_names


def test_query_rotation_returns_descending(client, monkeypatch):
    """查询历史: 按日期降序, dates 字段升序"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]

    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for d in dates:
        payload = [{"rank": i + 1, "name": f"板块{i+1}", "strength": 100 - i * 10}
                   for i in range(5)]
        conn.execute(
            "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, "kpl", json.dumps(payload), 0))
    conn.commit(); conn.close()

    out = sector_rotation.query_rotation(days=5, source="kpl")
    assert out["dates"] == sorted(out["dates"])
    returned_dates = [x["date"] for x in out["days"]]
    assert returned_dates == sorted(returned_dates, reverse=True)
    assert len(out["days"]) == 3


def test_query_window_ranking_aggregates(client, monkeypatch):
    """多窗口排名: 聚合近 N 日板块平均强度(按名加权)"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for i in range(30):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        boards = [
            {"rank": 1, "name": "医药", "strength": 9000 + i},
            {"rank": 2, "name": "算力", "strength": 8000 + i},
            {"rank": 3, "name": "并购重组", "strength": 7000 + i},
            {"rank": 4, "name": "AI应用", "strength": 6000 + i},
            {"rank": 5, "name": "芯片", "strength": 5000 + i},
        ]
        conn.execute(
            "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, "kpl", json.dumps(boards), 0))
    conn.commit(); conn.close()

    out = sector_rotation.query_window_ranking((10, 20, 30), top_k=3, source="kpl")
    assert len(out["windows"]) == 3
    win10 = [x for x in out["windows"] if x["window"] == 10][0]
    names10 = [x["name"] for x in win10["top"]]
    assert "医药" in names10 and "算力" in names10


def test_api_sector_rotation(client, first_user):
    """API /api/kpl/sector-rotation 默认 source=kpl, 可指定 source=em"""
    token, _, _ = first_user
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    payload_kpl = [{"rank": 1, "name": "医药", "strength": 9969}]
    payload_em = [{"rank": 1, "name": "半导体", "strength": 320}]
    conn.execute("INSERT INTO daily_sector_top (date, source, boards, ts) VALUES ('2026-08-14', 'kpl', ?, 0)",
                 (json.dumps(payload_kpl),))
    conn.execute("INSERT INTO daily_sector_top (date, source, boards, ts) VALUES ('2026-08-14', 'em', ?, 0)",
                 (json.dumps(payload_em),))
    conn.commit(); conn.close()

    # 默认 kpl
    r = client.get("/api/kpl/sector-rotation?days=10", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200 and r.json().get("ok")
    d = r.json()
    assert d["source"] == "kpl"
    assert len(d["dates"]) >= 1
    assert d["dates"][0] == "2026-08-14"
    # 返回的 boards 只含 kpl(医药), 不含 em(半导体)
    names = [b["name"] for day in d["rotation"]["days"] for b in day["boards"]]
    assert "医药" in names and "半导体" not in names

    # 指定 em
    r2 = client.get("/api/kpl/sector-rotation?source=em&days=10", headers={"Authorization": "Bearer " + token})
    d2 = r2.json()
    assert d2["source"] == "em"
    names2 = [b["name"] for day in d2["rotation"]["days"] for b in day["boards"]]
    assert "半导体" in names2 and "医药" not in names2