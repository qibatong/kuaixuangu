# -*- coding: utf-8 -*-
"""pipeline 支持 strategy="spot" 测试 —— 2026-09-28 v4.11.80

背景: 主人要求「AI竞价选股出数据就锁定」= 让 **锁定链路**也能跑 spot 算法。
做法是把 spot 引擎(compute_score_spot + apply_spot_filters)接进**唯一链路** pipeline,
与 auction 共用名单源/昨日涨幅/补丁源/输出组装, **只在评分层与精筛层分叉**。

防复发断言(每条对应一次真实坑):
  A. **auction 零改动**: 不传 strategy 与传 strategy="auction" 结果逐字节一致
  B. **spot 跳过粗筛**: 候选 = 全市场(auction 会粗筛); 理由是粗筛排队键依赖竞价定格
  C. **spot 不加载竞价强度**: 六因子不含强度因子, 加载它是纯浪费
  D. **spot 输出字段完整**: 必须有 sealRatio/sealFund/limitBoards/breakCount;
     且 bidTurnover 不得 AttributeError(SpotScoreResult 无该字段)
  E. **spot 用实时价判定价格门槛**(price_gate="realtime")
  F. **spot 不剔除竞价涨幅缺失的票**(require_bid_change=False)

★ 2026-09-28 v4.11.80 第三步(重要): spot 的**名单源**已从 `_fetch_list`(snapshot 优先)
  改为 `_fetch_spot_universe`(实时全市场)。本文件所有 spot 用例因此必须额外打
  `_install_spot_universe` 桩 —— 不打就会**打真实网络**(实测 5561 只真票), 断言必红
  且用例不可重复。名单源本身的正确性(必须是实时而非定格)在
  tests/test_spot_lock_step3.py 的 A 组专测, 与本文件的"评分层分叉"关注点分开。
"""
import datetime

import pytest

from app.services.picker import pipeline
from app.services.picker.contract import QuoteRow
from app.services.picker.sources import base as sb

# auction 参数: 只保留"粗筛能真的裁掉一批"的门槛(bidGt), 其余放开避免误杀
AUCTION_F = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 3, "probLt": 0, "confLt": 0,
    "floatMvFloor": 0, "floatMvGt": 0, "priceGt": 0, "bidAmtFloor": 0,
}
# spot 用实时涨幅/量比/换手判定, 竞价字段门槛对 spot 无效 → 单独一套参数
SPOT_F = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "chgGt": 0, "chgFloor": 0, "priceGt": 0, "probLt": 0, "confLt": 0,
    "floatMvFloor": 0, "floatMvGt": 0, "volRatioFloor": 0, "turnoverFloor": 0,
    "turnoverGt": 0, "bidAmtFloor": 0, "bidGt": 0, "bidLt": 0, "scoreFloor": 0,
}

NOW = datetime.datetime(2026, 9, 28, 14, 0)      # 盘中(spot 不看时段)


def _q(code, *, name="某股", bid_change=3.0, bid_amt=5.0e7, mv=55e8,
       price=10.5, real=3.4, prev=10.15, open_=10.2, vol=4.8e6,
       turnover=5.5, vol_ratio=2.8, warn=2, ychg=2.0, bid_vol=3.0e6):
    """竞价行构造器。bid_vol 默认为非 None —— 竞价换手率(bid_turnover)由
    `bid_vol × price / free_mv` 派生, bid_vol=None ⇒ bid_turnover=None,
    而 test_auction_item_keeps_bid_turnover 需要它非 None 才能证伪 getattr 改造。"""
    return QuoteRow(code=code, name=name, bid_change=bid_change, bid_amt=bid_amt,
                    bid_vol=bid_vol, float_mv=mv, price=price, real_change=real,
                    prev_close=prev, open=open_, vol=vol, turnover=turnover,
                    vol_ratio=vol_ratio, warn_type=warn, yesterday_change=ychg)


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


class _FakePatch(sb.BaseSource):
    """补丁源: 给每个 code 造一个"实时"行。price 可调, 便于价格门槛对照实验。"""
    def __init__(self, label="fake_patch", fail=False, bad_bid=None, price=999.0):
        self.label = label
        self._fail = fail
        self.bad_bid = bad_bid
        self.price = price

    def fetch(self, ctx):
        if self._fail:
            return sb.SourceResult(error="模拟补丁源故障", degraded=True)
        out = {}
        for c in (ctx.codes or []):
            out[c] = _q(c, real=9.9, price=self.price, prev=900.0, open_=950.0,
                        vol=9.9e6, turnover=8.8, vol_ratio=3.3,
                        warn=(self.bad_bid or 2))
        return sb.SourceResult(rows=out, requested=len(out))


def _install(monkeypatch, mapping):
    monkeypatch.setattr(pipeline, "get_source", lambda label: mapping.get(label))


def _install_spot_universe(monkeypatch, rows):
    """把 **spot 名单源**替换成注入的 QuoteRow 字典。

    ★ 2026-09-28 v4.11.80 第三步: spot 的名单源已从 `_fetch_list`(snapshot 优先) 改为
    `_fetch_spot_universe`(实时全市场, 走 `ensure_spot_cache`)。因此本文件里所有
    `strategy="spot"` 的用例**必须改打这个桩** —— 否则它会去**打真实网络**(实测拉回
    5561 只真票), 断言 `n_universe == 30` 必然红, 且用例依赖外网、不可重复。

    ⚠️ 为什么打 `_fetch_spot_universe`(而不是 `fetcher.ensure_spot_cache`):
      前者是 pipeline 内的**单一收口点**, 打它可一次覆盖"取数+组装+竞价字段回填"整段,
      且不依赖 `QuoteRow.from_eastmoney` 的字段解析细节 —— 本文件的用例只关心
      **评分层/精筛层分叉**(A~G 组), 不该被行情行解析的字段契约牵动。
      名单源本身(必须是实时而非定格)由 tests/test_spot_lock_step3.py 的 A 组专测。
    """
    monkeypatch.setattr(pipeline, "_fetch_spot_universe",
                        lambda filters: sb.SourceResult(label="spot_market",
                                                       rows=dict(rows),
                                                       requested=len(rows)))


def _ctx(**kw):
    return pipeline.PickContext(date="2026-09-28", markets=["hs", "cyb", "kcb"],
                                zt_codes=set(), **kw)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """昨日涨幅/成交额一律走注入, 不碰网络。"""
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda codes: {})
    monkeypatch.setattr(fetcher, "fetch_yesterday_changes", lambda codes: {})
    # 2026-09-28 v4.11.80 第三步: 兜底禁掉 spot 实时全市场 —— 任何**忘了打桩**的 spot 用例
    #   都会拿到空源(确定性失败), 而不是**偷偷打外网**(静默变成"看起来过了")。
    monkeypatch.setattr(fetcher, "ensure_spot_cache", lambda a, f, b: ([], "测试环境禁用真实行情"))


# ==================== A. auction 零改动 ====================
def test_auction_default_equals_explicit(monkeypatch):
    """不传 strategy 与传 strategy="auction" 结果必须完全一致 —— 老调用方零影响。"""
    rows = {"600000": _q("600000"), "600001": _q("600001")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    r1 = pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW)                # 默认
    r2 = pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW, strategy="auction")
    assert r1.items == r2.items
    assert r1.n_universe == r2.n_universe


def test_auction_rejects_unknown_strategy(monkeypatch):
    """未知 strategy 不能静默当 auction —— 必须走 auction 分支(非 spot),
    即 is_spot=(strategy=="spot") 的严格相等判定(拼错不误入 spot)。"""
    rows = {"600000": _q("600000")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows)})
    res = pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW, strategy="Spot")   # 大小写不同
    # 走了 auction 分支 → 粗筛生效
    assert res.n_candidate < res.n_universe or res.n_universe <= 1


# ==================== B. spot 跳过粗筛 ====================
# 构造一组"竞价涨幅低 + 实时涨幅高"的票: auction 的 bidGt 会裁掉它们, spot 不会。
def _mixed_rows(n=30):
    """偶数号: 竞涨 1%(低于 bidGt=3, auction 粗筛会裁); 奇数号: 竞涨 5%(能过)。
    实时涨幅一律 4%(spot 的 chgGt=0 全过)。"""
    out = {}
    for i in range(n):
        code = "600%03d" % i
        bc = 1.0 if i % 2 == 0 else 5.0
        out[code] = _q(code, bid_change=bc, real=4.0)
    return out


def test_spot_skips_coarse_filter(monkeypatch):
    """spot 候选 = 全市场(不粗筛) —— 粗筛排队键是竞价定格涨幅, 对 spot 无意义。"""
    rows = _mixed_rows(30)
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    _install_spot_universe(monkeypatch, rows)
    res = pipeline.run(dict(SPOT_F), ctx=_ctx(), now=NOW, strategy="spot")
    assert res.n_universe == 30
    assert res.n_candidate == 30, "spot 必须全市场进候选(不粗筛), 实际=%d" % res.n_candidate


def test_auction_still_coarse_filters(monkeypatch):
    """对照组: auction 仍走粗筛(候选 < 全市场) —— 证明 B 的跳过只对 spot 生效。"""
    rows = _mixed_rows(30)
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    res = pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW, strategy="auction")
    assert res.n_candidate < res.n_universe, "auction 必须仍有粗筛(实际 cand=%d univ=%d)" % (
        res.n_candidate, res.n_universe)


# ==================== C. spot 不加载竞价强度 ====================
def test_spot_does_not_load_bid_strength(monkeypatch):
    """spot 六因子不含强度 → 绝不调 _load_strength(调了就是白读快照表 + 白跑 AI)。"""
    called = {"n": 0}

    def _boom(codes, ctx):
        called["n"] += 1
        return {}

    monkeypatch.setattr(pipeline, "_load_strength", _boom)
    rows = {"600000": _q("600000")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    _install_spot_universe(monkeypatch, rows)
    pipeline.run(dict(SPOT_F), ctx=_ctx(), now=NOW, strategy="spot")
    assert called["n"] == 0, "spot 不得加载竞价强度"
    # 对照组: auction 必须调
    pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW, strategy="auction")
    assert called["n"] == 1, "auction 仍必须加载竞价强度"


# ==================== D. spot 输出字段完整 ====================
def test_spot_item_has_spot_fields_no_attribute_error(monkeypatch):
    """spot item 必须有 4 个 spot 专有字段 + 不得因 bidTurnover 炸掉。

    回归: 改造时 to_dict() 硬取 `score.bid_turnover`, 而 SpotScoreResult 无该字段
    → AttributeError 'SpotScoreResult' object has no attribute 'bid_turnover'
    (v4.11.80 上机探针实测)。
    """
    rows = {"600000": _q("600000"), "600001": _q("600001")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    _install_spot_universe(monkeypatch, rows)
    res = pipeline.run(dict(SPOT_F), ctx=_ctx(), now=NOW, strategy="spot")
    assert res.items, "spot 应产出名单"
    it = res.items[0]
    for k in ("sealRatio", "sealFund", "limitBoards", "breakCount"):
        assert k in it, "spot item 缺字段 %s" % k
    # bidTurnover 必须是 None(不适用), 而不是崩掉或硬塞实时换手冒充竞价口径
    assert it["bidTurnover"] is None
    assert it["bidVolRatio"] is None


def test_auction_item_keeps_bid_turnover(monkeypatch):
    """对照组: auction item 的 bidTurnover 必须仍有值(getattr 改造不得把它变 None)。"""
    rows = {"600000": _q("600000")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    res = pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW, strategy="auction")
    assert res.items
    it = res.items[0]
    assert "bidTurnover" in it
    assert it["bidTurnover"] is not None, "auction 的竞价换手率不得被 getattr 兜成 None"


# ==================== E. spot 用实时价判定价格门槛 ====================
def test_spot_price_gate_uses_realtime(monkeypatch):
    """spot 价格门槛用**实时价**(row.price), 不用定格竞价价(昨收×竞涨)。

    构造能让两者分道扬镳:
      · 定格竞价价 ≈ prev_close×(1+bid_change/100) = 10.0×1.10 = 11.0
      · 实时价(行内 price) = 5.0
      · priceGt = 8.0 → 实时价 5.0 通过; 若误用竞价价 11.0 会被剔除。
    spot 侧 `apply_spot_filters` 第 11 条**硬取 r.price**(不读 ctx.price_gate),
    auction 侧 `apply_filters` 读 ctx.price_gate("auction") 用 auction_price。

    ★ v4.11.80 第三步: spot **不再走 `_fetch_patch`**(实时字段在 `_fetch_spot_universe`
      那一趟就拿全了) ⇒ 这里必须把"实时价"直接设在**注入的行**上(旧版靠 `_FakePatch`
      注入 5.0 已失效)。auction 对照组的定格价仍由 `_q(price=11.0, prev=10.0)` 给出。
    """
    f = dict(SPOT_F)
    f["priceGt"] = 8.0
    # spot 行: 实时价 5.0(< 8.0 ⇒ 应通过); prev/竞涨只影响 auction 的定格价口径
    spot_rows = {"600000": _q("600000", price=5.0, prev=10.0, bid_change=10.0)}
    auction_rows = {"600000": _q("600000", price=11.0, prev=10.0, bid_change=10.0)}
    _install(monkeypatch, {"snapshot": _FakeSource(auction_rows),
                           "eastmoney_realtime": _FakePatch(price=11.0)})
    _install_spot_universe(monkeypatch, spot_rows)
    res = pipeline.run(f, ctx=_ctx(), now=NOW, strategy="spot")
    assert len(res.items) == 1, (
        "spot 必须用实时价 5.0 过 priceGt=8.0(误用竞价价 11.0 才会被剔), 实际入选 %d" % len(res.items))
    # 对照: auction 用定格竞价价 11.0 > 8.0 → 被剔除
    fa = dict(AUCTION_F)
    fa["priceGt"] = 8.0
    res_a = pipeline.run(fa, ctx=_ctx(), now=NOW, strategy="auction")
    assert len(res_a.items) == 0, (
        "auction 必须用定格竞价价 11.0 判定 → 被 priceGt=8.0 剔除(实际 %d)" % len(res_a.items))


# ==================== F. spot 不剔除竞价涨幅缺失 ====================
def test_spot_keeps_rows_without_bid_change(monkeypatch):
    """spot 无 9:25 定格概念 → 竞价涨幅缺失**不得剔除**(auction 侧默认会剔)。"""
    rows = {"600000": _q("600000", bid_change=None)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    _install_spot_universe(monkeypatch, rows)
    res = pipeline.run(dict(SPOT_F), ctx=_ctx(), now=NOW, strategy="spot")
    assert len(res.items) == 1, "spot 不得因竞价涨幅缺失而剔除(实际入选 %d)" % len(res.items)
    # 对照组: auction(require_bid_change=True) 会剔掉
    res_a = pipeline.run(dict(AUCTION_F), ctx=_ctx(), now=NOW, strategy="auction")
    assert len(res_a.items) == 0, "auction 必须剔除竞价涨幅缺失的票"


# ==================== G. 幂等 ====================
def test_spot_is_idempotent(monkeypatch):
    """同 (date, filters, strategy=spot) 两次跑结果完全一致。"""
    rows = {("600%03d" % i): _q("600%03d" % i) for i in range(10)}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    _install_spot_universe(monkeypatch, rows)
    r1 = pipeline.run(dict(SPOT_F), ctx=_ctx(), now=NOW, strategy="spot")
    r2 = pipeline.run(dict(SPOT_F), ctx=_ctx(), now=NOW, strategy="spot")
    assert r1.items == r2.items
