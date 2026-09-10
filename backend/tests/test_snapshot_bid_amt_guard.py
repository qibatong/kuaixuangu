# -*- coding: utf-8 -*-
"""9_20/9_24 竞价额质量门 + 单位防御(2026-09-10)

生产实况: 9_20/9_24 时点竞价额覆盖率长期崩塌(9/4、9/7 为 0%, 9/9 为 1.4%,
9/10 为 0.7%), 而 9_25 定格稳定 92-93%。根因是竞价窗口内东财被限流 → 回退腾讯兜底,
腾讯无真实竞价额。两个后果:
  ① INSERT OR REPLACE 把窗口内上一轮的好数据冲成 0
  ② KPL 补位曾按"元"存入万元列 → 放大 1e4 倍(9/9 9_20 中位 5,775,000"万元")
"""
import pytest

from app.services import auction_snapshot as A


def _mk(n=1200, amt=0.0, chg=5.0):
    """构造全市场快照 map: 默认全部无竞价额、有涨幅(模拟腾讯兜底态)"""
    return {"%06d" % i: {"bid_change": chg, "bid_amt": amt, "name": "n%d" % i,
                         "bid_buy_amt": 0, "float_mv": 5e9, "free_mv": 3e9, "board": ""}
            for i in range(n)}


# ============================================================
# ① 单位防御 _fix_bid_amt_unit
# ============================================================

def test_fix_unit_converts_yuan_to_wan():
    """元当万元存储(放大 1e4) → 自动换算回万元"""
    raw = {"600000": {"bid_amt": 5_775_000.0}}     # 实为 577.5 万元
    n_fix, n_zero = A._fix_bid_amt_unit(raw)
    assert (n_fix, n_zero) == (1, 0)
    assert raw["600000"]["bid_amt"] == pytest.approx(577.5)


def test_fix_unit_zeroes_absurd_values():
    """换算后仍超上限(历史 4.9e15) → 置 0, 不落脏值"""
    raw = {"600001": {"bid_amt": 4.888e15}}
    n_fix, n_zero = A._fix_bid_amt_unit(raw)
    assert (n_fix, n_zero) == (0, 1)
    assert raw["600001"]["bid_amt"] == 0.0


def test_fix_unit_leaves_normal_untouched():
    """正常值(9_25 定格 P99=4452万, max=5.19亿)不动"""
    raw = {"600002": {"bid_amt": 4452.0}, "600003": {"bid_amt": 51875.0}}
    assert A._fix_bid_amt_unit(raw) == (0, 0)
    assert raw["600002"]["bid_amt"] == 4452.0 and raw["600003"]["bid_amt"] == 51875.0


# ============================================================
# ② 质量门 _guard_bid_amt_missing
# ============================================================

def test_guard_keeps_previous_values_when_source_degraded(monkeypatch):
    """源降级(有涨幅无竞价额) → 保留库内已有正值, 不让 0 冲掉"""
    raw = _mk(n=1200, amt=0.0, chg=5.0)
    prev = {"000000": 123.0, "000001": 456.0}
    monkeypatch.setattr(A.database, "get_conn", lambda: _FakeConn(prev))
    A._guard_bid_amt_missing("2026-09-10", "9_20", raw)
    assert raw["000000"]["bid_amt"] == 123.0
    assert raw["000001"]["bid_amt"] == 456.0
    assert raw["000002"]["bid_amt"] == 0.0      # 库里没有的, 保持 0


def test_guard_noop_when_coverage_healthy(monkeypatch):
    """覆盖率正常(>30%) → 不干预(正常采集不能被历史值污染)"""
    raw = _mk(n=1200, amt=100.0, chg=5.0)
    called = []
    monkeypatch.setattr(A.database, "get_conn",
                        lambda: called.append(1) or _FakeConn({}))
    A._guard_bid_amt_missing("2026-09-10", "9_25", raw)
    assert called == []                           # 根本没读库
    assert all(v["bid_amt"] == 100.0 for v in raw.values())


def test_guard_noop_when_market_data_itself_missing(monkeypatch):
    """连涨幅都没有(整批行情源故障) → 不是"竞价额缺失", 不干预"""
    raw = _mk(n=1200, amt=0.0, chg=0.0)
    monkeypatch.setattr(A.database, "get_conn", lambda: _FakeConn({"000000": 9.9}))
    A._guard_bid_amt_missing("2026-09-10", "9_20", raw)
    assert raw["000000"]["bid_amt"] == 0.0       # 涨幅全 0 → 不判定为源降级


def test_guard_skips_small_batch(monkeypatch):
    """小批量(非全市场, 如秒级采样 600 只) → 不触发全市场口径的门"""
    raw = _mk(n=600, amt=0.0, chg=5.0)
    monkeypatch.setattr(A.database, "get_conn", lambda: _FakeConn({"000000": 9.9}))
    A._guard_bid_amt_missing("2026-09-10", "9_20", raw)
    assert raw["000000"]["bid_amt"] == 0.0


class _FakeConn(object):
    """只实现 _guard_bid_amt_missing 用到的 execute(...).fetchall()"""

    def __init__(self, prev):
        self._prev = prev

    def execute(self, sql, params=()):
        return _FakeCur([(k, v) for k, v in self._prev.items()])

    def close(self):
        pass


class _FakeCur(object):
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows
