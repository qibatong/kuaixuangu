# -*- coding: utf-8 -*-
"""P4 锁仓接入层测试。

锁仓(系统批次 / 9:26 自动应用)是**后台线程**跑的, 出错没人看得见 — 这些用例
覆盖"静默失效"类故障: 市场口径大小写、模式拒绝、异常不抛、结果同构可落库。
"""
import time
from datetime import datetime

import pytest

from app.services.picker import lock as plock
from app.services.picker import mode as pm


# ---------------------------------------------------------------- 参数归一化
@pytest.mark.parametrize("raw,expect", [
    (["SH", "SZ", "BJ"], ["hs"]),                 # 大写别名折算, 北交所剔除
    (["hs", "cyb", "kcb"], ["hs", "cyb", "kcb"]),
    ("hs,cyb", ["hs", "cyb"]),                    # 逗号串
    (None, ["hs", "cyb", "kcb"]),                 # 空 → 全市场
    ([], ["hs", "cyb", "kcb"]),
    (["BJS"], ["hs", "cyb", "kcb"]),              # 只有北交所 → 落回全市场(不能变空)
])
def test_norm_markets(raw, expect):
    assert plock.norm_markets(raw) == expect


def test_norm_markets_never_empty():
    """归一化结果绝不能为空 — 空 markets 在老口径="不限制", 在 picker 里会被当
    成"什么都选不出"的边界, 必须显式兜底成全市场。"""
    for raw in (None, [], [""], ["XX"], ["BJ", "BJS"]):
        assert plock.norm_markets(raw)


def test_to_picker_filters_scalar_and_list_forms():
    """兼容标量 dict 与 qs() 的 [str] 形态; 缺失键落兜底"""
    a = plock.to_picker_filters({"bidGt": 9.5, "markets": ["SH", "SZ"]})
    assert a["bidGt"] == 9.5
    assert a["markets"] == ["hs"]
    assert a["probLt"] == 65.0          # 缺失 → 兜底

    b = plock.to_picker_filters({"bidGt": ["9.5"], "stSuspend": ["True"]})
    assert b["bidGt"] == 9.5
    assert b["stSuspend"] is True

    c = plock.to_picker_filters({})
    assert c["markets"] == ["hs", "cyb", "kcb"]
    assert c["bidAmtFloor"] == 1000.0


def test_to_picker_filters_bad_value_falls_back():
    """脏值(空串/非数字)落兜底, 不抛异常 — 后台任务不该因参数脏值崩掉"""
    f = plock.to_picker_filters({"bidGt": "", "probLt": "abc", "limitUp": None})
    assert f["bidGt"] == 7.0
    assert f["probLt"] == 65.0
    assert f["limitUp"] is False


# ---------------------------------------------------------------- 模式门禁
def test_auction_mode_rejects_lock():
    """竞价进行中(9:15-9:25)名单还在变 → 拒绝锁仓, 且**给出原因**不是静默空名单"""
    now = datetime(2026, 9, 8, 9, 20)
    r = plock.run_lock({}, now=now, markets=["hs"])
    assert not r.items
    assert r.mode == pm.PickMode.AUCTION.value
    assert r.errors and "拒绝锁仓" in r.errors[0]


def test_preopen_mode_rejects_lock():
    """盘前当日 9:25 快照还没生成 → 拒绝"""
    now = datetime(2026, 9, 8, 8, 30)
    r = plock.run_lock({}, now=now, markets=["hs"])
    assert not r.items
    assert r.mode == pm.PickMode.PREOPEN.value
    assert r.errors and "拒绝锁仓" in r.errors[0]


@pytest.mark.parametrize("hh,mm,mode", [
    (9, 26, pm.PickMode.LOCKED.value),
    (10, 30, pm.PickMode.INTRADAY.value),
    (16, 0, pm.PickMode.CLOSED.value),
])
def test_lockable_modes_not_rejected(hh, mm, mode, monkeypatch):
    """9:25 之后的三种模式都允许锁仓(名单都来自定格, 幂等)。

    注意 INTRADAY/CLOSED 也必须允许: 调度延迟/手动重跑时系统批次仍要能落库,
    且它们与 LOCKED 取的是同一份定格快照 → 名单一致。
    """
    monkeypatch.setattr(plock.pipeline, "run",
                        lambda f, **kw: plock.pipeline.PipelineResult())
    r = plock.run_lock({}, now=datetime(2026, 9, 8, hh, mm), markets=["hs"])
    assert r.mode == mode
    assert not any("拒绝锁仓" in e for e in r.errors)


# ---------------------------------------------------------------- 健壮性
def test_run_lock_never_raises(monkeypatch):
    """pipeline 内部炸了也要转成结果 + errors, 绝不让后台线程挂掉"""

    def _boom(*a, **kw):
        raise RuntimeError("模拟 pipeline 爆炸")

    monkeypatch.setattr(plock.pipeline, "run", _boom)
    r = plock.run_lock({}, now=datetime(2026, 9, 8, 9, 26), markets=["hs"])
    assert not r.items
    assert r.errors and "异常" in r.errors[0]


def test_run_lock_top_truncates(monkeypatch):
    """top 只影响返回条数(系统批次存 top30), 不影响内部排序"""
    items = [{"code": "60000%d" % i, "probability": 100 - i} for i in range(5)]

    def _fake_run(f, **kw):
        pr = plock.pipeline.PipelineResult()
        pr.items = list(items)
        return pr

    monkeypatch.setattr(plock.pipeline, "run", _fake_run)
    r = plock.run_lock({}, top=2, now=datetime(2026, 9, 8, 9, 26), markets=["hs"])
    assert [i["code"] for i in r.items] == ["600000", "600001"]


def test_run_lock_passes_normalized_markets_to_pipeline(monkeypatch):
    """pipeline 收到的必须是归一化后的小写 markets(否则名单全空, 见 #1578 事故)"""
    seen = {}

    def _fake_run(f, **kw):
        seen["filters"] = f
        seen["ctx"] = kw.get("ctx")
        return plock.pipeline.PipelineResult()

    monkeypatch.setattr(plock.pipeline, "run", _fake_run)
    monkeypatch.setattr(plock.pipeline, "load_context",
                        lambda markets=None: plock.pipeline.PickContext(
                            date="2026-09-08", markets=markets))
    f = {"bidGt": 7.0, "markets": ["SH", "SZ", "BJ"]}
    plock.run_lock(f, now=datetime(2026, 9, 8, 9, 26))
    assert seen["filters"]["markets"] == ["hs"]


# ---------------------------------------------------------------- 开关与对拍
@pytest.mark.parametrize("v,expect", [
    ("1", True), ("true", True), ("True", True), ("yes", True), ("on", True),
    ("0", False), ("", False), (None, False), ("2", False), ("false", False),
])
def test_enabled(v, expect):
    assert plock.enabled(v) is expect


def test_compare_with_legacy_identical():
    items = [{"code": "600000", "probability": 80, "confidence": 70}]
    out = plock.compare_with_legacy(items, items)
    assert out["identical"] is True


def test_compare_with_legacy_detects_diff():
    legacy = [{"code": "600000", "probability": 80, "confidence": 70},
              {"code": "600001", "probability": 70, "confidence": 70}]
    new = [{"code": "600000", "probability": 80, "confidence": 70},
           {"code": "600002", "probability": 90, "confidence": 70}]
    out = plock.compare_with_legacy(new, legacy)
    assert out["identical"] is False
    assert out["legacy_only"] == ["600001"]
    assert out["new_only"] == ["600002"]


def test_compare_failure_never_raises(monkeypatch):
    """对拍本身出错不影响落库"""
    from app.services.picker import parity

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(parity, "diff_items", _boom)
    out = plock.compare_with_legacy([], [])
    assert out["identical"] is None
    assert "error" in out


# ---------------------------------------------------------------- 落库字段
def test_items_have_save_batch_fields(monkeypatch):
    """锁仓结果必须能被 history.save_batch 直接落库(缺字段会整批失败)"""
    from app.services.picker import pipeline as pl

    def _fake_run(f, **kw):
        pr = pl.PipelineResult()
        pr.items = [{"code": "600000", "name": "X", "probability": 80,
                     "confidence": 70, "bidChange": 3.0, "realChange": 3.1,
                     "entityChange": 2.0, "bidTurnover": 1.2, "warnType": 0,
                     "circulationMV": 55.0, "industry": "-", "concept": "-",
                     "bidAmt": 5000.0, "bidRatio": 50.0, "qiangchou": 0}]
        return pr

    monkeypatch.setattr(plock.pipeline, "run", _fake_run)
    r = plock.run_lock({}, now=datetime(2026, 9, 8, 9, 26), markets=["hs"])
    need = ("code", "name", "probability", "confidence", "bidChange", "realChange",
            "entityChange", "bidTurnover", "warnType", "circulationMV", "industry",
            "concept", "bidAmt", "bidRatio", "qiangchou")
    for k in need:
        assert k in r.items[0], "落库字段缺失: %s" % k


# ---------------------------------------------------------------- 接入: 两条锁仓链路
def test_system_batch_switches_by_setting(monkeypatch):
    """settings picker_lock 开关决定 system_batch 走新链路还是老链路(默认老)"""
    from app.services import system_batch as sb

    called = {"new": 0, "legacy": 0}
    monkeypatch.setattr(sb, "_run_new", lambda f, tp: (called.__setitem__("new", 1), [{"code": "600000"}])[1])
    monkeypatch.setattr(sb, "_run_legacy", lambda f, tp: (called.__setitem__("legacy", 1), [{"code": "600001"}])[1])

    monkeypatch.setattr(sb, "_picker_lock_on", lambda: False)
    monkeypatch.setattr(sb, "_has_today_system_batch", lambda d, t: False)
    monkeypatch.setattr(sb.history, "save_batch",
                        lambda **kw: 1 if kw.get("result") else None)
    monkeypatch.setattr(sb.kpl, "apply_board_concept", lambda r, tag: 0)

    sb._do_run("9_25")
    assert called["legacy"] == 1 and called["new"] == 0

    called["legacy"] = called["new"] = 0
    monkeypatch.setattr(sb, "_picker_lock_on", lambda: True)
    sb._do_run("9_25")
    assert called["new"] == 1 and called["legacy"] == 0


def test_system_batch_empty_result_not_saved(monkeypatch):
    """空名单不落库(故障期不产生空批次) — 新老链路都一样"""
    from app.services import system_batch as sb
    saved = []
    monkeypatch.setattr(sb, "_has_today_system_batch", lambda d, t: False)
    monkeypatch.setattr(sb, "_picker_lock_on", lambda: True)
    monkeypatch.setattr(sb, "_run_new", lambda f, tp: [])
    monkeypatch.setattr(sb.kpl, "apply_board_concept", lambda r, tag: 0)
    monkeypatch.setattr(sb.history, "save_batch",
                        lambda **kw: saved.append(kw) or 1)
    sb._do_run("9_25")
    assert not saved


def test_auto_apply_uses_picker_when_enabled(monkeypatch):
    """picker_lock=1 → auto_apply 走新链路; 新链路无结果时回退老链路(不让用户空窗)"""
    from app.services import auto_apply as aa

    calls = {"new": 0, "legacy": 0}

    class _LR:
        items = [{"code": "600000"}]
        errors = []

        def summary(self):
            return ""

    monkeypatch.setattr(aa, "_picker_lock_on", lambda: True)
    monkeypatch.setattr(aa, "_load_strengths", lambda raw: {})
    monkeypatch.setattr(aa, "_legacy_result",
                        lambda: (calls.__setitem__("legacy", 1), [], "")[1])
    import app.services.picker.lock as plk
    monkeypatch.setattr(plk, "run_lock",
                        lambda f, **kw: (calls.__setitem__("new", 1), _LR())[1])
    aa._pick_result()
    assert calls["new"] == 1 and calls["legacy"] == 0

    # 新链路空 → 回退
    class _Empty:
        items = []
        errors = ["拒绝锁仓"]

        def summary(self):
            return ""

    monkeypatch.setattr(plk, "run_lock", lambda f, **kw: _Empty())
    aa._pick_result()
    assert calls["legacy"] == 1


def test_auto_apply_legacy_when_disabled(monkeypatch):
    """默认关 → 老链路(行为零变化)"""
    from app.services import auto_apply as aa
    monkeypatch.setattr(aa, "_picker_lock_on", lambda: False)
    monkeypatch.setattr(aa, "_legacy_result", lambda: ([{"code": "600000"}], ""))
    result, err = aa._pick_result()
    assert result and not err
