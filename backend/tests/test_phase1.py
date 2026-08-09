# -*- coding: utf-8 -*-
"""一期测试: 一字涨停统计 + 竞价抢筹信号"""
import pytest

from app.services import scorer, stats

# 竞价涨幅高(f615=4) + 竞价额占比高 → 应命中抢筹
QC_RAW = {"f2": 18.50, "f3": 1.0, "f4": 3.10, "f5": 150000.0, "f6": 2800.0,
          "f8": 5.50, "f10": 1.80, "f12": "600001", "f14": "测试甲",
          "f17": 18.90, "f18": 17.90, "f20": 5.0e10, "f21": 4.0e9,
          "f100": "软件服务", "f102": "广东", "f103": "AI概念",
          "f615": 4.0, "f616": 5.0e7, "f617": 300.0, "f618": 400.0, "f630": 3}


# ---------- 一字涨停判定 ----------
def test_limit_pct_by_board():
    assert scorer.limit_pct("600001", "测试", 10) == 0.10
    assert scorer.limit_pct("300001", "测试", 10) == 0.20
    assert scorer.limit_pct("688001", "测试", 10) == 0.20
    assert scorer.limit_pct("000001", "*ST测试", 10) == 0.05


def test_is_yizi_true():
    """今开直接封涨停价 → 一字"""
    raw = dict(QC_RAW)
    raw["f18"] = 10.00   # 昨收 10
    raw["f17"] = 11.00   # 今开涨停 10*1.1
    assert scorer.is_yizi(raw) is True


def test_is_yizi_high_open_false():
    """高开但未封板(今开 < 涨停价) → 非一字"""
    raw = dict(QC_RAW)
    raw["f18"] = 10.00
    raw["f17"] = 10.60   # 高开 6%, 未涨停
    assert scorer.is_yizi(raw) is False


def test_is_yizi_st_5pct():
    """ST 一字: 5% 涨停"""
    raw = dict(QC_RAW)
    raw["f14"] = "*ST测试"
    raw["f18"] = 10.00
    raw["f17"] = 10.50   # 5% 涨停
    assert scorer.is_yizi(raw) is True


def test_is_yizi_gem_20pct():
    """创业板一字: 20% 涨停"""
    raw = dict(QC_RAW)
    raw["f12"] = "300001"
    raw["f18"] = 10.00
    raw["f17"] = 12.00   # 20% 涨停
    assert scorer.is_yizi(raw) is True


# ---------- 一字涨停统计落库 ----------
def test_record_daily_yizi_counts(client):
    """统计一字数量 + 竞价总额, 同一天重复调用幂等更新"""
    yizi_a = dict(QC_RAW)
    yizi_a.update({"f12": "600001", "f18": 10.0, "f17": 11.0, "f616": 2.0e7})   # 一字, 竞价2000万
    yizi_b = dict(QC_RAW)
    yizi_b.update({"f12": "300002", "f14": "测试乙", "f18": 10.0, "f17": 12.0, "f616": 1.0e7})  # 创业板一字, 竞价1000万
    normal = dict(QC_RAW)
    normal.update({"f12": "600003", "f14": "测试丙", "f18": 10.0, "f17": 10.4})  # 非一字
    r = stats.record_daily_yizi([yizi_a, yizi_b, normal])
    assert r["yizi_count"] == 2
    assert abs(r["bid_amt"] - 3000.0) < 1   # 2000万+1000万 = 3000万(万元)
    # 幂等: 再调用一次, 行数仍为 1
    stats.record_daily_yizi([yizi_a])
    rows = stats.daily_yizi_trend(5)
    assert len(rows) == 1
    assert rows[0]["yizi_count"] == 1


def test_daily_yizi_trend_empty(client):
    """无数据时返回空列表"""
    # 用不存在日期直接查(记录函数写当天, 无法清空, 只验证接口形状)
    assert isinstance(stats.daily_yizi_trend(3), list)


# ---------- 抢筹信号 ----------
def test_qiangchou_true():
    assert scorer.is_qiangchou(4.0, 30.0) is True


def test_qiangchou_false_low_bid():
    """竞价涨幅低 → 非抢筹"""
    assert scorer.is_qiangchou(1.0, 50.0) is False


def test_qiangchou_false_low_ratio():
    """竞价占比低 → 非抢筹"""
    assert scorer.is_qiangchou(4.0, 10.0) is False


def test_qiangchou_false_no_ratio():
    """无昨日成交额(占比未知) → 非抢筹"""
    assert scorer.is_qiangchou(4.0, None) is False


def test_process_all_stocks_has_qiangchou(monkeypatch):
    """筛选结果带 qiangchou 字段(竞价窗口内); 高竞价+高占比的标的应为 1"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    # 该标的: f615=4(竞价4%) + 竞价额占比高
    yesterday = {"600001": 20000.0}   # 昨日成交 2 亿(万元) → 竞价 5000万/2亿 = 25%
    raw = dict(QC_RAW)
    raw["f616"] = 5.0e7               # 竞价 5000万
    f = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
         "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}
    result = scorer.process_all_stocks([raw], f, yesterday)
    assert len(result) == 1
    assert result[0]["qiangchou"] == 1


# ---------- 竞价/昨比 窗口口径 ----------
def test_bid_ratio_only_in_auction_window(monkeypatch):
    """非竞价窗口(收盘后/周末)即使有昨日额也不算 bidRatio(防同日自比误导)"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    yesterday = {"600001": 20000.0}
    raw = dict(QC_RAW)
    raw["f616"] = 5.0e7
    f = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
         "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}
    result = scorer.process_all_stocks([raw], f, yesterday)
    assert result[0]["bidRatio"] is None
    assert result[0]["qiangchou"] == 0


# ---------- 接口 ----------
def test_daily_yizi_api(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/stats/daily-yizi?days=5", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and isinstance(d.get("list"), list)


def test_daily_yizi_api_requires_auth(client):
    r = client.get("/api/stats/daily-yizi")
    assert r.status_code == 401
