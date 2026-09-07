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


# ---------- 昨日涨停判定(f103 概念标签) ----------
def test_is_first_board_yesterday_limit():
    """f103 含 '昨日涨停' 标签 → 昨日涨停, 应剔除"""
    raw = dict(QC_RAW)
    raw["f103"] = "AI概念,昨日涨停,华为概念"
    assert scorer.is_first_board(raw) is True


def test_is_first_board_yesterday_chain():
    """f103 含 '昨日连板' 标签 → 昨日涨停(连板), 应剔除"""
    raw = dict(QC_RAW)
    raw["f103"] = "AI概念,昨日连板,昨日连板_含一字"
    assert scorer.is_first_board(raw) is True


def test_is_first_board_yizi_tag():
    """f103 含 '昨日涨停_含一字' → 子串命中, 应剔除"""
    raw = dict(QC_RAW)
    raw["f103"] = "华为概念,昨日涨停_含一字"
    assert scorer.is_first_board(raw) is True


def test_is_first_board_no_tag():
    """f103 无昨日涨停/连板标签 → 非昨日涨停, 保留"""
    raw = dict(QC_RAW)
    raw["f103"] = "AI概念,华为概念,固态电池"
    assert scorer.is_first_board(raw) is False


def test_is_first_board_empty_concept():
    """f103 缺失/为空 → 非昨日涨停, 保留"""
    raw = dict(QC_RAW)
    raw["f103"] = ""
    assert scorer.is_first_board(raw) is False
    raw2 = dict(QC_RAW)
    raw2.pop("f103", None)
    assert scorer.is_first_board(raw2) is False


def test_is_first_board_ignores_f630():
    """回归: 旧逻辑 f630>=5 已废弃, f630 值不影响昨日涨停判断(f630 实际只有 0/1/2)"""
    raw = dict(QC_RAW)
    raw["f630"] = 5          # 旧逻辑会误判为昨日涨停
    raw["f103"] = "AI概念"    # 但概念无标签
    assert scorer.is_first_board(raw) is False
    raw2 = dict(QC_RAW)
    raw2["f630"] = 0          # f630=0 但概念有标签
    raw2["f103"] = "昨日涨停,AI概念"
    assert scorer.is_first_board(raw2) is True


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
    yesterday = {"600001": [20000.0, 15000.0]}   # [T日2亿, T-1日1.5亿] 万元 → 竞价5000万/2亿 = 25%
    raw = dict(QC_RAW)
    raw["f616"] = 5.0e7               # 竞价 5000万
    f = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
         "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}
    result = scorer.process_all_stocks([raw], f, yesterday)
    assert len(result) == 1
    assert result[0]["qiangchou"] == 1


# ---------- 竞价/昨比 窗口口径 ----------
def test_bid_ratio_any_time_uses_last_closed_day(monkeypatch):
    """竞价/昨比任何时间都计算: 分子=今日竞价额(f616), 分母=最近已收盘交易日(T)。
    pair=[最近已收盘T, T-1], 窗口/盘中/收盘同一口径, 避免今日累计额/地量日/前天错位失真。"""
    # 窗口内: pair[0]=最近已收盘 2亿 → 5000万/2亿 = 25%
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    yesterday = {"600001": [20000.0, 15000.0]}
    raw = dict(QC_RAW)
    raw["f616"] = 5.0e7
    f = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
         "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}
    result = scorer.process_all_stocks([raw], f, yesterday)
    assert abs(result[0]["bidRatio"] - 25.0) < 0.01
    # 非窗口(盘中/收盘): 分子改认 9:25 定格快照(2026-09-07 起窗口外不回退 f616;
    # day_bid_amt 有定格值 → 同样用 pair[0]=最近已收盘交易日, 仍计算显示)
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    result2 = scorer.process_all_stocks([raw], f, yesterday, day_bid_amt={"600001": 5000.0})
    assert abs(result2[0]["bidRatio"] - 25.0) < 0.01
    # pair 缺失 → None(定格有值但分母缺 → None)
    result3 = scorer.process_all_stocks([raw], f, {}, day_bid_amt={"600001": 5000.0})
    assert result3[0]["bidRatio"] is None


def test_get_bid_amt_off_window_no_f6_fallback(monkeypatch):
    """非窗口 f616 缺失时不得退回 f6(盘中 f6=累计成交额, 会算成荒谬比值)"""
    # 窗口内: f616 缺失退回 f6 → 竞价额 = f6
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    raw = dict(QC_RAW)
    raw.pop("f616", None)
    raw["f6"] = 5.0e7    # 窗口内 f6≈竞价额 5000万
    assert abs(scorer.get_bid_amt(raw, True) - 5000.0) < 1
    # 非窗口: f616 缺失 → 不退回 f6, 返回 0
    assert scorer.get_bid_amt(raw, False) == 0.0
    # 非窗口但 f616 有值 → 正常返回(竞价定格值仍可展示为竞价额)
    raw2 = dict(QC_RAW)
    raw2["f616"] = 5.0e7
    assert abs(scorer.get_bid_amt(raw2, False) - 5000.0) < 1


def test_bid_ratio_none_without_pair(monkeypatch):
    """无日K pair 时 bidRatio 为 None, 抢筹为 0"""
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    raw = dict(QC_RAW)
    raw["f616"] = 5.0e7
    f = {"stSuspend": False, "limitUp": False, "bidGt": 7, "probLt": 65, "confLt": 65,
         "floatMvFloor": 1, "floatMvGt": 5000, "priceGt": 5000, "bidAmtFloor": 0}
    result = scorer.process_all_stocks([raw], f, {})
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
