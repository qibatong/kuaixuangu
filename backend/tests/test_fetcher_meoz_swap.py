# -*- coding: utf-8 -*-
"""换源 WP3/WP4/WP5 在 fetcher 侧的防复发测试(2026-09-24)。

钉住的是**换源最容易静默改语义的三处**:

  1. WP3 昨涨停池: 猫爪池里混有跌停 `'d'` ⇒ 只认 `type=='u'`; 且返回值有**三分语义**
     (`None`=猫爪不可用 → 今天该日改问东财 / `set()`=该日已查但无涨停 → 继续往前找 /
     非空 set=命中) —— 把 `None` 和 `set()` 混为一谈会让"往前找最近交易日"要么停太早、
     要么把跌停票当涨停票。
  2. WP4 昨日成交额/涨跌幅: 猫爪 daily 的**跳过今日**语义必须与东财 K 线路径逐条对齐
     (盘中跳过未收盘那根; 收盘后不跳 —— 否则"昨日涨幅"整体滞后一天, 这是修过的 bug)。
  3. WP5 图表: 源链**任一源成功即短路**(不得再多打两次网络); 周/月K 猫爪没有,
     必须回 `{}` 交给原链条。
     🔴 2026-09-30 首源由**猫爪改回腾讯**(顺序: 腾讯 → 猫爪 → 东财): 原"猫爪首源"
     让逐只 chart 调用(每票 1 次猫爪 `a=daily`)在竞价时段把猫爪打成 345 次 429 ——
     见 fetcher.fetch_stock_chart_robust 的调整注明与 kpl.fill_close_change_from_kline
     的批量修复注释。**三个源一个没删**, 猫爪降为第一备源仍在链上, 东财保持末位兜底。
"""
import time

import pytest

from app.services import fetcher as F
from app.services import meoz_client as M


@pytest.fixture(autouse=True)
def _fresh_zt_cache(monkeypatch):
    """每个用例独立缓存 —— 否则模块级 _ZT_CACHE 会让用例互相污染(顺序相关 flaky)。"""
    monkeypatch.setattr(F, "_ZT_CACHE", {"codes": None, "ts": 0})
    monkeypatch.setattr(F, "_ZT_FAIL", {"ts": 0})


@pytest.fixture
def real_get_zt(monkeypatch):
    """取回被 conftest session 桩掉的**真实实现**(别名见 conftest 的说明)。

    不这样做就会测到桩: conftest 把 `fetcher.get_yesterday_zt_codes` 整体换成
    `lambda: None`, 于是本文件对主源/备源顺序的断言全部失效(且**仍然是绿的**)。
    """
    real = F._real_get_yesterday_zt_codes
    monkeypatch.setattr(F, "get_yesterday_zt_codes", real)
    return real


# ==================== 1. WP3 昨涨停池 ====================
def test_meoz_zt_codes_filters_down_limit(monkeypatch):
    """★猫爪涨停池含 'd'(跌停) ⇒ 不排掉会让"昨涨停"名单混入跌停票。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, **k: {
        "600519": {"type": "u"}, "000001": {"type": "d"}, "600000": {"type": "u"}})
    assert F._meoz_zt_codes_date("20260923") == {"600519", "600000"}


def test_meoz_zt_codes_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(F, "_meoz_enabled", lambda: False)
    assert F._meoz_zt_codes_date("20260923") is None, "None 的语义 = 猫爪不可用 ⇒ 该日回退东财"


def test_meoz_zt_codes_only_down_limit_is_empty_set(monkeypatch):
    """全是 'd' ⇒ 返回 `set()`(该日已查、无涨停) 而**不是** None(不可用) —— 三分语义。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, **k: {"000001": {"type": "d"}})
    got = F._meoz_zt_codes_date("20260923")
    assert got == set() and got is not None


def test_meoz_zt_codes_empty_response_is_none(monkeypatch):
    """空响应(非交易日 code=1002 / 接口故障) ⇒ None, 交给东财判这一天。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, **k: {})
    assert F._meoz_zt_codes_date("20260920") is None


def test_meoz_zt_codes_exception_is_none(monkeypatch):
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)

    def boom(date=None, **k):
        raise RuntimeError("猫爪 502")
    monkeypatch.setattr(M, "limit_pool_map", boom)
    assert F._meoz_zt_codes_date("20260923") is None, "异常不得冒泡(降级必须可见且可继续)"


def test_get_yesterday_zt_codes_prefers_meoz_and_skips_em(monkeypatch, real_get_zt):
    calls = []
    monkeypatch.setattr(F, "_meoz_zt_codes_date",
                        lambda ds: (calls.append(("meoz", ds)), {"600519"})[1])
    monkeypatch.setattr(F, "_em_zt_codes_date",
                        lambda ds: (calls.append(("em", ds)), {"000001"})[1])
    assert real_get_zt() == {"600519"}
    assert [c[0] for c in calls] == ["meoz"], "猫爪命中时不得再打东财(白打一次网络)"


def test_get_yesterday_zt_codes_falls_back_to_em_same_day(monkeypatch, real_get_zt):
    calls = []
    monkeypatch.setattr(F, "_meoz_zt_codes_date",
                        lambda ds: (calls.append(("meoz", ds)), None)[1])
    monkeypatch.setattr(F, "_em_zt_codes_date",
                        lambda ds: (calls.append(("em", ds)), {"000001"})[1])
    assert real_get_zt() == {"000001"}
    assert [c[0] for c in calls] == ["meoz", "em"], "同一日内在猫爪失败后回退东财"
    assert calls[0][1] == calls[1][1], "回退必须发生在**同一天**上(不是整轮放弃)"


def test_get_yesterday_zt_codes_skips_empty_days_forward(monkeypatch, real_get_zt):
    """空池(周末) ⇒ 继续往前找; 15 日窗口内命中即止(长假容错原样保留)。"""
    probed = []
    hit = {"20260919"}

    def meoz(ds):
        probed.append(ds)
        return hit if ds in hit else set()
    monkeypatch.setattr(F, "_meoz_zt_codes_date", meoz)
    monkeypatch.setattr(F, "_em_zt_codes_date", lambda ds: set())
    assert real_get_zt() == hit
    assert len(probed) >= 2, "首个空池日不得直接放弃"


def test_fetch_zt_pool_meoz_field_mapping(monkeypatch):
    """字段映射(与东财同构): fd_amount 元→亿 / first_time→HHMMSS / limit_times→lb 等。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, **k: {
        "600519": {"type": "u", "fd_amount": 1.2e8, "first_time": "09:25:00",
                   "limit_times": 2, "open_times": 1, "pct_chg": 10.0},
        "000001": {"type": "d", "fd_amount": 5e7, "first_time": "10:00:00",
                   "limit_times": 1, "open_times": 0, "pct_chg": -10.0}})
    out = F._fetch_zt_pool_meoz("20260923")
    assert set(out) == {"600519"}, "只取 type=='u'"
    assert out["600519"]["fund"] == pytest.approx(1.2)
    assert out["600519"]["fb"] == 92500, "first_time '09:25:00' → HHMMSS 整数(与东财 fbt 同口径)"
    assert out["600519"]["lb"] == 2 and out["600519"]["zbc"] == 1


def test_fetch_zt_pool_meoz_first_then_em(monkeypatch):
    monkeypatch.setattr(F, "_zt_cache", {})
    monkeypatch.setattr(F, "_fetch_zt_pool_meoz", lambda d: {"600519": {"fund": 1.0}})
    monkeypatch.setattr(F, "_fetch_zt_pool_em",
                        lambda d: (_ for _ in ()).throw(AssertionError("不应调用备源")))
    assert set(F.fetch_zt_pool("20260923")) == {"600519"}


def test_fetch_zt_pool_falls_back_to_em(monkeypatch):
    monkeypatch.setattr(F, "_zt_cache", {})
    monkeypatch.setattr(F, "_fetch_zt_pool_meoz", lambda d: {})
    monkeypatch.setattr(F, "_fetch_zt_pool_em", lambda d: {"000001": {"fund": 2.0}})
    assert set(F.fetch_zt_pool("20260923")) == {"000001"}


def test_fetch_zt_pool_all_sources_fail_returns_empty_without_cache(monkeypatch):
    """全源失败不写缓存(与原实现一致: 下次立即重试, 不被空结果锁 5 分钟)。"""
    monkeypatch.setattr(F, "_zt_cache", {})
    monkeypatch.setattr(F, "_fetch_zt_pool_meoz", lambda d: {})
    monkeypatch.setattr(F, "_fetch_zt_pool_em", lambda d: {})
    assert F.fetch_zt_pool("20260923") == {}
    assert F._zt_cache == {}


# ==================== 2. WP4 昨日成交额 / 涨跌幅 ====================
def _d(date, close, amt, chg=None):
    return {"symbol": "600519", "tradedate": date, "close": close,
            "amount": amt, "pct_chg": chg}


def test_yday_pair_skips_today_intraday():
    """★盘中必须跳过今日那根(未收盘, 额不完整) —— 与 _kline_amount_pair 同语义。"""
    rows = [_d("20260924", 11.0, 1.1e8), _d("20260923", 10.0, 1.0e8),
            _d("20260922", 9.0, 9.0e7)]
    pair, chg = F._yday_pair_from_daily(rows, today="20260924", after_close=False)
    assert pair == [10000.0, 9000.0], "amount 单位=元 ⇒ /1e4 得万元(与东财口径一致)"
    assert chg == pytest.approx(11.11, abs=0.01), "涨幅同为 T-1 vs T-2"


def test_yday_pair_keeps_today_after_close(monkeypatch):
    """★收盘后**不得**再跳今日 —— 否则"昨日涨幅"整整滞后一天(2026-09-08 修过的 bug)。

    🔴 2026-09-28 修用例的**日期依赖缺陷**(与本日 spot 改动无关, 独立暴露):
    用例写死 `today="20260924"`, 但 after_close 分支会拿 `_yday_expected_tdate()`
    (解析"**真实**当前应取交易日")逐行比对 —— 一旦日历走过 09-24, 该函数返回
    20260928 ≠ 20260924 ⇒ 恒返回 (None, None), 用例**必然假红**(实测 09-28 复现:
    不打桩 pair=None, 打桩成 20260924 立刻 pair=[11000.0,10000.0] chg=10.0)。
    修法: 把"预期交易日"一并打桩成用例写死的日期 —— 被测的是**口径**(收盘后不跳今日),
    不是"今天是几号"; 沿用真实时钟只会制造随时间腐烂的假红。
    另一条同类用例 test_yday_pair_skips_today_intraday 走 after_close=False,
    不进该分支, 故无此问题。
    """
    monkeypatch.setattr(F, "_yday_expected_tdate", lambda: "20260924")
    rows = [_d("20260924", 11.0, 1.1e8), _d("20260923", 10.0, 1.0e8),
            _d("20260922", 9.0, 9.0e7)]
    pair, chg = F._yday_pair_from_daily(rows, today="20260924", after_close=True)
    assert pair == [11000.0, 10000.0]
    assert chg == pytest.approx(10.0)


def test_yday_pair_prefers_official_pct_chg():
    rows = [_d("20260923", 10.0, 1.0e8, chg=9.99), _d("20260922", 9.0, 9.0e7)]
    _, chg = F._yday_pair_from_daily(rows, today="20260924", after_close=False)
    assert chg == pytest.approx(9.99), "官方 pct_chg 优先, 收盘价环比只是兜底"


def test_yday_pair_sorts_unsorted_input():
    """上游实测最新在前; 函数自行升序排, 不依赖上游顺序(顺序变了口径就错)。"""
    rows = [_d("20260922", 9.0, 9.0e7), _d("20260923", 10.0, 1.0e8)]
    pair, _ = F._yday_pair_from_daily(rows, today="20260924", after_close=False)
    assert pair == [10000.0, 9000.0]


def test_yday_pair_single_row_has_no_t1():
    pair, _ = F._yday_pair_from_daily([_d("20260923", 10.0, 1.0e8)],
                                      today="20260924", after_close=False)
    assert pair == [10000.0, None], "只有一根K线时 T-1 必须是 None(不是 0)"


def test_yday_pair_drops_bad_dates_and_zero_amount():
    rows = [_d("", 1.0, 1.0e8), _d("bad", 1.0, 1.0e8), _d("20260923", 10.0, 0.0)]
    assert F._yday_pair_from_daily(rows, today="20260924", after_close=False) == (None, None)


def test_yday_pair_empty_is_none_pair():
    """空输入必须回 (None, None) —— 回 (0, 0) 会被下游当"已知为 0 元"落库。"""
    assert F._yday_pair_from_daily([], today="20260924",
                                   after_close=False) == (None, None)
    assert F._yday_pair_from_daily(None, today="20260924",
                                   after_close=False) == (None, None)


# ==================== 3. WP5 图表源链 ====================
MINUTE_OK = {"period": "minute", "code": "600000", "name": "浦发银行",
             "time": ["09:30"], "price": [10.0], "avg": [10.0],
             "volume": [1.0], "preClose": 9.9}


def test_chart_robust_first_source_short_circuits(monkeypatch):
    """★首源成功必须短路: 否则每次画图都白打两次网络(东财在测试机是时段性风控)。

    🔴 2026-09-30: 首源由猫爪改为**腾讯**(见 fetcher 内的调整注明), 故本轮把第 1 源
    用腾讯打桩。钉住的**不变量没变** —— 首源成功 ⇒ 不得再打任何备源。
    """
    calls = []
    monkeypatch.setattr(F, "_fetch_chart_from_meoz",
                        lambda c, p: (calls.append("meoz"), {})[1])
    monkeypatch.setattr(F, "fetch_stock_chart",
                        lambda c, p="day": (calls.append("em"), {})[1])
    monkeypatch.setattr(F, "_fetch_chart_from_tencent",
                        lambda c, p="day": (calls.append("tx"), dict(MINUTE_OK))[1])
    monkeypatch.setattr(F, "_validate_chart_data", lambda d, p, source=None: True)
    monkeypatch.setattr(F, "_ensure_latest_period", lambda d, c: d)
    monkeypatch.setattr(F, "_CHART_CACHE", {})
    out = F.fetch_stock_chart_robust("600000", "minute")
    assert calls == ["tx"], "首源(腾讯)成功后不得再打备源, 实际 %s" % (calls,)
    assert out["code"] == "600000" and out["preClose"] == 9.9


def test_chart_robust_falls_back_to_meoz_then_eastmoney(monkeypatch):
    """🔴 2026-09-30 首源调整后的链路口径：**腾讯(首源) → 猫爪(第一备源) → 东财(末位兜底)**。

    两个断言各钉一件事：
      ① 备源(猫爪)成功时**不得再打东财**（性能纪律：东财 push2his 命中率约 5%、生产机 IP 被墙）；
      ② 猫爪也失败时才落到东财（保留它的**官方涨跌幅**与**更全覆盖** —— 见 fetcher 里的口径说明：
         主人口径是"影响逻辑计算就可以用东财"）。

    沿革: 本用例原名 `..._falls_back_to_tencent_then_eastmoney`。2026-09-24 WP5 曾把猫爪
    提为首源; 2026-09-30 因竞价时段 345 次 429 又调回腾讯首源(见 fetcher 内注明)。
    链上**三个源一个没删**, 只是顺序变了 ⇒ 断言改为钉新顺序, 守卫的不变量原样保留。
    """
    calls = []
    monkeypatch.setattr(F, "_fetch_chart_from_meoz",
                        lambda c, p: (calls.append("meoz"), dict(MINUTE_OK))[1])
    monkeypatch.setattr(F, "fetch_stock_chart",
                        lambda c, p="day": (calls.append("em"), dict(MINUTE_OK))[1])
    monkeypatch.setattr(F, "_fetch_chart_from_tencent",
                        lambda c, p="day": (calls.append("tx"), {})[1])
    monkeypatch.setattr(F, "_validate_chart_data", lambda d, p, source=None: True)
    monkeypatch.setattr(F, "_ensure_latest_period", lambda d, c: d)
    monkeypatch.setattr(F, "_CHART_CACHE", {})

    # ① 腾讯失败、猫爪可用 ⇒ ["tx","meoz"]，东财一次都没被调
    assert F.fetch_stock_chart_robust("600000", "minute")["code"] == "600000"
    assert calls == ["tx", "meoz"], "备源(猫爪)可用时不得再打东财, 实际 %s" % (calls,)

    # ② 猫爪也失败 ⇒ 落东财末位兜底（先清缓存，否则会命中上一次的结果）
    F._CHART_CACHE.clear()
    calls.clear()
    monkeypatch.setattr(F, "_fetch_chart_from_meoz",
                        lambda c, p: (calls.append("meoz"), {})[1])
    assert F.fetch_stock_chart_robust("600000", "minute")["code"] == "600000"
    assert calls == ["tx", "meoz", "em"], "猫爪空 ⇒ 东财末位兜底, 实际 %s" % (calls,)


def test_chart_robust_all_sources_fail_returns_empty(monkeypatch):
    monkeypatch.setattr(F, "_fetch_chart_from_meoz", lambda c, p: {})
    monkeypatch.setattr(F, "fetch_stock_chart", lambda c, p="day": {})
    monkeypatch.setattr(F, "_fetch_chart_from_tencent", lambda c, p="day": {})
    monkeypatch.setattr(F, "_CHART_CACHE", {})
    assert F.fetch_stock_chart_robust("600000", "minute") == {}


def test_fetch_chart_from_meoz_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(F, "_meoz_enabled", lambda: False)
    assert F._fetch_chart_from_meoz("600519", "minute") == {}
    assert F._fetch_chart_from_meoz("600519", "day") == {}


def test_fetch_chart_from_meoz_has_no_week_month(monkeypatch):
    """周K/月K 猫爪没有 ⇒ 必须回 {} 交给原链条(不能返回半成品骗过校验)。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    assert F._fetch_chart_from_meoz("600519", "week") == {}
    assert F._fetch_chart_from_meoz("600519", "month") == {}


def test_fetch_chart_from_meoz_minute_avg_uses_share_count(monkeypatch):
    """★均价 = amount / (vol × 100): 猫爪 vol 单位是**手**, 漏乘 100 均价会差 100 倍。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "minute_rows", lambda code, **k: [
        {"trademin": "0930", "time": "09:30:59", "close": 10.0,
         "vol": 100.0, "amount": 100000.0}])
    monkeypatch.setattr(F, "_meoz_pre_close", lambda code: (9.8, "某股"))
    monkeypatch.setattr(F, "_trim_minute_to_now", lambda r: r)
    d = F._fetch_chart_from_meoz("600519", "minute")
    assert d["time"] == ["09:30"] and d["price"] == [10.0]
    assert d["avg"] == [round(100000.0 / (100.0 * 100.0), 3)]
    assert d["preClose"] == 9.8 and d["name"] == "某股"


def test_fetch_chart_from_meoz_day_builds_preclose_from_last_pct(monkeypatch):
    """日K 的 preClose 由最后一根 pct_chg 反推(猫爪 daily 无独立 preClose 字段)。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", lambda codes, **k: {"600519": [
        {"symbol": "600519", "name": "贵州茅台", "tradedate": "20260924", "open": 11.0,
         "high": 11.0, "low": 11.0, "close": 11.0, "pct_chg": 10.0,
         "vol": 1.0, "amount": 1.1e8}]})
    d = F._fetch_chart_from_meoz("600519", "day")
    assert d["period"] == "day" and d["close"] == [11.0]
    assert d["preClose"] == pytest.approx(10.0), "11.0 / (1 + 10%) = 10.0"


def test_meoz_day_k_requests_full_legacy_depth(monkeypatch):
    """★日K 根数必须 ≥ 旧链**实际供数**深度(腾讯 count=200), 否则换首源 = 静默缩水。

    旧链名义首源东财(`fetch_stock_chart`)只拉 120 根, 但东财 chart 存在接口级时段性
    风控(常 502) ⇒ 线上长期实际由腾讯备源供数 200 根。v4.11.47 换首源时写死
    `days=120`, 用户看到的日K 就从 ~200 根缩到 ~120 根(约 10 个月 → 约 6 个月) ——
    这是**用户可见的静默退化**, 不是内部实现细节。

    同时断言「常量」与「真实调用参数」: 只改常量不改调用点 / 只改调用点不吃常量,
    两种半吊子改法都必须在 CI 变红。
    """
    seen = {}

    def _fake(codes, **kw):
        seen.update(kw)
        return {codes[0]: [
            {"symbol": codes[0], "name": "某股", "tradedate": "20260924", "open": 1.0,
             "high": 1.0, "low": 1.0, "close": 1.0, "pct_chg": 0.0,
             "vol": 1.0, "amount": 1.0}]}

    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", _fake)
    F._fetch_chart_from_meoz("600519", "day")
    assert F._MEOZ_DAY_K_BARS >= 200, (
        "日K 深度不得低于旧链实际供数(腾讯 count=200): 120 会让用户可见的K线缩水 40%")
    assert seen.get("days") == F._MEOZ_DAY_K_BARS, (
        "调用点必须使用 _MEOZ_DAY_K_BARS, 不得另写字面量(改常量不生效 = 假修复)")


# ==================== 4. 2026-09-30 批量日K(batch_close_chg_map, 猫爪429修复) ====================
# 背景: kpl.fill_close_change_from_kline 原对"缺票"逐只调 fetch_stock_chart_robust
#   ⇒ 每票 1 次猫爪 `a=daily`; 生产实测「连续多日封单」471 次请求 → 854 次逐只日K
#   → 345 次 429(96.5% 挤在 08:45-09:30)。批量口把它压成 800 只/次。
# 本组用例钉的是: ① 口径与逐只一致(前复权相邻收盘); ② 拿不到就**回 {}** 而不是
#   猜/补 0(否则会把"未知"写成"0%", 比不打请求更糟); ③ 老日期不做批量。

def _bj_today():
    """北京时间今天(与被测函数内同一口径) —— 用例不得写死日期, 否则随时间腐烂成假红。"""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def _ymd(date_str):
    """YYYY-MM-DD → YYYYMMDD(上游真实返回的是这个格式)。"""
    return str(date_str).replace("-", "")


def _row(date_str, close):
    return {"tradedate": _ymd(date_str), "close": close}


def test_batch_close_chg_prev_close_semantics(monkeypatch):
    """★口径 = 逐只: 目标日收盘 vs **上一交易日**收盘(前复权环比), 保留 2 位。

    上游真实顺序是"最新在前", 本用例按该顺序给, 顺带证明函数不依赖上游顺序。
    """
    today = _bj_today()
    prev = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - 86400))
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", lambda codes, **k: {
        "600519": [_row(today, 11.0), _row(prev, 10.0)]})
    assert F.batch_close_chg_map(["600519"], today) == {"600519": 10.0}


def test_batch_close_chg_sorts_unsorted_input(monkeypatch):
    """上游给的若是升序也不得算反 —— 算反会把 +10% 变成 -9.09%(静默错值)。"""
    today = _bj_today()
    prev = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - 86400))
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", lambda codes, **k: {
        "600519": [_row(prev, 10.0), _row(today, 11.0)]})
    assert F.batch_close_chg_map(["600519"], today) == {"600519": 10.0}


def test_batch_close_chg_no_prev_bar_yields_nothing(monkeypatch):
    """只有目标日那根(没有上一交易日) ⇒ 回 {} 交回逐只链, **不得**产出 0。"""
    today = _bj_today()
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", lambda codes, **k: {
        "600519": [_row(today, 11.0)]})
    assert F.batch_close_chg_map(["600519"], today) == {}


def test_batch_close_chg_missing_target_day_yields_nothing(monkeypatch):
    """目标日不在返回序列里(非交易日/上游缺该日) ⇒ 回 {}, 不猜。"""
    today = _bj_today()
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", lambda codes, **k: {
        "600519": [_row("2020-01-02", 11.0), _row("2020-01-03", 12.0)]})
    assert F.batch_close_chg_map(["600519"], today) == {}


def test_batch_close_chg_skips_old_lookback(monkeypatch):
    """超出 _BATCH_CLOSE_CHG_DAYS_MAX 个自然日的历史回看**不得发批量请求**
    (那时 recentdays 会拉到几十根/票, 批量反而更重) ⇒ 回 {}, 交回逐只链。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map",
                        lambda codes, **k: pytest.fail("老日期不得发起批量请求"))
    assert F.batch_close_chg_map(["600519"], "2020-01-02") == {}


def test_batch_close_chg_disabled_returns_empty(monkeypatch):
    """猫爪不可用 ⇒ 回 {}(逐只链接着兜底), 不得抛异常冒泡到接口。"""
    monkeypatch.setattr(F, "_meoz_enabled", lambda: False)
    assert F.batch_close_chg_map(["600519"], _bj_today()) == {}


def test_batch_close_chg_handles_upstream_exception(monkeypatch):
    """上游异常 ⇒ 该片跳过、整体回 {} —— 逐只兜底必须仍然接得住。"""
    def boom(codes, **k):
        raise RuntimeError("猫爪 429")
    monkeypatch.setattr(F, "_meoz_enabled", lambda: True)
    monkeypatch.setattr(M, "daily_history_map", boom)
    assert F.batch_close_chg_map(["600519"], _bj_today()) == {}
