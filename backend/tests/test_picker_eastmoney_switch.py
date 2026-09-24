# -*- coding: utf-8 -*-
"""换源 WP6「东财收口开关」测试(2026-09-24)。

为什么要这个开关: 猫爪成为主源后, 需要一条**不改代码、只改配置**就能把东财从链路上
摘掉的路径 —— 而不是物理删代码。两个理由:
  * 猫爪仍有字段缺口(`warn_type` f630 / `industry`), 删了就没退路;
  * 生产机东财被墙 ≠ 测试机东财不可用, 删掉等于放弃一个真实可用的备源。

本文件钉住三件事:
  1. `eastmoney_enabled()` 的**失败方向**: 只有明确的 "0/false/False" 才算关;
     读配置抛异常时必须**保持开启**(收口开关不能因为读库失败而意外瘫痪数据链)。
  2. 关闭时东财标签返回 `DisabledSource`(显式报错) 而**不是** `None` ——
     pipeline 拿到 None 会记「未知数据源标签」, 把"配置关停"误报成"配置写错"。
  3. 关闭东财**不影响**其它源, 且 `REGISTRY` 仍完整(否则"每个标签都已注册"的守卫误报)。
"""
import pytest

from app.services.picker.sources import base as sb
from app.services.picker.sources import eastmoney, meoz, snapshot, tencent


@pytest.fixture
def fake_settings(monkeypatch):
    """把 settings.get 换成可控桩; 返回一个 setter 供用例改写返回值。"""
    from app.services import settings as S
    box = {"value": 1, "raise": False}

    def fake_get(key, default=None):
        assert key == "use_eastmoney", "本开关只认 use_eastmoney"
        if box["raise"]:
            raise RuntimeError("settings 表读不到")
        return box["value"]

    monkeypatch.setattr(S, "get", fake_get)
    return box


# ==================== 1. eastmoney_enabled 语义 ====================
def test_enabled_by_default_and_truthy(fake_settings):
    fake_settings["value"] = 1
    assert sb.eastmoney_enabled() is True
    fake_settings["value"] = "1"
    assert sb.eastmoney_enabled() is True
    fake_settings["value"] = ""
    assert sb.eastmoney_enabled() is True, "空值按默认开(只有明确 0/false 才关)"


@pytest.mark.parametrize("off", ["0", 0, "false", "False"])
def test_disabled_values(fake_settings, off):
    fake_settings["value"] = off
    assert sb.eastmoney_enabled() is False


def test_read_failure_keeps_enabled(fake_settings):
    """★失败方向: 读配置异常必须**保持开启**。

    反了会怎样: settings 表被锁/迁移中 ⇒ 所有东财源静默关闭 ⇒ 竞价名单源少两级兜底,
    而且日志里只看到"东财源已关闭", 排查方向直接跑偏。
    """
    fake_settings["raise"] = True
    assert sb.eastmoney_enabled() is True


# ==================== 2. get_source 的门是否只拦东财 ====================
def test_get_source_returns_disabled_for_eastmoney_labels(fake_settings):
    fake_settings["value"] = "0"
    for label in ("eastmoney_market", "eastmoney_realtime"):
        src = sb.get_source(label)
        assert isinstance(src, sb.DisabledSource), "%s 必须显式关停(不是 None)" % label
        assert src.label == label, "label 必须保留, 否则日志里认不出是谁被关了"


def test_disabled_source_reports_reason_and_degrades(fake_settings, monkeypatch):
    """关停必须是**显式失败**: error 里写明是开关关的, 且标降级。"""
    fake_settings["value"] = "0"
    src = sb.get_source("eastmoney_market")
    res = src.run(sb.FetchContext(policy=_auction_policy(), date="2026-09-24"))
    assert not res.ok and "use_eastmoney=0" in (res.error or "")
    assert res.degraded is True


def test_disabled_source_does_no_network(fake_settings, monkeypatch):
    """关停的语义是"**一次网络都不打**" —— 打一次就白耗配额 + 可能触发封禁面。

    注意用**计数**断言而不是"让它抛异常": `BaseSource.run()` 会把异常吞成 error,
    于是"抛异常"版本在**旧代码上也是绿的**(真源打网络 → 抛 → 被吞 → 同样 not ok),
    那就是一条永远为真的空断言。计数才真正区分"没打"与"打了但失败"。
    """
    from app.services import fetcher
    fake_settings["value"] = "0"
    calls = []

    def spy(*a, **k):
        calls.append(a)
        return None, "不该被调用"
    monkeypatch.setattr(fetcher, "ensure_cache", spy)

    src = sb.get_source("eastmoney_market")
    assert isinstance(src, sb.DisabledSource)
    res = src.run(sb.FetchContext(policy=_auction_policy(), date="2026-09-24"))
    assert not res.ok
    assert calls == [], "关停的源不得触发任何取数调用(实际 %d 次)" % len(calls)


def test_get_source_ignores_switch_for_non_eastmoney(fake_settings):
    fake_settings["value"] = "0"
    assert isinstance(sb.get_source("meoz_market"), meoz.MeozMarketSource)
    assert isinstance(sb.get_source("tencent_market"), tencent.TencentMarketSource)
    assert isinstance(sb.get_source("snapshot"), snapshot.SnapshotSource)


def test_registry_stays_complete_when_eastmoney_off(fake_settings):
    """★关掉东财时 REGISTRY 不能缺项 —— 否则 POLICIES 标签守卫会误报"配置写错"。"""
    fake_settings["value"] = "0"
    sb.get_source("eastmoney_market")
    for label in ("snapshot", "eastmoney_market", "eastmoney_realtime",
                  "tencent_point", "tencent_market", "meoz_realtime", "meoz_market"):
        assert label in sb.REGISTRY, "%s 未登记" % label


def test_unknown_label_still_none(fake_settings):
    fake_settings["value"] = 1
    assert sb.get_source("no_such_source") is None


def test_eastmoney_prefix_definition():
    """前缀必须正好覆盖两个东财标签 —— 写成 'east' 会把别人的源也关掉。"""
    assert "eastmoney_market".startswith(sb._EASTMONEY_PREFIX)
    assert "eastmoney_realtime".startswith(sb._EASTMONEY_PREFIX)
    assert not "tencent_market".startswith(sb._EASTMONEY_PREFIX)
    assert not "meoz_market".startswith(sb._EASTMONEY_PREFIX)


# ==================== 3. 真源类不受影响 ====================
def test_real_eastmoney_sources_when_enabled(fake_settings):
    fake_settings["value"] = 1
    assert isinstance(sb.get_source("eastmoney_market"), eastmoney.EastmoneyMarketSource)
    assert isinstance(sb.get_source("eastmoney_realtime"),
                      eastmoney.EastmoneyRealtimeSource)


def _auction_policy():
    from app.services.picker import mode as pm
    return pm.POLICIES[pm.PickMode.AUCTION]
