# -*- coding: utf-8 -*-
"""重构 P2 评分层测试

防复发断言(每条对应一次真实事故或语义坑):
  1. 流通市值缺失不得拿满分(老链路 f21=0 → 落 ["0","30"] 桶 = 1.0 分, 权重 11%)
  2. 昨日涨幅缺失不得落 ["0","1"] 桶拿 0.4 分(老链路 f3/None → 0 → 冒充"昨日微涨")
  3. 竞价涨幅缺失不得退化成当日涨幅(9/7 大跌票混入根因) — 走 default 0.1
  4. 置信度加成: 字段缺失一律不加成(老链路缺失=0 → 同样不加成, 行为需一致)
  5. 分档/取整与老 scorer 逐值一致(由 test_admin 分档表用例逐值锁死)
"""
import pytest

from app.services import scorer
from app.services.picker.contract import QuoteRow
from app.services.picker.score import ScoredRow, compute_score, score_rows


def _cfg():
    return scorer.get_scoring_cfg()


def _row(**kw):
    return QuoteRow(**kw)


# ==================== 分档取值 ====================
def test_full_fields_hits_buckets():
    """字段完备: 各因子按分档表取值"""
    r = _row(code="600000", bid_change=3.0, bid_vol=4.8e6, warn_type=2,
             float_mv=55e8, yesterday_change=2.0, price=10.5)
    s = compute_score(r, _cfg())
    p = s.parts
    assert p["bid"]["score"] == 1.0            # 3.0 ∈ [3, 5.5)
    assert p["activity"]["score"] == 1.0       # 0.916% ∈ [0.8, 99)
    assert p["warn"]["score"] == 0.18          # 2 不在 [3,6) → default
    assert p["market"]["score"] == 0.88        # 55亿 ∈ [30, 60)
    assert p["yesterday"]["score"] == 0.65     # 2.0 ∈ [1, 3)
    assert s.probability == 83
    assert s.confidence == 80                  # 65 + 换手8 + 竞价7


def test_missing_float_mv_never_gets_full_mark():
    """市值缺失 → default 0.22, **绝不能**落进 ["0","30"] 桶拿 1.0 分。
    老链路 parse_float(None)=0.0 → 0 亿 → 满分 1.0(等于"不知道多大"≈"超小盘最优")。"""
    r = _row(code="600000", bid_change=3.0, bid_vol=4.8e6, warn_type=2,
             float_mv=None, yesterday_change=2.0, price=10.5)
    s = compute_score(r, _cfg())
    assert s.parts["market"]["value"] is None
    assert s.parts["market"]["score"] == 0.22           # default, 非 1.0
    assert s.parts["market"]["score"] != 1.0
    # 对照: 真·0 市值(不可能存在)与缺失语义不同
    r0 = _row(code="600000", float_mv=0.0, bid_change=3.0, bid_vol=4.8e6,
              warn_type=2, yesterday_change=2.0, price=10.5)
    assert compute_score(r0, _cfg()).parts["market"]["score"] == 0.22   # 0 也被当缺失


def test_missing_yesterday_never_gets_positive_bucket():
    """昨日涨幅缺失 → default 0.15, 不得落 ["0","1"] 桶拿 0.4(冒充"昨日微涨")"""
    r = _row(code="600000", bid_change=3.0, bid_vol=4.8e6, warn_type=2,
             float_mv=55e8, yesterday_change=None, price=10.5)
    s = compute_score(r, _cfg())
    assert s.parts["yesterday"]["value"] is None
    assert s.parts["yesterday"]["score"] == 0.15


def test_missing_bid_change_never_uses_real_change():
    """竞价涨幅缺失 → default 0.1; 即便 row.real_change 有值也不得拿来顶替
    (老链路 f615 缺失退 f3 = 拿当日涨幅当竞价涨幅, 9/7 大跌票混入根因)"""
    r = _row(code="600000", bid_change=None, real_change=-5.0, bid_vol=4.8e6,
             warn_type=2, float_mv=55e8, yesterday_change=2.0, price=10.5)
    s = compute_score(r, _cfg())
    assert s.parts["bid"]["value"] is None
    assert s.parts["bid"]["score"] == 0.1
    # -5% 若被拿去打分会落 default 0.1 之外的桶(桶里无负数区间, 实际也是 0.1),
    # 因此额外断言: 缺失与"真实 -5%"的置信度处理不同(缺失不加 conf_bid)
    r2 = _row(code="600000", bid_change=-5.0, real_change=-5.0, bid_vol=4.8e6,
              warn_type=2, float_mv=55e8, yesterday_change=2.0, price=10.5)
    assert compute_score(r2, _cfg()).parts["bid"]["value"] == -5.0


def test_confidence_bonus_requires_known_value():
    """置信度加成: 缺失一律不加成"""
    base = _row(code="600000", bid_change=3.0, bid_vol=4.8e6, warn_type=4,
                float_mv=55e8, yesterday_change=2.0, price=10.5)
    s = compute_score(base, _cfg())
    assert s.confidence == 90          # 65+10(warn)+8(换手)+7(竞价) → 钳到 90
    miss = _row(code="600000", bid_change=None, bid_vol=None, warn_type=None,
                float_mv=55e8, yesterday_change=2.0, price=10.5)
    assert compute_score(miss, _cfg()).confidence == 65     # 三项加成全部不加


def test_missing_bid_turnover_uses_default():
    """竞价换手缺失(无竞价额/竞价量) → default 0.1, 不得算成 0 再落桶"""
    r = _row(code="600000", bid_change=3.0, warn_type=2, float_mv=55e8,
             yesterday_change=2.0, price=10.5)      # 无 bid_amt/bid_vol → None
    s = compute_score(r, _cfg())
    assert r.bid_turnover is None
    assert s.parts["activity"]["score"] == 0.1


def test_bid_turnover_uses_amount_not_volume():
    """竞价换手 = 竞价额÷流通市值(不依赖竞价量 — 快照表无 bid_vol 字段)"""
    r = _row(code="600000", float_mv=55e8, bid_amt=3.0e7)     # 3000万 / 55亿
    assert r.bid_vol is None
    assert r.bid_turnover == pytest.approx(3.0e7 / 55e8 * 100)
    # 无竞价额时退回 竞价量×价 兜底
    r2 = _row(code="600000", float_mv=55e8, bid_vol=4.8e6, price=10.5)
    assert r2.bid_turnover == pytest.approx(4.8e6 * 10.5 / 55e8 * 100)


# ==================== 批量与排序 ====================
def test_score_rows_sorted_by_probability():
    rows = [
        _row(code="000001", bid_change=1.0, bid_vol=1e5, warn_type=0,
             float_mv=200e8, yesterday_change=0.0, price=5.0),
        _row(code="600000", bid_change=3.0, bid_vol=4.8e6, warn_type=2,
             float_mv=55e8, yesterday_change=2.0, price=10.5),
        _row(code="300001", bid_change=4.5, bid_vol=3e6, warn_type=1,
             float_mv=40e8, yesterday_change=5.0, price=20.0),
    ]
    out = score_rows(rows, _cfg())
    assert [o.code for o in out][0] in ("600000", "300001")
    probs = [o.score.probability for o in out]
    assert probs == sorted(probs, reverse=True)


def test_scored_row_to_dict_shape():
    """输出字段与老链路 item 同构(前端零改动)"""
    r = _row(code="600000", name="浦发银行", bid_change=3.0, bid_vol=4.8e6,
             warn_type=2, float_mv=55e8, yesterday_change=2.0, price=10.5,
             bid_amt=5.0e7, real_change=3.4, open=10.2, vol=4.8e6,
             turnover=0.9, vol_ratio=1.8, industry="银行", concept="金融")
    it = ScoredRow(row=r, score=compute_score(r, _cfg())).to_dict()
    for k in ("code", "name", "probability", "confidence", "bidChange",
              "realChange", "entityChange", "bidTurnover", "warnType",
              "circulationMV", "industry", "concept", "bidAmt", "price",
              "volRatio", "turnover"):
        assert k in it, k
    assert it["bidAmt"] == 5000.0            # 元 → 万元
    assert it["circulationMV"] == 55.0
    assert it["entityChange"] == pytest.approx((10.5 - 10.2) / 10.2 * 100)


# ==================== 负竞涨低分桶 (2026-09-09) ====================
def test_negative_bid_change_gets_penalty_bucket():
    """中石科技事故: 竞涨 -8.01% 曾落不进正分档桶 → 与"数据缺失"同吃 default 0.1,
    34% 权重只扣 3.4 分, 负竞涨照样靠换手/强度凑分入选。现显式负桶 [-99,0) → 0.05。"""
    s = compute_score(_row(code="300684", bid_change=-8.01, bid_vol=4.8e6,
                           warn_type=2, float_mv=196e8, yesterday_change=17.39,
                           price=88.23), _cfg())
    assert s.parts["bid"]["value"] == -8.01
    assert s.parts["bid"]["score"] == 0.05
    # 平开 0% 也落负桶(0 ∈ [-99, 0)); 微涨 0.5% 仍走低正桶 0.4
    assert compute_score(_row(code="600000", bid_change=0.0), _cfg()).parts["bid"]["score"] == 0.05
    assert compute_score(_row(code="600000", bid_change=0.5), _cfg()).parts["bid"]["score"] == 0.4
    # 缺失仍 default 0.1(不知道 ≠ 差, 语义必须区分)
    assert compute_score(_row(code="600000", bid_change=None), _cfg()).parts["bid"]["score"] == 0.1
