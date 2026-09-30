# -*- coding: utf-8 -*-
"""竞价期静默窗口(meoz_client.quiet_now) —— 2026-09-30 主人「测试机竞价期间不要去拉数据」。

覆盖:
  ① parse_window: 正常/空/非法/全角/跨零点
  ② quiet_now:    未配置不生效 / 窗口内 / **闭区间两端** / 窗外 / 非交易日 / 跨零点
  ③ call():       窗口内 **完全不碰上游**(连 _post_one 都不调) 且返回 None; 窗外正常放行
  ④ 载体优先级:   settings `meoz_quiet_window` 覆盖 config.MEOZ_QUIET_WINDOW
  ⑤ 缓存命中:     窗口内若已有缓存仍可返回(不打上游) —— call_cached 的行为不被破坏
  ⑥ 语义隔离:     enabled() 不受静默影响(它仍只回答"是否配置可用")
"""
import calendar
import datetime

import pytest

from app.services import meoz_client as M

# 2026-09-29(周二) 是交易日(当日库内有完整快照); 2026-09-26 是周六。
TRADE_DAY = (2026, 9, 29)
SATURDAY = (2026, 9, 26)


def bj_ts(y, mo, d, h, mi, s=0):
    """构造一个 ts, 使 `time.gmtime(ts + 8*3600)` 恰好等于给定**北京时间**。"""
    naive = datetime.datetime(y, mo, d, h, mi, s)
    return calendar.timegm(naive.timetuple()) - 8 * 3600


# ---------------------------------------------------------------- ① parse_window
def test_parse_normal():
    assert M.parse_window("09:05-09:30") == (9 * 3600 + 5 * 60, 9 * 3600 + 30 * 60)


def test_parse_empty_or_none_is_disabled():
    assert M.parse_window("") is None
    assert M.parse_window(None) is None
    assert M.parse_window("   ") is None


@pytest.mark.parametrize("bad", ["garbage", "09:05", "9-5", "25:00-26:00", "09:70-10:00", "a:b-c:d"])
def test_parse_invalid_fails_open(bad):
    """非法配置**不静默**(fail-open): 宁可照常拉, 也不要因错别字把该机猫爪源整天关掉。"""
    assert M.parse_window(bad) is None


def test_parse_accepts_fullwidth_and_tilde():
    """手工在 settings 里填时很容易打出全角冒号/波浪号。"""
    assert M.parse_window("09：05～09:30") == (9 * 3600 + 5 * 60, 9 * 3600 + 30 * 60)
    assert M.parse_window("09:05~09:30") == (9 * 3600 + 5 * 60, 9 * 3600 + 30 * 60)


def test_parse_wrap_around_midnight_kept_as_is():
    """跨零点由 start>end 表达, 解析层不拆开。"""
    assert M.parse_window("23:50-00:10") == (23 * 3600 + 50 * 60, 10 * 60)


# ---------------------------------------------------------------- ② quiet_now
def _set_window(monkeypatch, spec, *, via_settings=True):
    if via_settings:
        monkeypatch.setattr(M, "_get_setting", lambda k: spec)
    else:
        monkeypatch.setattr(M, "_get_setting", lambda k: None)
        monkeypatch.setattr(M.config, "MEOZ_QUIET_WINDOW", spec, raising=False)


def test_not_configured_never_quiet(monkeypatch):
    _set_window(monkeypatch, "")
    for hm in ((9, 5), (9, 20), (9, 30)):
        assert M.quiet_now(bj_ts(*TRADE_DAY, hm[0], hm[1])) is False


def test_inside_window(monkeypatch):
    _set_window(monkeypatch, "09:05-09:30")
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 20)) is True


def test_window_is_closed_interval_both_ends(monkeypatch):
    """闭区间: 起止两端**都算**在窗口内 —— 采集链的时点常正好压在边界上。"""
    _set_window(monkeypatch, "09:05-09:30")
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 5, 0)) is True
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 30, 0)) is True
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 4, 59)) is False
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 30, 1)) is False


def test_non_trade_day_never_quiet(monkeypatch):
    """周六即便落在窗口内也不静默 —— 没有竞价就没有配额争抢, 且便于周末调试。"""
    _set_window(monkeypatch, "09:05-09:30")
    assert M.quiet_now(bj_ts(*SATURDAY, 9, 20)) is False


def test_wrap_around_midnight(monkeypatch):
    _set_window(monkeypatch, "23:50-00:10")
    assert M.quiet_now(bj_ts(*TRADE_DAY, 23, 55)) is True
    assert M.quiet_now(bj_ts(*TRADE_DAY, 0, 5)) is True
    assert M.quiet_now(bj_ts(*TRADE_DAY, 12, 0)) is False


def test_settings_overrides_env(monkeypatch):
    """载体优先级: settings 优先, 回落 config —— 与 _apikey()/_lines() 同一约定。"""
    monkeypatch.setattr(M.config, "MEOZ_QUIET_WINDOW", "01:00-02:00", raising=False)
    _set_window(monkeypatch, "09:05-09:30", via_settings=True)
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 20)) is True     # settings 生效
    assert M.quiet_now(bj_ts(*TRADE_DAY, 1, 30)) is False    # env 那份被覆盖


def test_env_fallback_when_settings_absent(monkeypatch):
    _set_window(monkeypatch, "09:05-09:30", via_settings=False)
    assert M.quiet_now(bj_ts(*TRADE_DAY, 9, 20)) is True
    assert M.quiet_now(bj_ts(*TRADE_DAY, 11, 0)) is False


# ---------------------------------------------------------------- ③ call() 闸门
@pytest.fixture
def no_network(monkeypatch):
    """把 apikey 配上(enabled() 为真), 并让**任何**真实出网都失败可见。"""
    monkeypatch.setattr(M, "_apikey", lambda: "test-apikey-not-real")
    calls = []

    def fake_post(url, payload, timeout):
        calls.append(payload.get("apiname"))
        return {"code": 200, "data": [{"symbol": "600000"}]}

    monkeypatch.setattr(M, "_post_one", fake_post)
    return calls


def test_call_blocked_and_no_upstream_hit(monkeypatch, no_network):
    """窗口内: 返回 None, 且 `_post_one` **一次都没被调用** —— 这是本限制的核心断言。"""
    monkeypatch.setattr(M, "quiet_now", lambda ts=None: True)
    monkeypatch.setattr(M, "_log_quiet_skip", lambda apiname: None)
    assert M.call("screening") is None
    assert no_network == [], "静默窗口内不得产生任何上游请求"


def test_call_passes_when_not_quiet(monkeypatch, no_network):
    monkeypatch.setattr(M, "quiet_now", lambda ts=None: False)
    data = M.call("screening")
    assert data is not None
    assert no_network == ["screening"]


def test_call_gate_uses_real_quiet_now_and_blocks(monkeypatch, no_network):
    """不走替身: 用真实 quiet_now + 真实的窗口配置, 只把"当前时间"落在窗口内。

    做法: 把窗口配成覆盖**当前真实时刻**的一整天范围, 这样无需注入 ts 即可命中。
    """
    # 用仓库自带的 bj_date()(返回 "YYYY-MM-DD"); 直接喂 datetime 会让 is_trade_day
    # 拿到 "2026-09-30T01:51:00" 这种带 T 的 isoformat 而抛 ValueError。
    if not M.tc.is_trade_day(M.tc.bj_date()):
        pytest.skip("今天非交易日: quiet_now 按设计不静默, 本用例无从命中")
    _set_window(monkeypatch, "00:00-23:59")
    monkeypatch.setattr(M, "_log_quiet_skip", lambda apiname: None)
    assert M.quiet_now() is True
    assert M.call("screening") is None
    assert no_network == []


def test_enabled_semantics_unchanged(monkeypatch):
    """enabled() 仍只回答"是否配置可用", 不被静默污染(否则健康盘面等会跟着漂)。"""
    monkeypatch.setattr(M, "_apikey", lambda: "k")
    _set_window(monkeypatch, "09:05-09:30")
    assert M.enabled() is True


# ---------------------------------------------------------------- ⑤ 缓存命中
def test_cached_hit_still_returned_during_quiet(monkeypatch, no_network):
    """窗口内若**已有缓存**仍可返回(不打上游) —— 限制的是出网, 不是读缓存。"""
    _set_window(monkeypatch, "09:05-09:30")
    monkeypatch.setattr(M, "quiet_now", lambda ts=None: True)
    cached = {"code": 200, "data": [{"symbol": "600000", "name": "浦发银行"}]}
    monkeypatch.setattr(M.store, "get", lambda k: cached)
    assert M.call_cached("screening", {"date": "2026-09-29"}) == cached
    assert no_network == []


def test_quiet_result_is_not_cached(monkeypatch, no_network):
    """被静默拦下的空结果**不得写进缓存** —— 否则窗口结束后仍会读到这份空数据。"""
    _set_window(monkeypatch, "09:05-09:30")
    monkeypatch.setattr(M, "quiet_now", lambda ts=None: True)
    monkeypatch.setattr(M.store, "get", lambda k: None)
    wrote = []
    monkeypatch.setattr(M.store, "set", lambda k, v, ttl: wrote.append(k))
    assert M.call_cached("screening") is None
    assert wrote == [], "空结果不得入缓存"
    assert no_network == []
