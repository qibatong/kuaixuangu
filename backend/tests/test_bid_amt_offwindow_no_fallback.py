# -*- coding: utf-8 -*-
"""窗口外(盘中/收盘)竞价额只认 9:25 定格快照, 不得回退 f616(2026-09-07)

主人反馈: 竞价额门槛 3000→2500 万后"选出 56 只", 且**同一参数两次调用返回 19/56 只**
(批次 #8226/#8227), #8225 甚至 122 只 —— 结果随行情源抖动。

根因: 窗口外竞价额原逻辑 `day_bid_amt.get(code) or get_bid_amt(s, False)`,
      快照缺失时回退读行情 f616; 而**腾讯兜底行 f616 被近似为全天累计成交额**
      (fetcher 腾讯映射 f616 = 成交额×1e4), 收盘后动辄数亿 → 竞价额门槛形同虚设。
修复: 窗口外 bid_amt = 定格快照值, 缺失即 0(不满足"≥门槛"被过滤, 不用成交额冒充)。
"""
from app.services import scorer


def _score_one(f616, day_bid_amt, auction_ok=False):
    """用 score_all_stocks 跑单只, 返回 bidAmt(万元)"""
    raw = [{"f12": "600000", "f14": "测试股", "f2": 10.0, "f3": 5.0, "f4": 9.5,
            "f5": 1000, "f6": 5.0e8,          # 累计成交额 5 亿(元)
            "f8": 3.0, "f21": 5e10, "f615": 5.0, "f616": f616,
            "f617": 1e6, "f630": 0}]
    out = scorer.score_all_stocks(raw, {}, {}, qiangchou_codes=None,
                                  day_bid_amt=day_bid_amt)
    return (out[0].get("bidAmt") if out else None)


def test_offwindow_uses_snapshot_only():
    """窗口外: 定格快照有值 → 用快照(即使 f616 是巨大的成交额)"""
    amt = _score_one(f616=5.0e8, day_bid_amt={"600000": 3200.0})   # 快照 3200 万
    assert abs((amt or 0) - 3200.0) < 1, f"应取定格 3200 万, 实际 {amt}"


def test_offwindow_missing_snapshot_is_zero_not_amount(monkeypatch):
    """窗口外: 快照缺失 → 0(不回退 f616/成交额; 否则门槛失效)"""
    # 模拟腾讯兜底: f616 = 全天成交额 5 亿(元)
    amt = _score_one(f616=5.0e8, day_bid_amt={})
    assert abs((amt or 0) - 0.0) < 1e-6, f"快照缺失应返回 0, 实际 {amt}(若为 50000 万=成交额则门槛失效)"


def test_in_window_uses_live_f616(monkeypatch):
    """竞价窗口内(且 9:30 前)仍用行情 f616(新鲜可信), 不被本修复影响"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    monkeypatch.setattr(scorer, "_bj_hm", lambda: 9 * 60 + 25)   # 9:25
    amt = _score_one(f616=8.0e7, day_bid_amt={}, auction_ok=True)   # 8000 万
    assert amt is not None and abs(amt - 8000.0) < 1, f"窗口内应取 f616=8000 万, 实际 {amt}"


class TestIntraday930Boundary:
    """9:30-15:00 连续竞价时段: 竞价额一律用 9:25 定格, 不得用实时成交额"""

    def _amt(self, monkeypatch, hm, f616, snap):
        from app.services import scorer
        monkeypatch.setattr(scorer, "in_auction_window", lambda: True)   # 窗口内
        monkeypatch.setattr(scorer, "_bj_hm", lambda: hm)
        raw = [{"f12": "600000", "f14": "测试股", "f2": 10.0, "f3": 5.0, "f4": 9.5,
                "f5": 1000, "f6": 5.0e8, "f8": 3.0, "f21": 5e10, "f615": 5.0,
                "f616": f616, "f617": 1e6, "f630": 0}]
        out = scorer.score_all_stocks(raw, {}, {}, qiangchou_codes=None,
                                      day_bid_amt={"600000": snap} if snap else {})
        return (out[0].get("bidAmt") if out else None)

    def test_before_930_uses_live(self, monkeypatch):
        """9:25(竞价中) → 用实时 f616"""
        amt = self._amt(monkeypatch, 9 * 60 + 25, f616=8.0e7, snap=None)
        assert abs((amt or 0) - 8000.0) < 1, f"9:25 应取 f616=8000 万, 实际 {amt}"

    def test_at_930_uses_snapshot(self, monkeypatch):
        """9:30(已开盘) → 用 9:25 定格, 不用实时成交额"""
        amt = self._amt(monkeypatch, 9 * 60 + 30, f616=5.0e8, snap=3200.0)
        assert abs((amt or 0) - 3200.0) < 1, f"9:30 应取定格 3200 万, 实际 {amt}"

    def test_midday_uses_snapshot(self, monkeypatch):
        """14:00(盘中) → 用 9:25 定格"""
        amt = self._amt(monkeypatch, 14 * 60, f616=9.0e8, snap=4500.0)
        assert abs((amt or 0) - 4500.0) < 1, f"14:00 应取定格 4500 万, 实际 {amt}"

    def test_midday_missing_snapshot_zero(self, monkeypatch):
        """盘中 + 快照缺失 → 0(不用成交额冒充)"""
        amt = self._amt(monkeypatch, 11 * 60, f616=9.0e8, snap=None)
        assert abs((amt or 0) - 0.0) < 1e-6, f"盘中快照缺失应为 0, 实际 {amt}"
