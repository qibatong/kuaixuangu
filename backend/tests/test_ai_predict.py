# -*- coding: utf-8 -*-
"""AI 预测概率层(ai_predict) — 异动分第三层数据源

主人拍板标准(2026-09-20): AI 全市场榜 Top30 ∩ p≥0.80 三档
  p≥0.90 → 1.0 / p≥0.85 → 0.85 / p≥0.80 → 0.70; 低于门槛 → 不进榜(走 default)。
"""
import pytest

from app.services import ai_predict as ap


# ---------------------------------------------------------------- 档位边界
def test_bucket_boundaries():
    b = [["0.90", "1.01", 1.0], ["0.85", "0.90", 0.85], ["0.80", "0.85", 0.70]]
    assert ap._bucket(0.92, b) == 1.0
    assert ap._bucket(0.90, b) == 1.0            # 左闭
    assert ap._bucket(0.86, b) == 0.85
    assert ap._bucket(0.85, b) == 0.85
    assert ap._bucket(0.81, b) == 0.70
    assert ap._bucket(0.80, b) == 0.70
    assert ap._bucket(0.7999, b) is None         # 低于门槛 → 不在榜
    assert ap._bucket(0.50, b) is None
    assert ap._bucket(0.92, None) is None        # 无配置 → 全部不进榜(降级安全)
    assert ap._bucket(0.92, []) is None
    assert ap._bucket(0.92, [["bad", "x", "y"]]) is None   # 坏配置跳过不炸


# ---------------------------------------------------------------- TopN + 交集
@pytest.fixture()
def market(monkeypatch):
    """全市场 35 只: code_00 概率最高 … code_34 最低(0.98 递减到 0.29)"""
    probs = {f"code_{i:02d}": round(0.98 - i * 0.02, 4) for i in range(35)}
    monkeypatch.setattr(ap, "_market_prob_map", lambda date: probs)
    return probs


def test_topn_cap(monkeypatch):
    """全市场按概率取前 topn —— 第 31 名起即使过门槛也不加分(极端日封顶保险)"""
    probs = {f"code_{i:02d}": 0.95 for i in range(40)}       # 40 只全 ≥0.90
    monkeypatch.setattr(ap, "_market_prob_map", lambda date: probs)
    out = ap.ai_score_map([f"code_{i:02d}" for i in range(40)], "2026-09-18",
                          topn=30, buckets=[["0.90", "1.01", 1.0]])
    assert len(out) == 30                        # 封顶 30
    assert all(v == 1.0 for v in out.values())


def test_codes_intersection(market):
    """只返回调用方需要的候选池交集"""
    out = ap.ai_score_map(["code_00", "code_01", "not_in_market"],
                          "2026-09-18", topn=30,
                          buckets=[["0.90", "1.01", 1.0]])
    assert out == {"code_00": 1.0, "code_01": 1.0}


def test_below_threshold_not_in_map(monkeypatch):
    """低于 0.80 的票不进返回 dict(语义: 不在 AI 榜 → 调用方走 ai_default)"""
    probs = {"aaa": 0.95, "bbb": 0.82, "ccc": 0.79, "ddd": 0.50}
    monkeypatch.setattr(ap, "_market_prob_map", lambda date: probs)
    out = ap.ai_score_map(probs.keys(), "2026-09-18", topn=30,
                          buckets=[["0.90", "1.01", 1.0], ["0.85", "0.90", 0.85],
                                   ["0.80", "0.85", 0.70]])
    assert set(out) == {"aaa", "bbb"}
    assert out["aaa"] == 1.0 and out["bbb"] == 0.70


def test_empty_market_degrades(monkeypatch):
    """全市场 map 为空(模型缺失/取数失败) → 返回 {}(层降级, 绝不炸)"""
    monkeypatch.setattr(ap, "_market_prob_map", lambda date: {})
    assert ap.ai_score_map(["1"], "2026-09-18") == {}


def test_bad_topn_falls_back(market):
    """topn 配置坏(0/负/非数字) → 回退 30, 不炸"""
    out = ap.ai_score_map(["code_00"], "2026-09-18", topn=0,
                          buckets=[["0.90", "1.01", 1.0]])
    assert out == {"code_00": 1.0}


# ---------------------------------------------------------------- 模型加载
def test_load_model_missing_file():
    """模型文件不存在(本地开发机/文件被清) → None, 调用方降级 —— 绝不抛异常"""
    import os
    if os.path.exists(ap.MODEL_PATH):
        assert ap._load_model() is not None   # 文件在 → 能加载(或 venv 缺库降级)
    else:
        assert ap._load_model() is None       # 文件缺 → None 降级
