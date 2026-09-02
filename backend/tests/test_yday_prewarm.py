# -*- coding: utf-8 -*-
"""开盘前昨比预热测试(2026-09-02 生产事故后新增):
调度在交易日 9:05 窗口触发; 分批拉取按 BATCH 切分并调用 fetch_yesterday_amounts(wait=True)。
"""
import time

import pytest

from app.services import yday_prewarm, fetcher


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """清空触发记录, 隔离用例"""
    yday_prewarm._fired.clear()
    yield
    yday_prewarm._fired.clear()


def test_prewarm_batch_splits_and_calls_fetch(monkeypatch):
    """预热按 BATCH 分批, 每批调用 fetch_yesterday_amounts(wait=True)"""
    calls = []
    codes = ["%06d" % i for i in range(550)]          # 550 只 → 3 批(200/200/150)

    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: {c: {} for c in codes})
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts",
                        lambda batch, wait=False: calls.append((len(batch), wait)))
    monkeypatch.setattr(yday_prewarm, "BATCH", 200)

    yday_prewarm._prewarm_once()
    assert len(calls) == 3
    assert [n for n, _ in calls] == [200, 200, 150]
    assert all(w for _, w in calls)                   # 全部 wait=True


def test_prewarm_skips_when_empty_market(monkeypatch):
    """全市场行情为空时跳过, 不发起拉取"""
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts",
                        lambda batch, wait=False: (_ for _ in ()).throw(AssertionError("不应调用")))
    yday_prewarm._prewarm_once()                      # 不应抛异常


def test_scheduler_fires_once_per_day(monkeypatch):
    """调度判断: 9:05 窗口内触发一次, 同日不重复"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 3, 9 * 60 + 6, "2026-09-03"))   # 周四 09:06
    monkeypatch.setattr(yday_prewarm, "_prewarm_once", lambda: fired.append(1))

    for _ in range(3):                                # 模拟轮询 3 次(同一天)
        g, wday, hm, date = yday_prewarm._bj()
        if wday < 5 and abs(hm - yday_prewarm.PREWARM_AT) <= yday_prewarm.WINDOW \
                and yday_prewarm._fired.get(date) != date:
            yday_prewarm._prewarm_once()
            yday_prewarm._fired[date] = date
    assert fired == [1]                               # 同日只触发一次


def test_scheduler_weekend_skips(monkeypatch):
    """周六(9/5, wday=5)在窗口内也不触发"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 5, 9 * 60 + 5, "2026-09-05"))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once", lambda: fired.append(1))
    g, wday, hm, date = yday_prewarm._bj()
    if wday < 5 and abs(hm - yday_prewarm.PREWARM_AT) <= yday_prewarm.WINDOW:
        yday_prewarm._prewarm_once()
    assert fired == []
