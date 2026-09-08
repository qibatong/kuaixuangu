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


# ---------------- P5 补强: 重启补跑 / 收盘刷新 / 失败重试(2026-09-08) ----------------
def test_tick_fires_intraday_prewarm_once(monkeypatch):
    """9:06 盘中窗口触发一次, 同日不重复"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 3, 9 * 60 + 6, "2026-09-03"))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda: fired.append(1) or True)
    assert yday_prewarm._scheduler_tick() is True
    assert yday_prewarm._scheduler_tick() is False      # 同日已成功, 不再触发
    assert fired == [1]


def test_tick_catchup_after_restart(monkeypatch):
    """错过 9:05 窗口(服务重启)→ 12:00 补跑一轮"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 3, 12 * 60, "2026-09-03"))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda: fired.append(1) or True)
    assert yday_prewarm._scheduler_tick() is True
    assert fired == [1]


def test_tick_close_refresh_after_1510(monkeypatch):
    """15:12 收盘刷新触发(收盘后 T 推进到今天)"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 3, 15 * 60 + 12, "2026-09-03"))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda: fired.append("close") or True)
    assert yday_prewarm._scheduler_tick() is True
    assert fired == ["close"]


def test_tick_failure_retries_next_round(monkeypatch):
    """预热失败(False)不记成功 → 30s 后下轮重试"""
    calls = []
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 3, 9 * 60 + 6, "2026-09-03"))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda: calls.append(1) or False)
    assert yday_prewarm._scheduler_tick() is True       # 尝试了
    assert yday_prewarm._scheduler_tick() is True       # 失败 → 再试
    assert len(calls) == 2


def test_tick_no_catchup_after_1455(monkeypatch):
    """过了 14:55 不再补跑盘中预热(留给用户请求异步补齐, 避免盘尾抢资源)"""
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 3, 15 * 60, "2026-09-03"))   # 15:00
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda: (_ for _ in ()).throw(AssertionError("15:00 不应再补盘中预热")))
    assert yday_prewarm._scheduler_tick() is False


def test_tick_weekend_skips_new(monkeypatch):
    """周六(9/5, wday=5)三态全不触发"""
    monkeypatch.setattr(yday_prewarm, "_bj",
                        lambda: (None, 5, 9 * 60 + 5, "2026-09-05"))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda: (_ for _ in ()).throw(AssertionError("周末不应触发")))
    assert yday_prewarm._scheduler_tick() is False
