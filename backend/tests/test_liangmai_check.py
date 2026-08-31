# -*- coding: utf-8 -*-
"""量脉独立校验任务测试 (feature/liangmai) — mock 数据源, 验证告警触发逻辑
注意: 不设置 LIANGMAI_TOKEN 环境变量(避免污染其他测试文件导致真网络调用)"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch

from app.services import liangmai_check


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, args=None):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def _snap_row(code, bc, name=""):
    return {"code": code, "bid_change": bc, "name": name}


# ---------- 涨停池交叉校验 ----------

@patch.object(liangmai_check.database, "get_conn",
              return_value=FakeConn([_snap_row("000001", 10.0), _snap_row("000002", 9.98),
                                     _snap_row("600519", -1.0),   # 非涨停
                                     _snap_row("300001", 20.0)]))
@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_limit_up",
              return_value=[{"dm": "000001"}, {"dm": "000002"}, {"dm": "600519"}])
def test_limit_up_small_diff_no_alert(mock_lm, mock_notify, mock_db):
    """差异小(1只) → 不告警"""
    diff = liangmai_check.check_limit_up("2026-08-28")
    # 快选 {000001,000002,300001} vs 量脉 {000001,000002,600519}: miss={600519} extra={300001} diff=2
    assert diff == 2
    mock_notify.send_text.assert_not_called()


@patch.object(liangmai_check.database, "get_conn",
              return_value=FakeConn([_snap_row("000001", 10.0)]))
@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_limit_up",
              return_value=[{"dm": str(x)} for x in range(100, 130)])
def test_limit_up_large_diff_alert(mock_lm, mock_notify, mock_db):
    """差异大(量脉30只 vs 快选1只) → 告警"""
    diff = liangmai_check.check_limit_up("2026-08-28")
    assert diff > liangmai_check.DIFF_THRESHOLD
    mock_notify.send_text.assert_called_once()
    assert "涨停池" in mock_notify.send_text.call_args[1]["title"]


@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_limit_up", side_effect=RuntimeError("502"))
def test_limit_up_lm_fail_skip(mock_lm, mock_notify):
    """量脉拉取失败 → 跳过校验不告警"""
    assert liangmai_check.check_limit_up("2026-08-28") is None
    mock_notify.send_text.assert_not_called()


# ---------- 抢筹健康自检 ----------

@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_grab_amount",
              return_value=[{"code": "0005%d" % i, "qcwtje": 100.0, "qczf": 9.8} for i in range(8)])
def test_grab_health_ok(mock_lm, mock_notify):
    assert liangmai_check.check_grab_health("2026-08-31") == 8
    mock_notify.send_text.assert_not_called()


@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_grab_amount", return_value=[])
def test_grab_health_empty_alert(mock_lm, mock_notify):
    liangmai_check.check_grab_health("2026-08-31")
    mock_notify.send_text.assert_called_once()
    assert "为空" in mock_notify.send_text.call_args[0][0]


@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_grab_amount",
              return_value=[{"code": "0005%d" % i, "qcwtje": 100.0, "qczf": 30.0} for i in range(8)])
def test_grab_health_bad_change_alert(mock_lm, mock_notify):
    """抢筹涨幅>25% 异常 → 告警"""
    liangmai_check.check_grab_health("2026-08-31")
    mock_notify.send_text.assert_called_once()
    assert "涨幅异常" in mock_notify.send_text.call_args[0][0]


# ---------- 全市场行情健康自检 ----------

@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_market_all", return_value=[{"f12": str(i)} for i in range(5000)])
def test_market_health_ok(mock_lm, mock_notify):
    assert liangmai_check.check_market_health() == 5000
    mock_notify.send_text.assert_not_called()


@patch.object(liangmai_check, "notify")
@patch.object(liangmai_check.liangmai, "fetch_market_all", side_effect=RuntimeError("502"))
def test_market_health_fail_alert(mock_lm, mock_notify):
    """接口 502 → 告警(兼做服务端稳定性监测)"""
    liangmai_check.check_market_health()
    mock_notify.send_text.assert_called_once()
    assert "告警" in mock_notify.send_text.call_args[1]["title"]
