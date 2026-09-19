# -*- coding: utf-8 -*-
"""2026-09-19 修复: 东财 clist 分页"越界页被误判为故障" → 竞价窗口熔断

事故现实（9/17、9/18 两天日志完全一致, 见 docs/diagnosis-20260919-clist-paging-circuit-breaker.md）:
    09:15:12  数据源故障: eastmoney_clist 进入异常状态
    09:29:0x  数据源恢复 (故障 535~546 秒)   ← 正好盖住竞价窗口
    09:25:28  快照拉取失败 market=hs/cyb/kcb err=东财数据源熔断中, 快速失败
              → 9_25 定格 = 开盘啦 + TickPlus 兜底源, 东财贡献 0 只
              → TickPlus 无 f630 → 17% 异动因子全落 default 0.18

根因: 全市场分页**固定请求 SPOT_MAX_PAGES(30) 页**, 而各板块真实页数只有
      ceil(total/200)（实测 18/8/4 页）→ 越界页命中东财 rc=102「没有更多数据」
      → 被当"接口异常"抛 → 失败页 ≥ 成功页 → 判整批故障 → 熔断。
      逐页实测: 失败**只出现在第 19 页以后, 1~18 页零失败**; 150ms 慢速串行同样复现
      → 确定性行为, 与请求频率、出口 IP 无关（**不是限流**）。

本文件锁死四件事（每条都是"改回去立刻红"的反向防线）:
  1. rc=102 / 空 diff = "到底"语义 → 返回空列表, **不抛异常**
  2. rc 既非 0 也非 102 → 仍抛异常（真故障不得被静默吞掉）
  3. 只请求真实页数（+1 探测页）, 不再打 20~30 页
  4. 越界页不再触发 _record(False) → eastmoney_clist 熔断不再打开
"""
import json
import urllib.parse

import pytest

from app.services import fetcher as F


@pytest.fixture(autouse=True)
def _clean_health():
    """每个用例前后复位 eastmoney_clist 健康状态, 避免熔断态跨用例污染"""
    _reset()
    yield
    _reset()


def _reset(src="eastmoney_clist"):
    with F._health_lock:
        F._HEALTH[src].update(ok=0, fail=0, last_ok=0, last_fail=0, ms_sum=0, ms_cnt=0,
                              down_since=0, cooldown=60, base_cooldown=60,
                              down_threshold=1, fails_in_row=0)


# ---------------------------------------------------------------- 桩

class _Resp:
    """最小 HTTP 响应: 只要 read() 和上下文管理器协议"""

    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, pages):
    """把 fetcher._http_get 换成按 pn 应答的桩。

    pages: {page: payload}; payload 为 dict(JSON 体) 或 Exception 实例(模拟网络异常)。
            未列出的页号一旦被请求 → AssertionError("不应请求第 N 页") ——
            这正是"不再打越界页"的断言方式。
    返回 calls: 实际被请求过的页号列表(按调用顺序)。
    """
    calls = []

    def fake_http_get(req, timeout=None, context=None, **kw):
        url = getattr(req, "full_url", str(req))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        pn = int(q["pn"][0])
        calls.append(pn)
        if pn not in pages:
            raise AssertionError("不应请求第 %d 页（越界/未预期）" % pn)
        payload = pages[pn]
        if isinstance(payload, Exception):
            raise payload
        return _Resp(payload)

    monkeypatch.setattr(F, "_http_get", fake_http_get)
    return calls


def _rows(n, start=0):
    return [{"f12": "%06d" % (start + i), "f2": "10.0", "f3": "3.0"} for i in range(n)]


def _ok(n, total):
    return {"rc": 0, "data": {"total": total, "diff": _rows(n)}}


def _page_break():
    """东财翻过末页的真实返回体"""
    return {"rc": 102, "data": None}


def _board_pages(total):
    """按真实页数铺一个板块的分页响应: 满页×(n-1) + 末页 + 越界探测页"""
    n = (total + F._CLIST_PZ - 1) // F._CLIST_PZ
    m = {1: _ok(F._CLIST_PZ if n > 1 else total, total)}
    for p in range(2, n):
        m[p] = _ok(F._CLIST_PZ, total)
    if n >= 2:
        m[n] = _ok(total - (n - 1) * F._CLIST_PZ, total)
    m[n + 1] = _page_break()          # 探测页: 东财明确回 rc=102
    return m, n


# ------------------------------------------------- 1. "到底"语义 vs 真异常

def test_rc102_means_end_of_list_not_error(monkeypatch):
    """核心回归①: rc=102「没有更多数据」= 正常到底, 返回空列表而不是抛异常"""
    _install(monkeypatch, {19: _page_break()})
    page = F._fetch_clist_page("m:1+t:2", 19, "f12")
    assert list(page) == [], "越界页应返回空列表"
    assert page.total == 0


def test_rc0_empty_diff_is_also_end_of_list(monkeypatch):
    """rc=0 但 diff 为空: 同样按"到底"处理(不抛)"""
    _install(monkeypatch, {3: {"rc": 0, "data": {"total": 621, "diff": []}}})
    assert list(F._fetch_clist_page("m:1+t:23", 3, "f12")) == []


def test_unknown_rc_still_raises(monkeypatch):
    """反向防线②: rc 既非 0 也非 102 = 真异常, 必须照旧抛出（不许被静默吞掉）"""
    _install(monkeypatch, {1: {"rc": 1, "data": None}})
    with pytest.raises(RuntimeError, match="rc=1"):
        F._fetch_clist_page("m:1+t:2", 1, "f12")


def test_em_end_rc_constant_is_102():
    """反向防线: rc=102 是东财「没有更多数据」的返回码, 不得被改成别的值"""
    assert F._EM_RC_END == 102
    assert F._CLIST_PZ == 200


def test_page_carries_total_and_stays_list_like(monkeypatch):
    """total 用于算真实页数; _ClistPage 必须保持 list 语义（既有调用方零改动）"""
    _install(monkeypatch, {1: _ok(200, 3487)})
    page = F._fetch_clist_page("m:1+t:2", 1, "f12")
    assert page.total == 3487
    assert isinstance(page, list) and len(page) == 200
    assert json.dumps(page).startswith("[")          # 可直接序列化
    acc = []
    acc.extend(page)
    assert len(acc) == 200                            # extend / len 语义不变


@pytest.mark.parametrize("total,want", [
    (3487, 18), (1452, 8), (621, 4), (5600, 28), (200, 1), (201, 2), (1, 1),
    (0, None), (None, None), ("", None), ("abc", None),
])
def test_page_count(total, want):
    """真实页数 = ceil(total/200); total 缺失/非法 → None(调用方回退固定上限)"""
    assert F._page_count(total) == want


# ------------------------------------------------- 2. 只请求真实页数

def test_only_real_pages_requested_hs(monkeypatch):
    """沪深主板: total=3487 → 真实 18 页 → 只请求 1..19（含 1 探测页）, 不碰 20+"""
    pages, n = _board_pages(3487)
    calls = _install(monkeypatch, pages)
    out = F.fetch_eastmoney_all("m:1+t:2")
    assert n == 18
    # 第 1 页串行、第 2~19 页 8 线程并发 → calls 的记录顺序天然不确定, 只比较集合
    assert sorted(calls) == list(range(1, 20)), "应只请求 1..19, 实际 %s" % sorted(calls)
    assert len(out) == 3487


def test_only_real_pages_requested_cyb_regression(monkeypatch):
    """核心回归③（事故复盘）: 创业板 total=1452 → 真实 8 页。

    老实现固定打 30 页 → 第 9~30 页全 rc=102 → 22 失败 : 8 成功 → 判整批故障 → 熔断。
    修复后: 只请求 1..9, **熔断不打开、失败计数为 0**。
    """
    pages, n = _board_pages(1452)
    calls = _install(monkeypatch, pages)
    out = F.fetch_eastmoney_all("m:0+t:80")
    assert n == 8
    assert sorted(calls) == list(range(1, 10))
    assert len(out) == 1452
    h = F._HEALTH["eastmoney_clist"]
    assert h["fail"] == 0, "越界页不得被计为失败"
    assert h["ok"] == 1
    assert F._check_circuit("eastmoney_clist") is False, "越界页不得触发熔断"


def test_three_boards_do_not_open_circuit(monkeypatch):
    """三个板块连续各采一轮（等价 9:15~9:25 的轮询）: 熔断始终不打开"""
    for fs, total in (("m:1+t:2", 3487), ("m:0+t:80", 1452), ("m:1+t:23", 621)):
        pages, _n = _board_pages(total)
        _install(monkeypatch, pages)
        out = F.fetch_eastmoney_all(fs)
        assert len(out) == total
        assert F._check_circuit("eastmoney_clist") is False, "%s 采完不应熔断" % fs
    assert F._HEALTH["eastmoney_clist"]["fail"] == 0


def test_total_missing_falls_back_to_max_pages(monkeypatch):
    """兼容性防线④: total 缺失（老桩/异常响应）→ 回退固定上限, 行为与旧版一致"""
    monkeypatch.setattr(F.config, "SPOT_MAX_PAGES", 4)
    pages = {p: {"rc": 0, "data": {"diff": _rows(200)}} for p in range(1, 5)}
    calls = _install(monkeypatch, pages)
    out = F.fetch_eastmoney_all("m:1+t:2")
    assert sorted(calls) == [1, 2, 3, 4]
    assert len(out) == 800


def test_probe_page_absorbs_underreported_total(monkeypatch):
    """探测页的价值: total 少报一档时, 仍能拿到只多 21 只的真实末页, 且不多打第 4 页"""
    pages = {1: _ok(200, 400), 2: _ok(200, 400), 3: _ok(21, 400)}
    calls = _install(monkeypatch, pages)
    out = F.fetch_eastmoney_all("m:1+t:23")
    assert sorted(calls) == [1, 2, 3], "total=400 只应请求 3 页(2 真实页 + 1 探测页)"
    assert len(out) == 421


def test_short_page_stops_merge(monkeypatch):
    """短页 = 末页: 合并到此为止, 不把探测页的空数据当缺口"""
    monkeypatch.setattr(F.config, "SPOT_MAX_PAGES", 8)
    pages = {1: _ok(200, 300), 2: _ok(100, 300), 3: _page_break()}
    _install(monkeypatch, pages)
    out = F.fetch_eastmoney_all("m:1+t:2")
    assert len(out) == 300


# ------------------------------------------------- 3. 真故障仍要判失败

def test_first_page_empty_is_source_failure(monkeypatch):
    """首页为空 = 该分区无数据, 属真故障（页≥2 为空才是"到底"）→ 记失败 + 抛异常"""
    _install(monkeypatch, {1: {"rc": 0, "data": {"total": 0, "diff": []}}})
    with pytest.raises(RuntimeError, match="首页无数据"):
        F.fetch_eastmoney_all("m:1+t:2")
    h = F._HEALTH["eastmoney_clist"]
    assert h["fail"] == 1 and h["ok"] == 0
    assert F._check_circuit("eastmoney_clist") is True


def test_first_page_network_error_records_single_failure(monkeypatch):
    """首页网络异常: 只记 1 次失败（不是 30 次）, 异常原样抛出"""
    _install(monkeypatch, {1: RuntimeError("connection reset")})
    with pytest.raises(RuntimeError, match="connection reset"):
        F.fetch_eastmoney_all("m:1+t:2")
    h = F._HEALTH["eastmoney_clist"]
    assert h["fail"] == 1 and h["ok"] == 0


def test_first_page_empty_keeps_single_page_contract(monkeypatch):
    """单页接口 fetch_eastmoney 契约不变: 空数据仍抛异常（上游按异常走兜底）"""
    monkeypatch.setattr(F, "_fetch_clist_page", lambda fs, page, fid="f3": [])
    with pytest.raises(RuntimeError, match="首页无数据"):
        F.fetch_eastmoney("m:1+t:2")
    assert F._HEALTH["eastmoney_clist"]["fail"] == 1


def test_single_page_still_returns_data(monkeypatch):
    """单页正常路径不受影响"""
    monkeypatch.setattr(F, "_fetch_clist_page",
                        lambda fs, page, fid="f3": [{"f12": "600001", "f2": "10"}])
    assert F.fetch_eastmoney("m:1+t:2") == [{"f12": "600001", "f2": "10"}]
    assert F._HEALTH["eastmoney_clist"]["ok"] == 1
