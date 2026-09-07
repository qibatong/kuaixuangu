# -*- coding: utf-8 -*-
"""昨日同时刻对比口径(2026-09-07 主人: "盘中不是和上个交易日同一时间比较")

原逻辑 `[s for s in arr if s['ts'] <= now]` —— 昨日快照 ts 全都早于"今天此刻",
条件恒真 → 永远取昨日**最后一条(15:00 收盘=全天)**, 并非"同时刻"。
修复后按**日内时刻**匹配: 今日 14:30 → 昨日 14:00(或之前最近)的快照。
"""
import time
import pytest


def _epoch_bj(days_ago, hh, mm):
    """构造"北京时间 X 天前 hh:mm"的 epoch(与服务器时区无关)"""
    import datetime
    g = time.gmtime(time.time() + 8 * 3600)
    base = datetime.datetime(g.tm_year, g.tm_mon, g.tm_mday, hh, mm)
    return int(base.timestamp() - 8 * 3600 - days_ago * 86400)


def _now_at(hh, mm):
    """构造"今天北京时间 hh:mm"的 epoch(注入用)"""
    return _epoch_bj(0, hh, mm)


@pytest.fixture
def arr(monkeypatch):
    """昨日 intraday: 09:30=0亿 / 10:00=3000 / 14:00=8000 / 15:00=10000(全天)"""
    import app.services.settings as S
    data = [
        {"ts": _epoch_bj(1, 9, 30), "amount": 0.0, "stockCount": 5000},
        {"ts": _epoch_bj(1, 10, 0), "amount": 3000.0, "stockCount": 5000},
        {"ts": _epoch_bj(1, 14, 0), "amount": 8000.0, "stockCount": 5000},
        {"ts": _epoch_bj(1, 15, 0), "amount": 10000.0, "stockCount": 5000},
    ]

    def fake_get(key, default=None):
        return data if "market_brief_intraday_" in (key or "") else default

    monkeypatch.setattr(S, "get", fake_get)
    return data


def test_midday_matches_same_clock(arr):
    """今日 14:30 → 取昨日 14:00 的 8000 亿(不是昨日全天 10000)"""
    from app.services import fetcher
    r = fetcher.get_same_time_yesterday(now=_now_at(14, 30))
    assert r is not None, "应有昨日同时刻数据"
    assert r["amount"] == 8000.0, f"14:30 应匹配昨日 14:00 的 8000 亿, 实际 {r}"


def test_morning_matches_same_clock(arr):
    """今日 10:30 → 取昨日 10:00 的 3000 亿"""
    from app.services import fetcher
    r = fetcher.get_same_time_yesterday(now=_now_at(10, 30))
    assert r["amount"] == 3000.0, f"10:30 应匹配昨日 10:00, 实际 {r}"


def test_after_close_matches_full_day(arr):
    """收盘后(19:00) → 昨日已无更晚快照 → 取昨日全天 10000(合理)"""
    from app.services import fetcher
    r = fetcher.get_same_time_yesterday(now=_now_at(19, 0))
    assert r["amount"] == 10000.0, f"收盘后应对昨日全天, 实际 {r}"


def test_auction_before_first_snapshot(arr):
    """竞价时段(9:20)早于昨日首条(9:30) → 取首条(0 亿)"""
    from app.services import fetcher
    r = fetcher.get_same_time_yesterday(now=_now_at(9, 20))
    assert r["amount"] == 0.0, f"9:20 应取昨日首条(尚未成交), 实际 {r}"
