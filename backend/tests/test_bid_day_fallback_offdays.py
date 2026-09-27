# -*- coding: utf-8 -*-
"""定格 map 非交易时段自动回退最近交易日 (2026-09-08 主人需求)
============================================================
背景: 凌晨 0:00-9:25 采集前 / 周末 / 节假日当日无 9_25 快照 → load_day_bid_amt /
      load_day_bid_change 若返回 {} → ① 竞价额门槛(bidAmtFloor)把名单滤空; ② 竞涨缺定格
      → scorer.get_bid_change 退 f3(收盘涨幅) → 重现 9/7 生产事故"竞涨=现涨+大跌票混入"。
修复: 与 load_snapshot_full 同口径 — 9_25 行每交易日必采, 以其存在性判定"该交易日已完成
      竞价定格"; 当日无行则自动回退表内最近 ≤当日 的交易日(15 自然日窗口)。

★★ 2026-09-27 v4.11.66 追加「交易日历过滤」—— 本文件的日期构造方式因此改写:
   原实现把「最近交易日」等同于「表内 MAX(date)」, 隐含假设"表里只可能有交易日行"。
   2026-09-25(中秋·周五·法定休市, 当天傍晚才补上日历门禁)打破了这个假设: 当天照常采集
   并落库了一整天的**幽灵快照**(四个时点的 bid_change/bid_amt 各自都等于 09-24 的 9_25
   定格值) ⇒ 09-27(周日)读侧取到 09-25, 整站竞价数据被静态值顶掉。
   故读侧改为 `latest_trade_snap_date()`: 回溯候选 → `trade_calendar.latest_trade_in()`
   挑第一个**真交易日**。

★ 本文件的日期构造铁律(2026-09-27 修订): 测试日期**不得用 `today - 1 天` 这类自然日推算**,
  必须走 `trade_calendar` —— 否则在周末/节假日跑测试时 "昨天" 本身就不是交易日, 断言会
  随运行日历日漂移(本轮实测: 09-27 周日跑, 旧写法 `_day(1)=09-26` 是周六 → 2 个用例红,
  而代码是对的、测试是错的: **测试自己踩了它要防的那个坑**)。
验证: 周末/凌晨回退 / 当日有行仍用当日(盘中正常不变) / 无参默认调用 / 空库兜底 /
      回退日同样多时点取最晚(9_25 优先) / **休市日幽灵行必须被跳过(新增)**。
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.core import config
from app.core import trade_calendar as tc
from app.services import auction_snapshot

_BJ8 = timedelta(hours=8)


def _day(ago):
    """真实北京日期(ago 天前) — 回退 SQL 下界 date('now','-15 days','+8 hours') 用
    SQLite 真实时钟, 无法 monkeypatch, 测试日期必须落在 15 自然日窗口内"""
    return (datetime.now(timezone.utc) + _BJ8 - timedelta(days=ago)).strftime("%Y-%m-%d")


def _recent_weekend(within=7):
    """最近一个**周六/周日**(必然非交易日; 用 weekday 判定, 与节假日表无关)"""
    base = datetime.strptime(_day(0), "%Y-%m-%d")
    for i in range(1, within + 1):
        c = base - timedelta(days=i)
        if c.weekday() >= 5:
            return c.strftime("%Y-%m-%d")
    return None


D_TODAY = _day(0)                                # 今天(可能非交易日)
D_OFF = _recent_weekend()                        # 最近一个周末日 ★ 必然非交易日
D_LAST = tc.prev_trade_date(D_OFF)               # D_OFF 之前的最近**交易日**(回退目标)
D_PREV = tc.prev_trade_date(D_LAST)              # 再上一个交易日


@pytest.fixture(autouse=True)
def _clean_rows():
    """用例前后清空本文件使用日期的 snapshot_bid 行, 断言只命中本用例插入的数据"""
    dates = tuple(x for x in (D_LAST, D_PREV, D_OFF, D_TODAY) if x)

    def _del():
        conn = sqlite3.connect(config.DB_FILE)
        try:
            conn.execute(
                "DELETE FROM snapshot_bid WHERE date IN (%s)" % ",".join("?" * len(dates)),
                dates)
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
    assert auction_snapshot.load_day_bid_amt(D_OFF) == {"600000": 8888.0}, \
        "非交易日无快照应回退最近交易日 9_25 定格竞价额"
    assert auction_snapshot.load_day_bid_change(D_OFF) == {"600000": 3.5}, \
        "非交易日无快照应回退最近交易日 9_25 定格竞价涨幅"


def test_default_call_falls_back_like_offday():
    """无参调用(生产 stocks.py/system_batch 形态, date=_bj_date()=今天) → 今天无行回退最近交易日"""
    _snap(D_LAST, "9_25", "600001", 5000, 2.0)
    assert auction_snapshot.load_day_bid_amt()["600001"] == 5000.0
    assert auction_snapshot.load_day_bid_change()["600001"] == 2.0


def test_uses_query_day_when_snapshot_exists():
    """当日已有 9_25 行(正常交易日 9:25 采集完成后) → 必须用当日, 不得跨日回退"""
    _snap(D_LAST, "9_25", "600002", 3000, 1.5)
    _snap(D_PREV, "9_25", "600002", 9999, 9.9)
    assert auction_snapshot.load_day_bid_amt(D_LAST) == {"600002": 3000.0}, \
        "当日有快照不得回退昨日(9_25 盘中/盘后行为不变)"
    assert auction_snapshot.load_day_bid_change(D_LAST) == {"600002": 1.5}


def test_empty_db_returns_empty():
    """无任何历史快照(空库/长假超 15 日窗口) → 保持原兜底返回 {}, 不把陈旧数据当最近交易日"""
    assert auction_snapshot.load_day_bid_amt(D_OFF) == {}
    assert auction_snapshot.load_day_bid_change(D_OFF) == {}


def test_holiday_ghost_rows_are_skipped():
    """★ v4.11.66 回归: 休市日残留的「幽灵快照」行**必须被跳过**, 不得当成最近交易日。

    事故形态(2026-09-25 中秋): 休市日当天尚无日历门禁 → 照常采集, 四个时点的
    bid_change/bid_amt 各自都等于上一交易日的 9_25 定格值。读侧原用裸 `MAX(date)`,
    于是休市日行当选 ⇒ 整站竞价数据(竞价封单/爆量/净额 + 两市概况 + 选股定格)被静态值顶掉。

    本用例: 在「必然非交易日」的周末日 D_OFF 上插一条幽灵行(值 7777),
    真实值放上一交易日 D_PREV(3000) —— 查询 D_OFF 时必须拿到 3000。
    ★ 旧实现在这里是 7777(取到 MAX(date)=D_OFF 的幽灵行), 故本用例**专治该回归**。
    """
    _snap(D_OFF, "9_25", "600004", 7777, 7.7)     # 幽灵行(休市日静态值)
    _snap(D_PREV, "9_25", "600004", 3000, 1.5)    # 真实交易日定格
    amt = auction_snapshot.load_day_bid_amt(D_OFF)
    chg = auction_snapshot.load_day_bid_change(D_OFF)
    assert amt == {"600004": 3000.0}, \
        "休市日幽灵行被当成最近交易日了(D_OFF=%s, 期望跳过它取 %s)" % (D_OFF, D_PREV)
    assert chg == {"600004": 1.5}


def test_latest_trade_snap_date_picks_trade_day():
    """`latest_trade_snap_date()` 直接断言: 候选里的周末行被跳过, 取到交易日"""
    _snap(D_OFF, "9_25", "600005", 111, 1.1)
    _snap(D_LAST, "9_25", "600005", 222, 2.2)
    assert auction_snapshot.latest_trade_snap_date(D_OFF) == D_LAST, \
        "周末日(D_OFF)有行时仍须回落到交易日 D_LAST"
    assert auction_snapshot.latest_trade_snap_date(D_LAST) == D_LAST
    # fail-open: 候选里**没有任何交易日**(只剩周末幽灵行) → 返回原日期, 绝不主动留空
    conn = sqlite3.connect(config.DB_FILE)
    try:
        conn.execute("DELETE FROM snapshot_bid WHERE date=?", (D_LAST,))
        conn.commit()
    finally:
        conn.close()
    assert auction_snapshot.latest_trade_snap_date(D_OFF) == D_OFF
