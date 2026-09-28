# -*- coding: utf-8 -*-
"""`_snap25_map` 进程内缓存 的仓库级单测（2026-09-28）

背景: 「龙虎榜」一次请求要连查三次 9_25 全市场快照（补竞价涨幅 / 流通市值 / 竞价换手），
原实现**无缓存** ⇒ 三次全表查询合计 103ms；全仓 11 处调用点同样在重复查库。

★ 附带钉住一个被实测否决的方案：先改成了共享 `kv_cache`（`_cached`），结果**更慢** ——
  该映射约 5561 条，共享层 set/get 需 49ms/18ms（json 序列化 + 落库 + 读回），
  超过一次查询(~35ms)：第 1 次补全从 53ms 涨到 164ms。故最终用进程内缓存。

★★ 两条测试纪律（踩过坑后加的，勿删）：
  1) 计数只统计**主线程**发起的连接 —— 本仓 conftest 会启动整个 app，
     `yday_prewarm` / `kpl` 回看预热等**后台线程**也用 `config.DB_FILE` 查库，
     不筛线程会把它们的连接算进来（曾导致 `assert 2 == 1` 偶发失败）。
  2) 只用**后台预热不会碰**的日期（非交易日 9/19、9/20；预热只跑"最近 5 个交易日"），
     否则后台线程可能抢先填充缓存，让"应当查库一次"的断言变成 0 次。
"""
import sqlite3
import sys
import threading
import time as _real_time

sys.path.insert(0, "backend")

from app.services import kpl                                  # noqa: E402

D_TEST = "2026-09-20"      # 补丁后的"今天"（周日，预热不会碰）→ 走 60s TTL
D_HIST = "2026-09-19"      # 历史日（周六，预热不会碰）→ 走 6h TTL
ROW = (D_HIST, "9_25", "600000", 1.5, 100.0, "甲公司", 1.0e9, 8.0e8, "机器人")


class _Clock:
    """可控时钟: 只覆盖 `time()`，其余转发真实模块"""

    def __init__(self, ts):
        self.now = ts

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(_real_time, name)


_DB_SEQ = {"n": 0}


def _mkdb(tmp_path, rows):
    _DB_SEQ["n"] += 1
    db = tmp_path / ("t%d.db" % _DB_SEQ["n"])        # 每次新建独立库名(同名会导致建表冲突)
    c = sqlite3.connect(str(db))
    c.execute("""CREATE TABLE snapshot_bid(
        date TEXT, time_point TEXT, code TEXT, bid_change REAL, bid_amt REAL,
        name TEXT, float_mv REAL, free_mv REAL, board TEXT)""")
    c.executemany("INSERT INTO snapshot_bid VALUES(?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return str(db)


def _setup(monkeypatch, tmp_path, rows, now=1000.0, today=D_TEST):
    """干净环境: 临时库 + 清空进程内缓存 + 可控时钟"""
    db = _mkdb(tmp_path, rows)
    monkeypatch.setattr(kpl.config, "DB_FILE", db)
    monkeypatch.setattr(kpl, "_SNAP25_CACHE", {})
    monkeypatch.setattr(kpl, "_bj_today", lambda: today)
    clock = _Clock(now)
    monkeypatch.setattr(kpl, "time", clock)
    return db, clock


def _count_db_connects(monkeypatch, db_path, only_main_thread=True):
    """统计指向该临时库的连接(**默认只算主线程**, 排除 conftest 起的后台预热线程)"""
    n = {"v": 0}
    real = sqlite3.connect
    main = threading.current_thread()

    def counting(database, *a, **kw):
        if str(database) == str(db_path):
            if not only_main_thread or threading.current_thread() is main:
                n["v"] += 1
        return real(database, *a, **kw)

    monkeypatch.setattr(kpl.sqlite3, "connect", counting)
    return n


# =========================================================================== #
# 一、★ 本次优化的回归测试
# =========================================================================== #
def test_three_lhb_fills_share_one_snapshot_query(monkeypatch, tmp_path):
    """★ 三次补全(竞价涨幅/流通市值/竞价换手)必须只查库一次 —— 原为三次全表查询共 103ms"""
    db, _ = _setup(monkeypatch, tmp_path, [ROW])
    n = _count_db_connects(monkeypatch, db)
    lst = [{"code": "600000"}]
    kpl.fill_bid_change_from_snap(lst, D_HIST)
    kpl.fill_float_mv_from_snap(lst, D_HIST)
    kpl.fill_bid_turnover_from_snap(lst, D_HIST)
    assert n["v"] == 1, "同一日期三次补全必须共用一次快照查询"
    assert lst[0]["bidChange"] == 1.5, "竞价涨幅仍须正确补上"
    assert lst[0]["floatMv"] == 1.0e9, "流通市值仍须正确补上"


def test_second_call_hits_cache_without_db(monkeypatch, tmp_path):
    """第二次取数不得再碰库(把 DB_FILE 指到不存在路径来证明真的走了缓存)"""
    _setup(monkeypatch, tmp_path, [ROW])
    first = kpl._snap25_map(D_HIST)
    assert first["600000"]["bid_change"] == 1.5
    monkeypatch.setattr(kpl.config, "DB_FILE", str(tmp_path / "不存在.db"))
    assert kpl._snap25_map(D_HIST) == first, "第二次必须命中缓存(否则会因 DB 不可用而变空)"


def test_does_not_use_shared_cache(monkeypatch, tmp_path):
    """★ 钉住"刻意不用共享 kv_cache" —— 若有人改回 `_cached`, 本用例即失败。

    理由见模块 docstring: 共享层 json 序列化+落库(set 49ms/get 18ms)超过一次查询(~35ms)。
    """
    _setup(monkeypatch, tmp_path, [ROW])
    touched = []
    monkeypatch.setattr(kpl, "_cached", lambda key, ttl, loader: touched.append(key) or loader())
    kpl._snap25_map(D_HIST)
    assert touched == [], "9_25 快照不得走共享缓存(序列化开销超过查询本身)"


# =========================================================================== #
# 二、TTL 与缓存键
# =========================================================================== #
def test_today_ttl_expires_after_60s(monkeypatch, tmp_path):
    """今日 60s: 过期后必须重查(9:25 采集/重采要及时反映)"""
    db, clock = _setup(monkeypatch, tmp_path, [(D_TEST, "9_25", "600000", 1.0, 1.0, "甲", 1.0, 1.0, "")])
    n = _count_db_connects(monkeypatch, db)
    kpl._snap25_map(D_TEST)
    clock.now += 30                                  # 60s 内 → 命中
    kpl._snap25_map(D_TEST)
    assert n["v"] == 1, "30s 时应命中缓存"
    clock.now += 40                                  # 累计 70s → 过期
    kpl._snap25_map(D_TEST)
    assert n["v"] == 2, "超过 60s 必须重查"


def test_historical_ttl_is_long(monkeypatch, tmp_path):
    """历史 6h: 5 小时后仍应命中"""
    db, clock = _setup(monkeypatch, tmp_path, [ROW])
    n = _count_db_connects(monkeypatch, db)
    kpl._snap25_map(D_HIST)
    clock.now += 5 * 3600
    kpl._snap25_map(D_HIST)
    assert n["v"] == 1, "历史快照 5 小时内应命中缓存"
    clock.now += 2 * 3600                            # 累计 7h → 过期
    kpl._snap25_map(D_HIST)
    assert n["v"] == 2, "超过 6h 必须重查"


def test_different_dates_do_not_collide(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path,
           [ROW, (D_TEST, "9_25", "600001", 9.9, 1.0, "乙", 1.0, 1.0, "")])
    kpl._snap25_map(D_HIST)
    kpl._snap25_map(D_TEST)
    assert "600000" in kpl._snap25_map(D_HIST)
    assert "600001" in kpl._snap25_map(D_TEST)
    assert "600001" not in kpl._snap25_map(D_HIST), "两个日期不得互相污染"


def test_date_none_resolves_to_freeze_day(monkeypatch, tmp_path):
    """date 空 → 定格基准日(既有语义), 缓存键也按解析后的日期"""
    _setup(monkeypatch, tmp_path, [ROW])
    monkeypatch.setattr(kpl, "freeze_day", lambda: D_HIST)
    assert "600000" in kpl._snap25_map(None)
    assert list(kpl._SNAP25_CACHE.keys()) == [D_HIST]


# =========================================================================== #
# 三、★ 空结果/异常不得缓存
# =========================================================================== #
def test_empty_result_not_cached(monkeypatch, tmp_path):
    """★ 该日期无快照(0 行) → 不得缓存。

    理由: 0 行查询本身极便宜, 而 9:25 采集落库后必须**立刻**可见 ——
    若把空结果缓存 60s, 9:25 后的竞价各页会整整一分钟拿不到数据。
    """
    db, _ = _setup(monkeypatch, tmp_path, [])
    n = _count_db_connects(monkeypatch, db)
    assert kpl._snap25_map(D_TEST) == {}
    assert kpl._SNAP25_CACHE == {}, "空结果不得进缓存"
    kpl._snap25_map(D_TEST)
    assert n["v"] == 2, "空结果必须每次都重查(才会在 9:25 落库后立刻可见)"


def test_failure_not_cached_and_degrades(monkeypatch, tmp_path):
    """查库异常 → 降级为空且不缓存, 不影响调用方"""
    _setup(monkeypatch, tmp_path, [ROW])
    monkeypatch.setattr(kpl.config, "DB_FILE", str(tmp_path / "不存在.db"))
    assert kpl._snap25_map(D_HIST) == {}
    assert kpl._SNAP25_CACHE == {}


def test_cache_size_is_bounded(monkeypatch, tmp_path):
    """缓存最多保留 _SNAP25_CACHE_MAX 个日期(每份约 1.7MB, 防止长期运行累积)"""
    _setup(monkeypatch, tmp_path, [ROW])
    for i in range(kpl._SNAP25_CACHE_MAX + 3):
        d = "2026-01-%02d" % (10 + i)                # 远期非交易日, 预热不会碰
        monkeypatch.setattr(kpl.config, "DB_FILE",
                            _mkdb(tmp_path, [(d, "9_25", "600000", 1.0, 1.0, "甲", 1.0, 1.0, "")]))
        kpl._snap25_map(d)
    assert len(kpl._SNAP25_CACHE) <= kpl._SNAP25_CACHE_MAX, "进程内缓存必须有上限"
