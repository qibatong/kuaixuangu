# -*- coding: utf-8 -*-
"""把「轮询间隔必须大于缓存 TTL」从注释升级为断言（方案 WP2a / P2 收口）。

背景：补采间隔 ≤ 猫爪 fundflow_kp 缓存 TTL 时，每轮补采都命中上一轮自己写的缓存
      ⇒ 拿到同一份旧值，外部观测为「改了但没效果」。
      该约束原先只写在 auction_snapshot.py 的注释里 —— 注释不会报错，断言会。
"""
import pytest

from app.services import auction_snapshot, meoz_client


def test_interval_equals_ttl_plus_margin():
    """基本式：间隔 = TTL + 5。TTL=30 ⇒ 35，与改动前的硬编码值一致（行为零变化）。"""
    assert auction_snapshot.netfill_interval() == int(meoz_client.cache_ttl("fundflow_kp")) + 5


@pytest.mark.parametrize("ttl", [6, 10, 30, 40, 120])
def test_interval_follows_ttl(monkeypatch, ttl):
    """★ 真正的守卫：不变量不是「碰巧 35 > 30」，而是「间隔由 TTL 推导」。

    改 TTL，间隔必须跟着走 —— 这样「任一侧被改导致静默失效」在结构上不可能发生。
    （原先想写的 `assert 35 > 30` 是弱断言：它只锁住当下的数字，换个 TTL 就失去意义。）
    """
    monkeypatch.setitem(meoz_client._TTL_BY_API, "fundflow_kp", ttl)
    assert auction_snapshot.netfill_interval() == ttl + 5
    assert auction_snapshot.netfill_interval() > ttl


def test_hardcoded_interval_is_detected(monkeypatch):
    """反向保护：若有人把间隔退回硬编码常量 35，本测试必须红。

    手法：把 TTL 抬到远超旧常量（120），推导值应为 125；
    此时若 interval 仍等于 35，说明推导被绕回硬编码。
    """
    monkeypatch.setitem(meoz_client._TTL_BY_API, "fundflow_kp", 120)
    assert auction_snapshot.netfill_interval() == 125
    assert auction_snapshot.netfill_interval() != 35, "间隔未跟随 TTL —— 推导被绕回硬编码"


def test_window_derived_from_contract_is_equivalent():
    """WP1b 等价性锁：契约推导出的窗口/阈值 == 改动前的硬编码值（行为零变化）。

    09:25:35 + 35s == 09:26:10 ； 0.19 × 5209 ≈ 990 ≈ 旧 NETFILL_MIN_N(1000)。
    若契约里的 ready_after / probe.min 被改，这里立刻红 —— 提示补采窗口跟着变了。
    """
    assert auction_snapshot.NETFILL_START_SEC == 9 * 3600 + 26 * 60 + 10
    # 🔴 2026-09-29 主人收紧: 硬上限 09:29:50 → **09:26:30(= 定格时刻)**
    assert auction_snapshot.NETFILL_END_SEC == 9 * 3600 + 26 * 60 + 30
    assert abs(auction_snapshot.NETFILL_MIN_N - 1000) <= 10


def test_netfill_window_sane():
    """窗口顺序 + 硬停早于 9:30 开盘买点。"""
    assert auction_snapshot.NETFILL_START_SEC < auction_snapshot.NETFILL_END_SEC
    assert auction_snapshot.NETFILL_END_SEC <= 9 * 3600 + 30 * 60


def test_ttl_registry_covers_snapshot_apis():
    """登记表必须覆盖所有 _AUC_SNAP_TTL 使用者，漏一个就失去守卫意义。

    ⚠️ 顺带暴露一个既有小瑕疵：index_snapshot / emoindic 用的是**字面量 30**
    （不是常量）—— 即系统里原本有两个独立的「30」。本次改动顺带归口到登记表。
    """
    for api in ("auc_kp", "fundflow_kp", "daily_auc", "daily_auc_detail",
                "daily_auc_fd", "valuation", "screening"):
        assert meoz_client.cache_ttl(api) == 30, api
    # 归口的两个字面量 30 也应在登记表内
    assert meoz_client.cache_ttl("index_snapshot") == 30
    assert meoz_client.cache_ttl("emoindic") == 30
