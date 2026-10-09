# -*- coding: utf-8 -*-
"""meoz_client.call() 网络故障契约测试（2026-10-09 修 UnboundLocalError）。

生产实测复现：
    猫爪网络故障 a=pricelimit line=http://sz.numcat.net:8866/api err=timed out → 切线路
    猫爪调用异常 a=pricelimit err=cannot access local variable 'data' where it is not associated with a value
契约（函数 docstring 自己写的）：**失败返回 None(永不抛异常)**。
"""
from app.services import meoz_client as M


class _Store:
    """替身：信号量/锁直接放行（本测试只关心线路切换与返回契约）。"""

    def acquire_sem(self, *a, **k):
        return "k"

    def release_lock(self, *a, **k):
        return None


def _prep(monkeypatch, lines=("line1", "line2")):
    monkeypatch.setattr(M, "enabled", lambda: True)
    monkeypatch.setattr(M, "quiet_now", lambda: False)
    monkeypatch.setattr(M, "_lines", lambda: list(lines))
    monkeypatch.setattr(M, "_apikey", lambda: "k")
    monkeypatch.setattr(M, "store", _Store())


def test_network_error_then_success_switches_line(monkeypatch):
    """第一条线路网络故障 ⇒ 切第二条并返回其数据（原实现此时会抛 UnboundLocalError）。"""
    _prep(monkeypatch)
    seen = []

    def fake_post(url, payload, timeout):
        seen.append(url)
        if url == "line1":
            raise TimeoutError("timed out")
        return {"code": 200, "data": {"ok": 1}}
    monkeypatch.setattr(M, "_post_one", fake_post)

    got = M.call("limit_pool", params={})
    assert got == {"code": 200, "data": {"ok": 1}}
    assert seen == ["line1", "line2"], "网络故障必须换线路重试"


def test_all_lines_network_error_returns_none(monkeypatch):
    """★ 回归锚点：所有线路都网络故障 ⇒ 返回 None，**不得抛异常**。"""
    _prep(monkeypatch)

    def fake_post(url, payload, timeout):
        raise OSError("connection reset")
    monkeypatch.setattr(M, "_post_one", fake_post)

    assert M.call("limit_pool") is None


def test_network_error_then_non_dict_returns_none(monkeypatch):
    """第一条网络故障、第二条返回非 dict ⇒ 仍按契约返回 None。"""
    _prep(monkeypatch)

    def fake_post(url, payload, timeout):
        if url == "line1":
            raise ConnectionError("reset")
        return "not-a-dict"
    monkeypatch.setattr(M, "_post_one", fake_post)

    assert M.call("limit_pool") is None


def test_stale_data_not_reused_across_lines(monkeypatch):
    """★ 修复的另一个意义：第二条线路网络故障时，**不能**把第一条线路的残留 data 当成功结果。"""
    _prep(monkeypatch, lines=("line1", "line2"))

    def fake_post(url, payload, timeout):
        # 第一条：网络故障；第二条：返回非 dict（模拟"拿到响应但不是 JSON 对象"）
        if url == "line1":
            raise TimeoutError("timed out")
        return None
    monkeypatch.setattr(M, "_post_one", fake_post)

    assert M.call("limit_pool") is None


def test_business_error_returns_none_without_switching(monkeypatch):
    """业务错误(code=1002) ⇒ 返回 None 且**不切线路**（既有语义不能被本次修复破坏）。"""
    _prep(monkeypatch)
    seen = []

    def fake_post(url, payload, timeout):
        seen.append(url)
        return {"code": 1002, "message": "未找到涨跌停池数据"}
    monkeypatch.setattr(M, "_post_one", fake_post)

    assert M.call("limit_pool") is None
    assert seen == ["line1"], "业务错误不切线路"
