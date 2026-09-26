# -*- coding: utf-8 -*-
"""粗筛排队键改版单测(2026-09-26): 由「定格三因子粗排分降序」改为「定格竞价涨幅降序」。

背景(主人指令): 粗筛不再自造复合分, 直接用「当日涨幅榜」这把市场公认的尺子。
口径依据 —— 定格时点(9:25 撮合**之后**) C=O ⇒ **当日涨幅 ≡ 开盘涨幅 ≡ 竞价涨幅**,
三者同值; 而竞价涨幅的权威来源就是定格快照的 `bid_change`(见 contract.FIELD_AUTHORITY)。
**门槛与名额上限(200)一律未动** —— 排队键只决定"触顶时谁被砍掉", 不改变任何票的入选资格。

本文件是这套改动的**确定性守卫**(不读库、不联网; 打分档与权重已不参与排队, 故无需注入 cfg):
  1. 行为改变的正例: 竞价涨幅高的票靠前 —— 且**不是**旧键「竞价额降序」能给出的顺序
     (写死反证断言, 否则本用例对改键无感)。
  2. 键的语义: 就是 (-涨幅, code); 负涨幅照大小排; 缺失排最后且**不冒充 0.0(平开)**。
  3. 并列确定性: 同涨幅按 code 升序, 且与输入顺序无关。
  4. 名额截断保留涨幅最高的(名额仍为 limit/COARSE_MAX=200)。
  5. 两条后端链路同尺子: stocks._snapshot_candidate_codes 与 filter.coarse_filter
     在等价输入上给出**同一顺序**(防 2026-09-18 那种"两个入口判出两份名单")。
  6. 改键**不动门槛**: 涨幅最高但市值超上限的票照样被剔除。
"""
import os
import sys

os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.stocks import _SNAP_CANDIDATE_MAX, _snapshot_candidate_codes
from app.services.picker import filter as pfilter
from app.services.picker.contract import QuoteRow
from app.services.picker.score import COARSE_RANK_MISSING, coarse_rank_key

F = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidLt": 0, "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 1000, "priceGt": 300, "bidAmtFloor": 3000,
}


def CTX(**kw):
    """过滤上下文工厂 —— 用工厂而不是模块级单例: FilterContext 无状态, 但每个用例
    新建可避免将来有人加可变字段后用例之间互相污染。"""
    return pfilter.FilterContext(markets=["hs", "cyb", "kcb"], zt_codes=set(), **kw)


def _row(code, *, bid_change=3.0, bid_amt_wan=5000.0, mv_yi=55.0,
         turnover=None, warn=None, ychg=None):
    """构造契约行(bid_amt_wan 单位万元 / mv_yi 单位亿)。"""
    return QuoteRow(code=code, name="某股", bid_change=bid_change,
                    bid_amt=None if bid_amt_wan is None else bid_amt_wan * 1e4,
                    float_mv=None if mv_yi is None else mv_yi * 1e8,
                    auc_turnover=turnover, warn_type=warn,
                    yesterday_change=ychg, prev_close=10.0)


# ==================== 1. 行为改变: 竞价涨幅高的靠前 ====================
def test_coarse_filter_orders_by_frozen_bid_change_desc():
    """涨幅最高的票必须排最前 —— 而旧键「竞价额降序」给的是**相反**顺序。

    hot 特意给了最小的竞价额、最大的市值(旧复合键里的 activity/market 两项都会把它
    往下拉), cold 则相反 ⇒ 只有"按涨幅排"才能得出本顺序。
    """
    hot = _row("600102", bid_change=6.5, bid_amt_wan=3000.0, mv_yi=900.0)
    cold = _row("600101", bid_change=0.5, bid_amt_wan=50000.0, mv_yi=30.0)
    assert cold.bid_amt > hot.bid_amt, "前置条件: cold 竞价额更大(旧键①会把它排前面)"

    assert pfilter.coarse_filter([hot, cold], F, CTX()) == ["600102", "600101"], \
        "必须按定格竞价涨幅降序"

    # 反证: 旧键「竞价额降序」的顺序恰好相反 —— 锁住"本用例真的对改键敏感"
    old_key_order = [c for c, _ in sorted(
        [("600101", cold.bid_amt), ("600102", hot.bid_amt)], key=lambda x: -x[1])]
    assert old_key_order == ["600101", "600102"]


def test_coarse_rank_key_is_negated_bid_change():
    """键 = (-涨幅, code): 涨幅越高键越小(升序排出降序); 负涨幅照大小排, 不被夹到 0。"""
    assert coarse_rank_key(6.5, "600002") < coarse_rank_key(0.5, "600001")
    assert coarse_rank_key(0.5, "600001") < coarse_rank_key(0.5, "600002"), "同涨幅 code 小者在前"
    assert coarse_rank_key(0.0, "600003") < coarse_rank_key(-5.0, "600001"), "平开优于低开"
    # 缺失 → 垫底, 且**不冒充 0.0**(0.0 = 平开 是有效涨幅, 与"没有数据"不是一回事)
    assert coarse_rank_key(None, "600001") == (COARSE_RANK_MISSING, "600001")
    assert coarse_rank_key(None, "000001") > coarse_rank_key(-9.9, "999999")


# ==================== 2. 并列确定性 ====================
def test_coarse_rank_ties_break_by_code_and_ignore_input_order():
    """同涨幅按 code 升序; 且与输入顺序无关。"""
    rows = [_row("600302", bid_change=3.0),
            _row("600301", bid_change=3.0),
            _row("600303", bid_change=3.0)]
    assert pfilter.coarse_filter(rows, F, CTX()) == ["600301", "600302", "600303"]
    assert pfilter.coarse_filter(list(reversed(rows)), F, CTX()) == \
        ["600301", "600302", "600303"], "换输入顺序结果必须一致"


# ==================== 3. 名额截断保留涨幅最高的 ====================
def test_coarse_rank_limit_keeps_top_change_not_top_amount():
    """名额截断保留的是**涨幅**最高的, 不是竞价额最大的(名额仍为 limit/COARSE_MAX)。"""
    rows = [_row("6004%02d" % i, bid_change=1.0, bid_amt_wan=9000.0 + i)
            for i in range(6)]
    rows += [_row("600401", bid_change=6.9, bid_amt_wan=3000.0)]
    assert pfilter.coarse_filter(rows, F, CTX(), limit=1) == ["600401"], \
        "涨幅最高的票竞价额最小, 仍必须被保留"


def test_coarse_max_unchanged_at_200():
    """名额上限未随改键变动 —— 三处必须同值(本处只能验后端两处, 前端由
    pickFromSnapshot.test.js 的同名断言兜住)。"""
    assert pfilter.COARSE_MAX == 200
    assert _SNAP_CANDIDATE_MAX == 200


# ==================== 4. 缺涨幅排最后(且只在兼容开关下可达) ====================
def test_missing_bid_change_sorts_last_when_required_disabled():
    """require_bid_change=False(竞价早期数据未全的兼容开关)时, 缺涨幅的票不剔除,
    但必须排在所有有效涨幅之后。"""
    rows = [_row("600501", bid_change=None),
            _row("600502", bid_change=1.0),
            _row("600503", bid_change=2.0)]
    assert pfilter.coarse_filter(rows, F, CTX(require_bid_change=False)) == \
        ["600503", "600502", "600501"], "缺涨幅必须垫底(不得冒充平开挤进 0 档)"

    # 默认 require_bid_change=True ⇒ 缺涨幅在**门槛阶段**就被剔除, 根本进不到排序
    assert pfilter.coarse_filter(rows, F, CTX()) == ["600503", "600502"]


# ==================== 5. 两条后端链路同尺子 ====================
def test_snapshot_chain_uses_same_rank_key_as_picker():
    """_snapshot_candidate_codes(盘后快照链路) 与 picker.filter.coarse_filter
    (唯一主链路) 必须给出**同一顺序**。任一单独改键即红。"""
    spec = [
        # code,   bid_change, bid_amt(万), mv(亿), auc_turnover
        ("600201", 4.0, 50000.0, 55.0, 0.05),
        ("600202", 1.0, 4000.0, 55.0, 2.0),
        ("600203", 6.0, 6000.0, 40.0, 0.6),
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

    a = pfilter.coarse_filter(quote_rows, F, CTX())
    b = _snapshot_candidate_codes(snap_rows, F, set())
    assert a == b, "两条链路排队键不一致: picker=%r snapshot=%r" % (a, b)
    assert a == ["600203", "600201", "600204", "600202"], "必须按定格涨幅降序"
    # 顺带锁住"不是竞价额降序"(否则本用例对改键无感)
    assert a != ["600201", "600204", "600203", "600202"]


def test_snapshot_chain_survives_absent_mv_and_turnover():
    """快照行缺市值/换手时不抛异常, 且仍按涨幅排序(改键后排队不再依赖市值与换手)。"""
    snap = {"600301": {"name": "甲", "bid_change": 1.0, "bid_amt": 5000.0},
            "600302": {"name": "乙", "bid_change": 3.0, "bid_amt": 9000.0}}
    assert _snapshot_candidate_codes(snap, F, set()) == ["600302", "600301"]


# ==================== 6. 改键不动门槛 ====================
def test_rank_key_does_not_change_eligibility():
    """涨幅最高但市值超上限的票照样被剔除 —— 排队键只改顺序, 不改入选资格。"""
    over = _row("600601", bid_change=7.0, mv_yi=5000.0)     # > floatMvGt(1000 亿)
    ok = _row("600602", bid_change=1.0, mv_yi=55.0)
    assert pfilter.coarse_filter([over, ok], F, CTX()) == ["600602"]
