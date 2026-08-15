# -*- coding: utf-8 -*-
"""CacheStore 单元测试: SqliteCacheStore(默认) + RedisCacheStore(可选)"""
import os
import tempfile
import time

import pytest

from app.services.cache_store import SqliteCacheStore, create_store

if os.environ.get("TEST_REDIS"):
    try:
        from app.services.cache_store import RedisCacheStore
    except Exception:
        RedisCacheStore = None
else:
    RedisCacheStore = None


@pytest.fixture()
def cs():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        pass
    st = SqliteCacheStore(path)
    yield st
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        os.unlink(path + "-wal")
    except OSError:
        pass
    try:
        os.unlink(path + "-shm")
    except OSError:
        pass


class TestSqliteCacheStore:
    def test_set_get(self, cs):
        cs.set("k", {"a": 1})
        assert cs.get("k") == {"a": 1}
        assert cs.get("missing", "dft") == "dft"

    def test_ttl_expire(self, cs):
        cs.set("k", 42, ttl=1)
        assert cs.get("k") == 42
        time.sleep(1.2)
        assert cs.get("k") is None

    def test_delete(self, cs):
        cs.set("k", 1)
        cs.delete("k")
        assert cs.get("k") is None

    def test_clear_prefix(self, cs):
        cs.set("kpl:a", 1)
        cs.set("kpl:b", 2)
        cs.set("other", 3)
        cs.clear_prefix("kpl:")
        assert cs.get("kpl:a") is None
        assert cs.get("kpl:b") is None
        assert cs.get("other") == 3

    def test_incr_fixed_window(self, cs):
        # 固定窗口: 首次设 TTL, 后续不刷
        cs.incr("rate:1", ttl=60)
        cs.incr("rate:1", ttl=60)
        assert cs.incr("rate:1", ttl=60) == 3
        # 过期后重新计数
        cs.set("rate:2", 1, ttl=0)
        cs.delete("rate:2")
        assert cs.incr("rate:2", ttl=60) == 1

    def test_setnx(self, cs):
        assert cs.setnx("done:x", 1, ttl=60) is True
        assert cs.setnx("done:x", 1, ttl=60) is False  # 已存在
        cs.delete("done:x")
        assert cs.setnx("done:x", 1, ttl=60) is True

    def test_semaphore(self, cs):
        l1 = cs.acquire_sem("kpl", limit=2, timeout=1)
        l2 = cs.acquire_sem("kpl", limit=2, timeout=1)
        assert l1 is not None and l2 is not None
        # 槽位满 → 超时拿不到
        l3 = cs.acquire_sem("kpl", limit=2, timeout=0.3)
        assert l3 is None
        # 释放后能再拿
        cs.release_lock(l2)
        l4 = cs.acquire_sem("kpl", limit=2, timeout=1)
        assert l4 is not None

    def test_json_roundtrip(self, cs):
        v = [{"code": "688062", "list": [1, 2, 3], "name": "富信科技"}]
        cs.set("kpl:stock_plate:688062", v, ttl=300)
        assert cs.get("kpl:stock_plate:688062") == v


@pytest.mark.skipif(RedisCacheStore is None, reason="redis-py 未安装")
class TestRedisCacheStore:
    def test_set_get(self):
        st = RedisCacheStore()
        st.set("t:k", [1, 2, 3])
        assert st.get("t:k") == [1, 2, 3]
        st.delete("t:k")

    def test_incr(self):
        st = RedisCacheStore()
        st.delete("t:r")
        st.incr("t:r", ttl=60)
        assert st.incr("t:r", ttl=60) == 2
        st.delete("t:r")


def test_create_store_default_sqlite():
    st = create_store()
    assert isinstance(st, SqliteCacheStore)
