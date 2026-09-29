# -*- coding: utf-8 -*-
"""竞价净额补齐 fill_bid_net_from_snap —— 2026-09-29

主人反馈「竞价异动有些展示的数据和实时的数据不一致，包括其他子板块的」。核实出的**真缺陷**之一：
  · 竞价异动的落库快照由 save_auction_history(phase='bid') 在 **09:24:2x** 采集（设计如此：再晚
    开盘啦 Type4 的竞价净额会被清零），而那一刻开盘啦**还没产出竞净额**（官方 ready_after=09:25:35）
    ⇒ 落库行 bidNetAmt **全为 0**。生产实测(2026-09-29)：seal 72/72 全 0、bid_net 37/37 全 0。
  · 自采定格 snapshot_bid.auc_main_net(9_25)（口径 = 猫爪 fundflow_kp 官方成品，单位元，
    契约原文「9:25 定格后即为当日终值」）当日 **1292 只有值** ⇒ 有现成官方口径没被用上。
⇒ 盘后/历史看「竞价净额」tab 一直是 0/空，与实时（竞价时段接口直给）不一致。

断言：
  ① 0/缺 → 用快照值补齐（单位元，**不换算**）；
  ② 已带非 0 → **不覆盖**（竞价时段接口直给的真值优先）；
  ③ 负值（净流出）同样要补；
  ④ 只认 9_25 时点（9_24 行不算数）；
  ⑤ 快照无该 code / 快照本身为 0 → 保持原值；
  ⑥ 空列表 / 表不存在 → 原样返回不抛（补齐失败绝不影响主流程）。
"""
import sqlite3

import pytest

from app.core import config
from app.services import kpl


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
                 "auc_main_net REAL, bid_amt REAL, float_mv REAL)")
    conn.executemany("INSERT INTO snapshot_bid VALUES (?,?,?,?,?,?)", [
        ("2026-09-29", "9_25", "600001", 123456789.0, 100.0, 1e9),
        ("2026-09-29", "9_25", "600002", 0.0, 100.0, 1e9),        # 快照里也是 0(官方非零率约 23%)
        ("2026-09-29", "9_25", "600003", -5000000.0, 100.0, 1e9),  # 净流出(负值)也要补
        ("2026-09-29", "9_24", "600004", 999.0, 100.0, 1e9),       # 只认 9_25 时点
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_FILE", str(db))
    return str(db)


def test_fill_zeros_including_negative(tmp_db):
    lst = [{"code": "600001", "bidNetAmt": 0.0}, {"code": "600003", "bidNetAmt": 0.0}]
    kpl.fill_bid_net_from_snap(lst, "2026-09-29")
    assert lst[0]["bidNetAmt"] == 123456789.0, "0 值应被官方 9:25 净额补齐(单位元, 不换算)"
    assert lst[1]["bidNetAmt"] == -5000000.0, "净流出(负值)同样要补"


def test_do_not_overwrite_nonzero(tmp_db):
    lst = [{"code": "600001", "bidNetAmt": 777.0}]
    kpl.fill_bid_net_from_snap(lst, "2026-09-29")
    assert lst[0]["bidNetAmt"] == 777.0, "竞价时段接口直给的真值优先, 不得被覆盖"


def test_missing_code_or_snapshot_zero_keeps_value(tmp_db):
    lst = [{"code": "600002", "bidNetAmt": 0.0}, {"code": "600099", "bidNetAmt": 0.0}]
    kpl.fill_bid_net_from_snap(lst, "2026-09-29")
    assert lst[0]["bidNetAmt"] == 0.0, "快照自身为 0(该票当日无净额) ⇒ 保持"
    assert lst[1]["bidNetAmt"] == 0.0, "快照里没有这只票 ⇒ 保持"


def test_only_925_point_counts(tmp_db):
    lst = [{"code": "600004", "bidNetAmt": 0.0}]
    kpl.fill_bid_net_from_snap(lst, "2026-09-29")
    assert lst[0]["bidNetAmt"] == 0.0, "只有 9_25 时点算数, 9_24 行不参与"


def test_empty_list_and_noop(tmp_db):
    assert kpl.fill_bid_net_from_snap([], "2026-09-29") == []
    lst = [{"code": "600001", "bidNetAmt": 5.0}]
    out = kpl.fill_bid_net_from_snap(lst, "2026-09-29")
    assert out is lst and lst[0]["bidNetAmt"] == 5.0


def test_table_missing_does_not_raise(monkeypatch, tmp_path):
    """库异常(无表)时原样返回 —— 补齐失败绝不影响主流程。"""
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setattr(config, "DB_FILE", str(db))
    lst = [{"code": "600001", "bidNetAmt": 0.0}]
    assert kpl.fill_bid_net_from_snap(lst, "2026-09-29") is lst
    assert lst[0]["bidNetAmt"] == 0.0
