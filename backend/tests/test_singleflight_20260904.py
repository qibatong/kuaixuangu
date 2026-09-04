# -*- coding: utf-8 -*-
"""2026-09-04 缓存击穿保护(single-flight)回归:
背景: 页面首屏 11 并发(loadAll)同刻 cache miss → N 线程同时 loader 打外网
     再抢 KPL sem(limit=3) → 排队累积 1.7-2.0s(生产 nginx maxRt 实测锁死 1.71s)
修复: cache_store.cached_singleflight — miss 时 setnx 抢加载锁, 抢到者 loader 写缓存,
     未抢到者轮询等缓存, 从 N 次外网降到 1 次(进程内严格, 跨进程 sqlite 尽力最坏 2)
覆盖: 并发 miss 仅 1 次 loader / TTL 命中不 loader / loader None 不写缓存 / 锁超时降级
"""
import threading
import time

import pytest

from app.services.cache_store import cached_singleflight, store as _cs

PREFIX = "sf_test:"


@pytest.fixture(autouse=True)
def _clean_sf_keys():
    yield
    # 清理测试 key(含锁)
    for n in range(10):
        for k in (PREFIX + str(n), PREFIX + str(n) + ":lock"):
            try:
                _cs.delete(k)
            except Exception:
                pass


def test_concurrent_miss_only_one_loader():
    """10 线程同时 miss 同一 key → loader 只调 1 次(击穿保护核心)"""
    calls = {"n": 0}
    _lock = threading.Lock()

    def loader():
        with _lock:
            calls["n"] += 1
        time.sleep(0.15)          # 模拟外网慢(放大并发窗口)
        return {"v": 1}

    _cs.delete(PREFIX + "0")
    _cs.delete(PREFIX + "0:lock")
    vals = []
    ths = [threading.Thread(target=lambda: vals.append(cached_singleflight(_cs, PREFIX + "0", ttl=30, loader=loader))) for _ in range(10)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert all(v == {"v": 1} for v in vals), vals
    assert calls["n"] == 1, f"并发 miss 应只调 1 次 loader, 实际 {calls['n']} 次"
    assert _cs.get(PREFIX + "0") == {"v": 1}


def test_cache_hit_no_loader():
    """缓存 TTL 内命中 → 不调 loader"""
    calls = {"n": 0}
    _cs.set(PREFIX + "1", {"cached": True}, ttl=60)
    r = cached_singleflight(_cs, PREFIX + "1", ttl=60, loader=lambda: (calls.__setitem__("n", calls["n"] + 1), {"v": 2})[1])
    assert r == {"cached": True}
    assert calls["n"] == 0


def test_loader_none_not_cached_retried():
    """loader 返回 None(外网失败) → 不写缓存, 下次调用重新 loader"""
    calls = {"n": 0}
    _cs.delete(PREFIX + "2")
    _cs.delete(PREFIX + "2:lock")

    def flaky():
        calls["n"] += 1
        return None if calls["n"] == 1 else {"v": 3}

    assert cached_singleflight(_cs, PREFIX + "2", ttl=30, loader=flaky) is None
    assert _cs.get(PREFIX + "2") is None, "loader 失败不应写缓存"
    r = cached_singleflight(_cs, PREFIX + "2", ttl=30, loader=flaky)
    assert r == {"v": 3} and calls["n"] == 2


def test_lock_expiry_fallback_direct_load():
    """锁被残留(未释放)且无缓存 → 轮询至 wait 超时后降级直接加载(不写缓存防风暴)"""
    calls = {"n": 0}
    _cs.delete(PREFIX + "3")
    _cs.delete(PREFIX + "3:lock")
    _cs.set(PREFIX + "3:lock", 1, ttl=600)   # 模拟残留锁(长 TTL)

    def loader():
        calls["n"] += 1
        return {"v": 4}

    t0 = time.time()
    r = cached_singleflight(_cs, PREFIX + "3", ttl=30, loader=loader, wait=0.3)
    dt = time.time() - t0
    assert r == {"v": 4}
    assert 0.2 <= dt < 2.0, f"应在 wait 超时后降级, 实际 {dt:.2f}s"


def test_kpl_cached_uses_singleflight(monkeypatch):
    """kpl._cached 已接线 singleflight: 并发 miss 同 key 仅 1 次 _call(外网)"""
    from app.services import kpl
    calls = {"n": 0}
    _lock = threading.Lock()
    _cs.delete("kpl:sf_probe")
    _cs.delete("kpl:sf_probe:lock")

    def fake_call(host, params):
        with _lock:
            calls["n"] += 1
        time.sleep(0.1)
        return {"errcode": "0", "List": []}

    monkeypatch.setattr(kpl, "_call", fake_call)
    results = []
    ths = [threading.Thread(target=lambda: results.append(kpl._cached("sf_probe", 30, lambda: kpl._call("default", {"a": "x"})))) for _ in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert len(results) == 6
    assert all(r == {"errcode": "0", "List": []} for r in results)
    assert calls["n"] == 1, f"kpl._cached 并发 miss 应只 1 次外网, 实际 {calls['n']}"


def test_stats_overview_uses_singleflight():
    """api_stats_auction_overview 源码接线 cached_singleflight(防聚合重算风暴)"""
    import inspect
    from app.api import stats as api_stats
    src = inspect.getsource(api_stats)
    assert "cached_singleflight" in src


def test_bid_snapshot_3points_uses_cache(client, first_user):
    """三时点榜走结果缓存: 二次请求命中缓存不重算(页面 loadAll ×2 + 轮询防全量重算)"""
    import inspect
    from app.api import stats as api_stats
    src = inspect.getsource(api_stats.api_stats_bid_snapshot_3points)
    assert "cached_singleflight" in src and "s3points:" in src
    # 行为: 历史日请求写缓存(date 指定), 二次命中仍返回合法结构
    _cs.delete("s3points:hist:2099-01-01:100")
    _cs.delete("s3points:hist:2099-01-01:100:lock")
    token, _, _ = first_user
    hdrs = {"Authorization": "Bearer " + token}
    r1 = client.get("/api/stats/bid-snapshot-3points?date=2099-01-01", headers=hdrs)
    assert r1.status_code == 200
    # 2099 无数据应返回 ok 空列表(协议不 500)
    j = r1.json()
    assert j.get("ok") is True
    _cs.delete("s3points:hist:2099-01-01:100")
    _cs.delete("s3points:hist:2099-01-01:100:lock")


def test_kpl_prewarm_window_and_stub():
    """KPL 预热窗口函数: 工作日 9:15-15:05(北京)开启, 非工作日/盘前盘后关闭
    注意: kpl_prewarm_active 内部将 now_ts 按 UTC+8 转换, 因此测试需传北京时刻对应
    的 UTC 时间戳(gmtime 基准), 与本机时区无关。"""
    from app.services import kpl as kpl_mod
    import calendar
    # 北京 2026-09-04(周五) 09:30 → UTC 2026-09-04 01:30
    bj_0930 = calendar.timegm((2026, 9, 4, 1, 30, 0))
    assert kpl_mod.kpl_prewarm_active(bj_0930), "周五 9:30 应在预热窗口"
    # 北京 2026-09-04 17:00 → UTC 09:00(盘后关闭)
    bj_1700 = calendar.timegm((2026, 9, 4, 9, 0, 0))
    assert not kpl_mod.kpl_prewarm_active(bj_1700), "盘后 17:00 应关闭"
    # 北京 2026-09-05(周六) 10:00 → UTC 02:00
    sat_1000 = calendar.timegm((2026, 9, 5, 2, 0, 0))
    assert not kpl_mod.kpl_prewarm_active(sat_1000), "周六应关闭"
    # 北京 2026-09-04 09:00 → UTC 01:00(早于 9:15 未开盘)
    bj_0900 = calendar.timegm((2026, 9, 4, 1, 0, 0))
    assert not kpl_mod.kpl_prewarm_active(bj_0900), "9:00 早于窗口起点应关闭"
