# -*- coding: utf-8 -*-
"""二期测试: 9:20 快照存取 + 涨幅加速度"""
import pytest

from app.services import auction_snapshot, scorer

# 与 conftest MOCK_RAW 兼容的行情样本
RAW = {"f2": 18.50, "f3": 3.20, "f4": 3.10, "f5": 150000.0, "f6": 2800.0,
       "f8": 5.50, "f10": 1.80, "f12": "600001", "f14": "测试甲",
       "f17": 18.90, "f18": 17.90, "f20": 5.0e10, "f21": 4.0e9,
       "f100": "软件服务", "f102": "广东", "f103": "AI概念",
       "f615": 4.0, "f616": 5.0e7, "f617": 300.0, "f618": 400.0, "f630": 3}

FILTER = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
          "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}


# ---------- 快照存取 ----------
def test_snapshot_save_load(client, monkeypatch):
    """snapshot_at 抓取全市场并落库, load 可读回(幂等覆盖)"""
    calls = {"n": 0}

    def fake_fetch(fs):
        calls["n"] += 1
        a = dict(RAW)
        a["f12"] = "600001"
        b = dict(RAW)
        b.update({"f12": "000002", "f14": "测试乙", "f615": 1.2})
        return [a, b]

    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", fake_fetch)
    n = auction_snapshot.snapshot_at("9_20")
    assert n == 2 and calls["n"] == 3   # hs/cyb/kcb 三分区
    snap = auction_snapshot.load_snapshot()
    assert snap["600001"]["bid_change"] == 4.0
    assert snap["000002"]["bid_change"] == 1.2
    # 幂等: 再次抓取覆盖同日期同时点, 行数不变
    auction_snapshot.snapshot_at("9_20")
    assert len(auction_snapshot.load_snapshot()) == 2


def test_snapshot_multi_time_points(client, monkeypatch):
    """9:15/9:20/9:25 三时点独立归档, 互不覆盖"""
    seq = {"n": 0}

    def fake_fetch2(fs):
        seq["n"] += 1
        a = dict(RAW)
        a["f12"] = "600001"
        a["f615"] = [1.0, 2.5, 4.0][min((seq["n"] - 1) // 3, 2)]   # 每时点三分区同值, 依次 1.0/2.5/4.0
        return [a]

    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", fake_fetch2)
    auction_snapshot.snapshot_at("9_15")
    auction_snapshot.snapshot_at("9_20")
    auction_snapshot.snapshot_at("9_25")
    assert auction_snapshot.load_snapshot(time_point="9_15")["600001"]["bid_change"] == 1.0
    assert auction_snapshot.load_snapshot(time_point="9_20")["600001"]["bid_change"] == 2.5
    assert auction_snapshot.load_snapshot(time_point="9_25")["600001"]["bid_change"] == 4.0


def test_snapshot_load_empty(client):
    """无数据日期返回空 map"""
    assert auction_snapshot.load_snapshot("2000-01-01") == {}


def test_snapshot_fetch_fail_returns_0(client, monkeypatch):
    """三分区全失败时返回 0"""
    def boom(fs):
        raise RuntimeError("network down")
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", boom)
    assert auction_snapshot.snapshot_at("9_20") == 0


def test_snapshot_invalid_time_point(client):
    assert auction_snapshot.snapshot_at("9_99") == 0


def test_snapshot_filters_abnormal_change(client, monkeypatch):
    """归档过滤异常涨幅(±30%外, 防非交易时段字段污染)"""
    def fake_fetch(fs):
        a = dict(RAW); a["f12"] = "600001"; a["f615"] = 4.0       # 正常
        b = dict(RAW); b.update({"f12": "000002", "f615": 360.5})  # 异常
        return [a, b]
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", fake_fetch)
    n = auction_snapshot.snapshot_at("9_25")
    assert n == 1
    snap = auction_snapshot.load_snapshot(time_point="9_25")
    assert "600001" in snap and "000002" not in snap


# ---------- 历史回放 ----------
def test_query_snapshot_sorted(client, monkeypatch):
    """query_snapshot 按竞价涨幅降序 + limit"""
    def fake_fetch(fs):
        a = dict(RAW); a["f12"] = "600001"; a["f615"] = 1.0
        b = dict(RAW); b.update({"f12": "000002", "f615": 6.0})
        c = dict(RAW); c.update({"f12": "300003", "f615": 3.0})
        return [a, b, c]
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", fake_fetch)
    auction_snapshot.snapshot_at("9_25")
    rows = auction_snapshot.query_snapshot(auction_snapshot._bj_date(), "9_25", 50)
    assert [r["code"] for r in rows] == ["000002", "300003", "600001"]   # 6.0 > 3.0 > 1.0
    assert len(auction_snapshot.query_snapshot(auction_snapshot._bj_date(), "9_25", 2)) == 2


# ---------- 涨幅加速度 ----------
def test_accel_in_auction_window(monkeypatch):
    """竞价窗口内: accel = 9:25涨幅 - 9:20涨幅"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    raw = dict(RAW)   # f615=4.0 (9:25 竞价涨幅)
    snap = {"600001": {"bid_change": 1.5}}   # 9:20 时 1.5%
    result = scorer.process_all_stocks([raw], FILTER, {}, snap)
    assert result[0]["accel"] == 2.5


def test_accel_none_without_snapshot(monkeypatch):
    """窗口内但无 9:20 快照 → accel None"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    result = scorer.process_all_stocks([dict(RAW)], FILTER, {}, {})
    assert result[0]["accel"] is None


def test_accel_none_off_window(monkeypatch):
    """非竞价窗口 → accel None(避免收盘数据误导)"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    snap = {"600001": {"bid_change": 1.5}}
    result = scorer.process_all_stocks([dict(RAW)], FILTER, {}, snap)
    assert result[0]["accel"] is None


def test_accel_negative_when_pullback(monkeypatch):
    """9:25 涨幅低于 9:20(竞价回落) → accel 为负"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    raw = dict(RAW)
    raw["f615"] = 0.8
    snap = {"600001": {"bid_change": 3.0}}
    result = scorer.process_all_stocks([raw], FILTER, {}, snap)
    assert result[0]["accel"] == -2.2
