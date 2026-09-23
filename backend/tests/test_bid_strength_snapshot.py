# -*- coding: utf-8 -*-
"""bid_strength._fill_snapshot 量比层测试

**现行口径(2026-09-24 v4.11.39 起)**: 竞价量比 = 竞价成交量 ÷ 近 5 日平均每分钟成交量,
取猫爪官方成品列 `snapshot_bid.auc_vol_ratio`(测试 ⑤)。
旧口径「今 9:25 竞价额 ÷ 昨 9:25 竞价额」降级为**回退路径**, 仅在新列无值时生效(测试 ⑥⑦):
  ① 今 bid_amt ÷ 昨 bid_amt(万元相除, 等价于猫爪 auc_amt 元相除)
  ② 昨额 < 100万 → 量比失真, 判不可用(None 走 default)
  ③ 无昨日快照 → 量比不可用(None)
  ④ 今额 = 0 → 量比不可用(None)

历史背景: 2026-09-20 曾换源猫爪官方成品(daily_auc.auc_to_pre_auc_vol_ratio),
回测/口径实证发现该字段不可靠(r≈0.19/max691倍) → 回退纯自算。
那说的是 **auc_to_pre_auc_vol_ratio(竞昨量比)**; 2026-09-24 换的
**auc_vol_ratio(5 日每分钟量比)** 经 66 日实测自洽(反推日均量日间比值中位 0.9911)。
"""
import sqlite3

import pytest

from app.db import database
from app.services import bid_strength as bs


def _build_conn():
    """内存 snapshot_bid 表(最小列集, 与 _fill_snapshot SELECT 对齐; 无 auc_vol_ratio = 未迁移老库)"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
        "bid_change REAL, bid_amt REAL, ts INTEGER, name TEXT, bid_buy_amt REAL, "
        "float_mv REAL, free_mv REAL, auc_main_net REAL)")
    return conn


def _build_conn_vr():
    """含 auc_vol_ratio 列(2026-09-24 新增列)的 snapshot_bid"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
        "bid_change REAL, bid_amt REAL, ts INTEGER, name TEXT, bid_buy_amt REAL, "
        "float_mv REAL, free_mv REAL, auc_main_net REAL, auc_vol_ratio REAL)")
    return conn


def _seed(conn, date, code, bid_amt):
    """插一条 9_25 快照(bid_amt 单位万元)"""
    conn.execute(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
        "bid_buy_amt,float_mv,free_mv,auc_main_net) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (date, "9_25", code, 1.0, bid_amt, 0, "x", 0.0, 1e10, 1e10, 0.0))


def _seed_vr(conn, date, code, bid_amt, vr=0.0):
    """插一条 9_25 快照, 带标准口径量比 auc_vol_ratio(vr=0 表示无值)"""
    conn.execute(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
        "bid_buy_amt,float_mv,free_mv,auc_main_net,auc_vol_ratio) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (date, "9_25", code, 1.0, bid_amt, 0, "x", 0.0, 1e10, 1e10, 0.0, vr))


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


# ------------------------------------------------ 2026-09-24 v4.11.39 换口径
def test_std_vol_ratio_priority(monkeypatch):
    """⑤ 有 auc_vol_ratio(标准口径: 竞价量 ÷ 近5日每分钟量) → **优先**采用, 不再自算。"""
    conn = _build_conn_vr()
    _seed_vr(conn, "2026-09-18", "600001", bid_amt=100, vr=7.25)
    _seed_vr(conn, "2026-09-17", "600001", bid_amt=200)   # 旧口径会给 0.5, 不应被采用
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 7.25


def test_std_vol_ratio_missing_falls_back(monkeypatch):
    """⑥ 新列为 0(历史行/采集缺) → 回退旧口径「今额/昨额」(换口径当天不断层)。"""
    conn = _build_conn_vr()
    _seed_vr(conn, "2026-09-18", "600001", bid_amt=100, vr=0)
    _seed_vr(conn, "2026-09-17", "600001", bid_amt=200)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 0.5


def test_old_db_without_new_column(monkeypatch):
    """⑦ 未迁移老库(无 auc_vol_ratio 列) → SELECT 不报错, 走回退路径。"""
    conn = _build_conn()
    _seed(conn, "2026-09-18", "600001", bid_amt=200)
    _seed(conn, "2026-09-17", "600001", bid_amt=100)   # 昨额=100万(门槛值, 不过滤)
    conn.commit()
    out, _ = _run(monkeypatch, conn, "2026-09-18")
    assert out["600001"].bid_vol_ratio == 2.0
