# -*- coding: utf-8 -*-
"""
板块成分股「昨日名单 + 今日实时行情」覆盖用例 (2026-09-07 主人方案)
==================================================================
背景: 用户反馈「市场雷达-连板强度-成分股」数据显示上交易日。诊断结论: 开盘啦盘中
      **不提供当日成分股**(实时 18 参数组合全 1020 + Date=今天 1020, 仅历史日可用)。
主人方案: 成分名单保留(历史接口=昨日成分, 盘中板块成分几乎不变), 但每只票的行情
      (价/涨跌/换手/量比/成交额/流通/主力)用**实时接口**覆盖 → 观感即盘中实时。

实现:
  ① fetcher.fetch_spot_details_by_codes: 从全市场行情缓存 ent['raw'](东财 diff 行/
     腾讯映射行)按 code 提取富字段; 腾讯行无 f6 → 回落 f616; f62 主力仅东财有。
  ② kpl.fetch_board_stocks: date 空时不再试必败的 after 实时, 直接历史接口
     (Date=今天 优先, 未冻结回退上一交易日), 解析后按 code 实时行情覆盖。
"""
import time

import pytest

from app.services import fetcher, kpl
from app.core import config


@pytest.fixture(autouse=True)
def _clean_quote_cache():
    """隔离 quote map 缓存(测试自建 raw)"""
    with fetcher._quote_map_lock:
        saved = dict(fetcher._quote_map_cache)
        fetcher._quote_map_cache.clear()
    yield
    with fetcher._quote_map_lock:
        fetcher._quote_map_cache.clear()
        fetcher._quote_map_cache.update(saved)


def _mk_eastmoney_row(code, price=10.0, chg=3.5, amt=1.2e9, mv=5e10, main=1.5e7):
    """东财 diff 格式行(f6 成交额 / f62 主力净额 均有)"""
    return {"f2": price, "f3": chg, "f8": 4.2, "f10": 1.8, "f12": code,
            "f14": "股" + code, "f21": mv, "f6": amt, "f62": main}


def _mk_tencent_row(code, price=9.0, chg=-1.2):
    """腾讯映射行(无 f6/f62; f616≈成交额)"""
    return {"f2": price, "f3": chg, "f8": 2.1, "f12": code,
            "f14": "T" + code, "f21": 3e10, "f616": 8.8e8}


# ---------- ① fetcher 富行情按需取 ----------

def test_spot_details_from_eastmoney_raw():
    """东财 raw: f6/f62 完整提取"""
    with fetcher._quote_map_lock:
        fetcher._quote_map_cache["m:0+t:6"] = {
            "raw": [_mk_eastmoney_row("600000")], "map": {}, "ts": time.time()}
    d = fetcher.fetch_spot_details_by_codes(["600000", "999999"])
    assert set(d) == {"600000"}
    q = d["600000"]
    assert q["price"] == 10.0 and q["change"] == 3.5
    assert q["amount"] == 1.2e9 and q["floatMv"] == 5e10
    assert q["mainNet"] == 1.5e7, "东财行应有主力净额"
    assert q["turnover"] == 4.2 and q["volRatio"] == 1.8


def test_spot_details_tencent_fallback_no_f6_f62():
    """腾讯兜底行: 无 f6 → 回落 f616; 无 f62 → mainNet=None(前端显示 -)"""
    with fetcher._quote_map_lock:
        fetcher._quote_map_cache["m:0+t:6"] = {
            "raw": [_mk_tencent_row("600000")], "map": {}, "ts": time.time()}
    d = fetcher.fetch_spot_details_by_codes(["600000"])
    q = d["600000"]
    assert q["amount"] == 8.8e8, "腾讯行应回落 f616"
    assert q["mainNet"] is None, "腾讯无主力字段 → None 而非 0(前端显示 -, 防误判为净流入0)"


def test_spot_details_empty_and_cold():
    """空列表 → {}; 冷启动(无缓存)兜底拉一次全市场建 raw"""
    assert fetcher.fetch_spot_details_by_codes([]) == {}
    calls = {"n": 0}
    monkeypatch = pytest.MonkeyPatch()

    def fake_map(fs):
        calls["n"] += 1
        # 让 fetch_spot_quote_map 建缓存: 它调 _fetch_market_all_with_fallback, 这里直接放行
        return fetcher._build_quote_map([_mk_eastmoney_row("600000")])

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", lambda fs: [_mk_eastmoney_row("600000")])
    d = fetcher.fetch_spot_details_by_codes(["600000"])
    monkeypatch.undo()
    assert "600000" in d


# ---------- ② kpl.fetch_board_stocks 实时覆盖 ----------

def _mk_kpl_row(code, price=9.9, chg=1.5):
    """开盘啦 w41 成分股行(38+ 列), 对齐 fetch_board_stocks 解析字段位置"""
    row = [""] * 40
    row[0], row[1] = code, "股" + code
    row[4] = "概念X"
    row[5] = str(price)      # 昨日价
    row[6] = str(chg)        # 昨日涨幅
    row[7] = "5e8"
    row[10] = "4e10"
    row[11] = "2e6"
    row[21] = "1.5"
    row[23] = "首板"
    row[25] = "3.3"
    row[38] = "9e10"
    return row


def test_board_stocks_overrides_quotes_realtime(monkeypatch):
    """P0(主人反馈场景): date 空时不再调 after 实时(必 1020); 直接历史接口,
    解析出的每只票 price/change/turnover/amount/mainNet 被实时行情覆盖"""
    # mock _call: 只响应 his(历史), after 若被调用则 fail(证明已删除该分支)
    called = {"keys": []}

    def fake_call(host_key, params):
        called["keys"].append((host_key, params.get("apiv"), params.get("Date")))
        assert host_key == "his", f"不应再调 after 实时(after 必 1020), 实际 {host_key}"
        return {"errcode": "0", "list": [_mk_kpl_row("600000")]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    # mock 实时行情: 返回与历史不同的值 → 断言覆盖生效
    monkeypatch.setattr(fetcher, "fetch_spot_details_by_codes", lambda codes: {
        "600000": {"price": 12.34, "change": 5.67, "turnover": 6.6, "volRatio": 2.2,
                   "amount": 9e8, "floatMv": 6e10, "mainNet": -3e6}})

    out = kpl.fetch_board_stocks("801001")
    assert len(out) == 1
    it = out[0]
    assert it["code"] == "600000"
    assert it["price"] == 12.34, "price 应被实时覆盖"
    assert it["change"] == 5.67, "change 应被实时覆盖"
    assert it["turnover"] == 6.6
    assert it["amount"] == 9e8
    assert it["mainNet"] == -3e6, "mainNet 实时覆盖(负值也要能覆盖)"
    assert it["concept"] == "概念X" and it["limitTag"] == "首板", "结构性字段保留历史值"
    assert all(k[0] == "his" for k in called["keys"]), "只走历史接口, after 分支已删除"


def test_board_stocks_fallback_yesterday_when_today_not_frozen(monkeypatch):
    """Date=今天 1020/空(盘中未冻结) → 回退上一交易日名单, 行情仍实时覆盖"""
    calls = {"n": 0}

    def fake_call(host_key, params):
        calls["n"] += 1
        if params.get("Date") == time.strftime("%Y-%m-%d"):
            return {"errcode": 1020, "errmsg": "参数出错"}   # 今天未冻结
        return {"errcode": "0", "list": [_mk_kpl_row("600000", price=8.8, chg=-2.0)]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    monkeypatch.setattr(fetcher, "fetch_spot_details_by_codes", lambda codes: {
        "600000": {"price": 11.1, "change": 4.4, "turnover": 5.0, "volRatio": 1.0,
                   "amount": 1e9, "floatMv": 5e10, "mainNet": 1e6}})

    out = kpl.fetch_board_stocks("801001")
    assert calls["n"] == 2, "今天被拒后应回退昨日(2 次 his 调用)"
    assert out and out[0]["price"] == 11.1, "回退昨日的名单也要被实时行情覆盖"


# ---------- ③ 主人补充: 成分股 = 今日实时涨幅 top30 ----------

def test_board_stocks_sorted_by_today_change_top30(monkeypatch):
    """P0(主人 9/7 补充): 实时请求拉全量成分(st=500), 行情覆盖后按**今日 change
    降序**截取 st=30 —— 而非开盘啦昨日排行(昨日跌幅榜的票今天可能排最前)"""
    rows = []
    # 构造 35 只(超过 30, 验证截断): 昨日排序使 code0 在最前(昨日强), 今日它最弱
    for i in range(35):
        r = _mk_kpl_row("60%04d" % i, price=10 + i * 0.1, chg=i - 20)
        rows.append(r)

    seen_st = {"v": None}

    def fake_call(host_key, params):
        seen_st["v"] = params.get("st")
        if params.get("Date") == time.strftime("%Y-%m-%d"):
            return {"errcode": 1020}
        return {"errcode": "0", "list": rows}

    monkeypatch.setattr(kpl, "_call", fake_call)
    # 实时行情: code_i 的今日涨幅 = i(单调), 与昨日排序无关
    monkeypatch.setattr(fetcher, "fetch_spot_details_by_codes", lambda codes: {
        c: {"price": 12.0, "change": float(int(c[2:])), "turnover": 5.0, "volRatio": 1.0,
            "amount": 1e9, "floatMv": 5e10, "mainNet": 1e6} for c in codes})

    out = kpl.fetch_board_stocks("801001")
    assert seen_st["v"] == "500", "实时请求应拉全量成分(st=500), 而非昨日 top30 再排序"
    assert len(out) == 30, "截断到 30"
    assert out[0]["code"] == "600034", "今日涨幅最大的应排第一(60 0034→change=34)"
    assert out[-1]["code"] == "600005", "第 30 名应是 change=5(35 只里最小前 30)"
    changes = [x["change"] for x in out]
    assert changes == sorted(changes, reverse=True), "应按今日 change 严格降序"


def test_board_stocks_history_keeps_original_order(monkeypatch):
    """date 指定(历史回看) → 不做实时 top 排序(保持开盘啦当日排行)"""
    calls = {"n": 0}

    def fake_call(host_key, params):
        calls["n"] += 1
        assert params.get("Date") == "2026-09-04", "历史回看必须带指定 Date"
        return {"errcode": "0", "list": [_mk_kpl_row("600000", chg=1.0),
                                        _mk_kpl_row("600001", chg=9.0)]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    monkeypatch.setattr(fetcher, "fetch_spot_details_by_codes", lambda codes: {})
    out = kpl.fetch_board_stocks("801001", date="2026-09-04")
    assert calls["n"] == 1
    assert [x["code"] for x in out] == ["600000", "600001"], "历史回看保持开盘啦原排序"
