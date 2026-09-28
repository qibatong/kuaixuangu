# -*- coding: utf-8 -*-
"""盘中实时选股(spot)重建测试 —— 2026-09-28

背景: spot 于 2026-09-09(提交 4c56083)整体下线, 参数零消费点。本次重建后
需锁死两件事:
  1. 评分六因子口径与原实现(4c56083^ scorer.compute_score_spot)逐值一致;
  2. **6 个盘中参数真的被消费**(这是下线期间暴露的静默失效: 前端能勾、
     选了不生效 → 用户以为在过滤其实没有)。

防复发断言(每条对应一次真实坑):
  A. 缺失字段走 default 分, 不冒充 0(契约铁律1)
  B. chgGt/chgFloor 用**实时**涨幅判定(不是竞价涨幅)
  C. volRatioFloor/turnoverFloor/turnoverGt 真的生效
  D. spotExcludeZT 剔除已涨停
  E. 竞价专用参数(bidAmtFloor)不参与 spot 过滤
"""
import pytest

from app.services.picker.contract import QuoteRow
from app.services.picker.filter import apply_spot_filters, FilterContext
from app.services.picker.score import ScoredRow
from app.services.picker.score_spot import (DEFAULT_SCORING_SPOT,
                                            compute_score_spot, get_spot_cfg)


def _cfg():
    return get_spot_cfg()


def _row(**kw):
    return QuoteRow(**kw)


def _f(**kw):
    """完整筛选参数(照 scorer.validate_filters 输出的 18 键, 便于按需覆盖)。"""
    base = {
        "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
        "bidGt": 7.0, "bidLt": 0.0, "probLt": 50.0, "confLt": 50.0, "scoreFloor": 50.0,
        "floatMvFloor": 30.0, "floatMvGt": 100.0, "priceGt": 30.0, "bidAmtFloor": 3000.0,
        "chgFloor": 0.0, "chgGt": 9.5, "volRatioFloor": 1.0, "turnoverFloor": 1.0,
        "turnoverGt": 0.0, "spotExcludeZT": False,
    }
    base.update(kw)
    return base


# ==================== A. 评分: 分档取值 ====================
def test_spot_full_fields_hits_buckets():
    """字段完备: 六因子按分档表取值。

    实时涨幅 4.0 ∈ [3,6) → 1.0; 量比 2.5 ∈ [2,99) → 1.0;
    换手 5.0 ∈ [3,15) → 1.0; 封成比: 封单 3 亿 / 市值 50 亿 = 6% ∈ [2,99) → 1.0;
    市值 50 亿 ∈ [30,60) → 0.88; 昨日涨幅 2.0 ∈ [1,3) → 0.65。
    """
    r = _row(code="600000", real_change=4.0, vol_ratio=2.5, turnover=5.0,
             free_mv=50e8, yesterday_change=2.0)
    sc = compute_score_spot(r, {"fund": 3.0}, _cfg())
    p = sc.parts
    assert p["chg"]["score"] == 1.0
    assert p["vol_ratio"]["score"] == 1.0
    assert p["turnover"]["score"] == 1.0
    assert p["seal"]["score"] == 1.0
    assert p["market"]["score"] == 0.88
    assert p["yesterday"]["score"] == 0.65
    assert sc.seal_ratio == 6.0
    # 1.0*0.28 + 1.0*0.26 + 1.0*0.18 + 1.0*0.14 + 0.88*0.08 + 0.65*0.06
    # = 0.28+0.26+0.18+0.14+0.0704+0.039 = 0.9694 → 96.94 → 钳 95 → round 95
    assert sc.probability == 95
    # conf = 65 + 12(封成比>=2) + 8(量比>=2) + 6(涨幅∈[1.5,6]) = 91 → 钳 90
    assert sc.confidence == 90


def test_spot_missing_fields_use_default_not_zero():
    """A. 缺失字段走 default 分, 绝不冒充 0(契约铁律1)。

    老实现 parse_float(None)=0.0 → 市值 0 亿落进 market 首桶 ["0","30"] 拿满分 1.0,
    等于"不知道多大"被翻译成"超小盘最优"。
    """
    r = _row(code="600000")                     # 全 None
    sc = compute_score_spot(r, None, _cfg())
    assert sc.parts["chg"]["score"] == 0.1       # default
    assert sc.parts["vol_ratio"]["score"] == 0.15
    assert sc.parts["turnover"]["score"] == 0.15
    assert sc.parts["market"]["score"] == 0.22   # 绝不是 1.0
    assert sc.parts["yesterday"]["score"] == 0.15
    assert sc.seal_ratio == 0.0                  # 非涨停 = 语义 0


def test_spot_seal_ratio_only_for_limit_up():
    """封成比: 无封单 → 0.0(语义 0, 不是缺失); 有封单但市值缺失 → 0.0(无法算)。"""
    r1 = _row(code="600000", real_change=4.0, vol_ratio=2.0, turnover=5.0, free_mv=50e8)
    assert compute_score_spot(r1, None, _cfg()).seal_ratio == 0.0
    r2 = _row(code="600000", real_change=4.0, vol_ratio=2.0, turnover=5.0)  # 无市值
    assert compute_score_spot(r2, {"fund": 3.0}, _cfg()).seal_ratio == 0.0


def test_spot_weight_table_matches_legacy():
    """权重表与原实现(4c56083^)逐值一致 —— 重建不得改口径。"""
    c = DEFAULT_SCORING_SPOT
    assert c["w_chg"] == 0.28
    assert c["w_vol_ratio"] == 0.26
    assert c["w_turnover"] == 0.18
    assert c["w_seal"] == 0.14
    assert c["w_market"] == 0.08
    assert c["w_yesterday"] == 0.06
    assert abs(sum([c["w_chg"], c["w_vol_ratio"], c["w_turnover"],
                    c["w_seal"], c["w_market"], c["w_yesterday"]]) - 1.0) < 1e-9
    assert c["conf_seal_high"] == 12
    assert c["conf_vol_ratio"] == 8
    assert c["conf_chg"] == 6


# ==================== B~E. 过滤: 6 个参数必须真消费 ====================
def _sr(row, prob=90, conf=80):
    from app.services.picker.score_spot import SpotScoreResult
    class _S:
        probability = prob
        confidence = conf
    return ScoredRow(row=row, score=_S())


def test_B_chg_gt_uses_realtime_not_bid_change():
    """B. chgGt 判定用**实时涨幅**(real_change), 不是竞价涨幅(bid_change)。

    用竞价涨幅判定会让"竞价好看但盘中已跳水"的票混入 —— 这正是 spot 与
    auction 的本质差异。
    """
    # 实时 12%(超上限 9.5), 但竞价 5%(在范围内) → 必须被 chg_gt 剔除
    r = _row(code="600000", real_change=12.0, bid_change=5.0, vol_ratio=2.0,
             turnover=5.0, free_mv=50e8, price=10.0)
    out = apply_spot_filters([_sr(r)], _f(chgGt=9.5))
    assert len(out.kept) == 0
    assert out.stats.get("chg_gt") == 1

    # 实时 5% / 竞价 5% → 通过
    r2 = _row(code="600001", real_change=5.0, bid_change=5.0, vol_ratio=2.0,
              turnover=5.0, free_mv=50e8, price=10.0)
    out2 = apply_spot_filters([_sr(r2)], _f(chgGt=9.5))
    assert len(out2.kept) == 1


def test_B2_chg_floor_rejects_dump():
    """跌到 -5% 的票被 chgFloor=0 剔除(低开/大跌不追)。"""
    r = _row(code="600000", real_change=-5.0, vol_ratio=2.0, turnover=5.0,
             free_mv=50e8, price=10.0)
    out = apply_spot_filters([_sr(r)], _f(chgFloor=0.0))
    assert out.stats.get("chg_floor") == 1


def test_C_vol_ratio_floor_effective():
    """C1. volRatioFloor 生效: 量比 0.8 < 1.0 → 剔除。"""
    r = _row(code="600000", real_change=5.0, vol_ratio=0.8, turnover=5.0,
             free_mv=50e8, price=10.0)
    out = apply_spot_filters([_sr(r)], _f(volRatioFloor=1.0))
    assert out.stats.get("vol_ratio") == 1
    # 量比缺失 → 也剔除(无法证明达标)
    r2 = _row(code="600001", real_change=5.0, turnover=5.0, free_mv=50e8, price=10.0)
    out2 = apply_spot_filters([_sr(r2)], _f(volRatioFloor=1.0))
    assert out2.stats.get("vol_ratio") == 1
    # 关掉门槛(0) → 放行
    out3 = apply_spot_filters([_sr(r2)], _f(volRatioFloor=0))
    assert len(out3.kept) == 1


def test_C2_turnover_floor_and_gt_effective():
    """C2. turnoverFloor / turnoverGt 生效: 换手 0.5 低于下限 → 剔; 30 高于上限 → 剔。"""
    r_low = _row(code="600000", real_change=5.0, vol_ratio=2.0, turnover=0.5,
                 free_mv=50e8, price=10.0)
    out = apply_spot_filters([_sr(r_low)], _f(turnoverFloor=1.0))
    assert out.stats.get("turnover_floor") == 1

    r_hi = _row(code="600001", real_change=5.0, vol_ratio=2.0, turnover=30.0,
                free_mv=50e8, price=10.0)
    out2 = apply_spot_filters([_sr(r_hi)], _f(turnoverFloor=1.0, turnoverGt=25.0))
    assert out2.stats.get("turnover_gt") == 1

    # turnoverGt=0 = 不限
    out3 = apply_spot_filters([_sr(r_hi)], _f(turnoverFloor=1.0, turnoverGt=0))
    assert len(out3.kept) == 1


def test_D_spot_exclude_zt_effective():
    """D. spotExcludeZT 剔除已涨停封板(买不进)。标记由路由注入 _spot_zt。"""
    r = _row(code="600000", real_change=10.0, vol_ratio=2.0, turnover=5.0,
             free_mv=50e8, price=10.0)
    setattr(r, "_spot_zt", True)
    out = apply_spot_filters([_sr(r)], _f(spotExcludeZT=True, chgGt=0))
    assert out.stats.get("spot_zt") == 1
    # 不勾选 → 保留
    out2 = apply_spot_filters([_sr(r)], _f(spotExcludeZT=False, chgGt=0))
    assert len(out2.kept) == 1


def test_E_bid_amt_floor_not_applied_in_spot():
    """E. 竞价额门槛(bidAmtFloor)**不参与** spot 过滤 —— 盘中距 9:25 已远。

    防复发: 若有人误把竞价 apply_filters 拿来复用, 会把竞价额门槛带进来,
    盘中名单会被 9:25 的旧竞价额错误裁剪。
    """
    r = _row(code="600000", real_change=5.0, vol_ratio=2.0, turnover=5.0,
             free_mv=50e8, price=10.0, bid_amt=None)   # 竞价额缺失
    # bidAmtFloor 设成极大值: 若 spot 错误消费它, 此票必被剔除
    out = apply_spot_filters([_sr(r)], _f(bidAmtFloor=999999.0))
    assert len(out.kept) == 1
    assert "bid_amt" not in out.stats


def test_spot_filters_market_and_still_apply():
    """共用门槛在 spot 下仍生效: 市场范围 / 市值区间 / 价格上限。"""
    # 创业板票在只要沪深时被剔除
    r = _row(code="300001", real_change=5.0, vol_ratio=2.0, turnover=5.0,
             free_mv=50e8, price=10.0)
    out = apply_spot_filters([_sr(r)], _f(markets=["hs"]))
    assert out.stats.get("market") == 1

    # 价格超上限(spot 用实时价)
    r2 = _row(code="600000", real_change=5.0, vol_ratio=2.0, turnover=5.0,
              free_mv=50e8, price=99.0)
    out2 = apply_spot_filters([_sr(r2)], _f(priceGt=30.0))
    assert out2.stats.get("price_gt") == 1

    # 市值超上限
    r3 = _row(code="600000", real_change=5.0, vol_ratio=2.0, turnover=5.0,
              free_mv=500e8, price=10.0)
    out3 = apply_spot_filters([_sr(r3)], _f(floatMvGt=100.0))
    assert out3.stats.get("mv_gt") == 1
