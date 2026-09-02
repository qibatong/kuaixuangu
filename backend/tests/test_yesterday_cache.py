# -*- coding: utf-8 -*-
"""昨日成交额失败缓存测试(2026-09-02 生产事故回归):
事故: 失败不写缓存 → 每请求重复拉全市场 3950 只 → 打爆东财/同花顺K线 → /api/stocks 20-43s
修复: 失败/超时也写当日缓存(带时间戳), 重试窗口 YESTERDAY_RETRY_TTL 内不再拉取。
注意: _fetch_yesterday_amount_one 返回 [T日万元, T-1日万元] pair; 缓存值即 pair。
"""
import time

import pytest

from app.services import fetcher
from app.core import config

# conftest 的 session 级 mock_data_source 会把 fetcher.fetch_yesterday_amounts 整体替换为
# 假实现(fake_yesterday_amounts); 本测试测的是真实缓存逻辑 → 恢复原始实现(与 test_tencent_fallback 同法)
_ORIG_FETCH_YDAY = fetcher.fetch_yesterday_amounts


@pytest.fixture(autouse=True)
def _use_real_fetch(monkeypatch):
    """恢复真实 fetch_yesterday_amounts(覆盖 session 级 mock)"""
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", _ORIG_FETCH_YDAY)
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache.clear()
    yield
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache.clear()


@pytest.fixture
def fake_fetch(monkeypatch):
    """替换单只拉取为可控假实现, 记录调用次数"""
    calls = {"n": 0, "result": None}

    def _fake(code):
        calls["n"] += 1
        return calls["result"]

    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_one", _fake)
    return calls


def test_success_cache_hit(monkeypatch, fake_fetch):
    """成功结果当日缓存: 第二次调用不重复拉取, 且返回 pair"""
    fake_fetch["result"] = [20000.0, 15000.0]
    codes = ["000001", "600519"]
    r1 = fetcher.fetch_yesterday_amounts(codes, wait=True)
    assert r1 == {"000001": [20000.0, 15000.0], "600519": [20000.0, 15000.0]}
    assert fake_fetch["n"] == 2

    r2 = fetcher.fetch_yesterday_amounts(codes, wait=True)  # 命中缓存
    assert r2 == {"000001": [20000.0, 15000.0], "600519": [20000.0, 15000.0]}
    assert fake_fetch["n"] == 2                          # 未再拉取


def test_fail_writes_cache_and_skip_retry(monkeypatch, fake_fetch):
    """失败也写缓存: 重试窗口内第二次调用不再打扰数据源(事故回归点)"""
    fake_fetch["result"] = None                          # 全部失败
    codes = ["000001", "600519"]
    r1 = fetcher.fetch_yesterday_amounts(codes, wait=True)
    assert r1 == {}                                      # 失败 → 返回空
    assert fake_fetch["n"] == 2

    r2 = fetcher.fetch_yesterday_amounts(codes, wait=True)  # 窗口内
    assert r2 == {}
    assert fake_fetch["n"] == 2                          # 关键: 未重复拉取


def test_fail_cache_expired_allows_retry(monkeypatch, fake_fetch):
    """失败缓存超过重试窗口后允许重试; 恢复后成功"""
    fake_fetch["result"] = None
    codes = ["000001"]
    assert fetcher.fetch_yesterday_amounts(codes, wait=True) == {}
    assert fake_fetch["n"] == 1

    # 把失败缓存时间戳改旧, 模拟窗口过期
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache["000001"][2] = time.time() - config.YESTERDAY_RETRY_TTL - 1

    fake_fetch["result"] = [8888.0, 8000.0]              # 数据源恢复
    r = fetcher.fetch_yesterday_amounts(codes, wait=True)
    assert r == {"000001": [8888.0, 8000.0]}
    assert fake_fetch["n"] == 2


def test_circuit_shortcut_writes_fail_cache(monkeypatch, fake_fetch):
    """四源全熔断短路: 写失败缓存, 窗口内不再触发熔断判定重复日志"""
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src: True)   # 全熔断
    codes = ["000001"]
    assert fetcher.fetch_yesterday_amounts(codes) == {}
    assert fake_fetch["n"] == 0                          # 短路未拉取

    r2 = fetcher.fetch_yesterday_amounts(codes, wait=True)  # 窗口内
    assert r2 == {}
    assert fake_fetch["n"] == 0                          # 不再判定


def test_partial_fail_and_success_mixed(monkeypatch, fake_fetch):
    """混合场景: 部分成功部分失败, 成功进输出+缓存, 失败写失败缓存"""
    real = {"000001": [111.0, 100.0], "000002": None, "600519": [333.0, 300.0]}
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_one",
                        lambda c: real.get(c))
    codes = list(real)
    r = fetcher.fetch_yesterday_amounts(codes, wait=True)
    assert r == {"000001": [111.0, 100.0], "600519": [333.0, 300.0]}  # 失败(000002)不进输出
    with fetcher._yesterday_lock:
        assert fetcher._yesterday_cache["000002"][1] is None   # 失败已缓存
        assert fetcher._yesterday_cache["000001"][1] == [111.0, 100.0]


def test_concurrent_request_skips_when_batch_in_progress(monkeypatch, fake_fetch):
    """并发去重: 已有全量拉取进行中, 并发请求直接返回缓存不重复拉(批锁)"""
    fake_fetch["result"] = [20000.0, 15000.0]
    # 先模拟批锁被持有(另一请求正在拉)
    assert fetcher._yday_batch_lock.acquire(blocking=False)
    try:
        r = fetcher.fetch_yesterday_amounts(["000001"])
        assert r == {}                      # 批锁被持有 → 返回现有缓存(空)
        assert fake_fetch["n"] == 0         # 关键: 未触发任何拉取
    finally:
        fetcher._yday_batch_lock.release()


def test_async_path_returns_immediately_and_triggers_bg(monkeypatch, fake_fetch):
    """异步路径(wait=False): 立即返回(不阻塞), 后台线程触发拉取"""
    import threading
    fake_fetch["result"] = [20000.0, 15000.0]
    # 批锁未持有时异步调用 → 启动后台线程, 本请求立即返回
    r = fetcher.fetch_yesterday_amounts(["000001"], wait=False)
    # 后台线程可能尚未完成 → 结果可能为空; 关键断言: 未同步阻塞拉取
    assert r in ({}, {"000001": [20000.0, 15000.0]})
    # 等待后台线程完成(批锁释放 = 拉取完成)
    deadline = time.time() + 5
    while fetcher._yday_batch_lock.locked() and time.time() < deadline:
        time.sleep(0.05)
    assert fake_fetch["n"] == 1             # 后台确实拉了一次
    assert fetcher.fetch_yesterday_amounts(["000001"], wait=False) == {"000001": [20000.0, 15000.0]}
