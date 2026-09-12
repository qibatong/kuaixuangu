# -*- coding: utf-8 -*-
"""P1(2026-09-13)股性采集修复的守护测试

覆盖三处改动, 防止被后续重构悄悄改回去:
  * P1-a 盘后存档: 时刻 15:30→18:30; _fired **成功后**才置位 + 失败退避重试
  * P1-b 盘前补救: 09:00-09:05 窗口回溯补齐缺失交易日(limit_history / lhb_history)
  * P1-c 日K TTL : 缓存末根日期 < 期望最近交易日 → 回源(旧逻辑命中即永久返回)

这些是**纯单测**: 全部 mock DB 与上游, 不发任何真实网络请求。
"""
import inspect
import json
import types

import pytest


def _mod():
    from app.services import stock_temper
    return stock_temper


# ---------------- 假 DB 连接 ----------------
class _Row:
    def __init__(self, v):
        self._v = v

    def fetchone(self):
        return self._v


class _FakeConn:
    """execute 按 SQL 前缀分流: SELECT 返回预设行, 写操作记录到 self.writes"""

    def __init__(self, row=None, count=0):
        self.row = row
        self.count = count
        self.writes = []

    def execute(self, sql, params=None):
        s = sql.strip().upper()
        if s.startswith("SELECT"):
            if "COUNT(*)" in s:
                return _Row((self.count,))

            return _Row(self.row)
        self.writes.append((sql, params))
        return self

    def commit(self):
        pass

    def close(self):
        pass


# ==================== P1-c: 日K缓存新鲜度 ====================
def test_norm_day_various_formats():
    st = _mod()
    assert st._norm_day("2026-09-11") == "2026-09-11"
    assert st._norm_day("2026/09/11") == "2026-09-11"
    assert st._norm_day("20260911") == "2026-09-11"
    assert st._norm_day("2026-09-11 00:00:00") == "2026-09-11"
    assert st._norm_day("2026-09-11T15:00:00") == "2026-09-11"
    assert st._norm_day("") == ""
    assert st._norm_day(None) == ""
    assert st._norm_day("not-a-date") == ""


def test_expected_last_trade_day(monkeypatch):
    st = _mod()

    def _fake_bj(ymd, hm):
        y, m, d = ymd
        return lambda: (types.SimpleNamespace(tm_year=y, tm_mon=m, tm_mday=d), hm)

    # 周一 09:00(未收盘) → 当日日K未定型, 期望上一工作日 = 周五
    monkeypatch.setattr(st, "_bj", _fake_bj((2026, 9, 14), 9 * 60))
    assert st._expected_last_trade_day() == "2026-09-11"
    # 周一 18:00(已收盘) → 期望当日
    monkeypatch.setattr(st, "_bj", _fake_bj((2026, 9, 14), 18 * 60))
    assert st._expected_last_trade_day() == "2026-09-14"
    # 周六 20:00 → 回退到周五
    monkeypatch.setattr(st, "_bj", _fake_bj((2026, 9, 12), 20 * 60))
    assert st._expected_last_trade_day() == "2026-09-11"
    # 周日 08:00 → 回退到周五
    monkeypatch.setattr(st, "_bj", _fake_bj((2026, 9, 13), 8 * 60))
    assert st._expected_last_trade_day() == "2026-09-11"


def test_kline_last_day():
    st = _mod()
    assert st._kline_last_day(json.dumps({"time": ["2026-09-09", "2026-09-11"]})) == "2026-09-11"
    assert st._kline_last_day(json.dumps({"time": []})) == ""
    assert st._kline_last_day(json.dumps({})) == ""
    assert st._kline_last_day("不是JSON") == ""


def test_kline_stale_cache_refetches(monkeypatch):
    """陈旧缓存(末根 8/21 < 期望 9/11)必须回源, 并把新数据写回缓存"""
    st = _mod()
    old = json.dumps({"time": ["2026-08-20", "2026-08-21"],
                      "open": [1, 2], "close": [1, 2], "high": [1, 2], "low": [1, 2]})
    conn = _FakeConn(row=(old,))
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    monkeypatch.setattr(st, "_expected_last_trade_day", lambda: "2026-09-11")
    called = {}

    def fake_fetch(code, period):
        called["code"] = code
        return {"time": ["2026-09-11"], "open": [3], "close": [3], "high": [3], "low": [3]}

    monkeypatch.setattr(st.fetcher, "fetch_stock_chart_robust", fake_fetch)
    out = st._kline("000001")
    assert called.get("code") == "000001", "陈旧缓存应回源"
    assert out["time"] == ["2026-09-11"]
    assert conn.writes, "回源结果应写回 stock_kline"


def test_kline_fresh_cache_no_refetch(monkeypatch):
    """新鲜缓存(末根 = 期望日)不回源 —— 否则 rebuild 会全量打上游"""
    st = _mod()
    fresh = json.dumps({"time": ["2026-09-10", "2026-09-11"],
                        "open": [1, 2], "close": [1, 2], "high": [1, 2], "low": [1, 2]})
    conn = _FakeConn(row=(fresh,))
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    monkeypatch.setattr(st, "_expected_last_trade_day", lambda: "2026-09-11")
    called = {}
    monkeypatch.setattr(st.fetcher, "fetch_stock_chart_robust",
                        lambda c, p: called.setdefault("hit", c))
    out = st._kline("000001")
    assert "hit" not in called, "新鲜缓存不应回源"
    assert conn.writes == [], "不应重复写缓存"
    assert out["time"][-1] == "2026-09-11"


def test_kline_valid_json_without_time_treated_as_fresh(monkeypatch):
    """合法 JSON 但取不出末根日期 → 按新鲜处理, 避免异常形态下全量回源打爆上游。
    (真正损坏的 JSON 会落到回源分支, 那也是正确行为 —— 拿不到可用数据就该重拉)"""
    st = _mod()
    conn = _FakeConn(row=(json.dumps({"foo": 1}),))
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    monkeypatch.setattr(st, "_expected_last_trade_day", lambda: "2026-09-11")
    called = {}
    monkeypatch.setattr(st.fetcher, "fetch_stock_chart_robust",
                        lambda c, p: called.setdefault("hit", c))
    out = st._kline("000001")
    assert "hit" not in called
    assert out == {"foo": 1}


def test_kline_refresh_true_always_refetches(monkeypatch):
    """refresh=True 是显式强制回源, 不受 TTL 影响"""
    st = _mod()
    conn = _FakeConn(row=(json.dumps({"time": ["2026-09-11"]}),))
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    called = {}

    def fake_fetch(c, p):
        called["hit"] = c
        return {"time": ["2026-09-11"], "open": [1], "close": [1], "high": [1], "low": [1]}

    monkeypatch.setattr(st.fetcher, "fetch_stock_chart_robust", fake_fetch)
    st._kline("000001", refresh=True)
    assert called.get("hit") == "000001"


# ==================== P1-a: 盘后存档时刻 + 成功后置位 ====================
def test_backfill_at_moved_to_evening():
    """采集时刻必须晚于上游(选股宝 flash 池)发布时刻 —— 15:30 实测必然为空"""
    st = _mod()
    assert st.BACKFILL_AT >= 18 * 60, "15:10~15:50 早于上游发布, 该窗口从未成功过"


def test_save_day_dropped_dead_force_param():
    """force 从未被函数体引用, 已删除; 防止有人再加回来"""
    assert "force" not in inspect.signature(_mod().save_day).parameters


def test_daily_task_sets_fired_only_on_success(monkeypatch):
    st = _mod()
    st._fired, st._backoff, st._running = None, 0, True     # 首次失败
    monkeypatch.setattr(st, "save_day", lambda d: 0)     # 上游未发布

    def _no_rebuild(*a, **k):
        pytest.fail("落库失败时不应重建画像")

    monkeypatch.setattr(st, "rebuild_profiles", _no_rebuild)
    st._daily_task("2026-09-11")
    assert st._fired is None, "失败不得置位(旧逻辑起线程即置位 → 窗口内不再重试)"
    assert st._backoff == st._BACKOFF0
    assert st._running is False, "必须复位, 否则后续窗口永远无法重入"


def test_daily_task_success_sets_fired_and_resets_backoff(monkeypatch):
    st = _mod()
    st._fired, st._backoff, st._running = None, 600, True
    marks = {}
    monkeypatch.setattr(st, "save_day", lambda d: 59)
    monkeypatch.setattr(st, "rebuild_profiles", lambda *a, **k: marks.setdefault("rb", 1))
    st._daily_task("2026-09-11")
    assert st._fired == "2026-09-11"
    assert st._backoff == 0
    assert marks.get("rb") == 1
    assert st._running is False


def test_daily_task_backoff_capped(monkeypatch):
    """退避必须封顶, 否则失败次数多时下次尝试会落到窗口外"""
    st = _mod()
    st._fired, st._backoff, st._running = None, st._BACKOFF_MAX, True
    monkeypatch.setattr(st, "save_day", lambda d: 0)
    monkeypatch.setattr(st, "rebuild_profiles", lambda *a, **k: None)
    st._daily_task("2026-09-11")
    assert st._backoff == st._BACKOFF_MAX


# ==================== P1-b: 盘前补救 ====================
def test_rescue_missing_fills_gaps(monkeypatch):
    st = _mod()
    days_seen = {}

    def fake_rows(table, day):
        if table not in ("limit_history", "lhb_history"):
            return -1
        return 0                                  # 全部缺失

    monkeypatch.setattr(st, "_day_rows", fake_rows)
    monkeypatch.setattr(st, "save_day", lambda d: 30)
    monkeypatch.setattr(st, "_bj_date", lambda: "2026-09-14")     # 周一
    conn = _FakeConn(count=0)
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    monkeypatch.setattr(st.kpl, "fetch_lhb", lambda d: [{"code": "000001"}])
    fixed = st._rescue_missing(days=7)
    assert "lhb:2026-09-11" in fixed, "周一应回溯补周五的龙虎榜"
    assert any(x.startswith("limit:") for x in fixed)
    # 周末(9/12 六, 9/13 日)不应出现
    assert not any(("2026-09-12" in x or "2026-09-13" in x) for x in fixed)
    assert any("lhb_history (date, list, ts)" in s for s, _ in conn.writes)


def test_rescue_missing_skips_when_data_exists(monkeypatch):
    """数据齐全时不应重复写库(幂等 + 不打扰上游)"""
    st = _mod()
    monkeypatch.setattr(st, "_day_rows", lambda t, d: 40)
    monkeypatch.setattr(st, "_bj_date", lambda: "2026-09-14")
    conn = _FakeConn(count=40)
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    monkeypatch.setattr(st.kpl, "fetch_lhb", lambda d: pytest.fail("数据齐全不应请求上游"))
    assert st._rescue_missing(days=7) == []
    assert conn.writes == []


def test_rescue_missing_skips_on_count_error(monkeypatch):
    """计数异常(-1)必须跳过, 否则会把查询失败误判成"数据缺失"而空写"""
    st = _mod()
    monkeypatch.setattr(st, "_day_rows", lambda t, d: -1)
    monkeypatch.setattr(st, "_bj_date", lambda: "2026-09-14")
    conn = _FakeConn(count=-1)
    monkeypatch.setattr(st.database, "get_conn", lambda: conn)
    assert st._rescue_missing(days=3) == []
    assert conn.writes == []


def test_day_rows_whitelist():
    """表名走白名单, 防止拼串注入"""
    assert _mod()._day_rows("users", "2026-09-11") == -1


# ==================== 调度窗口触发 ====================
def _run_one_loop(st, monkeypatch, wday, hm, date, next_try=0):
    """跑 _scheduler_loop 一轮后打断, 返回被启动的线程名列表"""
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            self.target, self.args, self.name = target, args, name

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(st.threading, "Thread", FakeThread)
    monkeypatch.setattr(st, "_bj", lambda: (types.SimpleNamespace(tm_wday=wday), hm))
    monkeypatch.setattr(st, "_bj_date", lambda g=None: date)

    def stop(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(st.time, "sleep", stop)
    st._fired, st._rescued, st._running, st._next_try = None, None, False, next_try
    try:
        st._scheduler_loop()
    except KeyboardInterrupt:
        pass
    return started


def test_scheduler_fires_rescue_at_0900(monkeypatch):
    st = _mod()
    started = _run_one_loop(st, monkeypatch, wday=0, hm=9 * 60 + 2, date="2026-09-14")
    assert "stock-temper-rescue" in started
    assert st._rescued == "2026-09-14"
    assert "stock-temper-daily" not in started, "09:02 不在盘后窗口"


def test_scheduler_fires_daily_in_evening_window(monkeypatch):
    st = _mod()
    started = _run_one_loop(st, monkeypatch, wday=0, hm=18 * 60 + 30, date="2026-09-14")
    assert "stock-temper-daily" in started
    assert st._running is True, "起线程后必须置 running 防重入"
    assert "stock-temper-rescue" not in started, "18:30 不在盘前窗口"


def test_scheduler_silent_on_weekend(monkeypatch):
    st = _mod()
    started = _run_one_loop(st, monkeypatch, wday=5, hm=9 * 60 + 2, date="2026-09-12")
    assert started == []
    assert st._rescued is None


def test_scheduler_skips_daily_when_next_try_in_future(monkeypatch):
    """退避期内不应重复起线程"""
    import time as _t
    st = _mod()
    started = _run_one_loop(st, monkeypatch, wday=0, hm=18 * 60 + 30, date="2026-09-14",
                            next_try=_t.time() + 3600)
    assert "stock-temper-daily" not in started


# ==================== auction_snapshot: 龙虎榜挪到晚间窗口 ====================
def test_lhb_moved_to_evening_window():
    """结构性守卫: 龙虎榜写入必须在 18:30 窗口内, 且带失败回滚。

    背景: 龙虎榜盘后公布(通常 18:00 后), 旧逻辑放 15:30 采必然为空;
    且 setnx 占锁后判空不释放 → 一次空就当天废弃(生产实证从未成功落库)。
    """
    from app.services import auction_snapshot as asnap
    src = inspect.getsource(asnap._scheduler_loop)
    i_1530 = src.index("15 * 60 + 30 <= hm <= 15 * 60 + 35")
    i_1830 = src.index("18 * 60 + 30 <= hm <= 18 * 60 + 40")
    i_lhb = src.index("lhb_history (date, list, ts)")
    assert i_lhb > i_1830 > i_1530, "龙虎榜写入必须在 18:30 窗口之后"
    assert 'store.delete("sched:done:lhb_" + date)' in src, "缺失败回滚 → 窗口内不会重试"
