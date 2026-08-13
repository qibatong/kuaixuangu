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
