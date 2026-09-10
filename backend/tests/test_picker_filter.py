# -*- coding: utf-8 -*-
"""重构 P2 过滤层测试

防复发断言:
  1. 停牌"未知"(prev_close/vol 缺失)不得被剔除(2026-09-01 事故: 降级行 f4/f5=0
     → is_suspended 判停牌 → 整批被误杀); 但"9:25 有竞价额"是强证据 → 判非停牌
  2. 昨涨停名单(zt_codes)为权威: 集合=None(名单不可用)才降级 concept 文本匹配
  3. 竞价涨幅缺失默认剔除(竞价选股没竞价数据的票不该入选), 且原因可在 stats 查到
  4. 剔除顺序/条件与已退役老链路 apply_filters 逐条一致(逐项用例锁死)
  5. 每个剔除分支都要有 stats 计数(可观测 = 可排查"为什么这只票没了")
"""
import pytest

from app.services import scorer
from app.services.picker import filter as pf
from app.services.picker.contract import QuoteRow
from app.services.picker.score import ScoredRow, compute_score

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000,
}


def _mk(code="600000", name="浦发", **kw):
    base = dict(code=code, name=name, bid_change=3.0, bid_vol=4.8e6,
                warn_type=2, float_mv=55e8, yesterday_change=2.0, price=10.5,
                bid_amt=5.0e7, prev_close=10.15, vol=4.8e6)
    base.update(kw)
    r = QuoteRow(**base)
    return ScoredRow(row=r, score=compute_score(r, scorer.get_scoring_cfg()))


def _run(rows, f=None, ctx=None):
    return pf.apply_filters(rows, f or dict(FULL), ctx or pf.FilterContext())


# ==================== 市场范围 ====================
@pytest.mark.parametrize("code,markets,want", [
    ("600000", ["hs"], True),
    ("000001", ["hs"], True),
    ("300750", ["hs"], False),
    ("300750", ["cyb"], True),
    ("688981", ["kcb"], True),
    ("688981", ["hs", "cyb"], False),
    ("830001", ["hs", "cyb", "kcb"], False),   # 北交所一律排除
    ("600000", None, True),                     # 不限制
])
def test_in_markets(code, markets, want):
    assert pf.in_markets(code, markets) is want


def test_market_filter_drops_out_of_range():
    out = _run([_mk("600000"), _mk("300750", "宁德")],
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert [i.code for i in out.kept] == ["600000"]
    assert out.stats.get("market") == 1


# ==================== ST / 停牌 ====================
def test_st_dropped_when_stSuspend_off():
    f = dict(FULL, stSuspend=False)
    out = _run([_mk("600000", "ST某某"), _mk("600001")], f,
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert [i.code for i in out.kept] == ["600001"]
    assert out.stats.get("st") == 1


def test_unknown_suspend_not_dropped_by_default():
    """停牌未知(prev_close/vol 皆 None) → 默认不剔除(防 2026-09-01 误杀)"""
    out = _run([_mk("600000", prev_close=None, vol=None)],
               dict(FULL, stSuspend=False),
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert len(out.kept) == 1
    assert "suspend_unknown" not in out.stats


def test_unknown_suspend_can_be_dropped_on_purpose():
    """bid_amt 也没(连"9:25 有成交"的证据都没有) → 未知停牌按配置剔除"""
    out = _run([_mk("600000", prev_close=None, vol=None, bid_amt=None)],
               dict(FULL, stSuspend=False),
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set(),
                                    drop_unknown_suspend=True))
    assert not out.kept
    assert out.stats.get("suspend_unknown") == 1


def test_bid_amt_proves_not_suspended():
    """9:25 有竞价额 → 即便无 prev_close/vol 也判非停牌(DEGRADE_RULES)"""
    out = _run([_mk("600000", prev_close=None, vol=None, bid_amt=5.0e7)],
               dict(FULL, stSuspend=False),
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set(),
                                    drop_unknown_suspend=True))
    assert len(out.kept) == 1


def test_real_suspend_dropped():
    out = _run([_mk("600000", vol=0.0)], dict(FULL, stSuspend=False),
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert not out.kept
    assert out.stats.get("suspend") == 1


# ==================== 昨涨停 ====================
def test_first_board_uses_zt_codes_as_authority():
    """zt_codes 命中即剔除(即便 concept 里没有"昨日涨停"文本)"""
    r = _mk("600000", concept="金融")
    out = _run([r], dict(FULL, limitUp=False),
               ctx=pf.FilterContext(markets=["hs"], zt_codes={"600000"}))
    assert not out.kept and out.stats.get("first_board") == 1


def test_first_board_falls_back_to_concept_when_list_unavailable():
    """名单不可用(zt_codes=None) → 降级 concept 文本(老 is_first_board 行为)"""
    r = _mk("600000", concept="昨日连板")
    out = _run([r], dict(FULL, limitUp=False),
               ctx=pf.FilterContext(markets=["hs"], zt_codes=None))
    assert not out.kept
    r2 = _mk("600001", concept="金融")
    out2 = _run([r2], dict(FULL, limitUp=False),
                ctx=pf.FilterContext(markets=["hs"], zt_codes=None))
    assert len(out2.kept) == 1


def test_limitUp_on_keeps_first_board():
    out = _run([_mk("600000", concept="昨日涨停")], dict(FULL, limitUp=True),
               ctx=pf.FilterContext(markets=["hs"], zt_codes={"600000"}))
    assert len(out.kept) == 1


# ==================== 数值门槛 ====================
def test_bid_gt_drops_high_bid():
    out = _run([_mk("600000", bid_change=9.0), _mk("600001", bid_change=3.0)],
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert [i.code for i in out.kept] == ["600001"]
    assert out.stats.get("bid_gt") == 1


def test_missing_bid_change_dropped_and_counted():
    out = _run([_mk("600000", bid_change=None)],
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert not out.kept and out.stats.get("no_bid_change") == 1


def test_missing_bid_change_can_be_kept():
    out = _run([_mk("600000", bid_change=None)],
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set(),
                                    require_bid_change=False))
    assert len(out.kept) == 1


def test_mv_and_bid_amt_and_price_thresholds():
    rows = [
        _mk("600001", float_mv=20e8),        # 市值 < 30 亿
        _mk("600002", float_mv=300e8),       # 市值 > 100 亿
        _mk("600003", bid_amt=1e7),          # 竞价额 1000 万 < 3000 万
        # 价格 > 30: 昨收必须与现价自洽(现价=昨收×(1+竞价涨幅)), 否则数据本身矛盾
        _mk("600004", price=50.0, prev_close=48.5, bid_change=3.0),
        _mk("600005"),                       # 正常
    ]
    out = _run(rows, ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert [i.code for i in out.kept] == ["600005"]
    assert out.stats["mv_floor"] == 1 and out.stats["mv_gt"] == 1
    assert out.stats["bid_amt"] == 1 and out.stats["price_gt"] == 1


def test_missing_mv_or_bid_amt_dropped():
    """市值/竞价额缺失 → 无法证明达标 → 剔除(与老口径 0<floor 同结果, 防虚胖)"""
    out = _run([_mk("600001", float_mv=None), _mk("600002", bid_amt=None)],
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert not out.kept
    assert out.stats["mv_floor"] == 1 and out.stats["bid_amt"] == 1


def test_prob_conf_double_low_drops():
    """双低才剔除(概率低 **且** 置信度低)"""
    weak = _mk("600001", bid_change=0.5, bid_vol=1e4, warn_type=0,
               float_mv=90e8, yesterday_change=-2.0)
    out = _run([weak], dict(FULL, probLt=90, confLt=90),
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert not out.kept and out.stats.get("prob_conf") == 1
    # 只降低 probLt(单低) → 保留
    out2 = _run([weak], dict(FULL, probLt=90, confLt=55),
                ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert len(out2.kept) == 1


# ==================== 可观测性 ====================
def test_stats_cover_every_drop_reason():
    rows = [_mk("300750", "宁德", bid_change=9.0),      # market + bid_gt 之外的分支
            _mk("600001", float_mv=1e8)]
    out = _run(rows, ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert out.rejected == 2
    assert sum(out.stats.values()) == 2


# ==================== 竞涨下限 bidLt (2026-09-09) ====================
def test_bid_lt_drops_negative_bid_change():
    """低开/大跌竞涨不再入选(中石科技 9/8 竞涨 -8.01% 混入事故):
    默认 bidLt=0 → 竞涨<0 剔除, 平开 0.0 保留; 剔除原因可查(stats.bid_lt)"""
    f = dict(FULL, bidLt=0)
    out = _run([_mk("600000", bid_change=-8.01),
                _mk("600001", bid_change=0.0),
                _mk("600002", bid_change=3.0)], f,
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert [i.code for i in out.kept] == ["600001", "600002"]
    assert out.stats.get("bid_lt") == 1


def test_bid_lt_customizable_negative():
    """bidLt 可配负值(极端低吸策略): bidLt=-5 时 -3 保留、-8 剔除"""
    f = dict(FULL, bidLt=-5)
    out = _run([_mk("600000", bid_change=-3.0),
                _mk("600001", bid_change=-8.0)], f,
               ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert [i.code for i in out.kept] == ["600000"]
    assert out.stats.get("bid_lt") == 1


def test_bid_lt_not_breaking_no_bid_change_rule():
    """bid_change=None 仍走 require_bid_change(默认剔除), 与 bidLt 互不干扰"""
    f = dict(FULL, bidLt=0)
    out = _run([_mk("600000", bid_change=None)],
               f, ctx=pf.FilterContext(markets=["hs"], zt_codes=set()))
    assert not out.kept
    assert out.stats.get("no_bid_change") == 1 and out.stats.get("bid_lt") is None
