# -*- coding: utf-8 -*-
"""bid_strength._fill_snapshot 量比层换源测试(2026-09-20 主人拍板)

验证「竞昨量比」从本地自算(今额/昨额)切换为猫爪官方成品(daily_auc.auc_to_pre_auc_vol_ratio
落库 snapshot_bid.auc_pre_vol_ratio)后的取数优先级:
  ① 官方值 > 0 → 直接用官方值(不自算覆盖)
  ② 官方值缺失/0 → 回退自算(历史老行兼容)
  ③ 老库无 auc_pre_vol_ratio 列 → 回退自算(PRAGMA 探测降级)
"""
import sqlite3

import pytest

from app.db import database
from app.services import bid_strength as bs


def _build_conn(with_pre_vol=True):
    """内存 snapshot_bid 表(最小列集, 与 _fill_snapshot SELECT 对齐)"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    ddl = ("CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
           "bid_change REAL, bid_amt REAL, ts INTEGER, name TEXT, bid_buy_amt REAL, "
           "float_mv REAL, free_mv REAL, auc_main_net REAL")
    if with_pre_vol:
        ddl += ", auc_pre_vol_ratio REAL"
    ddl += ")"
    cur.execute(ddl)
    return conn


def _seed(conn, date, code, bid_amt, pre_vol):
    """插一条 9_25 快照(bid_amt 单位万元; pre_vol 官方竞昨量比, 可为 None=无官方值)"""
    if pre_vol is None:
        conn.execute(
            "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
            "bid_buy_amt,float_mv,free_mv,auc_main_net) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (date, "9_25", code, 1.0, bid_amt, 0, "x", 0.0, 1e10, 1e10, 0.0))
    else:
        conn.execute(
            "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
            "bid_buy_amt,float_mv,free_mv,auc_main_net,auc_pre_vol_ratio) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, "9_25", code, 1.0, bid_amt, 0, "x", 0.0, 1e10, 1e10, 0.0, pre_vol))


def _run(monkeypatch, conn, date):
    monkeypatch.setattr(database, "get_conn", lambda: conn)
    out = {}
    ret_date = bs._fill_snapshot(out, None, date)
    return out, ret_date


def test_official_pre_vol_ratio_wins(monkeypatch):
    """① 官方竞昨量比 > 0 → 直接用官方值, 不自算(即使自算值不同)"""
    conn = _build_conn(True)
    _seed(conn, "2026-09-18", "600001", bid_amt=100, pre_vol=3.0)   # 官方 3.0
    _seed(conn, "2026-09-17", "600001", bid_amt=50, pre_vol=None)   # 自算=2.0
    conn.commit()
    out, date = _run(monkeypatch, conn, "2026-09-18")
    assert date == "2026-09-18"
    assert out["600001"].bid_vol_ratio == 3.0          # 官方优先, 不自算覆盖


def test_fallback_to_self_compute_when_no_official(monkeypatch):
    """② 官方值缺失(0) → 回退自算(今额/昨额)"""
    conn = _build_conn(True)
    _seed(conn, "2026-09-18", "600001", bid_amt=100, pre_vol=0.0)   # 无官方值
    _seed(conn, "2026-09-17", "600001", bid_amt=200, pre_vol=None)  # 自算=0.5(昨额≥100万)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 0.5          # 自算兜底


def test_fallback_when_column_missing(monkeypatch):
    """③ 老库无 auc_pre_vol_ratio 列 → PRAGMA 探测降级, 回退自算不报错"""
    conn = _build_conn(False)                          # 无官方列
    _seed(conn, "2026-09-18", "600001", bid_amt=100, pre_vol=None)
    _seed(conn, "2026-09-17", "600001", bid_amt=200, pre_vol=None)  # 自算=0.5
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 0.5          # 自算兜底


def test_official_value_immune_to_yday_distortion(monkeypatch):
    """④ 官方值不受「昨额<100万失真」影响: 昨额极小但官方有值 → 仍用官方"""
    conn = _build_conn(True)
    _seed(conn, "2026-09-18", "600001", bid_amt=100, pre_vol=1.03)   # 官方 1.03
    # 昨日竞价额仅 1 万(自算会失真爆炸=100倍), 但官方值正常
    _seed(conn, "2026-09-17", "600001", bid_amt=1.0, pre_vol=None)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 1.03         # 官方优先, 不自算失真
