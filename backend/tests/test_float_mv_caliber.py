# -*- coding: utf-8 -*-
"""D 组：竞价异动「流通(亿)」/「竞换」口径统一到**实际流通(free_mv)**。

背景（2026-09-29 生产实测）：同一只票同一天，两个 tab 差 2 倍 ——
  三时点封单榜 600825 流通 47.70 亿 / 竞换 0.89%；竞价委买 98.32 亿 / 0.43%。
根因：`fill_bid_turnover_from_snap` 用 `s["float_mv"]`（东财**流通市值** ≈ 自由流通 2 倍），
而它的 docstring 写的是「自由流通市值×100，与开盘啦口径一致」；`bid_net_from_snap` 只 SELECT
float_mv；`_merge_broken_bid_snap` 的 bid_date 分支同理。
口径依据：主人 2026-08-19 定的「流通列 = 实际流通」，也是
`fill_float_mv_from_snap` / `_boom_from_snap` / 三时点榜既有的实现。
"""
import sqlite3

import pytest

from app.core import config
from app.services import kpl

FLOAT_MV = 98.32e8      # 东财流通市值 98.32 亿
FREE_MV = 47.70e8       # 实际流通(自由流通) 47.70 亿 —— 应该用它
BID_AMT_WAN = 4236.46   # 9:25 竞价额(万元)


def test_fill_turnover_uses_free_mv(monkeypatch):
    """竞换 = 竞价额/实际流通×100；流通列也必须是实际流通。"""
    lst = [{"code": "600825"}]
    fake = {"600825": {"bid_amt": BID_AMT_WAN, "free_mv": FREE_MV, "float_mv": FLOAT_MV,
                       "bid_change": 10.06}}
    monkeypatch.setattr(kpl, "_snap25_map", lambda d=None: fake)
    kpl.fill_bid_turnover_from_snap(lst, "2026-09-29")
    it = lst[0]
    # 4236.46 万 × 1e4 / 47.70 亿 × 100 = 0.8875%
    assert abs(it["bidTurnover"] - 0.8875) < 0.02, it
    assert abs(it["floatMv"] - FREE_MV) < 1, "流通列必须是实际流通(free_mv), 不得是流通市值"
    # 反向守卫：若误用 float_mv 会得到 0.43%（差一倍），必须能区分
    assert abs(it["bidTurnover"] - 0.4309) > 0.1


def test_fill_turnover_falls_back_to_float_mv(monkeypatch):
    """free_mv 缺失时才允许用 float_mv 兜底（口径降级但不留空）。"""
    lst = [{"code": "600001"}]
    fake = {"600001": {"bid_amt": 1000.0, "free_mv": 0, "float_mv": 10e8, "bid_change": 10.0}}
    monkeypatch.setattr(kpl, "_snap25_map", lambda d=None: fake)
    kpl.fill_bid_turnover_from_snap(lst, "2026-09-29")
    assert abs(lst[0]["floatMv"] - 10e8) < 1


@pytest.fixture()
def snap_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, bid_amt REAL,"
                 " bid_change REAL, float_mv REAL, free_mv REAL, name TEXT, board TEXT,"
                 " bid_buy_amt REAL)")
    conn.execute("INSERT INTO snapshot_bid VALUES ('2026-09-29','9_25','600825',?,10.06,?,?,"
                 "'新华传媒','',?)", (BID_AMT_WAN, FLOAT_MV, FREE_MV, 77.37e8))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_FILE", str(db))
    return db


def test_bid_net_rebuild_uses_free_mv(snap_db):
    """竞价净额榜按快照重建时，流通列/竞换也必须取实际流通。"""
    out = kpl.bid_net_from_snap("2026-09-29")
    assert out, "应能从 9_25 快照重建出净额榜"
    row = out[0]
    assert abs(row["floatMv"] - FREE_MV) < 1, row
    assert abs(row["bidTurnover"] - 0.8875) < 0.02, row
