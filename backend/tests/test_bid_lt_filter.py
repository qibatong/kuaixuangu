# -*- coding: utf-8 -*-
"""竞涨下限 bidLt 过滤 (2026-09-08 主人反馈「名单常现大跌票」)
筛选历史上只有上限 bidGt(防追高开), 无下限 → 竞价大额低开(资金出逃形态)的票稳定入选:
深中华A(-5.46)/四方精创(-5.31)/恒宝(-2.99) 等 9/7 全天被选上百次且当日均收跌 3.5-6.7%。
bidLt: 竞价涨幅低于该值剔除; 默认 -50(不触发, 老用户行为不变); 前端 FilterPanel 可配「竞涨 ≥」。
"""
from app.services.scorer import validate_filters, apply_filters, score_all_stocks


def _row(f12="600000", f615=3.0, f3=3.0, f616=5.0e8, name="测试股"):
    return {"f12": f12, "f14": name, "f2": 9.38, "f3": f3, "f4": 10.0, "f5": 1000,
            "f6": 5.0e8, "f8": 3.0, "f21": 5e10, "f616": f616, "f617": 1e6, "f630": 0,
            "f615": f615}


def _f(**kw):
    q = {"stSuspend": ["0"], "limitUp": ["0"], "markets": ["hs,cyb,kcb"], "bidGt": ["10"],
         "floatMvFloor": ["30"], "floatMvGt": ["1000"], "priceGt": ["300"],
         "bidAmtFloor": ["3000"], "probLt": ["5"], "confLt": ["50"]}
    q.update(kw)
    return validate_filters(q)


def _scored(f615, day_bid_change=None):
    chg = day_bid_change or {}
    return score_all_stocks([_row(f615=f615)], {}, {}, qiangchou_codes=None,
                            day_bid_amt={}, day_bid_change=chg)


def test_default_no_bidlt_keeps_low_open():
    """默认(不带 bidLt) = 不设下限: 竞价 -5.46%(深中华A 形态)保留(老行为不变)"""
    scored = _scored(f615=-5.46, day_bid_change={"600000": -5.46})
    out = apply_filters(scored, _f())
    assert len(out) == 1, "未设 bidLt 时负竞涨不应被剔除"


def test_bidlt_drops_big_low_open():
    """bidLt=-2: 竞价 -5.46 剔除; 竞涨 -1 保留(±2 缓冲内)"""
    f = _f(bidLt=["-2"])
    # 低开深绿 → 剔除
    out1 = apply_filters(_scored(f615=-5.46, day_bid_change={"600000": -5.46}), f)
    assert out1 == [], "竞涨 -5.46 < 下限 -2 应剔除"
    # 近平开 → 保留
    out2 = apply_filters(_scored(f615=-1.0, day_bid_change={"600000": -1.0}), f)
    assert len(out2) == 1, "竞涨 -1.0 ≥ 下限 -2 应保留"


def test_bidlt_zero_keeps_only_rising():
    """bidLt=0: 竞价上涨才保留 — 深中华A(-5.46)与恒宝(-2.99)均剔除"""
    f = _f(bidLt=["0"])
    for chg in (-5.46, -2.99, -0.5):
        out = apply_filters(_scored(f615=chg, day_bid_change={"600000": chg}), f)
        assert out == [], "竞涨 %.2f < 0 应剔除" % chg
    out_ok = apply_filters(_scored(f615=0.5, day_bid_change={"600000": 0.5}), f)
    assert len(out_ok) == 1, "竞涨 +0.5 应保留"


def test_bidlt_does_not_break_upper_bound():
    """bidLt 与 bidGt 并存: 竞涨 -5.46 剔除(下限), +8.5 剔除(上限), +3 保留"""
    f = _f(bidLt=["-2"], bidGt=["7"])
    assert apply_filters(_scored(f615=-5.46, day_bid_change={"600000": -5.46}), f) == []
    assert apply_filters(_scored(f615=8.5, day_bid_change={"600000": 8.5}), f) == []
    assert len(apply_filters(_scored(f615=3.0, day_bid_change={"600000": 3.0}), f)) == 1


def test_spot_path_also_applies_bidlt():
    """盘中 spot 与竞价共用 process_all_stocks→apply_filters, bidLt 同样生效"""
    from app.services.scorer import process_all_stocks
    f = _f(bidLt=["-2"])
    raw = [_row(f615=-5.46)]
    out = process_all_stocks(raw, f, {}, {}, qiangchou_codes=None,
                             day_bid_amt={"600000": 5000.0}, day_bid_change={"600000": -5.46})
    assert out == [], "盘中路径 bidLt=-2 也应剔除竞涨 -5.46"
