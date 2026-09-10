# -*- coding: utf-8 -*-
"""概念深查耗时预算 time_budget(2026-09-10 生产 504 止血)

背景: 竞价异动抢筹右表 listLast 固定 100 只, 开盘啦共享池命中率低(实测 10/100)
→ 90 只逐股外网查询, 并发受 _SEM=3 限流 → 单次 183~198 秒 → nginx 60s 超时 504,
且两个 uvicorn worker 被占满, 连累 /api/stocks 一起 504。
概念只是"展示字段", 不值得让整个接口赌上 3 分钟 → 加总耗时预算, 超预算即停。
"""
import time

from app.services import kpl


class _FakeStore(object):
    def __init__(self):
        self.data = {}

    def get(self, k, default=None):
        return self.data.get(k, default)

    def set(self, k, v, ttl=0):
        self.data[k] = v


def _rows(n):
    return [{"code": "%06d" % (600000 + i), "concept": "原值"} for i in range(n)]


def test_time_budget_stops_deep_query(monkeypatch):
    """按股查询超预算即停: 未查到的保留原值, 不拖垮整个接口"""
    monkeypatch.setattr(kpl, "store", _FakeStore())
    monkeypatch.setattr(kpl, "fetch_board_map", lambda: {})      # 第一层不覆盖

    def slow_plate(code):
        time.sleep(0.05)          # 模拟单只外网查询 50ms
        return "测试概念"

    monkeypatch.setattr(kpl, "fetch_stock_plate", slow_plate)

    rows = _rows(60)
    t0 = time.time()
    n = kpl.apply_board_concept(rows, log_tag="test", deep=True, time_budget=0.2)
    cost = time.time() - t0

    assert 0 < n < 60, "预算耗尽应只补齐部分, 实际覆盖 %d/60" % n
    assert cost < 1.5, "耗时预算未生效, 单次 %.2fs 会拖垮接口" % cost
    # 超出预算未查到的 → 保留原值(下轮共享池命中后自动补齐)
    assert any(r["concept"] == "原值" for r in rows)
    assert any(r["concept"] == "测试概念" for r in rows)


def test_time_budget_zero_means_unlimited(monkeypatch):
    """time_budget=0 → 不设预算(离线/回补任务用), 全部查完"""
    monkeypatch.setattr(kpl, "store", _FakeStore())
    monkeypatch.setattr(kpl, "fetch_board_map", lambda: {})
    monkeypatch.setattr(kpl, "fetch_stock_plate", lambda code: "测试概念")

    rows = _rows(60)
    n = kpl.apply_board_concept(rows, log_tag="test", deep=True, time_budget=0)
    assert n == 60
    assert all(r["concept"] == "测试概念" for r in rows)
