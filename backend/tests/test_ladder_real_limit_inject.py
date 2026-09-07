import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from app.services import kpl


def test_inject_uses_eastmoney_real_days():
    """龙版传媒 pid=5 但东财真实 6 板 → limitUpDays=6(不再回退五板+)"""
    all_ = {5: [{"code": "605577", "name": "龙版传媒", "price": 10.0}]}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(kpl, "real_limit_days", lambda date: {"605577": 6})
        out = kpl._inject_real_limit_days(all_, "2026-09-07")
    assert out[5][0]["limitUpDays"] == 6


def test_inject_fallback_to_pid_when_real_missing():
    """东财偶发未就绪(空) → 用开盘啦 pid 档位兜底(五板+=5), 图不会崩"""
    all_ = {5: [{"code": "605577", "name": "龙版传媒"}]}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(kpl, "real_limit_days", lambda date: {})
        out = kpl._inject_real_limit_days(all_, "2026-09-07")
    assert out[5][0]["limitUpDays"] == 5


def test_inject_does_not_pollute_input():
    """注入必须是 deepcopy, 不污染 fetch_ladder 进程内缓存对象"""
    raw = {"code": "605577", "name": "龙版传媒"}
    all_ = {5: [raw]}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(kpl, "real_limit_days", lambda date: {"605577": 6})
        kpl._inject_real_limit_days(all_, "2026-09-07")
    assert "limitUpDays" not in raw


def test_inject_max_of_pid_and_real():
    """东财值小于开盘啦 pid 档位时取 max(pid, real)"""
    all_ = {5: [{"code": "605577", "name": "龙版传媒"}]}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(kpl, "real_limit_days", lambda date: {"605577": 3})
        out = kpl._inject_real_limit_days(all_, "2026-09-07")
    assert out[5][0]["limitUpDays"] == 5  # max(5, 3)


def test_inject_multi_tier_and_real_six_plus():
    """多档混合: 2 板股东财=2 保持, 5 板档东财=7 注入 7"""
    all_ = {
        2: [{"code": "600001", "name": "二板股"}],
        5: [{"code": "605577", "name": "龙版传媒"}],
    }
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(kpl, "real_limit_days", lambda date: {"600001": 2, "605577": 7})
        out = kpl._inject_real_limit_days(all_, "2026-09-07")
    assert out[2][0]["limitUpDays"] == 2
    assert out[5][0]["limitUpDays"] == 7
