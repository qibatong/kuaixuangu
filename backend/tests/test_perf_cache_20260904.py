# -*- coding: utf-8 -*-
"""2026-09-04 性能缓存回归(主人反馈首页加载慢深查后修复):
1. yidong 三接口 fetch_kpl_doc90/doc108/doc109/pianli_hot 原无缓存, 每请求真拉开盘啦
   外网(nginx 实测 avg urt 0.96s) → 无参场景走 _cached(KPL_YIDONG_TTL 15s)
2. stats/kpl api 曾硬编码 spotMap fs "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", 与预热线程
   market_fs(["hs","cyb","kcb"]) 顺序不同 = 缓存 key 不同 → 每次 miss 锁内拉全市场 1-2s
   → 统一 market_fs 生成, 断言源码不再含硬编码串
3. auction-overview 原无缓存(历史4日聚合 avg 0.58s) → 结果写跨进程缓存
"""
import inspect

import pytest

from app.services import kpl
from app.services.cache_store import store as _cs


def _clean(*keys):
    for k in keys:
        try:
            _cs.delete(k)
        except Exception:
            pass


YIDONG_FNS = [
    (kpl.fetch_kpl_doc90, "kpl:yidong_doc90"),
    (kpl.fetch_kpl_doc108, "kpl:yidong_doc108"),
    (kpl.fetch_kpl_doc109, "kpl:yidong_doc109"),
    (kpl.fetch_kpl_pianli_hot, "kpl:yidong_pianli_hot"),
]


def test_yidong_fetch_noarg_cached(monkeypatch):
    """无参调用走共享缓存: TTL 内二次调用不再打外网(原每请求真拉开盘啦)"""
    calls = {"n": 0}

    def fake_call(host, params):
        calls["n"] += 1
        return {"errcode": "0", "List": [["600001", "测A", 0, "测试", 1, 2, 3, "x", 4, 0, 5, 6, 7]]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    for _, key in YIDONG_FNS:
        _clean(key)
    for fn, key in YIDONG_FNS:
        calls["n"] = 0
        assert fn() is not None
        assert fn() is not None          # 第二次应在 15s TTL 内命中缓存
        assert calls["n"] == 1, f"{fn.__name__} 无参二次调用应命中缓存(外网仅1次), 实际 {calls['n']}"
        _clean(key)


def test_yidong_fetch_with_extra_bypass_cache(monkeypatch):
    """带 extra(个股/历史维度)不进共享缓存直接外网, 避免无参数区分的 key 串数据"""
    calls = {"n": 0}
    monkeypatch.setattr(kpl, "_call", lambda host, params: (calls.__setitem__("n", calls["n"] + 1), {"errcode": "0", "List": []})[1])
    _clean("kpl:yidong_doc90")
    kpl.fetch_kpl_doc90(StockID="600001")
    kpl.fetch_kpl_doc90(StockID="600001")
    assert calls["n"] == 2, "带 extra 参数每次都应真实外网"


def test_no_hardcoded_spotmap_fs():
    """spotMap fs 必须统一 market_fs 生成: api/stats 与 api/kpl 源码不得再含旧硬编码串
    (顺序与预热 market_fs 不一致 → 缓存 key miss → 每次锁内拉全市场 5556 只 1-2s)"""
    from app.api import kpl as api_kpl
    from app.api import stats as api_stats
    old = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    for mod in (api_kpl, api_stats):
        src = inspect.getsource(mod)
        assert old not in src, f"{mod.__name__} 仍含硬编码 spotMap fs"
    # 正向断言: 调用点已统一 market_fs(与预热线程同源函数, 缓存 key 必然一致)
    assert "market_fs([\"hs\", \"cyb\", \"kcb\"])" in inspect.getsource(api_stats)


def test_auction_overview_writes_cache(client, first_user):
    """auction-overview 结果写跨进程缓存(date 空 key='_'), 二次请求直接命中不再聚合"""
    _clean("auction_overview:_")
    token, _, _ = first_user
    hdrs = {"Authorization": "Bearer " + token}
    r1 = client.get("/api/stats/auction-overview", headers=hdrs)
    assert r1.status_code == 200
    assert r1.json().get("ok")
    assert _cs.get("auction_overview:_") is not None, "date 空请求应写入缓存"
    # 命中缓存仍返回合法结构(数据可能为空库, 只验协议)
    r2 = client.get("/api/stats/auction-overview", headers=hdrs)
    assert r2.status_code == 200 and r2.json().get("ok")
    _clean("auction_overview:_")
