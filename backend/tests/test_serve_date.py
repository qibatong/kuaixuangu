# -*- coding: utf-8 -*-
"""服务日期唯一判据(serve_date/allow_back) —— 主人 2026-09-30「数据日期规矩」。

规矩: 交易日 **09:00 前**显示上一交易日; **09:00 起**显示当天;
      **当天拿不到数据也不能用上一交易日的数据**(09:00~09:15 因此必然是空白)。

本文件的重点是**边界**: 08:59:59 / 09:00:00 这一秒之差, 以及"非交易日永远可回退"。
"""
import calendar
import datetime

import pytest

from app.services import serve_date as S

TRADE = (2026, 9, 29)       # 周二, 交易日(当日库内有完整快照)
TRADE2 = (2026, 9, 28)      # 周一, 交易日
SAT = (2026, 9, 26)         # 周六
HOLIDAY = (2026, 9, 25)     # 中秋 · 法定休市(周五)


def bj_ts(y, mo, d, h, mi, s=0):
    """构造 ts, 使 `time.gmtime(ts + 8*3600)` 恰为给定**北京时间**。"""
    naive = datetime.datetime(y, mo, d, h, mi, s)
    return calendar.timegm(naive.timetuple()) - 8 * 3600


# ---------------------------------------------------------------- 日历前提自检
def test_calendar_baseline():
    """夹具前提: 这些日子到底是不是交易日 —— 前提错了后面全是假绿。"""
    import app.core.trade_calendar as tc
    assert tc.is_trade_day("2026-09-29") is True
    assert tc.is_trade_day("2026-09-28") is True
    assert tc.is_trade_day("2026-09-26") is False   # 周六
    assert tc.is_trade_day("2026-09-25") is False   # 中秋休市


# ---------------------------------------------------------------- allow_back
def test_allow_back_trade_day_switches_exactly_at_0900():
    """🔴 本规矩的核心: 08:59:59 允许回退, 09:00:00 起禁止 —— 一秒之差。"""
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 0, 0, 0)) is True
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 8, 59, 59)) is True
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 9, 0, 0)) is False    # ← 分界
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 9, 0, 1)) is False
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 9, 10)) is False      # 09:00~09:15 空窗
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 14, 0)) is False
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 15, 0)) is False      # 收盘后
    assert S.allow_back("2026-09-29", bj_ts(*TRADE, 23, 59, 59)) is False


def test_allow_back_offday_always_allowed():
    """周末/法定休市: 没有"当天"可言, 任何时刻都允许回退。"""
    for hm in ((0, 0), (9, 0), (12, 0), (23, 59)):
        assert S.allow_back("2026-09-26", bj_ts(*SAT, hm[0], hm[1])) is True
        assert S.allow_back("2026-09-25", bj_ts(*HOLIDAY, hm[0], hm[1])) is True


@pytest.mark.parametrize("bad", ["", None, "garbage", "2026-13-45"])
def test_allow_back_unknown_day_is_conservative(bad):
    """无法识别的一天 ⇒ **保守禁止回退**: 宁可显示空, 也不拿昨天冒充今天。"""
    assert S.allow_back(bad, bj_ts(*TRADE, 3, 0)) is False


def test_allow_back_past_trade_day_never_allowed():
    """🔴 历史交易日**任何时刻**都不许平移 —— 否则用户回看某天会被静默挪走一天
    (2026-09-29 龙虎榜"回看 09-29 拿到 09-28"就是这一类事故)。

    注意 03:00 这个时刻: 单看"现在没到 09:00"会误判为"允许", 必须靠 `d != 今天` 挡住。
    """
    for hm in ((3, 0), (8, 0), (9, 30), (20, 0)):
        assert S.allow_back("2026-09-28", bj_ts(*TRADE, hm[0], hm[1])) is False
    # 非交易日的历史日仍然允许(它本来就要对齐到最近交易日)
    assert S.allow_back("2026-09-26", bj_ts(*TRADE, 3, 0)) is True


def test_allow_back_now_answers_about_today_only():
    """`allow_back_now` = 问"现在能不能退": 交易日 09:00 前可、09:00 起不可。"""
    assert S.allow_back_now(bj_ts(*TRADE, 8, 59, 59)) is True
    assert S.allow_back_now(bj_ts(*TRADE, 9, 0, 0)) is False
    assert S.allow_back_now(bj_ts(*TRADE, 12, 0)) is False
    assert S.allow_back_now(bj_ts(*SAT, 12, 0)) is True       # 周末: 可
    assert S.allow_back_now(bj_ts(*HOLIDAY, 12, 0)) is True   # 法定休市: 可


# ---------------------------------------------------------------- serve_date
def test_serve_date_explicit_never_falls_back():
    """用户显式选日期 = 他就要看那一天, 任何时刻都原样返回。"""
    for hm in ((3, 0), (9, 10), (20, 0)):
        assert S.serve_date("2026-08-11", bj_ts(*TRADE, hm[0], hm[1])) == ("2026-08-11", S.MODE_EXPLICIT)
    # 甚至显式选了"非交易日"也不改
    assert S.serve_date("2026-09-26", bj_ts(*TRADE, 3, 0)) == ("2026-09-26", S.MODE_EXPLICIT)


def test_serve_date_trade_day_before_0900_is_previous_trade_day():
    """凌晨 2 点: 属于上一个交易日的尾巴 ⇒ 显示 09-28(09-29 是交易日)。
    这正是主人截图里"凌晨 2 点两张空表"被修掉的那一条。"""
    assert S.serve_date("", bj_ts(*TRADE, 2, 0)) == ("2026-09-28", S.MODE_PREV)
    assert S.serve_date("", bj_ts(*TRADE, 8, 59, 59)) == ("2026-09-28", S.MODE_PREV)


def test_serve_date_trade_day_from_0900_is_today():
    assert S.serve_date("", bj_ts(*TRADE, 9, 0, 0)) == ("2026-09-29", S.MODE_TODAY)
    assert S.serve_date("", bj_ts(*TRADE, 9, 10)) == ("2026-09-29", S.MODE_TODAY)      # 空窗仍是今天
    assert S.serve_date("", bj_ts(*TRADE, 14, 30)) == ("2026-09-29", S.MODE_TODAY)
    assert S.serve_date("", bj_ts(*TRADE, 22, 0)) == ("2026-09-29", S.MODE_TODAY)


def test_serve_date_monday_before_0900_goes_to_friday():
    """跨周末: 周一凌晨应显示上周五, 且要是**交易日**(日历回退, 不是减一天)。"""
    import app.core.trade_calendar as tc
    d, mode = S.serve_date("", bj_ts(2026, 9, 28, 3, 0))
    assert mode == S.MODE_PREV
    assert d == "2026-09-25" or tc.is_trade_day(d)      # 09-25 是中秋休市 ⇒ 应落到 09-24
    assert tc.is_trade_day(d) is True


def test_serve_date_offday_mode():
    """周六任何时刻 ⇒ mode=offday, 由调用方对齐到最近交易日。"""
    assert S.serve_date("", bj_ts(*SAT, 3, 0)) == ("2026-09-26", S.MODE_OFFDAY)
    assert S.serve_date("", bj_ts(*SAT, 9, 30)) == ("2026-09-26", S.MODE_OFFDAY)
    # 法定休市日同理(09-25 是周五, 极易被当成交易日)
    assert S.serve_date("", bj_ts(*HOLIDAY, 10, 0)) == ("2026-09-25", S.MODE_OFFDAY)


def test_today_helper():
    assert S.today(bj_ts(*TRADE, 0, 1)) == "2026-09-29"
    assert S.today(bj_ts(*TRADE, 23, 59)) == "2026-09-29"
    assert S.today(bj_ts(*TRADE2, 0, 0, 1)) == "2026-09-28"
