# -*- coding: utf-8 -*-
"""评分门槛 scoreFloor(2026-09-10 主人拍板全站默认 80; 2026-09-20 主人拍板降到 50,
低于 50 分的票不显示)

防复发断言:
  1. validate_filters 默认 scoreFloor=50(全站默认, 不传参也生效)
  2. 评分 < scoreFloor 的票一律剔除, **不看可信度** —— 与 probLt 双低剔除(高信心
     可救低概率)是两条独立规则, 高信心不能让低分票过关
  3. 剔除原因可观测(stats['score_floor']), 排查"票为什么没了"不用人肉复算
  4. clamp 下限=50(2026-09-20 起 0 不再合法, 50 是系统铁底)
"""
import pytest

from app.services import scorer
from app.services.picker import filter as pf
from app.services.picker.contract import QuoteRow
from app.services.picker.score import ScoreResult, ScoredRow


def _mk(code="600000", name="浦发", prob=85, conf=80):
    r = QuoteRow(code=code, name=name, bid_change=3.0, bid_vol=4.8e6,
                 warn_type=2, float_mv=55e8, yesterday_change=2.0, price=10.5,
                 bid_amt=5.0e7, prev_close=10.15, vol=4.8e6)
    return ScoredRow(row=r, score=ScoreResult(probability=prob, confidence=conf))


def _base(**over):
    f = {"stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
         "bidGt": 7, "probLt": 50, "confLt": 50,
         "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000}
    f.update(over)
    return f


# ==================== 默认值 ====================
def test_validate_filters_default_is_50():
    """全站默认 50: 调用方不传 scoreFloor 也生效"""
    f = scorer.validate_filters({})
    assert f["scoreFloor"] == 50


def test_validate_filters_clamps_and_floor_min_50():
    """clamp 下限=50(0 不再合法); 越界值被夹到 50~100"""
    assert scorer.validate_filters({"scoreFloor": ["0"]})["scoreFloor"] == 50
    assert scorer.validate_filters({"scoreFloor": ["95"]})["scoreFloor"] == 95
    assert scorer.validate_filters({"scoreFloor": ["999"]})["scoreFloor"] == 100


# ==================== 过滤行为 ====================
def test_below_floor_dropped():
    out = pf.apply_filters([_mk("600000", prob=79), _mk("600001", prob=80),
                            _mk("600002", prob=91)],
                           _base(scoreFloor=80), pf.FilterContext(zt_codes=set()))
    # apply_filters 不排序(保持入参顺序), 排序由 score_rows 负责
    assert sorted(i.code for i in out.kept) == ["600001", "600002"]
    assert out.stats.get("score_floor") == 1


def test_high_confidence_cannot_save_low_score():
    """与双低剔除独立: conf=90(远高于 confLt=65) 也救不了 prob=70 的票"""
    out = pf.apply_filters([_mk(prob=70, conf=90)],
                           _base(scoreFloor=80), pf.FilterContext(zt_codes=set()))
    assert out.kept == []
    assert out.stats.get("score_floor") == 1


def test_double_low_still_works_when_floor_off():
    """scoreFloor=0 关闭门槛 → 退回老行为: 只有双低才剔除"""
    out = pf.apply_filters([_mk(prob=70, conf=90), _mk("600001", prob=49, conf=49)],
                           _base(scoreFloor=0), pf.FilterContext(zt_codes=set()))
    assert [i.code for i in out.kept] == ["600000"]
    assert out.stats.get("prob_conf") == 1
    assert "score_floor" not in out.stats


def test_missing_key_defaults_to_disabled():
    """调用方没给 scoreFloor(老调用点) → 门槛关闭, 不得静默启用硬门槛"""
    out = pf.apply_filters([_mk(prob=60)], _base(), pf.FilterContext(zt_codes=set()))
    assert len(out.kept) == 1


# ==================== 降级豁免 (2026-09-11) ====================
def test_degraded_exempts_floor():
    """补丁源全失败(东财点查+腾讯点查都断)时, 候选行只有 9:25 定格字段 →
    算出来的 probability 是**保守占位分**(换手/量比/异动/昨日涨幅全缺), 不是
    "这只票差"。此时 scoreFloor 会把"点查失败 → 快照行直出保名单"这条降级
    保命路径整批砍成空名单(2026-09-11 实测 2 例全空)。→ 豁免评分门槛。"""
    ctx = pf.FilterContext(zt_codes=set(), score_floor_exempt=True)
    out = pf.apply_filters([_mk(prob=40)], _base(scoreFloor=80), ctx)
    assert [i.code for i in out.kept] == ["600000"], "降级时低占位分不得被 scoreFloor 砍掉"
    assert "score_floor" not in out.stats


def test_degraded_exempt_does_not_skip_other_filters():
    """豁免只作用于评分门槛: 板块/竞涨/市值/竞额等硬条件在降级时**照常生效**
    (防"降级 = 不过滤"的过度放行)"""
    ctx = pf.FilterContext(zt_codes=set(), score_floor_exempt=True)
    # 竞价涨幅 9 > bidGt 7 → 仍须剔除
    row = _mk(prob=40)
    row.row.bid_change = 9.0
    out = pf.apply_filters([row], _base(scoreFloor=80), ctx)
    assert out.kept == []
    assert out.stats.get("bid_gt") == 1
