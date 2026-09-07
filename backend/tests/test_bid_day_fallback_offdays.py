# -*- coding: utf-8 -*-
"""定格 map 非交易时段自动回退最近交易日 (2026-09-08 主人需求)
============================================================
背景: 凌晨 0:00-9:25 采集前 / 周末 / 节假日当日无 9_25 快照 → load_day_bid_amt /
      load_day_bid_change 若返回 {} → ① 竞价额门槛(bidAmtFloor)把名单滤空; ② 竞涨缺定格
      → scorer.get_bid_change 退 f3(收盘涨幅) → 重现 9/7 生产事故"竞涨=现涨+大跌票混入"。
修复: 与 load_snapshot_full 同口径 — 9_25 行每交易日必采, 以其存在性判定"该交易日已完成
      竞价定格"; 当日无行则自动回退表内最近 ≤当日 的交易日(15 自然日窗口)。
验证: 周末/凌晨回退 / 当日有行仍用当日(盘中正常不变) / 无参默认调用 / 空库兜底 /
      回退日同样多时点取最晚(9_25 优先)。
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.core import config
from app.services import auction_snapshot

_BJ8 = timedelta(hours=8)


def _day(ago):
    """真实北京日期(ago 天前) — 回退 SQL 下界 date('now','-15 days','+8 hours') 用
    SQLite 真实时钟, 无法 monkeypatch, 测试日期必须落在 15 自然日窗口内"""
    return (datetime.now(timezone.utc) + _BJ8 - timedelta(days=ago)).strftime("%Y-%m-%d")


D_QUERY = _day(0)  # 查询日(模拟凌晨 0:00-9:25 / 周末 / 节假日, 当日无快照)
D_LAST = _day(1)   # 最近交易日(有 9_25 定格行)


@pytest.fixture(autouse=True)
def _clean_rows():
    """用例前后清空本文件使用日期的 snapshot_bid 行, 断言只命中本用例插入的数据"""
    def _del():
        conn = sqlite3.connect(config.DB_FILE)
        try:
            conn.execute("DELETE FROM snapshot_bid WHERE date IN (?,?)", (D_LAST, D_QUERY))
            conn.commit()
        finally:
            conn.close()
    _del()
    yield
    _del()


def _ensure_cols(conn):
    """测试库可能无 free_mv 列(生产已 ALTER); 缺失则补列, 保证插入与生产口径一致"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(snapshot_bid)")]
    if "free_mv" not in cols:
        conn.execute("ALTER TABLE snapshot_bid ADD COLUMN free_mv REAL")
        conn.commit()


def _snap(date, tp, code, bid_amt, chg):
    """插一条 snapshot_bid(bid_amt 万元; bid_change %)"""
    conn = sqlite3.connect(config.DB_FILE)
    try:
        _ensure_cols(conn)
        conn.execute(
            "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,"
            "bid_buy_amt,float_mv,board,free_mv) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (date, tp, code, chg, bid_amt, 0, "测试股", 0.0, 1e10, "概念A", 1e10))
        conn.commit()
    finally:
        conn.close()


def test_offday_falls_back_to_last_trade_day():
    """查询日无快照(凌晨/周末/节假日) → bid_amt/bid_change 回退最近交易日, 且多时点取最晚
    (D_LAST 同时有 9_20/9_25 行 → 取 9_25 定格)"""
    _snap(D_LAST, "9_20", "600000", 100, 1.0)
    _snap(D_LAST, "9_25", "600000", 8888, 3.5)
    assert auction_snapshot.load_day_bid_amt(D_QUERY) == {"600000": 8888.0}, \
        "当日无快照应回退最近交易日 9_25 定格竞价额"
    assert auction_snapshot.load_day_bid_change(D_QUERY) == {"600000": 3.5}, \
        "当日无快照应回退最近交易日 9_25 定格竞价涨幅"


def test_default_call_falls_back_like_offday():
    """无参调用(生产 stocks.py/system_batch 形态, date=_bj_date()=今天) → 今天无行回退昨天"""
    _snap(D_LAST, "9_25", "600001", 5000, 2.0)
    assert auction_snapshot.load_day_bid_amt()["600001"] == 5000.0
    assert auction_snapshot.load_day_bid_change()["600001"] == 2.0


def test_uses_query_day_when_snapshot_exists():
    """当日已有 9_25 行(正常交易日 9:25 采集完成后) → 必须用当日, 不得跨日回退"""
    _snap(D_QUERY, "9_25", "600002", 3000, 1.5)
    _snap(D_LAST, "9_25", "600002", 9999, 9.9)
    assert auction_snapshot.load_day_bid_amt(D_QUERY) == {"600002": 3000.0}, \
        "当日有快照不得回退昨日(9_25 盘中/盘后行为不变)"
    assert auction_snapshot.load_day_bid_change(D_QUERY) == {"600002": 1.5}


def test_empty_db_returns_empty():
    """无任何历史快照(空库/长假超 15 日窗口) → 保持原兜底返回 {}, 不把陈旧数据当最近交易日"""
    assert auction_snapshot.load_day_bid_amt(D_QUERY) == {}
    assert auction_snapshot.load_day_bid_change(D_QUERY) == {}
