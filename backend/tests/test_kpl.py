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
    kpl._cache.clear()
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
    kpl._cache.clear()
    r = kpl.fetch_zt_reason("001337")
    assert len(r) == 1
    assert r[0]["reason"] == "黄金；现货黄金创新高"
    assert r[0]["sclt"] == "日内龙一"


# ---------- 人气热榜解析 ----------
def test_fetch_hot_rank(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "Day": "2026-08-13",
        "List": [["600721", "百花医药", 0, 0, 1, 0, 0], ["600664", "哈药股份", 0, 5, 2, 0, 0]],
    })
    kpl._cache.clear()
    rows = kpl.fetch_hot_rank()
    assert len(rows) == 2
    assert rows[0]["code"] == "600721"
    assert rows[0]["change"] == 0
    assert rows[0]["rank"] == 1
    assert rows[1]["name"] == "哈药股份"
    assert rows[1]["change"] == 5


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
    kpl._cache.clear()
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
    kpl._cache.clear()
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
    kpl._cache.clear()
    perf = kpl.fetch_yesterday_perf()
    assert set(perf.keys()) == {"zt", "lb", "pb"}
    assert abs(perf["zt"]["change"] - 2.117) < 1e-9
    assert abs(perf["lb"]["change"] - 1.171) < 1e-9
    assert abs(perf["pb"]["change"] - 6.77) < 1e-9
    assert perf["zt"]["date"] == "2026-08-13"
    assert len(calls) == 3  # 三个板块接口都调了


# ---------- 竞价爆量(Type=10, 同源解析) ----------
def test_fetch_bid_boom(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: {
        "info": [["688825", "长鑫科技", 54.39, 1.63, 0, 2.39, 56546659, 0, 0, 0,
                  579284936, "储存、芯片", 244920289633, 199735586, 11806006154,
                  -11606270568, "首板"]],
    })
    kpl._cache.clear()
    rows = kpl.fetch_bid_boom()
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "688825"
    # 实测字段语义(2026-08-13): row6=竞价净额, row10=竞价成交额(爆量主指标)
    assert r["bidAmt"] == 579284936
    assert r["bidNetAmt"] == 56546659
    # Type=10 无委买额(row4 恒0), 从 Type=4 按代码合并补充
    assert r["bidSealAmt"] == 0
    assert r["limitBoards"] == 1
    assert r["board"] == "储存、芯片"


# ---------- 炸板(东财 flash) ----------
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
    kpl._cache.clear()
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
    kpl._cache.clear()
    rows = kpl.fetch_broken_zt("2026-08-12")
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-08-12"
    assert rows[0]["code"] == "002536"
    assert urls and "date=2026-08-12" in urls[0]   # URL 带 date 参数
    # yesterday → 解析为具体日期(日历兜底: 跳过周末)
    kpl._cache.clear()
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-12")
    rows2 = kpl.fetch_broken_zt("yesterday")
    assert rows2[0]["day"] == "2026-08-12"


# ---------- 调用失败降级 ----------
def test_call_failure_returns_none(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda *a, **k: None)
    kpl._cache.clear()
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
    kpl._cache.clear()
    rows = kpl.fetch_yest_zt()
    assert len(rows) == 2
    m = {r["code"]: r for r in rows}
    assert m["600266"]["stillLimit"] is False      # 今断
    assert m["600683"]["stillLimit"] is True       # 连板
    assert m["600683"]["limitUpDays"] == 3


def test_fetch_yest_broken(monkeypatch):
    _mk_flash_pool(monkeypatch,
                   [("600266", "城建发展", 10.0, 1), ("600683", "京投发展", 10.0, 3)],
                   today_codes=["600683"])   # 600266 今日未涨停 → 断板
    kpl._cache.clear()
    rows = kpl.fetch_yest_broken()
    codes = [r["code"] for r in rows]
    assert "600266" in codes
    assert "600683" not in codes


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
                return FakeCursor([(1, 5.0, 500.0), (2, 5.5, 800.0)])
            # 9_25: code, bid_change, bid_amt, float_mv, name
            return FakeCursor([
                (1, 6.0, 1000.0, 5e9, "A"),
                (2, 6.0, 900.0, 8e9, "B"),
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
    kpl._cache.clear()
    d = kpl.fetch_bid_qiangcang()
    monkeypatch.setattr("sqlite3.connect", real)
    assert isinstance(d, dict)
    l20 = d["list20"]
    lLast = d["listLast"]
    # 左表过滤 >5%: code1(10%) + code2(6%) 入选, code3(3.33%) code4(<2亿) code5(bidNetAmt=0) 过滤
    assert len(l20) == 2
    assert l20[0]["code"] == "1" and l20[0]["qcDelta"] == 10.0
    assert l20[1]["code"] == "2" and l20[1]["qcDelta"] == 6.0
    # 右表 9:24→9:25 段
    assert len(lLast) == 2
    mLast = {r["code"]: r for r in lLast}
    # code1: qcDeltaLast = 6.0 - 5.0 = 1.0%
    assert mLast[1]["qcDeltaLast"] == 1.0
    assert mLast[1]["bidChange24"] == 5.0
    # code2: qcDeltaLast = 6.0 - 5.5 = 0.5%
    assert mLast[2]["qcDeltaLast"] == 0.5
    # 右表按抢筹幅度降序: code1(1.0) > code2(0.5)
    assert lLast[0]["code"] == 1


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
                    ("600001", "测试甲", 1.5, 1e8, 12.3, 0.2, 5.0, 8e9, "AI概念"),
                    ("600002", "测试乙", 0.8, 5e7, 8.9, 0.1, 4.0, 6e9, "医药"),
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
    kpl._cache.clear()
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
    kpl._cache.clear()
    d2 = kpl.fetch_bid_qiangcang()
    assert len(d2["list20"]) == 2, d2   # 读库返回, 非僵尸数据
    assert d2["list20"][0]["code"] == "600001"
    assert d2["list20"][0]["qcDelta"] == 12.3
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
            # 9_25: code, bid_change, bid_amt, float_mv, name
            return FakeCursor([
                ("A", 6.0, 1000.0, 6e9, "甲"),
                ("B", 5.4, 800.0, 8e9, "乙"),
                ("C", 5.3, 700.0, 7e9, "丙"),
                ("D", 6.5, 900.0, 9e9, "丁"),
                ("E", 6.0, 500.0, 4e8, "戊"),   # fmv=4亿<5亿
                ("F", 6.0, 800.0, 8e9, "己"),   # 无秒级无9_24
            ])
        def close(self): pass
    real = sqlite3.connect
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(kpl, "_seal_map", lambda: {})
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [])   # 只看 listLast
    kpl._cache.clear()
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
