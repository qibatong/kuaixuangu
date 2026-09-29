# -*- coding: utf-8 -*-
"""P0③ 按代码点查实时行情（2026-09-29）。

量化依据（生产 2026-09-29 09:14~09:29 竞价窗口，全部来自日志）:
  · `全市场行情map刷新 … 共5561只` 共 **8 次**（每次 ~33 个 HTTP 请求），另有缓存刷新 4 次；
  · 同窗口 `429|限流` 日志 **241 条**；
  · 而这三次刷新要服务的名单只有 **51 只**（现涨）/ **51 只**（三时点）/ 几十~几百只（爆量）。
⇒ 改为东财 ulist 按代码点查（`fetch_raw_by_codes`，每批 60，整段复用 config.FIELDS 与 clist 同构）:
   51 只 = 1 个请求。

本文件锁四件事:
  ① 逐 code 缓存/去重/过期（不得每轮重复出网）；
  ② 点查失败返回 {} 不抛（共享不是依赖）；
  ③ 三处调用方在**有小名单时不再拉全市场**（这是本优化的核心断言）；
  ④ 点查失败/为空时**仍退回全市场**（可用性不降，行为可回滚）。
"""
import time

import pytest

from app.services import fetcher
from app.services import kpl as svc_kpl
from app.api import kpl as api_kpl
from app.api import stats as api_stats


def _row(code, rc=1.5, price=10.0):
    return {"f12": code, "f14": "测试", "f2": price, "f3": rc, "f8": 2.0,
            "f10": 3.0, "f17": 9.0}


@pytest.fixture(autouse=True)
def _clear_code_cache():
    fetcher._code_quote_cache.clear()
    yield
    fetcher._code_quote_cache.clear()


# ---------------- ① 缓存 / 去重 / 过期 ----------------

def test_by_codes_dedup_and_cache_hit(monkeypatch):
    calls = []

    def _raw(codes, extra_fields=None):
        calls.append(list(codes))
        return [_row(c) for c in codes]

    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _raw)
    m1 = fetcher.fetch_spot_quote_map_by_codes(["600519", "000001", "600519"])
    assert calls[0] == ["600519", "000001"], "重复代码必须去重"
    assert m1["600519"]["realChange"] == 1.5 and m1["600519"]["price"] == 10.0
    fetcher.fetch_spot_quote_map_by_codes(["600519", "000001"])
    assert len(calls) == 1, "TTL 内必须命中逐码缓存, 不得再次出网"


def test_by_codes_refreshes_after_ttl(monkeypatch):
    calls = []

    def _raw(codes, extra_fields=None):
        calls.append(list(codes))
        return [_row(c, rc=float(len(calls))) for c in codes]

    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _raw)
    fetcher.fetch_spot_quote_map_by_codes(["600519"])
    q, _ts = fetcher._code_quote_cache["600519"]
    fetcher._code_quote_cache["600519"] = (q, time.time() - fetcher._CODE_QUOTE_TTL - 1)
    m = fetcher.fetch_spot_quote_map_by_codes(["600519"])
    assert len(calls) == 2, "过期必须重新点查"
    assert m["600519"]["realChange"] == 2.0, "过期后应取到新值"


# ---------------- ② 失败不抛 ----------------

def test_by_codes_failure_returns_empty(monkeypatch):
    def _boom(codes, extra_fields=None):
        raise RuntimeError("ulist 挂了")

    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _boom)
    assert fetcher.fetch_spot_quote_map_by_codes(["600519"]) == {}
    assert fetcher.fetch_spot_quote_map_by_codes([]) == {}


# ---------------- ③ 调用方不再拉全市场 ----------------

def test_kpl_change_uses_by_codes(monkeypatch):
    """竞价异动现涨: 有小名单时不得拉全市场（旧路径为几十只票拉 5561 只/33 请求）。"""
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map_by_codes",
                        lambda codes: {"600519": {"realChange": 3.3}})
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: pytest.fail("有一个小名单时不该拉全市场"))
    lst = [{"code": "600519"}, {"code": "000001"}]
    n = api_kpl._update_spot_change(lst)
    assert n == 1
    assert lst[0]["change"] == 3.3 and lst[0]["realChange"] == 3.3


def test_stats_change_uses_by_codes(monkeypatch):
    """三时点现涨(~51 只): 同样走按代码点查, 不拉全市场。"""
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    monkeypatch.setattr(api_stats, "_is_intraday_stats", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map_by_codes",
                        lambda codes: {"600519": {"realChange": 4.4}})
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: pytest.fail("有一个小名单时不该拉全市场"))
    lst = [{"code": "600519"}]
    api_stats._apply_change_stats(lst, today)
    assert lst[0]["real_change"] == 4.4 and lst[0]["realChange"] == 4.4


def test_boom_spot_map_passes_codes(monkeypatch):
    """竞价爆量: 应把"过滤后存活的代码"传给点查(而不是拉全市场)。"""
    got = {}

    def _by_codes(codes):
        got["codes"] = list(codes)
        return {"600519": {"realChange": 2.2}}

    monkeypatch.setattr(fetcher, "fetch_spot_quote_map_by_codes", _by_codes)
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: pytest.fail("有一个小名单时不该拉全市场"))
    m = svc_kpl._boom_spot_map(["600519", "000001"])
    assert got["codes"] == ["600519", "000001"]
    assert m["600519"]["realChange"] == 2.2


# ---------------- ④ 点查失败仍退回全市场 ----------------

def test_kpl_change_falls_back_to_full_market(monkeypatch):
    """点查返回空 ⇒ 必须退回全市场 spot map(可用性不降)。"""
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map_by_codes", lambda codes: {})
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: {"600519": {"realChange": 1.1}})
    lst = [{"code": "600519"}]
    assert api_kpl._update_spot_change(lst) == 1
    assert lst[0]["change"] == 1.1


def test_boom_spot_map_falls_back_when_empty(monkeypatch):
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map_by_codes", lambda codes: {})
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: {"600519": {"realChange": 9.9}})
    assert svc_kpl._boom_spot_map(["600519"])["600519"]["realChange"] == 9.9
    # 不传 codes(旧语义: 需要全市场) 时直接走全市场, 不点查
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map_by_codes",
                        lambda codes: pytest.fail("不传 codes 时不该点查"))
    assert svc_kpl._boom_spot_map()["600519"]["realChange"] == 9.9
