# -*- coding: utf-8 -*-
"""
跨进程共享缓存/限流/计数/锁 (CacheStore)
========================================
背景: 单体 workers=1 时缓存/限流/调度标记都在进程内, 多 worker 后:
  - 缓存 ×N → KPL 付费配额 ×N 触发限流
  - 限流计数独立 → 失效
  - 调度标记独立 → 重复采集
目标: 状态外置 → Redis(生产容器) / SQLite 表(测试/兜底, 零依赖)

用法:
    from ..services.cache_store import store
    store.get(key) / store.set(key, val, ttl) / store.delete(key)
    store.incr(key, ttl)                  # 固定窗口原子自增(限流/配额)
    store.setnx(key, value, ttl)          # 仅不存在时写入(调度去重)
    lk = store.acquire_sem(name, limit)   # 分布式信号量 → 锁 key | None
    store.release_lock(lk)

切换: config.CACHE_BACKEND = redis | sqlite (默认 sqlite, 零依赖)
所有方法失败降级(返回 None/False), 绝不抛异常中断主流程。
"""
import json
import sqlite3
import threading
import time

try:
    import redis as _redis
    _HAS_REDIS = True
except ImportError:          # 测试机无 redis-py: 自动走 sqlite, 不炸
    _redis = None
    _HAS_REDIS = False

from ..core import config, logger

log = logger.get_logger(__name__)

# SQLite 写操作进程内串行化(单文件 DB 单写者; 跨进程由 busy_timeout 兜底)
_DB_LOCK = threading.Lock()


class CacheStore:
    """抽象接口: 所有方法失败降级, 保证主流程不中断"""

    def get(self, key, default=None):
        raise NotImplementedError

    def set(self, key, value, ttl=0):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def clear_prefix(self, prefix):
        raise NotImplementedError

    def incr(self, key, ttl=0):
        """固定窗口原子自增(限流/配额计数): 首次创建并设 TTL, 后续只 +1 不刷 TTL"""
        raise NotImplementedError

    def setnx(self, key, value=1, ttl=0):
        """SET NX EX: 仅当 key 不存在时写入; 返回 True=本次设置成功(调度去重用)"""
        raise NotImplementedError

    def acquire_sem(self, name, limit=3, timeout=10, expire=20):
        """分布式信号量: 从 name 的 limit 个槽位中抢一个; 成功返回锁 key, 超时返回 None"""
        raise NotImplementedError

    def release_lock(self, key):
        raise NotImplementedError


class SqliteCacheStore(CacheStore):
    """SQLite 实现: 表 kv_cache(key PK, val, expire_at), 零依赖"""

    def __init__(self, db_file=None):
        self.db_file = db_file or config.DB_FILE
        self._ensure_table()

    def _ensure_table(self):
        try:
            with self._conn() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS kv_cache (
                    key TEXT PRIMARY KEY,
                    val TEXT NOT NULL DEFAULT '',
                    expire_at INTEGER NOT NULL DEFAULT 0
                )""")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kv_expire ON kv_cache(expire_at)")
        except Exception as e:
            log.warning("kv_cache 建表失败 err=%s", e)

    def _conn(self):
        conn = sqlite3.connect(self.db_file, timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _alive(row, now):
        return row is not None and (not row[1] or row[1] > now)

    def get(self, key, default=None):
        try:
            now = int(time.time())
            with self._conn() as conn:
                row = conn.execute("SELECT val, expire_at FROM kv_cache WHERE key=?", (key,)).fetchone()
                if not self._alive(row, now):
                    return default
                return json.loads(row[0])
        except Exception:
            return default

    def set(self, key, value, ttl=0):
        try:
            exp = int(time.time()) + int(ttl) if ttl else 0
            data = json.dumps(value, ensure_ascii=False)
            with _DB_LOCK, self._conn() as conn:
                conn.execute("INSERT OR REPLACE INTO kv_cache(key, val, expire_at) VALUES(?,?,?)",
                             (key, data, exp))
        except Exception as e:
            log.warning("cache set 失败 key=%s err=%s", key, e)

    def delete(self, key):
        try:
            with _DB_LOCK, self._conn() as conn:
                conn.execute("DELETE FROM kv_cache WHERE key=?", (key,))
        except Exception:
            pass

    def clear_prefix(self, prefix):
        try:
            with _DB_LOCK, self._conn() as conn:
                conn.execute("DELETE FROM kv_cache WHERE key LIKE ?", (prefix + "%",))
        except Exception:
            pass

    def incr(self, key, ttl=0):
        try:
            now = int(time.time())
            with _DB_LOCK, self._conn() as conn:
                row = conn.execute("SELECT val, expire_at FROM kv_cache WHERE key=?", (key,)).fetchone()
                if self._alive(row, now):
                    cur = int(row[0] or 0) + 1
                    exp = row[1]            # 固定窗口: 保留首次 TTL
                else:
                    cur = 1
                    exp = now + int(ttl) if ttl else 0
                conn.execute("INSERT OR REPLACE INTO kv_cache(key, val, expire_at) VALUES(?,?,?)",
                             (key, str(cur), exp))
            return cur
        except Exception as e:
            log.warning("cache incr 失败 key=%s err=%s", key, e)
            return 0

    def setnx(self, key, value=1, ttl=0):
        try:
            now = int(time.time())
            with _DB_LOCK, self._conn() as conn:
                row = conn.execute("SELECT expire_at FROM kv_cache WHERE key=?", (key,)).fetchone()
                if row and (not row[0] or row[0] > now):
                    return False
                if row:
                    conn.execute("DELETE FROM kv_cache WHERE key=?", (key,))
                exp = now + int(ttl) if ttl else 0
                conn.execute("INSERT OR REPLACE INTO kv_cache(key, val, expire_at) VALUES(?,?,?)",
                             (key, str(value), exp))
            return True
        except Exception as e:
            log.warning("cache setnx 失败 key=%s err=%s", key, e)
            return False

    def acquire_sem(self, name, limit=3, timeout=10, expire=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i in range(limit):
                lk = "sem:%s:%d" % (name, i)
                if self.setnx(lk, 1, ttl=expire):
                    return lk
            time.sleep(0.05)
        return None

    def release_lock(self, key):
        self.delete(key)


class RedisCacheStore(CacheStore):
    """Redis 实现(生产多 worker 共享). 连接失败时 create_store 捕获降级 sqlite"""

    def __init__(self, url=None):
        self.r = _redis.Redis.from_url(url or config.REDIS_URL, decode_responses=True)
        self.r.ping()   # 连接自检, 失败抛异常

    def get(self, key, default=None):
        try:
            v = self.r.get(key)
            return json.loads(v) if v is not None else default
        except Exception:
            return default

    def set(self, key, value, ttl=0):
        try:
            self.r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl or None)
        except Exception as e:
            log.warning("redis set 失败 key=%s err=%s", key, e)

    def delete(self, key):
        try:
            self.r.delete(key)
        except Exception:
            pass

    def clear_prefix(self, prefix):
        try:
            for k in self.r.scan_iter(match=prefix + "*"):
                self.r.delete(k)
        except Exception:
            pass

    def incr(self, key, ttl=0):
        try:
            n = self.r.incr(key)
            if n == 1 and ttl:
                self.r.expire(key, ttl)
            return int(n)
        except Exception as e:
            log.warning("redis incr 失败 key=%s err=%s", key, e)
            return 0

    def setnx(self, key, value=1, ttl=0):
        try:
            return bool(self.r.set(key, str(value), nx=True, ex=ttl or None))
        except Exception as e:
            log.warning("redis setnx 失败 key=%s err=%s", key, e)
            return False

    def acquire_sem(self, name, limit=3, timeout=10, expire=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i in range(limit):
                lk = "sem:%s:%d" % (name, i)
                if self.r.set(lk, "1", nx=True, ex=expire):
                    return lk
            time.sleep(0.05)
        return None

    def release_lock(self, key):
        try:
            self.r.delete(key)
        except Exception:
            pass


# ---- 跨进程 single-flight(缓存击穿保护) 2026-09-04 ----
# 背景: 首屏 11 并发请求(页面 loadAll)同刻 cache miss → N 个线程同时调 loader 打外网,
#       每个再抢 KPL sem(limit=3) → 排队累积 1.7-2.0s(生产 nginx maxRt 实测)
# 方案: miss 时 setnx 抢"加载锁"(原子, 同进程内严格; 跨进程 sqlite 尽力而为最坏 2 次),
#       抢到者调 loader 写缓存, 未抢到者轮询等缓存(≤ 锁超时), 从 N 次外网降到 1-2 次
_SF_WAIT = 15.0          # 未抢到锁的等待上限(秒); loader 网络超时 ≤12s 可覆盖
_SF_POLL = 0.03          # 轮询间隔(秒)


def cached_singleflight(store_obj, key, ttl, loader, wait=None, lock_ttl=None):
    """缓存读取 + 击穿保护:
    命中直接返回; miss 时抢锁(singleflight), 抢到者 loader 并写缓存;
    其余请求等待缓存写入后返回(最多 wait 秒), loader 失败返回 None 不缓存。
    key 建议含命名空间前缀(如 'kpl:' / 'ao:'), 锁 key 自动追加 ':lock'。

    2026-09-04 v2: 等待者的降级路径(锁消失/超时)也必须先抢锁再 loader —
    否则多个等待者同时超时/同时发现锁消失会各自 loader(N 次外网), 违背
    singleflight 语义(测试并发 miss 仅 1 次 loader 曾偶发 2 次暴露此缺陷)。
    """
    v = store_obj.get(key)
    if v is not None:
        return v
    lk = key + ":lock"
    lttl = lock_ttl or (max(int(ttl * 2), 60) if ttl else 60)

    def _locked_load():
        """已持锁: 双检缓存后 loader 并写缓存(全程持锁, finally 释放)"""
        try:
            v = store_obj.get(key)
            if v is not None:
                return v
            data = loader()
            if data is not None:
                store_obj.set(key, data, ttl)
            return data
        finally:
            store_obj.delete(lk)

    if store_obj.setnx(lk, 1, ttl=lttl):
        return _locked_load()
    # 未抢到锁: 轮询等持有者写缓存
    deadline = time.time() + (wait if wait is not None else _SF_WAIT)
    while time.time() < deadline:
        time.sleep(_SF_POLL)
        v = store_obj.get(key)
        if v is not None:
            return v
        # 锁已消失但无缓存(持有者 loader 失败/异常/超时) → 抢锁兜底加载
        # (先抢锁再 loader: 防止多个等待者同刻发现锁消失而各自打外网)
        if store_obj.get(lk) is None and store_obj.setnx(lk, 1, ttl=lttl):
            return _locked_load()
    # 等待超时兜底: 同样先抢锁(可能锁已释放), 抢不到则直接加载不写缓存
    if store_obj.setnx(lk, 1, ttl=lttl):
        return _locked_load()
    return loader()


def create_store():
    """按 config.CACHE_BACKEND 创建单例; redis 不可用时自动降级 sqlite"""
    if getattr(config, "CACHE_BACKEND", "sqlite") == "redis" and _HAS_REDIS:
        try:
            st = RedisCacheStore()
            log.info("CacheStore: Redis 就绪 (%s)", config.REDIS_URL)
            return st
        except Exception as e:
            log.warning("Redis 不可用(%s), 降级 SqliteCacheStore", e)
    return SqliteCacheStore()


# 全局单例: 全项目统一入口
store = create_store()
