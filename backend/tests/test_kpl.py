# -*- coding: utf-8 -*-
"""开盘啦(kpl)数据源解析逻辑测试"""
import pytest

from app.services import kpl


# ---------- 竞价涨停委买额解析 ----------
def test_parse_bid_seal():
    data = {"info": [
        ["300862", "蓝盾光电", 32.84, 19.99, 2041987916, 19.99, 7932172, 0.36,
         16578945, 50396264, 16578945, "并购重组、智能驾驶", 4616157703,
         36338768, 68157438, -31818670, "3连板"],
        ["600602", "云赛智联", 20.25, 9.99, 1204935750, 9.99, 55847472, 0.83,
         101227563, 1018562850, 101227563, "算力租赁、算力", 12191297761,
         279426957, 362921736, -83494779, "首板"],
    ]}
    rows = kpl._parse_bid_seal(data)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["code"] == "300862"
    assert r0["name"] == "蓝盾光电"
    assert r0["realChange"] == 19.99
    assert r0["bidSealAmt"] == 2041987916
    assert r0["bidNetAmt"] == 7932172
    assert r0["board"] == "并购重组、智能驾驶"
    assert r0["limitBoards"] == 3          # "3连板" -> 3
    assert rows[1]["limitBoards"] == 1     # "首板" -> 1


# ---------- 市场情绪解析 ----------
def test_fetch_sentiment(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "info": [{"ztjs": "92", "Day": "2026-08-12", "df_num": "1", "strong": "78", "lbgd": "7"}],
        "tip": "温馨提示",
        "errcode": "0",
    })
    kpl._cache.clear()
    s = kpl.fetch_sentiment()
    assert s["ztCount"] == 92
    assert s["strong"] == 78
    assert s["lbgd"] == 7
    assert s["dfNum"] == 1
    assert s["day"] == "2026-08-12"


# ---------- 连板梯队解析 ----------
def test_parse_ladder():
    data = {"info": [[
        ["002483", "润邦股份", 1, "", 1786497900, "造船", 103302544, 190927488,
         26446780, 38851255, -12404475, 42109205, "造船、专用设备", 3409410664,
         1.24, 1, 0, 0, "", "801374", 1, 5.75, 9.94],
    ]]}
    rows = kpl._parse_ladder(data, 1)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "002483"
    assert r["name"] == "润邦股份"
    assert r["seal"] == 103302544
    assert r["maxSeal"] == 190927488
    assert r["mainNet"] == 26446780
    assert r["boardCode"] == "801374"
    assert r["amplitude"] == 9.94
    assert r["ladderLabel"] == "首板"


# ---------- 板块强度解析 ----------
def test_parse_board_rank():
    data = {"list": [
        ["801159", "机器人概念", 7599, 1.346, 0.457, 489161664113, 568713851,
         20382431106, -19813717255, 0.825, 18688529430208, 1.36, 550100743,
         23899196317696, -3911638533, 40.9432, 36.0769, 7599, 1.346],
    ]}
    rows = kpl._parse_board_rank(data)
    assert len(rows) == 1
    r = rows[0]
    assert r["boardCode"] == "801159"
    assert r["name"] == "机器人概念"
    assert r["strength"] == 7599
    assert r["change"] == 1.346
    assert r["mainNet"] == 568713851
    assert r["volRatio"] == 0.825
    assert r["peNow"] == 40.9432


# ---------- 连板数解析 ----------
def test_lb():
    assert kpl._lb("3连板") == 3
    assert kpl._lb("首板") == 1
    assert kpl._lb("6天4板") == 4
    assert kpl._lb("") == 0


# ---------- 涨停原因解析 ----------
def test_fetch_zt_reason(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "StockID": "001337",
        "List": [{"Date": "2026-01-28", "Reason": "黄金；现货黄金创新高", "SCLT": "日内龙一",
                  "GNSM": "黄金：...", "Boom_ZS": "沪金主连价格突破"}],
        "errcode": "0",
    })
    kpl._cache.clear()
    r = kpl.fetch_zt_reason("001337")
    assert len(r) == 1
    assert r[0]["reason"] == "黄金；现货黄金创新高"
    assert r[0]["sclt"] == "日内龙一"


# ---------- 人气热榜解析 ----------
def test_fetch_hot_rank(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "Day": "2026-08-13",
        "List": [["600721", "百花医药", 0, 0, 1, 0, 0], ["600664", "哈药股份", 0, 5, 2, 0, 0]],
    })
    kpl._cache.clear()
    rows = kpl.fetch_hot_rank()
    assert len(rows) == 2
    assert rows[0]["code"] == "600721"
    assert rows[0]["change"] == 0
    assert rows[0]["rank"] == 1
    assert rows[1]["name"] == "哈药股份"
    assert rows[1]["change"] == 5


# ---------- 调用失败降级 ----------
def test_call_failure_returns_none(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: None)
    kpl._cache.clear()
    assert kpl.fetch_bid_seal() is None
    assert kpl.fetch_sentiment() is None
