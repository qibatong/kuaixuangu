# -*- coding: utf-8 -*-
"""重构 P3 编排层测试

防复发断言:
  1. 名单源失败 → 不产出名单(绝不静默返回空/半残), errors 有原因
  2. **补丁源失败不影响名单**(9/8 事故反面: 老链路点查失败 → 整批降级 → 名单虚胖)
  3. 竞价字段永远认定格, 补丁行不得覆盖(9/7 竞涨=现涨)
  4. 幂等: 同 (date, mode, filters) 两次跑结果完全一致
  5. 粗筛只按定格可判定字段, 不误杀(粗筛空 → 结果必空, 不回退实时全市场)
"""
import datetime

import pytest

from app.services.picker import filter as pfilter
from app.services.picker import mode as pm
from app.services.picker import pipeline
from app.services.picker.contract import QuoteRow
from app.services.picker.sources import base as sb

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 300, "bidAmtFloor": 3000,
}


def _q(code, *, name="某股", bid_change=3.0, bid_amt=5.0e7, mv=55e8,
       price=10.5, real=3.4, prev=10.15, open_=10.2, vol=4.8e6,
       turnover=0.9, vol_ratio=1.8, warn=2, ychg=2.0):
    return QuoteRow(code=code, name=name, bid_change=bid_change, bid_amt=bid_amt,
                    bid_vol=None, float_mv=mv, price=price, real_change=real,
                    prev_close=prev, open=open_, vol=vol, turnover=turnover,
                    vol_ratio=vol_ratio, warn_type=warn, yesterday_change=ychg)


class _FakeSource(sb.BaseSource):
    """可控名单源: 返回预设 rows, 或按 fail=True 模拟失败"""
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


class _FakePatch(sb.BaseSource):
    """可控补丁源: 给每个 code 造一个"实时"行(价格被改大, 竞价字段被污染)"""
    def __init__(self, label="fake_patch", fail=False, bad_bid=None):
        self.label = label
        self._fail = fail
        self.bad_bid = bad_bid

    def fetch(self, ctx):
        if self._fail:
            return sb.SourceResult(error="模拟补丁源故障", degraded=True)
        rows = {}
        for c in ctx.codes or []:
            rows[c] = QuoteRow(code=c, name="补丁", price=999.0, real_change=9.9,
                               open=20.0, turnover=5.0, vol_ratio=3.0,
                               prev_close=10.0, vol=1e6, warn_type=1,
                               float_mv=55e8,
                               # 污染字段: 补丁源带来的假竞价数据(必须被丢弃)
                               bid_change=self.bad_bid, bid_amt=1.0e5,
                               source="patch")
        return sb.SourceResult(rows=rows, degraded=True, requested=len(ctx.codes or []))


def _install(monkeypatch, mapping):
    # 必须 patch **pipeline 里已导入的名字**(from ... import get_source 是值绑定),
    # patch sources.base.get_source 对 pipeline 不生效。
    monkeypatch.setattr(pipeline, "get_source", lambda label: mapping.get(label))


def _ctx(**kw):
    return pipeline.PickContext(date="2026-09-08", markets=["hs", "cyb", "kcb"],
                                zt_codes=set(), **kw)


# ==================== 基本流程 ====================
def test_pipeline_runs_and_returns_items(monkeypatch):
    rows = {"600000": _q("600000"), "600001": _q("600001", bid_change=9.9)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    # 15:00 后 → CLOSED(名单源 snapshot, realtime_patch=False → 不补)
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 16, 0))
    assert res.mode == "closed" and res.ok
    assert [i["code"] for i in res.items] == ["600000"]      # 600001 超 bidGt
    assert res.n_universe == 2 and res.n_candidate == 1


def test_pipeline_list_source_failure_yields_no_list(monkeypatch):
    """名单源失败 → 不产出名单 + errors 明示(铁律2: 降级必须可见, 不静默空)"""
    _install(monkeypatch, {"snapshot": _FakeSource(fail=True)})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 16, 0))
    assert not res.items and not res.ok
    assert res.degraded and any("名单源" in e for e in res.errors)


def test_pipeline_patch_failure_keeps_list(monkeypatch):
    """补丁源失败 → 名单照出(老链路此时会整批降级, 名单虚胖/翻车)"""
    rows = {"600000": _q("600000")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch(fail=True),
                           "tencent_point": _FakePatch(fail=True)})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 11, 0))
    assert res.mode == "intraday"
    assert [i["code"] for i in res.items] == ["600000"]
    assert any("补丁源" in e for e in res.errors)


def test_pipeline_patch_never_overwrites_bid_fields(monkeypatch):
    """补丁行带来的假竞价数据(bid_change/bid_amt)必须被丢弃 —— 竞价字段认定格"""
    rows = {"600000": _q("600000", bid_change=3.0, bid_amt=5.0e7)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch(bad_bid=99.0)})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 11, 0))
    it = res.items[0]
    assert it["bidChange"] == 3.0            # 定格值, 不是补丁的 99
    assert it["bidAmt"] == 5000.0            # 定格 5.0e7 元 → 5000 万元
    assert it["price"] == 999.0              # 展示字段已被补丁更新


# ==================== 幂等 ====================
def test_pipeline_is_deterministic(monkeypatch):
    """同 (date, mode, filters) 两次跑 → 完全一致(主人核心诉求)"""
    rows = {"600000": _q("600000"), "600002": _q("600002", bid_change=4.0),
            "600003": _q("600003", bid_change=2.0)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    kw = dict(ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 11, 0))
    a = pipeline.run(dict(FULL), **kw)
    b = pipeline.run(dict(FULL), **kw)
    assert [i["code"] for i in a.items] == [i["code"] for i in b.items]
    assert [i["probability"] for i in a.items] == [i["probability"] for i in b.items]


# ==================== 粗筛 ====================
def test_coarse_filter_does_not_need_score():
    """粗筛不依赖评分(全市场 5500 只不必先评分)"""
    rows = [_q("600000", bid_change=9.9),      # 超 bidGt
            _q("600001", mv=1e8),              # 市值不足
            _q("600002", bid_amt=1e6),         # 竞额不足
            _q("300003"),                      # 正常(cyb)
            _q("600004")]                      # 正常
    codes = pfilter.coarse_filter(rows, FULL, pfilter.FilterContext(zt_codes=set()))
    assert set(codes) == {"300003", "600004"}
    assert codes[0] == "600004" or codes[0] == "300003"      # 按竞价额降序(同额)


def test_coarse_filter_respects_limit():
    rows = [_q("6000%02d" % i, bid_amt=(i + 1) * 1e7) for i in range(20)]
    codes = pfilter.coarse_filter(rows, FULL,
                                  pfilter.FilterContext(zt_codes=set()), limit=5)
    assert len(codes) == 5
    assert codes[0] == "600019"      # 竞价额最大


# ==================== 模式接线 ====================
def test_auction_mode_uses_market_source(monkeypatch):
    """竞价窗口(9:15-9:25)无当日定格 → 名单源=东财全市场"""
    rows = {"600000": _q("600000")}
    _install(monkeypatch, {"eastmoney_market": _FakeSource(rows, label="eastmoney_market")})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 9, 20))
    assert res.mode == "auction" and res.sources[0] == "eastmoney_market"
    assert [i["code"] for i in res.items] == ["600000"]


def test_unknown_source_label_is_reported(monkeypatch):
    _install(monkeypatch, {})            # 注册表为空 → 取不到源
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=datetime.datetime(2026, 9, 8, 16, 0))
    assert not res.items and res.errors


# ==================== 输出结构 ====================
def test_item_shape_matches_legacy(monkeypatch):
    """输出 item 与老链路同构(前端零改动) + 新增 degraded/source"""
    rows = {"600000": _q("600000")}
    _install(monkeypatch, {"snapshot": _FakeSource({"600000": _q("600000")})})
    res = pipeline.run(dict(FULL), ctx=_ctx(yesterday_map={"600000": [10000.0, 9000.0]}),
                       now=datetime.datetime(2026, 9, 8, 16, 0))
    it = res.items[0]
    for k in ("code", "name", "probability", "confidence", "bidChange", "realChange",
              "entityChange", "bidTurnover", "bidVolRatio", "warnType",
              "circulationMV", "industry", "concept", "bidAmt", "bidRatio",
              "accel", "price", "volRatio", "turnover", "qiangchou", "province",
              "speed", "degraded", "source"):
        assert k in it, k
    assert it["bidRatio"] == pytest.approx(50.0)   # 5000万/10000万(单位同为万元)
