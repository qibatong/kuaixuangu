# -*- coding: utf-8 -*-
"""选股接口测试: mock 数据源, 不依赖外部网络"""
import time

import pytest

from app.services import fetcher, scorer
from conftest import MOCK_RAW


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def test_ping(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=ping", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and "before930" in d


def test_filter_returns_stocks(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=filter&markets=sh_sz&bid_min=0", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert len(d.get("list", [])) > 0
    # 每条记录必含核心字段
    for s in d["list"]:
        assert s["code"] and s["name"]
        assert s["probability"] >= 0
        assert "bidChange" in s and "realChange" in s


def test_filter_invalid_action(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=hack", headers=hdrs(token))
    assert r.status_code == 400


def test_lock_after_930_rejected(client, first_user, monkeypatch):
    """9:30 后 lock 应被拒(403)"""
    token, _, _ = first_user

    def fake_bj_now():
        return ("2099-01-01", "15:00:00", False)

    monkeypatch.setattr(scorer, "bj_now", fake_bj_now)
    r = client.get("/api/stocks?action=lock&markets=sh_sz", headers=hdrs(token))
    assert r.status_code == 403


def test_lock_before_930_ok(client, first_user, monkeypatch):
    """9:30 前 lock 成功"""
    token, _, _ = first_user

    def fake_bj_now():
        return ("2099-01-01", "09:25:00", True)

    monkeypatch.setattr(scorer, "bj_now", fake_bj_now)
    r = client.get("/api/stocks?action=lock&markets=sh_sz", headers=hdrs(token))
    assert r.status_code == 200
    assert r.json().get("ok")


def test_filter_with_ratio(client, first_user, monkeypatch):
    """昨日成交额 map 有值时(竞价窗口内), 竞价/昨比应算出"""
    token, _, _ = first_user
    # 手动让 fetch_yesterday_amounts 返回 [T日, T-1日] 有值 pair, 且处于竞价窗口
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    import tests.conftest as ct
    orig = fetcher.fetch_yesterday_amounts
    fetcher.fetch_yesterday_amounts = lambda codes: {s["f12"]: [10000.0, 8000.0] for s in MOCK_RAW}
    try:
        r = client.get("/api/stocks?action=filter&markets=sh_sz", headers=hdrs(token))
        assert r.status_code == 200
        for s in r.json()["list"]:
            assert s["bidRatio"] is not None and s["bidRatio"] >= 0
    finally:
        fetcher.fetch_yesterday_amounts = orig


# ---------- 盘中全市场分页拉取 ----------
def test_fetch_eastmoney_all_paginates(monkeypatch):
    """盘中模式全市场拉取: 并发分页拉取, 末页不足200 → 合并时停止, 但所有页已并发提交"""
    calls = {"n": 0}
    def fake_page(fs, page, fid="f3"):
        calls["n"] += 1
        if page == 1:
            return [{"f12": f"60000{i}", "f3": 8.0} for i in range(200)]   # 满页
        return [{"f12": f"00000{i}", "f3": 3.0} for i in range(50)]        # 半页=末页
    monkeypatch.setattr(fetcher, "_fetch_clist_page", fake_page)
    monkeypatch.setattr(fetcher.config, "SPOT_MAX_PAGES", 5)
    out = fetcher.fetch_eastmoney_all("m:1+t:2")
    assert len(out) == 250
    # 并发模式: 所有页在提交阶段就已调用, 共 SPOT_MAX_PAGES 次
    assert calls["n"] == 5


def test_fetch_eastmoney_all_skips_failed_pages(monkeypatch):
    """分页失败跳过, 不影响其他页"""
    calls = {"n": 0}
    def fake_page(fs, page, fid="f3"):
        calls["n"] += 1
        if page == 2:
            raise RuntimeError("boom")
        return [{"f12": f"60000{i}", "f3": 8.0} for i in range(200)]
    monkeypatch.setattr(fetcher, "_fetch_clist_page", fake_page)
    monkeypatch.setattr(fetcher.config, "SPOT_MAX_PAGES", 5)
    out = fetcher.fetch_eastmoney_all("m:1+t:2")
    # page2 失败跳过, 1/3/4/5 页各200 → 800只, 共尝试5次(SPOT_MAX_PAGES)
    assert len(out) == 800
    assert calls["n"] == 5


# ====================================================================
# 腾讯 K 线接口 + 源自动降级 (from test_new_features_20260822)
# ====================================================================

import json
import re
import urllib.request


class _TencentResp:
    """Mock urllib response for Tencent API tests"""
    def __init__(self, payload, encoding="utf-8"):
        if isinstance(payload, (dict, list)):
            self._body = json.dumps(payload).encode(encoding)
        elif isinstance(payload, str):
            self._body = payload.encode(encoding)
        else:
            self._body = bytes(payload)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------- B. 腾讯 K 线接口 ----------

def test_tencent_fetch_chart_day(monkeypatch):
    """腾讯日K: 正确解析 day 周期"""
    from app.services import fetcher

    fake_rows = [
        ["2026-08-18", 10.0, 10.5, 10.8, 9.8, 100000],
        ["2026-08-19", 10.5, 11.0, 11.2, 10.3, 150000],
        ["2026-08-20", 11.0, 11.5, 11.8, 10.8, 120000],
    ]
    payload = {"data": {"sh600001": {"day": fake_rows}}}

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp(payload))

    r = fetcher._fetch_chart_from_tencent("600001", "day")
    assert r["period"] == "day"
    assert r["code"] == "600001"
    assert r["time"] == ["2026-08-18", "2026-08-19", "2026-08-20"]
    assert r["preClose"] == 11.0


@pytest.mark.parametrize("period", ["week", "month"])
def test_tencent_fetch_chart_week_month(period, monkeypatch):
    """腾讯周K/月K: 直接嵌套结构 + qfq 嵌套结构"""
    from app.services import fetcher

    kp = period
    fake_rows = [
        ["W32", 20.0, 22.0, 22.5, 19.5, 500000],
        ["W33", 22.0, 24.0, 24.5, 21.5, 600000],
    ]
    # 直接结构: data.secid.week = rows
    payload_direct = {"data": {"sh600001": {kp: fake_rows}}}

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp(payload_direct))

    r = fetcher._fetch_chart_from_tencent("600001", period)
    assert r["period"] == period
    assert len(r["time"]) == 2
    assert r["open"] == [20.0, 22.0]
    assert r["close"] == [22.0, 24.0]
    assert r["preClose"] == 22.0

    # qfq 嵌套结构
    payload_qfq = {"data": {"sh600001": {"qfq": {kp: fake_rows}}}}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp(payload_qfq))
    r2 = fetcher._fetch_chart_from_tencent("600001", period)
    assert r2["period"] == period
    assert len(r2["time"]) == 2


def test_tencent_fetch_chart_invalid_period_and_empty(monkeypatch):
    """腾讯 K 线: 非法 period → {}; 空 data → {}"""
    from app.services import fetcher

    assert fetcher._fetch_chart_from_tencent("600001", "hour") == {}

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp({}))
    assert fetcher._fetch_chart_from_tencent("600001", "day") == {}


def test_tencent_fetch_minute_parsing(monkeypatch):
    """腾讯分时: 正确解析 time/price/avg/volume"""
    from app.services import fetcher

    minute_rows = [
        "0930 18.50 1500 27750000",
        "0931 18.60 1800 33480000",
        "0932 18.30 2500 45750000",
    ]
    payload = {"data": {"sh600001": {"data": {"data": minute_rows}}}}

    monkeypatch.setattr(fetcher, "_fetch_quote_tencent",
                        lambda code: {"preclose": 18.00})
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp(payload))

    r = fetcher._fetch_minute_from_tencent("sh600001", "600001")
    assert r["period"] == "minute"
    assert r["preClose"] == 18.00
    assert r["time"] == ["09:30", "09:31", "09:32"]
    assert r["price"] == [18.50, 18.60, 18.30]
    assert len(r["avg"]) == 3


def test_tencent_fetch_minute_bad_rows_skipped(monkeypatch):
    """腾讯分时: 坏行被跳过"""
    from app.services import fetcher

    minute_rows = [
        "0930 18.50 1500 27750000",
        "bad-line-short",
        "0931 bad 1800 33480000",
        "0932 18.30 2500 45750000",
    ]
    payload = {"data": {"sh600001": {"data": {"data": minute_rows}}}}

    monkeypatch.setattr(fetcher, "_fetch_quote_tencent",
                        lambda code: {"preclose": 18.00})
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp(payload))

    r = fetcher._fetch_minute_from_tencent("sh600001", "600001")
    assert len(r["time"]) == 2


def test_tencent_fetch_quote_parsing(monkeypatch):
    """腾讯实时行情: 正确解析 qt.gtimg.cn 响应"""
    from app.services import fetcher

    # 按代码期望的位置构造 42 个字段 (0-41), 其余填 "0"
    fields = ["0"] * 42
    fields[0] = "1"
    fields[1] = "测试甲"
    fields[2] = "600001"
    fields[3] = "18.50"   # close
    fields[4] = "18.00"   # preclose
    fields[5] = "18.000"  # open
    fields[6] = "300000"  # volume
    fields[30] = "20260822143000"  # timestamp YYYYMMDDHHMMSS
    fields[33] = "18.55"  # high
    fields[34] = "18.00"  # low
    fields[35] = "48000000000/5000000/4800000000"  # amount(含 / 分隔)
    fields[37] = "4800000000"  # amount 兜底

    gbk_body = 'v_sh600001="' + "~".join(fields) + '";'

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp(gbk_body, encoding="gbk"))

    q = fetcher._fetch_quote_tencent("600001")
    assert q is not None
    assert q["close"] == 18.50
    assert q["preclose"] == 18.00
    assert q["open"] == 18.00
    assert q["volume"] == 300000.0
    assert q["date"] == "2026-08-22"


def test_tencent_fetch_quote_bad_responses(monkeypatch):
    """腾讯行情: 空响应/格式错误 → None"""
    from app.services import fetcher

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp("", encoding="gbk"))
    assert fetcher._fetch_quote_tencent("600001") is None

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: _TencentResp("nothing", encoding="gbk"))
    assert fetcher._fetch_quote_tencent("600001") is None


def test_ensure_latest_period_day_append(monkeypatch):
    """day: 最后一根 < 今天 → 追加"""
    from app.services import fetcher

    data = {
        "period": "day", "time": ["2026-08-20", "2026-08-21"],
        "open": [10.0, 10.5], "close": [10.5, 11.0],
        "high": [10.8, 11.2], "low": [9.8, 10.3],
        "volume": [100000, 150000], "amount": [1050000, 1650000],
    }
    monkeypatch.setattr(fetcher, "_fetch_quote_tencent",
                        lambda code: {"date": "2026-08-22", "open": 11.0, "close": 11.5,
                                      "high": 11.8, "low": 10.8, "volume": 120000,
                                      "amount": 1380000.0})
    r = fetcher._ensure_latest_period(data, "600001")
    assert len(r["time"]) == 3
    assert r["time"][-1] == "2026-08-22"


def test_ensure_latest_period_day_refresh(monkeypatch):
    """day: 最后一根 == 今天 → 刷新"""
    from app.services import fetcher

    data = {
        "period": "day", "time": ["2026-08-21", "2026-08-22"],
        "open": [10.5, 11.0], "close": [11.0, 11.2],
        "high": [11.2, 11.5], "low": [10.3, 10.9],
        "volume": [150000, 100000], "amount": [1650000, 1120000],
    }
    monkeypatch.setattr(fetcher, "_fetch_quote_tencent",
                        lambda code: {"date": "2026-08-22", "open": 11.0, "close": 11.8,
                                      "high": 12.0, "low": 10.5, "volume": 200000,
                                      "amount": 2360000.0})
    r = fetcher._ensure_latest_period(data, "600001")
    assert len(r["time"]) == 2
    assert r["close"][-1] == 11.8
    assert r["high"][-1] == 12.0
    assert r["low"][-1] == 10.5


def test_ensure_latest_period_week_refresh(monkeypatch):
    """week: 最后一根属于当前周 → 刷新"""
    from app.services import fetcher

    data = {
        "period": "week", "time": ["2026-08-21", "2026-08-22"],
        "open": [20.0, 21.0], "close": [21.5, 22.0],
        "high": [22.0, 22.5], "low": [19.5, 20.5],
        "volume": [400000, 500000], "amount": [8600000, 11000000],
    }
    monkeypatch.setattr(fetcher, "_fetch_quote_tencent",
                        lambda code: {"date": "2026-08-22", "open": 21.0, "close": 23.0,
                                      "high": 23.5, "low": 20.5, "volume": 600000,
                                      "amount": 13800000.0})
    r = fetcher._ensure_latest_period(data, "600001")
    assert len(r["time"]) == 2
    assert r["close"][-1] == 23.0


def test_ensure_latest_period_skip_minute_and_empty(monkeypatch):
    """minute 不处理; 空数据原样返回"""
    from app.services import fetcher

    minute_data = {"period": "minute", "time": ["09:30"], "price": [18.5]}
    assert fetcher._ensure_latest_period(minute_data, "600001") == minute_data
    assert fetcher._ensure_latest_period({}, "600001") == {}


def test_ensure_latest_period_quote_failed(monkeypatch):
    """腾讯行情拉取失败 → 原样返回"""
    from app.services import fetcher

    data = {
        "period": "day", "time": ["2026-08-21"],
        "open": [10.5], "close": [11.0], "high": [11.2], "low": [10.3],
        "volume": [150000], "amount": [1650000],
    }
    monkeypatch.setattr(fetcher, "_fetch_quote_tencent", lambda code: None)
    r = fetcher._ensure_latest_period(data, "600001")
    assert r["close"][0] == 11.0


# ---------- C. 源自动降级 ----------
# 2026-09-10 二审: 去兜底 ≠ 删同语义真实源 —— K线恢复 东财→腾讯(同语义真实 OHLC)。
# 本用例随之恢复(它是"降级到同语义真实源"的锁定, 不是"用现价编造竞价字段"那类兜底)。


def test_fetch_stock_chart_robust_falls_back_to_tencent(monkeypatch):
    """东财失败时, 降级腾讯成功"""
    from app.services import fetcher
    from app.core import config
    from urllib.error import URLError

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://em1"])

    def fake_urlopen(req, timeout=5, context=None):
        if "gtimg" in (req.full_url or ""):
            payload = {"data": {"sh600001": {
                "day": [["2026-08-20", 10.0, 10.5, 10.8, 9.8, 100000],
                        ["2026-08-21", 10.5, 11.0, 11.2, 10.3, 150000]]
            }}}
            return _TencentResp(payload)
        raise URLError("eastmoney host down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})
    monkeypatch.setattr(fetcher, "_broken_hosts", {})

    r = fetcher.fetch_stock_chart_robust("600001", "day")
    assert r.get("period") == "day"
    assert len(r.get("time", [])) == 2


def test_fetch_stock_chart_robust_all_sources_fail(monkeypatch):
    """全部源(东财+腾讯)都失败 → 返回 {}"""
    from app.services import fetcher
    from app.core import config
    from urllib.error import URLError

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://em1"])

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: (_ for _ in ()).throw(URLError("down")))
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})
    monkeypatch.setattr(fetcher, "_broken_hosts", {})

    r = fetcher.fetch_stock_chart_robust("600001", "day")
    assert r == {}


# ====================================================================
# fetcher.fetch_stock_chart + /api/stock/chart endpoint (from test_new_features_20260820)
# ====================================================================

import sqlite3 as _sqlite3


class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self): return self
    def __exit__(self, *a): return False


def _mk_kline_rows(n=30, base=10.0, step=0.1, start_date="2026-07-01"):
    """造 n 根日K假数据"""
    rows = []
    from datetime import datetime, timedelta
    s = datetime.strptime(start_date, "%Y-%m-%d")
    for i in range(n):
        d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
        o = round(base + i * step, 2)
        c = round(base + i * step + 0.05, 2)
        h = round(base + i * step + 0.20, 2)
        l = round(base + i * step - 0.05, 2)
        vol = 1000000 + i * 1000
        amt = 15000000 + i * 20000
        rows.append(f"{d},{o},{c},{h},{l},{vol},{amt},2.0")
    return rows


def test_fetch_stock_chart_day_ok(monkeypatch):
    """day 周期 → 正确解析 + 缓存命中"""
    from app.services import fetcher
    from app.core import config

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://mock-em"])
    monkeypatch.setattr(config, "KLINE_TIMEOUT", 1)

    def fake_urlopen(req, timeout=5, context=None):
        return _FakeResp({
            "data": {
                "code": "600001", "name": "测试股份",
                "preKPrice": 17.89,
                "klines": _mk_kline_rows(5),
            }
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})

    r = fetcher.fetch_stock_chart("600001", "day")
    assert r.get("period") == "day"
    assert r.get("code") == "600001"
    assert r.get("name") == "测试股份"
    assert r.get("preClose") == 17.89
    assert len(r["time"]) == 5
    for k in ("open", "close", "high", "low", "volume", "amount"):
        assert len(r[k]) == 5
        assert all(isinstance(x, float) for x in r[k])
    assert all(r["high"][i] >= max(r["open"][i], r["close"][i]) for i in range(len(r["high"])))
    assert all(r["low"][i] <= min(r["open"][i], r["close"][i]) for i in range(len(r["low"])))

    # 第二次调用命中缓存
    hits = [0]

    def fake_urlopen_count(req, timeout=5, context=None):
        hits[0] += 1
        return _FakeResp({"data": {"name": "X", "klines": _mk_kline_rows(1)}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_count)
    r2 = fetcher.fetch_stock_chart("600001", "day")
    assert r2["name"] == "测试股份"
    assert hits[0] == 0


@pytest.mark.parametrize("period,lmt", [("week", 120), ("month", 60)])
def test_fetch_stock_chart_week_month_periods(period, lmt, monkeypatch):
    """week/month 周期: 正确 klt/lmt/secid/preClose"""
    from app.services import fetcher
    from app.core import config
    import urllib.parse

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://mock-em"])
    monkeypatch.setattr(config, "KLINE_TIMEOUT", 1)
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})

    captured_url = {}

    def fake_urlopen(req, timeout=5, context=None):
        captured_url["url"] = req.full_url
        return _FakeResp({
            "data": {
                "code": "000002", "name": "测乙", "f60": 9.75,
                "klines": _mk_kline_rows(10),
            }
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = fetcher.fetch_stock_chart("000002", period)
    assert r["period"] == period
    assert len(r["time"]) == 10
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(captured_url["url"]).query)
    assert qs["secid"] == ["0.000002"]
    expected_klt = {"week": "102", "month": "103"}[period]
    assert qs["klt"] == [expected_klt]
    assert qs["lmt"] == [str(lmt)]
    assert r["preClose"] == 9.75
    assert qs["fqt"] == ["1"]


def test_fetch_stock_chart_minute_ok(monkeypatch):
    """minute 周期 → time/price/avg/volume + preClose + 缓存"""
    from app.services import fetcher
    from app.core import config

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://mock-em"])
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})
    # 2026-09-01: _trim_minute_to_now 盘中会把分时对齐到全天 242 条网格(未交易置空),
    # 测试在交易时段跑会断言失败(数据条数 242≠3) — mock 掉时间网格逻辑, 只验证解析本身
    monkeypatch.setattr(fetcher, "_trim_minute_to_now", lambda r: r)

    mock_trends = [
        "2026-08-20 09:30,18.50,1500,18.45,27750000",
        "2026-08-20 09:31,18.60,1800,,33480000",
        "2026-08-20 09:32,18.30,2500,18.40,45750000",
    ]

    def fake_urlopen(req, timeout=5, context=None):
        return _FakeResp({
            "data": {
                "code": "300003", "name": "测丙",
                "preClose": 18.00,
                "trends": mock_trends,
            }
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = fetcher._fetch_minute_trend("300003")
    assert r["period"] == "minute"
    assert r["code"] == "300003"
    assert r["preClose"] == 18.00
    assert r["time"] == ["09:30", "09:31", "09:32"]
    assert r["price"] == [18.50, 18.60, 18.30]
    assert r["volume"] == [1500.0, 1800.0, 2500.0]
    assert r["avg"] == [18.45, None, 18.40]

    # 缓存命中
    count = [0]

    def fake_c(req, timeout=5, context=None):
        count[0] += 1
        return _FakeResp({"data": {"trends": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_c)
    r2 = fetcher.fetch_stock_chart("300003", "minute")
    assert r2["time"][0] == "09:30"
    assert count[0] == 0


def test_fetch_stock_chart_edge_and_host_fallbacks(monkeypatch):
    """空code/非法period/全HOST熔断 → {}; 坏行跳过"""
    from app.services import fetcher
    from app.core import config

    assert fetcher.fetch_stock_chart("") == {}
    assert fetcher.fetch_stock_chart("600001", "hour") == {}

    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://a", "https://b", "https://c"])

    from urllib.error import URLError

    def all_fail(req, timeout=5, context=None):
        raise URLError("host down")

    monkeypatch.setattr(urllib.request, "urlopen", all_fail)
    r = fetcher.fetch_stock_chart("600001", "day")
    assert r == {}

    # 坏行跳过
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})

    def one_good(req, timeout=5, context=None):
        return _FakeResp({
            "data": {
                "name": "测丁", "preKPrice": 25,
                "klines": [
                    "2026-08-19,25.0,25.5,26.0,24.5,1000,25500,2.0",
                    "bad-line-short",
                    "2026-08-20,bad,26.0,27,24,1500,39000,3.0",
                    "2026-08-21,26.0,26.5,27.0,25.5,2000,53000,2.0",
                ],
            }
        })

    monkeypatch.setattr(urllib.request, "urlopen", one_good)
    r = fetcher.fetch_stock_chart("000004", "day")
    assert r
    assert len(r["time"]) == 2
    assert r["time"][0] == "2026-08-19"
    assert r["time"][1] == "2026-08-21"


def test_fetch_stock_chart_secid_branch(monkeypatch):
    """secid: 6/9开头 → 1.code; 其余 → 0.code"""
    from app.services import fetcher
    assert fetcher._secid("600001") == "1.600001"
    assert fetcher._secid("900901") == "1.900901"
    assert fetcher._secid("000001") == "0.000001"
    assert fetcher._secid("300123") == "0.300123"


# ----- /api/stock/chart endpoint ------------------------------------------------

def test_api_stock_chart_4periods(client, first_user, monkeypatch):
    """四种 period 成功路径"""
    from app.services import fetcher

    def fake_chart(code, period):
        base = {"code": code, "period": period, "name": f"N{code}", "preClose": 10.0}
        if period == "minute":
            return {**base, "time": ["09:30"], "price": [10.1], "avg": [10.1], "volume": [100]}
        return {**base,
                "time": ["2026-08-20"], "open": [10], "close": [10.1],
                "high": [10.2], "low": [9.9], "volume": [1000], "amount": [10000]}

    monkeypatch.setattr(fetcher, "fetch_stock_chart", fake_chart)
    token, _, _ = first_user
    for period in ("minute", "day", "week", "month"):
        r = client.get(f"/api/stock/chart?code=600001&period={period}",
                       headers=hdrs(token))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert d["period"] == period
        assert d["code"] == "600001"


def test_api_stock_chart_invalid_params(client, first_user):
    """缺 code → 400; 非法 period → 400"""
    token, _, _ = first_user
    r = client.get("/api/stock/chart?period=day", headers=hdrs(token))
    assert r.status_code == 400
    assert r.json().get("ok") is False

    r2 = client.get("/api/stock/chart?code=600001&period=hour", headers=hdrs(token))
    assert r2.status_code == 400
    assert "period 非法" in (r2.json().get("msg") or "")


def test_api_stock_chart_fetcher_returns_empty(client, first_user, monkeypatch):
    """fetch_stock_chart 返回 {} → 502"""
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "fetch_stock_chart", lambda *a, **kw: {})
    token, _, _ = first_user
    r = client.get("/api/stock/chart?code=999999&period=day", headers=hdrs(token))
    assert r.status_code == 502
    assert not r.json().get("ok")


# ---------- 抢筹口径改版: 左视图=右视图竞价抢筹集合 (2026-09-01) ----------
def test_score_qiangchou_from_qc_codes():
    """命中右视图"竞价抢筹"代码集 → qiangchou=1; 未命中 → 0(即使满足旧公式涨幅/竞昨比)"""
    # MOCK_RAW 两条: 600001 竞价涨幅3.5% 竞额5e7万? (f616 5e7元=5000万); 000002 竞价涨幅4.8%
    # 旧公式需 bid_ratio>=20 才打标; 新口径只看集合命中
    raw = MOCK_RAW
    # 集合命中 600001 与 000002
    scored = scorer.score_all_stocks(raw, {}, {}, qiangchou_codes={"600001"})
    m = {s["code"]: s for s in scored}
    assert m["600001"]["qiangchou"] == 1     # 命中集合
    assert m["000002"]["qiangchou"] == 0     # 未命中集合 → 不打标(即使竞价涨幅4.8%)
    # 集合为空 → 回退旧公式(不崩, 至少 000002 竞/昨比达标与否由旧公式决定)
    scored2 = scorer.score_all_stocks(raw, {}, {}, qiangchou_codes=set())
    m2 = {s["code"]: s for s in scored2}
    assert "qiangchou" in m2["600001"] and m2["600001"]["qiangchou"] in (0, 1)
    # 未传集合(默认 None) → 走旧公式, 行为与改版前一致
    scored3 = scorer.score_all_stocks(raw, {}, {})
    m3 = {s["code"]: s for s in scored3}
    assert "qiangchou" in m3["600001"]
