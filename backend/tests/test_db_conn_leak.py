# -*- coding: utf-8 -*-
"""DB 连接泄漏防回归(2026-09-09 生产事故)

事故: `with sqlite3.connect(...) as conn` **只管事务(commit/rollback), 不关连接**
—— sqlite3.Connection.__exit__ 不调 close。cache_store 是全站最高频 DB 调用
(kpl 逐股板块缓存/抢筹缓存/信号量), 开盘并发下每次泄漏一个 fd:
uvicorn 进程 open_fd 打满 ulimit 1024(实测 997 个是 kuaixuan.db 句柄) →
"unable to open database file" + "database is locked" 全站报错, 首页竞价无数据。

本文件锁定两个不变量:
  1) CacheStore 每次调用后连接必须关闭(不能只靠 `with conn`)
  2) database.get_conn 必须带 busy_timeout(写者排队而非立刻失败)
"""
import sqlite3

import pytest

from app.db import database
from app.services import cache_store


def _is_closed(conn):
    try:
        conn.execute("SELECT 1")
        return False
    except sqlite3.ProgrammingError:
        return True
    except Exception:
        return True          # 已关闭的其他表现


@pytest.fixture
def spy_connections(monkeypatch, tmp_path):
    """收集 CacheStore 打开的所有连接, 便于事后断言是否全部关闭"""
    opened = []
    real = cache_store.sqlite3.connect

    def spy(*a, **kw):
        conn = real(*a, **kw)
        opened.append(conn)
        return conn

    monkeypatch.setattr(cache_store.sqlite3, "connect", spy)
    return opened, str(tmp_path / "cache.db")


def test_cache_store_closes_every_connection(spy_connections):
    """set/get/incr/setnx/sem 全部走完 → 每个连接都已 close(不泄漏 fd)"""
    opened, dbfile = spy_connections
    st = cache_store.SqliteCacheStore(db_file=dbfile)
    st.set("k1", {"a": 1}, 30)
    st.get("k1")
    st.incr("n1", 30)
    st.setnx("x1", 1, 30)
    lk = st.acquire_sem("s1", limit=2, timeout=1)
    if lk:
        st.release_lock(lk)
    st.delete("k1")

    assert len(opened) >= 5, "连接数异常少, 断言失去意义: %d" % len(opened)
    leaked = [c for c in opened if not _is_closed(c)]
    assert not leaked, "泄漏 %d/%d 个连接(未 close → fd 累积打满 ulimit)" % (
        len(leaked), len(opened))


def test_cache_store_many_calls_still_closed(spy_connections):
    """高频调用 200 次(模拟开盘逐股板块缓存)后, 累计连接全部关闭"""
    opened, dbfile = spy_connections
    st = cache_store.SqliteCacheStore(db_file=dbfile)
    for i in range(200):
        st.setnx("kpl:stock_plate_%06d:lock" % i, 1, 30)
    assert len(opened) >= 200
    leaked = [c for c in opened if not _is_closed(c)]
    assert not leaked, "高频调用泄漏 %d 个连接" % len(leaked)


def test_cache_store_value_roundtrip(spy_connections):
    """修 close 不能改语义: 写入仍能读回, 且过期后失效"""
    opened, dbfile = spy_connections
    st = cache_store.SqliteCacheStore(db_file=dbfile)
    st.set("rt", {"v": 42}, 60)
    assert st.get("rt") == {"v": 42}
    assert st.get("missing", "dft") == "dft"
    st.set("ttl", 1, -1)                 # 已过期
    assert st.get("ttl") is None


def test_get_conn_has_busy_timeout():
    """get_conn 必须带 timeout: 高并发写冲突排队, 不立刻抛 database is locked"""
    conn = database.get_conn()
    try:
        n = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert n >= 5000, "busy_timeout=%s 太小, 写冲突会立刻失败" % n
    finally:
        conn.close()
