# -*- coding: utf-8 -*-
"""P1 全市场预计算 + 物化表回归(2026-09-12)

锁定五条性质, 每条都对着一个"如果不锁就会复发"的坑:

  1. 预计算落库 → 读回往返一致(分数/定格字段不失真)
  2. 幂等: 同日重跑两次, 结果逐票一致(PRIMARY KEY(date,code) + INSERT OR REPLACE)
  3. 行数闸门: 全市场行数不足 → **不落库**(宁可不写, 也不写半张残缺表)
  4. 开关双向: precompute_read=0 走原路径; =1 且物化表有数据 → 走 precompute
  5. 容灾回退: 开关=1 但物化表为空 → 静默回退原路径, 接口不报错(首页不能空白)
  6. 冻结字段不被补丁改写(物化路径下评分与门槛必须用同一套值, 否则名单漂移)

用假日期 + 假代码前缀, 用例后自行清理 —— 测试库 session 共享, 写真数据会污染。
"""
import datetime

import pytest

from app.db import database
from app.services import settings
from app.services.picker import pipeline, precompute
from app.services.picker.contract import QuoteRow
from app.services.picker.sources import base as sb

_DATE = "2099-02-02"          # 假日期
_PREFIX = "PC"                # 假代码前缀

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 300, "bidAmtFloor": 3000,
}


def _code(i):
    return "%s%03d" % (_PREFIX, i)


def _q(code, *, name="某股", bid_change=3.0, bid_amt=5.0e7, mv=55e8,
       prev=10.15, ychg=2.0, industry="半导体", concept="AI"):
    return QuoteRow(code=code, name=name, bid_change=bid_change, bid_amt=bid_amt,
                    float_mv=mv, prev_close=prev, yesterday_change=ychg,
                    industry=industry, concept=concept, source="snapshot")


def _universe(n=3):
    return {_code(i): _q(_code(i), bid_change=3.0 + i * 0.5) for i in range(n)}


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    """清空假数据 + 复位开关 + 放宽行数闸门。

    MIN_ROWS 降到 1: 用例只落几只票, 生产闸门 500 会判"定格数据缺失"直接回退
    (2026-09-12 首轮踩到: 日志"仅 1 行(< 500), 回退原路径")。闸门语义本身由
    test_precompute_min_rows_guard 单独覆盖。
    """
    monkeypatch.setattr(precompute, "MIN_ROWS", 1)
    database.init_db()
    precompute.clear_date(_DATE)
    yield
    precompute.clear_date(_DATE)
    settings.set(precompute.READ_SWITCH, 0)


# ==================== 1. 落库与读回 ====================
def test_precompute_roundtrip():
    """物化 → 读回: 分数与定格字段逐票一致(不失真)"""
    rows = _universe(3)
    st = precompute.precompute_all(_DATE, rows=rows, strengths={}, min_rows=1)
    assert st["ok"], st.get("error")
    assert st["n_score"] == 3

    got_rows, got_scores = precompute.read_materialized(_DATE, min_rows=1)
    assert set(got_rows) == set(rows)
    assert set(got_scores) == set(rows)
    for c, r in rows.items():
        assert got_rows[c].bid_change == r.bid_change
        assert got_rows[c].bid_amt == r.bid_amt
        # ★ 2026-09-20: 物化表 float_mv 列存的是**统一市值 mv**(free 优先, 缺则 float),
        #   读回时回填到 free_mv → 校验去留后的统一口径, 而非原 float_mv 列。
        assert got_rows[c].mv == r.mv
        assert got_rows[c].prev_close == r.prev_close
        assert got_rows[c].yesterday_change == r.yesterday_change
        assert 5 <= got_scores[c].probability <= 95
        assert 55 <= got_scores[c].confidence <= 90


def test_precompute_rank_is_dense_desc_by_probability():
    """rank 按 probability 降序且连续(前端排序与物化表一致)"""
    precompute.precompute_all(_DATE, rows=_universe(4), strengths={}, min_rows=1)
    conn = database.get_conn()
    data = conn.execute(
        "SELECT code, probability, rank FROM stock_score_daily WHERE date=? "
        "ORDER BY rank", (_DATE,)).fetchall()
    conn.close()
    assert [d[2] for d in data] == [1, 2, 3, 4]
    probs = [d[1] for d in data]
    assert probs == sorted(probs, reverse=True)


# ==================== 2. 幂等 ====================
def test_precompute_idempotent():
    """同日重跑两次: 行数与逐票分数完全一致(PRIMARY KEY + INSERT OR REPLACE)"""
    a = precompute.precompute_all(_DATE, rows=_universe(3), strengths={}, min_rows=1)
    conn = database.get_conn()
    first = conn.execute(
        "SELECT code, probability, confidence, rank FROM stock_score_daily "
        "WHERE date=? ORDER BY code", (_DATE,)).fetchall()
    conn.close()

    b = precompute.precompute_all(_DATE, rows=_universe(3), strengths={}, min_rows=1)
    conn = database.get_conn()
    second = conn.execute(
        "SELECT code, probability, confidence, rank FROM stock_score_daily "
        "WHERE date=? ORDER BY code", (_DATE,)).fetchall()
    n = conn.execute("SELECT COUNT(*) FROM stock_score_daily WHERE date=?",
                     (_DATE,)).fetchone()[0]
    conn.close()

    assert a["ok"] and b["ok"]
    assert first == second
    assert n == 3, "重跑后行数不变(REPLACE 而非追加)"


# ==================== 3. 行数闸门 ====================
def test_precompute_min_rows_guard():
    """全市场行数不足 → 不落库(9/11 熔断日只落 132 行的教训: 宁缺半张表)"""
    st = precompute.precompute_all(_DATE, rows=_universe(2), strengths={},
                                   min_rows=500)
    assert not st["ok"] and "不落库" in st["error"]
    got_rows, got_scores = precompute.read_materialized(_DATE, min_rows=1)
    assert not got_rows and not got_scores, "失败时不得留下半张表"


# ==================== 4-5. 开关与容灾 ====================
class _FakeSource(sb.BaseSource):
    def __init__(self, rows=None, fail=False, label="fake_list"):
        self.label = label
        self._rows = rows or {}
        self._fail = fail

    def fetch(self, ctx):
        if self._fail or not self._rows:
            return sb.SourceResult(error="模拟名单源故障", degraded=True)
        return sb.SourceResult(rows=dict(self._rows), requested=len(self._rows))


class _FakePatch(sb.BaseSource):
    """补丁源: 默认只补展示字段; pollute=True 时**故意污染** float_mv/prev_close,
    用于验证物化路径会把它还原回去。

    默认不污染市值是必须的 —— float_mv 参与 floatMvFloor 门槛判定, 一改票就被剔
    (2026-09-12 首轮踩到: 回退用例被自己的桩坑成 mv_floor=1, 假红)。
    """

    def __init__(self, label="fake_patch", fail=False, pollute=False):
        self.label = label
        self._fail = fail
        self.pollute = pollute

    def fetch(self, ctx):
        if self._fail:
            return sb.SourceResult(error="模拟补丁源故障", degraded=True)
        rows = {}
        for c in ctx.codes or []:
            rows[c] = QuoteRow(code=c, name="补丁", price=999.0, real_change=9.9,
                               turnover=5.0, vol_ratio=3.0, vol=1e6,
                               float_mv=9.9e8 if self.pollute else None,
                               prev_close=99.0 if self.pollute else None,
                               source="patch")
        return sb.SourceResult(rows=rows, requested=len(ctx.codes or []))


def _install(monkeypatch, mapping):
    # 必须 patch pipeline 里已导入的名字(from ... import get_source 是值绑定)
    monkeypatch.setattr(pipeline, "get_source", lambda label: mapping.get(label))


def _ctx(**kw):
    return pipeline.PickContext(date=_DATE, markets=["hs", "cyb", "kcb"],
                                zt_codes=set(), **kw)


_NOW = datetime.datetime(2026, 9, 8, 11, 0)      # 盘中 → INTRADAY


def test_switch_off_uses_original_path(monkeypatch):
    """开关关闭(默认) → 行为与 P1 前完全一致(零影响上线)"""
    settings.set(precompute.READ_SWITCH, 0)
    rows = {"600000": _q("600000", name="浦发")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=_NOW)
    assert "precompute" not in res.sources
    assert res.n_universe == 1


def test_switch_on_uses_materialized(monkeypatch):
    """开关开启 + 物化表有数据 → 走 precompute, 名单不空"""
    precompute.precompute_all(_DATE, rows={"600000": _q("600000", name="浦发")},
                              strengths={}, min_rows=1)
    settings.set(precompute.READ_SWITCH, 1)
    _install(monkeypatch, {"snapshot": _FakeSource(fail=True),
                           "eastmoney_realtime": _FakePatch()})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=_NOW)
    assert "precompute" in res.sources, "开关开 + 有数据 → 必须走物化表"
    assert [i["code"] for i in res.items] == ["600000"]


def test_switch_on_but_table_empty_falls_back(monkeypatch):
    """开关开启但物化表为空(批跑失败/冷启动) → 静默回退原路径, 接口不报错"""
    settings.set(precompute.READ_SWITCH, 1)
    rows = {"600000": _q("600000", name="浦发")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=_NOW)
    assert "precompute" not in res.sources
    assert res.ok and [i["code"] for i in res.items] == ["600000"], "回退后名单照出"


def test_switch_on_read_exception_falls_back(monkeypatch):
    """物化表读取抛异常 → 也不能让首页空白(异常一律吞掉回退)"""
    settings.set(precompute.READ_SWITCH, 1)

    def _boom(*a, **kw):
        raise RuntimeError("模拟物化表读取崩溃")

    monkeypatch.setattr(precompute, "read_materialized", _boom)
    rows = {"600000": _q("600000", name="浦发")}
    _install(monkeypatch, {"snapshot": _FakeSource(rows),
                           "eastmoney_realtime": _FakePatch()})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=_NOW)
    assert "precompute" not in res.sources
    assert res.ok and res.items


# ==================== 6. 冻结字段不被补丁改写 ====================
def test_materialized_path_freezes_gate_fields(monkeypatch):
    """物化路径: 补丁只能补展示字段, 不得改写参与评分/门槛的定格字段。

    否则会出现"评分用物化值、门槛用补丁值"的两套口径 → 名单随行情源可用性漂移,
    这正是预计算要消灭的问题。
    """
    precompute.precompute_all(
        _DATE,
        rows={"600000": _q("600000", name="浦发", mv=55e8, prev=10.15)},
        strengths={}, min_rows=1)
    settings.set(precompute.READ_SWITCH, 1)
    _install(monkeypatch, {"snapshot": _FakeSource(fail=True),
                           "eastmoney_realtime": _FakePatch(pollute=True)})
    res = pipeline.run(dict(FULL), ctx=_ctx(), now=_NOW)
    assert res.items, "名单应产出"
    row = res.rows["600000"]
    assert row.float_mv == 55e8, "市值不得被补丁的 9.9e8 改写"
    assert row.prev_close == 10.15, "昨收不得被补丁的 99.0 改写"
    # 展示字段仍应来自补丁(补丁的价值就在这里)
    it = res.items[0]
    assert it["price"] == 999.0 and it["turnover"] == 5.0


def test_materialized_path_does_not_exempt_score_floor(monkeypatch):
    """物化路径下补丁失败**不豁免** scoreFloor: 评分来自预计算, 并未失真。

    (原路径补丁失败时评分是占位分, 必须豁免 —— 见 pipeline 注释; 物化路径不适用)
    """
    precompute.precompute_all(
        _DATE,
        rows={"600000": _q("600000", name="浦发", bid_change=0.1, bid_amt=3.1e7)},
        strengths={}, min_rows=1)
    settings.set(precompute.READ_SWITCH, 1)
    _install(monkeypatch, {"snapshot": _FakeSource(fail=True),
                           "eastmoney_realtime": _FakePatch(fail=True),
                           "tencent_point": _FakePatch(fail=True)})
    f = dict(FULL)
    f["scoreFloor"] = 100          # 任何票都够不到的门槛
    res = pipeline.run(f, ctx=_ctx(), now=_NOW)
    assert not res.items, "物化路径评分真实 → scoreFloor 照常生效"
    assert res.stats.get("score_floor") == 1
