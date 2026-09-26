"""快照候选池粗筛单测(2026-09-07): _snapshot_candidate_codes 纯函数"""
import os
import sys

os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.stocks import _snapshot_candidate_codes, _SNAP_CANDIDATE_MAX
from app.services import scorer


def _row(code, name, bid_change, bid_amt, free_mv_yi, board=""):
    """构造一条快照行(市值入参单位: 亿, 内部转元存储同 snapshot_bid)"""
    return {code: {"name": name, "bid_change": bid_change, "bid_amt": bid_amt,
                   "float_mv": free_mv_yi * 1e8, "free_mv": free_mv_yi * 1e8, "board": board}}


def _mk_rows(items):
    out = {}
    for it in items:
        out.update(_row(*it))
    return out


def _default_f(overrides=None):
    """默认筛选(与前端 defaultFilterSettings 对齐)"""
    f = scorer.validate_filters({
        "stSuspend": ["0"], "limitUp": ["0"], "markets": ["hs,cyb,kcb"],
        "bidGt": ["7"], "floatMvFloor": ["30"], "floatMvGt": ["1000"],
        "priceGt": ["300"], "bidAmtFloor": ["3000"],
    })
    if overrides:
        f.update(overrides)
    return f


def test_basic_default_filter():
    rows = _mk_rows([
        # code, name, bid_change, bid_amt(万), free_mv(亿)
        ("600001", "合格票A", 3.0, 5000, 50),      # 全符合
        ("600002", "涨幅过高", 8.0, 5000, 50),     # bid_change > 7 剔除
        ("600003", "金额不足", 3.0, 2000, 50),     # bid_amt < 3000 剔除
        ("600004", "市值过小", 3.0, 5000, 20),     # free_mv < 30亿 剔除
        ("600005", "市值过大", 3.0, 5000, 2000),   # free_mv > 1000亿 剔除
        ("300001", "创业板OK", 5.0, 4000, 80),     # cyb 符合
        ("688001", "科创板OK", 4.0, 3500, 60),     # kcb 符合
        ("830001", "北交所不在hs/cyb/kcb", 3.0, 5000, 50),  # 板块剔除
        ("600010", "*ST风险", 3.0, 5000, 50),      # ST 剔除
        ("600011", "昨涨停股", 3.0, 5000, 50),     # limitUp=False 剔昨涨停(yzt)
    ])
    yzt = {"600011"}
    codes = _snapshot_candidate_codes(rows, _default_f(), yzt)
    assert "600001" in codes
    assert "600002" not in codes
    assert "600003" not in codes
    assert "600004" not in codes
    assert "600005" not in codes
    assert "300001" in codes
    assert "688001" in codes
    assert "830001" not in codes
    assert "600010" not in codes
    assert "600011" not in codes
    print("codes:", codes)


def test_limit_up_includes_yzt():
    rows = _mk_rows([
        ("600011", "昨涨停股", 3.0, 5000, 50),
    ])
    # limitUp=True → 昨涨停股保留
    codes = _snapshot_candidate_codes(rows, _default_f({"limitUp": True}), {"600011"})
    assert "600011" in codes


def test_sort_by_frozen_bid_change_desc():
    """排队键 = **定格竞价涨幅降序**(2026-09-26 改键); 竞价额/市值/换手**不再参与排队**。

    旧版本叫 `test_sort_by_bid_amt_desc`: 三只票涨幅相同、竞价额不同, 靠"竞价额越大 →
    竞价换手越高 → 三因子粗排分越高"**碰巧**排出一致的顺序(名字对、机理不对)。改键后
    涨幅相同 ⇒ 退化为按 code 升序, 竞价额彻底退出排序, 故本用例重写为直接锁新契约。
    """
    rows = _mk_rows([
        ("600001", "低额低涨幅", 1.0, 3000, 50),
        ("600002", "高额低涨幅", 1.0, 9000, 50),   # 竞价额最大, 但涨幅不高 → 不得靠前
        ("600003", "中额高涨幅", 6.0, 6000, 50),
    ])
    codes = _snapshot_candidate_codes(rows, _default_f(), set())
    assert codes == ["600003", "600001", "600002"], \
        "必须按定格涨幅降序; 同涨幅按 code 升序(与竞价额无关)"


def test_candidate_max_cap():
    rows = _mk_rows([("60%04d" % i, "股%d" % i, 3.0, 5000, 50) for i in range(1, 200)])
    codes = _snapshot_candidate_codes(rows, _default_f(), set())
    assert len(codes) <= _SNAP_CANDIDATE_MAX


def test_st_suspend_true_keeps_st():
    rows = _mk_rows([
        ("600010", "ST股", 3.0, 5000, 50),
    ])
    # stSuspend=True → 保留 ST
    codes = _snapshot_candidate_codes(rows, _default_f({"stSuspend": True}), set())
    assert "600010" in codes


if __name__ == "__main__":
    test_basic_default_filter()
    test_limit_up_includes_yzt()
    test_sort_by_frozen_bid_change_desc()
    test_candidate_max_cap()
    test_st_suspend_true_keeps_st()
    print("all passed")
