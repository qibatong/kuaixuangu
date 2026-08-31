# -*- coding: utf-8 -*-
"""量脉数据源接入层测试 (feature/liangmai)
mock 网关响应, 不依赖真实 token/网络
注意: 不设置 LIANGMAI_TOKEN 环境变量(避免污染其他测试文件导致真网络调用)"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch

from app.services import liangmai


# ---------- 字段映射 ----------

def _fake_market_rows():
    return [
        {"dm": "600519", "p": 1290.54, "ud": -6.86, "zf": -0.53, "hs": 0.12,
         "cje": 2013281644, "v": 15588, "lb": 0.86, "h": 1305.0, "l": 1286.0,
         "o": 1297.99, "yc": 1297.4, "sz": 1621000000000, "lt": 1621000000000,
         "pe": 19.8, "sjl": 6.4, "zdf60": 1.2, "zdfnc": -2.3, "t": "2026-08-31 15:05:38"},
        {"dm": "000001", "p": 11.72, "ud": 0.07, "zf": 0.6, "hs": 0.35,
         "cje": 802660000, "v": 686033, "lb": 0.86, "h": 11.77, "l": 11.62,
         "o": 11.64, "yc": 11.65, "sz": 227400000000, "lt": 227400000000,
         "pe": 5.8, "sjl": 0.6, "zdf60": 2.0, "zdfnc": 3.0, "t": "2026-08-31 15:05:38"},
    ]


@patch.object(liangmai, "call", return_value={"data": _fake_market_rows()})
@patch.object(liangmai, "_name_map", return_value={"600519": "贵州茅台", "000001": "平安银行"})
def test_fetch_market_all_mapping(mock_name, mock_call, monkeypatch):
    monkeypatch.setattr(liangmai, "_MARKET_ALL_TTL", 0)  # 绕过跨进程节流
    rows = liangmai.fetch_market_all()
    assert len(rows) == 2
    s = rows[0]
    assert s["f12"] == "600519"
    assert s["f14"] == "贵州茅台"
    assert s["f2"] == pytest.approx(1290.54)
    assert s["f3"] == pytest.approx(-0.53)
    assert s["f21"] == pytest.approx(1621000000000)


def test_full_code():
    assert liangmai._full_code("600519") == "600519.SH"
    assert liangmai._full_code("000001") == "000001.SZ"
    assert liangmai._full_code("300750") == "300750.SZ"
    assert liangmai._full_code("688825") == "688825.SH"
    assert liangmai._full_code("920223") == "920223.BJ"
    assert liangmai._full_code("430047") == "430047.BJ"


def test_yesterday_amount_skip_today():
    """跳过今天(未收盘)行, T = 最近已收盘"""
    rows = [
        {"t": "2026-08-27", "a": 3203715700},   # T-1
        {"t": "2026-08-28", "a": 2086008400},   # T
        {"t": "2026-08-31", "a": 2013281600},   # 今天(盘中) 应跳过
    ]
    with patch.object(liangmai, "call", return_value={"data": rows}):
        v = liangmai._fetch_yesterday_amount_one("600519", "20260831")
    assert v == pytest.approx([208600.84, 320371.57], rel=1e-3)


def test_yesterday_amount_insufficient():
    """不足两行返回 None"""
    with patch.object(liangmai, "call", return_value={"data": [{"t": "2026-08-28", "a": 100.0}]}):
        assert liangmai._fetch_yesterday_amount_one("600519", "20260831") is None


@patch.object(liangmai, "call", return_value={"data": [{"code": "000560", "name": "我爱我家",
                                                        "openAmt": 26564000, "qczf": 9.85,
                                                        "qccje": 26035040, "qcwtje": 1805061350}]})
def test_fetch_grab_amount(mock_call):
    rows = liangmai.fetch_grab_amount("2026-08-31", "0", "1")
    assert len(rows) == 1
    assert rows[0]["code"] == "000560"
    assert rows[0]["qczf"] == pytest.approx(9.85)


@patch.object(liangmai, "call", return_value={"data": [{"dm": "000712", "mc": "锦龙股份",
                                                        "zf": 9.97, "lbc": 3, "fbt": "092500",
                                                        "hy": "证券Ⅱ"}]})
def test_fetch_limit_up(mock_call):
    rows = liangmai.fetch_limit_up("2026-08-28")
    assert len(rows) == 1
    assert rows[0]["dm"] == "000712"
    assert rows[0]["lbc"] == 3


def test_no_token_raises():
    with patch.object(liangmai, "_token", return_value=""):
        with pytest.raises(RuntimeError):
            liangmai.call("market_snapshot_all")
