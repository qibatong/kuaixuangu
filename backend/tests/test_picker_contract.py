# -*- coding: utf-8 -*-
"""契约层单测 (重构 P0) — 重点断言三条铁律, 尤其是两个事故根因:
  ① f615 缺失/' -' 时**不得**退化取 f3(老逻辑隐式 fallback → 竞涨=现涨 → 大跌票混入)
  ② 缺字段 = None 而非 0(老逻辑填 0 → priceGt 静默失效 → 名单虚胖一倍)
"""
import math

from app.services.picker.contract import QuoteRow


# ---------------- 铁律1: 缺失 = None, 永不填 0 ----------------
def test_defaults_are_none_not_zero():
    r = QuoteRow(code="000001", name="平安银行")
    for f in ("price", "real_change", "bid_change", "bid_amt", "float_mv",
              "open", "prev_close", "vol", "amount", "turnover"):
        assert getattr(r, f) is None, "%s 默认值应为 None(不是 0)" % f


def test_missing_and_require():
    r = QuoteRow(code="000001", price=10.0)
    assert r.missing("bid_change", "price") == ["bid_change"]
    assert r.require("price") is True
    assert r.require("price", "bid_change") is False


def test_missing_fields_excludes_meta():
    r = QuoteRow(code="000001")
    miss = r.missing_fields()
    assert "source" not in miss and "degraded" not in miss
    assert "price" in miss and "bid_change" in miss


# ---------------- 铁律1 事故根因: 不得退化取 f3 ----------------
def test_bid_change_not_fallback_to_f3_outside_window():
    """窗口外 f615 缺失 → bid_change 必须 None, 绝不能变成 f3(现涨幅)"""
    s = {"f12": "000001", "f14": "平安银行", "f2": 10.5, "f3": -8.2, "f615": None}
    r = QuoteRow.from_eastmoney(s, auction_window=False)
    assert r.bid_change is None, "窗口外 f615 缺失不得退化取 f3"
    assert r.real_change == -8.2     # 现涨幅仍应正常


def test_bid_change_dash_string_not_fallback():
    """盘后东财 f615 = '-'(现价涨幅也无意义) → 必须 None"""
    s = {"f12": "000001", "f14": "平安银行", "f2": 10.5, "f3": 3.1, "f615": "-"}
    r = QuoteRow.from_eastmoney(s, auction_window=True)
    assert r.bid_change is None


def test_bid_change_taken_in_auction_window():
    """竞价窗口内 f615 有效 → 正常取"""
    s = {"f12": "000001", "f14": "平安银行", "f2": 10.5, "f3": 1.0, "f615": "4.32"}
    r = QuoteRow.from_eastmoney(s, auction_window=True)
    assert r.bid_change == 4.32


def test_day_bid_change_overrides_realtime():
    """9:25 定格值是竞价字段的权威来源, 优先于实时 f615"""
    s = {"f12": "000001", "f14": "平安银行", "f615": "1.0", "f616": "500"}
    r = QuoteRow.from_eastmoney(s, auction_window=True,
                                day_bid_change=5.12, day_bid_amt_wan=3200.0)
    assert r.bid_change == 5.12
    assert r.bid_amt == 3200.0 * 1e4      # 万元 → 元


def test_bid_amt_not_from_f6_outside_window():
    """窗口外禁止拿 f6(累计成交额)当竞价额 — 老逻辑曾算出 1000%+ 荒谬昨比"""
    s = {"f12": "000001", "f6": 999999999.0}
    r = QuoteRow.from_eastmoney(s, auction_window=False)
    assert r.bid_amt is None
    assert r.amount == 999999999.0        # 成交额本身正常保留


# ---------------- 派生量: 缺数据 → None, 不误判 ----------------
def test_is_suspended_none_when_unknown():
    """停牌判断: 老逻辑 f4/f5 缺失会误判停牌(2026-09-01 事故), 契约下应返回未知 None"""
    assert QuoteRow(code="1").is_suspended is None
    assert QuoteRow(code="1", prev_close=10.0, vol=0).is_suspended is True
    assert QuoteRow(code="1", prev_close=10.0, vol=1000).is_suspended is False


def test_entity_change_none_without_open():
    """缺今开 → 实体涨幅 None(老逻辑填 0 → 实体列全 0%)"""
    assert QuoteRow(price=10.0, open=None).entity_change is None
    r = QuoteRow(price=11.0, open=10.0)
    assert math.isclose(r.entity_change, 10.0)


def test_bid_turnover_none_when_missing():
    assert QuoteRow(bid_vol=100, price=10.0).bid_turnover is None   # 缺市值
    r = QuoteRow(bid_vol=100, price=10.0, float_mv=1e9)
    assert math.isclose(r.bid_turnover, 100 * 10.0 / 1e9 * 100)


# ---------------- 快照行映射 ----------------
def test_snapshot_float_mv_fallback_free_mv():
    """快照脏数据: float_mv=0 但 free_mv 有值 → 必须回退(老逻辑只取 float_mv → 被误杀)"""
    v = {"code": "000002", "name": "万科A", "float_mv": 0, "free_mv": 8.5e9,
         "bid_change": 3.35, "bid_amt": 4200.0, "pre_close": 12.0}
    r = QuoteRow.from_snapshot(v)
    assert r.float_mv == 8.5e9
    assert r.bid_change == 3.35
    assert r.bid_amt == 4200.0 * 1e4
    assert r.price == 12.0                # 定格无实时价 → 昨收(DEGRADE_RULES)
    assert r.source == "snapshot"


def test_snapshot_degraded_flag():
    v = {"code": "000002", "bid_change": 1.0}
    assert QuoteRow.from_snapshot(v, degraded=True).degraded is True


# ---------------- 腾讯行映射 ----------------
def test_tencent_row_no_fake_bid_fields():
    """腾讯无竞价专属字段 → bid_* 必须 None, 不拿现价涨幅冒充竞价涨幅"""
    f = [""] * 88
    f[1], f[2], f[3], f[4], f[5] = "贵州茅台", "600519", "1680.0", "1650.0", "1660.0"
    f[31], f[36], f[37], f[38], f[43] = "1.82", "12000", "201600", "0.5", "21000"
    r = QuoteRow.from_tencent(f)
    assert r.code == "600519" and r.price == 1680.0 and r.prev_close == 1650.0
    assert r.real_change == 1.82
    assert r.bid_change is None and r.bid_amt is None     # 不得造假
    assert r.float_mv == 21000 * 1e8
    assert r.amount == 201600 * 1e4


# ---------------- 展示层: 单位转换 + None 透传 ----------------
def test_to_dict_units_and_none_passthrough():
    r = QuoteRow(code="000001", name="平安银行", price=10.0,
                 bid_amt=3.2e7, float_mv=2.5e10, amount=1.8e9)
    d = r.to_dict()
    assert d["bidAmt"] == 3200.0          # 元 → 万元
    assert d["circulationMV"] == 250.0    # 元 → 亿
    assert d["amount"] == 18.0            # 元 → 亿
    assert d["realChange"] is None        # None 原样透出(前端显示 '-' 而非 0.00)
    assert d["degraded"] is False


def test_bad_values_become_none():
    """非法值(''、'-'、NaN、非数字) 一律 None"""
    s = {"f12": "000001", "f2": "", "f3": "-", "f21": "abc", "f630": None}
    r = QuoteRow.from_eastmoney(s)
    assert r.price is None and r.real_change is None
    assert r.float_mv is None and r.warn_type is None
