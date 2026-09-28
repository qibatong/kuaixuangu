# -*- coding: utf-8 -*-
"""昨比跨进程 single-flight(2026-09-29 P0-c)测试。

背景: `fetcher._yday_batch_lock` 是**进程内**锁, 而生产 uvicorn 是 `--workers 2`
⇒ 两个进程各持一把, 同时都认为"该我拉", 于是同一批 need(全市场 5561 只 ÷ 500/片
≈ 12 片猫爪 `daily`)**被两个进程各拉一遍**。2026-09-28 实测的 429(web 进程 / a=daily)
里就剩这一半没治(另一半「分片顺序不稳 ⇒ 缓存打不中」已由分片排序消除)。

本文件只锁**新增的跨进程令牌语义**, 不重测拉取本身:
  ① 抢不到令牌 ⇒ 本轮跳过: 不拉取、不起线程, 且**进程内锁必须放回**(否则本进程永久卡死);
  ② 抢到令牌 ⇒ 令牌键名随线程一起走, 由线程在 finally 释放(两把锁同生共死 —— 否则会出现
     "进程内锁已放、跨进程令牌仍被占", 另一 worker 白等到 TTL 过期);
  ③ 同步路径(wait=True) ⇒ **等**令牌, 拿锁后复查 need, 另一进程已填则一次都不拉;
  ④ 异步路径的等待必须是**极小正数** —— `acquire_sem(timeout=0)` 因循环是
     `while time.time() < deadline` 会一次都不尝试、永远返回 None(等于永久关掉异步昨比)。
     这条是真实踩过的坑: tests/test_yesterday_cache 先抓到, 本文件把它钉成断言。
"""
import threading

import pytest

from app.services import fetcher

# conftest 的 session 级 mock_data_source 会把 fetcher.fetch_yesterday_amounts 整体换掉,
# 本文件要测**真实实现** ⇒ 模块导入时留存原始入口(与 test_yesterday_cache 同法)。
_REAL_FETCH = fetcher.fetch_yesterday_amounts


@pytest.fixture(autouse=True)
def _use_real_fetch(monkeypatch):
    """恢复真实 fetch_yesterday_amounts(覆盖 session 级 mock)。"""
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", _REAL_FETCH)
    yield


class _FakeStore:
    """只实现本模块用到的两个方法(避免碰真实 cache_store 落盘)。"""

    def __init__(self, sem="sem:yday:0"):
        self._sem = sem
        self.acquired = []
        self.released = []

    def acquire_sem(self, name, limit=3, timeout=10, expire=20):
        self.acquired.append({"name": name, "limit": limit,
                              "timeout": timeout, "expire": expire})
        return self._sem

    def release_lock(self, key):
        self.released.append(key)


def _patch_need(monkeypatch, need):
    """把「要不要拉」收敛成固定值: 读库没命中 ⇒ need 非空。"""
    monkeypatch.setattr(fetcher, "_yday_hydrate_from_db", lambda codes, today, now: [])
    monkeypatch.setattr(fetcher, "_collect_yday_need", lambda pending, today, now: list(need))
    monkeypatch.setattr(fetcher, "_meoz_enabled", lambda: True)


@pytest.fixture(autouse=True)
def _unlock_batch_lock():
    """失败用例不得把进程内批锁留给后面的用例(否则后续集体卡死, 极难排查)。"""
    yield
    if fetcher._yday_batch_lock.locked():
        fetcher._yday_batch_lock.release()


def test_async_skips_when_token_held_by_other_worker(monkeypatch):
    """另一 worker 持令牌 ⇒ 本轮跳过: 不拉取、不起线程, 且进程内锁必须放回。"""
    store = _FakeStore(sem=None)
    monkeypatch.setattr(fetcher, "store", store)
    _patch_need(monkeypatch, ["600001"])
    monkeypatch.setattr(fetcher, "_do_fetch_yesterday",
                        lambda *a, **k: pytest.fail("抢不到跨进程令牌时不得拉取"))
    monkeypatch.setattr(fetcher, "_yday_background_fetch",
                        lambda *a, **k: pytest.fail("抢不到跨进程令牌时不得起线程"))

    _REAL_FETCH(["600001"])

    assert store.acquired and store.acquired[0]["name"] == "yday"
    assert not fetcher._yday_batch_lock.locked(), "跳过时必须把进程内锁放回"


def test_async_wait_is_tiny_but_positive(monkeypatch):
    """异步路径的等待必须是**极小正数**。

    🔴 `CacheStore.acquire_sem` 的实现是 `while time.time() < deadline:` —— 传 timeout=0
    时 deadline==now, 循环体一次都不执行, **永远返回 None** ⇒ 异步昨比会被永久跳过。
    这正是一开始写错的形态(由 tests/test_yesterday_cache 抓到)。
    """
    store = _FakeStore(sem=None)
    monkeypatch.setattr(fetcher, "store", store)
    _patch_need(monkeypatch, ["600001"])
    monkeypatch.setattr(fetcher, "_yday_background_fetch", lambda *a, **k: None)

    _REAL_FETCH(["600001"])

    t = store.acquired[0]["timeout"]
    assert t > 0, "timeout=0 会让 acquire_sem 一次都不尝试 ⇒ 异步昨比被永久关掉"
    assert t <= 0.2, "异步(用户请求)路径不该真的等令牌"


def test_async_passes_token_to_background_thread(monkeypatch):
    """抢到令牌 ⇒ 令牌键名连同 need 一起交给后台线程(由它负责释放)。"""
    store = _FakeStore()
    monkeypatch.setattr(fetcher, "store", store)
    _patch_need(monkeypatch, ["600001"])
    got = {}

    class _T(threading.Thread):
        def __init__(self, target=None, args=(), kwargs=None, **kw):
            super().__init__(target=target, args=args, kwargs=kwargs or {}, **kw)
            got["args"] = args

        def start(self):                 # 不起真线程: 避免用例间竞态
            got["started"] = True

    monkeypatch.setattr(fetcher.threading, "Thread", _T)
    _REAL_FETCH(["600001"])

    assert got.get("started") is True
    assert got["args"][2] == "sem:yday:0", "跨进程令牌必须传给后台线程"
    assert fetcher._yday_batch_lock.locked(), "线程未跑完前进程内锁应仍被持有"


def test_background_fetch_releases_token_and_lock(monkeypatch):
    """两把锁同生共死: 线程结束必须同时放掉进程内锁与跨进程令牌。"""
    store = _FakeStore()
    monkeypatch.setattr(fetcher, "store", store)
    monkeypatch.setattr(fetcher, "_do_fetch_yesterday", lambda need, today: (len(need), 0))

    fetcher._yday_batch_lock.acquire()                     # 模拟调用方已加锁
    fetcher._yday_background_fetch(["600001"], "2026-09-29", "sem:yday:0")

    assert store.released == ["sem:yday:0"]
    assert not fetcher._yday_batch_lock.locked()


def test_background_fetch_without_token_still_releases_lock(monkeypatch):
    """向后兼容: tok=None(旧调用方/直接调用)时只放进程内锁, 不报错。"""
    store = _FakeStore()
    monkeypatch.setattr(fetcher, "store", store)
    monkeypatch.setattr(fetcher, "_do_fetch_yesterday", lambda need, today: (len(need), 0))

    fetcher._yday_batch_lock.acquire()
    fetcher._yday_background_fetch(["600001"], "2026-09-29")

    assert store.released == []
    assert not fetcher._yday_batch_lock.locked()


def test_sync_path_waits_then_skips_when_other_worker_already_filled(monkeypatch):
    """同步路径: 等令牌(阻塞) → 复查 need → 另一进程已填 ⇒ 一次都不拉, 并释放令牌。"""
    store = _FakeStore()
    monkeypatch.setattr(fetcher, "store", store)
    monkeypatch.setattr(fetcher, "_yday_hydrate_from_db", lambda codes, today, now: [])
    monkeypatch.setattr(fetcher, "_meoz_enabled", lambda: True)
    calls = {"n": 0}

    def _collect(pending, today, now):
        calls["n"] += 1
        return ["600001"] if calls["n"] == 1 else []       # 拿锁后复查 → 已被别人填过

    monkeypatch.setattr(fetcher, "_collect_yday_need", _collect)
    monkeypatch.setattr(fetcher, "_do_fetch_yesterday",
                        lambda *a, **k: pytest.fail("need 复查为空时不得拉取"))

    _REAL_FETCH(["600001"], wait=True)

    assert store.acquired[0]["timeout"] > 0, "同步(后台任务)路径必须等令牌"
    assert store.released == ["sem:yday:0"]
    assert not fetcher._yday_batch_lock.locked()


def test_token_ttl_is_above_typical_fetch():
    """令牌 TTL 必须够长(12 片全市场量级), 否则会在拉取中途过期 ⇒ 单飞失效(又变并发)。"""
    assert fetcher._YDAY_SEM_TTL >= 120


def test_batch_lock_is_still_the_inner_guard():
    """进程内批锁必须保留(跨进程令牌是**外套**, 不是替代品): 同进程并发请求仍靠它挡。"""
    assert isinstance(fetcher._yday_batch_lock, type(threading.Lock()))
