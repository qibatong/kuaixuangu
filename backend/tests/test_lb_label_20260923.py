# -*- coding: utf-8 -*-
"""连板高度标签(P0, 2026-09-23 主人拍板)单测。

背景: 竞选出票上打「买入前一日连板高度」标签, 数据源 kpl.real_limit_days(上一交易日)。
口径与取数陷阱见 app/api/stocks.py 里 _fill_lb 的注释(limitBoards 盘中会 +1 板, 不可用)。

本文件覆盖:
  A. _prev_trade_day_before: 有 snapshot_bid 数据 → 取 < 基准日的最近一天
  B. _prev_trade_day_before: 数据里无更早日期 → 按日历跳周末降级
  C. _fill_lb: 正常打标 lb / lbDate; 昨日未涨停的票也写 lb=0(前端据此显示「新启动」)
  D. _fill_lb: 涨停池不可用(空 dict) → **不打标且不改动名单** (独立降级)
  E. _fill_lb: 取数抛异常 → **吞掉**, 名单照常返回 (绝不 500)
  F. _fill_lb: 只**新增** lb/lbDate, 不改动行内既有字段 —— 证明它不参与筛选/排序/评分
  以及基准日显式传入时以该日为准(盘前/回放走上一交易日名单, 标签必须跟着名单日期走)。

⚠️ 本文件**不共用** conftest 的 session 级临时库: 那库里可能有别的测试插入的
   snapshot_bid 日期, 会让「上一交易日」与「日历降级」两条断言随机失败。
   故每个用例都用 tmp_path 建一个只含本用例数据的独立库, 并把 config.DB_FILE 指过去。
"""
import sqlite3

import pytest

from app.api import stocks as S
from app.core import config


@pytest.fixture
def iso_db(tmp_path, monkeypatch):
    """独立空库 + 指过去 config.DB_FILE; 返回「插入日期」的小函数。

    _prev_trade_day_before 内部是 `from ..core import config` 后读 config.DB_FILE,
    故 monkeypatch 模块属性即可生效。
    """
    path = tmp_path / "lb_iso.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_FILE", str(path))

    def insert(*dates):
        conn = sqlite3.connect(str(path))
        conn.executemany(
            "INSERT INTO snapshot_bid (date, time_point, code) VALUES (?,?,?)",
            [(d, "09:25:00", "000001") for d in dates])
        conn.commit()
        conn.close()

    return insert


# ---------------------------------------------------------------- A / B
def test_prev_trade_day_uses_snapshot(iso_db):
    """A: 9/23 的上一交易日应是 9/22 —— snapshot_bid 里 < 9/23 的最近一天。

    这条同时钉住「同花顺涨停池止于 09-18」那类数据缺口: 只要 snapshot_bid 有 9/22,
    就不会被错算成 9/18(2026-09-23 实测踩过, 会让今日名单整列连板数错一档)。
    """
    iso_db("2026-09-15", "2026-09-16", "2026-09-18", "2026-09-22")
    assert S._prev_trade_day_before("2026-09-23") == "2026-09-22"
    # 基准日落在周末(9/19-20)之后的首个交易日 9/21: 上一交易日仍是 9/18
    assert S._prev_trade_day_before("2026-09-21") == "2026-09-18"


def test_prev_trade_day_calendar_fallback(iso_db):
    """B: 数据里没有比基准日更早的日期 → 日历降级(跳周末), 仍返回可用日期。"""
    iso_db("2026-09-15")                 # 库里只有基准日当天, < 它的为空
    assert S._prev_trade_day_before("2026-09-15") == "2026-09-14"   # 9/14 是周一
    # 9/14 的前一天 9/13 是周日 → 必须跳到 9/11(周五)
    assert S._prev_trade_day_before("2026-09-14") == "2026-09-11"


# ---------------------------------------------------------------- C / F
def _stub(monkeypatch, pool, freeze="2026-09-23"):
    """桩掉涨停池取数 + 名单定格日; 返回记录「实际取数日」的 dict。"""
    monkeypatch.setattr(S, "_freeze_fields",
                        lambda now=None: {"freezeDate": freeze, "freezeIsToday": True})
    called = {}

    def _fake(date):
        called["date"] = date
        return pool

    monkeypatch.setattr(S.kpl, "real_limit_days", _fake)
    return called


def test_fill_lb_marks_rows(monkeypatch, iso_db):
    """C: 池内票写真实连板数, 昨日未涨停的票写 0(不是不写) —— 前端要能区分「0 板」与「未知」。"""
    iso_db("2026-09-18", "2026-09-22")
    called = _stub(monkeypatch, {"600001": 3, "000002": 1})
    lst = [{"code": "600001", "name": "甲"}, {"code": "000002", "name": "乙"},
           {"code": "000003", "name": "丙"}]
    S._fill_lb(lst)
    assert called["date"] == "2026-09-22"           # 基准日 9/23 → 取数日 9/22
    assert [it["lb"] for it in lst] == [3, 1, 0]     # 丙 不在池 → 0
    assert {it["lbDate"] for it in lst} == {"2026-09-22"}


def test_fill_lb_only_adds_two_keys(monkeypatch, iso_db):
    """F: 只新增 lb/lbDate —— 其余字段(含参与筛选/排序的)逐字节不变。
    这是「标签只展示、不参与筛选/排序」的可执行证据。"""
    iso_db("2026-09-22")
    _stub(monkeypatch, {"600001": 2})
    row = {"code": "600001", "name": "甲", "probability": 88, "confidence": 70,
           "bidChange": 5.0, "circulationMV": 42.0, "concept": "AI"}
    before = dict(row)
    S._fill_lb([row])
    assert {k: row[k] for k in before} == before
    assert set(row) - set(before) == {"lb", "lbDate"}


def test_fill_lb_ref_date_overrides(monkeypatch, iso_db):
    """基准日显式传入时以该日为准: 回放 9/18 名单就必须按 9/17 之前打标, 不能用今天。

    (9/17 库里没有 → 上一交易日 = 9/16; 关键是**没有**用到 9/23。)
    """
    iso_db("2026-09-16", "2026-09-18", "2026-09-22")
    called = _stub(monkeypatch, {"600001": 1}, freeze="2026-09-23")
    S._fill_lb([{"code": "600001"}], ref_date="2026-09-18")
    assert called["date"] == "2026-09-16"


# ---------------------------------------------------------------- D / E
def test_fill_lb_empty_pool_leaves_rows_untouched(monkeypatch, iso_db):
    """D: 涨停池为空(东财挂了/取数日非交易日) → 不打标, 且**不能写 lb=0** ——
    否则前端会把「未知」渲染成「新启动」, 那是把故障伪装成结论。"""
    iso_db("2026-09-22")
    _stub(monkeypatch, {})
    lst = [{"code": "600001", "name": "甲"}]
    S._fill_lb(lst)
    assert lst == [{"code": "600001", "name": "甲"}]


def test_fill_lb_swallows_exceptions(monkeypatch, iso_db):
    """E: 取数抛异常必须吞掉 —— 标签是可选展示, 绝不能让它把选股接口打成 500。"""
    iso_db("2026-09-22")
    monkeypatch.setattr(S, "_freeze_fields",
                        lambda now=None: {"freezeDate": "2026-09-23", "freezeIsToday": True})

    def _boom(date):
        raise RuntimeError("东财断连")

    monkeypatch.setattr(S.kpl, "real_limit_days", _boom)
    lst = [{"code": "600001", "name": "甲"}]
    S._fill_lb(lst)                                  # 不抛
    assert lst == [{"code": "600001", "name": "甲"}]


def test_fill_lb_noop_on_empty_list(monkeypatch):
    """空名单不打网不写键(避免无意义请求与日志噪音)。"""
    def _boom(date):
        raise AssertionError("空名单不该取数")

    monkeypatch.setattr(S.kpl, "real_limit_days", _boom)
    S._fill_lb([])
    S._fill_lb(None)


# ---------------------------------------------------------------- 接线
def test_fill_lb_wired_before_spot_fetch():
    """接线检查: _fill_lb 在 _fill_spot_fields **内部、且在行情拉取之前** 被调用。

    顺序很关键 —— 该函数行情失败时有多个提前 return, 放在后面会漏标。
    """
    import inspect
    src = inspect.getsource(S._fill_spot_fields)
    i_lb = src.find("_fill_lb(lst)")
    i_fetch = src.find("fetcher.fetch_spot_quote_map")
    assert i_lb != -1, "_fill_spot_fields 未接线 _fill_lb"
    assert i_fetch != -1 and i_lb < i_fetch, "_fill_lb 必须在行情拉取之前"
