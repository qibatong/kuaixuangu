# -*- coding: utf-8 -*-
"""昨日成交额「收盘落库 + 全天读库」测试(2026-09-10 去兜底核心改动)

背景: 原实现每次选股实时逐只拉东财日K, 并挂 同花顺/腾讯/量脉 多源兜底链 ——
口径不一致 + 熔断横跳 → 数据跳变。改为: 每交易日收盘(15:10)批量拉一次全市场
写入 yday_amount 表(按 code 覆盖写), 之后全天直接读库, 零网络零兜底。

本文件锁定四条性质:
  ① 落库/读库往返正确(含 prev_amount / chg), 同 code 覆盖写
  ② 读库过滤 过期 tdate / 空 amount(长假或停更不得当脏数据用)
  ③ hydrate 只填未命中的 code, 不覆盖当日已成功的实时缓存; 命中即零网络
  ④ 只有收盘刷新(stage=close)落库, 盘中预热(stage=open)不落库(否则污染"昨日"语义)

注意: 用 YDT 前缀假代码并自行清理 —— 测试库是 session 共享的, 写真实代码会污染
其他用例的读库结果。
"""
import time

import pytest

from app.db import database
from app.services import fetcher, yday_prewarm

# conftest 的 session 级 mock_data_source 把 fetch_yesterday_amounts 整体替换为假实现,
# 本文件要测真实读库路径 → import 期留真实实现再还原(同 test_yesterday_cache 做法)
_ORIG_FETCH_YDAY = fetcher.fetch_yesterday_amounts

_CODE_A = "YDT001"
_CODE_B = "YDT002"


@pytest.fixture(autouse=True)
def _clean_yday_rows(monkeypatch):
    """确保表存在, 用例前后清掉本文件写入的假代码, 并清昨比缓存"""
    database.init_db()
    _purge()
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache.clear()
    yield
    _purge()
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache.clear()


def _purge():
    conn = database.get_conn()
    try:
        conn.execute("DELETE FROM yday_amount WHERE code IN (?,?)", (_CODE_A, _CODE_B))
        conn.commit()
    finally:
        conn.close()


def _expect_tdate():
    """`yday_amount` 此刻应有的 T 日(`YYYYMMDD`) —— 直接取生产实现, 不自行推算。

    ★ 2026-09-26: 读库/落库都改为按「期望 T 日」逐行比对后, 测试**不能再用"今天"**
      当 tdate —— 周末 / 长假 / 盘中的"今天"根本不是 T 日, 用它会得到"数据不可信"这个
      **正确**结果, 从而把用例测歪(且用例会随真实时钟在周末/节假日变红)。
    """
    return fetcher._yday_expected_tdate()


def _tdate(offset_days=0):
    """库内 tdate 测试值: 0 → 期望 T 日; 负数 → 在其基础上往前推 N 天。"""
    base = _expect_tdate()
    if not base:
        return ""
    if not offset_days:
        return base
    t = time.mktime(time.strptime(base, "%Y%m%d")) + offset_days * 86400
    return time.strftime("%Y%m%d", time.localtime(t))


# ---------------- ① 落库/读库往返 ----------------
def test_db_put_get_roundtrip():
    t = _tdate()
    n = fetcher.yday_db_put([
        (_CODE_A, t, 12345.0, 11111.0, 3.55),
        (_CODE_B, t, 6789.0, 6000.0, -1.20),
    ])
    assert n == 2
    got = fetcher.yday_db_get([_CODE_A, _CODE_B], expect_tdate=t)
    assert got[_CODE_A] == (12345.0, 11111.0, 3.55)
    assert got[_CODE_B] == (6789.0, 6000.0, -1.20)
    # 带 tdate 过滤时只认相等的行(下面这条旧 tdate 的行必须被排除)
    assert fetcher.yday_db_get([_CODE_A], expect_tdate=_tdate(-1)) == {}


def test_db_put_overwrites_same_code():
    """按 code 覆盖写: 收盘刷新覆盖前一日数据, 库内该 code 始终只有一行"""
    fetcher.yday_db_put([(_CODE_A, _tdate(-1), 100.0, 90.0, 1.0)])
    fetcher.yday_db_put([(_CODE_A, _tdate(), 200.0, 100.0, 2.0)])
    assert fetcher.yday_db_get([_CODE_A], expect_tdate=_tdate())[_CODE_A] == (200.0, 100.0, 2.0)


def test_db_put_get_empty():
    assert fetcher.yday_db_get([]) == {}
    assert fetcher.yday_db_put([]) == 0


# ---------------- ② 读库过滤 ----------------
def test_db_get_skips_stale_tdate():
    """tdate 超过 _YDAY_MAX_AGE_DAYS(跨长假/停更) → 不返回, 回落实时源"""
    stale = _tdate(-(fetcher._YDAY_MAX_AGE_DAYS + 5))
    fetcher.yday_db_put([(_CODE_A, stale, 100.0, 90.0, 1.0)])
    assert fetcher.yday_db_get([_CODE_A]) == {}, "过期 tdate 必须被过滤"


def test_db_get_skips_null_amount():
    fetcher.yday_db_put([(_CODE_A, _tdate(), None, None, None)])
    assert fetcher.yday_db_get([_CODE_A]) == {}, "无成交额的行不得返回"


def test_db_get_requires_expect_tdate():
    """★ 2026-09-26: 给出 expect_tdate 时**只认 tdate 逐位相等的行**(正确性判据)"""
    fetcher.yday_db_put([(_CODE_A, "20260925", 12345.0, 11111.0, 3.55)])
    # 不带 expect: 只过 5 天新鲜度 ⇒ 09-25 的行会被返回(正是旧行为的危险之处)
    assert fetcher.yday_db_get([_CODE_A]) != {}, "5 天新鲜度窗口拦不住错日期的行"
    # 带 expect: 09-25 不是此刻的 T 日 ⇒ 必须被挡掉
    assert fetcher.yday_db_get([_CODE_A], expect_tdate=_tdate()) == {}
    assert fetcher.yday_db_get([_CODE_A], expect_tdate="20260925") != {}


def test_hydrate_ignores_wrong_tdate():
    """★ 脏行(tdate ≠ 期望 T 日) → 读库当没有, 回落实时源 —— 本次事故的自愈判据

    生产现场: 库里 5556 行的 tdate=20260925(**中秋法定休市日, 永远不可能成为期望
    T 日**), 值却是 09-10 的 ⇒ 修复后无论哪天读都命中不了, 等价于不存在。
    """
    fetcher.yday_db_put([(_CODE_A, "20260925", 12345.0, 11111.0, 3.55)])
    rest = fetcher._yday_hydrate_from_db([_CODE_A], fetcher._bj_date_str(), time.time())
    assert rest == [_CODE_A], "tdate 不符的行必须视为未命中(否则数据再次冻结)"
    with fetcher._yesterday_lock:
        assert _CODE_A not in fetcher._yesterday_cache


def test_load_yday_chg_filters_by_tdate():
    """预计算读「昨日涨幅」必须带 tdate 过滤(否则因子一直吃冻结值)"""
    from app.services.picker import precompute
    fetcher.yday_db_put([(_CODE_A, _tdate(), 100.0, 90.0, 3.55)])
    assert precompute.load_yday_chg().get(_CODE_A) == 3.55
    fetcher.yday_db_put([(_CODE_A, _tdate(-3), 100.0, 90.0, 9.99)])
    assert precompute.load_yday_chg().get(_CODE_A) is None, "tdate 不符的行不得进因子"


# ---------------- ③ hydrate: 命中即零网络 ----------------
def test_hydrate_from_db_fills_cache_and_returns_rest():
    """库中命中的 code 直接填当日缓存, 未命中的原样返回(走实时源)"""
    fetcher.yday_db_put([(_CODE_A, _tdate(), 12345.0, 11111.0, 3.55)])
    today = fetcher._bj_date_str()
    rest = fetcher._yday_hydrate_from_db([_CODE_A, _CODE_B], today, time.time())
    assert rest == [_CODE_B], "只有库里没有的才需要走实时源"
    with fetcher._yesterday_lock:
        ent = fetcher._yesterday_cache[_CODE_A]
    assert ent[0] == today
    assert ent[1] == [12345.0, 11111.0], "缓存里存的是成交额对"
    assert ent[3] == 3.55, "涨跌幅必须一并带出(否则 chg 又走补齐重试)"


def test_hydrate_does_not_override_fresh_cache():
    """当日已成功实时拉到的缓存不被读库结果覆盖(实时值更新)"""
    today = fetcher._bj_date_str()
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache[_CODE_A] = [today, [777.0, 700.0], time.time(), 9.9]
    fetcher.yday_db_put([(_CODE_A, _tdate(), 12345.0, 11111.0, 3.55)])
    fetcher._yday_hydrate_from_db([_CODE_A], today, time.time())
    with fetcher._yesterday_lock:
        assert fetcher._yesterday_cache[_CODE_A][1] == [777.0, 700.0]


def test_hydrate_db_failure_falls_back_to_live(monkeypatch):
    """读库异常 → 全部 code 回落实时源(落库是加速手段, 不是主链路)"""
    def _boom(*a, **k):                       # 兼容 yday_db_get(codes, expect_tdate=...)
        raise RuntimeError("库挂了")

    monkeypatch.setattr(fetcher, "yday_db_get", _boom)
    assert fetcher._yday_hydrate_from_db([_CODE_A, _CODE_B], fetcher._bj_date_str(), time.time()) \
        == [_CODE_A, _CODE_B]


def test_fetch_yesterday_amounts_reads_db_without_network(monkeypatch):
    """端到端: 库里有数据时 fetch_yesterday_amounts 完全不发网络请求"""
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", _ORIG_FETCH_YDAY)
    fetcher.yday_db_put([(_CODE_A, _tdate(), 12345.0, 11111.0, 3.55)])
    calls = []
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_one",
                        lambda code: calls.append(code) or ([1.0, 1.0], 1.0))
    out = fetcher.fetch_yesterday_amounts([_CODE_A], wait=True)
    assert out == {_CODE_A: [12345.0, 11111.0]}
    assert calls == [], "读库命中时不得走实时源"


# ---------------- ④ 只有收盘刷新落库 ----------------
def test_persist_to_db_writes_cached_rows():
    """落库 tdate 必须 = 期望 T 日 (2026-09-26 起 `_persist_to_db` 会校验, 不匹配即拒写)"""
    t8 = _expect_tdate()
    date = "%s-%s-%s" % (t8[:4], t8[4:6], t8[6:])          # 期望 T 日的横线格式(收盘路径口径)
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache[_CODE_A] = [date, [12345.0, 11111.0], time.time(), 3.55]
        fetcher._yesterday_cache[_CODE_B] = [date, None, time.time(), None]      # 失败行不落库
    assert yday_prewarm._persist_to_db([_CODE_A, _CODE_B], date) == 1
    assert fetcher.yday_db_get([_CODE_A], expect_tdate=t8)[_CODE_A] == (12345.0, 11111.0, 3.55)
    assert fetcher.yday_db_get([_CODE_B]) == {}, "失败(无成交额)的行不得落库"


def test_persist_to_db_rejects_non_tdate(monkeypatch):
    """★ 2026-09-26 第三道防线: 落库 tdate ≠ 期望 T 日 → 拒绝写入

    模拟"非收盘路径调用"(如手动补落库脚本)把非 T 日的值冠上 T 日标签 —— 那正是
    本次生产事故(数据冻结但标签每天前进)的成因, 必须被挡在库外。
    """
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache[_CODE_A] = ["1999-01-01", [1.0, 1.0], time.time(), 1.0]
    assert yday_prewarm._persist_to_db([_CODE_A], "1999-01-01") == 0
    assert fetcher.yday_db_get([_CODE_A]) == {}, "被拒的行不得进库"


def test_prewarm_only_close_stage_persists(monkeypatch):
    """盘中预热(stage=open)不得落库; 收盘刷新(stage=close)才落库"""
    written = []
    monkeypatch.setattr(yday_prewarm, "_persist_to_db",
                        lambda codes, date: written.append(date) or 1)
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: (None, 0, 9 * 60 + 5, "2026-09-10"))
    monkeypatch.setattr(yday_prewarm.scorer, "market_fs", lambda ms: "m:1+t:2")
    monkeypatch.setattr(yday_prewarm.fetcher, "fetch_spot_quote_map",
                        lambda fs: {_CODE_A: {}})
    monkeypatch.setattr(yday_prewarm.fetcher, "fetch_yesterday_amounts",
                        lambda codes, wait=False: {})
    monkeypatch.setattr(yday_prewarm.fetcher, "_chg_missing", lambda ent: True)

    assert yday_prewarm._prewarm_once(stage="open") is True
    assert written == [], "盘中预热不能落库(T=前一交易日, 写进去会污染)"
    assert yday_prewarm._prewarm_once(stage="close") is True
    assert written == ["2026-09-10"], "收盘刷新必须落库"


def test_prewarm_close_returns_false_when_source_not_ready(monkeypatch):
    """★ 2026-09-26: 收盘落库 0 行(源尚未更新今日K线) → 返回 False, 窗口内重试

    返回 True 会让 `_fired[(date,"close")]` 记成"已成功", 当天彻底不再补,
    次日只能冷启动全市场拉取(2026-09-02 事故想规避的场景)。
    """
    monkeypatch.setattr(yday_prewarm, "_persist_to_db", lambda codes, date: 0)
    monkeypatch.setattr(yday_prewarm, "_bj", lambda: (None, 4, 15 * 60 + 10, "2026-09-10"))
    monkeypatch.setattr(yday_prewarm.scorer, "market_fs", lambda ms: "m:1+t:2")
    monkeypatch.setattr(yday_prewarm.fetcher, "fetch_spot_quote_map",
                        lambda fs: {_CODE_A: {}})
    monkeypatch.setattr(yday_prewarm.fetcher, "fetch_yesterday_amounts",
                        lambda codes, wait=False: {})
    monkeypatch.setattr(yday_prewarm.fetcher, "_chg_missing", lambda ent: True)
    assert yday_prewarm._prewarm_once(stage="close") is False
