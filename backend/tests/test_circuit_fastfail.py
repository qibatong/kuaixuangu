# -*- coding: utf-8 -*-
"""2026-09-10 生产雪崩(全站无法登录 38 分钟)修复用例

事故链: 东财故障 → 熔断被"同批成功页"反复解除(横跳) → 每次选股完整重试 30 页
        → 单次 30s+ → 2 worker × 40 槽线程池被占满 → 登录排队 38 分钟。

本文件锁死三件事:
  1. 熔断冷却期内的成功**不解除**熔断(必须冷却结束后的半开探测成功才恢复)
  2. 全市场分页**整批只记一次**熔断采样(不再逐页 _record)
  3. 分页故障能快速失败(取消剩余页 + 抛异常交腾讯兜底), 不再等满 40s
"""
import inspect
import time

import pytest

from app.services import fetcher as F


@pytest.fixture(autouse=True)
def _clean_health():
    """每个用例前后都把 eastmoney_clist 健康状态复位, 避免污染其它测试"""
    _reset()
    yield
    _reset()


def _reset(src="eastmoney_clist"):
    F._HEALTH[src].update(ok=0, fail=0, last_ok=0, last_fail=0, ms_sum=0, ms_cnt=0,
                          down_since=0, cooldown=60, base_cooldown=60,
                          down_threshold=1, fails_in_row=0)


# ---------- 1. 熔断语义: 冷却期内的成功不解除 ----------

def test_success_during_cooldown_does_not_reopen():
    """核心回归: 失败后同批到达的成功不得解除熔断(原实现会, 导致熔断横跳)"""
    F._record("eastmoney_clist", False)
    assert F._check_circuit("eastmoney_clist") is True
    F._record("eastmoney_clist", True)                       # 冷却期内的成功页
    assert F._check_circuit("eastmoney_clist") is True       # 仍在熔断
    assert F._HEALTH["eastmoney_clist"]["down_since"] > 0


def test_success_after_cooldown_recovers():
    """冷却结束后的半开探测成功 → 正常恢复"""
    F._record("eastmoney_clist", False)
    F._HEALTH["eastmoney_clist"]["down_since"] = time.time() - 61
    assert F._check_circuit("eastmoney_clist") is False      # 半开
    F._record("eastmoney_clist", True)
    assert F._HEALTH["eastmoney_clist"]["down_since"] == 0


def test_success_during_cooldown_keeps_backoff():
    """冷却期内的成功不得清零连续失败计数(否则指数退避被抹平)"""
    for _ in range(3):
        F._record("eastmoney_clist", False)
    assert F._HEALTH["eastmoney_clist"]["fails_in_row"] == 3
    F._record("eastmoney_clist", True)
    assert F._HEALTH["eastmoney_clist"]["fails_in_row"] == 3


def test_cooldown_backoff_capped():
    for _ in range(12):
        F._record("eastmoney_clist", False)
    assert F._HEALTH["eastmoney_clist"]["cooldown"] == F._CIRCUIT_MAX_COOLDOWN


# ---------- 2. 分页整批只记一次 + 3. 快速失败 ----------

def test_paging_all_fail_records_once_and_opens_circuit(monkeypatch):
    """30 页全挂: 熔断开 + fail 只 +1(原实现 +30), 且快速失败抛异常交兜底"""
    def boom(fs, p, sort):
        raise RuntimeError("boom")

    monkeypatch.setattr(F, "_fetch_clist_page", boom)
    with pytest.raises(RuntimeError):
        F.fetch_eastmoney_all("fs=x")
    h = F._HEALTH["eastmoney_clist"]
    assert h["fail"] == 1
    assert h["ok"] == 0
    assert F._check_circuit("eastmoney_clist") is True


def test_paging_all_ok_records_success_once(monkeypatch):
    """正常态: 整批只记一次成功, 不熔断, 数据正常返回"""
    monkeypatch.setattr(F, "_fetch_clist_page",
                        lambda fs, p, sort: [{"f12": "600001", "f2": 10}])
    out = F.fetch_eastmoney_all("fs=x")
    assert out, "正常态应返回数据"
    h = F._HEALTH["eastmoney_clist"]
    assert h["fail"] == 0 and h["ok"] == 1
    assert F._check_circuit("eastmoney_clist") is False


def test_paging_minor_failures_not_treated_as_source_down(monkeypatch):
    """少量页失败(1/30)属抖动: 不熔断, 也不快速失败(保住其余 29 页数据)"""
    def flaky(fs, p, sort):
        if p == 7:
            raise RuntimeError("one page timeout")
        return [{"f12": "60000%d" % (p % 10), "f2": 1}]

    monkeypatch.setattr(F, "_fetch_clist_page", flaky)
    out = F.fetch_eastmoney_all("fs=x")
    assert out
    assert F._check_circuit("eastmoney_clist") is False      # 单页抖动不误熔断


# ---------- 4. 登录链路不进 anyio 线程池 ----------

def test_login_routes_are_async():
    """登录/邮箱验证/探活必须为 async: 同步 def 会被慢请求堵在 anyio 线程池里排队"""
    from app.api import auth, health
    assert inspect.iscoroutinefunction(auth.api_login)
    assert inspect.iscoroutinefunction(auth.api_verify_email)
    assert inspect.iscoroutinefunction(auth.api_resend_verify)
    assert inspect.iscoroutinefunction(health.api_health)
