# -*- coding: utf-8 -*-
"""`close_change_daily`(全市场收盘涨跌幅落库) 与 `_apply_change_for` 北京日期口径 的仓库级单测

背景(2026-09-28 生产实测):
  `close_change_history` 原为**惰性填充** —— 只有被请求到的股票才写库, 实测覆盖
  每日 45~680 行(全市场 5000+) ⇒ 未被请求到的股票每次都要现场多源拉日K, 而猫爪
  `a=daily` 持续 429 限流(退避 1s)。代价: 「龙虎榜」实时路径每请求 +1.0s、
  历史回看日冷态 +4.0s。本模块负责每交易日把该表写满。

★ 全部硬编码日期 + 固定时钟(与 `test_perf_v41174.py` 同口径), 不依赖运行时刻。
★ 本文件**不打网络**、**不碰真库**: 行情拉取与落库都在测试内替换。
"""
import calendar
import sys
import time as _real_time

sys.path.insert(0, "backend")

from app.api import kpl as api_kpl                                  # noqa: E402
from app.core import trade_calendar as tc                           # noqa: E402
from app.services import close_change_daily as ccd                  # noqa: E402
from app.services import fetcher                                    # noqa: E402
from app.services import kpl                                        # noqa: E402

TODAY = "2026-09-28"      # 交易日(周一)
HIST = "2026-09-24"       # 更早的交易日(回看日)


class _BjClock:
    """固定北京时刻的 `time` 替身: 只覆盖 `time()`, `gmtime`/`strftime` 转发真实模块。

    epoch 反推: 北京时刻对应 epoch = timegm(该时刻按 UTC 解释) - 8h
    ⇒ `gmtime(clock.time() + 8*3600)` 正好还原成北京时刻(与生产同口径)。
    """

    def __init__(self, bj_date, hh, mm):
        self._ts = calendar.timegm(
            _real_time.strptime("%s %02d:%02d:00" % (bj_date, hh, mm), "%Y-%m-%d %H:%M:%S")
        ) - 8 * 3600
        self._real = _real_time

    def __getattr__(self, name):
        return getattr(self._real, name)

    def time(self):
        return self._ts


def _quote_map(n, bad=0):
    """造 n 只行情行; 前 bad 只为「realChange/change 都为 None」的坏行"""
    m = {}
    for i in range(n):
        code = "%06d" % (600000 + i)
        if i < bad:
            m[code] = {"realChange": None, "change": None}
        else:
            m[code] = {"realChange": round((i % 2000) / 100.0 - 10, 2)}
    return m


def _after_close(monkeypatch):
    """把「当下」固定为当日收盘后, 并放行门禁"""
    monkeypatch.setattr(ccd, "time", _BjClock(TODAY, 19, 0))
    monkeypatch.setattr(kpl, "_close_chg_persist_allowed", lambda d: True)


# =========================================================================== #
# 一、污染防护(本模块唯一的风险点)
# =========================================================================== #
def test_save_day_rejects_non_today(monkeypatch):
    """★ 行情快照只反映「当下」: 传历史日必须**直接拒绝且不拉行情**。

    `_close_chg_persist_allowed` 对 `date < 今天` 是放行的(其设计场景是逐只日K回填),
    若这里不显式挡住, 就会把今天的涨幅写进历史日 —— 那是**永久性**数据污染。
    """
    monkeypatch.setattr(ccd, "time", _BjClock(TODAY, 19, 0))
    called = []
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: called.append(fs) or {})
    put = []
    monkeypatch.setattr(kpl, "_close_chg_db_put", lambda d, p: put.append((d, p)))
    assert ccd.save_day(HIST) == 0
    assert called == [], "历史日不得触发行情拉取"
    assert put == [], "历史日不得写库"


def test_save_day_blocked_before_close(monkeypatch):
    """门禁未放行(未过 15:00 / 非交易日) → 不拉行情、不写库"""
    monkeypatch.setattr(ccd, "time", _BjClock(TODAY, 10, 0))
    monkeypatch.setattr(kpl, "_close_chg_persist_allowed", lambda d: False)
    called = []
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: called.append(fs) or {})
    assert ccd.save_day() == 0
    assert called == []


# =========================================================================== #
# 二、落库正确性
# =========================================================================== #
def test_save_day_skips_incomplete_quote(monkeypatch):
    """行情明显不完整(< MIN_CODES) → 不写库, 留给后续自检重试"""
    _after_close(monkeypatch)
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: _quote_map(100))
    put = []
    monkeypatch.setattr(kpl, "_close_chg_db_put", lambda d, p: put.append((d, p)))
    assert ccd.save_day() == 0
    assert put == []


def test_save_day_writes_full_market(monkeypatch):
    """正常路径: 全市场一次写入, code→pct 精确传递, 且只写当日"""
    _after_close(monkeypatch)
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: _quote_map(5000))
    put = []
    monkeypatch.setattr(kpl, "_close_chg_db_put", lambda d, p: put.append((d, p)))
    monkeypatch.setattr(ccd, "_day_rows", lambda d: 5000)
    assert ccd.save_day() == 5000
    assert len(put) == 1
    assert put[0][0] == TODAY, "必须写在当日键下"
    assert len(put[0][1]) == 5000
    assert put[0][1]["600100"] == round((100 % 2000) / 100.0 - 10, 2)


def test_save_day_drops_rows_without_change(monkeypatch):
    """realChange/change 都为 None 的坏行必须剔除 —— 绝不能写成 0 冒充涨跌幅"""
    _after_close(monkeypatch)
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: _quote_map(4000, bad=500))
    put = []
    monkeypatch.setattr(kpl, "_close_chg_db_put", lambda d, p: put.append((d, p)))
    monkeypatch.setattr(ccd, "_day_rows", lambda d: 3500)
    assert ccd.save_day() == 3500
    assert len(put[0][1]) == 3500
    assert "600000" not in put[0][1], "坏行(canonical None)不得入库"


# =========================================================================== #
# 三、自检条件(自愈的关键: 跨重启/跨时点都能补)
# =========================================================================== #
def _needed(monkeypatch, hm, rows, trade=True, gate=True):
    monkeypatch.setattr(ccd, "_bj", lambda: (None, hm))
    monkeypatch.setattr(ccd, "_day_rows", lambda d: rows)
    monkeypatch.setattr(kpl, "_close_chg_persist_allowed", lambda d: gate)
    monkeypatch.setattr(tc, "is_trade_day_of", lambda g: trade)
    return ccd._needed(None, TODAY)


def test_needed_false_before_run_at(monkeypatch):
    assert _needed(monkeypatch, ccd.RUN_AT - 1, 0) is False


def test_needed_false_on_non_trade_day(monkeypatch):
    assert _needed(monkeypatch, ccd.RUN_AT + 30, 0, trade=False) is False


def test_needed_false_when_day_already_full(monkeypatch):
    assert _needed(monkeypatch, ccd.RUN_AT + 30, ccd.DONE_ROWS) is False


def test_needed_false_when_gate_denies(monkeypatch):
    assert _needed(monkeypatch, ccd.RUN_AT + 30, 0, gate=False) is False


def test_needed_true_when_day_incomplete(monkeypatch):
    assert _needed(monkeypatch, ccd.RUN_AT + 30, 100) is True


# =========================================================================== #
# 四、`_apply_change_for` 的「今天」必须是北京日期
# =========================================================================== #
def test_apply_change_for_uses_beijing_today(monkeypatch):
    """★ 如实说明: 这是**不变量固定**, 不是活 bug 复现。

    默认实现曾写 `time.strftime("%Y-%m-%d", time.gmtime())`(UTC)。分析结论:
    `today` 只被 `_is_intraday() and serve_date == today` 消费, 而 `_is_intraday()` 的
    窗口是北京 09:30~15:00 —— 该区间内 BJ 与 UTC **必然同日期** ⇒ 差异当前**不可达**。
    本用例把 `_is_intraday` 置 True 并取北京 02:00(UTC 为前一天) 来固定这个不变量,
    防止将来放宽盘中窗口时把"今天"判错(那样会让"盘中看今日"退回历史分支、丢失实时现涨)。
    """
    monkeypatch.setattr(api_kpl, "_time", _BjClock(TODAY, 2, 0))
    monkeypatch.setattr(api_kpl, "_is_intraday", lambda: True)
    spot = []
    monkeypatch.setattr(api_kpl, "_update_spot_change", lambda lst: spot.append(1) or 0)
    kline = []
    monkeypatch.setattr(kpl, "fill_close_change_from_kline", lambda lst, d: kline.append(d) or 0)
    api_kpl._apply_change_for([{"code": "600000"}], TODAY)
    assert spot == [1], "北京日期 == serve_date 时应走实时现涨分支"
    assert kline == [], "不得退回历史(收盘涨幅固定)分支"


def test_apply_change_for_historical_date_uses_close_change(monkeypatch):
    """历史回看日: 无论盘中与否都走「当日收盘涨幅」固定值"""
    monkeypatch.setattr(api_kpl, "_time", _BjClock(TODAY, 10, 0))
    monkeypatch.setattr(api_kpl, "_is_intraday", lambda: True)
    spot = []
    monkeypatch.setattr(api_kpl, "_update_spot_change", lambda lst: spot.append(1) or 0)
    kline = []
    monkeypatch.setattr(kpl, "fill_close_change_from_kline", lambda lst, d: kline.append(d) or 0)
    api_kpl._apply_change_for([{"code": "600000"}], HIST)
    assert spot == [], "回看历史日不得用实时现涨"
    assert kline == [HIST], "回看历史日必须取该日收盘涨幅"
