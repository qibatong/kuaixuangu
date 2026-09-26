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

    # ---------------- 过期行回收(2026-09-26 v4.11.53) ----------------
    def test_purge_expired_removes_only_expired(self, cs):
        """回收过期行 —— 读侧本来就取不到(`_alive` 判过期即返回 default), 故零行为影响

        生产实测背景: kv_cache 3885 行里 3802 行(97.9%)早已过期仍在库, 最早的在 40 天前。
        根因是 `get()` 判过期只返回 default、**从不删行**, 而 `clear_prefix()` 虽有实现
        却**从来没有任何调度调用过它** ⇒ 表单调增长(日调度键 + kpl 逐股缓存 + 限流窗口)。
        """
        cs.set("alive", 1, ttl=3600)
        cs.set("forever", 2)                 # ttl=0 ⇒ expire_at=0 ⇒ **永不过期**, 不得被清
        cs.set("dead", 3, ttl=1)
        time.sleep(1.2)
        assert cs.purge_expired() == 1, "只删那 1 行过期的"
        assert cs.get("alive") == 1
        assert cs.get("forever") == 2, "永久键(expire_at=0)绝不能被清掉"
        assert cs.get("dead") is None

    def test_purge_expired_noop_when_none_expired(self, cs):
        cs.set("a", 1, ttl=3600)
        cs.set("b", 2)
        assert cs.purge_expired() == 0

    def test_purge_expired_idempotent(self, cs):
        cs.set("dead", 3, ttl=1)
        time.sleep(1.2)
        assert cs.purge_expired() == 1
        assert cs.purge_expired() == 0, "第二次已无行可删"


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

    def test_purge_expired_noop(self):
        """Redis 由服务端按 TTL 物理删除 ⇒ 无行可回收(返回 0 以统一调用方口径)"""
        st = RedisCacheStore()
        assert st.purge_expired() == 0


def test_create_store_default_sqlite():
    st = create_store()
    assert isinstance(st, SqliteCacheStore)
