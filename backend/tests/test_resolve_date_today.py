# -*- coding: utf-8 -*-
"""`api/kpl._resolve_date()` —— 请求"今天"时绝不允许退到上一交易日。

背景(生产事故 2026-09-29 13:2x): 该函数原先只用数据表 `daily_sector_top` 做日期对齐,
而该表在工作日 **15:30 之后**才由 sector_rotation.record_today_top 写入 ⇒ 盘中每次
`_resolve_date(今天)` 都返回**上一交易日** ⇒ 11 个走它的端点(封单/委买/爆量/净额/今炸板/
昨炸板/昨涨停/昨断板/龙虎榜 …) 全部把昨天的数据当今天返回(主人原话:"很多采用的昨天的")。

判据(主人铁律): 零值/未落库不得回退昨日 ⇒ 交易日必须原样返回; 只有非交易日才允许回退。
"""
import sqlite3

import pytest

from app.api import kpl as akpl
from app.db import database


@pytest.fixture()
def fake_db(tmp_path, monkeypatch):
    """只建 daily_sector_top(对齐用的数据表), 且**故意不写今天** —— 复刻盘中现场。"""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE daily_sector_top "
                 "(date TEXT, source TEXT, boards TEXT, ts INTEGER, PRIMARY KEY (date, source))")
    for d in ("2026-09-28", "2026-09-24", "2026-09-25"):   # 09-25 是中秋休市(幽灵行)
        conn.execute("INSERT INTO daily_sector_top VALUES (?,?,?,0)", (d, "kpl", "[]"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "get_conn", lambda: sqlite3.connect(str(db)))
    return db


def test_today_trade_day_returns_itself_even_if_table_lacks_today(fake_db):
    """🔴 回归: 交易日(今天)即使 daily_sector_top 还没写今天, 也必须返回今天。"""
    assert akpl._resolve_date("2026-09-29") == "2026-09-29"


def test_holiday_still_aligns_backwards(fake_db):
    """非交易日(09-25 中秋)仍按数据表 + 交易日历对齐到 09-24(保留 v4.11.66 的幽灵行修复)。"""
    assert akpl._resolve_date("2026-09-25") == "2026-09-24"


def test_weekend_aligns_backwards(fake_db):
    """周末(09-26/09-27)同样对齐到 09-24(表里 09-28 晚于它, 不参与)。"""
    assert akpl._resolve_date("2026-09-27") == "2026-09-24"


def test_empty_date_returns_empty(fake_db):
    assert akpl._resolve_date("") == ""


def test_trade_day_before_any_data_is_unaffected(fake_db):
    """交易日一律原样返回 —— 不再受"表里有没有那天"影响(这才是本次修复的语义)。"""
    assert akpl._resolve_date("2026-09-23") == "2026-09-23"
