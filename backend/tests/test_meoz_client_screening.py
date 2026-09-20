# -*- coding: utf-8 -*-
"""meoz_client.screening_map / free_mv_map 单元测试(2026-09-20 自由流通市值全市场)。

覆盖:
  ① screening_map: 解析 items 矩阵 → {symbol: {字段}}
  ② free_mv_map 取值优先级: screening(全市场) > auc_kp(138) > 本地快照
  ③ screening 缺失时 auc_kp 补位; 两者都缺时本地兜底
"""
import pytest

from app.services import meoz_client as M


def _screening_response(rows):
    return {"code": 200, "data": {"fields": list(rows[0].keys()), "items": [list(r.values()) for r in rows]}}


def test_screening_map_parses(monkeypatch):
    """① screening_map: 解析 + free_float_mv 透传"""
    rows = [
        {"symbol": "000001", "name": "平安银行", "free_float_mv": 9.5e10, "circ_mv": 2.25e11},
        {"symbol": "600519", "name": "贵州茅台", "free_float_mv": 7.15e11, "circ_mv": 1.57e12},
    ]
    monkeypatch.setattr(M, "call_cached", lambda *a, **k: _screening_response(rows))
    m = M.screening_map(date_offset=0)
    assert set(m) == {"000001", "600519"}
    assert m["000001"]["free_float_mv"] == 9.5e10
    assert m["600519"]["name"] == "贵州茅台"


def test_free_mv_map_priority_screening_first(monkeypatch):
    """② free_mv_map: screening 优先(全市场), auc_kp 不覆盖已有值"""
    scr = {"000001": {"free_float_mv": 9.5e10},
           "600519": {"free_float_mv": 7.15e11}}
    kp = {"600519": {"free_float_mv": 1.0},      # 同股 auc_kp 值不应覆盖 screening
          "300999": {"free_float_mv": 2.0e9}}    # screening 缺 → auc_kp 补位
    monkeypatch.setattr(M, "screening_map", lambda **k: scr)
    monkeypatch.setattr(M, "auc_qc_net", lambda **k: kp)
    # 本地兜底: 桩掉 sqlite(用不存在的库, 不抛即可)
    m = M.free_mv_map(date_offset=0)
    assert m["000001"] == 9.5e10
    assert m["600519"] == 7.15e11                # screening 值胜出
    assert m["300999"] == 2.0e9                  # auc_kp 补位


def test_free_mv_map_falls_back_when_screening_empty(monkeypatch):
    """③ screening 空 → 全走 auc_kp"""
    monkeypatch.setattr(M, "screening_map", lambda **k: {})
    monkeypatch.setattr(M, "auc_qc_net", lambda **k: {"600519": {"free_float_mv": 7.15e11}})
    m = M.free_mv_map(date_offset=0)
    assert m == {"600519": 7.15e11}


def test_free_mv_map_swallows_errors(monkeypatch):
    """screening 抛异常 → 静默降级, 不冒泡"""
    def _boom(**k):
        raise RuntimeError("挂了")
    monkeypatch.setattr(M, "screening_map", _boom)
    monkeypatch.setattr(M, "auc_qc_net", lambda **k: {"600519": {"free_float_mv": 7.15e11}})
    m = M.free_mv_map(date_offset=0)
    assert m == {"600519": 7.15e11}
