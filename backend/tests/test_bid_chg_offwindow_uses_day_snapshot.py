# -*- coding: utf-8 -*-
"""窗口外(盘中/收盘)竞涨只认 9:25 定格快照 bid_change, 不得退 f3 现价涨幅(2026-09-08)

生产事故: fd27ebe 盘后 filter 走快照候选+东财 ulist 点查, 东财行情 f615(竞价涨幅)
**收盘后返回 "-"** → float("-") 抛异常 → scorer.get_bid_change 退 f3(现价/收盘涨幅)
→ ①竞涨列=现涨列 ②「涨幅≤7%(bidGt)」过滤按现价判 → 当日大跌票(f3≤7 恒成立)混入名单。
修复: 与 bidAmt 定格(load_day_bid_amt)同款, 窗口外以当日 9:25 定格快照 bid_change
覆写行 f615, 评分/过滤/展示全链路同源。
"""
from app.services import scorer


def _row(f615=None, f3=-6.2):
    """构造单只行情行; f615=None 模拟缺失, f615="-" 模拟东财收盘后形态"""
    r = {"f12": "600000", "f14": "测试股", "f2": 9.38, "f3": f3, "f4": 10.0,
         "f5": 1000, "f6": 5.0e8, "f8": 3.0, "f21": 5e10, "f616": 5.0e8,
         "f617": 1e6, "f630": 0}
    if f615 is not None:
        r["f615"] = f615
    return r


def _score_one(monkeypatch, f615, day_bid_change, in_window=False, hm=14 * 60):
    monkeypatch.setattr(scorer, "in_auction_window", lambda: in_window)
    if in_window:
        monkeypatch.setattr(scorer, "_bj_hm", lambda: hm)
    out = scorer.score_all_stocks([_row(f615=f615)], {}, {}, qiangchou_codes=None,
                                  day_bid_amt={}, day_bid_change=day_bid_change)
    it = out[0]
    return it.get("bidChange"), it.get("realChange")


def test_offwindow_uses_day_snapshot_when_f615_missing(monkeypatch):
    """窗口外 + 行情无 f615 → 竞涨取 9:25 定格(3.5), 不得退 f3(现价 -6.2)"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    bid_chg, real_chg = _score_one(monkeypatch, f615=None,
                                   day_bid_change={"600000": 3.5})
    assert bid_chg == 3.5, f"竞涨应取定格 3.5, 实际 {bid_chg}(退 f3 事故)"
    assert real_chg == -6.2, "现涨保持行情 f3"
    assert bid_chg != real_chg, "竞涨≠现涨"


def test_offwindow_uses_day_snapshot_when_f615_dash(monkeypatch):
    """窗口外 + 东财 f615='-'(收盘后真实形态, float 抛异常) → 竞涨取定格"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    bid_chg, real_chg = _score_one(monkeypatch, f615="-",
                                   day_bid_change={"600000": 3.5})
    assert bid_chg == 3.5, f"f615='-' 时竞涨应取定格 3.5, 实际 {bid_chg}"
    assert real_chg == -6.2


def test_offwindow_no_snapshot_falls_back_to_quote(monkeypatch):
    """窗口外 + 快照缺该 code(新股/北交) → 保留行情值, 不误杀"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    bid_chg, _ = _score_one(monkeypatch, f615=5.0, day_bid_change={})
    assert bid_chg == 5.0, f"快照缺失应回退行情 f615=5.0, 实际 {bid_chg}"
    # f615 缺失且无定格 → 退 f3(极端兜底, 不在此修复范围)
    bid_chg2, _ = _score_one(monkeypatch, f615=None, day_bid_change={})
    assert bid_chg2 == -6.2


def test_in_window_uses_live_f615(monkeypatch):
    """竞价窗口内(9:25) → 用行情 f615, 不被定格覆盖"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    monkeypatch.setattr(scorer, "_bj_hm", lambda: 9 * 60 + 25)
    bid_chg, _ = _score_one(monkeypatch, f615=5.0, day_bid_change={"600000": 3.5},
                            in_window=True, hm=9 * 60 + 25)
    assert bid_chg == 5.0, f"窗口内应取行情 f615=5.0, 实际 {bid_chg}"


def test_apply_filters_drops_gt_bidgt_after_freeze(monkeypatch):
    """端到端过滤: 盘后大跌票(f3=-3)若竞价涨幅 8.5 > bidGt(7) → 定格后必须被剔除;
    修复前按 f3=-3 ≤7 误放行(「涨幅≤7%」形同虚设的根)"""
    from app.services.scorer import validate_filters, process_all_stocks
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    f = validate_filters({"stSuspend": ["0"], "limitUp": ["0"],
                          "markets": ["hs,cyb,kcb"], "bidGt": ["7"],
                          "floatMvFloor": ["30"], "floatMvGt": ["1000"],
                          "priceGt": ["300"], "bidAmtFloor": ["3000"],
                          "probLt": ["5"], "confLt": ["50"]})
    # 行: 竞价定格 8.5%(超限), 现价 -3%(大跌) — 修复前 f3=-3≤7 会通过
    raw = [_row(f615=None, f3=-3.0)]
    out_gt = process_all_stocks(raw, f, {}, {}, qiangchou_codes=None,
                                day_bid_amt={"600000": 5000.0},
                                day_bid_change={"600000": 8.5})
    assert out_gt == [], f"竞价涨幅 8.5>7 应被剔除(定格生效), 实际 {len(out_gt)} 只"
    # 对照组: 竞价定格 3.0%(合规) + 现价 -3% → 保留(符合竞价条件)
    out_ok = process_all_stocks([_row(f615=None, f3=-3.0)], f, {}, {},
                                qiangchou_codes=None,
                                day_bid_amt={"600000": 5000.0},
                                day_bid_change={"600000": 3.0})
    assert len(out_ok) == 1, "竞价涨幅 3.0≤7 应保留"


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
