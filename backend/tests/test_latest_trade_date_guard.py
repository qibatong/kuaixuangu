# -*- coding: utf-8 -*-
"""读侧「最近交易日」交易日历过滤 —— 纯函数回归 (2026-09-27 v4.11.66)
=====================================================================
事故: 2026-09-25(中秋 · 周五 · **法定休市**, 见 core/trade_calendar.py:HOLIDAYS_2026)当天
      傍晚才补上调度侧日历门禁, 白天照常跑了 4 枪快照采集。源端三路全空
      (daily_auc tradedate≠20260925 / screening 竞价 0 只 / TickPlus 0 条), 系统自己
      打了 `[数据质量] 竞价封单数据异常` 也照落库 —— 于是那一整天写进 snapshot_bid 的
      四个时点, 值**各自都等于 09-24 的 9_25 定格值**(静态复制)。

      09-27(周日)读侧用裸 `SELECT MAX(date)` 取"最近交易日" → 取到 09-25 幽灵日
      ⇒ 竞价异动十个 tab 全被静态值顶掉: 竞价封单三层排序退化成三层同值;
        竞价爆量「今日÷昨日」量比恒 1.0 → 被"量比>2"全量滤掉 → **tab 变空**;
        昨涨停/昨断板去问选股宝要休市日数据 → **空表**; 昨炸板与今炸板塌到同一天。

修复: `trade_calendar.latest_trade_in(candidates, day)` —— 在已有候选里挑最近**真交易日**,
      不做纯日历推算(那样可能挑到库里根本没有的日期), 全不合规时 fail-open 返回 None。

本文件用**硬编码日期**(不依赖运行时钟)锁住该行为。
"""
import pytest

from app.core import trade_calendar as tc

# 2026-09-25 = 中秋(周五, 法定休市); 09-24 周四、09-26 周六、09-27 周日、09-28 周一
HOLIDAY = "2026-09-25"
THU = "2026-09-24"
SAT = "2026-09-26"
SUN = "2026-09-27"
MON = "2026-09-28"
# 远期历史交易日(2026-08-12 周三), 用于"past trade day 应放行"的断言而不引入时钟依赖
PAST_TRADE = "2026-08-12"


def test_calendar_baseline_20260925():
    """先钉住日历本身(若哪天误删表里的 09-25, 本文件其余断言会静默失去意义)"""
    assert tc.is_holiday(HOLIDAY) is True
    assert tc.is_trade_day(HOLIDAY) is False
    assert tc.is_trade_day(THU) is True
    assert tc.is_trade_day(SAT) is False
    assert tc.is_trade_day(SUN) is False
    assert tc.is_trade_day(MON) is True


def test_holiday_candidate_is_skipped():
    """★ 核心回归: 候选里带休市日的幽灵行 → 必须跳到真交易日"""
    assert tc.latest_trade_in([HOLIDAY, THU, "2026-09-23"], SUN) == THU
    # 周末日同理
    assert tc.latest_trade_in([SAT, THU], SUN) == THU


def test_respects_upper_bound():
    """上界约束: 候选里比 day 晚的必须排除(否则历史回看会串到"未来"数据)"""
    assert tc.latest_trade_in([MON, THU], SUN) == THU
    assert tc.latest_trade_in(["2026-09-28"], HOLIDAY) is None


def test_all_invalid_returns_none():
    """候选全不合规 / 为空 → None, 由调用方保留原值(fail-open, 绝不主动留空)"""
    assert tc.latest_trade_in([HOLIDAY, SAT, SUN], SUN) is None
    assert tc.latest_trade_in([], SUN) is None
    assert tc.latest_trade_in(None, SUN) is None


def test_day_none_returns_first_trade_candidate():
    """day=None(取"表内最近交易日"形态) → 返回第一个合规候选"""
    assert tc.latest_trade_in([HOLIDAY, THU], None) == THU
    assert tc.latest_trade_in([SAT, THU], None) == THU


def test_accepts_date_and_struct_time():
    """候选可能是 sqlite 返回的字符串/date/struct_time, 一律要能识别"""
    import time
    from datetime import date as _d
    assert tc.latest_trade_in([_d(2026, 9, 25), _d(2026, 9, 24)], SUN) == THU
    g = time.gmtime(time.mktime(time.strptime(SUN, "%Y-%m-%d")))
    assert tc.latest_trade_in([HOLIDAY, THU], g) == THU


def test_junk_entries_are_ignored():
    """坏值(None/空串/不可解析)不得让整条挑选失败"""
    assert tc.latest_trade_in([None, "", "not-a-date", HOLIDAY, THU], SUN) == THU


# ===================== 写侧同源门禁 (v4.11.66 同批) =====================
def test_close_chg_persist_blocked_on_non_trade_day():
    """写侧 `kpl._close_chg_persist_allowed` 必须带**交易日**门禁。

    原实现只有"是否已收盘"一个维度 ⇒ `date < 今天` 一律放行、
    `date == 今天 且 过 15:00` 放行 ⇒ **周六/节假日 15:00 后**直接命中。
    实测后果: 测试机 2026-09-26(周六) 在 close_change_history 落 57 行、
    09-05(周六) 落 47 行。非交易日本无收盘价, 写进去是相邻交易日 K 线重复值。
    """
    from app.services import kpl
    assert kpl._close_chg_persist_allowed(HOLIDAY) is False   # 中秋休市
    assert kpl._close_chg_persist_allowed(SAT) is False       # 周六
    assert kpl._close_chg_persist_allowed(SUN) is False       # 周日
    # 历史交易日回填不受影响(原"date<今天 一律放行"的正向用途必须保留)。
    # ⚠️ 用**远期**历史日(2026-08-12 周三)而非 THU: 该函数含"date<今天"分支,
    #    若拿近期日期断言 True 就等于把运行时钟偷偷写进断言(本文件立身原则是不依赖时钟)。
    assert kpl._close_chg_persist_allowed(PAST_TRADE) is True
    assert kpl._close_chg_persist_allowed("") is False        # 空值仍拒绝
