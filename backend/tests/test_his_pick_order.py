# -*- coding: utf-8 -*-
"""竞价选股（/api/his-pick）快照**读侧**必须按评分降序返回。

2026-10-08 主人反馈：「竞价选股排序不是按照评分的」。根因不在评分/过滤/排序逻辑
（`services/his_pick.processAllStocks` 确实按 `probability` 降序），而在**快照的 rank**：
`save_snapshot` 用「**那一次请求过滤后的列表下标**」当 rank，而竞价时段前端会带不同
筛选条件各请求一次 ⇒ 两套枚举互相错位 ⇒ 表里的 rank 序不再等于评分序。
生产实测：2026-10-08 共 336 行，rank 连续且唯一（1..336），但评分序列是
`48,95,48,48,95,48,48,95…` **完全无序**；而 2026-10-03 那次 113 行**是有序的**
⇒ 典型的「有时候才坏」的静默脏数据（线上不报错，只是名次不对）。

修法：`api/his_pick._snapshot_items()` 改为 `ORDER BY probability DESC, rank`
（评分相同的再用 rank 稳定兜底）⇒ ① 前端看到的顺序 = 他的原件语义；
② **历史脏快照无需改库即刻自愈**；③ 9:30 后「读快照→刷现涨→回写」路径回写时
顺便把库里的 rank 也修正。

本用例把「rank 已经错乱」的快照灌进临时库，断言读出来仍是评分降序。
"""
import sqlite3

import pytest

from app.services import his_pick as H
from app.api import his_pick as A

DAY = "2026-10-08"

# (code, name, rank, probability)：rank 是"乱的"（刻意复刻生产现场那种交错）
DIRTY = [
    ("600001", "甲股", 1, 48),
    ("600002", "乙股", 2, 95),
    ("600003", "丙股", 3, 48),
    ("600004", "丁股", 4, 48),
    ("600005", "戊股", 5, 95),
    ("600006", "己股", 6, 60),
]


@pytest.fixture()
def dirty_db(tmp_path, monkeypatch):
    """造一个 rank 与评分**不一致**的快照库，并把 H.DB 指向它"""
    db = tmp_path / "aipick_test.db"
    c = sqlite3.connect(str(db))
    H.snapshot_table(c)
    for code, name, rank, prob in DIRTY:
        c.execute(
            "INSERT OR REPLACE INTO his_pick_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (DAY, "2026-10-08 19:25:51", code, name, rank, prob, 90, 1.0, 2.0, 3.0,
             0.0, "测试", "概念A", 1000.0))
    c.commit()
    c.close()
    monkeypatch.setattr(H, "DB", str(db))
    return db


def test_snapshot_read_is_sorted_by_probability_desc(dirty_db):
    items, day = A._snapshot_items()
    assert day == DAY
    assert len(items) == len(DIRTY), "行数应与灌入一致（不会被过滤）"

    probs = [it["probability"] for it in items]
    assert probs == sorted(probs, reverse=True), \
        "读侧必须按评分降序；实测序列=%s（修好之前是脏 rank 序）" % probs
    # 最高分必须排在第一位（= 金牌卡 / 「导出前 N」用的就是列表前几项）
    assert items[0]["name"] == "乙股" or items[0]["probability"] == 95


def test_save_snapshot_rewrites_rank_in_score_order(dirty_db):
    """写侧兜底：即便调用方递进来一份乱序名单，落库的 rank 也要 = 评分序"""
    c = sqlite3.connect(str(dirty_db))
    rows = list(c.execute(
        "SELECT code, name, probability FROM his_pick_daily WHERE trade_date=? ORDER BY rank",
        (DAY,)).fetchall())
    c.close()

    # 原样（乱序）回写：模拟"9:30 后读快照→刷现涨→回写"这条路径
    items = [{"code": r[0], "name": r[1], "probability": r[2]} for r in rows]
    assert H.save_snapshot(items, DAY, {"pool_size": 0}) is True

    c = sqlite3.connect(str(dirty_db))
    after = c.execute(
        "SELECT rank, probability FROM his_pick_daily WHERE trade_date=? ORDER BY rank",
        (DAY,)).fetchall()
    c.close()
    probs = [p for _r, p in after]
    assert probs == sorted(probs, reverse=True), \
        "回写后 rank 序必须被修正为评分降序，实测=%s" % probs
    assert [r for r, _p in after] == list(range(1, len(after) + 1)), "rank 仍应连续"
