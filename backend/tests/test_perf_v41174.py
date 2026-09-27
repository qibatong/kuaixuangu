# -*- coding: utf-8 -*-
"""v4.11.74 两条新规则的仓库级单测 —— 都是**纯规则判定**，必须确定性可复现。

规则 1: `meoz_client._hist_ttl_for` —— 回看日（**绝对** tradedate 且**早于今天**）用长 TTL。
规则 2: `kpl.fill_close_change_from_kline` —— **盘前空窗短路**（目标日 == 今天 且北京 < 09:15）。

★ 本文件全部**硬编码日期 + 固定时钟**，不依赖运行时刻（否则会在别的日子悄悄变绿/变红）。
★ 「今天」的替身时钟必须走 `gmtime(time.time() + 8*3600)` 这一口径 ——
  生产代码就是"把北京时刻塞进按 UTC 解释的 struct_time"；直接用 `tzinfo=+8h` 构造会差 8 小时，
  曾让边界用例在盘前/盘中判反。
"""
import calendar
import sys
import time as _real_time

sys.path.insert(0, "backend")

from app.core import config                                     # noqa: E402
from app.services import fetcher                                 # noqa: E402
from app.services import kpl                                     # noqa: E402
from app.services import meoz_client                             # noqa: E402

TODAY = "2026-09-28"      # 交易日（freeze_day 在盘前返回它 —— 这正是本次要修的窗口）
HIST = "2026-09-24"       # 更早的交易日（回看日）


class _BjClock:
    """把模块里的 `time` 换成「北京时刻固定」的替身。

    只覆盖 `time()`；`gmtime` / `strftime` 转发真实模块（保持生产口径）。
    epoch 反推: 北京时刻对应 epoch = timegm(该时刻按 UTC 解释) - 8h
    ⇒ `gmtime(clock.time() + 8*3600)` 正好还原成北京时刻。
    """

    def __init__(self, bj_date, hh, mm):
        self._ts = calendar.timegm(
            _real_time.strptime("%s %02d:%02d:00" % (bj_date, hh, mm), "%Y-%m-%d %H:%M:%S")
        ) - 8 * 3600
        self._real = _real_time

    def __getattr__(self, name):                       # gmtime / strftime / mktime …
        return getattr(self._real, name)

    def time(self):
        return self._ts


# =========================================================================== #
# 规则 1：_hist_ttl_for
# =========================================================================== #
def _ttl(params, ttl=30.0, bj=(TODAY, 2, 0)):
    """在固定北京时刻下求 _hist_ttl_for（替身注入用函数内 monkeypatch，避免夹具耦合）。"""
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(meoz_client, "time", _BjClock(*bj))
        return meoz_client._hist_ttl_for(params, ttl)
    finally:
        mp.undo()


def test_hist_ttl_lookback_absolute_date_gets_long_ttl():
    """回看日（绝对日期，早于今天）→ 长 TTL。带横线写法必须等价。"""
    assert _ttl({"tradedate": "20260924"}) == 3600
    assert _ttl({"tradedate": "2026-09-24"}) == 3600
    assert _ttl({"tradedate": 20260924}) == 3600                      # int 也认
    assert _ttl({"symbols": "600000,000001", "tradedate": "20260924"}) == 3600


def test_hist_ttl_today_keeps_original_ttl_including_hyphen_form():
    """🔴 关键回归：**带横线写法的今天** 必须仍是原 TTL。

    历史缺陷：直接 `str(td) != today` 比较 → `2026-09-28` 被判成历史日
    ⇒ 交易日盘中的当天数据会被缓存 1 小时。必须先归一化再比较。
    """
    assert _ttl({"tradedate": "20260928"}) == 30
    assert _ttl({"tradedate": "2026-09-28"}) == 30                    # ← 归一化后正好命中今天


def test_hist_ttl_relative_offset_key_never_long():
    """🔴 实时路径是**相对键**（tradedate_offset）—— 跨自然日复用，绝不能进长 TTL 分支。"""
    assert _ttl({"tradedate_offset": 0}) == 30
    assert _ttl({"tradedate_offset": -1}) == 30
    assert _ttl({}) == 30
    assert _ttl({"tradedate": ""}) == 30
    assert _ttl(None) == 30


def test_hist_ttl_malformed_or_future_falls_back_to_original():
    """非法格式 / 未来日期一律走原 TTL（安全侧）。"""
    assert _ttl({"tradedate": "2026-9-24"}) == 30                     # 归一化后非 8 位 → 原 TTL
    assert _ttl({"tradedate": "abc"}) == 30
    assert _ttl({"tradedate": "20261001"}) == 30                      # 未来 → 原 TTL


def test_hist_ttl_never_shrinks_an_already_longer_ttl():
    """长 TTL 只做**抬升**，不压低调用方本来给的更大值。"""
    assert _ttl({"tradedate": "20260924"}, ttl=7200.0) == 7200
    assert _ttl({"tradedate": "20260924"}, ttl=600.0) == 3600


def test_hist_ttl_called_through_call_cached_uniform_entry(monkeypatch):
    """★ 接入点是 `call_cached` 的**统一入口** —— 用计数替身证明真实请求会带上长 TTL。

    这里记录的是 `store.set` 实际收到的 ttl（即真正落到缓存里的值），
    比只记录调用参数更接近事实。
    """
    written = []
    monkeypatch.setattr(meoz_client, "time", _BjClock(TODAY, 2, 0))
    monkeypatch.setattr(meoz_client, "call",
                        lambda apiname, params=None, fields=None: {"rows": []})
    monkeypatch.setattr(meoz_client.store, "get", lambda key, default=None: None)
    monkeypatch.setattr(meoz_client.store, "set",
                        lambda key, val, ttl=0: written.append((key, ttl)))

    meoz_client.call_cached("auc_kp", {"tradedate": "20260924"}, ttl=30)
    assert written, "未走到真实写入，替身未生效"
    assert written[0][1] == 3600, "回看日经 call_cached 统一入口后应写成 3600，实际 %r" % (written[0][1],)

    written.clear()
    meoz_client.call_cached("auc_kp", {"tradedate_offset": 0}, ttl=30)
    assert written and written[0][1] == 30, "实时相对键必须仍是原 TTL"

    written.clear()
    meoz_client.call_cached("auc_kp", {"tradedate": "20260928"}, ttl=30)
    assert written and written[0][1] == 30, "今天必须仍是原 TTL"


# =========================================================================== #
# 规则 2：fill_close_change_from_kline 盘前空窗短路
# =========================================================================== #
def _prepare(monkeypatch, bj_date, hh, mm):
    """清干净内存态 + 固定时钟 + 掐断库/落库，返回「兜底被调用了」的记录表。"""
    monkeypatch.setattr(kpl, "_CLOSE_CHG_CACHE", {})
    monkeypatch.setattr(kpl, "_CLOSE_CHG_RESYNCED", set())
    monkeypatch.setattr(kpl, "time", _BjClock(bj_date, hh, mm))
    monkeypatch.setattr(kpl, "_close_chg_db_get", lambda *a, **k: {})
    monkeypatch.setattr(kpl, "_close_chg_persist_allowed", lambda *a, **k: False)
    calls = []

    def _rec(name, ret=None):
        def _fn(*a, **k):
            calls.append(name)
            return ret
        return _fn

    # 四个兜底源全部替换成计数替身（任一被打到都会记录）
    monkeypatch.setattr(fetcher, "fetch_stock_chart_robust", _rec("robust"))
    monkeypatch.setattr(kpl, "_close_chg_pct_sina", _rec("sina"))
    monkeypatch.setattr(kpl, "_close_chg_pct_tencent", _rec("tencent"))
    monkeypatch.setattr(kpl, "_close_chg_pct_ths", _rec("ths"))
    return calls


def _lst():
    return [{"code": "600000", "change": 1.11, "realChange": 1.11},
            {"code": "000001", "change": 2.22, "realChange": 2.22}]


def test_fill_close_change_premarket_today_short_circuits_and_touches_nothing(monkeypatch):
    """🔴 主用例：盘前（北京 02:00）目标日 == 今天 ⇒ 返回 0、**一次兜底都不打**、字段一字不动。"""
    calls = _prepare(monkeypatch, TODAY, 2, 0)
    lst = _lst()
    assert kpl.fill_close_change_from_kline(lst, TODAY) == 0
    assert calls == [], "盘前短路被绕过，实际打了兜底源: %r" % (calls,)
    assert [it["change"] for it in lst] == [1.11, 2.22], "短路不应改动任何字段"
    assert [it["realChange"] for it in lst] == [1.11, 2.22]


def test_fill_close_change_before_0915_boundary(monkeypatch):
    """边界：09:14 → 短路；09:15 → 不短路（保留盘中/开盘后的原行为）。"""
    calls = _prepare(monkeypatch, TODAY, 9, 14)
    lst = _lst()
    kpl.fill_close_change_from_kline(lst, TODAY)
    assert calls == [], "09:14 应仍在短路窗口内"

    calls2 = _prepare(monkeypatch, TODAY, 9, 15)
    lst2 = _lst()
    kpl.fill_close_change_from_kline(lst2, TODAY)
    assert calls2, "09:15 起不应再短路（需保留当日日K覆盖能力）"


def test_fill_close_change_after_open_still_overrides_with_todays_kline(monkeypatch):
    """09:20（盘中）目标日 == 今天 ⇒ 走原路径：拿当天那根日K算收盘涨幅并覆盖。"""
    calls = _prepare(monkeypatch, TODAY, 9, 20)
    monkeypatch.setattr(
        fetcher, "fetch_stock_chart_robust",
        lambda code, period: (calls.append("robust"),
                              {"time": [HIST, TODAY], "close": [10.0, 11.0]})[1])
    lst = [_lst()[0]]
    assert kpl.fill_close_change_from_kline(lst, TODAY) == 1
    assert lst[0]["change"] == 10.0, "应为 (11-10)/10*100 = 10.0"
    assert lst[0]["realChange"] == 10.0
    assert lst[0]["real_change"] == 10.0


def test_fill_close_change_lookback_date_is_never_short_circuited(monkeypatch):
    """🔴 关键回归：短路**只对"今天"生效** —— 回看日即便同样在盘前也照常补齐。"""
    calls = _prepare(monkeypatch, TODAY, 2, 0)          # 时钟仍是盘前 02:00
    monkeypatch.setattr(
        fetcher, "fetch_stock_chart_robust",
        lambda code, period: (calls.append("robust"),
                              {"time": ["2026-09-23", HIST], "close": [20.0, 22.0]})[1])
    lst = [_lst()[0]]
    assert kpl.fill_close_change_from_kline(lst, HIST) == 1
    assert lst[0]["change"] == 10.0, "回看日 2026-09-24 应得到 (22-20)/20*100 = 10.0"


def test_fill_close_change_empty_inputs_are_noop():
    """空列表 / 空日期 → 直接 0，不抛异常（既有契约，加回归保护）。"""
    assert kpl.fill_close_change_from_kline([], TODAY) == 0
    assert kpl.fill_close_change_from_kline(_lst(), "") == 0
    assert kpl.fill_close_change_from_kline(None, TODAY) == 0


def test_config_db_is_isolated_temp_db():
    """自检：本文件跑在 conftest 的临时库上，不会碰到真实库。"""
    assert config.DB_FILE, "DB_FILE 未设置"
    assert "kuaixuan_test_" in str(config.DB_FILE) or str(config.DB_FILE).endswith(".db")
