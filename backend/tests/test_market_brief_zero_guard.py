# -*- coding: utf-8 -*-
"""两市概况 amount=0 保护(2026-09-07 主人反馈"两市资金 0亿 缩量 20304亿")

收盘后/数据源异常时全市场 f6 可能全 0 → amount=0。上一版会把 0 **写进 5 分钟缓存**
并展示(前端显示"两市资金 0亿")。正确行为: 无效值不写缓存, 沿用上次有效值。
"""
import pytest


def _mk_fetcher(monkeypatch, amounts):
    """构造 fetch_market_brief 环境: amounts 为每次拉取的 f6 列表"""
    from app.services import fetcher
    calls = {"n": 0}

    def fake_raw(fs):
        i = min(calls["n"], len(amounts) - 1)
        calls["n"] += 1
        return [{"f12": "60000%d" % k, "f6": a} for k, a in enumerate(amounts[i])]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", fake_raw)
    monkeypatch.setattr(fetcher, "_market_brief_cache", {"ts": 0, "data": None})
    fetcher.store.delete(fetcher._MARKET_BRIEF_KEY)
    return fetcher, calls


def test_zero_not_cached_and_keeps_last(monkeypatch):
    """第一次有效(19458亿) → 第二次 0 → 应沿用 19458, 且不把 0 写缓存"""
    f, _ = _mk_fetcher(monkeypatch, [[1.9458e12], [0.0]])
    b1 = f.fetch_market_brief(max_age=0)
    assert b1["amount"] == 19458.0
    b2 = f.fetch_market_brief(max_age=0)      # 本次全 0
    assert b2["amount"] == 19458.0, "0 值应沿用上次有效值, 不得返回 0"
    assert (f.store.get(f._MARKET_BRIEF_KEY) or {}).get("amount") == 19458.0, \
        "0 值不得写入缓存"


def test_first_zero_no_history_returns_zero(monkeypatch):
    """首次即 0 且无历史 → 返回 0(调用方/前端自行处理, 不臆造数据)"""
    f, _ = _mk_fetcher(monkeypatch, [[0.0]])
    b = f.fetch_market_brief(max_age=0)
    assert b["amount"] == 0.0


def test_valid_value_refreshes_cache(monkeypatch):
    """正常有效值应刷新缓存(不能被沿用逻辑挡住)"""
    f, _ = _mk_fetcher(monkeypatch, [[1.0e12], [2.0e12]])
    assert f.fetch_market_brief(max_age=0)["amount"] == 10000.0
    assert f.fetch_market_brief(max_age=0)["amount"] == 20000.0
