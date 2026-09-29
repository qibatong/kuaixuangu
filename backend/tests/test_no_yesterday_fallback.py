# -*- coding: utf-8 -*-
"""主人铁律「如果获取的为零就不要使用昨天的数据」的回归测试。

覆盖 2026-09-29 落地的四处收敛 + 两枪不变式：
1. `_read_auction_fast()`：**交易日**当日为空 ⇒ 返回空，绝不查更早日期；
   非交易日（周末/休市）仍允许对齐到最近交易日。
2. `bid-net` 端点：盘后读库为空时**不再**用上一交易日快照重建榜单。
3. `_resolve_date()` 之外的「三时点榜」解析：交易日请求原样返回（已在
   tests/test_resolve_date_today.py 覆盖同类语义，这里只钉 `_read_auction_fast`）。
4. 「两枪」时刻不变式：第二枪必须落在 [开盘啦出数 09:25:30, 定格 09:26:30] 之间。
"""
import sqlite3
import time

import pytest

from app.api import kpl as ak
from app.services import auction_snapshot as asnap


def _bj(y, m, d, hh=11, mm=0):
    return time.strptime("%04d-%02d-%02d %02d:%02d:00" % (y, m, d, hh, mm), "%Y-%m-%d %H:%M:%S")


class _Row(object):
    def __init__(self, code="600001"):
        self._d = {"code": code, "name": "测试", "bidSealAmt": 1.0}
    def get(self, k, dv=None):
        return self._d.get(k, dv)
    def __setitem__(self, k, v):
        self._d[k] = v
    def __getitem__(self, k):
        return self._d[k]


# ---------------- 1) _read_auction_fast ----------------

def test_fast_path_trade_day_never_looks_back(monkeypatch):
    """交易日当日为空 ⇒ (空, 今天)，且**一次都不去查更早日期**。"""
    monkeypatch.setattr(ak, "_bj_now", lambda: _bj(2026, 9, 29))
    monkeypatch.setattr(ak.kpl, "freeze_day", lambda: "2026-09-29")
    monkeypatch.setattr(ak.kpl, "query_auction_history", lambda d, t: [])
    called = {"n": 0}

    def _ltd(table, day, op="<="):
        called["n"] += 1
        return "2026-09-28"

    monkeypatch.setattr(ak, "_latest_trade_date_in", _ltd)
    lst, d = ak._read_auction_fast("seal")
    assert lst == [] and d == "2026-09-29"
    assert called["n"] == 0, "交易日不得去查更早日期(铁律)"


def test_fast_path_holiday_still_aligns(monkeypatch):
    """非交易日(周日)当日为空 ⇒ 仍对齐到最近交易日 09-24（回看语义允许）。"""
    monkeypatch.setattr(ak, "_bj_now", lambda: _bj(2026, 9, 27))
    monkeypatch.setattr(ak.kpl, "freeze_day", lambda: "2026-09-24")

    def _q(date, tab):
        return [_Row()] if date == "2026-09-24" else []

    monkeypatch.setattr(ak.kpl, "query_auction_history", _q)
    monkeypatch.setattr(ak, "_latest_trade_date_in", lambda table, day, op="<=": "2026-09-24")
    monkeypatch.setattr(ak, "_apply_change_for", lambda lst, d: None)
    lst, d = ak._read_auction_fast("seal")
    assert d == "2026-09-24" and len(lst) == 1


# ---------------- 2) bid-net 不再用昨日快照重建 ----------------

def _dummy(query=""):
    class _U(object):
        pass
    u = _U()
    u.query = query

    class _R(object):
        pass
    r = _R()
    r.url = u
    r.client = _U()
    r.client.host = "127.0.0.1"
    return r


def test_bid_net_no_prev_day_rebuild(monkeypatch):
    """盘后读库为空 ⇒ 返回空名单；绝不调用 bid_net_from_snap(昨天)。"""
    import json
    monkeypatch.setattr(ak, "_is_auction_hours", lambda: False)
    monkeypatch.setattr(ak, "_read_auction_fast", lambda tab: ([], "2026-09-29"))
    monkeypatch.setattr(ak.kpl, "fill_bid_turnover_from_snap", lambda lst, d: lst)
    monkeypatch.setattr(ak.kpl, "fill_bid_amt_from_snap", lambda lst, d: lst)
    monkeypatch.setattr(ak.kpl, "fill_bid_net_from_snap", lambda lst, d: lst)
    monkeypatch.setattr(ak, "_apply_change_for", lambda lst, d: None)
    monkeypatch.setattr(ak.kpl, "apply_board_concept_db", lambda *a, **k: None)
    called = {"n": 0}
    monkeypatch.setattr(ak.kpl, "bid_net_from_snap",
                        lambda d: called.__setitem__("n", called["n"] + 1) or [_Row()])
    resp = ak.api_kpl_bid_net(_dummy(), uid=1)
    body = json.loads(resp.body.decode())
    assert body["list"] == []
    assert called["n"] == 0, "不得再拿上一交易日快照重建净额榜(铁律)"


# ---------------- 4) 两枪时刻不变式 ----------------

def test_two_shot_window_invariants():
    freeze = asnap._BID25_FREEZE_SEC                      # 09:26:30 定格
    shot2 = asnap._BID_HIST2_SEC
    assert shot2 == 9 * 3600 + 25 * 60 + 35               # 09:25:35(开盘啦 09:25:30 出数 + 余量)
    assert shot2 >= 9 * 3600 + 25 * 60 + 30, "第二枪不得早于开盘啦出数时刻 09:25:30"
    assert shot2 < freeze, "第二枪必须早于定格, 否则与猫爪定格抢同一窗口"
    assert asnap._BID_QC_UNTIL_SEC >= shot2, "上界(09:26:30)必须容得下第二枪"


def test_bid_hist_stats_format(monkeypatch):
    """两枪日志统计: 逐 tab 条数 + 净额非 0 数(次日核对第二枪是否拿到原生净额)。"""
    class _FakeKpl(object):
        def query_auction_history(self, date, tab):
            if tab == "seal":
                return [{"code": "600001", "bidNetAmt": 5}, {"code": "600002", "bidNetAmt": 0}]
            return []
    s = asnap._bid_hist_stats(_FakeKpl(), "2026-09-29")
    assert "seal=2(净额非0 1)" in s and "bid_net=0(净额非0 0)" in s
