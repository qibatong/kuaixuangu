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


def _tdate(offset_days=0):
    return time.strftime("%Y%m%d", time.localtime(time.time() + offset_days * 86400))


# ---------------- ① 落库/读库往返 ----------------
def test_db_put_get_roundtrip():
    n = fetcher.yday_db_put([
        (_CODE_A, _tdate(), 12345.0, 11111.0, 3.55),
        (_CODE_B, _tdate(), 6789.0, 6000.0, -1.20),
    ])
    assert n == 2
    got = fetcher.yday_db_get([_CODE_A, _CODE_B])
    assert got[_CODE_A] == (12345.0, 11111.0, 3.55)
    assert got[_CODE_B] == (6789.0, 6000.0, -1.20)


def test_db_put_overwrites_same_code():
    """按 code 覆盖写: 收盘刷新覆盖前一日数据, 库内该 code 始终只有一行"""
    fetcher.yday_db_put([(_CODE_A, _tdate(-1), 100.0, 90.0, 1.0)])
    fetcher.yday_db_put([(_CODE_A, _tdate(), 200.0, 100.0, 2.0)])
    assert fetcher.yday_db_get([_CODE_A])[_CODE_A] == (200.0, 100.0, 2.0)


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
    def _boom(codes):
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
    date = fetcher._bj_date_str()
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache[_CODE_A] = [date, [12345.0, 11111.0], time.time(), 3.55]
        fetcher._yesterday_cache[_CODE_B] = [date, None, time.time(), None]      # 失败行不落库
    assert yday_prewarm._persist_to_db([_CODE_A, _CODE_B], date) == 1
    assert fetcher.yday_db_get([_CODE_A])[_CODE_A] == (12345.0, 11111.0, 3.55)
    assert fetcher.yday_db_get([_CODE_B]) == {}, "失败(无成交额)的行不得落库"


def test_prewarm_only_close_stage_persists(monkeypatch):
    """盘中预热(stage=open)不得落库; 收盘刷新(stage=close)才落库"""
    written = []
    monkeypatch.setattr(yday_prewarm, "_persist_to_db",
                        lambda codes, date: written.append(date) or 0)
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
