# -*- coding: utf-8 -*-
"""换源 WP0 猫爪源适配层测试(2026-09-24「去东财换猫爪」)

防复发断言(每条都对应一个具体的坑, 不是"覆盖率凑数"):
  1. **单位**: 猫爪 vol/auc_vol 是「手」, 契约是「股」⇒ 必须 ×100。
     (原型 _research/meoz_adapter_verify.py 漏了这步; 真跑验证式:
      `vol×100×close == amount`, `auc_vol×100×m_price == auc_amt`, 5557 样本零越界)
  2. **缺口不得填 0**: warn_type / industry 猫爪没有 ⇒ 恒 None。
     (填 0 = 静默撒谎: 评分的异动兜底档与前端行业列会把"不知道"显示成"已知为 0")
  3. **竞价闸门与东财一致**: 窗口外不得读 auc_*(否则"换源"会静默改变名单语义);
     定格 map 永远优先(9:25 定格是竞价字段的权威来源)。
  4. **点查只认请求集**: 上游多回代码必须丢弃(否则名单被意外扩集)。
  5. **源顺序**(WP2 起): 非竞价模式补丁源猫爪在前、竞价窗口名单源猫爪在前;
     东财保留为次级(换源不换掉兜底)。WP0 期间"备而不用"(断言不得引用)的旧不变量已作废。
  6. **全市场行数闸门**(WP2 起): 行数不足一个数量级 ⇒ 视为不可用交下一级源。
     理由: 全市场源 `requested`=0 ⇒ coverage 恒 1.0, 半残数据会被静默当成有效名单源接管。
"""
import pytest

from app.services.picker import mode as pm
from app.services.picker.contract import QuoteRow
from app.services.picker.sources import base as sb
from app.services.picker.sources import meoz as MZ

# 猫爪 screening 行样例(字段名与真跑返回一致: symbol/close/vol 单位=手 ...)
MZ_ROW = {
    "tradedate": "20260924", "symbol": "000002", "name": "万科A",
    "open": 3.89, "high": 3.95, "low": 3.70, "close": 3.76, "pre_close": 3.92,
    "pct_chg": -4.08, "vol": 6672651.0, "amount": 2512837900.0,
    "turnover_rate_f": 0.61, "volume_ratio": 1.32,
    "free_float_mv": 4.51e10, "circ_mv": 4.52e10,
    "auc_pct_chg": -0.77, "auc_amt": 79314400.0, "auc_vol": 203893.0,
    "auc_turnover": 0.18, "theme_names_kpl": "房地产、物业管理",
}


def _ctx(mode_key, **over):
    policy = pm.POLICIES[mode_key]
    kw = dict(policy=policy, date="2026-09-24")
    kw.update(over)
    return sb.FetchContext(**kw)


def _patch_screening(monkeypatch, smap):
    """替换 screening_map, 并记录最后一次调用参数(供断言 symbols/date 传参)。"""
    calls = {}

    def fake(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return smap

    monkeypatch.setattr(MZ.meoz_client, "screening_map", fake)
    monkeypatch.setattr(MZ.meoz_client, "enabled", lambda: True)
    return calls


def _market_map(n, seed=None):
    """构造 n 行全市场 map(seed 先占位, 其余用唯一占位代码补齐)。

    全市场源有行数闸门(`MZ._MEOZ_MARKET_MIN_ROWS`), 所以"能当名单源用"的用例
    必须喂够行数 —— 否则测的就不是源本身而是闸门了。
    """
    m = dict(seed or {})
    i = 0
    while len(m) < n:
        c = "%06d" % (900000 + i)
        m[c] = {"symbol": c}
        i += 1
    return m


# ==================== 1. 字段映射与单位 ====================
def test_from_meoz_maps_all_basic_fields():
    q = QuoteRow.from_meoz(MZ_ROW)
    assert q.code == "000002" and q.name == "万科A"
    assert q.price == 3.76 and q.prev_close == 3.92 and q.open == 3.89
    assert q.real_change == -4.08
    assert q.turnover == 0.61 and q.vol_ratio == 1.32
    assert q.amount == 2512837900.0               # 元, 不换算
    assert q.float_mv == 4.52e10 and q.free_mv == 4.51e10
    assert q.auc_turnover == 0.18
    assert q.concept == "房地产、物业管理"          # theme_names_kpl → concept
    assert q.source == "meoz"


def test_from_meoz_vol_unit_is_hand_times_100():
    """★核心防复发: 猫爪 vol/auc_vol 单位=手, 契约单位=股 ⇒ 必须 ×100。"""
    q = QuoteRow.from_meoz(MZ_ROW, auction_window=True)
    assert q.vol == 6672651.0 * 100, "猫爪 vol 是手, 契约 vol 是股(真跑: vol×100×close==amount)"
    assert q.bid_vol == 203893.0 * 100, "猫爪 auc_vol 是手(真跑: auc_vol×100×m_price==auc_amt)"


def test_from_meoz_vol_identity_holds():
    """真跑验证式的可执行版本: vol×100×close ≈ amount(单位自洽性断言)。"""
    q = QuoteRow.from_meoz(MZ_ROW)
    assert abs(q.vol * q.price - q.amount) / q.amount < 0.02


def test_from_meoz_gaps_are_none_not_zero():
    """★缺口必须 None: warn_type(无 f630 等价)/ industry(无行业字段)。"""
    q = QuoteRow.from_meoz(MZ_ROW)
    assert q.warn_type is None, "猫爪无 f630 → None; 填 0 会让异动兜底档把'不知道'当'已知为 0'"
    assert q.industry is None
    assert q.bid_change is None, "窗口外不得读 auc_pct_chg"
    assert q.bid_amt is None and q.bid_vol is None


def test_from_meoz_empty_row_is_all_none():
    q = QuoteRow.from_meoz({"symbol": "999999"})
    assert q.code == "999999" and q.price is None and q.bid_amt is None
    assert q.vol is None and q.warn_type is None


# ==================== 2. 竞价闸门与定格权威 ====================
def test_from_meoz_auction_window_reads_auc_fields():
    q = QuoteRow.from_meoz(MZ_ROW, auction_window=True)
    assert q.bid_change == -0.77
    assert q.bid_amt == 79314400.0                # 猫爪竞价额已是元, 不换算
    assert q.bid_vol == 203893.0 * 100


def test_from_meoz_day_bid_map_overrides_realtime():
    """定格是竞价字段的唯一权威 → 优先于窗口内 auc_*。"""
    q = QuoteRow.from_meoz(MZ_ROW, auction_window=True,
                           day_bid_change=1.23, day_bid_amt_wan=4567.0,
                           day_bid_vol=8.8e5)
    assert q.bid_change == 1.23
    assert q.bid_amt == 4567.0 * 1e4, "定格额单位=万元, 契约内统一为元"
    assert q.bid_vol == 8.8e5


def test_from_meoz_period_auction_marks_row():
    """period_auction(时段事实)与 auction_window(源能力)可分开声明(供停牌判定)。"""
    q = QuoteRow.from_meoz(MZ_ROW, auction_window=True, period_auction=False)
    assert q.bid_change == -0.77 and q.auction_window is False


# ==================== 3. 点查源 ====================
def test_meoz_point_source_requires_codes():
    r = MZ.MeozRealtimeSource().run(_ctx(pm.PickMode.INTRADAY))
    assert not r.ok and "候选代码集" in (r.error or "")


def test_meoz_point_source_drops_unrequested_codes(monkeypatch):
    """★点查只认请求集: 上游多回代码必须丢弃(否则名单被意外扩集)。"""
    calls = _patch_screening(monkeypatch, {"000002": MZ_ROW,
                                           "600519": {"symbol": "600519", "name": "贵州茅台"}})
    r = MZ.MeozRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["000002"]))
    assert r.ok and set(r.rows) == {"000002"}, "只应保留请求集内的代码"
    assert calls["kwargs"].get("symbols") == ["000002"], "点查必须把 codes 传给上游"


def test_meoz_point_source_coverage_and_degraded_passthrough(monkeypatch):
    _patch_screening(monkeypatch, {"000002": MZ_ROW})
    r = MZ.MeozRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["000002", "000001"]))
    assert r.ok and r.coverage == 0.5 and r.requested == 2
    assert r.degraded is False, "猫爪是(规划中的)一级源, 不该无条件标降级"


def test_meoz_point_source_empty_is_error(monkeypatch):
    _patch_screening(monkeypatch, {})
    r = MZ.MeozRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["000002"]))
    assert not r.ok and r.rows == {}


def test_meoz_source_error_on_exception(monkeypatch):
    """★铁律2: 异常必须转成 error, 绝不向上抛(降级必须可见)。"""
    def boom(*a, **k):
        raise RuntimeError("猫爪 connection reset")
    monkeypatch.setattr(MZ.meoz_client, "screening_map", boom)
    monkeypatch.setattr(MZ.meoz_client, "enabled", lambda: True)
    r = MZ.MeozRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["000002"]))
    assert not r.ok and "RuntimeError" in (r.error or "") and r.rows == {}


def test_meoz_source_reports_disabled(monkeypatch):
    monkeypatch.setattr(MZ.meoz_client, "enabled", lambda: False)
    r = MZ.MeozRealtimeSource().run(_ctx(pm.PickMode.INTRADAY, codes=["000002"]))
    assert not r.ok and "未启用" in (r.error or "")


# ==================== 4. 全市场源 ====================
def test_meoz_market_source_ok_and_error(monkeypatch):
    calls = _patch_screening(monkeypatch, _market_map(1000, {"000002": MZ_ROW}))
    r = MZ.MeozMarketSource().run(_ctx(pm.PickMode.AUCTION, markets=["hs"]))
    assert r.ok and "000002" in r.rows
    assert r.rows["000002"].bid_change == -0.77, "竞价模式(auction_window)应读 auc_*"
    assert not calls["kwargs"].get("symbols"), "全市场源不得传 symbols"
    _patch_screening(monkeypatch, {})
    r2 = MZ.MeozMarketSource().run(_ctx(pm.PickMode.AUCTION, markets=["hs"]))
    assert not r2.ok and "返回空" in (r2.error or "")


def test_meoz_market_row_gate_blocks_halfbroken(monkeypatch):
    """★闸门: 行数少一个数量级 ⇒ 报错交下一级源, 绝不"换个源把名单缩水"。

    全市场源 `requested`=0 ⇒ coverage 恒 1.0, 而 `_fetch_list` 只看 ok(error 为空
    且 rows 非空) ⇒ 没有闸门时**半残结果会被静默当成有效名单源接管**。
    """
    _patch_screening(monkeypatch, {"000002": MZ_ROW})
    r = MZ.MeozMarketSource().run(_ctx(pm.PickMode.AUCTION, markets=["hs"]))
    assert not r.ok and "行数异常" in (r.error or "")
    assert r.degraded is True, "闸门拦截必须标降级(让 pipeline 知道是异常路径)"


def test_meoz_market_row_gate_boundary(monkeypatch):
    """边界: 恰好等于阈值放行, 少 1 只拦截(防阈值被写成 <= 或 > 的经典偏移)。"""
    n = MZ._MEOZ_MARKET_MIN_ROWS
    _patch_screening(monkeypatch, _market_map(n, {"000002": MZ_ROW}))
    assert MZ.MeozMarketSource().run(_ctx(pm.PickMode.AUCTION)).ok
    _patch_screening(monkeypatch, _market_map(n - 1, {"000002": MZ_ROW}))
    assert not MZ.MeozMarketSource().run(_ctx(pm.PickMode.AUCTION)).ok


def test_meoz_market_source_ignores_markets_param(monkeypatch):
    """猫爪 screening 无市场范围参数(永远全市场, 含北交所; 北交所由 filter 排除)。"""
    calls = _patch_screening(monkeypatch,
                             _market_map(1000, {"000002": MZ_ROW, "920001": {"symbol": "920001"}}))
    MZ.MeozMarketSource().run(_ctx(pm.PickMode.AUCTION, markets=["cyb"]))
    assert "markets" not in calls["kwargs"], "不得把 markets 硬塞成上游参数"


def test_meoz_market_outside_auction_window_has_no_bid(monkeypatch):
    """全市场源在非竞价模式(收盘回放)不得注入竞价值 —— 同东财全市场源约束。"""
    _patch_screening(monkeypatch, _market_map(1000, {"000002": MZ_ROW}))
    r = MZ.MeozMarketSource().run(_ctx(pm.PickMode.CLOSED, markets=["hs"]))
    assert r.ok and r.rows["000002"].bid_change is None


# ==================== 5. 注册表 / 备而不用不变量 ====================
def test_meoz_labels_registered():
    for label, cls in (("meoz_realtime", MZ.MeozRealtimeSource),
                       ("meoz_market", MZ.MeozMarketSource)):
        src = sb.get_source(label)
        assert src is not None and isinstance(src, cls) and src.label == label
        assert label in sb.REGISTRY


def test_policies_now_use_meoz():
    """★换源 WP2(2026-09-24) 已切换优先级 —— 猫爪是主源, 东财是次级。

    本用例由 WP0 期间的 `test_policies_still_do_not_use_meoz`(断言"不得引用")**反转**而来:
    WP0 的价值边界是"备而不用 ⇒ 零运行时行为变化"; WP2 起这条边界作废, 改为钉住新秩序:
      ① 4 个非竞价模式的**补丁源**必须猫爪在前、东财在后(东财保留为兜底, 不是删掉);
      ② 竞价窗口的**名单源**必须猫爪在前。
    ⚠️ 断言的是"顺序"不是"是否出现" —— 只断言出现, 东财被重新排到第一位也照样绿。
    """
    for m in (pm.PickMode.PREOPEN, pm.PickMode.LOCKED,
              pm.PickMode.INTRADAY, pm.PickMode.CLOSED):
        sp = pm.POLICIES[m].source_priority
        assert sp[0] == "snapshot", "%s 名单源必须是定格快照" % m.value
        assert sp.index("meoz_realtime") < sp.index("eastmoney_realtime"), \
            "%s 补丁源必须猫爪优先于东财" % m.value
    auction = pm.POLICIES[pm.PickMode.AUCTION].source_priority
    assert auction[0] == "meoz_market", "竞价窗口名单源必须猫爪优先"
    assert "eastmoney_market" in auction, "东财必须保留为次级(换源不换掉兜底)"
    assert auction.index("meoz_market") < auction.index("eastmoney_market") \
        < auction.index("tencent_market"), "三级名单源顺序: 猫爪→东财→腾讯"


def test_screening_map_passes_symbols_param(monkeypatch):
    """screening 点查参数拼装: 列表 → 逗号分隔; 不传则无 symbols 键(全市场)。"""
    from app.services import meoz_client as MC
    seen = []

    def fake_cached(apiname, params=None, **kw):
        seen.append((apiname, dict(params or {})))
        return {"data": {"fields": ["symbol", "name"], "items": [["000002", "万科A"]]}}

    monkeypatch.setattr(MC, "call_cached", fake_cached)
    MC.screening_map(symbols=["000002", "600519"])
    MC.screening_map()
    assert seen[0][0] == "screening"
    assert seen[0][1].get("symbols") == "000002,600519"
    assert "symbols" not in seen[1][1]


def test_screening_fields_include_open_and_vol():
    """换源 WP0 把 open/vol 纳入 screening 字段串(否则 meoz 源 open/vol 恒 None)。
    两者均在 openapi screening 字段表内(已核对 54 项), 传之安全。"""
    from app.services import meoz_client as MC
    f = MC._SCREENING_FIELDS
    assert "open" in f.split(",") and "vol" in f.split(",")
    # 反面守卫: 这两个字段**不在** openapi 清单内, 传了会让整个 screening 调用 422
    # → 猫爪主源全挂。任何"顺手加字段"都必须先核 openapi。
    assert "auc_vol_ratio" not in f.split(","), "screening openapi 无此字段, 传=主源 422 全挂"
    assert "main_net_amount" not in f.split(","), "同上"
