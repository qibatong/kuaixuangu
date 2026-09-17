# -*- coding: utf-8 -*-
"""选股评分 17% 异动因子的**数据源开关**测试 (2026-09-17 新增)。

主人要求: 把评分里占 17% 权重的「异动」改回东财 f630 异动等级。
落地方式 = settings `use_bid_strength` 置 0, 但**光设开关不够** ——
`_load_strength`(pipeline 主链路) 原先直接调 `bid_strength.load`, 绕过了
`enabled()` 判断; 而带判断的 `load_scores` 只被 precompute 物化路径调用,
生产的 `precompute_read` 未启用 → 主链路是唯一生效路径。本文件锁定修复后的语义。
"""
import types

import pytest

from app.services import bid_strength as bs
from app.services.picker import pipeline as pl
from app.services.picker.contract import QuoteRow
from app.services.picker.score import compute_score


# ---------------------------------------------------------------- 开关解析
@pytest.mark.parametrize("raw,expect", [
    ("1", True), ("true", True), ("True", True),
    ("0", False), ("", False), (None, False), ("false", False),
])
def test_enabled_switch_parsing(monkeypatch, raw, expect):
    monkeypatch.setattr(bs, "enabled", bs.enabled)          # 保留真实实现
    from app.services import settings as st
    monkeypatch.setattr(st, "get", lambda k, d=None: raw)
    assert bs.enabled() is expect


def test_enabled_defaults_false_when_db_fails(monkeypatch):
    from app.services import settings as st

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(st, "get", _boom)
    assert bs.enabled() is False                            # 取不到 → 保守关闭


# ---------------------------------------------------------------- 主链路短路
def _ctx(strengths=None, date=None):
    return types.SimpleNamespace(strengths=strengths or {}, date=date)


def test_load_strength_returns_empty_when_switch_off(monkeypatch):
    """开关关闭 → 必须短路返回 {}, 且**不得**调用 bid_strength.load(退回 f630)。"""
    monkeypatch.setattr(bs, "enabled", lambda: False)
    called = {"n": 0}

    def _should_not_be_called(*a, **k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(bs, "load", _should_not_be_called)
    out = pl._load_strength(["600000", "000001"], _ctx())
    assert out == {}
    assert called["n"] == 0, "开关关闭时不得再去加载竞价强度"


def test_load_strength_works_when_switch_on(monkeypatch):
    monkeypatch.setattr(bs, "enabled", lambda: True)
    monkeypatch.setattr(bs, "load", lambda codes, date=None: {
        "600000": bs.BidStrength(code="600000", bid_vol_ratio=3.0)})
    out = pl._load_strength(["600000"], _ctx())
    assert "600000" in out and 0.0 <= out["600000"] <= 1.0


def test_injected_strengths_win_over_switch(monkeypatch):
    """调用方显式注入(测试/对拍)优先, 不受开关影响。"""
    monkeypatch.setattr(bs, "enabled", lambda: False)
    out = pl._load_strength(["600000"], _ctx(strengths={"600000": 0.9}))
    assert out == {"600000": 0.9}


def test_load_strength_swallows_exception(monkeypatch):
    """加载异常必须吞掉返回 {}, 绝不阻塞选股(退回 f630)。"""
    monkeypatch.setattr(bs, "enabled", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("kpl down")

    monkeypatch.setattr(bs, "load", _boom)
    assert pl._load_strength(["600000"], _ctx()) == {}


# ---------------------------------------------------------------- 端到端语义
def _cfg():
    return {
        "w_bid": 0.34, "w_activity": 0.32, "w_warn": 0.17,
        "w_market": 0.11, "w_yesterday": 0.06,
        "conf_warn_high": 10, "conf_turnover": 8, "conf_bid": 7,
        "factors": {
            "bid": {"default": 0.1, "buckets": [["3", "5.5", 1]]},
            "activity": {"default": 0.1, "buckets": [["0.8", "99", 1]]},
            "warn": {"default": 0.18, "buckets": [["5", "6", 1], ["4", "5", 0.85], ["3", "4", 0.6]]},
            "market": {"default": 0.22, "buckets": [["0", "30", 1]]},
            "yesterday": {"default": 0.15, "buckets": [["3", "9.5", 0.9]]},
        },
    }


def _row(warn_type):
    mv = 50e8
    # bid_turnover 是派生属性(bid_amt/float_mv*100), 反推构造出 0.5%
    return QuoteRow(code="600000", name="测试", bid_change=4.0,
                    bid_amt=mv * 0.5 / 100, warn_type=warn_type,
                    float_mv=mv, yesterday_change=5.0)


def test_no_strength_uses_f630_warn_bucket():
    """无 strength(开关关闭后) → 17% 因子按 f630 分档打分。"""
    assert compute_score(_row(5), _cfg(), None).parts["warn"]["score"] == 1.0
    assert compute_score(_row(4), _cfg(), None).parts["warn"]["score"] == 0.85
    assert compute_score(_row(3), _cfg(), None).parts["warn"]["score"] == 0.6


@pytest.mark.parametrize("f630", [0, 1, 2, 9, 10, 11, 12, 14])
def test_f630_out_of_bucket_falls_to_default(f630):
    """🔴 实测 f630 取值域是 0~14, 分档表只覆盖 3/4/5 → 其余全落 default 0.18。

    这条测试是**把已知错配钉死**: 改回东财后 17% 因子对这些票恒为 0.18,
    等于该权重对排序失效(常数不改变名次, 但整体下移 13.9 分, 会挤掉
    scoreFloor=80 边缘的票)。若将来重校分档表, 本测试应同步更新。
    """
    assert compute_score(_row(f630), _cfg(), None).parts["warn"]["score"] == 0.18


def test_strength_overrides_f630_when_present():
    """开关打开(strength 注入) → 直接用连续分数, 不看 f630。"""
    assert compute_score(_row(0), _cfg(), 0.93).parts["warn"]["score"] == 0.93
    assert compute_score(_row(5), _cfg(), 0.30).parts["warn"]["score"] == 0.30
