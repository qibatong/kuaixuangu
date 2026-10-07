# -*- coding: utf-8 -*-
"""盘中主力净流入动态异动分(2026-09-20 主人四连拍板)

「开盘后主力持续净流入的加分 · AI竞价选股放开到10点 · 10点之前不再锁定」:
  ① 9:30-10:00 用盘中实时主力净额重算 ff 层(档位 max 只加不减) → 修正总分并重排;
  ② 10:00 冻结(settings intraday_ff_scores_{date});
  ③ 只重排不增票; 绝对赋值 → 重复应用幂等;
  ④ 落库/计算缓存保持定格(_maybe_intraday_overlay 返回新 list)。
"""
import pytest

from app.api import stocks as st_mod
from app.services import bid_strength as bs


def _scoring_cfg():
    """scorer.get_scoring_cfg 的最小替身(与 test_bid_strength._cfg 同款 bid_strength 段)。

    ⚠️ v8(2026-10-07) 起 **AI 层已删除**: w_ai / ai_buckets / ai_topn / ai_default
    四个键随之移除; 原本 AI 那 0.25 份额归量比(0.45 → 0.70), 使**权重和仍为 1.0**
    —— 这样下方各处期望值可直接按权重书写, 且差分(bonus)与删除前**逐字相同**
    (原式里 AI 项在 static/live 两侧都是 0.25×0.35, 抵消)。
    """
    return {
        "w_warn": 0.17,
        "factors": {"bid_strength": {
            "buckets": [["3", "9999", 1.0], ["2", "3", 0.85], ["1.5", "2", 0.7],
                        ["1.0", "1.5", 0.55], ["0.6", "1.0", 0.4], ["0", "0.6", 0.25]],
            "default": 0.22,
            "w_vol_ratio": 0.70, "w_ff": 0.30,
            "ff_buckets": [["0.30", "9999", 1.0], ["0.10", "0.30", 0.85],
                           ["0.03", "0.10", 0.7], ["0.005", "0.03", 0.55],
                           ["0.0001", "0.005", 0.45],
                           ["-0.005", "0", 0.30], ["-0.03", "-0.005", 0.20],
                           ["-9999", "-0.03", 0.10]],
            "ff_default": 0.35,
        }},
    }


def _strengths():
    """定格三层输入: A/B 量比同档(1.2→0.55), B 竞价净额无信号(ff None)"""
    return {
        "600001": bs.BidStrength(code="600001", bid_vol_ratio=1.2,
                                 ff_pct=0.01, _free_mv=8e8),
        "600002": bs.BidStrength(code="600002", bid_vol_ratio=1.2,
                                 ff_pct=None, _free_mv=8e8),
    }


def _lst():
    return [
        {"code": "600001", "probability": 80},      # 竞价强者
        {"code": "600002", "probability": 79},      # 竞价差 1 分
    ]


def _patch_common(monkeypatch, secs_wday, net_map):
    """公共 mock: 窗口时间/当日快照/定格信号/盘中净额/评分配置

    bid_strength 在 stocks._intraday_ff_compute 内是函数级 import → patch 真实模块 bs;
    其余(auction_snapshot/scorer/settings)为 stocks 顶层 import → patch 属性即可。
    """
    monkeypatch.setattr(st_mod, "_bj_secs_wday", lambda: secs_wday)
    monkeypatch.setattr(st_mod.auction_snapshot, "has_today_snapshot",
                        lambda date=None: True)
    monkeypatch.setattr(bs, "load", lambda codes, date=None: _strengths())
    monkeypatch.setattr(st_mod, "_main_net_map", lambda codes: net_map)
    monkeypatch.setattr(st_mod.scorer, "get_scoring_cfg", _scoring_cfg)


# ------------------------------------------------------------------ 现算
def test_compute_bonus_only_positive_and_absolute(monkeypatch):
    """B 盘中大买(ff_live 5% → 满分档) → 加分; A 无盘中净额 → 不进 map(保持定格)"""
    # warn_static(B) = 0.70*0.55 + 0.30*0.35       = 0.49
    # warn_live(B)   = 0.70*0.55 + 0.30*1.0        = 0.685
    # bonus = 0.17 × (0.685-0.49) × 100 = 0.17 × 0.195 × 100 = 3.315
    #       → 79 + 3.315 = 82.315 → 取整 82
    # (v8 删 AI 层后差分仍为 0.195: 原 AI 项 0.25×0.35 在 static/live 两侧相同, 抵消)
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 0),
                  {"600002": 0.05 * 8e8})            # 盘中净流入 4000 万
    out = st_mod._intraday_ff_compute(_lst(), "2026-09-21")
    assert out == {"600002": 82}
    assert "600001" not in out                        # 无净额数据 → 不修正


def test_compute_no_net_no_bonus(monkeypatch):
    """盘中净额全缺 → 空 map(全员保持定格)"""
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 0), {})
    assert st_mod._intraday_ff_compute(_lst(), "2026-09-21") == {}


# ------------------------------------------------------------------ 应用
def test_apply_resorts(monkeypatch):
    """动态窗口内: B 加分后反超 A → 原地重排 B 在前"""
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 0),
                  {"600002": 0.05 * 8e8})
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    assert [it["code"] for it in lst] == ["600002", "600001"]
    assert lst[0]["probability"] == 82
    assert lst[1]["probability"] == 80                # A 未修正, 保持定格


def test_apply_idempotent_absolute(monkeypatch):
    """重复应用不叠加(绝对赋值语义)"""
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 0),
                  {"600002": 0.05 * 8e8})
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    first = [it["probability"] for it in lst]
    st_mod._apply_intraday_ff_bonus(lst)
    assert [it["probability"] for it in lst] == first


def test_apply_before_930_noop(monkeypatch):
    """9:30 前(锁定期直读路径) → 完全不动"""
    _patch_common(monkeypatch, (9 * 3600 + 25 * 60, 0), {})
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    assert lst == _lst()


def test_apply_weekend_noop(monkeypatch):
    """周末回放 → 不动"""
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 6), {})
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    assert lst == _lst()


def test_apply_switch_off_noop(monkeypatch):
    """开关 intraday_ff_bonus=0 → 不动(应急回滚通道)"""
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 0), {})
    monkeypatch.setattr(st_mod.settings, "get",
                        lambda k, d=None: 0 if k == st_mod._INTRA_FF_SWITCH else d)
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    assert lst == _lst()


# ------------------------------------------------------------------ 冻结
def test_after_1000_reads_frozen_cache(monkeypatch):
    """≥10:00 只读冻结缓存并应用(不再现算)"""
    frozen = {"600002": 88}
    _patch_common(monkeypatch, (10 * 3600 + 5 * 60, 0), {})
    monkeypatch.setattr(st_mod.settings, "get",
                        lambda k, d=None: frozen if k.startswith("intraday_ff_scores_")
                        else d)
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    assert [it["code"] for it in lst] == ["600002", "600001"]
    assert lst[0]["probability"] == 88


def test_after_1000_missing_cache_computes_and_freezes(monkeypatch):
    """≥10:00 缓存缺失(10 点前无人访问) → 现算一次并冻结写入"""
    written = {}
    _patch_common(monkeypatch, (10 * 3600 + 5 * 60, 0),
                  {"600002": 0.05 * 8e8})
    # 恒返回 default(开关 default=1 生效; 冻结缓存 default=None → 现算)
    monkeypatch.setattr(st_mod.settings, "get", lambda k, d=None: d)
    monkeypatch.setattr(st_mod.settings, "set",
                        lambda k, v: written.__setitem__(k, v))
    lst = _lst()
    st_mod._apply_intraday_ff_bonus(lst)
    assert lst[0]["code"] == "600002"
    assert any(k.startswith("intraday_ff_scores_") for k in written)


# ------------------------------------------------------------------ 重算路径
def test_overlay_returns_new_list_original_untouched(monkeypatch):
    """_maybe_intraday_overlay: 返回新 list(行浅拷贝), 原 result 保持定格分"""
    _patch_common(monkeypatch, (9 * 3600 + 35 * 60, 0),
                  {"600002": 0.05 * 8e8})
    result = _lst()
    out = st_mod._maybe_intraday_overlay(result)
    assert out is not result
    assert out[0]["code"] == "600002"                 # 新 list 已重排+修正
    assert result[0]["code"] == "600001"              # 原 list 纹丝未动(落库/缓存安全)
    assert result[0]["probability"] == 80
    assert result[1]["probability"] == 79


def test_overlay_offwindow_passthrough(monkeypatch):
    """窗口外 → 原对象直通(零拷贝)"""
    _patch_common(monkeypatch, (8 * 3600, 0), {})
    result = _lst()
    assert st_mod._maybe_intraday_overlay(result) is result
