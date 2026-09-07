# -*- coding: utf-8 -*-
"""2026-09-03 修复「竞额列=实时成交额」: 
- auction_snapshot.load_day_bid_amt: 当日各时点快照取最接近 9:25 定格的 bid_amt
- scorer.process_all_stocks(day_bid_amt): 窗口外(盘中/收盘)bidAmt/bidRatio 以 9:25 定格竞价额为准,
  不再用腾讯兜底伪 f616(=实时累计成交额); 窗口内行情 f616 新鲜仍直接使用"""
import pytest

from app.db import database
from app.services import auction_snapshot, scorer

# 行情样本: 腾讯兜底伪竞价字段(实时累计成交额 20亿=2e9元 塞进 f616)
TENCENT_RAW = {"f2": 18.50, "f3": 3.20, "f4": 3.10, "f5": 150000.0, "f6": 2.0e9,
               "f8": 5.50, "f10": 1.80, "f12": "600001", "f14": "测试甲",
               "f17": 18.90, "f18": 17.90, "f20": 5.0e10, "f21": 4.0e9,
               "f100": "软件服务", "f102": "广东", "f103": "AI概念",
               "f615": 4.0, "f616": 2.0e9, "f617": 300.0, "f630": 3}
# 东财正常行情: f616=5000万(5e7元)=9:25 定格竞价额
EM_RAW = dict(TENCENT_RAW, f616=5.0e7)

FILTER = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
          "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}

TEST_DATE = "2020-01-02"


def _insert_snapshot(date, tp, code, bid_amt, bid_change=3.0):
    conn = database.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, bid_amt, name, ts) "
        "VALUES (?,?,?,?,?,?,0)", (date, tp, code, bid_change, bid_amt, code))
    conn.commit()
    conn.close()


def _clean(date):
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid WHERE date=?", (date,))
    conn.commit()
    conn.close()


# ---------- load_day_bid_amt ----------
def test_load_day_bid_amt_prefers_925(monkeypatch):
    """同股多时点: 取最接近 9:25 定格的时点(9_25 > 9_24 > 9_20 > 9_15)"""
    _clean(TEST_DATE)
    monkeypatch.setattr(auction_snapshot, "_bj_date", lambda: TEST_DATE)
    _insert_snapshot(TEST_DATE, "9_15", "600001", 100.0)
    _insert_snapshot(TEST_DATE, "9_20", "600001", 200.0)
    _insert_snapshot(TEST_DATE, "9_24", "600001", 250.0)
    _insert_snapshot(TEST_DATE, "9_25", "600001", 300.0)
    _insert_snapshot(TEST_DATE, "9_15", "000002", 120.0)   # 只有早时点
    _insert_snapshot(TEST_DATE, "9_25", "300003", 0.0)     # 0 值不参与
    try:
        m = auction_snapshot.load_day_bid_amt()
        assert m["600001"] == 300.0     # 9_25 定格
        assert m["000002"] == 120.0     # 无更新时点取 9_15
        assert "300003" not in m        # bid_amt<=0 跳过
    finally:
        _clean(TEST_DATE)


def test_load_day_bid_amt_no_data():
    """无数据日期返回空 map"""
    assert auction_snapshot.load_day_bid_amt("2000-01-01") == {}


# ---------- process_all_stocks: bidAmt 修复 ----------
def test_bid_amt_off_window_uses_day_snapshot(monkeypatch):
    """窗口外(盘中/收盘): 腾讯伪 f616=实时成交额 2e9 → bidAmt 用 9:25 定格快照 8000万"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    r = scorer.process_all_stocks([dict(TENCENT_RAW)], FILTER,
                                  {"600001": [20000.0, 15000.0]}, {},
                                  day_bid_amt={"600001": 8000.0})
    assert r[0]["bidAmt"] == 8000.0
    # bidRatio 连带修正: 8000万 / 昨日20000万 = 40% (而非实时成交额的 10000%+)
    assert r[0]["bidRatio"] == 40.0


def test_bid_amt_off_window_missing_code_is_zero(monkeypatch):
    """2026-09-07 语义变更: 快照缺该 code(采集缺失) → **0**, 不再回退行情 f616。
    原回退会把腾讯兜底的"全天成交额"(此处 200000 万)当竞价额 → 竞价额门槛形同虚设
    (实测同一参数返回 19/56/122 只)。缺失应表现为 0(不满足"≥门槛"被过滤)。"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    r = scorer.process_all_stocks([dict(TENCENT_RAW)], FILTER, {}, {},
                                  day_bid_amt={"000001": 500.0})
    assert r[0]["bidAmt"] == 0.0


def test_bid_amt_off_window_no_map_is_zero(monkeypatch):
    """不传 day_bid_amt(旧调用方/兼容) → 同样为 0(窗口外只认定格快照)"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    r = scorer.process_all_stocks([dict(EM_RAW)], FILTER, {}, {})
    assert r[0]["bidAmt"] == 0.0


def test_bid_amt_in_window_uses_live_f616(monkeypatch):
    """竞价窗口内: 行情 f616 新鲜(东财定格/腾讯仍在竞价累计)直接使用, 快照定格不覆盖"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    r = scorer.process_all_stocks([dict(EM_RAW)], FILTER, {}, {},
                                  day_bid_amt={"600001": 3000.0})
    assert r[0]["bidAmt"] == 5000.0
