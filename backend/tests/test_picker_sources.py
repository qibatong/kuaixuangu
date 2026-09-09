# -*- coding: utf-8 -*-
"""重构 P1 数据源适配层测试

防复发断言(每一条都对应一次真实事故):
  1. 腾讯源不得拿现价涨幅冒充竞价涨幅(9/7 竞涨=现涨 → 大跌票混入)
  2. 东财窗口外 f615 缺失不得退化 f3(同上根因)
  3. 快照 float_mv=0 必须回退 free_mv(老逻辑只取 float_mv → 市值 0 被误杀)
  4. 降级行 price 不得填 0(9/8 priceGt 静默失效 → 名单虚胖一倍)
  5. 适配层任何失败都返回 SourceResult.error, 绝不抛异常(降级必须可见)
  6. mode.source_priority 的标签必须都在 REGISTRY(防 pipeline 取不到源)
"""
import pytest

from app.services import fetcher
from app.services.picker import mode as pm
from app.services.picker.contract import QuoteRow
from app.services.picker.sources import base as sb
from app.services.picker.sources import eastmoney, snapshot, tencent

# 东财 diff 行样例(f2现价 f3涨幅 f4/f18昨收 f5量(手) f6额 f8换手 f17今开 f21流通市值)
EM_ROW = {
    "f12": "600354", "f14": "敦煌种业", "f2": 10.5, "f3": 3.45, "f4": 10.15,
    "f18": 10.15, "f5": 12345.0, "f6": 1.29e8, "f8": 2.5, "f10": 1.8,
    "f17": 10.2, "f21": 5.5e9, "f100": "农牧", "f103": "农业",
    "f615": 3.45, "f616": 5.0e7, "f617": 4.8e6, "f630": 2,
}


def _ctx(mode_key, **over):
    """构造 FetchContext"""
    policy = pm.POLICIES[mode_key]
    kw = dict(policy=policy, date="2026-09-08")
    kw.update(over)
    return sb.FetchContext(**kw)


# ==================== 东财点查源 ====================
def test_eastmoney_maps_basic_fields(monkeypatch):
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", lambda codes: [dict(EM_ROW)])
    r = eastmoney.EastmoneyRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    assert r.ok and r.error is None
    row = r.rows["600354"]
    assert row.code == "600354" and row.name == "敦煌种业"
    assert row.price == 10.5 and row.prev_close == 10.15 and row.open == 10.2
    assert row.real_change == 3.45 and row.turnover == 2.5 and row.vol_ratio == 1.8
    assert row.vol == 12345.0 * 100                 # 手 → 股
    assert row.amount == 1.29e8 and row.float_mv == 5.5e9
    assert row.warn_type == 2 and row.source == "eastmoney"


def test_eastmoney_auction_window_reads_f615(monkeypatch):
    """竞价窗口内且无定格 → 允许取实时 f615/f616(这是唯一合法的实时竞价来源)"""
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", lambda codes: [dict(EM_ROW)])
    r = eastmoney.EastmoneyRealtimeSource().run(_ctx(pm.PickMode.AUCTION, codes=["600354"]))
    row = r.rows["600354"]
    assert row.bid_change == 3.45
    assert row.bid_amt == 5.0e7


def test_eastmoney_outside_window_never_falls_back_to_f3(monkeypatch):
    """★防复发: 窗口外 f615='-' → bid_change 必须 None, 绝不能退化成 f3(现涨幅)"""
    bad = dict(EM_ROW, f615="-", f616="-", f3=-8.2)     # 大跌票 + 无竞价数据
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", lambda codes: [bad])
    r = eastmoney.EastmoneyRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    row = r.rows["600354"]
    assert row.bid_change is None, "窗口外竞价涨幅缺失必须 None, 不得退化 f3(大跌票混入根因)"
    assert row.bid_amt is None
    assert row.real_change == -8.2                       # 现涨幅照常保留


def test_eastmoney_day_bid_map_overrides_realtime(monkeypatch):
    """9:25 定格是竞价字段的权威来源, 优先于窗口内实时值"""
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", lambda codes: [dict(EM_ROW)])
    ctx = _ctx(pm.PickMode.AUCTION, codes=["600354"],
               day_bid_change={"600354": 1.23}, day_bid_amt_wan={"600354": 4567.0})
    row = eastmoney.EastmoneyRealtimeSource().run(ctx).rows["600354"]
    assert row.bid_change == 1.23, "定格值必须覆盖实时 f615"
    assert row.bid_amt == 4567.0 * 1e4


def test_eastmoney_requires_codes():
    """点查源不接受无候选集调用(那会退化成全市场 → 名单波动根因)"""
    r = eastmoney.EastmoneyRealtimeSource().run(_ctx(pm.PickMode.INTRADAY))
    assert not r.ok and "候选代码集" in (r.error or "")


def test_eastmoney_exception_becomes_error(monkeypatch):
    """★铁律2: 异常必须转成 error, 绝不向上抛(降级必须可见而非崩给前端)"""
    def boom(codes):
        raise RuntimeError("Remote end closed connection without response")
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", boom)
    r = eastmoney.EastmoneyRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    assert not r.ok and "RuntimeError" in (r.error or "")
    assert r.rows == {}


def test_eastmoney_empty_result_is_error(monkeypatch):
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", lambda codes: [])
    r = eastmoney.EastmoneyRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    assert not r.ok and r.rows == {}


# ==================== 快照定格源 ====================
def test_snapshot_maps_and_converts_units(monkeypatch):
    monkeypatch.setattr(snapshot.auction_snapshot, "load_snapshot_full",
                        lambda d: {"600354": {"name": "敦煌种业", "bid_change": 4.5,
                                              "bid_amt": 3200.0, "float_mv": 5.5e9,
                                              "free_mv": 5.5e9, "board": "hs"}})
    r = snapshot.SnapshotSource().run(_ctx(pm.PickMode.INTRADAY))
    assert r.ok
    row = r.rows["600354"]
    assert row.bid_change == 4.5
    assert row.bid_amt == 3200.0 * 1e4, "快照 bid_amt 单位=万元, 契约内统一为元"
    assert row.float_mv == 5.5e9
    assert row.source == "snapshot"


def test_snapshot_float_mv_zero_falls_back_to_free_mv(monkeypatch):
    """★防复发: 快照历史脏数据 float_mv=0 但 free_mv 有值 → 必须回退, 否则被误杀"""
    monkeypatch.setattr(snapshot.auction_snapshot, "load_snapshot_full",
                        lambda d: {"000002": {"name": "万科A", "bid_change": 1.1,
                                              "bid_amt": 900.0, "float_mv": 0.0,
                                              "free_mv": 1.2e11, "board": "hs"}})
    row = snapshot.SnapshotSource().run(_ctx(pm.PickMode.INTRADAY)).rows["000002"]
    assert row.float_mv == 1.2e11, "float_mv=0 必须回退 free_mv(老逻辑只取 float_mv 致市值 0)"


def test_snapshot_price_never_zero(monkeypatch):
    """★防复发: 快照无实时价 → price=None 而非 0(填 0 让 priceGt 静默失效 → 虚胖)"""
    monkeypatch.setattr(snapshot.auction_snapshot, "load_snapshot_full",
                        lambda d: {"600354": {"name": "X", "bid_change": 1.0,
                                              "bid_amt": 100.0, "float_mv": 1e9,
                                              "free_mv": 1e9, "board": "hs"}})
    row = snapshot.SnapshotSource().run(_ctx(pm.PickMode.INTRADAY)).rows["600354"]
    assert row.price is None, "无实时价不得填 0(0 会让价格门槛失效)"
    assert row.is_suspended is None, "数据不全时停牌判断应为'未知', 不得误判停牌"


def test_snapshot_respects_codes_filter(monkeypatch):
    monkeypatch.setattr(snapshot.auction_snapshot, "load_snapshot_full",
                        lambda d: {"600354": {"name": "A", "bid_change": 1.0, "bid_amt": 1.0,
                                              "float_mv": 1e9, "free_mv": 1e9},
                                   "000001": {"name": "B", "bid_change": 2.0, "bid_amt": 2.0,
                                              "float_mv": 1e9, "free_mv": 1e9}})
    r = snapshot.SnapshotSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    assert set(r.rows) == {"600354"}


def test_snapshot_empty_is_error(monkeypatch):
    monkeypatch.setattr(snapshot.auction_snapshot, "load_snapshot_full", lambda d: {})
    r = snapshot.SnapshotSource().run(_ctx(pm.PickMode.INTRADAY))
    assert not r.ok and "定格快照" in (r.error or "")


# ==================== 腾讯源 ====================
def test_tencent_never_fakes_bid_fields(monkeypatch):
    """★核心防复发: 腾讯无竞价字段, fetcher 会塞 f615=现价涨幅。
    适配层必须显式清空 → 否则重现"竞涨=现涨 → 大跌票混入"事故(9/7)。

    2026-09-09 口径细化: **仅竞价窗口内**例外 —— 9:15-9:25 尚未撮合, 现价即竞价
    虚拟价、累计额即竞价额, 此时 f615/f616 语义正确, 必须取(否则竞价窗口名单恒空,
    生产实证 25 次调用入选全为 0)。窗口外(INTRADAY/CLOSED...)一律清空, 本用例即守此。
    """
    raw = [dict(EM_ROW, f615=-7.8, f616=9.9e7)]     # 现价涨幅被塞进 f615
    monkeypatch.setattr(fetcher, "fetch_tencent_by_codes", lambda codes: raw)
    r = tencent.TencentPointSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    row = r.rows["600354"]
    assert row.bid_change is None, "腾讯无竞价数据, 不得拿 f615(现价涨幅)冒充竞价涨幅"
    assert row.bid_amt is None
    assert row.bid_vol is None
    assert row.real_change == 3.45                    # 实时展示字段照常保留
    assert row.source == "tencent" and row.degraded is True


def test_tencent_uses_day_bid_map_when_available(monkeypatch):
    """定格 map 是权威: 腾讯源有定格值时必须用定格"""
    monkeypatch.setattr(fetcher, "fetch_tencent_by_codes", lambda codes: [dict(EM_ROW)])
    ctx = _ctx(pm.PickMode.INTRADAY, codes=["600354"],
               day_bid_change={"600354": 2.34}, day_bid_amt_wan={"600354": 1200.0})
    row = tencent.TencentPointSource().run(ctx).rows["600354"]
    assert row.bid_change == 2.34
    assert row.bid_amt == 1200.0 * 1e4


def test_tencent_exception_and_empty_are_errors(monkeypatch):
    monkeypatch.setattr(fetcher, "fetch_tencent_by_codes",
                        lambda codes: (_ for _ in ()).throw(RuntimeError("timeout")))
    r = tencent.TencentPointSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    assert not r.ok and "timeout" in (r.error or "")
    monkeypatch.setattr(fetcher, "fetch_tencent_by_codes", lambda codes: [])
    r2 = tencent.TencentPointSource().run(_ctx(pm.PickMode.INTRADAY, codes=["600354"]))
    assert not r2.ok


# ==================== 注册表与模式对齐 ====================
def test_all_policy_source_labels_registered():
    """★防 pipeline KeyError: 模式里用到的每个源标签都必须能取到 adapter"""
    for key, pol in pm.POLICIES.items():
        for label in pol.source_priority:
            src = sb.get_source(label)
            assert src is not None, "模式 %s 声明了未注册的数据源标签 %r" % (key.value, label)
            assert src.label == label


def test_get_source_unknown_returns_none():
    assert sb.get_source("no_such_source") is None


def test_source_result_coverage_and_missing():
    r = sb.SourceResult(rows={"600354": QuoteRow(code="600354")}, requested=4)
    assert abs(r.coverage - 0.25) < 1e-9
    assert r.missing_codes(["600354", "000001"]) == ["000001"]
    empty = sb.SourceResult(rows={}, error="boom", requested=3)
    assert empty.ok is False and empty.coverage == 0.0


# ==================== 全市场源(签名与降级契约) ====================
def test_eastmoney_market_source_ok_and_error(monkeypatch):
    monkeypatch.setattr(fetcher, "ensure_cache", lambda a, fs, b: ([dict(EM_ROW)], None))
    r = eastmoney.EastmoneyMarketSource().run(_ctx(pm.PickMode.AUCTION, markets=["hs"]))
    assert r.ok and "600354" in r.rows
    monkeypatch.setattr(fetcher, "ensure_cache", lambda a, fs, b: (None, "东财全挂"))
    r2 = eastmoney.EastmoneyMarketSource().run(_ctx(pm.PickMode.AUCTION, markets=["hs"]))
    assert not r2.ok and "东财全挂" in (r2.error or "")


def test_tencent_market_source_strips_bid_fields(monkeypatch):
    """全市场腾讯兜底同样不得冒充竞价字段(与点查源同契约, 窗口外口径)"""
    monkeypatch.setattr(fetcher, "fetch_tencent_market", lambda fs: [dict(EM_ROW, f615=9.9)])
    r = tencent.TencentMarketSource().run(_ctx(pm.PickMode.CLOSED, markets=["hs"]))
    assert r.ok and r.rows["600354"].bid_change is None
    monkeypatch.setattr(fetcher, "fetch_tencent_market", lambda fs: [])
    r2 = tencent.TencentMarketSource().run(_ctx(pm.PickMode.CLOSED, markets=["hs"]))
    assert not r2.ok
