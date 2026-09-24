# -*- coding: utf-8 -*-
"""竞价窗口(9:15-9:25)语义测试 —— 2026-09-09 生产事故防复发

事故回放: 生产机东财被墙(当日东财全市场失败 2357 次, 100% 走腾讯兜底),
竞价窗口 25 次调用**入选恒为 0**:
  - 17 次: 腾讯行在集合竞价期尚未撮合 → vol=0 → 被判"停牌"全剔
  - 8 次:  当日 9:25 定格尚未生成 → 竞价涨幅缺失 → 粗筛为空
且新链路 n_universe=5557>0 被判"成功", 回退老链路根本没触发; 老链路同口径
(f4<=0 or f5==0)也是 0 只, 对拍"一致" → 一整天无人发现。

本文件锁死三件事:
  1. 竞价窗口内 vol==0 **不得**判停牌(只看昨收); 窗口外行为不变
  2. 竞价窗口内腾讯行(无竞价专属字段)允许用 f615/f616 —— 此时现价即竞价虚拟价
  3. 竞价窗口名单源是**序列**(猫爪 → 东财 → 腾讯全市场), 不是单点
"""
import datetime

import pytest

from app.services.picker import filter as pfilter
from app.services.picker import mode as pm
from app.services.picker import pipeline
from app.services.picker.contract import QuoteRow
from app.services.picker.score import score_rows
from app.services.picker.sources import base as sb
from app.services.picker.sources import tencent as tsrc

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 300, "bidAmtFloor": 3000,
}

AUCTION_NOW = datetime.datetime(2026, 9, 8, 9, 20)      # 竞价窗口内
CLOSED_NOW = datetime.datetime(2026, 9, 8, 16, 0)       # 收盘后


def _q(code="600000", *, vol=4.8e6, prev=10.15, bid_change=3.0, bid_amt=5.0e7,
       mv=55e8, price=10.5, window=False):
    return QuoteRow(code=code, name="某股", bid_change=bid_change, bid_amt=bid_amt,
                    float_mv=mv, price=price, real_change=3.4, prev_close=prev,
                    open=10.2, vol=vol, turnover=0.9, vol_ratio=1.8,
                    warn_type=2, yesterday_change=2.0, auction_window=window)


class _FakeSource(sb.BaseSource):
    def __init__(self, rows=None, fail=False, label="fake_list"):
        self.label = label
        self._rows = rows or {}
        self._fail = fail

    def fetch(self, ctx):
        if self._fail:
            return sb.SourceResult(error="模拟名单源故障", degraded=True)
        if not self._rows:
            return sb.SourceResult(error="空", degraded=True)
        return sb.SourceResult(rows=dict(self._rows), requested=len(self._rows))


def _install(monkeypatch, mapping):
    monkeypatch.setattr(pipeline, "get_source", lambda label: mapping.get(label))


def _ctx(**kw):
    return pipeline.PickContext(date="2026-09-08", markets=["hs", "cyb", "kcb"],
                                zt_codes=set(), **kw)


# ==================== 1. 停牌判定: 竞价窗口内 vol==0 不是停牌 ====================
def test_auction_window_zero_vol_is_not_suspended():
    """事故根因: 竞价期尚未撮合, 成交量恒 0 —— 不得据此判停牌"""
    row = _q(vol=0.0, window=True)
    assert row.is_suspended is False


def test_non_auction_zero_vol_still_suspended():
    """窗口外 vol==0 仍是停牌/无成交(老口径不变)"""
    row = _q(vol=0.0, window=False)
    assert row.is_suspended is True


def test_auction_window_bad_prev_close_still_suspended():
    """竞价窗口内昨收<=0 仍是停牌(防放水: 真停牌/异常行照样剔除)"""
    row = _q(vol=0.0, prev=0.0, window=True)
    assert row.is_suspended is True


def test_missing_prev_close_or_vol_is_unknown():
    """缺失 → None(未知), 由调用方决定, 不默认剔除也不默认保留"""
    assert _q(vol=None, window=True).is_suspended is None
    assert _q(prev=None, window=True).is_suspended is None


def test_filter_keeps_auction_window_candidates(monkeypatch):
    """端到端: 竞价窗口 + vol=0 的候选必须进名单(修复前会被全部剔除)"""
    row = _q(vol=0.0, window=True)
    fctx = pfilter.FilterContext(markets=["hs", "cyb", "kcb"], zt_codes=set())
    out = pfilter.apply_filters(score_rows([row]), dict(FULL), fctx)
    assert [k.row.code for k in out.kept] == ["600000"]
    assert not out.stats.get("suspend")


# ==================== 2. 腾讯行竞价字段: 按时段决定可信度 ====================
def _tencent_ctx(now):
    return sb.FetchContext(policy=pm.resolve_mode(now), date="2026-09-08",
                           markets=["hs", "cyb", "kcb"])


def test_tencent_keeps_bid_fields_in_auction_window():
    """竞价窗口内: 现价=竞价虚拟价、累计额=竞价额 → f615/f616 语义正确, 必须取"""
    raw = [{"f12": "600000", "f14": "某股", "f2": 10.5, "f3": 3.4, "f4": 10.15,
            "f5": 0, "f6": 0, "f615": 3.0, "f616": 5.0e7}]
    rows = tsrc._rows_from_tencent(raw, _tencent_ctx(AUCTION_NOW))
    r = rows["600000"]
    assert r.bid_change == pytest.approx(3.0)
    assert r.bid_amt == pytest.approx(5.0e7)
    assert r.auction_window is True          # 时段事实 → 停牌判定不误杀


def test_tencent_clears_bid_fields_outside_auction_window():
    """窗口外: 现价涨幅≠竞价涨幅、累计额≠竞价额 → 一律清空(9/7 事故防复发)"""
    raw = [{"f12": "600000", "f14": "某股", "f2": 10.5, "f3": -8.0, "f4": 10.15,
            "f5": 100, "f6": 1.0e9, "f615": -8.0, "f616": 1.0e9}]
    rows = tsrc._rows_from_tencent(raw, _tencent_ctx(CLOSED_NOW))
    r = rows["600000"]
    assert r.bid_change is None and r.bid_amt is None and r.bid_vol is None
    assert r.auction_window is False


def test_tencent_frozen_map_wins_over_realtime():
    """定格 9:25 值优先于任何实时字段(权威来源铁律)"""
    raw = [{"f12": "600000", "f14": "某股", "f2": 10.5, "f615": 9.9, "f616": 1.0}]
    ctx = sb.FetchContext(policy=pm.resolve_mode(AUCTION_NOW), date="2026-09-08",
                          day_bid_change={"600000": 2.5},
                          day_bid_amt_wan={"600000": 4000.0})
    r = tsrc._rows_from_tencent(raw, ctx)["600000"]
    assert r.bid_change == pytest.approx(2.5)
    assert r.bid_amt == pytest.approx(4.0e7)


# ==================== 3. 竞价窗口名单源是序列, 不是单点 ====================
def test_auction_list_source_is_a_sequence(monkeypatch):
    """猫爪失败 → 东财失败 → 自动切腾讯全市场(生产机东财被墙是常态)

    三级都要出现在 calls 里: 只注入 eastmoney/tencent 会让 `get_source("meoz_market")`
    返回 None 而"跳过", 测不到真实优先级链。
    """
    rows = {"600000": _q(vol=0.0, window=True)}
    calls = []

    class _Spy(_FakeSource):
        def fetch(self, ctx):
            calls.append(self.label)
            return super().fetch(ctx)

    _install(monkeypatch, {"meoz_market": _Spy(fail=True, label="meoz_market"),
                           "eastmoney_market": _Spy(fail=True,
                                                    label="eastmoney_market"),
                           "tencent_market": _Spy(rows, label="tencent_market")})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=AUCTION_NOW)
    assert res.mode == "auction"
    assert calls == ["meoz_market", "eastmoney_market", "tencent_market"], \
        "名单源必须按 猫爪→东财→腾讯 的顺序依次尝试"
    assert res.sources[0] == "tencent_market"
    assert [i["code"] for i in res.items] == ["600000"]


def test_auction_all_list_sources_fail_is_visible(monkeypatch):
    """三个名单源都挂 → 不产出名单 + errors 明示(铁律2: 降级可见)"""
    _install(monkeypatch, {"meoz_market": _FakeSource(fail=True, label="meoz_market"),
                           "eastmoney_market": _FakeSource(fail=True,
                                                           label="eastmoney_market"),
                           "tencent_market": _FakeSource(fail=True,
                                                         label="tencent_market")})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=AUCTION_NOW)
    assert not res.items and res.n_universe == 0
    assert sum("名单源" in e for e in res.errors) >= 1
    assert res.degraded


def test_auction_policy_declares_three_list_sources():
    """AUCTION 的名单源数量=3 且顺序为 猫爪→东财→腾讯(防改回单点/防顺序被调乱)"""
    pol = pm.POLICIES[pm.PickMode.AUCTION]
    assert pol.list_source_count == 3
    assert pol.source_priority[:3] == ("meoz_market", "eastmoney_market",
                                       "tencent_market")


def test_non_auction_policies_keep_single_list_source():
    """其余时段名单源仍是定格快照(幂等铁律, 不因本次改动被放宽)"""
    for m in (pm.PickMode.PREOPEN, pm.PickMode.LOCKED, pm.PickMode.INTRADAY,
              pm.PickMode.CLOSED):
        assert pm.POLICIES[m].list_source_count == 1
        assert pm.POLICIES[m].source_priority[0] == "snapshot"
