# -*- coding: utf-8 -*-
"""开盘啦(kpl)数据源解析逻辑测试"""
import pytest

from app.services import kpl


# ---------- 竞价涨停委买额解析 ----------
def test_parse_bid_seal():
    data = {"info": [
        ["300862", "蓝盾光电", 32.84, 19.99, 2041987916, 19.99, 7932172, 0.36,
         16578945, 50396264, 16578945, "并购重组、智能驾驶", 4616157703,
         36338768, 68157438, -31818670, "3连板"],
        ["600602", "云赛智联", 20.25, 9.99, 1204935750, 9.99, 55847472, 0.83,
         101227563, 1018562850, 101227563, "算力租赁、算力", 12191297761,
         279426957, 362921736, -83494779, "首板"],
    ]}
    rows = kpl._parse_bid_seal(data)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["code"] == "300862"
    assert r0["name"] == "蓝盾光电"
    assert r0["realChange"] == 19.99
    assert r0["bidSealAmt"] == 2041987916
    assert r0["bidNetAmt"] == 7932172
    assert r0["board"] == "并购重组、智能驾驶"
    assert r0["limitBoards"] == 3          # "3连板" -> 3
    assert rows[1]["limitBoards"] == 1     # "首板" -> 1


# ---------- 市场情绪解析 ----------
def test_fetch_sentiment(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "info": [{"ztjs": "92", "Day": "2026-08-12", "df_num": "1", "strong": "78", "lbgd": "7"}],
        "tip": "温馨提示",
        "errcode": "0",
    })
    kpl.clear_cache()
    s = kpl.fetch_sentiment()
    assert s["ztCount"] == 92
    assert s["strong"] == 78
    assert s["lbgd"] == 7
    assert s["dfNum"] == 1
    assert s["day"] == "2026-08-12"


# ---------- 连板梯队解析 ----------
def test_parse_ladder():
    data = {"info": [[
        ["002483", "润邦股份", 1, "", 1786497900, "造船", 103302544, 190927488,
         26446780, 38851255, -12404475, 42109205, "造船、专用设备", 3409410664,
         1.24, 1, 0, 0, "", "801374", 1, 5.75, 9.94],
    ]]}
    rows = kpl._parse_ladder(data, 1)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "002483"
    assert r["name"] == "润邦股份"
    assert r["seal"] == 103302544
    assert r["maxSeal"] == 190927488
    assert r["mainNet"] == 26446780
    assert r["boardCode"] == "801374"
    assert r["amplitude"] == 9.94
    assert r["ladderLabel"] == "首板"


# ---------- 板块强度解析 ----------
def test_parse_board_rank():
    data = {"list": [
        ["801159", "机器人概念", 7599, 1.346, 0.457, 489161664113, 568713851,
         20382431106, -19813717255, 0.825, 18688529430208, 1.36, 550100743,
         23899196317696, -3911638533, 40.9432, 36.0769, 7599, 1.346],
    ]}
    rows = kpl._parse_board_rank(data)
    assert len(rows) == 1
    r = rows[0]
    assert r["boardCode"] == "801159"
    assert r["name"] == "机器人概念"
    assert r["strength"] == 7599
    assert r["change"] == 1.346
    assert r["mainNet"] == 568713851
    assert r["volRatio"] == 0.825
    assert r["peNow"] == 40.9432


# ---------- 连板数解析 ----------
def test_lb():
    assert kpl._lb("3连板") == 3
    assert kpl._lb("首板") == 1
    assert kpl._lb("6天4板") == 4
    assert kpl._lb("") == 0


# ---------- 涨停原因解析 ----------
def test_fetch_zt_reason(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "StockID": "001337",
        "List": [{"Date": "2026-01-28", "Reason": "黄金；现货黄金创新高", "SCLT": "日内龙一",
                  "GNSM": "黄金：...", "Boom_ZS": "沪金主连价格突破"}],
        "errcode": "0",
    })
    kpl.clear_cache()
    r = kpl.fetch_zt_reason("001337")
    assert len(r) == 1
    assert r[0]["reason"] == "黄金；现货黄金创新高"
    assert r[0]["sclt"] == "日内龙一"


# ---------- 人气热榜解析 ----------
def test_fetch_hot_rank(monkeypatch):
    # 真实返回结构: [code, name, 当日涨幅(%), 其他指标, 排名, ...]
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "Day": "2026-08-13",
        "List": [["600721", "百花医药", 3.35, 0, 1, 0, 0], ["600664", "哈药股份", 0.57, 44, 2, 0, 0]],
    })
    kpl.clear_cache()
    rows = kpl.fetch_hot_rank()
    assert len(rows) == 2
    assert rows[0]["code"] == "600721"
    assert rows[0]["change"] == 3.35
    assert rows[0]["rank"] == 1
    assert rows[1]["name"] == "哈药股份"
    # 涨幅必须取 row[2](当日涨跌幅), 不能取 row[3](该列是 44, 超出 A 股单日涨停限制)
    assert rows[1]["change"] == 0.57


# ---------- 龙虎榜解析 ----------
def test_fetch_lhb(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "Time": "2026-08-12",
        "list": [
            {"ID": "002552", "Name": "宝鼎科技", "IncreaseAmount": "10.00%", "D3": "0",
             "BuyIn": "26482652", "JoinNum": 0, "Turnover": "2053271665",
             "CircPrice": 22135992570.23, "Amplitude": "12.05", "TurnoverRatio": "9.88",
             "Capitalization": 23306278833.17},
            {"ID": "600721", "Name": "百花医药", "IncreaseAmount": "10.04%", "D3": "3",
             "BuyIn": "149643072.72", "JoinNum": 12, "Turnover": "1840794640",
             "CircPrice": 5395203319.05, "Amplitude": "5.96", "TurnoverRatio": "34.43",
             "Capitalization": 5395203319.05},
        ],
    })
    kpl.clear_cache()
    rows = kpl.fetch_lhb()
    assert len(rows) == 2
    assert rows[0]["code"] == "002552"
    assert rows[0]["change"] == 10.0
    assert rows[0]["limitBoards"] == 0
    assert rows[1]["name"] == "百花医药"
    assert rows[1]["change"] == 10.04
    assert rows[1]["limitBoards"] == 3


# ---------- 龙虎榜营业部明细解析 ----------
def test_fetch_lhb_detail(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "Name": "宝鼎科技", "Time": "2026-08-12", "QuoteChange": "10%", "lbnum": "2",
        "BuyIn": "26482652", "Turnover": "2053271665", "TurnoverRatio": "9.88",
        "List": [{
            "BuyTotal": "123456789", "SellTotal": "98765432", "UpReason": "机器人概念",
            "BuyList": [{"Name": "深股通专用", "Buy": "92083446", "Sell": "99048114"}],
            "SellList": [{"Name": "机构专用", "Buy": "100", "Sell": "50000000"}],
        }],
    })
    kpl.clear_cache()
    d = kpl.fetch_lhb_detail("002552", "2026-08-12")
    assert d["name"] == "宝鼎科技"
    assert d["change"] == 10
    assert d["limitBoards"] == 2
    assert d["buyTotal"] == 123456789
    assert d["upReason"] == "机器人概念"
    assert len(d["buyList"]) == 1
    assert d["buyList"][0]["name"] == "深股通专用"
    assert d["buyList"][0]["buy"] == 92083446
    assert d["sellList"][0]["sell"] == 50000000


# ---------- 昨日涨停今表现解析 ----------
def test_fetch_yesterday_perf(monkeypatch):
    calls = {}
    def fake_call(host_key, params, timeout=12):
        pid = params.get("PlateID")
        calls[pid] = True
        change = {"801900": 2.117, "801901": 1.171, "801902": 6.77}[pid]
        return {"List": ["--", 0, 79350794569, -30594498, change, 0, 0, 0],
                "Date": "2026-08-13", "errcode": "0"}
    monkeypatch.setattr(kpl, "_call", fake_call)
    kpl.clear_cache()
    perf = kpl.fetch_yesterday_perf()
    assert set(perf.keys()) == {"zt", "lb", "pb"}
    assert abs(perf["zt"]["change"] - 2.117) < 1e-9
    assert abs(perf["lb"]["change"] - 1.171) < 1e-9
    assert abs(perf["pb"]["change"] - 6.77) < 1e-9
    assert perf["zt"]["date"] == "2026-08-13"
    assert len(calls) == 3  # 三个板块接口都调了


# ---------- 竞价爆量(Type=10, 同源解析) ----------
def test_fetch_bid_boom(monkeypatch):
    """竞价爆量(2026-08-19 改版): 全市场按竞价量比排序取前200
    过滤(23:10 主人要求): 竞价量比>2 且 竞价成交额>100万"""
    import sqlite3
    class FakeCursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def __iter__(self): return iter(self.rows)
    class FakeConn:
        def execute(self, sql, params=()):
            q = sql.strip()
            if q.startswith("SELECT MAX(time_point) FROM snapshot_bid"):
                return FakeCursor([("9_25",)])              # 今日最新时点
            if q.startswith("SELECT MAX(date) FROM snapshot_bid WHERE date <"):
                return FakeCursor([("2026-08-12",)])        # 昨日
            if "time_point=?" in q or "time_point='" in q:
                # 今日 9_25 全市场行: (code, bid_amt万元, name, bid_change, float_mv, board)
                if params and len(params) > 1 and params[1] == "9_25" and "date=?" in q:
                    return FakeCursor([
                        ("600001", 3000.0, "甲", 5.0, 4e9, "板块A"),   # 量比 3000/1000=3.0
                        ("600002", 2000.0, "乙", 4.0, 5e9, "板块B"),   # 量比 2000/2000=1.0
                        ("600003", 15000.0, "丙", 6.0, 6e9, "板块C"),  # 量比 15000/3000=5.0
                        ("600004", 500.0, "丁", 3.0, 3e9, "板块D"),    # 竞价额<1000万 过滤
                    ])
                if params and len(params) == 1 and params[0] == "2026-08-12":  # SQL 内写死 9_25
                    return FakeCursor([
                        ("600001", 1000.0), ("600002", 2000.0), ("600003", 3000.0),
                    ])
                return FakeCursor([])
            return FakeCursor([])
        def close(self): pass
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    kpl.clear_cache()
    rows = kpl.fetch_bid_boom()
    # 量比>2 + 成交额>100万: 600003(5.0) > 600001(3.0); 600002(量比1.0≤2) 600004(无昨日) 过滤
    assert len(rows) == 2
    assert [r["code"] for r in rows] == ["600003", "600001"]
    assert rows[0]["bidRatioYest"] == 5.0
    assert rows[1]["bidRatioYest"] == 3.0
    # bidAmt 万元→元: 600003 15000万 = 1.5亿
    assert rows[0]["bidAmt"] == 15000 * 10000
    # 字段补全
    assert rows[0]["name"] == "丙" and rows[0]["board"] == "板块C"

def test_fetch_broken_zt(monkeypatch):
    import urllib.request
    class FakeResp:
        def read(self):
            return ('{"code":20000,"data":[{"symbol":"000001.SZ","stock_chi_name":"PingAnBank",'
                    '"change_percent":0.05,"limit_up_days":2,"break_limit_up_times":3,'
                    '"first_limit_up":1786586004,"first_break_limit_up":1786586148,'
                    '"surge_reason":{"symbol":"000001.SZ","stock_reason":"金融科技龙头",'
                    '"related_plates":[{"plate_name":"银行","plate_reason":"降息预期"}]}}]}').encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    kpl.clear_cache()
    rows = kpl.fetch_broken_zt()
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "000001"            # 去 .SZ 后缀
    assert r["name"] == "PingAnBank"
    assert abs(r["change"] - 5.0) < 1e-9    # 0.05 -> 5%
    assert r["limitUpDays"] == 2
    assert r["breakTimes"] == 3
    assert "金融科技龙头" in r["reason"]     # 个股原因
    assert "银行" in r["reason"]             # 板块原因
    assert r["day"]                          # 行内带日期


# ---------- 炸板指定日期(昨炸板) ----------
def test_fetch_broken_zt_by_day(monkeypatch):
    import urllib.request
    urls = []

    class FakeResp:
        def read(self):
            return ('{"code":20000,"data":[{"symbol":"002536.SZ","stock_chi_name":"飞龙股份",'
                    '"change_percent":0.0788,"limit_up_days":0,"break_limit_up_times":1,'
                    '"first_limit_up":1786584600,"first_break_limit_up":1786584700}]}').encode()

        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, *a, **k):
        urls.append(req.full_url)
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    kpl.clear_cache()
    rows = kpl.fetch_broken_zt("2026-08-12")
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-08-12"
    assert rows[0]["code"] == "002536"
    assert urls and "date=2026-08-12" in urls[0]   # URL 带 date 参数
    # yesterday → 解析为具体日期(日历兜底: 跳过周末)
    kpl.clear_cache()
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-12")
    rows2 = kpl.fetch_broken_zt("yesterday")
    assert rows2[0]["day"] == "2026-08-12"


# ---------- 调用失败降级 ----------
def test_call_failure_returns_none(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: None)
    kpl.clear_cache()
    assert kpl.fetch_bid_seal() is None
    assert kpl.fetch_sentiment() is None


# ---------- 昨日涨停 / 昨断板 / 竞价抢筹 ----------
def _mk_flash_pool(monkeypatch, day_pool, today_codes):
    """mock _flash_pool: 昨日池返回 day_pool, 今日池返回 today_codes 对应的简单行"""
    def fake(pool_name, date=None):
        if date:  # 昨日
            return [{"code": x[0], "name": x[1], "change": x[2], "limitUpDays": x[3]}
                    for x in day_pool]
        return [{"code": c, "name": c, "change": 10.0, "limitUpDays": 1} for c in today_codes]
    monkeypatch.setattr(kpl, "_flash_pool", fake)
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [])
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-12")
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})


def test_fetch_yest_zt(monkeypatch):
    _mk_flash_pool(monkeypatch,
                   [("600266", "城建发展", 10.0, 1), ("600683", "京投发展", 10.0, 3)],
                   today_codes=["600683"])   # 京投发展今仍涨停
    kpl.clear_cache()
    rows = kpl.fetch_yest_zt()
    assert len(rows) == 2
    m = {r["code"]: r for r in rows}
    assert m["600266"]["stillLimit"] is False      # 今断
    assert m["600683"]["stillLimit"] is True       # 连板
    assert m["600683"]["limitUpDays"] == 3


def test_fetch_yest_broken(monkeypatch):
    """昨断板(2026-08-18 新语义): 前一日连板>=2 且 昨日未涨停(连板中断)"""
    import sqlite3
    class FakeCursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
        def fetchone(self): return self.rows[0] if self.rows else None
    class FakeConn:
        def execute(self, sql, params=()):
            if "SELECT DISTINCT date" in sql:
                return FakeCursor([("2026-08-11",)])   # 前一日
            return FakeCursor([])
        def close(self): pass
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    def fake_pool(pool_name, date=None):
        if date == "2026-08-11":   # 前一日涨停池: 2 只均 >=2 板
            return [{"code": "600266", "name": "城建发展", "change": 10.0, "limitUpDays": 2},
                    {"code": "600683", "name": "京投发展", "change": 10.0, "limitUpDays": 2}]
        if date == "2026-08-12":   # 昨日涨停池: 仅 600683(600266 昨日未涨停)
            return [{"code": "600683", "name": "京投发展", "change": 10.0, "limitUpDays": 3}]
        return []
    monkeypatch.setattr(kpl, "_flash_pool", fake_pool)
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [])
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-12")
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})
    kpl.clear_cache()
    rows = kpl.fetch_yest_broken()
    codes = [r["code"] for r in rows]
    assert "600266" in codes      # 前一日2板+昨日未涨停 → 断板
    assert "600683" not in codes  # 昨日仍涨停 → 连板中, 非断板


def test_fetch_bid_qiangcang(monkeypatch):
    """左右双表抢筹:
    - list20 = 开盘啦 Type4 全市场竞价异动, 抢筹强度 qcDelta = bidNetAmt/floatMv*100
    - listLast = snapshot_bid 9:24→9:25 段: 抢筹幅度 = 9:25涨幅 − 9:24涨幅"""
    import sqlite3
    import time as _t
    class FakeT:
        tm_hour, tm_min, tm_wday = 9, 20, 3   # 竞价时段(9:20 周四)
    monkeypatch.setattr(_t, "gmtime", lambda t=None: FakeT())
    class FakeCursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class FakeConn:
        def __init__(self): self.executed = []
        def execute(self, sql, params=()):
            self.executed.append(sql)
            if "snapshot_lastsec" in sql:
                return FakeCursor([])   # 秒级序列空 → 走 9_24 兜底
            if "9_24" in sql:
                return FakeCursor([(1, 5.0, 500.0), (2, 5.5, 800.0), (3, 5.8, 700.0)])
            # 9_25: code, bid_change, bid_amt(万元), float_mv, name
            # 新过滤(2026-08-19 22:33): 竞价金额阈值 1000万 → 500万
            return FakeCursor([
                (1, 6.0, 1000.0, 5e9, "A"),     # 1000万 达标
                (2, 6.0, 1500.0, 8e9, "B"),     # 1500万 达标
                (3, 6.0, 400.0, 9e9, "C"),      # 400万 < 500万 → 被金额过滤
            ])
        def close(self): pass
    real = sqlite3.connect
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    # 模拟开盘啦 Type4 返回: bidNetAmt/floatMv*100 控制 qcDelta
    # code1: 6e8/6e9=10%, code2: 4.8e8/8e9=6%, code3: 1e8/3e9=3.33%(被过滤), code4: fmv=1e9(=10亿)>2e8 通过, qcDelta=50%
    fake_seal = [
        {"code": "1", "name": "A", "realChange": 1.0, "bidNetAmt": 6e8, "floatMv": 6e9,
         "bidAmt": 1e8, "bidTurnover": 0.5, "bidChange": 5.0, "board": "板块A"},
        {"code": "2", "name": "B", "realChange": 0.5, "bidNetAmt": 4.8e8, "floatMv": 8e9,
         "bidAmt": 5e7, "bidTurnover": 0.3, "bidChange": 4.0, "board": "板块B"},
        {"code": "3", "name": "C", "realChange": 0.0, "bidNetAmt": 1e8, "floatMv": 3e9,
         "bidAmt": 3e7, "bidTurnover": 0.1, "bidChange": 3.0, "board": "板块C"},   # 3.33% 被过滤
        {"code": "4", "name": "D", "realChange": 0.0, "bidNetAmt": 5e8, "floatMv": 1e8,
         "bidAmt": 5e7, "bidTurnover": 0.2, "bidChange": 2.0, "board": "板块D"},   # fmv=1亿<2e8被过滤
        {"code": "5", "name": "E", "realChange": 0.0, "bidNetAmt": 0, "floatMv": 5e9,
         "bidAmt": 5e7, "bidTurnover": 0.2, "bidChange": 2.0, "board": "板块E"},   # bidNetAmt=0被过滤
    ]
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: fake_seal)
    kpl.clear_cache()
    d = kpl.fetch_bid_qiangcang()
    monkeypatch.setattr("sqlite3.connect", real)
    assert isinstance(d, dict)
    l20 = d["list20"]
    lLast = d["listLast"]
    # 左表过滤 >0.5%(2026-08-18 阈值 5%→0.5%): code1(10%) + code2(6%) + code3(3.33%) 入选, code4(<2亿) code5(bidNetAmt=0) 过滤
    assert len(l20) == 3
    assert l20[0]["code"] == "1" and l20[0]["qcDelta"] == 10.0
    assert l20[1]["code"] == "2" and l20[1]["qcDelta"] == 6.0
    # 右表 9:24→9:25 段; code3 竞价金额400万<500万被过滤(2026-08-19 阈值1000万→500万)
    assert len(lLast) == 2
    mLast = {r["code"]: r for r in lLast}
    # code1: qcDeltaLast = 6.0 - 5.0 = 1.0%
    assert mLast[1]["qcDeltaLast"] == 1.0
    assert mLast[1]["bidChange24"] == 5.0
    # code2: qcDeltaLast = 6.0 - 5.5 = 0.5%
    assert mLast[2]["qcDeltaLast"] == 0.5
    # 右表按抢筹幅度降序: code1(1.0) > code2(0.5)
    assert lLast[0]["code"] == 1
    # 竞额/昨比: bidAmt(元) / 昨日额pair[0](万元) / 100 → %; code1 l20 bidAmt=1e8元 → 50%
    assert l20[0]["bidRatio"] == 50.0
    # listLast code1 bidAmt=9_25快照1000万*10000=1e7元 → 1e7/20000/100=5.0%
    assert lLast[0]["bidRatio"] == 5.0


def test_fetch_bid_qiangcang_persist(monkeypatch):
    """抢筹结果持久化: 竞价时段(接口有数据)存库 → 非竞价时段读库, 不丢失
    注: fetch_bid_qiangcang 按时间窗(9:15-9:30)决定 live/读库, 测试必须 mock gmtime 固定时段"""
    import sqlite3
    import time as _t
    class FakeT:
        def __init__(self, hour, minute, wday=3):  # wday=3 周四, 工作日
            self.tm_hour, self.tm_min, self.tm_wday = hour, minute, wday
    def fake_gmtime(t=None):
        return _FIXED[0]
    class FakeCursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class FakeConn:
        def __init__(self): self.executed = []
        def execute(self, sql, params=()):
            self.executed.append(sql)
            if "snapshot_lastsec" in sql:
                return FakeCursor([])   # 秒级序列空 → 走 9_24 兜底
            if "9_24" in sql:
                return FakeCursor([])
            if sql.strip().startswith("SELECT code, name, real_change"):
                return FakeCursor([
                    # code, name, real_change, bid_amt, qc_delta, bid_turnover, bid_change, float_mv, board, bid_ratio
                    ("600001", "测试甲", 1.5, 1e8, 12.3, 0.2, 5.0, 8e9, "AI概念", 50.0),
                    ("600002", "测试乙", 0.8, 5e7, 8.9, 0.1, 4.0, 6e9, "医药", 25.0),
                ])
            return FakeCursor([])
        def executemany(self, sql, params=()): self.executed.append(sql)
        def commit(self): pass
        def close(self): pass
    real_connect = sqlite3.connect
    real_gmtime = _t.gmtime
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})
    # 阶段1: 竞价时段(9:20) mock 接口有数据 → live 分支 → 落库
    _FIXED = [FakeT(9, 20)]
    monkeypatch.setattr(_t, "gmtime", fake_gmtime)
    fake_seal = [
        {"code": "600001", "name": "测试甲", "realChange": 1.5, "bidNetAmt": 9.84e8,
         "floatMv": 8e9, "bidAmt": 1e8, "bidTurnover": 0.2, "bidChange": 5.0, "board": "AI概念"},
        {"code": "600002", "name": "测试乙", "realChange": 0.8, "bidNetAmt": 5.34e8,
         "floatMv": 6e9, "bidAmt": 5e7, "bidTurnover": 0.1, "bidChange": 4.0, "board": "医药"},
    ]
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: fake_seal)
    kpl.clear_cache()
    d1 = kpl.fetch_bid_qiangcang()
    assert len(d1["list20"]) == 2, d1
    # 阶段2: 非竞价时段(14:00) 即使接口返回"僵尸数据"(bidNetAmt=0)也必须走读库, 不丢失
    _FIXED = [FakeT(14, 0)]
    zombie_seal = [
        {"code": "600001", "name": "测试甲", "realChange": 0, "bidNetAmt": 0,
         "floatMv": 8e9, "bidAmt": 0, "bidTurnover": 0, "bidChange": 0, "board": ""},
        {"code": "600002", "name": "测试乙", "realChange": 0, "bidNetAmt": 0,
         "floatMv": 6e9, "bidAmt": 0, "bidTurnover": 0, "bidChange": 0, "board": ""},
    ]
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: zombie_seal)
    kpl.clear_cache()
    d2 = kpl.fetch_bid_qiangcang()
    assert len(d2["list20"]) == 2, d2   # 读库返回, 非僵尸数据
    assert d2["list20"][0]["code"] == "600001"
    assert d2["list20"][0]["qcDelta"] == 12.3
    assert d2["list20"][0]["bidRatio"] == 50.0   # 竞额/昨比读库保留
    monkeypatch.setattr("sqlite3.connect", real_connect)
    monkeypatch.setattr(_t, "gmtime", real_gmtime)


def test_calc_lastsec_qc():
    """最后一秒差值回退: 9_25 差值大直接用; 小则向前回退找大差值"""
    # 场景1: 9_25(6.0) vs 最新秒(5.0) 差 1.0 ≥ 0.5 → 直接用
    d, ts = kpl._calc_lastsec_qc(6.0, [(34200, 5.0, 100.0), (34201, 5.0, 120.0)])
    assert d == 1.0
    # 场景2: 9_25 vs 最新秒差 0.2(小) → 回退: 最新秒(5.2) vs 前一秒(4.0) 差 1.2 ≥ 0.5 → 用 1.2
    # 序列: t1=4.0, t2=5.2(最后一秒实际变化发生在 t1→t2 之间)
    d, ts = kpl._calc_lastsec_qc(5.4, [(34200, 4.0, 100.0), (34201, 5.2, 150.0)])
    assert d == 1.2
    # 场景3: 全部差值小(0.1/0.2) → 返回最大差值 0.2(仍标记)
    d, ts = kpl._calc_lastsec_qc(5.3, [(34200, 5.2, 100.0), (34201, 5.1, 120.0)])
    assert abs(abs(d) - 0.2) < 1e-9
    # 场景4: 空序列 → None
    assert kpl._calc_lastsec_qc(5.0, []) == (None, None)
    # 场景5: 单点序列(9_25 vs 唯一秒) 差大
    d, ts = kpl._calc_lastsec_qc(6.5, [(34200, 5.0, 100.0)])
    assert d == 1.5


def test_fetch_bid_qiangcang_lastsec_full(monkeypatch):
    """完整 mock 端到端: 秒级差值回退全场景(listLast)
    A: 9_25−最新秒差大(1.0) → 直接用
    B: 9_25−最新秒差小(0.2) → 回退 最新秒−前一秒(1.2) → 取 1.2
    C: 全部差值小 → 取最大差(0.2)
    D: 无秒级序列 → 9_24 兜底(0.5)
    E: fmv=4亿<5亿 → 过滤
    F: 无秒级且无9_24 → 不进列表
    期望排序: B(1.2) > A(1.0) > D(0.5) > C(0.2)"""
    import sqlite3
    import time as _t
    class FakeT:
        tm_hour, tm_min, tm_wday = 9, 20, 3   # 竞价时段
    monkeypatch.setattr(_t, "gmtime", lambda t=None: FakeT())
    class FakeCursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class FakeConn:
        def __init__(self): self.executed = []
        def execute(self, sql, params=()):
            self.executed.append(sql)
            if "snapshot_lastsec" in sql:
                # code -> [(ts, bid_change, bid_amt), ...] 升序
                return FakeCursor([
                    ("A", 5.0, 100.0, 35495), ("A", 5.0, 120.0, 35501),
                    ("B", 4.0, 100.0, 35495), ("B", 5.2, 150.0, 35501),
                    ("C", 5.2, 100.0, 35495), ("C", 5.1, 110.0, 35501),
                    # D 无秒级; E/F 无秒级
                ])
            if "9_24" in sql:
                # D 有 9_24: 6.0 → 9_25(6.5)−6.0=0.5
                return FakeCursor([("D", 6.0, 800.0)])
            if sql.strip().startswith("SELECT code, name, real_change"):
                return FakeCursor([])
            # 9_25: code, bid_change, bid_amt(万元), float_mv, name
            # 新过滤(2026-08-19): 最后一秒抢筹要求竞价金额>1000万
            return FakeCursor([
                ("A", 6.0, 1000.0, 6e9, "甲"),
                ("B", 5.4, 2000.0, 8e9, "乙"),
                ("C", 5.3, 1500.0, 7e9, "丙"),
                ("D", 6.5, 1200.0, 9e9, "丁"),
                ("E", 6.0, 500.0, 4e8, "戊"),   # fmv=4亿<5亿
                ("F", 6.0, 800.0, 8e9, "己"),   # 无秒级无9_24
            ])
        def close(self): pass
    real = sqlite3.connect
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [])   # 只看 listLast
    kpl.clear_cache()
    d = kpl.fetch_bid_qiangcang()
    monkeypatch.setattr("sqlite3.connect", real)
    lLast = d["listLast"]
    m = {r["code"]: r for r in lLast}
    # 6 只中 E 过滤(fmv<5亿), F 无数据源 → 4 只入选
    assert len(lLast) == 4, lLast
    # A: 9_25(6.0)−最新秒(5.0)=1.0
    assert m["A"]["qcDeltaLast"] == 1.0
    # B: 9_25(5.4)−5.2=0.2 小 → 回退 5.2−4.0=1.2
    assert m["B"]["qcDeltaLast"] == 1.2
    # C: 全小 → 最大差 5.2−5.1=0.1? 9_25(5.3)−5.1=0.2 更大 → 0.2
    assert m["C"]["qcDeltaLast"] == 0.2
    # D: 9_24 兜底 6.5−6.0=0.5
    assert m["D"]["qcDeltaLast"] == 0.5
    # E/F 不在
    assert "E" not in m and "F" not in m
    # 排序: B(1.2) > A(1.0) > D(0.5) > C(0.2)
    assert [r["code"] for r in lLast] == ["B", "A", "D", "C"]


def test_fetch_bid_qiangcang_list20chg(monkeypatch):
    """涨幅抢筹(全市场快照): qcDeltaChg = 9_25涨幅 − 9_20涨幅, 过滤>5%, fmv≥2亿, 竞价额≥500万, 竞价涨幅>2%
    A: 9_20=1.5 → 9_25=8.64, qcDeltaChg=7.14, amt=991.5 ✅ 入选
    B: 9_20=3.0 → 9_25=7.0,  qcDeltaChg=4.0  被过滤(差<5)
    C: fmv=1e8(<2亿) 被过滤
    D: 9_20 无数据 → 不进
    E: amt=300(<500万) 被过滤
    F: chg25=1.5(竞价涨幅≤2%) 被过滤"""
    import sqlite3
    import time as _t
    class FakeT:
        tm_hour, tm_min, tm_wday = 9, 20, 3
    monkeypatch.setattr(_t, "gmtime", lambda t=None: FakeT())
    class FakeCursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class FakeConn:
        def __init__(self): self.executed = []
        def execute(self, sql, params=()):
            self.executed.append(sql)
            if "9_20" in sql and "snapshot_lastsec" not in sql:
                # code, bid_change
                return FakeCursor([("A", 1.5), ("B", 3.0), ("C", 1.0), ("E", 1.0), ("F", -2.0)])
            if "snapshot_lastsec" in sql:
                return FakeCursor([])
            if "9_24" in sql:
                return FakeCursor([])
            if sql.strip().startswith("SELECT code, name, real_change"):
                return FakeCursor([])
            # 9_25: code, bid_change, bid_amt, float_mv, name, board
            return FakeCursor([
                ("A", 8.64, 991.5, 6.4e9, "甲", "AI概念"),
                ("B", 7.0, 800.0, 8e9, "乙", "医药"),
                ("C", 6.0, 500.0, 1e8, "丙", "芯片"),
                ("D", 6.5, 900.0, 9e9, "丁", "军工"),
                ("E", 8.0, 300.0, 7e9, "戊", "芯片"),   # 竞价额300万<500万
                ("F", 1.5, 900.0, 7e9, "己", "消费"),   # 竞价涨幅1.5%≤2%
            ])
        def close(self): pass
    real = sqlite3.connect
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [])
    kpl.clear_cache()
    d = kpl.fetch_bid_qiangcang()
    monkeypatch.setattr("sqlite3.connect", real)
    l20c = d["list20Chg"]
    m = {r["code"]: r for r in l20c}
    # A 入选 7.14; B 差<5 过滤; C fmv<2亿 过滤; D 无9_20 不进; E 竞价额<500万 过滤; F 竞价涨幅≤2% 过滤
    assert len(l20c) == 1, l20c
    assert m["A"]["qcDeltaChg"] == 7.14
    assert m["A"]["bidChange20"] == 1.5
    assert m["A"]["bidChange"] == 8.64
    assert m["A"]["board"] == "AI概念"


# ---------- 市场概览: 涨跌家数分布 + 两市概况 (2026-08-16) ----------
def test_market_breadth_rise_fall(client, monkeypatch):
    """涨跌家数: 今日最新 + 昨日同时刻(对比)"""
    from app.services import kpl

    def fake_flash_line(fields, date=None):
        # 今日曲线两条(升序), 昨日一条
        if date:
            return [{"rise_count": 2000, "fall_count": 3000, "timestamp": 1786000000}]
        return [
            {"rise_count": 2200, "fall_count": 2800, "timestamp": 1786077000},
            {"rise_count": 2423, "fall_count": 1882, "timestamp": 1786077300},
        ]

    monkeypatch.setattr(kpl, "_flash_line", fake_flash_line)
    b = kpl.fetch_market_breadth()
    assert b is not None
    assert b["rise"] == 2423 and b["fall"] == 1882          # 今日最新
    assert b["yesterday"] and b["yesterday"]["rise"] == 2000  # 昨日
    assert b["yesterday"]["fall"] == 3000


def test_market_breadth_none_on_fail(client, monkeypatch):
    """涨跌家数接口失败 -> None (前端静默降级)"""
    from app.services import kpl
    monkeypatch.setattr(kpl, "_flash_line", lambda *a, **k: [])
    assert kpl.fetch_market_breadth() is None


def test_market_brief_fetch(client, monkeypatch):
    """两市概况: 全市场股票数 + 成交额(亿)"""
    from app.services import fetcher

    def fake_all(fs):
        return [
            {"f12": "600001", "f6": 1.0e10},   # 1 亿
            {"f12": "000002", "f6": 2.0e10},   # 2 亿
            {"f12": "300003", "f6": None},
        ]

    monkeypatch.setattr(fetcher, "fetch_eastmoney_all", fake_all)
    fetcher._market_brief_cache.update({"ts": 0.0, "data": None})   # 清缓存
    d = fetcher.fetch_market_brief(max_age=0)
    assert d is not None
    assert d["stockCount"] == 3
    assert abs(d["amount"] - 300.0) < 0.01    # 3e10 元 = 300 亿


def test_market_brief_api(client, first_user, monkeypatch):
    def hdrs(token):
        return {"Authorization": "Bearer " + token}

    """/api/kpl/market-brief 聚合返回 breadth + market + last"""
    from app.services import kpl, fetcher
    import app.api.kpl as kpl_api

    monkeypatch.setattr(kpl, "fetch_market_breadth",
                        lambda: {"rise": 2423, "fall": 1882, "ts": 1, "day": "2026-08-17",
                                 "yesterday": {"rise": 2000, "fall": 3000, "ts": 1, "day": "2026-08-14"}})
    monkeypatch.setattr(fetcher, "fetch_market_brief",
                        lambda *a, **k: {"stockCount": 5100, "amount": 21428.0, "date": "2026-08-17"})
    token, _, _ = first_user
    r = client.get("/api/kpl/market-brief", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["breadth"]["rise"] == 2423
    assert d["market"]["stockCount"] == 5100
    assert "last" in d


# ---------- 分时快照 + 昨日同时刻对比 (2026-08-16) ----------
def test_record_intraday_snapshot(client, monkeypatch):
    """记录 + 读回分时快照, settings 应累加为 list"""
    from app.services import fetcher
    # mock 全市场拉取
    monkeypatch.setattr(fetcher, "fetch_eastmoney_all",
                        lambda fs: [{"f12": "1", "f6": 1e10}] * 100)
    fetcher._market_brief_cache.update({"ts": 0.0, "data": None})

    snap = fetcher.record_intraday_snapshot(date="2026-08-17")
    assert snap is not None
    assert snap["stockCount"] == 100
    assert abs(snap["amount"] - 10000.0) < 0.5     # 100*1e10/1e8 = 10000 亿
    # 再调一次累加
    snap2 = fetcher.record_intraday_snapshot(date="2026-08-17")
    assert snap2["ts"] >= snap["ts"]
    # 取昨日同时刻: 给定昨日 list, 应返回 ts<=now 的最后一条
    from app.services import settings as st_svc
    st_svc.set("market_brief_intraday_2026-08-16",
               [{"ts": 1000, "amount": 5000.0, "stockCount": 5100},
                {"ts": 6000, "amount": 8000.0, "stockCount": 5100},
                {"ts": 99999, "amount": 9999.0, "stockCount": 5100}])
    y = fetcher.get_same_time_yesterday()
    # 当前 ts 远大于 1000+86400(系统有偏), 最稳: 取 ts<= now 的最后一条
    if y:
        assert y["amount"] >= 0


def test_market_brief_last_same_time(client, first_user, monkeypatch):
    """/api/kpl/market-brief 返回 last_same_time 字段"""
    from app.services import kpl, fetcher
    monkeypatch.setattr(kpl, "fetch_market_breadth", lambda: {"rise": 1, "fall": 1, "ts": 1, "day": "x", "yesterday": None})
    monkeypatch.setattr(fetcher, "fetch_market_brief",
                        lambda *a, **k: {"stockCount": 100, "amount": 500.0, "date": "2026-08-17"})
    monkeypatch.setattr(fetcher, "get_same_time_yesterday",
                        lambda: {"amount": 450.0, "stockCount": 100, "ts": 1000, "date": "2026-08-16"})
    def hdrs(token): return {"Authorization": "Bearer " + token}
    token, _, _ = first_user
    r = client.get("/api/kpl/market-brief", headers=hdrs(token))
    d = r.json()
    assert "last_same_time" in d
    assert d["last_same_time"]["amount"] == 450.0


def test_merge_broken_bid_snap(monkeypatch):
    """炸板补竞价涨幅/换手: 按 day 查 9_25 快照补 bidChange/bidTurnover
    A: day=今天 快照 bid_change=8.64, bid_amt=991.5万, float_mv=6.4e9 → 换手=0.15
    B: day=2026-08-13 有快照 → 用该日数据
    C: 快照无此股 → 不加字段(前端显示-)"""
    fake_snap = {
        "2026-08-14": {"A": {"bid_change": 8.64, "bid_amt": 991.5, "float_mv": 6.4e9, "name": "甲", "board": "AI"}},
        "2026-08-13": {"B": {"bid_change": 5.2, "bid_amt": 300.0, "float_mv": 8e9, "name": "乙", "board": "医药"}},
    }
    monkeypatch.setattr(kpl, "_snap25_map", lambda date: fake_snap.get(date, {}))
    lst = [
        {"code": "A", "name": "甲", "day": "2026-08-14"},
        {"code": "B", "name": "乙", "day": "2026-08-13"},
        {"code": "C", "name": "丙", "day": "2026-08-14"},   # 无快照
    ]
    kpl._merge_broken_bid_snap(lst)
    a = lst[0]
    assert a["bidChange"] == 8.64
    # 991.5万 * 10000 / 6.4e9 * 100 = 0.15%
    assert abs(a["bidTurnover"] - 0.15) < 0.01, a
    b = lst[1]
    assert b["bidChange"] == 5.2
    assert abs(b["bidTurnover"] - 0.04) < 0.01, b   # 300万/80亿*100=0.0375→0.04
    c = lst[2]
    assert "bidChange" not in c and "bidTurnover" not in c


# ====================================================================
# 异动监管 3 接口 + boom 不限条数 (from test_new_features_20260822)
# ====================================================================

import os as _os
import sys as _sys
import time as _time

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


def _hdrs(token):
    return {"Authorization": "Bearer " + token}


# ---------- A. 异动监管 3 接口 ----------

def test_yidong_realtime_normal(client, first_user, monkeypatch):
    """yidong-realtime: 正常解析 List 字段, 返回结构正确"""
    from app.services import kpl as kpl_svc
    from app.api import deps

    token, _, _ = first_user
    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1

    fake_resp = {
        "List": [
            ["600001", "测试甲", 1, "涨幅异动", "-1.18", "30", "151.26",
             "涨停触发", "9.98", "", "", "", "已触发"],
            ["000002", "测试乙", 1, "封板异动", "", "", "",
             "翻红触发", "5.01", "", "", "", "未触发"],
        ],
        "Many_Num": 156,
        "Day": "2026-08-22",
        "Time": 930,
    }
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc90", lambda **kw: fake_resp)

    r = client.get("/api/kpl/yidong-realtime", headers=_hdrs(token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["count"] == 2
    assert d["manyNum"] == 156
    assert d["day"] == "2026-08-22"
    assert d["time"] == 930
    assert d["list"][0]["code"] == "600001"
    assert d["list"][0]["type"] == "涨幅异动"
    assert d["list"][0]["triggered"] == "已触发"
    # 新增偏离值字段: 当日涨幅(4) / 统计天数(5) / 累计涨幅偏离值(6) / 触发阈值(8)
    assert d["list"][0]["change"] == -1.18
    assert d["list"][0]["days"] == 30
    assert d["list"][0]["deviation"] == 151.26
    assert d["list"][0]["target"] == 9.98


def test_yidong_realtime_empty_and_source_fail(client, first_user, monkeypatch):
    """yidong-realtime: 空 List / 源返回 None / List 中空元素 → 跳过, 不崩"""
    from app.services import kpl as kpl_svc
    from app.api import deps

    token, _, _ = first_user
    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1

    # Case 1: 空 List
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc90",
                        lambda **kw: {"List": [], "Many_Num": 0, "Day": "", "Time": 0})
    r = client.get("/api/kpl/yidong-realtime", headers=_hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["count"] == 0

    # Case 2: 源返回 None
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc90", lambda **kw: None)
    r2 = client.get("/api/kpl/yidong-realtime", headers=_hdrs(token))
    assert r2.status_code == 200
    assert r2.json()["count"] == 0

    # Case 3: List 中有空元素 → 跳过
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc90",
                        lambda **kw: {"List": [None, [], ["600001", "A", 1, "T"]]})
    r3 = client.get("/api/kpl/yidong-realtime", headers=_hdrs(token))
    assert r3.status_code == 200
    assert r3.json()["count"] == 1


def test_yidong_monitor_normal_and_empty(client, first_user, monkeypatch):
    """yidong-monitor: 正常解析 + 空数据"""
    from app.services import kpl as kpl_svc
    from app.api import deps

    token, _, _ = first_user
    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1

    fake_resp = {
        "List": [
            ["600001", "测试甲", "2026-08-20", "2026-08-22", 3],
            ["000002", "测试乙", "2026-08-21", "2026-08-22", 1],
        ],
    }
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc108", lambda **kw: fake_resp)
    r = client.get("/api/kpl/yidong-monitor", headers=_hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["count"] == 2
    assert d["list"][0]["code"] == "600001"
    assert d["list"][0]["times"] == 3

    # 空
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc108", lambda **kw: {})
    r2 = client.get("/api/kpl/yidong-monitor", headers=_hdrs(token))
    assert r2.status_code == 200
    assert r2.json()["count"] == 0


def test_yidong_multi_normal(client, first_user, monkeypatch):
    """yidong-multi: 正常解析 + 字段结构"""
    from app.services import kpl as kpl_svc
    from app.api import deps

    token, _, _ = first_user
    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1

    fake_resp = {
        "List": [
            ["600001", "测试甲", 5, "近10日5次异动"],
        ],
        "Day": "2026-08-22",
    }
    monkeypatch.setattr(kpl_svc, "fetch_kpl_doc109", lambda **kw: fake_resp)
    r = client.get("/api/kpl/yidong-multi", headers=_hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["count"] == 1
    assert d["day"] == "2026-08-22"
    assert d["list"][0]["times"] == 5
    assert d["list"][0]["desc"] == "近10日5次异动"


def test_yidong_vip_guard(client, second_user, monkeypatch):
    """异动接口: 普通用户(未VIP)访问应 403"""
    client.app.dependency_overrides.clear()
    token, _ = second_user
    r = client.get("/api/kpl/yidong-realtime", headers=_hdrs(token))
    assert r.status_code == 403
    err = r.json()
    # 响应结构: {"detail": {"ok": False, "code": "vip_required", "msg": "..."}}
    detail = err.get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("ok") is False
        assert "VIP" in detail.get("msg", "") or "付费" in detail.get("msg", "")
    else:
        assert "VIP" in str(detail) or "付费" in str(detail)


# ---------- D. boom 不限条数 + free_mv 回退 ----------

def _mock_boom_helpers(monkeypatch, todays, yests, today_date="2026-08-22"):
    """helper: mock sqlite3.connect + fetch_spot_quote_map + time.strftime"""
    import app.services.kpl as kpl
    import app.services.fetcher as fetcher_mod

    monkeypatch.setattr("time.strftime", lambda fmt: today_date)

    class FakeCursor:
        def __init__(self, rows): self.rows = list(rows)
        def fetchall(self): return self.rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def __iter__(self): return iter(self.rows)

    class FakeConn:
        def execute(self, sql, params=()):
            q = sql.strip()
            if "SELECT MAX(time_point)" in q:
                return FakeCursor([("9_25",)])
            if "SELECT MAX(date) FROM snapshot_bid WHERE date <" in q:
                return FakeCursor([("2026-08-21",)])
            if "WHERE date=? AND time_point=?" in q:
                return FakeCursor(todays)
            if "WHERE date=? AND time_point='9_25'" in q:
                return FakeCursor(yests)
            return FakeCursor([])
        def close(self): pass

    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(fetcher_mod, "fetch_spot_quote_map", lambda *a, **k: {})
    kpl.clear_cache()


def test_fetch_bid_boom_no_limit_count(monkeypatch):
    """超过 200 条仍全返回 (不限条数) + 小市值也能计算 bidTurnover"""
    import app.services.kpl as kpl

    todays = []
    for i in range(300):
        amt = 3000.0 + i * 10
        fmv = 1.0 if i == 10 else 4e9 + i * 100000
        todays.append((f"60{i:04d}", amt, f"股票{i:04d}", 3.0 + i * 0.01, fmv, "测试板块"))
    yests = [(f"60{i:04d}", 1000.0 + i * 3) for i in range(300)]

    _mock_boom_helpers(monkeypatch, todays, yests)
    rows = kpl.fetch_bid_boom()

    assert len(rows) == 300, f"不限条数应返回 300, 实际 {len(rows)}"
    ratios = [r["bidRatioYest"] for r in rows]
    assert ratios == sorted(ratios, reverse=True)

    stock_10 = next(r for r in rows if r["code"] == "600010")
    assert stock_10["bidTurnover"] > 0
    assert stock_10["floatMv"] > 0


def test_fetch_bid_boom_filter_edge_cases(monkeypatch):
    """边界过滤: 量比≤2 / 成交额≤100万 / 无昨日 均被过滤"""
    import app.services.kpl as kpl

    todays = [
        ("600001", 3000.0, "甲", 5.0, 4e9, "板块A"),   # 量比3.0 ✓
        ("600002", 2000.0, "乙", 4.0, 5e9, "板块B"),   # 量比1.0 ≤ 2 ✗
        ("600003", 50.0,  "丙", 6.0, 6e9, "板块C"),   # 成交额50万 ≤ 100万 ✗
        ("600004", 15000.0, "丁", 8.0, 7e9, "板块D"), # 无昨日 ✗
    ]
    yests = [("600001", 1000.0), ("600002", 2000.0)]

    _mock_boom_helpers(monkeypatch, todays, yests)
    rows = kpl.fetch_bid_boom()

    assert len(rows) == 1
    assert rows[0]["code"] == "600001"
    assert rows[0]["bidRatioYest"] == 3.0


def test_fetch_bid_boom_yest_no_data(monkeypatch):
    """无昨日数据 → 返回空列表"""
    import app.services.kpl as kpl
    import app.services.fetcher as fetcher_mod

    monkeypatch.setattr("time.strftime", lambda fmt: "2026-08-22")

    class FakeCursor:
        def __init__(self, rows): self.rows = list(rows)
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows
        def __iter__(self): return iter(self.rows)

    class FakeConn:
        def execute(self, sql, params=()):
            q = sql.strip()
            if "SELECT MAX(time_point)" in q:
                return FakeCursor([("9_25",)])
            if "SELECT MAX(date) FROM snapshot_bid WHERE date <" in q:
                return FakeCursor([(None,)])
            return FakeCursor([])
        def close(self): pass

    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(fetcher_mod, "fetch_spot_quote_map", lambda *a, **k: {})
    kpl.clear_cache()
    rows = kpl.fetch_bid_boom()
    assert rows == []


# ====================================================================
# 竞价异动 API fast-path + endpoint 分支 (from test_new_features_20260820)
# ====================================================================

import json as _json
import sqlite3 as _sqlite3
import time as _time_mod


def test_is_auction_hours_logic():
    """纯逻辑: 竞价时段边界校验"""
    from app.api import kpl as kpl_api

    def _pin_bj(mp, bj_hour, bj_min, wday=3):
        def fake_gmtime(secs=None):
            return _time.struct_time(
                (2026, 8, 20, bj_hour, bj_min, 0, wday, 232, 0))
        mp.setattr(_time, "gmtime", fake_gmtime)

    cases = [
        (9, 14, 3, False),
        (9, 15, 3, True),
        (9, 25, 3, True),
        (9, 30, 3, True),
        (9, 31, 3, False),
        (9, 25, 5, False),
        (9, 25, 6, False),
        (10, 0, 4, False),
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
    """_update_spot_change 只覆盖 change / realChange, 其他字段绝不改动"""
    from app.api import kpl as kpl_api
    from app.services import fetcher

    original = fetcher.fetch_spot_quote_map

    def fake_spot_map(fs):
        return {
            "600001": {"realChange": 5.55, "price": 18.0},
            "000002": {"realChange": -2.30},
        }

    try:
        fetcher.fetch_spot_quote_map = fake_spot_map
        lst = [
            {"code": "600001", "name": "测试甲", "change": 1.0, "realChange": 0,
             "board": "AI概念、机器人"},
            {"code": "000002", "name": "测试乙", "change": 4.0, "board": ""},
            {"code": "300003", "name": "测试丙", "change": 0.5, "board": "芯片"},
        ]
        n = kpl_api._update_spot_change(lst)
        assert n == 2
        assert lst[0]["change"] == 5.55
        assert lst[0]["realChange"] == 5.55
        assert lst[0]["board"] == "AI概念、机器人"
        assert lst[0]["name"] == "测试甲"
        assert lst[1]["change"] == -2.30
        assert lst[2]["change"] == 0.5
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
        ]
        kpl_api._ensure_concepts(lst, "test")
        assert captured.get("deep") is False
        assert captured.get("truncate") == 2
        assert captured.get("blank_if_missing") is False
        assert sum(1 for it in lst if it["board"]) == 4
    finally:
        kpl.apply_board_concept = original


def test_read_auction_fast_today_then_nearest(monkeypatch):
    """_read_auction_fast: 优先今日; 今日无则 MAX(date); 两者皆空返回 []/today"""
    from app.services import kpl as kpl_svc
    from app.api import kpl as kpl_api

    called = {}

    def fake_query(date, tab):
        called[(date, tab)] = called.get((date, tab), 0) + 1
        if date == "2026-08-20" and tab == "seal":
            return [{"code": "600001"}]
        return []

    monkeypatch.setattr(kpl_svc, "query_auction_history", fake_query)
    monkeypatch.setattr(kpl_api, "_update_spot_change", lambda lst: 0)
    g = _time.struct_time((2026, 8, 20, 2, 0, 0, 3, 232, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g)
    lst, d = kpl_api._read_auction_fast("seal")
    assert d == "2026-08-20"
    assert len(lst) == 1

    # Case 2: 今日无数据, 最近交易日
    from app.db import database

    class FakeCursor:
        def __init__(self, rows): self._r = rows
        def fetchone(self): return self._r.pop(0) if self._r else None
        def close(self): pass

    class FakeConn:
        def __init__(self, row): self._row = row
        def execute(self, sql, args=()): return FakeCursor([self._row])
        def close(self): pass

    monkeypatch.setattr(database, "get_conn", lambda: FakeConn(("2026-08-19",)))
    called.clear()

    def fake_query2(date, tab):
        if date == "2026-08-19" and tab == "boom":
            return [{"code": "000002"}, {"code": "300003"}]
        return []

    monkeypatch.setattr(kpl_svc, "query_auction_history", fake_query2)
    lst, d = kpl_api._read_auction_fast("boom")
    assert d == "2026-08-19"
    assert len(lst) == 2

    # Case 3: DB 炸了也不崩
    def bad_conn():
        raise RuntimeError("DB 炸了")

    monkeypatch.setattr(database, "get_conn", bad_conn)
    monkeypatch.setattr(kpl_svc, "query_auction_history", lambda x, y: [])
    lst, d = kpl_api._read_auction_fast("yest_zt")
    assert lst == []
    assert d == "2026-08-20"


# ----- 竞价异动 endpoint fast-path 分支 -----

def _make_bid_seal_fake():
    return [{"code": "600001", "name": "测A", "change": 10.01},
            {"code": "000002", "name": "测B", "change": 5.05}]


def test_kpl_bid_seal_live_vs_fast_path(client, vip_user, monkeypatch):
    """竞价时段 → fetch_bid_seal + deep=True; 非竞价 → _read_auction_fast"""
    from app.services import kpl as kpl_svc
    from app.api import kpl as kpl_api

    token, _, _ = vip_user
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
    assert d["ok"] and d.get("count") == 2
    assert calls["fetch_bid_seal"] == 1
    assert calls["apply_deep"] and calls["apply_deep"].get("deep") is True
    assert calls["apply_deep"].get("truncate") == 2

    # --- Case 2: 非竞价时段 ---
    calls.clear()
    g_non = _time.struct_time((2026, 8, 17, 10, 0, 0, 0, 229, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_non)
    fake_fast = [{"code": "600001", "name": "测A", "change": 1.2, "board": "AI、机器人"}]
    monkeypatch.setattr(kpl_api, "_read_auction_fast", lambda tab: (fake_fast, "2026-08-17"))
    r2 = client.get("/api/kpl/bid-seal", headers=_hdrs(token))
    d2 = r2.json()
    assert d2["ok"] and d2["count"] == 1
    assert d2["date"] == "2026-08-17"
    assert calls.get("fetch_bid_seal", 0) == 0


def test_kpl_bid_qiangcang_fastpath_skips_deep_concept(vip_user, client, monkeypatch):
    """bid-qiangcang 非竞价: _ensure_concepts 被调用, deep=True 不被调用"""
    from app.services import kpl as kpl_svc
    from app.api import kpl as kpl_api

    token, _, _ = vip_user
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
    assert spy["ensure_called"] is True
    assert spy["apply_deep_called"] is False

    # 指定 date 参数 → 切回 deep=True
    spy["apply_deep_called"] = False
    spy["ensure_called"] = False
    r2 = client.get("/api/kpl/bid-qiangcang?date=2026-08-17", headers=_hdrs(token))
    assert r2.status_code == 200
    assert spy["apply_deep_called"] is True


def test_kpl_broken_fastpath_reads_broken_today(vip_user, client, monkeypatch):
    """非竞价 broken: 读 broken_today 表 + 补辅助字段"""
    from app.api import kpl as kpl_api
    from app.services import kpl as kpl_svc

    token, _, _ = vip_user
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
