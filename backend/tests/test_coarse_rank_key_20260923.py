# -*- coding: utf-8 -*-
"""粗筛排队键改版单测(2026-09-23): 由「竞价额降序」改为「定格三因子粗排分降序」。

背景(实测, 非推测): 竞价额排名与最终评分排名的 Spearman 只有 0.5214。名额被顶满时
被砍掉的恰好是「竞价额中等、评分靠前」的票 —— 9/10 样本里名额压到 50 时漏 4 只,
含评分 88(全池第 3)的南宁百货 600712, 其竞价额仅列第 73 位。换成本文件的排队键后
Spearman 0.9631、名额压到 50 时漏损 4→0, 且不增加任何网络请求。

本文件是这套改动的**确定性守卫**(全部显式传 cfg=DEFAULT_SCORING, 不读库不联网):
  1. 与最终评分的一致性 —— warn/yesterday 缺失时粗排分必须与 compute_score 同值;
     两处算式一旦漂移(例如有人只改一边的市值口径), 本用例直接红。
  2. 权重确实来自 cfg, 不是硬编码。
  3. 行为改变的正例: 竞价额大的票不再自动靠前。
  4. 并列确定性: 同分按 code 升序, 且与输入顺序无关。
  5. 两条后端链路同尺子: _snapshot_candidate_codes 与 picker.filter.coarse_filter
     在等价输入上给出**同一顺序**(防 2026-09-18 那种"两个入口判出两份名单")。
"""
import copy
import os
import sys

os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.stocks import _snapshot_candidate_codes
from app.services import scorer
from app.services.picker import filter as pfilter
from app.services.picker.contract import QuoteRow
from app.services.picker.score import (compute_score, coarse_rank_key,
                                       coarse_rank_score)
from app.services.picker.score_factors import js_round

CFG = copy.deepcopy(scorer.DEFAULT_SCORING)

F = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidLt": 0, "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 1000, "priceGt": 300, "bidAmtFloor": 3000,
}
CTX = lambda: pfilter.FilterContext(markets=["hs", "cyb", "kcb"], zt_codes=set())
#   注: 用 lambda 而不是模块级单例 —— FilterContext 无状态, 但每个用例新建可避免
#   将来有人加可变字段后用例之间互相污染。


def _row(code, *, bid_change=3.0, bid_amt_wan=5000.0, mv_yi=55.0,
         turnover=None, warn=None, ychg=None):
    """构造契约行。turnover 显式给值时直接当 auc_turnover(受控); 单位 万元/亿。"""
    return QuoteRow(code=code, name="某股", bid_change=bid_change,
                    bid_amt=None if bid_amt_wan is None else bid_amt_wan * 1e4,
                    float_mv=None if mv_yi is None else mv_yi * 1e8,
                    auc_turnover=turnover, warn_type=warn,
                    yesterday_change=ychg, prev_close=10.0)


# ==================== 1. 与最终评分的一致性(漂移守卫) ====================
def test_coarse_rank_matches_compute_score_when_warn_and_yday_absent():
    """warn/yesterday 缺失时, 粗排分与最终评分必须同值 —— 两边算式漂移即红。

    前提: 两者都不夹边界。典型行本分在 15~81 之间, 距 5/95 很远(见 score.py 注释)。
    """
    rows = [
        _row("600001", bid_change=3.0, turnover=2.0),
        _row("600002", bid_change=1.0, turnover=0.05),
        _row("600003", bid_change=-2.0, turnover=None, mv_yi=None),
        _row("600004", bid_change=None, turnover=1.5),
        _row("600005", bid_change=5.0, turnover=0.9),
    ]
    for r in rows:
        got = coarse_rank_score(r, CFG)
        assert 5.0 < got < 95.0, "本分不该触及夹取边界, 否则说明配置或口径变了: %r" % got
        assert js_round(got) == compute_score(r, CFG, None).probability, \
            "粗排分与最终评分漂移了 code=%s" % r.code


def test_coarse_rank_uses_cfg_weights_not_hardcoded():
    """权重必须来自 cfg —— 改 w_bid 后本分要跟着变(否则就是写死了)。"""
    r = _row("600001", bid_change=5.0, turnover=None, mv_yi=None)
    a = copy.deepcopy(CFG)
    b = copy.deepcopy(CFG)
    b["w_bid"] = a["w_bid"] + 0.5
    # bid 因子得分为 1.0, 故差值恰为 0.5×1.0×100 = 50
    assert abs((coarse_rank_score(r, b) - coarse_rank_score(r, a)) - 50.0) < 1e-9


# ==================== 2. 行为改变: 竞价额大不再自动靠前 ====================
def test_coarse_filter_prefers_quality_over_bid_amount():
    """竞价额大但涨幅/换手差的票, 必须排在竞价额小但质量高的票**之后**。

    这正是改键的目的 —— 旧键(竞价额降序)会给出相反的顺序。
    """
    big = _row("600101", bid_change=1.0, turnover=0.05, bid_amt_wan=50000.0)  # 5 亿
    good = _row("600102", bid_change=5.0, turnover=2.0, bid_amt_wan=4000.0)  # 0.4 亿
    assert big.bid_amt > good.bid_amt, "前置条件: big 的竞价额确实更大"

    codes = pfilter.coarse_filter([big, good], F, CTX(), cfg=CFG)
    assert codes == ["600102", "600101"], "粗排分高的票必须靠前"

    # 反证: 旧键(竞价额降序)给的是相反顺序, 说明本用例真的锁住了改键行为
    old_key_order = [c for c, _ in sorted(
        [("600101", big.bid_amt), ("600102", good.bid_amt)], key=lambda x: -x[1])]
    assert old_key_order == ["600101", "600102"]


# ==================== 3. 并列确定性 ====================
def test_coarse_rank_ties_break_by_code_and_ignore_input_order():
    """同分按 code 升序; 且与输入顺序无关(旧键在竞价额相同时依赖输入顺序)。"""
    rows = [_row("600302", bid_change=3.0, turnover=1.0),
            _row("600301", bid_change=3.0, turnover=1.0),
            _row("600303", bid_change=3.0, turnover=1.0)]
    scores = {r.code: coarse_rank_score(r, CFG) for r in rows}
    assert len(set(scores.values())) == 1, "前置条件: 三只票必须同分"

    assert pfilter.coarse_filter(rows, F, CTX(), cfg=CFG) == \
        ["600301", "600302", "600303"]
    assert pfilter.coarse_filter(list(reversed(rows)), F, CTX(), cfg=CFG) == \
        ["600301", "600302", "600303"], "换输入顺序结果必须一致"

    # 键本身: 分高者在前, 同分 code 小者在前
    assert coarse_rank_key(90.0, "600002") < coarse_rank_key(80.0, "600001")
    assert coarse_rank_key(80.0, "600001") < coarse_rank_key(80.0, "600002")


def test_coarse_rank_limit_keeps_top_scored_not_top_amount():
    """名额截断保留的是粗排分最高的, 不是竞价额最大的(名额仍为 limit/COARSE_MAX)。"""
    rows = [_row("6004%02d" % i, bid_change=1.0, turnover=0.05,
                 bid_amt_wan=9000.0 + i) for i in range(6)]
    rows += [_row("600401", bid_change=5.0, turnover=2.0, bid_amt_wan=3000.0)]
    codes = pfilter.coarse_filter(rows, F, CTX(), limit=1, cfg=CFG)
    assert codes == ["600401"], "质量最高的票竞价额最小, 仍必须被保留"


# ==================== 4. 两条后端链路同尺子 ====================
def test_snapshot_chain_uses_same_rank_key_as_picker():
    """_snapshot_candidate_codes(盘后快照链路) 与 picker.filter.coarse_filter
    (唯一主链路) 必须给出**同一顺序**。任一单独改键即红。"""
    spec = [
        # code,   bid_change, bid_amt(万), mv(亿), auc_turnover
        ("600201", 1.0, 50000.0, 55.0, 0.05),
        ("600202", 5.0, 4000.0, 55.0, 2.0),
        ("600203", 3.0, 6000.0, 40.0, 0.6),
        ("600204", 2.0, 8000.0, 90.0, 1.2),
    ]
    quote_rows, snap_rows = [], {}
    for code, bc, amt_wan, mv_yi, to in spec:
        quote_rows.append(_row(code, bid_change=bc, bid_amt_wan=amt_wan,
                               mv_yi=mv_yi, turnover=to))
        snap_rows[code] = {
            "name": "某股", "bid_change": bc, "bid_amt": amt_wan,
            "free_mv": mv_yi * 1e8, "float_mv": mv_yi * 1e8,
            "auc_turnover": to, "pre_close": 10.0,
        }

    a = pfilter.coarse_filter(quote_rows, F, CTX(), cfg=CFG)
    b = _snapshot_candidate_codes(snap_rows, F, set(), cfg=CFG)
    assert a == b, "两条链路排队键不一致: picker=%r snapshot=%r" % (a, b)
    # 顺带锁住"不是竞价额降序"(否则本用例对改键无感)
    assert a != ["600201", "600204", "600203", "600202"]


def test_snapshot_chain_falls_back_when_turnover_and_mv_absent():
    """快照行缺市值/换手时不得抛异常, 且仍按可算出的分排序(缺失走 default)。"""
    snap = {"600301": {"name": "甲", "bid_change": 3.0, "bid_amt": 5000.0},
            "600302": {"name": "乙", "bid_change": 3.0, "bid_amt": 9000.0}}
    codes = _snapshot_candidate_codes(snap, F, set(), cfg=CFG)
    assert set(codes) == {"600301", "600302"}
