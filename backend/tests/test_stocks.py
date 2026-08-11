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


# ---------- 盘中实时选股(mode=spot) ----------
def test_spot_mode_returns_stocks(client, first_user, monkeypatch):
    """盘中模式: 返回实时评分结果, 含盘中特有字段(量比/换手/封单)"""
    token, _, _ = first_user
    monkeypatch.setattr(fetcher, "fetch_zt_pool", lambda *a, **k: {})  # 涨停池无数据
    r = client.get("/api/stocks?action=refresh&mode=spot&markets=sh_sz&probLt=0&confLt=0",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and d.get("mode") == "spot"
    assert len(d.get("list", [])) > 0
    for s in d["list"]:
        assert s["code"] and s["name"]
        assert "realChange" in s and "volRatio" in s and "turnover" in s
        assert "sealRatio" in s and "limitBoards" in s
        assert "bidAmt" in s and "bidChange" in s   # 盘中保留竞价字段展示


def test_spot_mode_uses_zt_pool(client, first_user, monkeypatch):
    """盘中模式: 涨停池数据进入评分(封单/连板字段生效)"""
    token, _, _ = first_user
    # 给 600001 加封单 2亿, 流通市值 40亿 → 封成比 5% → 封单分满分
    monkeypatch.setattr(fetcher, "fetch_zt_pool",
                        lambda *a, **k: {"600001": {"fund": 2.0, "fb": 930, "lb": 3, "zbc": 0, "zdp": 10.0}})
    r = client.get("/api/stocks?action=refresh&mode=spot&markets=sh_sz&probLt=0&confLt=0",
                   headers=hdrs(token))
    d = r.json()
    items = {s["code"]: s for s in d.get("list", [])}
    assert "600001" in items
    assert items["600001"]["limitBoards"] == 3
    assert items["600001"]["sealRatio"] > 0


def test_spot_mode_invalid_mode(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stocks?action=refresh&mode=hack", headers=hdrs(token))
    assert r.status_code == 400


def test_spot_compute_score():
    """盘中评分: 健康涨幅+高量比+高换手+强封单 → 高分; 无封单 → 封单分低"""
    raw = {"f2": 18.50, "f3": 4.0, "f8": 6.0, "f10": 2.5, "f12": "600001", "f14": "甲", "f21": 4.0e9}
    sc = scorer.compute_score_spot(raw, {"fund": 2.0})   # 封单2亿/市值40亿=5%
    assert sc["probability"] > 70
    assert sc["sealRatio"] == 5.0
    # 无封单 → 封单分为默认档, 总分较低
    sc2 = scorer.compute_score_spot(raw, None)
    assert sc2["sealRatio"] == 0.0
    assert sc2["probability"] <= sc["probability"]


def test_spot_filters(monkeypatch):
    """盘中过滤: 涨幅区间/量比下限/换手区间生效"""
    f = {"stSuspend": False, "limitUp": False, "spotExcludeZT": False,
         "chgFloor": 0, "chgGt": 9.5, "volRatioFloor": 1, "turnoverFloor": 0, "turnoverGt": 0,
         "probLt": 0, "confLt": 0, "floatMvFloor": 0, "floatMvGt": 9999, "priceGt": 9999}
    items = [
        {"code": "1", "name": "甲", "probability": 80, "confidence": 70, "circulationMV": 50,
         "price": 10, "realChange": 4.0, "volRatio": 2.0, "turnover": 5.0, "limitBoards": 0,
         "_raw": {"f4": 1.0, "f5": 1000}},
        {"code": "2", "name": "乙", "probability": 70, "confidence": 60, "circulationMV": 50,
         "price": 10, "realChange": 12.0, "volRatio": 2.0, "turnover": 5.0, "limitBoards": 0,
         "_raw": {"f4": 1.0, "f5": 1000}},   # 涨幅超上限 → 剔除
        {"code": "3", "name": "丙", "probability": 70, "confidence": 60, "circulationMV": 50,
         "price": 10, "realChange": 4.0, "volRatio": 0.5, "turnover": 5.0, "limitBoards": 0,
         "_raw": {"f4": 1.0, "f5": 1000}},   # 量比低于下限 → 剔除
    ]
    result = scorer.apply_spot_filters(items, f)
    assert [x["code"] for x in result] == ["1"]


# ---------- 昨日成交额 pair 日期错位回归 ----------
def test_kline_amount_pair_skips_today(monkeypatch):
    """东财日K盘中含'今天'(未收盘)K线 → 必须跳过, pair[0] 恒为最近已收盘交易日
    (修复: 宏昌科技分母错用8/7(前天), 宝莱特错用地量日 的根因)"""
    import datetime
    # 构造: 今天(未收盘,累计额大) + 昨天 + 前天
    today = datetime.date.today().strftime("%Y-%m-%d")
    from datetime import timedelta
    yest = (datetime.date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    before = (datetime.date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    # 同花顺格式 YYYYMMDD
    yest8 = yest.replace("-", "")
    before8 = before.replace("-", "")
    today8 = today.replace("-", "")

    # 东财格式(含今天): 今天1.5亿, 昨天2亿, 前天1亿 → pair[0] 应为 2亿(昨天)
    east = [f"{before},10,10,10,10,100,{1.0e8}", f"{yest},10,10,10,10,200,{2.0e8}", f"{today},10,10,10,10,300,{1.5e8}"]
    p = fetcher._kline_amount_pair(east)
    assert p is not None and abs(p[0] - 20000.0) < 1   # 2亿(昨天, 万元)
    assert abs(p[1] - 10000.0) < 1                       # 1亿(前天)

    # 同花顺格式(不含今天): 昨天2亿, 前天1亿 → pair[0]=2亿
    ths = [f"{before8},10,10,10,10,100,{1.0e8}", f"{yest8},10,10,10,10,200,{2.0e8}"]
    p2 = fetcher._kline_amount_pair(ths)
    assert p2 is not None and abs(p2[0] - 20000.0) < 1
    assert abs(p2[1] - 10000.0) < 1
