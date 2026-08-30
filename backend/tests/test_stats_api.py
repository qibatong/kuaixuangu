# -*- coding: utf-8 -*-
"""stats API 路由测试: 多时点对比 / 时点快照 / 战绩 / 一字涨停 / 三时点榜 / 数据质量"""
import pytest

from app.db import database


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def _seed_snapshot(monkeypatch):
    """直接写入 snapshot_bid 三时点数据(绕开采集), 返回写入的日期"""
    import app.services.auction_snapshot as snap
    monkeypatch.setattr(snap.fetcher, "fetch_eastmoney_all", lambda fs: [
        {"f12": "600001", "f14": "测A", "f615": 10.0, "f616": 5e7, "f21": 4e9},
        {"f12": "000002", "f14": "测B", "f615": 5.0, "f616": 3e7, "f21": 5e9},
    ])
    monkeypatch.setattr(snap, "_bj_date", lambda: "2026-08-20")
    snap.snapshot_at("9_15", force=True)
    snap.snapshot_at("9_20", force=True)
    snap.snapshot_at("9_25", force=True)
    return "2026-08-20"


def test_overview_latest_4_days(client, first_user, monkeypatch):
    """未指定 date → 返回最近的交易日期(本测试写入的 2026-08-20)"""
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    r = client.get("/api/stats/auction-overview", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["days"], "应有至少 1 天"
    day = d["days"][0]
    assert day["date"] == "2026-08-20"
    # 9:15 有数据: avg_change=(10+5)/2=7.5; total_amt=(5e7+3e7)/... 注意 snapshot bid_amt 存的是万元
    assert day["points"]["9_15"]["count"] == 2
    assert day["points"]["9_15"]["avg_change"] == 7.5


def test_overview_specified_date(client, first_user, monkeypatch):
    """指定 date 对齐最近交易日, 无数据日期返回空列表"""
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    # 2026-08-21 > 写入日 2026-08-20, 应回退到 2026-08-20(最近<=21的交易日)
    r = client.get("/api/stats/auction-overview?date=2026-08-21", headers=hdrs(token))
    d = r.json()
    assert d["days"][0]["date"] == "2026-08-20"
    # 无任何数据 → 空
    r2 = client.get("/api/stats/auction-overview?date=2000-01-01", headers=hdrs(token))
    assert r2.json()["days"] == []


def test_auction_snapshot_requires_date(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/auction-snapshot", headers=hdrs(token))
    assert r.status_code == 400


def test_auction_snapshot_invalid_time_point(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/auction-snapshot?date=2026-08-20&time_point=9_99",
                   headers=hdrs(token))
    assert r.status_code == 400
    assert "time_point" in r.json()["msg"]


def test_auction_snapshot_ok(client, first_user, monkeypatch):
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    r = client.get("/api/stats/auction-snapshot?date=2026-08-20&time_point=9_25",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["date"] == "2026-08-20"
    assert d["time_point"] == "9_25"
    # 600001 涨停 10% 在最前
    codes = [it["code"] for it in d["list"]]
    assert codes[0] == "600001"
    assert "测A" in [it.get("name") for it in d["list"]]


def test_daily_yizi_default_days(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/daily-yizi", headers=hdrs(token))
    assert r.status_code == 200
    assert r.json().get("ok")
    assert isinstance(r.json()["list"], list)


def test_daily_yizi_days_clamped(client, first_user):
    """days 超界 → clamp 到 1..30"""
    token, _, _ = first_user
    r = client.get("/api/stats/daily-yizi?days=99999", headers=hdrs(token))
    assert r.status_code == 200
    r2 = client.get("/api/stats/daily-yizi?days=abc", headers=hdrs(token))
    assert r2.status_code == 200


def test_bid_snapshot_invalid_tp(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/bid-snapshot?date=2026-08-20&time_point=bad",
                   headers=hdrs(token))
    assert r.status_code == 400


def test_bid_snapshot_ok(client, first_user, monkeypatch):
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    r = client.get("/api/stats/bid-snapshot?date=2026-08-20&time_point=9_20&limit=10",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 2
    assert d["list"][0]["code"] == "600001"
    # limit clamp
    r2 = client.get("/api/stats/bid-snapshot?date=2026-08-20&limit=99999",
                    headers=hdrs(token))
    assert r2.status_code == 200


def test_bid_snapshot_stock_requires_params(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/bid-snapshot-stock?date=2026-08-20", headers=hdrs(token))
    assert r.status_code == 400


def test_bid_snapshot_stock_ok(client, first_user, monkeypatch):
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    r = client.get("/api/stats/bid-snapshot-stock?date=2026-08-20&code=600001",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"]
    assert d["code"] == "600001"
    # 三时点都有数据
    assert set(d["points"].keys()) == {"9_15", "9_20", "9_25"}


def test_bid_snapshot_3points_requires_date(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/bid-snapshot-3points", headers=hdrs(token))
    assert r.status_code == 400


def test_bid_snapshot_3points_ok(client, first_user, monkeypatch):
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    # mock 东财实时行情
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda *a, **k: {"600001": {"realChange": 10.5}})
    # 2026-08-24: 现涨实时 merge 要求"盘中且 serve_date==今天" —
    # 测试在盘后/历史日期跑会走收盘涨幅兜底 → mock 窗口+时间让实时分支生效
    from app.api import stats as stats_api
    import time as _real_time
    class FakeTime:
        @staticmethod
        def gmtime(t=None):
            return _real_time.gmtime(t)
        @staticmethod
        def strftime(fmt, t=None):
            return "2026-08-20"   # == _seed_snapshot 写入日, 命中实时 merge 条件
    monkeypatch.setattr(stats_api, "_time", FakeTime)
    monkeypatch.setattr(stats_api, "_is_intraday_stats", lambda: True)
    r = client.get("/api/stats/bid-snapshot-3points?date=2026-08-20&limit=20",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    # 600001 9:25 涨停 10% → layer 1 排最前
    assert d["count"] >= 1
    first = d["list"][0]
    assert first["code"] == "600001"
    assert first["layer"] == 1
    assert first["tag"] == "9:25封死"
    # 实时涨幅叠加
    assert first.get("real_change") == 10.5


def test_seal_quality_no_data(client, first_user):
    """无任何快照数据且不传 date → 404"""
    token, _, _ = first_user
    # 清空 snapshot_bid, 让 MAX(date) 为空
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    conn.commit()
    conn.close()
    r = client.get("/api/stats/seal-quality", headers=hdrs(token))
    assert r.status_code == 404
    assert "无快照数据" in r.json()["msg"]


def test_seal_quality_ok(client, first_user, monkeypatch):
    token, _, _ = first_user
    _seed_snapshot(monkeypatch)
    r = client.get("/api/stats/seal-quality?date=2026-08-20", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"]
    assert "9_25" in d["report"]["points"]
    rep = d["report"]["points"]["9_25"]
    # 600001 涨停有 KPL 封单(bid_buy_amt=0 因未 mock KPL) → 涨停缺封单
    assert rep["n_zt"] == 1


def test_stats_require_auth(client):
    """未登录 401"""
    for path in ("/api/stats/auction-overview", "/api/stats/performance",
                 "/api/stats/daily-yizi", "/api/stats/bid-snapshot"):
        r = client.get(path)
        assert r.status_code == 401, path