# -*- coding: utf-8 -*-
"""bid_strength._fill_snapshot 量比层自算测试(2026-09-20 回退纯自算)

验证「竞昨量比」纯自算(今额/昨额, 昨额<100万过滤)：
  ① 今 bid_amt ÷ 昨 bid_amt(万元相除, 等价于猫爪 auc_amt 元相除)
  ② 昨额 < 100万 → 量比失真, 判不可用(None 走 default)
  ③ 无昨日快照 → 量比不可用(None)
  ④ 今额 = 0 → 量比不可用(None)

背景: 2026-09-20 曾换源猫爪官方成品(daily_auc.auc_to_pre_auc_vol_ratio),
回测/口径实证发现该字段不可靠(r≈0.19/max691倍) → 回退纯自算。
"""
import sqlite3

import pytest

from app.db import database
from app.services import bid_strength as bs


def _build_conn():
    """内存 snapshot_bid 表(最小列集, 与 _fill_snapshot SELECT 对齐)"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
        "bid_change REAL, bid_amt REAL, ts INTEGER, name TEXT, bid_buy_amt REAL, "
        "float_mv REAL, free_mv REAL, auc_main_net REAL)")
    return conn


def _seed(conn, date, code, bid_amt):
    """插一条 9_25 快照(bid_amt 单位万元)"""
    conn.execute(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
        "bid_buy_amt,float_mv,free_mv,auc_main_net) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (date, "9_25", code, 1.0, bid_amt, 0, "x", 0.0, 1e10, 1e10, 0.0))


def _run(monkeypatch, conn, date):
    monkeypatch.setattr(database, "get_conn", lambda: conn)
    out = {}
    ret_date = bs._fill_snapshot(out, None, date)
    return out, ret_date


def test_self_compute_vol_ratio(monkeypatch):
    """① 今额/昨额 自算: 100万 / 200万 = 0.5(昨额≥100万)"""
    conn = _build_conn()
    _seed(conn, "2026-09-18", "600001", bid_amt=100)
    _seed(conn, "2026-09-17", "600001", bid_amt=200)
    conn.commit()
    out, date = _run(monkeypatch, conn, "2026-09-18")
    assert date == "2026-09-18"
    assert out["600001"].bid_vol_ratio == 0.5


def test_yday_below_min_filtered(monkeypatch):
    """② 昨额<100万 → 量比失真, 判不可用(None)"""
    conn = _build_conn()
    _seed(conn, "2026-09-18", "600001", bid_amt=100)
    _seed(conn, "2026-09-17", "600001", bid_amt=1.0)   # 昨额仅 1 万
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio is None


def test_no_yday_snapshot(monkeypatch):
    """③ 无昨日快照 → 量比不可用(None)"""
    conn = _build_conn()
    _seed(conn, "2026-09-18", "600001", bid_amt=100)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio is None


def test_today_amt_zero(monkeypatch):
    """④ 今额=0 → 量比不可用(None)"""
    conn = _build_conn()
    _seed(conn, "2026-09-18", "600001", bid_amt=0)
    _seed(conn, "2026-09-17", "600001", bid_amt=200)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio is None
