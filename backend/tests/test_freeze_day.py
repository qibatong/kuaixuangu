# -*- coding: utf-8 -*-
"""v4.11.67「定格基准日」(`kpl.freeze_day`) 单测 —— 主人指令「**非交易日数据要定格才行**」。

口径: 非交易日(周末 / 法定休市)整页等价于「把最近一个交易日的**收盘定格画面**冻结下来」——
「今日」= 定格基准日 FD、「昨日」= FD 的前一交易日、行情字段取 FD 的落库/收盘值(不调实时)。

★ 本文件**全部硬编码日期, 不依赖运行时钟**: 2026-09-27 是周日, 任何"今天"假设都会
  在别的日子悄悄变绿/变红(2026-09-27 已因此踩过一次)。
交易日: 2026-09-24(周四) / 2026-09-23(周三)
非交易日: 2026-09-25(中秋·周五·法定休市) / 2026-09-26(周六) / 2026-09-27(周日)
"""
import sqlite3
import sys
import time as _t

sys.path.insert(0, "backend")

from app.core import trade_calendar as tc
from app.services import kpl

THU = "2026-09-24"
WED = "2026-09-23"
HOLIDAY = "2026-09-25"
SAT = "2026-09-26"
SUN = "2026-09-27"


# --------------------------------------------------------------------------- #
# 0. 先钉日历基线(本文件其余断言全部依赖它; 日历表一旦被改这里第一个红)
# --------------------------------------------------------------------------- #
def test_calendar_baseline_20260927():
    assert tc.is_trade_day(THU) is True
    assert tc.is_trade_day(WED) is True
    assert tc.is_holiday(HOLIDAY) is True        # 中秋(周五) 法定休市
    assert tc.is_trade_day(HOLIDAY) is False
    assert tc.is_trade_day(SAT) is False
    assert tc.is_trade_day(SUN) is False


# --------------------------------------------------------------------------- #
# 1. freeze_day: 交易日 → 自己; 非交易日 → 最近交易日
# --------------------------------------------------------------------------- #
def test_freeze_day_on_trade_day_is_itself():
    assert kpl.freeze_day(THU) == THU
    assert kpl.freeze_day(WED) == WED


def test_freeze_day_on_offdays_uses_snapshot(monkeypatch):
    """非交易日先查表里最近的**交易日**快照(带交易日历过滤)。"""
    monkeypatch.setattr(kpl, "_latest_trade_snap_date", lambda *a, **k: THU)
    assert kpl.freeze_day(HOLIDAY) == THU     # 中秋 → 09-24
    assert kpl.freeze_day(SAT) == THU
    assert kpl.freeze_day(SUN) == THU


def test_freeze_day_falls_back_to_calendar(monkeypatch):
    """表里也没数据时降级为纯日历推算(绝不返回空)。"""
    monkeypatch.setattr(kpl, "_latest_trade_snap_date", lambda *a, **k: None)
    assert kpl.freeze_day(HOLIDAY) == THU
    assert kpl.freeze_day(SAT) == THU
    assert kpl.freeze_day(SUN) == THU


def test_freeze_day_never_returns_empty(monkeypatch):
    """日历推算也失败时, 兜底返回入参自己 —— 返回空会把"回退"变成"无数据", 更糟。"""
    monkeypatch.setattr(kpl, "_latest_trade_snap_date", lambda *a, **k: None)
    monkeypatch.setattr(tc, "prev_trade_date", lambda *a, **k: None)
    assert kpl.freeze_day(SUN) == SUN


# --------------------------------------------------------------------------- #
# 2. ★ 核心回归: `_prev_trade_day()` 必须以「定格基准日」为参照
# --------------------------------------------------------------------------- #
def test_prev_trade_day_is_relative_to_freeze_day(monkeypatch):
    """★ 旧实现以**自然日**为参照 ⇒ 非交易日直接返回"最近交易日自己"(09-24) ⇒
    「今炸板」与「昨炸板」显示同一批股票(主人反馈的"没有定格")。
    新实现: 定格日(09-24) 的上一交易日 = 09-23。"""
    monkeypatch.setattr(kpl, "freeze_day", lambda *a, **k: THU)
    monkeypatch.setattr(kpl, "_latest_trade_snap_date", lambda day=None, **k: WED)
    assert kpl._prev_trade_day() == WED
    assert kpl._prev_trade_day() != THU          # 关键: 不得等于定格日自己


def test_prev_trade_day_calendar_fallback(monkeypatch):
    monkeypatch.setattr(kpl, "freeze_day", lambda *a, **k: THU)
    monkeypatch.setattr(kpl, "_latest_trade_snap_date", lambda *a, **k: None)
    assert kpl._prev_trade_day() == WED          # 09-24 的上一交易日 = 09-23(日历推算)


# --------------------------------------------------------------------------- #
# 3. ★ `_snap25_map()`(date 空) 必须取定格日的 9_25 快照
# --------------------------------------------------------------------------- #
def _mk_snap_db(path):
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE snapshot_bid (
        code TEXT, bid_change REAL, bid_amt REAL, name TEXT,
        float_mv REAL, free_mv REAL, board TEXT, date TEXT, time_point TEXT)""")
    for d, code, name in ((THU, "600001", "定格日票"), (WED, "600002", "前一日票")):
        c.execute("INSERT INTO snapshot_bid VALUES (?,?,?,?,?,?,?,?,?)",
                  (code, 3.0, 100.0, name, 1e9, 9e8, "板块", d, "9_25"))
    c.commit()
    c.close()


def test_snap25_map_defaults_to_freeze_day(tmp_path, monkeypatch):
    """★ 旧实现 date 空 = **裸自然日** ⇒ 非交易日查周日 = 0 行 ⇒ 「昨涨停」51 行里
    只有 11 行有行情字段。新实现必须落在定格基准日上。"""
    db = str(tmp_path / "snap.db")
    _mk_snap_db(db)
    monkeypatch.setattr(kpl.config, "DB_FILE", db)
    monkeypatch.setattr(kpl, "freeze_day", lambda *a, **k: THU)

    m = kpl._snap25_map()
    assert set(m.keys()) == {"600001"}                  # 定格日(09-24)
    assert m["600001"]["name"] == "定格日票"
    # 显式传日期仍按传入值(不受定格日影响)
    assert set(kpl._snap25_map(WED).keys()) == {"600002"}


# --------------------------------------------------------------------------- #
# 4. ★ `_seal_map(date)` 走落库快照, 非交易日不得打实时接口
# --------------------------------------------------------------------------- #
def test_seal_map_with_date_reads_frozen_snapshot(monkeypatch):
    live = {"n": 0}

    def fake_live():
        live["n"] += 1
        return [{"code": "999999", "bidAmt": 1}]

    def fake_hist(d, tab):
        assert tab == "seal"
        return [{"code": "600001", "bidAmt": 2}] if d == THU else []

    monkeypatch.setattr(kpl, "fetch_bid_seal", fake_live)
    monkeypatch.setattr(kpl, "query_auction_history", fake_hist)

    m = kpl._seal_map(THU)
    assert set(m.keys()) == {"600001"}
    assert live["n"] == 0          # ★ 定格/回看口径不得调开盘啦实时接口
    # date 为空 = 实时口径, 才允许打实时
    assert set(kpl._seal_map().keys()) == {"999999"}
    assert live["n"] == 1


# --------------------------------------------------------------------------- #
# 5. 竞价/盘中时段判据必须带交易日门禁(法定休市日 ≠ 交易日)
# --------------------------------------------------------------------------- #
def test_is_auction_hours_and_intraday_exclude_holiday(monkeypatch):
    """2026-09-25(中秋·**周五**) 的 9:15-9:30 不是竞价时段、10:00 不是盘中 ——
    旧判据只看 `tm_wday < 5` ⇒ 休市日照样走实时分支 ⇒ 页面不定格。"""
    from app.api import kpl as kpl_api

    g_hol = _t.struct_time((2026, 9, 25, 9, 25, 0, 4, 267, 0))    # 周五 09:25 但是休市
    monkeypatch.setattr(_t, "gmtime", lambda *a, **k: g_hol)
    assert kpl_api._is_auction_hours() is False
    g_hol2 = _t.struct_time((2026, 9, 25, 10, 0, 0, 4, 267, 0))
    monkeypatch.setattr(_t, "gmtime", lambda *a, **k: g_hol2)
    assert kpl_api._is_intraday() is False

    g_ok = _t.struct_time((2026, 9, 24, 9, 25, 0, 3, 267, 0))     # 09-24 真交易日
    monkeypatch.setattr(_t, "gmtime", lambda *a, **k: g_ok)
    assert kpl_api._is_auction_hours() is True


# --------------------------------------------------------------------------- #
# 6. ★ API: `/api/kpl/broken?date=D&day=yesterday` = 「D 这一天的昨炸板」
# --------------------------------------------------------------------------- #
def test_api_broken_date_plus_yesterday_uses_prev_pool(client, first_user, monkeypatch):
    """★ 旧行为: 只要带 date 就走"当日炸板"分支 ⇒ 非交易日「今炸板」≡「昨炸板」
    (前端 kplBroken(dt ? '' : 'yesterday', dt) 会把 day 丢掉)。"""
    from app.api import deps
    from app.api import kpl as kpl_api

    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1
    token, _, _ = first_user

    def fake_q(d, tab):
        assert tab == "broken_today"
        if d == WED:
            return [{"code": "600001", "name": "昨日炸板票", "day": WED}]
        if d == THU:
            return [{"code": "600002", "name": "当日炸板票", "day": THU}]
        return []

    monkeypatch.setattr(kpl_api, "_resolve_date", lambda d: d)
    monkeypatch.setattr(kpl_api.kpl, "query_auction_history", fake_q)
    monkeypatch.setattr(kpl_api.kpl, "_latest_trade_snap_date", lambda *a, **k: WED)
    monkeypatch.setattr(kpl_api.kpl, "_merge_broken_bid_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_float_mv_from_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_close_change_from_kline", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    h = {"Authorization": "Bearer " + token}
    # 昨炸板: 池 = 定格日(09-24)的前一交易日(09-23), 字段/日期 = 定格日 09-24
    r = client.get("/api/kpl/broken?date=%s&day=yesterday" % THU, headers=h)
    d = r.json()
    assert r.status_code == 200 and d["ok"], r.text
    assert [x["code"] for x in d["list"]] == ["600001"]
    assert d["date"] == THU and d["poolDate"] == WED
    # 今炸板: 池 = 定格日本身
    r2 = client.get("/api/kpl/broken?date=%s" % THU, headers=h)
    d2 = r2.json()
    assert [x["code"] for x in d2["list"]] == ["600002"]
    assert d2["date"] == THU


def test_api_broken_yesterday_falls_back_when_prev_missing(client, first_user, monkeypatch):
    """前一交易日无落库 → 退回"当日炸板"(宁可退化成今炸板, 也不返回空表)。"""
    from app.api import deps
    from app.api import kpl as kpl_api

    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1
    token, _, _ = first_user

    def fake_q(d, tab):
        return [{"code": "600002", "name": "当日炸板票", "day": d}] if d == THU else []

    monkeypatch.setattr(kpl_api, "_resolve_date", lambda d: d)
    monkeypatch.setattr(kpl_api.kpl, "query_auction_history", fake_q)
    monkeypatch.setattr(kpl_api.kpl, "_latest_trade_snap_date", lambda *a, **k: WED)
    monkeypatch.setattr(kpl_api.kpl, "_merge_broken_bid_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_float_mv_from_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_close_change_from_kline", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    r = client.get("/api/kpl/broken?date=%s&day=yesterday" % THU,
                   headers={"Authorization": "Bearer " + token})
    d = r.json()
    assert d["ok"] and [x["code"] for x in d["list"]] == ["600002"]
