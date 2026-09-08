# -*- coding: utf-8 -*-
"""模式层单测 (重构 P0) — 消灭散落时间硬编码后, 时段分派必须一次定义处处正确"""
from datetime import datetime, timedelta, timezone

from app.services.picker import mode as pm

BJ = timezone(timedelta(hours=8))


def ts(y, m, d, hh, mm):
    """构造北京时间对应时间戳(服务器时区无关)"""
    return datetime(y, m, d, hh, mm, tzinfo=BJ).timestamp()


# 2026-09-08 = 周二, 09-12 = 周六, 09-13 = 周日
def test_preopen_before_915():
    assert pm.resolve_mode(ts(2026, 9, 8, 8, 0)).mode is pm.PickMode.PREOPEN
    assert pm.resolve_mode(ts(2026, 9, 8, 0, 30)).mode is pm.PickMode.PREOPEN


def test_auction_window_915_to_925():
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 15)).mode is pm.PickMode.AUCTION
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 20)).mode is pm.PickMode.AUCTION
    # 边界: 9:14:59 仍属盘前
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 14)).mode is pm.PickMode.PREOPEN


def test_locked_925_to_930():
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 25)).mode is pm.PickMode.LOCKED
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 27)).mode is pm.PickMode.LOCKED
    # 边界: 9:24 仍属竞价窗口
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 24)).mode is pm.PickMode.AUCTION


def test_intraday_930_to_1500():
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 30)).mode is pm.PickMode.INTRADAY
    assert pm.resolve_mode(ts(2026, 9, 8, 14, 59)).mode is pm.PickMode.INTRADAY


def test_closed_after_1500():
    assert pm.resolve_mode(ts(2026, 9, 8, 15, 0)).mode is pm.PickMode.CLOSED
    assert pm.resolve_mode(ts(2026, 9, 8, 23, 59)).mode is pm.PickMode.CLOSED


def test_weekend_always_closed():
    """周末任何时点都是闭市回放(老逻辑同样跳过周末采集)"""
    assert pm.resolve_mode(ts(2026, 9, 12, 10, 0)).mode is pm.PickMode.CLOSED   # 周六
    assert pm.resolve_mode(ts(2026, 9, 13, 9, 20)).mode is pm.PickMode.CLOSED   # 周日


def test_holiday_hook_reserved():
    """节假日日历预留: 传入即生效(当前老代码无此能力, 属已知缺口补全)"""
    hol = {"2026-10-01"}
    assert pm.resolve_mode(ts(2026, 10, 1, 10, 0)).mode is pm.PickMode.INTRADAY
    assert pm.resolve_mode(ts(2026, 10, 1, 10, 0), holidays=hol).mode is pm.PickMode.CLOSED


def test_policy_semantics():
    """模式语义: 幂等 / 可锁 / 竞价窗口 — 决定取数与落库行为"""
    assert pm.POLICIES[pm.PickMode.PREOPEN].deterministic is True
    assert pm.POLICIES[pm.PickMode.LOCKED].deterministic is True
    assert pm.POLICIES[pm.PickMode.LOCKED].allow_lock is True      # 仅锁定期可落库
    assert pm.POLICIES[pm.PickMode.PREOPEN].allow_lock is False
    assert pm.POLICIES[pm.PickMode.INTRADAY].deterministic is False
    # 只有竞价窗口允许读实时 f615/f616
    assert pm.POLICIES[pm.PickMode.AUCTION].auction_window is True
    assert pm.POLICIES[pm.PickMode.INTRADAY].auction_window is False
    assert pm.POLICIES[pm.PickMode.LOCKED].auction_window is False


def test_every_mode_has_fail_message():
    """铁律2: 每个模式都必须有失败明示文案, 不允许静默降级"""
    for m, p in pm.POLICIES.items():
        assert p.fail_message, "模式 %s 缺少失败提示文案" % m.value
        assert p.source_priority, "模式 %s 缺少数据源优先级" % m.value


def test_trading_day_and_date():
    assert pm.is_trading_day(ts(2026, 9, 8, 10, 0)) is True     # 周二
    assert pm.is_trading_day(ts(2026, 9, 12, 10, 0)) is False   # 周六
    assert pm.bj_date(ts(2026, 9, 8, 23, 30)) == "2026-09-08"
