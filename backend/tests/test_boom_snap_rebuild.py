# -*- coding: utf-8 -*-
"""
竞价爆量「快照重建」用例 (2026-09-05 修复「周末/历史回看无数据」)
=============================================================
背景: 用户反馈「竞价爆量」tab 在非交易时段(周末/节假日/盘后)显示为空, 而委买/净额/抢筹
      等 tab 均能正常显示最近交易日数据。
根因: ① auction_daily_history 表长期只有 09-01 前的 'boom' 落库, 后续日期无 boom 行,
      前端非交易日自动回退带 date 走 query_auction_history('boom') 读到空;
      ② 实时加载器依赖「今天」的 snapshot_bid, 非交易日今天无快照 → 直接返回 []。
修复: 抽出 `_boom_from_snap(snap_date)` —— 从 snapshot_bid 重建量比榜, 供实时加载器回退
      与历史回看/非交易日重建复用。

过滤口径(与 fetch_bid_boom 一致):
  竞价量比 > 2 且 竞价成交额 > 100万(万元=100) 且 竞价涨幅 ≥ 0.01%
返回按 bidRatioYest 降序; bidAmt/yestBidAmt 为**元**(内部万元×10000)。

本文件覆盖: 空数据兜底 / 三条过滤规则 / 时点取最新 / 排序 / 金额单位 / 历史接口重建。
"""
import sqlite3

import pytest

from app.core import config
from app.services import kpl

# 测试专用日期(远离真实交易日, 避免与其他用例数据互相污染)
D_YEST = "2099-01-01"
D_TODAY = "2099-01-02"


@pytest.fixture(autouse=True)
def _cleanup_test_snapshots():
    """用例前后清理测试写入的 snapshot_bid 行(只删测试专用日期)"""
    def _del():
        conn = sqlite3.connect(config.DB_FILE)
        try:
            conn.execute("DELETE FROM snapshot_bid WHERE date IN (?,?)", (D_YEST, D_TODAY))
            conn.commit()
        finally:
            conn.close()
    _del()
    yield
    _del()


def _ensure_free_mv(conn):
    """测试库可能没有 free_mv 列(生产已 ALTER); 缺失则补列, 保证与生产口径一致"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(snapshot_bid)")]
    if "free_mv" not in cols:
        conn.execute("ALTER TABLE snapshot_bid ADD COLUMN free_mv REAL")
        conn.commit()


def _snap(conn, date, tp, code, bid_amt, chg=3.0, fmv=1e10, name="测试股", board="概念A",
          bid_buy_amt=0.0):
    """插一条 snapshot_bid(单位: bid_amt 万元, fmv 元)"""
    conn.execute(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
        "bid_buy_amt,float_mv,board,free_mv) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (date, tp, code, chg, bid_amt, 0, name, bid_buy_amt, fmv, board, fmv))
    conn.commit()


def _open():
    conn = sqlite3.connect(config.DB_FILE)
    _ensure_free_mv(conn)
    return conn


# ---------- ① 空数据兜底 ----------

def test_boom_from_snap_no_today_snapshot_returns_empty():
    """当日无 snapshot_bid → []（不该抛异常, 非交易日调历史日期时常见）"""
    conn = _open()
    try:
        _snap(conn, D_YEST, "9_25", "600000", 1000.0)   # 只有昨日
    finally:
        conn.close()
    assert kpl._boom_from_snap(D_TODAY, spot_map={}) == []


def test_boom_from_snap_no_yesterday_returns_empty():
    """有当日快照但无更早交易日 9_25 → []（无法算量比）"""
    conn = _open()
    try:
        _snap(conn, D_TODAY, "9_25", "600000", 1000.0)
    finally:
        conn.close()
    assert kpl._boom_from_snap(D_TODAY, spot_map={}) == []


# ---------- ② 过滤口径（三条规则） ----------

def test_boom_from_snap_filters_and_units():
    """P0: 量比>2 / 竞额>100万 / 涨幅≥0.01% 三条规则 + 金额万元→元 + 按量比降序"""
    conn = _open()
    try:
        # 昨日 9_25 竞价额(万元): 统一 100 → 量比 = 今日/100
        _snap(conn, D_YEST, "9_25", "600001", 100.0)
        _snap(conn, D_YEST, "9_25", "600002", 100.0)
        _snap(conn, D_YEST, "9_25", "600003", 100.0)
        _snap(conn, D_YEST, "9_25", "600004", 100.0)
        # 当日 9_25
        _snap(conn, D_TODAY, "9_25", "600001", 50.0, chg=3.0)    # 竞额 50万 ≤100万 → 跳过
        _snap(conn, D_TODAY, "9_25", "600002", 1000.0, chg=0.0)  # 涨幅 0 <0.01% → 跳过
        _snap(conn, D_TODAY, "9_25", "600003", 150.0, chg=3.0)   # 量比 1.5 ≤2 → 跳过
        _snap(conn, D_TODAY, "9_25", "600004", 1000.0, chg=5.0)  # 量比 10 → 保留
    finally:
        conn.close()

    out = kpl._boom_from_snap(D_TODAY, spot_map={})
    codes = [x["code"] for x in out]
    assert codes == ["600004"], f"只应保留 600004, 实际 {codes}"

    it = out[0]
    assert it["bidRatioYest"] == 10.0, "量比 = 今日竞价额 / 昨日竞价额"
    assert it["bidAmt"] == 1000 * 10000, "bidAmt 应转成元(万元×10000)"
    assert it["yestBidAmt"] == 100 * 10000, "yestBidAmt 应转成元"
    assert it["bidChange"] == 5.0
    assert it["realChange"] == 0.0, "spot_map 为空时实时涨幅退化为 0(不报错)"


def test_boom_from_snap_sorted_by_ratio_desc():
    """多条合格 → 按竞价量比降序"""
    conn = _open()
    try:
        for code in ("600001", "600002", "600003"):
            _snap(conn, D_YEST, "9_25", code, 100.0)
        _snap(conn, D_TODAY, "9_25", "600001", 300.0, chg=3.0)    # 量比 3
        _snap(conn, D_TODAY, "9_25", "600002", 900.0, chg=3.0)    # 量比 9
        _snap(conn, D_TODAY, "9_25", "600003", 500.0, chg=3.0)    # 量比 5
    finally:
        conn.close()

    out = kpl._boom_from_snap(D_TODAY, spot_map={})
    assert [x["code"] for x in out] == ["600002", "600003", "600001"]
    assert [x["bidRatioYest"] for x in out] == [9.0, 5.0, 3.0]


# ---------- ③ 时点取最新 ----------

def test_boom_from_snap_uses_latest_time_point():
    """同一天多个时点 → 取字典序最大的(9_15<9_20<9_24<9_25)"""
    conn = _open()
    try:
        _snap(conn, D_YEST, "9_25", "600001", 100.0)
        # 9_20 量比 3(合格), 9_25 量比 8(合格) → 应以 9_25 为准
        _snap(conn, D_TODAY, "9_20", "600001", 300.0, chg=3.0)
        _snap(conn, D_TODAY, "9_25", "600001", 800.0, chg=3.0)
    finally:
        conn.close()

    out = kpl._boom_from_snap(D_TODAY, spot_map={})
    assert len(out) == 1
    assert out[0]["bidRatioYest"] == 8.0, "应取最新时点 9_25 的竞价额"


# ---------- ④ 历史回看接口: 落库为空时重建 ----------

def test_api_bid_boom_history_rebuilds_when_no_history_row(client, first_user, monkeypatch):
    """P0(用户反馈场景): 历史日期走 query_auction_history('boom') 读到空 →
    用当日 snapshot_bid 重建, 而不是返回空列表"""
    conn = _open()
    try:
        _snap(conn, D_YEST, "9_25", "600777", 100.0)
        _snap(conn, D_TODAY, "9_25", "600777", 1000.0, chg=4.0, name="重建股")
    finally:
        conn.close()

    # 模拟历史落库为空(历史上 boom 长期未落库)
    monkeypatch.setattr(kpl, "query_auction_history", lambda date, tab: [])
    monkeypatch.setattr(kpl, "fill_bid_turnover_from_snap", lambda d, date: d)
    monkeypatch.setattr(kpl, "_boom_spot_map", lambda: {})

    r = client.get(f"/api/kpl/bid-boom?date={D_TODAY}",
                   headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 200, r.text
    d = r.json()
    lst = d.get("list") or d.get("data") or []
    assert any(x["code"] == "600777" for x in lst), f"历史无落库时应从快照重建, 实际 {len(lst)} 只"
