# -*- coding: utf-8 -*-
"""
市场概览(market-brief)缓存回归用例 (2026-09-04 二轮优化)
=========================================================
背景: 生产 journald 14:40-14:50 实测 uid=49 冷请求 **1174ms**(首屏最慢接口),
      且该接口此前**完全无结果缓存**。耗时拆解:
        1) kpl.fetch_market_breadth() → _flash_line 两次 xuangubao 外网(无缓存, timeout=10s)
        2) fetcher.fetch_market_brief() → 300s 进程内 dict 缓存, miss 时拉全市场(5556 只)
        3) get_same_time_yesterday() + settings 读库
修复:
  ① 聚合逻辑下沉 services/kpl.py build_market_brief_payload, 整段结果走跨进程缓存
     30s + single-flight 防击穿 → 命中 <50ms(api 与预热共用同一入口)
  ② fetcher.fetch_market_brief 进程内 dict → 跨进程 cache_store
     (uvicorn --workers 2 下两 worker 原本各拉各的全市场, 共享后拉取次数减半)
  ③ 纳入 KPL 预热(交易日 9:15-15:05 每 12s), 消除冷窗口
断言: 聚合次数 / 全市场拉取次数 / 字段完整性 / max_age=0 语义 / 预热 key 正确 / 异常兜底
"""
import time

import pytest

from app.services import fetcher, kpl
from app.services.cache_store import store

MB_KEY = kpl._MB_PAYLOAD_KEY          # market_brief_payload
FB_KEY = fetcher._MARKET_BRIEF_KEY    # market_brief_amt


@pytest.fixture(autouse=True)
def _clean_cache():
    """用例前后清理两个缓存 key + single-flight 锁, 保证隔离"""
    def _del():
        for k in (MB_KEY, MB_KEY + ":lock", FB_KEY):
            try:
                store.delete(k)
            except Exception:
                pass
    _del()
    yield
    _del()


def _fake_payload():
    return {"breadth": {"rise": 1234, "fall": 2345, "ts": int(time.time())},
            "market": {"stockCount": 5556, "amount": 12345.67, "date": "2026-09-04"},
            "last_same_time": {"amount": 11000.0}, "last": None,
            "ts": int(time.time())}


# ---------- ① market-brief 结果缓存 ----------

def test_market_brief_payload_only_computed_once(monkeypatch):
    """P0: TTL 内连续两次 fetch_market_brief_payload 只聚合 1 次(原每请求全量重算)"""
    calls = {"n": 0}

    def fake_build():
        calls["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(kpl, "build_market_brief_payload", fake_build)
    a = kpl.fetch_market_brief_payload()
    b = kpl.fetch_market_brief_payload()
    assert calls["n"] == 1, f"TTL 内应只聚合 1 次, 实际 {calls['n']}"
    assert a == b and a["market"]["stockCount"] == 5556


def test_market_brief_payload_ttl_matches_polling():
    """缓存新鲜度 **60s**(2026-10-01 P2-6 由 30 → 60)

    原判据不变(数据低频: 分时涨跌家数 / 两市成交额, 本就无需高频重算), 只是把 TTL 抬到与
    **前端轮询同频**(`MarketView.tick()` = 60s, 仅盘中): 原 30 < 60 ⇒ 每次轮询都必然穿透
    重算, 那句"缓存 30s"形同虚设。用户可见新鲜度不变(额外陈旧量 ≤ 1 个轮询周期)。
    """
    assert kpl.MARKET_BRIEF_TTL == 60


def test_market_brief_api_ok_and_fields(client, first_user, monkeypatch):
    """P0: 接口返回 ok=True + 五字段完整; 二次请求命中缓存(聚合仅 1 次)"""
    calls = {"n": 0}

    def fake_build():
        calls["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(kpl, "build_market_brief_payload", fake_build)
    h = {"Authorization": "Bearer " + first_user[0]}
    r1 = client.get("/api/kpl/market-brief", headers=h)
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["ok"] is True
    for f in ("breadth", "market", "last_same_time", "last", "ts"):
        assert f in d1, f"缺字段 {f}"
    r2 = client.get("/api/kpl/market-brief", headers=h)
    d2 = r2.json()
    assert d2["ok"] is True and d2["market"]["stockCount"] == 5556
    assert calls["n"] == 1, f"二次请求应命中缓存(聚合仅1次), 实际 {calls['n']}"


def test_market_brief_api_fallback_when_loader_fails(client, first_user, monkeypatch):
    """loader 返回 None(异常)时接口兜底直接算一次, 不返空 / 不 500"""
    state = {"n": 0}

    def flaky_build():
        state["n"] += 1
        if state["n"] == 1:
            return None          # 首次模拟失败
        return _fake_payload()

    monkeypatch.setattr(kpl, "build_market_brief_payload", flaky_build)
    r = client.get("/api/kpl/market-brief",
                   headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["market"]["stockCount"] == 5556
    assert state["n"] == 2, f"失败后应兜底再算一次, 实际调用 {state['n']} 次"


# ---------- ② fetch_market_brief 跨进程缓存 ----------

def test_fetch_market_brief_uses_cross_process_cache(monkeypatch):
    """P0: 二次调用不再拉全市场(原进程内 dict, 2 worker 各拉各的)"""
    calls = {"n": 0}

    def fake_all(fs):
        calls["n"] += 1
        return [{"f6": 1000000.0} for _ in range(100)]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", fake_all)
    a = fetcher.fetch_market_brief()
    b = fetcher.fetch_market_brief()
    assert calls["n"] == 1, f"跨进程缓存应命中(全市场仅拉1次), 实际 {calls['n']}"
    assert a == b and a["stockCount"] == 100
    # 关键: 缓存落在 cache_store(跨进程)而非仅进程内 dict
    assert store.get(FB_KEY) is not None, "两市概况应写入跨进程 store"


def test_fetch_market_brief_max_age_zero_forces_refresh(monkeypatch):
    """max_age=0 语义保持: 跳过缓存强制重拉(auction_snapshot / record_intraday 依赖)"""
    calls = {"n": 0}

    def fake_all(fs):
        calls["n"] += 1
        return [{"f6": 2000000.0}]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", fake_all)
    fetcher.fetch_market_brief()
    fetcher.fetch_market_brief(max_age=0)
    assert calls["n"] == 2, f"max_age=0 应强制重拉, 实际 {calls['n']}"


def test_fetch_market_brief_failure_degrades_to_cache(monkeypatch):
    """拉取失败时降级返回上次缓存, 不抛异常(东财封禁期行情源全挂场景)"""
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback",
                        lambda fs: [{"f6": 1000000.0}])
    first = fetcher.fetch_market_brief()

    def boom(fs):
        raise RuntimeError("行情源全挂")

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", boom)
    got = fetcher.fetch_market_brief(max_age=0)
    assert got == first, "拉取失败应降级返回上次缓存"


# ---------- ③ 预热覆盖 ----------

def test_kpl_prewarm_covers_market_brief(monkeypatch):
    """P0: 预热列表含 market_brief, 且 delete 用无 'kpl:' 前缀的正确 key
    (yidong_* 走 _cached 带 kpl: 前缀; market_brief_payload 自身已含命名空间)"""
    deleted = []
    computed = {"n": 0}
    real_delete = store.delete

    def spy_delete(k):
        deleted.append(k)
        return real_delete(k)

    monkeypatch.setattr(store, "delete", spy_delete)
    monkeypatch.setattr(kpl, "fetch_kpl_doc90", lambda: {"List": []})
    monkeypatch.setattr(kpl, "fetch_kpl_doc108", lambda: {"List": []})
    monkeypatch.setattr(kpl, "fetch_kpl_doc109", lambda: {"List": []})
    monkeypatch.setattr(kpl, "fetch_kpl_pianli_hot", lambda: {"List": []})

    def fake_payload():
        computed["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(kpl, "fetch_market_brief_payload", fake_payload)

    # 用 conftest 留存的真实实现(桩掉的 _kpl_prewarm_once 是空 lambda)
    kpl._kpl_prewarm_once_real()
    assert computed["n"] == 1, f"预热应触发 market_brief 重算, 实际 {computed['n']}"
    assert MB_KEY in deleted, f"预热应 delete 缓存 key {MB_KEY}, 实际 {deleted}"
    assert ("kpl:" + MB_KEY) not in deleted, \
        "market_brief_payload 的 key 不应再加 kpl: 前缀(否则预热删错 key)"
    # yidong 4 key 仍走带前缀的 delete(回归保护)
    assert "kpl:yidong_doc90" in deleted
