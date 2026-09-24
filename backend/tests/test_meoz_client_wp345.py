# -*- coding: utf-8 -*-
"""meoz_client 换源 WP3/WP4/WP5 新封装测试(2026-09-24)。

每个用例钉住一个**实测踩过的坑** —— 坑不在"能不能调通", 而在解析/口径与上游真实
形态不符时**静默出错**(本仓铁律 2: 降级必须可见, 静默错最贵):

  1. `limit_pool` 池里混有 `'d'`(跌停); 不判 type 会让"昨涨停"名单混入跌停票。
  2. `daily` 返回**矩阵 + 每只多日** ⇒ 走 `_sym_rows()` 会被**折叠成最后一行**
     (静默丢数据, 曾因此误判"接口上限 20 只") ⇒ `daily_history_map` 必须返回
     `{symbol: [行, ...]}`, 不经 `_sym_rows`。
  3. `minute` 的 symbol **带交易所后缀**(`600519.SH`), 而 `daily` 是纯 6 位
     ⇒ 同一数据商两个接口 code 格式不统一, 出口/入口都必须归一。
  4. 三者 TTL 一律复用 `_AUC_SNAP_TTL`, 不得新造"第二个 30"。
"""
import pytest

from app.services import meoz_client as M


def _mat(rows):
    """构造猫爪矩阵响应 {"data": {"fields": [...], "items": [[...]]}}。"""
    keys = list(rows[0].keys())
    return {"code": 200,
            "data": {"fields": keys, "items": [[r[k] for k in keys] for r in rows]}}


def _spy(seen, payload):
    """返回一个记录调用参数的 call_cached 替身。"""
    def fake(apiname, params=None, **kw):
        seen.append((apiname, dict(params or {})))
        return payload
    return fake


# ==================== 1. limit_pool(换源 WP3) ====================
def test_limit_pool_map_parses_and_passes_tradedate(monkeypatch):
    seen = []
    row = {"symbol": "600519", "name": "贵州茅台", "tradedate": "20260923",
           "type": "u", "is_break": False, "limit_times": 2, "open_times": 0,
           "fd_amount": 1.2e8, "first_time": "09:25:00", "last_time": "09:25:00",
           "pct_chg": 10.0, "close": 100.0, "amount": 3e8}
    monkeypatch.setattr(M, "call_cached", _spy(seen, _mat([row])))
    m = M.limit_pool_map(date="2026-09-23")
    assert seen[0][0] == "limit_pool"
    assert seen[0][1] == {"tradedate": "20260923"}, \
        "必须用**显式 tradedate**(offset 只接受 <=0, 传 1/2 返 422)"
    assert m["600519"]["type"] == "u" and m["600519"]["limit_times"] == 2
    assert m["600519"]["fd_amount"] == pytest.approx(1.2e8)


def test_limit_pool_map_passes_type_only_when_given(monkeypatch):
    """不传参数 = 当日全池; 不得凭空塞 offset/type/date(上游会把未知参数当 422)。"""
    seen = []
    monkeypatch.setattr(M, "call_cached", _spy(seen, {"data": {"fields": [], "items": []}}))
    M.limit_pool_map()
    assert seen[0][1] == {}
    M.limit_pool_map(limit_type="u")
    assert seen[1][1] == {"type": "u"}


def test_limit_pool_map_non_trading_day_is_empty(monkeypatch):
    """非交易日返 code=1002 ⇒ call() 返 None ⇒ 空 dict(上层靠空结果推进, 不靠异常)。"""
    monkeypatch.setattr(M, "call_cached", lambda *a, **k: None)
    assert M.limit_pool_map(date="20260920") == {}


# ==================== 2. daily(换源 WP4/WP5) ====================
def test_daily_history_map_keeps_every_day_per_symbol(monkeypatch):
    """★核心防复发: 一票多日**不得折叠**成最后一行。

    折叠正是 `_sym_rows()` 的行为(按 symbol 建字典覆盖), 会静默丢数据 ——
    历史误判"daily 批量上限 20 只"就是它造成的假象。
    """
    rows = [
        {"symbol": "600519", "name": "贵州茅台", "tradedate": "20260924", "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 11.0, "pct_chg": 1.0, "vol": 1.0, "amount": 1.1e9},
        {"symbol": "600519", "name": "贵州茅台", "tradedate": "20260923", "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 10.0, "pct_chg": -1.0, "vol": 1.0, "amount": 1.0e9},
        {"symbol": "000001", "name": "平安银行", "tradedate": "20260924", "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 5.0, "pct_chg": 0.5, "vol": 1.0, "amount": 5e8},
    ]
    monkeypatch.setattr(M, "call_cached", lambda *a, **k: _mat(rows))
    out = M.daily_history_map(["600519", "000001"], days=3)
    assert len(out["600519"]) == 2, "同一票多日行必须全部保留(折叠 = 静默丢数据)"
    assert [r["tradedate"] for r in out["600519"]] == ["20260924", "20260923"], \
        "行序原样透传(调用方按需自行排序)"
    assert len(out["000001"]) == 1


def test_daily_history_map_params_recentdays_vs_tradedate(monkeypatch):
    """recentdays 与 tradedate **互斥**; 且入口剥后缀(上游 daily 只认纯 6 位)。"""
    seen = []
    monkeypatch.setattr(M, "call_cached", _spy(seen, {}))
    M.daily_history_map(["600519"], days=3)
    M.daily_history_map("600519.SH", date="2026-09-24")
    M.daily_history_map(["600519.SH", "000001.SZ"])
    assert seen[0][1] == {"symbols": "600519", "recentdays": 3}
    assert seen[1][1] == {"symbols": "600519", "tradedate": "20260924"}, \
        "传 date 时不得再带 recentdays(同传会被上游忽略, 语义含糊)"
    assert seen[2][1] == {"symbols": "600519,000001", "recentdays": 3}, \
        "入口必须剥掉交易所后缀"


def test_daily_history_map_empty_symbols_short_circuits(monkeypatch):
    def boom(*a, **k):                                   # 空入参不该打网络
        raise AssertionError("空 symbols 不得打到上游")
    monkeypatch.setattr(M, "call_cached", boom)
    assert M.daily_history_map([]) == {}
    assert M.daily_history_map("") == {}


# ==================== 3. minute(换源 WP5) ====================
def test_minute_rows_strips_suffix_sorts_and_passes_symbol(monkeypatch):
    """★两侧都归一: 入口把 '600519.SH' 洗成 '600519' 传给上游, 出口 key 也是纯 6 位。"""
    seen = []
    rows = [
        {"symbol": "600519.SH", "tradedate": "20260924", "trademin": "1000",
         "time": "10:00:30", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
         "vol": 10.0, "amount": 1500.0},
        {"symbol": "600519.SH", "tradedate": "20260924", "trademin": "0930",
         "time": "09:30:59", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.2,
         "vol": 5.0, "amount": 600.0},
    ]
    monkeypatch.setattr(M, "call_cached", _spy(seen, _mat(rows)))
    got = M.minute_rows("600519.SH")
    assert seen[0][0] == "minute" and seen[0][1]["symbols"] == "600519"
    assert [r["trademin"] for r in got] == ["0930", "1000"], \
        "必须时间升序(不依赖上游顺序); 注意全天无 1300 这根"


def test_minute_rows_drops_rows_without_trademin(monkeypatch):
    """trademin 缺失的行画不出时间轴 ⇒ 丢弃, 不得把空串塞进 time 数组。"""
    rows = [{"symbol": "600519.SH", "tradedate": "20260924", "trademin": "",
             "time": "", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
             "vol": 0.0, "amount": 0.0}]
    monkeypatch.setattr(M, "call_cached", lambda *a, **k: _mat(rows))
    assert M.minute_rows("600519") == []


def test_minute_rows_empty_symbol_short_circuits(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("空 symbol 不得打到上游")
    monkeypatch.setattr(M, "call_cached", boom)
    assert M.minute_rows("") == []


# ==================== 4. 出口归一 & TTL ====================
def test_strip_market_suffix():
    for src, want in (("600519.SH", "600519"), ("000001.SZ", "000001"),
                      ("920001.BJ", "920001"), ("600519", "600519"),
                      (" 600519 ", "600519"), (None, "")):
        assert M._strip_market_suffix(src) == want


def test_wp345_ttls_reuse_auc_snap_ttl():
    """★三者 TTL 必须复用 `_AUC_SNAP_TTL`, 不许新造"第二个 30"。

    本仓历史上「两处独立的 30」正是静默失效点(TTL 与轮询间隔的先后关系被隐式耦合),
    故换源新增接口时在这里显式钉死: 想改 TTL 就改那一个常量。
    """
    for api in ("limit_pool", "limit_pool_yes", "daily", "minute"):
        assert M.cache_ttl(api) == M._AUC_SNAP_TTL, api
