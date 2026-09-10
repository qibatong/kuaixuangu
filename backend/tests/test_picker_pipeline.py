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


# ---------------------------------------------------------------- 竞价强度接入
def test_strength_replaces_warn_when_provided():
    """传 strengths → warn(f630) 因子被竞价强度替代。

    f630=0 时老口径拿 default 0.18; 传 strength=0.9 后应拿 0.9 → 差 (0.9-0.18)*17 ≈ 12 分。
    这正是"东财一断全员 default → 天花板崩 14 分"的修复点。
    """
    from app.services.picker.score import compute_score
    row = _q("600000", warn=0)          # f630=0, 即当前线上所有票的实际状态
    base = compute_score(row)
    with_st = compute_score(row, strength=0.9)
    assert with_st.probability - base.probability == 12      # (0.9-0.18)*0.17*100 ≈ 12.24
    assert with_st.parts["warn"]["value"] == "竞价强度"


def test_no_strength_keeps_legacy_warn():
    """不传 strengths → 行为与改造前完全一致(对拍基线不变)"""
    from app.services.picker.score import compute_score
    row = _q("600000", warn=0)
    assert compute_score(row).parts["warn"]["score"] == 0.18


def test_strength_high_adds_confidence():
    """竞价强度 ≥0.85 才加置信度(与老口径 warn>=4 同档位)"""
    from app.services.picker.score import compute_score
    row = _q("600000", warn=0)
    assert compute_score(row, strength=0.5).confidence == compute_score(row).confidence
    assert compute_score(row, strength=0.9).confidence > compute_score(row).confidence


def test_pipeline_uses_injected_strengths(monkeypatch):
    """pipeline: ctx.strengths 注入后直接用于评分, 不再自加载"""
    from app.services.picker import pipeline as pl
    _install(monkeypatch, {"snapshot": _FakeSource({"600000": _q("600000", warn=0)})})
    ctx_a = _ctx()
    res_a = pl.run(dict(FULL), ctx=ctx_a)
    ctx_b = _ctx()
    ctx_b.strengths = {"600000": 0.95}
    res_b = pl.run(dict(FULL), ctx=ctx_b)
    assert res_a.items and res_b.items
    assert res_b.items[0]["probability"] - res_a.items[0]["probability"] >= 12


def test_strength_load_failure_falls_back(monkeypatch):
    """竞价强度加载抛异常 → 退回 f630 行为, 不阻塞选股(降级可见, 名单照出)"""
    from app.services.picker import pipeline as pl
    _install(monkeypatch, {"snapshot": _FakeSource({"600000": _q("600000", warn=0)})})

    def _boom(*a, **k):
        raise RuntimeError("模拟竞价强度源故障")

    monkeypatch.setattr("app.services.bid_strength.load", _boom)
    res = pl.run(dict(FULL), ctx=_ctx())
    assert res.items, "强度源挂了也必须出名单(退回 f630)"


# ---------------- 异动等级档位(2026-09-09) ----------------
def test_warn_type_uses_strength_label_when_provided(monkeypatch):
    """pipeline: 传 strengths → item.warnType 走竞价强度档位(强5/中4/弱3/0=无),
    不再依赖东财 f630(腾讯/快照恒为 0 → 异动列全空)。"""
    rows = {"600000": _q("600000", warn=0),
            "600001": _q("600001", warn=0),
            "600002": _q("600002", warn=0),
            "600003": _q("600003", warn=0)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    ctx = _ctx()
    ctx.strengths = {"600000": 0.90,    # ≥0.85 → 强(5)
                     "600001": 0.70,    # ≥0.65 → ⚡中(4)
                     "600002": 0.50,    # ≥0.40 → ↑弱(3)
                     "600003": 0.20}    # <0.40 → 0(前端显 "-")
    res = pipeline.run(dict(FULL), ctx=ctx)
    got = {it["code"]: it["warnType"] for it in res.items}
    assert got["600000"] == 5
    assert got["600001"] == 4
    assert got["600002"] == 3
    assert got["600003"] == 0


def test_warn_type_falls_back_to_f630_without_strength(monkeypatch):
    """不传 strengths → item.warnType 退回 QuoteRow.warn_type(对拍/老链路兼容)"""
    rows = {"600000": _q("600000", warn=4)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    res = pipeline.run(dict(FULL), ctx=_ctx())        # 不传 strengths
    assert res.items[0]["warnType"] == 4


# ==================== 涨幅加速度 accel (2026-09-11 由老链路用例迁移) ====================
# 原 test_snapshot.py 的 4 条用例直测 scorer.process_all_stocks(已退役)。
# accel 是 live 展示字段(9:25 竞价涨幅 - 9:20 快照涨幅), 语义原样保留:
# 只在竞价窗口内计算, 无快照/非窗口一律 None。
def test_accel_in_auction_window(monkeypatch):
    """竞价窗口内: accel = 9:25 竞价涨幅 - 9:20 快照涨幅"""
    rows = {"600000": _q("600000", bid_change=4.0)}
    _install(monkeypatch, {"eastmoney_market": _FakeSource(rows, label="eastmoney_market")})
    ctx = _ctx(snapshot_map={"600000": {"bid_change": 1.5}})
    res = pipeline.run(dict(FULL), ctx=ctx, now=datetime.datetime(2026, 9, 8, 9, 20))
    assert res.mode == "auction"
    assert res.items[0]["accel"] == 2.5


def test_accel_none_without_snapshot(monkeypatch):
    """窗口内但无 9:20 快照 → accel None"""
    rows = {"600000": _q("600000", bid_change=4.0)}
    _install(monkeypatch, {"eastmoney_market": _FakeSource(rows, label="eastmoney_market")})
    res = pipeline.run(dict(FULL), ctx=_ctx(snapshot_map={}),
                       now=datetime.datetime(2026, 9, 8, 9, 20))
    assert res.items[0]["accel"] is None


def test_accel_none_off_window(monkeypatch):
    """非竞价窗口 → accel None(避免收盘数据误导)"""
    rows = {"600000": _q("600000", bid_change=4.0)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    ctx = _ctx(snapshot_map={"600000": {"bid_change": 1.5}})
    res = pipeline.run(dict(FULL), ctx=ctx, now=datetime.datetime(2026, 9, 8, 16, 0))
    assert res.mode == "closed"
    assert res.items[0]["accel"] is None


def test_accel_negative_when_pullback(monkeypatch):
    """9:25 涨幅低于 9:20(竞价回落) → accel 为负"""
    rows = {"600000": _q("600000", bid_change=0.8)}
    _install(monkeypatch, {"eastmoney_market": _FakeSource(rows, label="eastmoney_market")})
    ctx = _ctx(snapshot_map={"600000": {"bid_change": 3.0}})
    res = pipeline.run(dict(FULL), ctx=ctx, now=datetime.datetime(2026, 9, 8, 9, 20))
    assert res.items[0]["accel"] == -2.2


# ==================== 竞价/昨比口径 (2026-09-11 由老链路用例迁移) ====================
def test_bid_ratio_uses_last_closed_day(monkeypatch):
    """分子=当日竞价额(万元), 分母=最近已收盘交易日全天额(pair[0]), 窗口内外同口径"""
    rows = {"600000": _q("600000", bid_amt=5.0e7)}      # 竞价 5000 万
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    ctx = _ctx(yesterday_map={"600000": [20000.0, 15000.0]})   # T 日 2 亿
    res = pipeline.run(dict(FULL), ctx=ctx, now=datetime.datetime(2026, 9, 8, 16, 0))
    assert res.items[0]["bidRatio"] == pytest.approx(25.0)


def test_bid_ratio_none_without_pair(monkeypatch):
    """无日K pair 时 bidRatio 为 None, 抢筹为 0

    注: yesterday_map 为空会触发 pipeline.fill_yesterday 按候选拉日K, 必须把
    fetcher.fetch_yesterday_amounts 也打成空 —— 否则 conftest 的全局假实现
    (恒返回 [20000.0, 15000.0]) 会把 pair 补回来, 用例假绿。
    """
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda codes: {})
    rows = {"600000": _q("600000", bid_amt=5.0e7)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    res = pipeline.run(dict(FULL), ctx=_ctx(yesterday_map={}),
                       now=datetime.datetime(2026, 9, 8, 16, 0))
    assert res.items[0]["bidRatio"] is None
    assert res.items[0]["qiangchou"] == 0
