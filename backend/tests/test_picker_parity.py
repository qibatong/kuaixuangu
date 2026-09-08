# -*- coding: utf-8 -*-
"""重构 P2 新老链路**对拍**测试

对拍契约(核心 — 防止重构变成"重写一套行为不同的代码"):
  * 字段完备 + 窗口外(定格优先): 两条链路**逐票一致**(分/名单/竞涨/竞额/市值)
  * 字段完备 + 竞价窗口内(实时 f615/f616): 同样必须逐票一致
  * 字段缺失场景: 允许且应当出现差异 —— 差异即修正, 但每类差异必须有明确归因
    (市值缺失: 老给满分 1.0 新给 default 0.22; 竞价涨幅缺失: 老退 f3 新直接剔除)

任何"完备输入下不一致"都是新链路的计算错误, 本文件必须红。
"""
import pytest

from app.services import scorer
from app.services.picker import parity

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000,
}


def _raw(code, *, name="某股", price=10.5, prev=10.15, real=3.4, open_=10.2,
         bid_chg=3.0, bid_amt_wan=5000.0, mv_yi=55.0,
         warn=2, turnover=0.9, vol_ratio=1.8, industry="银行", concept="金融"):
    """构造东财行情行。

    令 **f616(竞价额) = 竞价量 × 价**, 使老链路"f5×100×f2"(量×价)与新链路
    "bid_amt"(权威竞价额)两种竞价换手口径数值相同 —— 真实行情两者本就近似相等
    (成交额 = 量×均价), 这里取严格相等以便对拍逐值比对。
    """
    amt_yuan = bid_amt_wan * 1e4
    bid_vol = amt_yuan / price                 # 股
    return {
        "f12": code, "f14": name,
        "f2": price, "f3": real, "f4": prev, "f18": prev,
        "f5": bid_vol / 100.0,                 # 手
        "f6": amt_yuan, "f8": turnover, "f10": vol_ratio,
        "f17": open_, "f21": mv_yi * 1e8,
        "f100": industry, "f103": concept,
        "f615": bid_chg, "f616": amt_yuan, "f617": bid_vol, "f630": warn,
    }


def _sample():
    return [
        _raw("600000", name="浦发银行", bid_chg=3.0, bid_amt_wan=5000, mv_yi=55),
        _raw("600001", name="某银行A", bid_chg=9.5, bid_amt_wan=8000, mv_yi=40),   # 超 bidGt
        _raw("600002", name="某银行B", bid_chg=2.0, bid_amt_wan=1000, mv_yi=45),   # 竞额不足
        _raw("300003", name="创业票C", bid_chg=4.0, bid_amt_wan=6000, mv_yi=20),   # 市值不足
        _raw("600004", name="ST异类", bid_chg=3.5, bid_amt_wan=5500, mv_yi=60),    # ST
        _raw("600005", name="高价票D", bid_chg=3.2, bid_amt_wan=5200, mv_yi=70,
             price=88.0),                                                          # 价格超限
        _raw("600006", name="正常票E", bid_chg=5.0, bid_amt_wan=7000, mv_yi=50, warn=1),
    ]


# ==================== 一致性: 字段完备 ====================
def test_parity_identical_outside_window_with_snapshot_values():
    """窗口外(9:25 定格权威): 完备输入 → 逐票一致"""
    raw = _sample()
    dc = {r["f12"]: r["f615"] for r in raw}
    da = {r["f12"]: r["f616"] / 1e4 for r in raw}
    dv = {r["f12"]: r["f617"] for r in raw}          # 定格竞价量(窗口外唯一权威)
    yc = {r["f12"]: 2.0 for r in raw}
    rep = parity.run(raw, dict(FULL), auction_window=False,
                     day_bid_change=dc, day_bid_amt_wan=da, day_bid_vol=dv,
                     yesterday_chg=yc, zt_codes=set())
    assert rep.identical, rep.summary() + " diff=%s%s" % (rep.score_diff, rep.field_diff)
    # 保留 600000/600006/600004(ST 票在 stSuspend=True 下不剔除), 其余各命中一条剔除
    # 规则: 600001 超 bidGt · 600002 竞额不足 · 300003 市值不足 · 600005 价格超限
    assert [i["code"] for i in rep.new] == ["600000", "600006", "600004"]
    assert set(rep.new_stats) == {"bid_gt", "bid_amt", "mv_floor", "price_gt"}


def test_parity_identical_inside_auction_window():
    """竞价窗口内(吃实时 f615/f616, 无定格): 完备输入 → 逐票一致"""
    raw = _sample()
    yc = {r["f12"]: 1.5 for r in raw}
    rep = parity.run(raw, dict(FULL), auction_window=True, yesterday_chg=yc,
                     zt_codes=set())
    assert rep.identical, rep.summary() + " diff=%s%s" % (rep.score_diff, rep.field_diff)


def test_parity_identical_with_zt_codes():
    """昨涨停名单生效(600006 在名单且未勾 limitUp) → 两条链路同步剔除"""
    raw = _sample()
    dc = {r["f12"]: r["f615"] for r in raw}
    da = {r["f12"]: r["f616"] / 1e4 for r in raw}
    f = dict(FULL, limitUp=False)
    dv = {r["f12"]: r["f617"] for r in raw}
    rep = parity.run(raw, f, auction_window=False, day_bid_change=dc,
                     day_bid_amt_wan=da, day_bid_vol=dv, zt_codes={"600006"})
    assert rep.identical, rep.summary()
    assert "600006" not in [i["code"] for i in rep.new]
    assert rep.new_stats.get("first_board") == 1


# ==================== 差异: 字段缺失(差异即修正) ====================
def test_missing_float_mv_is_scored_differently_on_purpose():
    """市值缺失: 老链路 f21=0 → 落 ["0","30"] 桶拿满分 1.0; 新链路 → default 0.22。
    这是**刻意修正**(不知道市值 ≠ 超小盘最优), 故必然产生分差。"""
    raw = [_raw("600000", mv_yi=0.0)]           # f21=0
    dc = {"600000": 3.0}
    da = {"600000": 5000.0}
    rep = parity.run(raw, dict(FULL), auction_window=False,
                     day_bid_change=dc, day_bid_amt_wan=da, zt_codes=set())
    # 老链路 0 市值 < floatMvFloor(30) → 剔除; 新链路 None 同样剔除 → 名单一致
    assert not rep.only_legacy and not rep.only_new
    assert len(rep.new) == 0


def test_missing_bid_change_new_link_drops_legacy_keeps():
    """竞价涨幅缺失(无定格值且非窗口): 老链路退 f3(当日涨幅) → 大概率保留;
    新链路判"无竞价数据" → 剔除。差异必须在报告里可见(only_legacy)。"""
    raw = [_raw("600000", bid_chg=None)]
    raw[0]["f615"] = None
    da = {"600000": 5000.0}
    rep = parity.run(raw, dict(FULL), auction_window=False,
                     day_bid_change={}, day_bid_amt_wan=da, zt_codes=set())
    assert rep.only_legacy == ["600000"]
    assert not rep.only_new
    assert rep.new_stats.get("no_bid_change") == 1


def test_require_bid_change_off_keeps_parity_with_legacy():
    """关掉 require_bid_change(兼容开关) → 新链路与老链路同结果"""
    raw = [_raw("600000", bid_chg=None)]
    raw[0]["f615"] = None
    da = {"600000": 5000.0}
    rep = parity.run(raw, dict(FULL), auction_window=False, day_bid_amt_wan=da,
                     zt_codes=set(), require_bid_change=False)
    assert not rep.only_legacy and not rep.only_new


# ==================== 健壮性 ====================
def test_parity_never_raises_on_empty_input():
    rep = parity.run([], dict(FULL), zt_codes=set())
    assert rep.identical and rep.n_raw == 0


def test_parity_restores_legacy_clock_and_zt(monkeypatch):
    """对拍结束后必须还原老链路的时钟/昨涨停补丁(否则污染后续请求)"""
    from app.services import fetcher
    raw = _sample()
    before_win, before_hm = scorer.in_auction_window, scorer._bj_hm
    before_zt = fetcher.get_yesterday_zt_codes
    parity.run(raw, dict(FULL), auction_window=True, zt_codes=set())
    assert scorer.in_auction_window is before_win
    assert scorer._bj_hm is before_hm
    assert fetcher.get_yesterday_zt_codes is before_zt
