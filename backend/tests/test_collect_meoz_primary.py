# -*- coding: utf-8 -*-
"""WP1 采集主源换猫爪(2026-09-24「去东财换猫爪」)测试

本文件里的 WP0/WP1/WP2 均指《快选-去东财换猫爪-施工图》的工作包,
与 v4.11.42 的 WP1b/WP2a(契约注册表/补采)无关。

防复发断言(每条对应一个具体坑):
  1. **主源顺序**: full=True 时猫爪 screening 先建行, 东财只补缺 —— 且"谁先"只决定
     两者都有值时谁的赢, **不改变覆盖度**(东财独有的票必须仍被补入, 否则名单变瘦)。
  2. **东财独有的字段必须补位**(warn_type=f630): 判据用 falsy 而非 `is None`,
     否则换源后该列会全变 0 —— "换源静默改了落库内容"的隐形回归。
  3. **秒级采样(full=False)源不变**: 猫爪 screening 是 30s 缓存的整市场拉取,
     塞进 18 秒窗口的逐秒采样只会采到同一份数据(序列退化成直线)。
  4. **防串日**: 猫爪 screening 的 offset 查询在目标日未产出时返回**上一交易日**那份
     (同 daily_auc 的坑); 必须显式传 tradedate + 按 tradedate 校验。
  5. **单点可回退**: `_MEOZ_PRIMARY` 常量存在且被 `_fetch_market_map` 真正读取。
"""
import pytest

from app.services import auction_snapshot as A
from app.services import fetcher, tickplus
from app.services import meoz_client as MC

TODAY = A._bj_date().replace("-", "")

# 猫爪 screening 行(tradedate=当日, 否则会被防串日过滤掉)
MZ_YESTERDAY = {"tradedate": "19990101", "symbol": "600354", "name": "敦煌种业",
                "close": 10.5, "pre_close": 10.15, "open": 10.2, "pct_chg": 3.45,
                "auc_pct_chg": 3.45, "auc_amt": 5.0e7, "auc_vol": 4.8e5,
                "free_float_mv": 5.6e9, "circ_mv": 5.7e9}


def _mz_row(code="600354", **over):
    d = dict(MZ_YESTERDAY)
    d["tradedate"] = TODAY
    d["symbol"] = code
    d.update(over)
    return d


# 东财 diff 行(字段名与 fetcher 返回一致)
EM_ROW = {"f12": "600354", "f14": "敦煌种业", "f2": 10.5, "f3": 3.45, "f4": 10.15,
          "f18": 10.15, "f5": 12345.0, "f6": 1.29e8, "f8": 2.5, "f10": 1.8,
          "f17": 10.2, "f21": 5.5e9, "f117": 5.4e9, "f100": "农牧", "f103": "农业",
          "f615": 3.45, "f616": 5.0e7, "f617": 4.8e6, "f630": 2}


def _patch_all(monkeypatch, *, sc_map, em_raw, em_full=None, tp_map=None):
    """把 _fetch_market_map 的全部上游换成夹具(真实 _merge_meoz / _merge_em_rows 照跑)。"""
    monkeypatch.setattr(MC, "enabled", lambda: True)
    monkeypatch.setattr(MC, "screening_map", lambda **kw: dict(sc_map))
    monkeypatch.setattr(MC, "valuation_map", lambda **kw: {})
    monkeypatch.setattr(MC, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(MC, "auc_fd_map", lambda *a, **k: {})
    monkeypatch.setattr(MC, "fundflow_map", lambda *a, **k: {})
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback",
                        lambda fs: [dict(r) for r in (em_full if em_full is not None else em_raw)])
    monkeypatch.setattr(fetcher, "_fetch_market_with_fallback",
                        lambda fs: [dict(r) for r in em_raw])
    monkeypatch.setattr(tickplus, "snapshot_map", lambda: dict(tp_map or {}))
    monkeypatch.setattr(A, "_fetch_kpl_fallback", lambda: {})


# ==================== 1. 主源顺序 ====================
def test_meoz_primary_flag_is_read_by_fetch(monkeypatch):
    """★单点可回退: 常量存在且被 _fetch_market_map 真正读取(不能只是文档摆设)。"""
    import inspect
    assert A._MEOZ_PRIMARY is True, "换源后默认猫爪优先"
    src = inspect.getsource(A._fetch_market_map)
    assert "_MEOZ_PRIMARY" in src, "开关必须在函数体里被读取, 否则改它没有任何效果"


def test_fetch_prefers_meoz_values_for_shared_fields(monkeypatch):
    """★主源语义: 两者都有值时**猫爪的赢**(市值 / 名称)。"""
    _patch_all(monkeypatch, sc_map={"600354": _mz_row()}, em_raw=[EM_ROW])
    raw = A._fetch_market_map(full=True)
    v = raw["600354"]
    assert v["float_mv"] == 5.7e9, "猫爪 circ_mv 应赢过东财 f21(主源优先)"
    assert v["free_mv"] == 5.6e9, "猫爪 free_float_mv 应赢过东财 f117"
    assert v["name"] == "敦煌种业"


def test_fetch_em_only_codes_still_merged(monkeypatch):
    """★换主源不得让名单变瘦: 猫爪没有的票必须由东财补入(只补缺 ≠ 只认猫爪)。"""
    em2 = dict(EM_ROW, f12="000002", f14="万科A")
    _patch_all(monkeypatch, sc_map={"600354": _mz_row()}, em_raw=[EM_ROW, em2])
    raw = A._fetch_market_map(full=True)
    assert set(raw) == {"600354", "000002"}, "东财独有的票必须补入"


def test_fetch_fills_eastmoney_only_field_warn_type(monkeypatch):
    """★东财独有字段必须补位: warn_type(f630)。判据用 falsy ⇒ 猫爪写的 0 不阻挡东财的 2。"""
    _patch_all(monkeypatch, sc_map={"600354": _mz_row()}, em_raw=[EM_ROW])
    raw = A._fetch_market_map(full=True)
    assert raw["600354"].get("warn_type") == 2, \
        "猫爪无 f630, 但东财有 ⇒ 必须补; 否则换源后该列静默全 0(隐形回归)"


def test_fetch_secondary_fills_only_missing(monkeypatch):
    """只补缺: 猫爪已给的非 0 值不得被东财覆盖(口径不同, 覆盖=串数)。"""
    _patch_all(monkeypatch, sc_map={"600354": _mz_row(auc_amt=9.9e7)},
               em_raw=[EM_ROW])
    raw = A._fetch_market_map(full=True)
    assert raw["600354"]["bid_amt"] == pytest.approx(9.9e7 / 1e4), "猫爪竞价额已存在 → 不被东财覆盖"
    assert raw["600354"]["bid_change"] == 3.45


def test_fetch_legacy_order_when_flag_off(monkeypatch):
    """回退路径: 关掉开关 ⇒ 回到"东财主源 + 猫爪只补缺"的旧顺序(市值取东财)。"""
    monkeypatch.setattr(A, "_MEOZ_PRIMARY", False)
    _patch_all(monkeypatch, sc_map={"600354": _mz_row()}, em_raw=[EM_ROW])
    raw = A._fetch_market_map(full=True)
    assert raw["600354"]["float_mv"] == 5.5e9, "旧顺序下东财 f21 应赢(回退有效)"


def test_fetch_second_level_sampling_still_uses_eastmoney(monkeypatch):
    """★秒级采样(full=False)源不变: 不调猫爪(30s 缓存的整市场拉取不适合逐秒采样)。"""
    called = {"n": 0}

    def spy(**kw):
        called["n"] += 1
        return {}
    monkeypatch.setattr(MC, "enabled", lambda: True)
    monkeypatch.setattr(MC, "screening_map", spy)
    monkeypatch.setattr(fetcher, "_fetch_market_with_fallback", lambda fs: [dict(EM_ROW)])
    raw = A._fetch_market_map(full=False)
    assert raw and "600354" in raw
    assert called["n"] == 0, "full=False 不得触发猫爪全市场拉取"


def test_fetch_falls_back_to_kpl_when_both_fail(monkeypatch):
    """主源与第二级全失败 → 兜底链(开盘啦)仍要接住。"""
    _patch_all(monkeypatch, sc_map={}, em_raw=[])
    monkeypatch.setattr(A, "_fetch_kpl_fallback",
                        lambda: {"600354": {"bid_change": 1.0, "bid_amt": 1.0, "name": "x",
                                            "bid_buy_amt": 0, "float_mv": 0, "free_mv": 0,
                                            "board": "", "warn_type": 0}})
    raw = A._fetch_market_map(full=True)
    assert "600354" in raw


# ==================== 2. _merge_em_rows 只补缺 ====================
def test_merge_em_rows_never_overwrites_nonzero():
    raw = {"600354": {"bid_change": 3.45, "bid_amt": 100.0, "name": "A", "bid_buy_amt": 5.0,
                      "float_mv": 1e9, "free_mv": 1e9, "board": "X", "warn_type": 1}}
    em = {"600354": {"bid_change": 9.9, "bid_amt": 999.0, "name": "B", "bid_buy_amt": 7.0,
                     "float_mv": 2e9, "free_mv": 2e9, "board": "Y", "warn_type": 2}}
    st = A._merge_em_rows(raw, em)
    v = raw["600354"]
    assert (v["bid_change"], v["bid_amt"], v["name"], v["bid_buy_amt"],
            v["float_mv"], v["free_mv"], v["board"], v["warn_type"]) == \
        (3.45, 100.0, "A", 5.0, 1e9, 1e9, "X", 1), "已有非 0 值一律不动"
    assert st == {"added": 0, "filled": 0}


def test_merge_em_rows_adds_new_codes_and_fills_gaps():
    raw = {"600354": {"bid_change": 0, "bid_amt": 0, "name": "", "bid_buy_amt": 0,
                      "float_mv": 0, "free_mv": 0, "board": "", "warn_type": 0}}
    em = {"600354": {"bid_change": 3.45, "bid_amt": 5000.0, "name": "敦煌种业",
                     "bid_buy_amt": 1.0, "float_mv": 5.5e9, "free_mv": 5.4e9,
                     "board": "农业", "warn_type": 2},
          "000002": {"bid_change": 1.0, "bid_amt": 2.0, "name": "万科A",
                     "bid_buy_amt": 0, "float_mv": 0, "free_mv": 0, "board": "", "warn_type": 0}}
    st = A._merge_em_rows(raw, em)
    assert st["added"] == 1 and st["filled"] == 8
    assert raw["600354"]["float_mv"] == 5.5e9 and raw["600354"]["warn_type"] == 2
    assert raw["000002"]["name"] == "万科A"


# ==================== 3. _screening_today 防串日 ====================
def test_screening_today_prefers_explicit_date(monkeypatch):
    """① 显式 tradedate 命中 → 直接用, 且**不再**走 offset 查询。"""
    calls = []

    def fake(**kw):
        calls.append(kw)
        return {"600354": _mz_row()} if "date" in kw else {"XXX": _mz_row("XXX")}
    monkeypatch.setattr(MC, "screening_map", fake)
    m = A._screening_today(TODAY)
    assert set(m) == {"600354"}
    assert len(calls) == 1 and calls[0]["date"] == TODAY


def test_screening_today_discards_cross_day_rows(monkeypatch):
    """★防串日核心: 显式查询空 + offset 返回**上一交易日** → 全丢弃, 返回 {}。"""
    def fake(**kw):
        if "date" in kw:
            return {}
        return {"600354": dict(MZ_YESTERDAY), "000002": dict(MZ_YESTERDAY, symbol="000002")}
    monkeypatch.setattr(MC, "screening_map", fake)
    assert A._screening_today(TODAY) == {}, "串日残值必须丢弃(不得写进定格)"


def test_screening_today_accepts_offset_rows_of_today(monkeypatch):
    """② 显式查询空 + offset 返回**当日** → 严格校验通过, 可用(兼容上游口径差异)。"""
    def fake(**kw):
        if "date" in kw:
            return {}
        return {"600354": _mz_row()}
    monkeypatch.setattr(MC, "screening_map", fake)
    assert set(A._screening_today(TODAY)) == {"600354"}


def test_screening_today_blank_date_returns_empty():
    """无目标日 ⇒ 不猜、不取(宁可本枪无猫爪, 也不要串日)。"""
    assert A._screening_today("") == {}


def test_merge_meoz_uses_screening_today():
    """源码级守卫: _merge_meoz 必须走防串日入口, 不得退回裸 screening_map。"""
    import inspect
    src = inspect.getsource(A._merge_meoz)
    assert "_screening_today(" in src
    assert "screening_map(date_offset=0)" not in src, "禁止绕过防串日入口"


def test_short_screening_response_does_not_shrink_result(monkeypatch):
    """★刻意不设"行数下限"阈值: 猫爪返回得少只是"补得少", 有东财第二级兜着 ⇒ 名单不变瘦。
    (若哪天有人手滑加了行数阈值把整源弃用, 本用例会红 —— 热路径阈值是误判源。)"""
    few = {"600519": _mz_row("600519", name="贵州茅台")}
    _patch_all(monkeypatch, sc_map=few, em_raw=[EM_ROW])
    raw = A._fetch_market_map(full=True)
    assert len(raw) == 2, "东财独有的票仍必须补入 —— 猫爪行数少不构成整源弃用的理由"


def test_merge_meoz_never_uses_cross_day_rows(monkeypatch):
    """端到端防串日: 上游只给上一交易日的整市场 ⇒ 一只都不能进结果。"""
    stale = {"%06d" % i: dict(MZ_YESTERDAY, symbol="%06d" % i) for i in range(1000)}

    def fake(**kw):
        return dict(stale)          # 无论传 date 还是 offset, 都只给昨日
    monkeypatch.setattr(MC, "enabled", lambda: True)
    monkeypatch.setattr(MC, "screening_map", fake)
    monkeypatch.setattr(MC, "valuation_map", lambda **kw: {})
    monkeypatch.setattr(MC, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(MC, "auc_fd_map", lambda *a, **k: {})
    monkeypatch.setattr(MC, "fundflow_map", lambda *a, **k: {})
    raw = {}
    st = A._merge_meoz(raw)
    assert raw == {} and st["added"] == 0 and st["val_n"] == 0
