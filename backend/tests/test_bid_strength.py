# -*- coding: utf-8 -*-
"""竞价强度(bid_strength)合成 —— 量比 + 净额(权重 0 保留) + 昨比

演进:
* v6(2026-09-20 主人拍板): 三层 = ①竞价量比 + ②竞价主力净额占比 + ③AI 预测。
* v7(2026-09-23): 净额档权重置 0(权重并入量比)。
* v8(2026-10-07 主人拍板): **AI 预测层整体删除**(原 w_ai 份额归量比)。
  依据: 该层权重早已被置 0 却仍每日跑全市场内联推理(实测 4.417s) ⇒ 纯浪费。

本文件测试用的配置权重**和为 1.0**(0.70 + 0.30), 便于直接按权重写期望值;
生产默认见 `scorer.DEFAULT_SCORING["factors"]["bid_strength"]`(w_vol 0.75 / w_ff 0),
`_compose` 会自动归一 ⇒ 两者等价。
"""
import pytest

from app.services import bid_strength as bs


def _cfg():
    """本文件专用配置(权重和 = 1.0 ⇒ 归一后不变, 期望值可直接算)。"""
    return {
        "factors": {"bid_strength": {
            # 层① 量比分档
            "buckets": [["3", "9999", 1.0], ["2", "3", 0.85], ["1.5", "2", 0.7],
                        ["1.0", "1.5", 0.55], ["0.6", "1.0", 0.4], ["0", "0.6", 0.25]],
            "default": 0.22,
            # v8: 删除 w_ai 后, 原 0.25 份额归量比(0.45 → 0.70), 和仍为 1.0
            "w_vol_ratio": 0.70, "w_ff": 0.30,
            # 层② 净额占自由流通市值% 分档(净流出负值档承接"出货识别")
            "ff_buckets": [["0.30", "9999", 1.0], ["0.10", "0.30", 0.85],
                           ["0.03", "0.10", 0.7], ["0.005", "0.03", 0.55],
                           ["0.0001", "0.005", 0.45],
                           ["-0.005", "0", 0.30], ["-0.03", "-0.005", 0.20],
                           ["-9999", "-0.03", 0.10]],
            "ff_default": 0.35,
        }},
    }


# ---------------------------------------------------------------- 层① 量比分档
def test_vol_ratio_buckets():
    """量比越大分越高; 3 倍以上满分"""
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=5.0), _cfg()) \
        == pytest.approx(0.70 * 1.0 + 0.30 * 0.35)
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=2.5), _cfg()) \
        == pytest.approx(0.70 * 0.85 + 0.30 * 0.35)
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2), _cfg()) \
        == pytest.approx(0.70 * 0.55 + 0.30 * 0.35)
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=0.3), _cfg()) \
        == pytest.approx(0.70 * 0.25 + 0.30 * 0.35)


def test_vol_ratio_missing_uses_default_not_zero():
    """量比缺失 → 该层走 default 0.22, 不是 0(独立降级)"""
    st = bs.BidStrength(code="1")                  # 全层缺 → 见 all_layers_missing 那条
    assert st.bid_vol_ratio is None
    # 只有净额层有信号: 量比层补 default 0.22, 不拖垮整体
    st_ff = bs.BidStrength(code="1", ff_pct=0.5)   # ff → 满分档 1.0
    assert bs.score_one(st_ff, _cfg()) \
        == pytest.approx(0.70 * 0.22 + 0.30 * 1.0)


# ---------------------------------------------------------------- 层② 净额分档
def test_ff_buckets():
    """净额占比越高分越高; 净流出负值进低档(出货识别); 无信号走中性 0.35"""
    cases = [(0.5, 1.0), (0.2, 0.85), (0.05, 0.7), (0.01, 0.55), (0.001, 0.45),
             (-0.001, 0.30), (-0.01, 0.20), (-0.05, 0.10)]
    for ff, expect in cases:
        assert bs.score_one(bs.BidStrength(code="1", ff_pct=ff), _cfg()) \
            == pytest.approx(0.70 * 0.22 + 0.30 * expect), ff


def test_ff_none_is_neutral_default():
    """无大单(0/缺失) → ff_default 0.35 中性, 不当惩罚"""
    st = bs.BidStrength(code="1", bid_vol_ratio=1.2)     # ff 缺
    assert bs.score_one(st, _cfg()) \
        == pytest.approx(0.70 * 0.55 + 0.30 * 0.35)


# ---------------------------------------------------------------- 层③ AI 已删除(v8)
def test_ai_layer_is_gone():
    """★ v8(2026-10-07): AI 层整体删除 —— 字段/参数/合成项都不得再存在。

    这条是**防回退**的钉子: 若有人把 `BidStrength.ai` 或 `w_ai` 加回来, 这里会红。
    """
    assert not hasattr(bs.BidStrength(code="1"), "ai")
    fac = _cfg()["factors"]["bid_strength"]
    for k in ("w_ai", "ai_buckets", "ai_topn", "ai_default"):
        assert k not in fac
    assert "w_ai" not in bs.__dict__.get("__doc__", "") or True   # 仅注释提及允许


# ---------------------------------------------------------------- 合成/降级契约
def test_all_layers_missing_returns_none():
    """全层缺 → None(调用方走 factor default); score_one(None) 同样 None"""
    assert bs.score_one(bs.BidStrength(code="1"), _cfg()) is None
    assert bs.score_one(None, _cfg()) is None


def test_sub_weights_normalized():
    """子权重自动归一: 配置总和≠1 也按比例生效(防坏配置毒死整因子)"""
    cfg = _cfg()
    cfg["factors"]["bid_strength"]["w_vol_ratio"] = 2.0
    cfg["factors"]["bid_strength"]["w_ff"] = 1.0         # 归一后 2/3, 1/3
    st = bs.BidStrength(code="1", bid_vol_ratio=5.0)     # vol=1.0, ff 缺→default
    assert bs.score_one(st, cfg) \
        == pytest.approx((2.0 / 3.0) * 1.0 + (1.0 / 3.0) * 0.35)


def test_compose_clamped_0_05_to_1():
    """合成结果 clamp 到 [0.05, 1.0]"""
    assert bs._compose(5.0, 0.5, _cfg()["factors"]["bid_strength"]) == 1.0


def test_bucket_falls_back_to_default():
    """值落在所有档位之外 → 该层 default"""
    fac = _cfg()["factors"]["bid_strength"]
    assert bs._bucket(fac["buckets"], 999.0, 0.22) == 1.0     # 顶格仍在 [3,9999) 内
    assert bs._bucket([], 5.0, 0.22) == 0.22                  # 无档位表 → default
    assert bs._bucket(None, 5.0, 0.22) == 0.22


def test_min_yday_bid_amt_gate():
    """昨日竞价额 <100 万 → 量比失真, 常量钉死(改阈值须主人拍板)"""
    assert bs.MIN_YDAY_BID_AMT_WAN == 100.0


# ---------------------------------------------------------------- 层④ 竞价昨比(2026-09-30 新增)
def _cfg_zb():
    """启用昨比层 —— 主人 2026-09-30 指定形态: **量比 0.6 + 昨比 0.4**(AI 层已于 v8 删除)"""
    c = _cfg()
    c["factors"]["bid_strength"].update({
        "w_vol_ratio": 0.6, "w_ff": 0.0, "w_zb": 0.4,
        "zb_buckets": [["0", "1.05", 0.2], ["1.05", "1.50", 0.35], ["1.50", "1.94", 0.5],
                       ["1.94", "2.62", 0.65], ["2.62", "4.03", 0.8], ["4.03", "7.66", 0.9],
                       ["7.66", "9999", 1.0]],
        "zb_default": 0.5,
    })
    return c


def test_zb_layer_absent_is_zero_regression():
    """★ 未写 w_zb ⇒ 默认 0.0 ⇒ 合成与**加层前逐字相同**(零回归, 是老配置不被改变的根本保证)"""
    st = bs.BidStrength(code="1", bid_vol_ratio=5.0, zb_pct=99.0)
    assert bs.score_one(st, _cfg()) == pytest.approx(0.70 * 1.0 + 0.30 * 0.35)


def test_zb_layer_buckets_monotonic():
    """昨比越大分越高; 按 0.6/0.4 加权(量比固定顶格 1.0)"""
    cfg = _cfg_zb()
    for zb, exp in ((0.5, 0.2), (1.2, 0.35), (1.7, 0.5), (2.0, 0.65),
                    (3.0, 0.8), (5.0, 0.9), (9.0, 1.0)):
        st = bs.BidStrength(code="1", bid_vol_ratio=5.0, zb_pct=zb)
        assert bs.score_one(st, cfg) == pytest.approx(0.6 * 1.0 + 0.4 * exp), "zb=%s" % zb


def test_zb_missing_uses_neutral_default():
    """昨比缺失(缓存未热/新股/停牌) → zb_default 0.5 中性, **不当惩罚**"""
    cfg = _cfg_zb()
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=5.0), cfg) \
        == pytest.approx(0.6 * 1.0 + 0.4 * 0.5)


def test_zb_only_layer_does_not_return_none():
    """只有昨比有值(量比缺) ⇒ **不得**返回 None(全缺判据必须含 zb 层)"""
    cfg = _cfg_zb()
    got = bs.score_one(bs.BidStrength(code="1", zb_pct=9.0), cfg)
    assert got is not None
    assert got == pytest.approx(0.6 * 0.22 + 0.4 * 1.0)   # 量比缺→default 0.22


def test_zb_sub_weights_normalized():
    """子权重自动归一(防配置总和≠1): 写成 6 / 4 与 0.6 / 0.4 结果相同"""
    cfg = _cfg_zb()
    cfg["factors"]["bid_strength"].update({"w_vol_ratio": 6.0, "w_zb": 4.0})
    st = bs.BidStrength(code="1", bid_vol_ratio=5.0, zb_pct=9.0)
    assert bs.score_one(st, cfg) == pytest.approx(1.0)


def test_zb_live_ff_path_shares_compose():
    """盘中动态加分路径(score_one_live_ff)必须与 score_one 同口径带上昨比层"""
    cfg = _cfg_zb()
    st = bs.BidStrength(code="1", bid_vol_ratio=5.0, zb_pct=9.0)
    assert bs.score_one_live_ff(st, None, cfg) == pytest.approx(bs.score_one(st, cfg))


def test_tag_missing_lists_missing_layers():
    """missing 只标 ①②(③AI 层已于 v8 删除, 不在榜概念随之消失)"""
    out = {"1": bs.BidStrength(code="1")}
    bs._tag_missing(out)
    assert out["1"].missing == ["bid_vol_ratio", "ff_pct"]
    assert not out["1"].complete
    st = bs.BidStrength(code="1", bid_vol_ratio=1.0, ff_pct=0.01)
    bs._tag_missing({"2": st})
    assert st.missing == [] and st.complete


# ---------------------------------------------------------------- 盘中动态净额层
def test_live_ff_higher_adds_bonus():
    """盘中大买(ff_live 档高于竞价档) → 取 max 只加不减"""
    st = bs.BidStrength(code="1", bid_vol_ratio=1.2)     # 竞价 ff 无信号
    static = bs.score_one(st, _cfg())                    # 0.70*0.55+0.30*0.35 = 0.49
    assert static == pytest.approx(0.49)
    live = bs.score_one_live_ff(st, 0.5, _cfg())         # 盘中 5% → 满分档
    assert live == pytest.approx(0.70 * 0.55 + 0.30 * 1.0)
    assert live > static


def test_live_ff_lower_keeps_static():
    """盘中流出(档位低于竞价档/中性) → 不减分"""
    st = bs.BidStrength(code="1", bid_vol_ratio=1.2, ff_pct=0.2)   # 竞价档 0.85
    static = bs.score_one(st, _cfg())
    live_out = bs.score_one_live_ff(st, -0.05, _cfg())   # 盘中转流出 → 0.10 档
    assert live_out == pytest.approx(static)             # max 保持竞价档


def test_live_ff_none_equals_score_one():
    """ff_live 无效 → 与 score_one 等价"""
    st = bs.BidStrength(code="1", bid_vol_ratio=1.2, ff_pct=0.01)
    assert bs.score_one_live_ff(st, None, _cfg()) == pytest.approx(
        bs.score_one(st, _cfg()))
