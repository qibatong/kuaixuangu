# -*- coding: utf-8 -*-
"""按股概念**后台补齐**（2026-10-01 竞价链路 P1-5 / 清单 1.3）

量化背景（测试机实测，`scripts/_kx_p15_probe.py`）：
  · 封单 tab 冷启动出网 **66 次**，其中 **60 次是按股 `GetStockIDPlate`**；
    爆量 tab **0 次**（纯库内算量比）、净额 tab **1 次** ⇒ 三 tab **无同源榜单可共用**。
  · 真缺口：`apply_board_concept` 的 3s 预算（2026-09-10 生产 504 止血线）用尽后
    `剩余84只本轮放弃` ⇒ 概念残缺，且下一轮轮询**再阻塞 3s** 补下一批。

本文件钉住三件事（都不出网：`fetch_stock_plate` 一律被替身掉）：
  ① 入队**去重**（同一 code 不重复排队）；② 队列**有上限**（不积压）；
  ③ 预算用尽时**把剩余 code 交给后台**（而不是丢掉）；④ `deep=False` 不走这条（历史回看不掺和）。
"""
import time

import pytest

from app.services import kpl
from app.services.cache_store import store


@pytest.fixture(autouse=True)
def _no_net_and_fast(monkeypatch):
    """替身: 按股查询零耗时零出网; 后台间隔 0(测试要快); 队列上限降到 3; 清共享池"""
    calls = []

    def fake_plate(code, use_cache=True):
        calls.append(str(code))
        return "概念A、概念B"

    monkeypatch.setattr(kpl, "fetch_stock_plate", fake_plate)
    monkeypatch.setattr(kpl, "_KPL_PLATE_WARM_GAP", 0)
    monkeypatch.setattr(kpl, "_KPL_PLATE_WARM_MAX", 3)
    # 🔴 setup 也要复位: 队列/去重集是**模块级**的, 全量跑时别的用例(竞价异动类)
    #   可能已经把 code 排进去 ⇒ 不复位会偶发"去重断言失败"(实测踩到)。线程已单次启动,
    #   故这里换队列不会再多起线程。
    with kpl._KPL_PLATE_WARM_LOCK:
        kpl._KPL_PLATE_WARM_QUEUED.clear()
    kpl._KPL_PLATE_WARM_Q = None
    try:
        store.delete("kpl:concept_deep")
    except Exception:                                            # noqa: BLE001
        pass
    yield calls
    # 收尾: 把模块态复位(避免影响其它用例)
    # 🔴 用**有界**等待而不是 q.join(): 若消费者线程因故没起来, join() 会永久阻塞,
    #   实测能把整轮 pytest 挂死(所以这里只轮询 unfinished_tasks, 最多 2s)。
    q = kpl._KPL_PLATE_WARM_Q
    if q is not None:
        _deadline = time.time() + 2.0
        while getattr(q, "unfinished_tasks", 0) > 0 and time.time() < _deadline:
            time.sleep(0.01)
    with kpl._KPL_PLATE_WARM_LOCK:
        kpl._KPL_PLATE_WARM_QUEUED.clear()
    kpl._KPL_PLATE_WARM_Q = None


def test_warm_enqueue_dedupes():
    """同一 code 不重复入队（页面每 30s 轮询会重复送来同一批 code）"""
    assert kpl.warm_stock_plates_async(["600000", "002852"]) == 2
    assert kpl.warm_stock_plates_async(["600000", "002852"]) == 0, "重复入队未被去重"


def test_warm_queue_has_cap():
    """队列上限: 超出部分丢弃（下轮请求会重新入队）—— 防"无上限积压"把上游打爆"""
    n = kpl.warm_stock_plates_async(["c%d" % i for i in range(50)])
    assert n == kpl._KPL_PLATE_WARM_MAX, "应在上限处截断, 实际入队 %d" % n


def test_warm_ignores_empty():
    assert kpl.warm_stock_plates_async([]) == 0
    assert kpl.warm_stock_plates_async(None) == 0


def test_budget_exhaustion_hands_remainder_to_background(monkeypatch):
    """🔴 核心: 3s 预算用尽 ⇒ 剩余 code **转后台**, 不是丢掉

    这是"概念残缺要好幾轮才补齐"的修法 —— 本请求耗时上限不变, 但下一轮轮询就命中。
    """
    handed = {}

    def spy(codes):
        handed["codes"] = list(codes)
        return len(codes)

    monkeypatch.setattr(kpl, "warm_stock_plates_async", spy)
    monkeypatch.setattr(kpl, "fetch_board_map", lambda: {})       # 第一层: 不出网
    monkeypatch.setattr(kpl, "fetch_stock_plate",
                        lambda code, use_cache=True: "概念A")

    # 25 只 > BATCH(20) ⇒ 循环至少两轮: 第一轮跑完, 第二轮开头即超预算(预算取极小值
    # 保证"已耗时 > 预算"必然成立 —— 用 0.0001s 在这里会因首轮只耗时几微秒而**不触发**)
    result = [{"code": "6%05d" % i} for i in range(25)]
    kpl.apply_board_concept(result, log_tag="test", deep=True, field="concept",
                            time_budget=1e-9)
    assert handed.get("codes"), "预算用尽后剩余 code 未转后台(仍会『本轮放弃』)"
    assert len(handed["codes"]) >= 1


def test_deep_false_does_not_touch_background(monkeypatch):
    """历史回看/大列表走 deep=False（只做榜单层）⇒ 不该触发后台按股补齐"""
    handed = {"n": 0}
    monkeypatch.setattr(kpl, "warm_stock_plates_async",
                        lambda codes: handed.__setitem__("n", handed["n"] + 1) or 0)
    monkeypatch.setattr(kpl, "fetch_board_map", lambda: {})
    result = [{"code": "600000"}]
    kpl.apply_board_concept(result, log_tag="test", deep=False, field="board")
    assert handed["n"] == 0, "deep=False 不应触发按股后台补齐"
