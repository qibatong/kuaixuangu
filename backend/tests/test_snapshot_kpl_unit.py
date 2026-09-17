# -*- coding: utf-8 -*-
"""2026-09-09 竞价额单位 bug 回归测试

背景(生产实锤 2026-09-09):
  `scorer.get_bid_amt()` 返回 **万元**(f616/10000), 而开盘啦 `kpl._parse_bid_seal`
  的 `bidAmt` 单位是 **元**。auction_snapshot 三处补位/兜底直接把元塞进万元字段,
  导致竞价额放大 1e4 倍 —— 9_20 时点竞价额中位 5,775,000 万元(实际应为 577 万)。

铁律: 跨源叠加必须**先对齐单位再赋值**。本文件守住这条。
"""
import pytest

from app.db import database
from app.services import auction_snapshot as asnap
from app.services import kpl


# 开盘啦原始行: 竞价额 500 万元 = 5_000_000 元
_KPL_AMT_YUAN = 5_000_000.0
_EXPECT_WAN = 500.0


@pytest.fixture
def _kpl_seal(monkeypatch):
    """桩掉开盘啦竞价榜: 返回 1 只, 竞价额 5_000_000 元"""
    monkeypatch.setattr(kpl, "clear_cache", lambda: None)
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [{
        "code": "600000", "name": "浦发银行",
        "bidChange": 3.0,
        "bidAmt": _KPL_AMT_YUAN,          # 元
        "bidSealAmt": 0.0,
        "floatMv": 2.0e10,                # 元
        "board": "银行",
    }])
    monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: [])


def test_kpl_fallback_converts_yuan_to_wan(_kpl_seal):
    """_fetch_kpl_fallback: 开盘啦元 → 本表万元; 市值必须落在**语义正确的列**

    2026-09-18 口径修正(v4.11.28): 原断言"float_mv 必须透传 2.0e10"恰好**锁死了
    错误行为** —— 开盘啦 `floatMv` 是"实际流通"(≈自由流通), 而 `float_mv` 列在全
    系统的语义是东财 f21 **流通市值**。直接透传导致 49 亿流通的票落库成 14.75 亿,
    被 floatMvFloor=30 系统性误剔(9/17 实测 56 只真大盘股, 其中 3 只其它门槛全过)。
    现在: 开盘啦值写 **free_mv**, float_mv 留 0 → 由 mv_cache.fill 用东财 f21 /
    腾讯 f44 补真值; 补不到则保持"未知"(粗筛不误杀, 见 picker.filter)。
    """
    fb = asnap._fetch_kpl_fallback()
    assert "600000" in fb, "兜底应包含开盘啦榜单股票"
    row = fb["600000"]
    assert row["bid_amt"] == pytest.approx(_EXPECT_WAN), \
        "竞价额必须换算成万元(元/1e4), 否则放大 1e4 倍"
    assert row["float_mv"] == 0, \
        "float_mv 列 = 流通市值(f21), 开盘啦给的不是这个口径 → 必须留 0 交给 mv_cache"
    assert row["free_mv"] == pytest.approx(2.0e10), \
        "开盘啦的'实际流通'必须写进 free_mv(语义正确的列), 不能丢"


def test_snapshot_at_backfill_converts_yuan_to_wan(monkeypatch, _kpl_seal):
    """snapshot_at 补位路径: 行情源竞价额为 0(东财故障)时由开盘啦补, 单位必须换算"""
    monkeypatch.setattr(asnap, "_fetch_market_map", lambda full=False: {
        "600000": {"bid_change": 0.0, "bid_amt": 0.0, "name": "浦发银行",
                   "bid_buy_amt": 0.0, "float_mv": 2.0e10, "free_mv": 2.0e10,
                   "board": ""},
    })
    monkeypatch.setattr(asnap, "check_seal_quality", lambda *a, **kw: None)

    n = asnap.snapshot_at("9_20", force=True)     # force: 跳过非交易日防御
    assert n == 1, "应落库 1 只"

    conn = database.get_conn()
    try:
        got = conn.execute(
            "SELECT bid_amt FROM snapshot_bid WHERE code='600000' AND time_point='9_20'"
        ).fetchone()
    finally:
        conn.close()
    assert got is not None, "快照未落库"
    assert float(got[0]) == pytest.approx(_EXPECT_WAN), \
        "落库竞价额应为 %.1f 万元, 实际 %s" % (_EXPECT_WAN, got[0])
