# -*- coding: utf-8 -*-
"""
按需行情接口 /api/quotes + fetcher.fetch_spot_quotes_by_codes 用例
==================================================================
背景(2026-09-05 B 方案「拆分独立行情接口」):
  /api/stocks 每次把全市场 spotMap(数千只)塞进响应, 叠加前端 30s 轮询反复拉大包,
  拖慢首屏与每次刷新 → 三处响应(spot/幂等直读/refresh直读+计算缓存)全部移除 spotMap,
  实时价内联到各 list item; 新增 /api/quotes 供前端对"不在返回名单的锁定票"按需取价。

覆盖:
  ① 接口只回请求 codes 中命中缓存的部分(未命中不返回占位)
  ② codes 缺失 → 400(参数校验)
  ③ 去重 + 限长 500(防恶意超长 codes)
  ④ fetcher 抛异常 → 降级 200 + 空 quotes(不允许 500 打断前端)
  ⑤ 服务层 fetch_spot_quotes_by_codes 从已缓存 map 过滤(不触发全市场拉取)
"""
import time

import pytest

from app.services import fetcher


@pytest.fixture(autouse=True)
def _clean_quote_cache():
    """用例隔离: 清空 quote map 缓存, 避免上一用例写入的 map 影响断言"""
    with fetcher._quote_map_lock:
        saved = dict(fetcher._quote_map_cache)
        fetcher._quote_map_cache.clear()
    yield
    with fetcher._quote_map_lock:
        fetcher._quote_map_cache.clear()
        fetcher._quote_map_cache.update(saved)


def _seed_cache(mapping):
    """预置已预热的 quote map 缓存(ts=now 保证未过期)"""
    with fetcher._quote_map_lock:
        fetcher._quote_map_cache["m:0+t:6"] = {
            "raw": [], "map": dict(mapping), "ts": time.time()}


# ---------- ① 只回命中的 code ----------

def test_quotes_api_returns_only_hit_codes(client, first_user, monkeypatch):
    """P0: 请求 2 个 code, 缓存只有 1 个 → 只回 1 个(count=1), 未命中的不返回占位"""
    _seed_cache({"600000": {"realChange": 3.2, "price": 10.5, "name": "浦发银行"}})
    r = client.get("/api/quotes?codes=600000,000001",
                   headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["count"] == 1
    assert "600000" in d["quotes"]
    assert d["quotes"]["600000"]["realChange"] == 3.2
    assert "000001" not in d["quotes"], "未命中缓存的 code 不应返回占位"


# ---------- ② 参数校验 ----------

def test_quotes_api_requires_codes(client, first_user):
    """codes 缺失 → 400 + ok:False"""
    r = client.get("/api/quotes", headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ---------- ③ 去重 + 限长 ----------

def test_quotes_api_dedups_codes(client, first_user, monkeypatch):
    """同一 code 重复出现 → 去重后只传一次"""
    seen = {}

    def fake(codes):
        seen["codes"] = list(codes)
        return {c: {"realChange": 1.0} for c in codes}

    monkeypatch.setattr(fetcher, "fetch_spot_quotes_by_codes", fake)
    r = client.get("/api/quotes?codes=600000,600000,600000",
                   headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 200
    assert seen["codes"] == ["600000"], "应去重后只请求一次"


def test_quotes_api_caps_at_500(client, first_user, monkeypatch):
    """传 600 个 code → 截断到 500(防恶意超长)"""
    seen = {}

    def fake(codes):
        seen["n"] = len(codes)
        return {}

    monkeypatch.setattr(fetcher, "fetch_spot_quotes_by_codes", fake)
    codes = ["%06d" % i for i in range(600)]
    r = client.get("/api/quotes?codes=" + ",".join(codes),
                   headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 200
    assert seen["n"] == 500


# ---------- ④ 降级不 500 ----------

def test_quotes_api_degrades_when_fetcher_raises(client, first_user, monkeypatch):
    """fetcher 异常 → 200 + 空 quotes(行情取失败不能打断前端, 下轮轮询自愈)"""

    def boom(codes):
        raise RuntimeError("行情源炸了")

    monkeypatch.setattr(fetcher, "fetch_spot_quotes_by_codes", boom)
    r = client.get("/api/quotes?codes=600000",
                   headers={"Authorization": "Bearer " + first_user[0]})
    assert r.status_code == 200, "行情失败应降级而不是 500"
    d = r.json()
    assert d["ok"] is True and d["quotes"] == {}


# ---------- ⑤ 服务层: 从缓存过滤, 不触发全市场 ----------

def test_fetch_spot_quotes_by_codes_filters_from_cache(monkeypatch):
    """P0: 已预热缓存时只从 map 过滤, 不调 fetch_spot_quote_map(不触发全市场拉取)"""
    _seed_cache({"600000": {"realChange": 1.0}, "000001": {"realChange": 2.0}})
    calls = {"n": 0}

    def fake_map(fs):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", fake_map)
    out = fetcher.fetch_spot_quotes_by_codes(["600000", "999999"])
    assert set(out) == {"600000"}, "只回缓存中存在的 code"
    assert calls["n"] == 0, "缓存已预热时不应再拉全市场"


def test_fetch_spot_quotes_by_codes_empty_list():
    """空 code_list → 空 dict(不落库不请求)"""
    assert fetcher.fetch_spot_quotes_by_codes([]) == {}
    assert fetcher.fetch_spot_quotes_by_codes(None) == {}


def test_fetch_spot_quotes_by_codes_cold_start_falls_back(monkeypatch):
    """冷启动(缓存从未预热) → 兜底拉一次全市场建 base, 再过滤"""
    calls = {"n": 0}

    def fake_map(fs):
        calls["n"] += 1
        return {"600000": {"realChange": 5.0}}

    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", fake_map)
    out = fetcher.fetch_spot_quotes_by_codes(["600000"])
    assert calls["n"] == 1, "冷启动应兜底拉一次"
    assert out["600000"]["realChange"] == 5.0
