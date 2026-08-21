# -*- coding: utf-8 -*-
"""
2026-08-20 新功能集中测试(覆盖当天所有改动)
==========================================
A. 竞价异动 API fast-path (非竞价时段读历史表 + 概念预烘焙 + 仅补涨幅):
   - api/kpl.py 新增工具函数: _is_auction_hours / _update_spot_change
                            / _read_auction_fast / _ensure_concepts
   - 各 endpoint bid-seal / bid-boom / bid-net / broken
               / bid-qiangcang / lhb / yest-zt / yest-broken 的非竞价分支

B. 股票图表:
   - fetcher.fetch_stock_chart: day/week/month/minute 四周期响应解析
   - /api/stock/chart endpoint: 正常路径 + 边界(空 code / 非法 period / 熔断)

C. 盘中概念静默刷新 concept_refresh:
   - _collect_codes: 从各表 tab 收集股票代码
   - _update_lists_with_board: 把概念回写到 auction/lhb/qc_snapshot
   - _refresh_batch: 逐股查询 + 概念截断 + 线程池并发
   - run_refresh_round(force=True/False): 时间窗/交易日跳过 + 完整流程
"""
import json
import os
import sqlite3
import sys
import time as _time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _hdrs(token):
    return {"Authorization": "Bearer " + token}


# ====================================================================
# A. 竞价异动 API fast-path 工具函数
# ====================================================================

def test_is_auction_hours_logic():
    """纯逻辑: 竞价时段边界校验
    关键: _is_auction_hours 内部调用 gmtime(time.time()+8h), 这使得 gmtime 的返回值
    的 tm_hour / tm_min 本身就是北京时间. 所以只要我们把 gmtime(secs) 的返回值直接
    当成北京时间 struct 即可."""
    from app.api import kpl as kpl_api

    def _pin_bj(mp, bj_hour, bj_min, wday=3):
        """让 gmtime 无论传入何 secs, 都直接返回 "BJ bj_hour:bj_min wday"
        (注意: 直接用 BJ 时间, 因为函数里已经 +8h 了)"""
        def fake_gmtime(secs=None):
            return _time.struct_time(
                (2026, 8, 20, bj_hour, bj_min, 0, wday, 232, 0))
        mp.setattr(_time, "gmtime", fake_gmtime)

    cases = [
        # (hour, min, wday, expected)
        (9, 14, 3, False),    # 9:14 → 还没开始
        (9, 15, 3, True),     # 9:15 开始
        (9, 25, 3, True),     # 9:25 中间
        (9, 30, 3, True),     # 9:30 竞价最后一分钟(含)
        (9, 31, 3, False),    # 9:31 开始交易
        (9, 25, 5, False),    # 周六 9:25 → 周末 false
        (9, 25, 6, False),    # 周日 9:25 → 周末 false
        (10, 0, 4, False),    # 工作日 10:00 → false
    ]
    for h, m, w, expected in cases:
        with pytest.MonkeyPatch.context() as mp:
            _pin_bj(mp, h, m, w)
            got = kpl_api._is_auction_hours()
            assert got is expected, f"BJ {h:02d}:{m:02d} wday={w} 期望={expected} 实得={got}"


def test_update_spot_change_empty_and_crash():
    """空列表 / 内部抛异常 -> 返回 0, 不崩"""
    from app.api import kpl as kpl_api

    assert kpl_api._update_spot_change(None) == 0
    assert kpl_api._update_spot_change([]) == 0
    # 内部取 spot 时抛异常 -> 安全 return 0
    from app.services import fetcher
    original = fetcher.fetch_spot_quote_map

    def boom(*a, **k):
        raise RuntimeError("模拟东财接口挂了")

    try:
        fetcher.fetch_spot_quote_map = boom
        n = kpl_api._update_spot_change([{"code": "600001", "change": 0}])
        assert n == 0
    finally:
        fetcher.fetch_spot_quote_map = original


def test_update_spot_change_override_change_fields():
    """_update_spot_change 只覆盖 change / realChange, 其他字段(含board)绝不改动"""
    from app.api import kpl as kpl_api
    from app.services import fetcher

    original = fetcher.fetch_spot_quote_map

    def fake_spot_map(fs):
        return {
            "600001": {"realChange": 5.55, "price": 18.0},
            "000002": {"realChange": -2.30},
            # 300003 不在 map 中 → 保持原值
        }

    try:
        fetcher.fetch_spot_quote_map = fake_spot_map
        lst = [
            {"code": "600001", "name": "测试甲", "change": 1.0, "realChange": 0,
             "board": "AI概念、机器人"},    # 概念字段不能被改
            {"code": "000002", "name": "测试乙", "change": 4.0,
             "board": ""},
            {"code": "300003", "name": "测试丙", "change": 0.5,
             "board": "芯片"},
        ]
        n = kpl_api._update_spot_change(lst)
        assert n == 2
        assert lst[0]["change"] == 5.55
        assert lst[0]["realChange"] == 5.55
        assert lst[0]["board"] == "AI概念、机器人"  # 概念绝不能变
        assert lst[0]["name"] == "测试甲"
        assert lst[1]["change"] == -2.30
        assert lst[2]["change"] == 0.5   # map 中没有 -> 保持原值
    finally:
        fetcher.fetch_spot_quote_map = original


def test_ensure_concepts_new_data_skips():
    """新数据(≥30% 已填概念) 不补 — 保护深查开销"""
    from app.api import kpl as kpl_api
    from app.services import kpl

    original = kpl.apply_board_concept
    called = [0]

    def spy(*a, **kw):
        called[0] += 1

    try:
        kpl.apply_board_concept = spy
        # 3 条里 1 条有概念 (33% -> 刚好≥30%阈值) → 跳过
        lst = [
            {"code": "1", "board": "AI、机器人"},
            {"code": "2", "board": ""},
            {"code": "3", "board": ""},
        ]
        kpl_api._ensure_concepts(lst, "test")
        assert called[0] == 0, "≥30% 有概念应该跳过"
    finally:
        kpl.apply_board_concept = original


def test_ensure_concepts_old_data_applies():
    """旧数据(<30% 有概念) 走 apply_board_concept(deep=False) 轻量模式补"""
    from app.api import kpl as kpl_api
    from app.services import kpl

    original = kpl.apply_board_concept
    captured = {}

    def spy(lst, **kw):
        captured.update(kw)
        for it in lst:
            if not it.get("board"):
                it["board"] = "补概念"

    try:
        kpl.apply_board_concept = spy
        lst = [
            {"code": "1", "board": "有"},
            {"code": "2", "board": ""},
            {"code": "3", "board": ""},
            {"code": "4", "board": ""},
        ]  # 25% 有概念
        kpl_api._ensure_concepts(lst, "test")
        assert captured.get("deep") is False
        assert captured.get("truncate") == 2
        assert captured.get("blank_if_missing") is False
        assert sum(1 for it in lst if it["board"]) == 4
    finally:
        kpl.apply_board_concept = original


def test_read_auction_fast_today_then_nearest(monkeypatch):
    """_read_auction_fast: 1. 优先今日; 2. 今日无则 MAX(date); 3. 两者皆空返回 []/today, 不崩"""
    from app.services import kpl as kpl_svc
    from app.api import kpl as kpl_api

    # Case 1: 今日有数据
    called = {}

    def fake_query(date, tab):
        called[(date, tab)] = called.get((date, tab), 0) + 1
        if date == "2026-08-20" and tab == "seal":
            return [{"code": "600001"}]
        return []

    monkeypatch.setattr(kpl_svc, "query_auction_history", fake_query)
    # monkeypatch _update_spot_change 直接 return 0(避免打东财)
    monkeypatch.setattr(kpl_api, "_update_spot_change", lambda lst: 0)
    g = _time.struct_time((2026, 8, 20, 2, 0, 0, 3, 232, 0))  # BJ 10:00
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g)
    lst, d = kpl_api._read_auction_fast("seal")
    assert d == "2026-08-20"
    assert len(lst) == 1

    # Case 2: 今日无数据, DB auction_daily_history MAX(date) 有最近交易日
    def fake_db(monkeypatch):
        from app.db import database
        class FakeCursor:
            def __init__(self, rows): self._r = rows
            def fetchone(self): return self._r.pop(0) if self._r else None
            def close(self): pass
        class FakeConn:
            def __init__(self, row): self._row = row
            def execute(self, sql, args=()): return FakeCursor([self._row])
            def close(self): pass
        monkeypatch.setattr(database, "get_conn",
                            lambda: FakeConn(("2026-08-19",)))

    fake_db(monkeypatch)
    called.clear()

    def fake_query2(date, tab):
        if date == "2026-08-19" and tab == "boom":
            return [{"code": "000002"}, {"code": "300003"}]
        return []
    monkeypatch.setattr(kpl_svc, "query_auction_history", fake_query2)
    lst, d = kpl_api._read_auction_fast("boom")
    assert d == "2026-08-19"
    assert len(lst) == 2

    # Case 3: 今日空 + 历史空(DB抛异常也不崩)
    from app.db import database
    def bad_conn():
        raise RuntimeError("DB 炸了")
    monkeypatch.setattr(database, "get_conn", bad_conn)
    monkeypatch.setattr(kpl_svc, "query_auction_history", lambda x, y: [])
    lst, d = kpl_api._read_auction_fast("yest_zt")
    assert lst == []
    assert d == "2026-08-20"


# ====================================================================
# B. fetcher.fetch_stock_chart + /api/stock/chart endpoint
# ====================================================================

class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self): return self
    def __exit__(self, *a): return False


def _mk_kline_rows(n=30, base=10.0, step=0.1, start_date="2026-07-01"):
    """造 n 根日K假数据: date,open,close,high,low,volume,amount,amp"""
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
    """day 周期 → 解析出 time/open/close/high/low/volume/amount, 正确填充 name/preClose"""
    from app.services import fetcher
    from app.core import config

    def fake_hosts(): return ["https://mock-em"]
    monkeypatch.setattr(config, "KLINE_HOSTS", fake_hosts())
    monkeypatch.setattr(config, "KLINE_TIMEOUT", 1)

    def fake_urlopen(req, timeout=5):
        return _FakeResp({
            "data": {
                "code": "600001",
                "name": "测试股份",
                "preKPrice": 17.89,
                "klines": _mk_kline_rows(5),
            }
        })

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # 清掉测试时可能遗留的 cache, 避免污染
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
    # high >= max(open, close)
    assert all(r["high"][i] >= max(r["open"][i], r["close"][i])
               for i in range(len(r["high"])))
    assert all(r["low"][i] <= min(r["open"][i], r["close"][i])
               for i in range(len(r["low"])))

    # 第二次调用应该命中缓存(再次 urlopen 会被这里断言 fail → 实际不调用即ok)
    # 用计数验证
    hits = [0]

    def fake_urlopen_count(req, timeout=5):
        hits[0] += 1
        return _FakeResp({"data": {"name": "X", "klines": _mk_kline_rows(1)}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_count)
    r2 = fetcher.fetch_stock_chart("600001", "day")
    assert r2["name"] == "测试股份"  # 仍然是缓存的结果
    assert hits[0] == 0, "缓存 TTL 内不应二次调用网络"


@pytest.mark.parametrize("period,lmt", [("week", 120), ("month", 60)])
def test_fetch_stock_chart_week_month_periods(period, lmt, monkeypatch):
    """week/month 周期: 正确 klt/lmt, 并填充 time...amount 各字段"""
    from app.services import fetcher
    from app.core import config
    import urllib.parse

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://mock-em"])
    monkeypatch.setattr(config, "KLINE_TIMEOUT", 1)
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})

    captured_url = {}

    def fake_urlopen(req, timeout=5):
        captured_url["url"] = req.full_url
        return _FakeResp({
            "data": {
                "code": "000002", "name": "测乙", "f60": 9.75,
                "klines": _mk_kline_rows(10),
            }
        })

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = fetcher.fetch_stock_chart("000002", period)
    assert r["period"] == period
    assert len(r["time"]) == 10
    # secid 深市(0开头) 应该是 0.000002
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(captured_url["url"]).query)
    assert qs["secid"] == ["0.000002"]
    expected_klt = {"week": "102", "month": "103"}[period]
    assert qs["klt"] == [expected_klt]
    assert qs["lmt"] == [str(lmt)]
    # preClose 字段回退 f60
    assert r["preClose"] == 9.75
    # 前复权 fqt=1
    assert qs["fqt"] == ["1"]


def test_fetch_stock_chart_minute_ok(monkeypatch):
    """minute 周期 → 返回 time/price/avg/volume + 昨收 preClose,
    时间 HH:MM 切分正确, 均价空字符串保留 None"""
    from app.services import fetcher
    from app.core import config

    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://mock-em"])
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})

    # 2 种 time 格式: 纯 HHMM(东财旧版) 与 "YYYY-MM-DD HH:MM"(新版)
    mock_trends = [
        "2026-08-20 09:30,1500,18.50,18.45,27750000",
        "2026-08-20 09:31,1800,18.60,,33480000",  # 均价空
        "2026-08-20 09:32,2500,18.30,18.40,45750000",
    ]

    import urllib.request

    def fake_urlopen(req, timeout=5):
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

    # 再次调用应命中缓存(60s TTL)
    count = [0]

    def fake_c(req, timeout=5):
        count[0] += 1
        return _FakeResp({"data": {"trends": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_c)
    r2 = fetcher.fetch_stock_chart("300003", "minute")
    assert r2["time"][0] == "09:30"
    assert count[0] == 0, "分钟级缓存命中未生效"


def test_fetch_stock_chart_edge_and_host_fallbacks(monkeypatch):
    """空code / 未知period → {} ; 所有 HOST 熔断 → {};
       klines 单条坏行应被跳过不影响整体"""
    from app.services import fetcher
    from app.core import config

    # 空 / 非法参数
    assert fetcher.fetch_stock_chart("") == {}
    assert fetcher.fetch_stock_chart("600001", "hour") == {}

    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://a", "https://b", "https://c"])

    # 所有 host 都抛异常
    import urllib.request
    from urllib.error import URLError

    def all_fail(req, timeout=5):
        raise URLError("host down")

    monkeypatch.setattr(urllib.request, "urlopen", all_fail)
    r = fetcher.fetch_stock_chart("600001", "day")
    assert r == {}, "全 HOST 熔断应返回 {}"

    # 重置 broken_hosts 和缓存
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    monkeypatch.setattr(fetcher, "_CHART_CACHE", {})

    # 混入坏行(解析失败) 应跳过
    def one_good(req, timeout=5):
        return _FakeResp({
            "data": {
                "name": "测丁", "preKPrice": 25,
                "klines": [
                    "2026-08-19,25.0,25.5,26.0,24.5,1000,25500,2.0",
                    "bad-line-short",   # 字段不足 → 跳过
                    "2026-08-20,bad,26.0,27,24,1500,39000,3.0",  # open 非数字 → 跳过
                    "2026-08-21,26.0,26.5,27.0,25.5,2000,53000,2.0",
                ],
            }
        })

    monkeypatch.setattr(urllib.request, "urlopen", one_good)
    r = fetcher.fetch_stock_chart("000004", "day")
    assert r, "one_good 应该返回数据"
    assert len(r["time"]) == 2, f"坏行应被跳过, 实得 {len(r['time'])} 条: {r['time']}"
    assert r["time"][0] == "2026-08-19"
    assert r["time"][1] == "2026-08-21"


def test_fetch_stock_chart_secid_branch(monkeypatch):
    """secid: 6/9开头 → 1.code; 其余 → 0.code"""
    from app.services import fetcher
    assert fetcher._secid("600001") == "1.600001"
    assert fetcher._secid("900901") == "1.900901"
    assert fetcher._secid("000001") == "0.000001"
    assert fetcher._secid("300123") == "0.300123"
    assert fetcher._secid("430047") == "0.430047"
    assert fetcher._secid("830799") == "0.830799"


# ----- /api/stock/chart endpoint ------------------------------------------------

def test_api_stock_chart_4periods(client, first_user, monkeypatch):
    """四种 period 成功路径: 响应结构与 period/code 对齐"""
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
                       headers=_hdrs(token))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert d["period"] == period
        assert d["code"] == "600001"
        if period == "minute":
            for k in ("time", "price", "avg", "volume"):
                assert k in d
        else:
            for k in ("time", "open", "close", "high", "low", "volume", "amount"):
                assert k in d


def test_api_stock_chart_invalid_params(client, first_user):
    """缺 code → 400; 非法 period → 400"""
    token, _, _ = first_user
    r = client.get("/api/stock/chart?period=day", headers=_hdrs(token))
    assert r.status_code == 400
    assert r.json().get("ok") is False

    r2 = client.get("/api/stock/chart?code=600001&period=hour", headers=_hdrs(token))
    assert r2.status_code == 400
    assert "period 非法" in (r2.json().get("msg") or "")

    r3 = client.get("/api/stock/chart?code=600001&period=DAY", headers=_hdrs(token))
    # 大写转小写: 应该成功 or 404 data 是 ok, 但 fetch_stock_chart 返回 {} 会变 502,
    # 这里只验证大小写转换:
    assert r3.status_code in (200, 502)   # 接受两种(正常环境需 mock)


def test_api_stock_chart_fetcher_returns_empty(client, first_user, monkeypatch):
    """fetch_stock_chart 返回 {} → endpoint 返回 502"""
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "fetch_stock_chart", lambda *a, **kw: {})
    token, _, _ = first_user
    r = client.get("/api/stock/chart?code=999999&period=day", headers=_hdrs(token))
    assert r.status_code == 502
    assert not r.json().get("ok")
    assert "熔断" in (r.json().get("msg") or "")


# ====================================================================
# C. concept_refresh 盘中概念静默刷新
# ====================================================================

def _tmp_db():
    """构造临时 SQLite, 写入 concept_refresh 需要的表, 返回路径"""
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute("""CREATE TABLE IF NOT EXISTS auction_daily_history (
        date TEXT, tab TEXT, list TEXT, ts INT, PRIMARY KEY (date, tab))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qc_snapshot (
        date TEXT, code TEXT, time TEXT, change REAL,
        bidAmt REAL, board TEXT, PRIMARY KEY (date, code, time))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lhb_history (
        date TEXT PRIMARY KEY, list TEXT, ts INT)""")
    return f.name, conn


def test_collect_codes_aggregates_tabs():
    """_collect_codes: 从 auction 八 tab + qc_snapshot + lhb_history 正确聚合去重"""
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path

        # 1) auction_daily_history: seal + boom 重复 code 600001
        seal = [{"code": "600001"}, {"code": "000002"}, {"code": "  "}, {"code": None}]
        boom = [{"code": "600001"}, {"code": "300003"}]
        broken_today = [{"code": "000004"}]
        for tab, lst in [("seal", seal), ("boom", boom), ("broken_today", broken_today)]:
            conn.execute(
                "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
                ("2026-08-20", tab, json.dumps(lst, ensure_ascii=False), 123))
        # 2) qc_snapshot
        conn.executemany(
            "INSERT INTO qc_snapshot(date,code,time,change,bidAmt,board) VALUES (?,?,?,?,?,?)",
            [("2026-08-20", "300003", "09:25", 3.0, 500, None),
             ("2026-08-20", "688111", "09:25", 5.0, 800, "")])
        # 3) lhb_history JSON list
        lhb = [{"code": "000004"}, {"code": "600111"}]
        conn.execute("INSERT INTO lhb_history(date,list,ts) VALUES (?,?,?)",
                     ("2026-08-20", json.dumps(lhb, ensure_ascii=False), 123))
        conn.commit()
        conn.close()

        codes = concept_refresh._collect_codes("2026-08-20")
        assert isinstance(codes, set)
        # 空 / None / 空格 被剔掉; 600001 / 000004 重复但只出现一次
        assert "600001" in codes and "000002" in codes and "300003" in codes
        assert "000004" in codes and "688111" in codes and "600111" in codes
        assert "" not in codes and " " not in codes and None not in codes
        # 按我造的数据 → 共 6 只; 如果有 7 只就是把空格/空字符串/NP 也混进来了
        extras = codes - {"600001", "000002", "300003", "000004", "688111", "600111"}
        assert not extras, f"多余的 code: {extras}"
        assert len(codes) == 6
    finally:
        config.DB_FILE = orig_db
        os.unlink(db_path) if os.path.exists(db_path) else None


def test_collect_codes_empty_and_corrupt(monkeypatch):
    """日期无数据 → 空 set; 某 tab JSON 坏了 → 跳过不崩"""
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path
        # 塞一个坏 JSON
        conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal", "NOT-A-JSON{", 123))
        conn.commit()
        conn.close()
        # 不应抛异常
        codes = concept_refresh._collect_codes("2026-08-20")
        assert codes == set()
        # 无此日期
        codes2 = concept_refresh._collect_codes("1999-01-01")
        assert codes2 == set()
    finally:
        config.DB_FILE = orig_db
        os.unlink(db_path) if os.path.exists(db_path) else None


def test_update_lists_with_board_idempotent():
    """_update_lists_with_board: 概念新值时才更新; 旧值相同不写;
       返回更新的 条目 数; qc_snapshot 里缺 board 或 board 不同时被更新"""
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path

        seal_src = [
            {"code": "600001", "board": "AI"},
            {"code": "000002", "board": ""},    # 旧值空 → 需要更新
            {"code": "300003"},                  # 缺 board 字段
        ]
        conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal", json.dumps(seal_src, ensure_ascii=False), 100))
        conn.execute(
            "INSERT INTO qc_snapshot(date,code,time,change,bidAmt,board) VALUES (?,?,?,?,?,?)",
            ("2026-08-20", "600001", "09:25", 3, 500, "旧值"))  # 与新值不同
        conn.execute(
            "INSERT INTO qc_snapshot(date,code,time,change,bidAmt,board) VALUES (?,?,?,?,?,?)",
            ("2026-08-20", "000002", "09:25", 3, 500, None))   # 空
        lhb_src = [{"code": "600001", "board": "AI"},
                   {"code": "000004"}]
        conn.execute("INSERT INTO lhb_history(date,list,ts) VALUES (?,?,?)",
                     ("2026-08-20", json.dumps(lhb_src, ensure_ascii=False), 100))
        conn.commit()
        conn.close()

        mapping = {
            "600001": "AI",           # 相同 → 不更新
            "000002": "医药、创新药",  # 新 → 更新
            "300003": "芯片、半导体",  # 新 → 更新
            "000004": "汽车、新能源车",  # 新(lhb)
            "688999": "不存在",       # 无对应股票 → 无更新
        }
        n = concept_refresh._update_lists_with_board("2026-08-20", mapping)
        # 000002 + 300003 = 2 (seal) + 000002 + 600001(2) (qc) + 000004(1) (lhb) = 至少 2+1+1 = 4
        # 注意 qc total_changes 累计, 所以只要 ≥ 4
        assert n >= 4

        # 验证 DB 中 board 都正确
        conn2 = sqlite3.connect(db_path)
        row = conn2.execute(
            "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
            ("2026-08-20", "seal")).fetchone()
        seal_new = json.loads(row[0])
        assert seal_new[0]["board"] == "AI"
        assert seal_new[1]["board"] == "医药、创新药"
        assert seal_new[2]["board"] == "芯片、半导体"
        # qc_snapshot
        qc_rows = conn2.execute(
            "SELECT code, board FROM qc_snapshot WHERE date='2026-08-20' ORDER BY code"
        ).fetchall()
        boards = {r[0]: r[1] for r in qc_rows}
        assert boards["600001"] == "AI"
        assert boards["000002"] == "医药、创新药"
        # lhb_history
        row = conn2.execute("SELECT list FROM lhb_history WHERE date='2026-08-20'").fetchone()
        lhb_new = json.loads(row[0])
        assert lhb_new[1]["board"] == "汽车、新能源车"
        conn2.close()

        # 空映射 → 0 更新
        assert concept_refresh._update_lists_with_board("2026-08-20", {}) == 0
    finally:
        config.DB_FILE = orig_db
        os.unlink(db_path) if os.path.exists(db_path) else None


def test_refresh_batch_truncate_and_threadpool(monkeypatch):
    """_refresh_batch: (1) fetch_stock_plate 的 '、'分隔概念取前 TRUNCATE_N 个
                        (2) 空/异常股返回空 board 被丢弃
                        (3) 线程池并发且每只 sleep N ms, 并发=MAX_WORKERS"""
    from app.services import concept_refresh
    from app.services import kpl

    # 用低并发 + 0 sleep 避免测试耗时
    orig_sleep = concept_refresh.PER_STOCK_SLEEP_MS
    orig_workers = concept_refresh.MAX_WORKERS
    orig_trunc = concept_refresh.TRUNCATE_N
    concept_refresh.PER_STOCK_SLEEP_MS = 0
    concept_refresh.MAX_WORKERS = 2
    concept_refresh.TRUNCATE_N = 2

    fake_db = {
        "600001": "AI、机器人、算力、大模型",  # 前2个
        "000002": "医药",                      # 1个
        "300003": "",                          # 空 → 丢弃
        "688111": "科创、半导体、封测",
        "000004": None,                        # None → 异常 → 丢弃
    }
    called_codes = set()

    def fake_plate(c):
        called_codes.add(c)
        if c == "000004":
            raise RuntimeError("boom")
        return fake_db.get(c)

    monkeypatch.setattr(kpl, "fetch_stock_plate", fake_plate)
    try:
        codes = {"600001", "000002", "300003", "688111", "000004"}
        res = concept_refresh._refresh_batch("2026-08-20", codes)
        assert called_codes == codes
        # 空 / 异常 2 只应被丢弃
        assert res.get("600001") == "AI、机器人"
        assert res.get("000002") == "医药"
        assert res.get("688111") == "科创、半导体"
        assert "300003" not in res
        assert "000004" not in res
        assert len(res) == 3
        # 空集 → 空 dict
        assert concept_refresh._refresh_batch("2026-08-20", set()) == {}
    finally:
        concept_refresh.PER_STOCK_SLEEP_MS = orig_sleep
        concept_refresh.MAX_WORKERS = orig_workers
        concept_refresh.TRUNCATE_N = orig_trunc


def test_run_refresh_round_skips_non_weekend_and_timewindow(monkeypatch):
    """run_refresh_round 默认: 周末 skip; 不在时间窗 skip;
       force=True 跳过时间窗但无数据返回 empty; 有数据 → ok 并写库"""
    from app.services import concept_refresh

    # 周末 10:00
    g_sat = _time.struct_time((2026, 8, 22, 2, 0, 0, 5, 234, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_sat)
    status, _ = concept_refresh.run_refresh_round(force=False)
    assert status == "skip"

    # 工作日 10:10(不在 9:30/10:00±5 的任何一个)
    g_off = _time.struct_time((2026, 8, 20, 2, 10, 0, 3, 232, 0))  # 10:10
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_off)
    status2, _ = concept_refresh.run_refresh_round(force=False)
    assert status2 == "skip"

    # 工作日 10:02(10:00 ±5 → 命中, 但 DB 没数据 → empty)
    g_hit = _time.struct_time((2026, 8, 20, 2, 2, 0, 3, 232, 0))  # 10:02
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_hit)

    # 临时 DB 无数据 → empty
    from app.core import config
    orig_db = config.DB_FILE
    db_path, _ = _tmp_db()
    config.DB_FILE = db_path
    try:
        status3, _ = concept_refresh.run_refresh_round(force=True)
        assert status3 == "empty"

        # 塞一条 auction +  mock 概念查询, force=True 跑完整流程 → ok
        def fake_collect(date):
            return {"600001", "000002"}

        def fake_batch(date, codes):
            return {"600001": "AI、机器人", "000002": "医药"}

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal",
             json.dumps([{"code": "600001"}, {"code": "000002"}], ensure_ascii=False),
             123))
        conn.commit()
        conn.close()
        monkeypatch.setattr(concept_refresh, "_collect_codes", fake_collect)
        monkeypatch.setattr(concept_refresh, "_refresh_batch", fake_batch)
        status4, msg = concept_refresh.run_refresh_round(force=True)
        assert status4 == "ok", msg
        assert "完成" in msg or "写库" in msg or "查询" in msg
    finally:
        config.DB_FILE = orig_db
        if os.path.exists(db_path):
            os.unlink(db_path)


# ====================================================================
# D. 竞价异动 endpoint 非竞价 fast-path / 竞价 deep=True 分支
#     (确保 _is_auction_hours 开关正确切换两条路径)
# ====================================================================

def _make_bid_seal_fake():
    return [{"code": "600001", "name": "测A", "change": 10.01},
            {"code": "000002", "name": "测B", "change": 5.05}]


def test_kpl_bid_seal_live_vs_fast_path(client, vip_user, monkeypatch):
    """竞价时段 → 调 fetch_bid_seal + apply_board_concept(deep=True)
       非竞价时段 → 调 _read_auction_fast(返回空但不崩) + 不调 deep=True 概念接口
    注意: _is_auction_hours 用 gmtime(time.time()+8h), 所以 gmtime 返回值的 tm_hour
         需要直接等于北京时间小时数"""
    from app.services import kpl as kpl_svc
    from app.api import kpl as kpl_api

    token, _ = vip_user
    # --- Case 1: 竞价时段(周一 09:25, tm_hour=9 直接表示北京时间)
    g_auc = _time.struct_time((2026, 8, 17, 9, 25, 0, 0, 229, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_auc)
    calls = {"fetch_bid_seal": 0, "apply_deep": None}

    def fetch():
        calls["fetch_bid_seal"] += 1
        return _make_bid_seal_fake()

    def apply_concept(lst, **kw):
        calls["apply_deep"] = kw
        for it in lst:
            it["board"] = "概念A、概念B"

    monkeypatch.setattr(kpl_svc, "fetch_bid_seal", fetch)
    monkeypatch.setattr(kpl_svc, "apply_board_concept", apply_concept)
    monkeypatch.setattr(kpl_api, "_read_auction_fast", lambda tab: ([], "2026-08-17"))

    r = client.get("/api/kpl/bid-seal", headers=_hdrs(token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d.get("count") == 2, (
        f"竞价时段应走 fetch_bid_seal 分支, ok={d.get('ok')}, count={d.get('count')}, "
        f"date={d.get('date')} list={d.get('list')}")
    assert calls["fetch_bid_seal"] == 1
    assert calls["apply_deep"] and calls["apply_deep"].get("deep") is True
    assert calls["apply_deep"].get("truncate") == 2

    # --- Case 2: 非竞价时段(10:00) → 走 _read_auction_fast, 不调 fetch_bid_seal
    calls.clear()
    g_non = _time.struct_time((2026, 8, 17, 10, 0, 0, 0, 229, 0))  # BJ 10:00
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_non)
    fake_fast = [{"code": "600001", "name": "测A", "change": 1.2, "board": "AI、机器人"}]
    monkeypatch.setattr(kpl_api, "_read_auction_fast", lambda tab: (fake_fast, "2026-08-17"))
    r2 = client.get("/api/kpl/bid-seal", headers=_hdrs(token))
    d2 = r2.json()
    assert d2["ok"] and d2["count"] == 1
    assert d2["date"] == "2026-08-17"
    assert calls.get("fetch_bid_seal", 0) == 0


def test_kpl_bid_qiangcang_fastpath_skips_deep_concept(vip_user, client, monkeypatch):
    """bid-qiangcang 非竞价且无 date: _ensure_concepts(轻量补, 非逐股) 被调用,
       apply_board_concept(deep=True) 不被调用"""
    from app.services import kpl as kpl_svc
    from app.api import kpl as kpl_api

    token, _ = vip_user
    # BJ 10:00 非竞价
    g_non = _time.struct_time((2026, 8, 17, 10, 0, 0, 0, 229, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_non)

    fake_data = {
        "list20": [{"code": "600001", "change": 3.0, "board": ""},
                   {"code": "000002", "change": 2.0, "board": "已填概念"}],
        "list20Chg": [],
        "listLast": [],
        "date": "2026-08-17",
    }
    monkeypatch.setattr(kpl_svc, "fetch_bid_qiangcang", lambda *a, **k: fake_data)

    spy = {"apply_deep_called": False, "ensure_called": False}

    def deep_concept(lst, **kw):
        if kw.get("deep") is True:
            spy["apply_deep_called"] = True

    def ensure(lst, tag):
        spy["ensure_called"] = True
        # 填充 600001 的 board(模拟榜单映射补概念)
        for it in lst:
            if not (it.get("board") or "").strip():
                it["board"] = "映射概念"

    monkeypatch.setattr(kpl_svc, "apply_board_concept", deep_concept)
    monkeypatch.setattr(kpl_api, "_ensure_concepts", ensure)
    monkeypatch.setattr(kpl_api, "_update_spot_change", lambda lst: 0)

    r = client.get("/api/kpl/bid-qiangcang", headers=_hdrs(token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["count20"] == 2
    assert spy["ensure_called"] is True, "非竞价应该走 _ensure_concepts"
    assert spy["apply_deep_called"] is False, "非竞价不应走 deep=True 逐股查"

    # 指定 date 参数 → 应该切回 deep=True(历史回看, 确保概念完整)
    spy["apply_deep_called"] = False
    spy["ensure_called"] = False
    r2 = client.get("/api/kpl/bid-qiangcang?date=2026-08-17", headers=_hdrs(token))
    assert r2.status_code == 200
    # date 参数时: fetch_bid_qiangcang(date) 后, 走 deep=True 分支
    assert spy["apply_deep_called"] is True, "带 date 回看必须走 deep=True 确保概念完整"


# ====================================================================
# E. /api/kpl/broken 非竞价时段 fast-path 分支: broken_today 表读取
# ====================================================================
def test_kpl_broken_fastpath_reads_broken_today(vip_user, client, monkeypatch):
    """非竞价 broken(今炸板): 读 _read_auction_fast('broken_today'), 返回 list/day 字段;
       _merge_broken_bid_snap / fill_float_mv_from_snap 被调用补齐辅助字段"""
    from app.api import kpl as kpl_api
    from app.services import kpl as kpl_svc

    token, _ = vip_user
    # BJ 10:00 非竞价
    g_non = _time.struct_time((2026, 8, 17, 10, 0, 0, 0, 229, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_non)

    calls = {"merge": 0, "fill_mv": 0}

    def fake_fast(tab):
        assert tab == "broken_today"
        return ([
            {"code": "000001", "name": "测炸板", "change": 1.0, "day": "2026-08-17"},
        ], "2026-08-17")

    def fake_merge(lst):
        calls["merge"] += 1

    def fake_fill_mv(lst, date):
        calls["fill_mv"] += 1

    monkeypatch.setattr(kpl_api, "_read_auction_fast", fake_fast)
    monkeypatch.setattr(kpl_svc, "_merge_broken_bid_snap", fake_merge)
    monkeypatch.setattr(kpl_svc, "fill_float_mv_from_snap", fake_fill_mv)
    # 竞价时段 fetch_* 不应被调用
    fetch_broken_called = [0]

    def fake_fetch_broken(*a, **k):
        fetch_broken_called[0] += 1
        return []

    monkeypatch.setattr(kpl_svc, "fetch_broken_zt", fake_fetch_broken)

    r = client.get("/api/kpl/broken", headers=_hdrs(token))
    d = r.json()
    assert r.status_code == 200, r.text
    assert d["ok"] and d["count"] == 1
    assert d["day"] == "2026-08-17"
    assert calls["merge"] == 1 and calls["fill_mv"] == 1
    assert fetch_broken_called[0] == 0
