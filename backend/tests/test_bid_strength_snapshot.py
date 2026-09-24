# -*- coding: utf-8 -*-
"""bid_strength._fill_snapshot 量比层取数测试

验证「竞价量比」的**标准口径优先 + 旧口径降级回退**两层结构
(2026-09-24 v4.11.40 换标准口径)：
  ① 标准口径优先: snapshot_bid.auc_vol_ratio(猫爪 official 成品,
     定义 = 竞价成交量 ÷ 近 5 日平均每分钟成交量)有值(>0) → 直读该列, 不再自算。
  ② 无列(老库未迁移) 或 该列 = 0(历史行) → 降级回退**纯自算**「今额/昨额」:
       今 bid_amt ÷ 昨 bid_amt(万元相除, 等价于猫爪 auc_amt 元相除);
       昨额 < 100万 → 量比失真, 判不可用(None 走 default)。
  ③ 无昨日快照 / 今额 = 0 → 量比不可用(None)。

背景:
  - 2026-09-20 曾换源猫爪官方成品 daily_auc.auc_to_pre_auc_vol_ratio,
    实证该字段不可靠(与真实竞昨量比 r≈0.19 / max 691 倍) → 当时回退纯自算。
  - 2026-09-24 主人拍板改用**标准量比口径**(= daily_auc.auc_vol_ratio,
    openapi 原文「竞价成交量 ÷ 近 5 日平均每分钟成交量」), 自算降为回退路径。
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


# ---------------------------------------------------------------------------
# 标准口径(2026-09-24 v4.11.40): snapshot_bid.auc_vol_ratio 优先, 自算降级
# ---------------------------------------------------------------------------
def _build_conn_vr():
    """内存 snapshot_bid 表(**含 auc_vol_ratio 列**, 模拟已迁移的新库)"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
        "bid_change REAL, bid_amt REAL, ts INTEGER, name TEXT, bid_buy_amt REAL, "
        "float_mv REAL, free_mv REAL, auc_main_net REAL, auc_vol_ratio REAL)")
    return conn


def _seed_vr(conn, date, code, bid_amt, vol_ratio=0.0):
    """插一条 9_25 快照(bid_amt 万元; vol_ratio = 标准口径竞价量比)"""
    conn.execute(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
        "bid_buy_amt,float_mv,free_mv,auc_main_net,auc_vol_ratio) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (date, "9_25", code, 1.0, bid_amt, 0, "x", 0.0, 1e10, 1e10, 0.0, vol_ratio))


def test_std_vol_ratio_priority(monkeypatch):
    """① 标准口径优先: 列有值 → 直读(本次自算值 100/200=0.5 被忽略)"""
    conn = _build_conn_vr()
    _seed_vr(conn, "2026-09-18", "600001", bid_amt=100, vol_ratio=2.34)
    _seed_vr(conn, "2026-09-17", "600001", bid_amt=200, vol_ratio=1.11)
    conn.commit()
    out, date = _run(monkeypatch, conn, "2026-09-18")
    assert date == "2026-09-18"
    assert out["600001"].bid_vol_ratio == 2.34      # 不是自算的 0.5


def test_std_zero_falls_back_to_self(monkeypatch):
    """② 列存在但为 0(历史行) → 降级回退自算 100/200 = 0.5"""
    conn = _build_conn_vr()
    _seed_vr(conn, "2026-09-18", "600001", bid_amt=100, vol_ratio=0.0)
    _seed_vr(conn, "2026-09-17", "600001", bid_amt=200, vol_ratio=0.0)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 0.5


def test_old_db_without_vr_column(monkeypatch):
    """③ 老库无 auc_vol_ratio 列 → 不报错, 且照常走自算"""
    conn = _build_conn()                            # 无 auc_vol_ratio 列
    _seed(conn, "2026-09-18", "600001", bid_amt=100)
    _seed(conn, "2026-09-17", "600001", bid_amt=200)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 0.5
