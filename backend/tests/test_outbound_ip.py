# -*- coding: utf-8 -*-
"""出站 IP 轮询公共层(app.core.net)测试

背景: 生产机双网卡双公网出口
    eth0 172.22.114.161 -> 121.196.230.80
    eth1 172.22.114.162 -> 101.37.204.78
实测东财对两个出口都封(IDC 级), 但新浪 eth0=403 / eth1=200 →
轮换是"出口 IP 容灾 + 分散限流"的现成手段, 不是东财解药。

守三条不变量:
  1. 无 IP 池 → ip_binding 是 no-op(单 IP 环境如测试机, 不能绑死)
  2. 严格 RR + 失败惩罚可恢复
  3. 各数据源(fetcher/kpl)共用同一个 rotator, 惩罚状态不割裂
"""
import socket

import pytest

from app.core import net


# ---------------------------------------------------------------- 1. 轮询逻辑
def test_acquire_strict_round_robin():
    r = net.IPRotator(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
    got = [r.acquire() for _ in range(6)]
    assert got == ["10.0.0.1", "10.0.0.2", "10.0.0.3"] * 2, got


def test_fail_penalty_skips_ip():
    """连续失败 IP_FAIL_THRESHOLD 次 → 后续轮询跳过它"""
    r = net.IPRotator(["10.0.0.1", "10.0.0.2"])
    r.report_fail("10.0.0.1")
    assert r.acquire() == "10.0.0.1", "仅失败 1 次不应跳过(阈值 2)"
    r.report_fail("10.0.0.1")
    assert r.acquire() == "10.0.0.2", "达到阈值后应跳过故障 IP"


def test_success_clears_penalty():
    r = net.IPRotator(["10.0.0.1", "10.0.0.2"])
    r.report_fail("10.0.0.1")
    r.report_fail("10.0.0.1")
    assert r.acquire() == "10.0.0.2"
    r.report_success("10.0.0.1")
    assert r.acquire() == "10.0.0.1", "成功后必须立刻恢复(惩罚清零)"


def test_all_penalized_still_returns_ip():
    """极端情况: 全部 IP 都故障 → 仍要放行(不硬卡请求)"""
    r = net.IPRotator(["10.0.0.1", "10.0.0.2"])
    for ip in ("10.0.0.1", "10.0.0.2"):
        r.report_fail(ip)
        r.report_fail(ip)
    out = r.acquire()
    assert out in ("10.0.0.1", "10.0.0.2"), out


def test_empty_pool_returns_none():
    assert net.IPRotator([]).acquire() is None
    assert net.IPRotator(None).acquire() is None


# ---------------------------------------------------------------- 2. 上下文行为
def test_binding_is_noop_without_pool(monkeypatch):
    """无 IP 池(测试机)时不能绑死任何 IP —— 否则连 127.0.0.1 都会失败"""
    monkeypatch.setattr(net, "_ROTATOR", net.IPRotator([]))
    with net.ip_binding():
        assert getattr(net._LOCAL, "bind_ip", None) is None


def test_binding_sets_and_clears(monkeypatch):
    monkeypatch.setattr(net, "_ROTATOR", net.IPRotator(["10.0.0.7"]))
    with net.ip_binding():
        assert net._LOCAL.bind_ip == "10.0.0.7"
    assert net._LOCAL.bind_ip is None, "出 with 必须清空(否则污染同线程后续请求)"


def test_binding_reports_fail_then_raises(monkeypatch):
    r = net.IPRotator(["10.0.0.8"])
    monkeypatch.setattr(net, "_ROTATOR", r)
    with pytest.raises(RuntimeError):
        with net.ip_binding():
            raise RuntimeError("网络炸了")
    assert r._penalty.get("10.0.0.8") == 1, "异常必须计入惩罚"


def test_patched_create_connection_injects_source(monkeypatch):
    """monkey-patch 生效: 绑定期间 create_connection 应带 source_address"""
    seen = {}

    def fake_orig(address, timeout=None, source_address=None, **kw):
        seen["sa"] = source_address
        return "SOCKET"

    monkeypatch.setattr(net, "_orig_create_connection", fake_orig)
    monkeypatch.setattr(net._LOCAL, "bind_ip", "10.0.0.9")
    net._patched_create_connection(("1.2.3.4", 443))
    assert seen["sa"] == ("10.0.0.9", 0), seen


# ---------------------------------------------------------------- 3. 各数据源共用
def test_fetcher_uses_shared_rotator():
    """fetcher 必须复用 core.net 的 rotator, 不能各持一份(惩罚状态会割裂)"""
    from app.services import fetcher
    assert fetcher._http_get.__module__ == "app.services.fetcher"
    assert fetcher._ip_binding is net.ip_binding


def test_kpl_uses_ip_rotation():
    """开盘啦是竞价数据唯一来源, 必须走轮换(此前 8 处裸 urlopen 单 IP)"""
    import inspect

    from app.services import kpl
    src = inspect.getsource(kpl._urlopen)
    assert "_net.http_get" in src, "kpl._urlopen 必须走公共网络层"
    body = inspect.getsource(kpl._call)
    assert "_urlopen(" in body, "_call 主请求必须走 _urlopen"


def test_kpl_all_requests_go_through_wrapper():
    """不得残留裸 urllib.request.urlopen(否则该请求又变单 IP)"""
    import inspect

    from app.services import kpl
    src = inspect.getsource(kpl)
    assert "urllib.request.urlopen(" not in src, "kpl 仍有裸 urlopen 未接入轮换"


def test_rotator_configured_from_env():
    """config.OUTBOUND_IPS 必须来自环境变量(内网 IP, 公网 IP 无法 bind)"""
    from app.core import config
    assert isinstance(config.OUTBOUND_IPS, list)
    for ip in config.OUTBOUND_IPS:
        assert not ip.startswith("121."), "必须是内网 IP, 公网 IP 在 ECS 上 bind 会失败"
