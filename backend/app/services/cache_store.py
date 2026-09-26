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
from contextlib import contextmanager

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

    def purge_expired(self):
        """回收**已过期**的条目, 返回删除行数(Redis 自带 TTL ⇒ 恒 0)。

        2026-09-26 新增。为什么需要它: `get()` 判过期只**返回 default、从不删行** ——
        生产 kv_cache 实测 3885 行里 3802 行(97.9%)早已过期仍在库, 最早的在 40 天前;
        而 `clear_prefix()` 虽有实现却**从来没有任何调度调用过它**, 只能靠人工。
        表因此单调增长(每日调度键 + kpl 逐股板块缓存 + 限流窗口 + 信号量槽)。

        安全性: 被删的行**读侧本来就取不到**(`_alive` 判过期即返回 default) ⇒
        **零行为影响**。唯一代价是一次批量 DELETE, 故只挂在每交易日收盘后的调度窗口。
        """
        return 0

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
            with self._tx() as conn:
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

    @contextmanager
    def _tx(self):
        """事务上下文(必须走它, 不许 `with self._conn() as conn`)

        2026-09-09 生产事故: `with sqlite3.connect(...) as conn` **只管事务
        (commit/rollback), 不关连接** —— sqlite3.Connection 的 __exit__ 不调 close。
        本类是全站最高频 DB 调用(kpl 逐股板块缓存/抢筹缓存/信号量), 开盘并发下
        每次调用泄漏一个 fd: uvicorn 进程 open_fd 打满 ulimit 1024 →
        "unable to open database file" + "database is locked" 全站报错, 首页竞价无数据。
        故这里显式 close, 保留原有事务语义(无异常 commit / 异常 rollback)。
        """
        conn = self._conn()
        try:
            with conn:
                yield conn
        finally:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass

    @staticmethod
    def _alive(row, now):
        return row is not None and (not row[1] or row[1] > now)

    def get(self, key, default=None):
        try:
            now = int(time.time())
            with self._tx() as conn:
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
            with _DB_LOCK, self._tx() as conn:
                conn.execute("INSERT OR REPLACE INTO kv_cache(key, val, expire_at) VALUES(?,?,?)",
                             (key, data, exp))
        except Exception as e:
            log.warning("cache set 失败 key=%s err=%s", key, e)

    def delete(self, key):
        try:
            with _DB_LOCK, self._tx() as conn:
                conn.execute("DELETE FROM kv_cache WHERE key=?", (key,))
        except Exception:
            pass

    def clear_prefix(self, prefix):
        try:
            with _DB_LOCK, self._tx() as conn:
                conn.execute("DELETE FROM kv_cache WHERE key LIKE ?", (prefix + "%",))
        except Exception:
            pass

    def purge_expired(self):
        """删除过期行(`expire_at > 0 AND expire_at <= now`), 返回删除行数。

        ⚠ 只删 `expire_at > 0` 的: `0` 在本模块语义是"**永不过期**"
          (见 `set()` —— `ttl=0` 即写 0), 不能用 `< now` 一概而论, 否则会把
          永久键(`layout:` / `settings:` 类)一起清掉。
        """
        try:
            now = int(time.time())
            with _DB_LOCK, self._tx() as conn:
                cur = conn.execute(
                    "DELETE FROM kv_cache WHERE expire_at > 0 AND expire_at <= ?", (now,))
                return cur.rowcount or 0
        except Exception as e:
            log.warning("cache purge_expired 失败 err=%s", e)
            return 0

    def incr(self, key, ttl=0):
        try:
            now = int(time.time())
            with _DB_LOCK, self._tx() as conn:
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
            with _DB_LOCK, self._tx() as conn:
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

    def purge_expired(self):
        """Redis 的 TTL 到期即由服务端物理删除 ⇒ 无需回收(返回 0 以统一调用方口径)。"""
        return 0

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
