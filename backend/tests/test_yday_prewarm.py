# -*- coding: utf-8 -*-
"""开盘前昨比预热测试(2026-09-02 生产事故后新增):
调度在交易日 9:05 窗口触发; 分批拉取按 BATCH 切分并调用 fetch_yesterday_amounts(wait=True)。
"""
import datetime
import time

import pytest

from app.services import yday_prewarm, fetcher


def _bj_at(y, m, d, hm):
    """构造与 `_bj()` 同形的返回值 `(g, wday, hm, "YYYY-MM-DD")`, 其中 `g` 是**真实
    `struct_time`**。

    2026-09-26: 调度门禁改走 `core/trade_calendar` 后, `g` 必须携带日期才能查出
    "是否法定休市日"。本文件原先把 `g` mock 成 `None` —— 那样 `is_trade_day_of(None)`
    会 **fail-open**(日历查不到日期时保守放行)成"是交易日", 节假日用例就失去意义。
    """
    dt = datetime.date(y, m, d)
    g = time.struct_time(
        (y, m, d, hm // 60, hm % 60, 0, dt.weekday(), dt.timetuple().tm_yday, 0))
    return g, g.tm_wday, hm, "%04d-%02d-%02d" % (y, m, d)


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
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 9 * 60 + 6))   # 周四 09:06
    monkeypatch.setattr(yday_prewarm, "_prewarm_once", lambda stage="open": fired.append(1))

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
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 5, 9 * 60 + 5))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once", lambda stage="open": fired.append(1))
    g, wday, hm, date = yday_prewarm._bj()
    if wday < 5 and abs(hm - yday_prewarm.PREWARM_AT) <= yday_prewarm.WINDOW:
        yday_prewarm._prewarm_once()
    assert fired == []


# ---------------- P5 补强: 重启补跑 / 收盘刷新 / 失败重试(2026-09-08) ----------------
def test_tick_fires_intraday_prewarm_once(monkeypatch):
    """9:06 盘中窗口触发一次, 同日不重复"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 9 * 60 + 6))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": fired.append(1) or True)
    assert yday_prewarm._scheduler_tick() is True
    assert yday_prewarm._scheduler_tick() is False      # 同日已成功, 不再触发
    assert fired == [1]


def test_tick_catchup_after_restart(monkeypatch):
    """错过 9:05 窗口(服务重启)→ 12:00 补跑一轮"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 12 * 60))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": fired.append(1) or True)
    assert yday_prewarm._scheduler_tick() is True
    assert fired == [1]


def test_tick_close_refresh_after_1510(monkeypatch):
    """15:12 收盘刷新触发(收盘后 T 推进到今天)"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 15 * 60 + 12))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": fired.append("close") or True)
    assert yday_prewarm._scheduler_tick() is True
    assert fired == ["close"]


def test_tick_failure_retries_next_round(monkeypatch):
    """预热失败(False)不记成功 → 30s 后下轮重试"""
    calls = []
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 9 * 60 + 6))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": calls.append(1) or False)
    assert yday_prewarm._scheduler_tick() is True       # 尝试了
    assert yday_prewarm._scheduler_tick() is True       # 失败 → 再试
    assert len(calls) == 2


def test_tick_no_catchup_after_1455(monkeypatch):
    """过了 14:55 不再补跑盘中预热(留给用户请求异步补齐, 避免盘尾抢资源)"""
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 15 * 60))   # 15:00
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": (_ for _ in ()).throw(AssertionError("15:00 不应再补盘中预热")))
    assert yday_prewarm._scheduler_tick() is False


def test_tick_weekend_skips_new(monkeypatch):
    """周六(9/5, wday=5)三态全不触发"""
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 5, 9 * 60 + 5))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": (_ for _ in ()).throw(AssertionError("周末不应触发")))
    assert yday_prewarm._scheduler_tick() is False


# ---------------- 2026-09-26: 门禁从"只判周末"改走交易日历 ----------------
def test_tick_holiday_skips_midautumn(monkeypatch):
    """★ 回归用例: 2026-09-25(中秋, **周五**)是法定休市日 → 窗口内也不得触发。

    09-25 事故: 门禁只判周末(wday=4 < 5) ⇒ 假日照跑预热, 冷缓存把"前一交易日"的
    昨比当成"昨日"灌进缓存, 与当天"幽灵名单"同源。此用例的日期**故意选周五**,
    这样"只判周末"的旧实现会照常触发 → 一旦有人回退成裸 `wday >= 5`, 本用例必红。
    """
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 25, 9 * 60 + 5))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": (_ for _ in ()).throw(
                            AssertionError("法定假日(中秋)不应触发预热")))
    assert yday_prewarm._scheduler_tick() is False


def test_tick_holiday_skips_national_day(monkeypatch):
    """2026-10-01(国庆, **周四**)是法定休市日 → 盘中预热与收盘刷新都不触发。

    国庆 10-01~10-07 是下一个法定假期, 本用例用于保证假期前该守卫已生效。
    """
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 10, 1, 15 * 60 + 12))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": (_ for _ in ()).throw(
                            AssertionError("法定假日(国庆)不应触发收盘刷新")))
    assert yday_prewarm._scheduler_tick() is False


def test_tick_normal_trading_day_still_fires(monkeypatch):
    """反向对照: 普通交易日(周四 2026-09-03)窗口内**必须**照常触发 —— 防止守卫改过头。"""
    fired = []
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: _bj_at(2026, 9, 3, 9 * 60 + 5))
    monkeypatch.setattr(yday_prewarm, "_prewarm_once",
                        lambda stage="open": fired.append(1) or True)
    assert yday_prewarm._scheduler_tick() is True
    assert fired == [1]
