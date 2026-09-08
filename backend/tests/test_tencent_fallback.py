# -*- coding: utf-8 -*-
"""腾讯行情兜底源测试(2026-08-30 主人要求: 东财被墙时用其他数据源采集)
注意: 直接测 fetcher._fetch_market_with_fallback / fetch_tencent_market,
避开 conftest 对 ensure_cache 的 fake patch(否则测到的是假数据)。"""
import logging
import re
import time

import pytest

from app.services import fetcher
from app.core import config

# conftest 的 session 级 mock_data_source 会 patch fetcher.ensure_cache,
# 但测试模块 import 发生在 fixture 执行前 → 此处保存的是真实实现, 供 TTL 测试还原
_ORIG_ENSURE_CACHE = fetcher.ensure_cache


def _use_real_ensure_cache(monkeypatch):
    monkeypatch.setattr(fetcher, "ensure_cache", _ORIG_ENSURE_CACHE)


def _make_tx_line(code, name, price, chg, vol_hand, amt_wan, turnover, mv_yi, limit):
    """构造腾讯行情一行(88 字段, 关键位置对齐): 1~名称~代码~现价~昨收~今开~..."""
    f = [""] * 88
    f[0] = "1"
    f[1], f[2], f[3], f[4], f[5] = name, code, price, price, price
    f[32] = str(chg)
    f[36] = str(vol_hand)
    f[37] = str(amt_wan)
    f[38] = str(turnover)
    f[44] = str(mv_yi)
    f[45] = str(mv_yi)
    f[47] = str(limit)
    return f'v_sh{code}="{"~".join(f)}"'


def _parse_tx(body):
    out = {}
    for line in body.split("\n"):
        m = re.search(r'v_([a-z]{2}\d{6})="(.*)"', line)
        if m:
            out[m.group(1)[2:]] = m.group(2).split("~")
    return out


# ---------- 腾讯代码 → 符号映射 ----------
def test_tencent_symbol_sh():
    assert fetcher._tencent_symbol("600519") == "sh600519"
    assert fetcher._tencent_symbol("900901") == "sh900901"


def test_tencent_symbol_sz():
    assert fetcher._tencent_symbol("000001") == "sz000001"
    assert fetcher._tencent_symbol("300750") == "sz300750"


def test_tencent_symbol_bj():
    assert fetcher._tencent_symbol("430047") == "bj430047"


# ---------- 腾讯响应解析 ----------
def test_fetch_tencent_batch_parse():
    """腾讯返回的 GBK 文本 → {code: fields} 解析正确(名称/现价位置)"""
    body = _make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53") + "\n" + \
           _make_tx_line("000001", "平安银行", "11.65", "0.52", "838126", "97322", "0.43", "2260.76", "12.75")
    parsed = _parse_tx(body)
    assert "600519" in parsed and "000001" in parsed
    assert parsed["600519"][1] == "贵州茅台"
    assert parsed["600519"][3] == "1297.40"


# ---------- 字段映射(腾讯 → 东财 diff 结构) ----------
def test_tencent_market_mapping(monkeypatch):
    """腾讯行情 → 东财 diff 格式字段映射正确(现价/涨跌/流通市值/竞价近似)"""
    body = _make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53")
    fields = _parse_tx(body)["600519"]
    # 直接注入代码清单(避免走快照库查询)
    fetcher._TENCENT_CODES_CACHE["codes"] = ["600519"]
    fetcher._TENCENT_CODES_CACHE["ts"] = 0
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519"])
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", lambda symbols: {"600519": fields})
    rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(rows) == 1
    r = rows[0]
    assert r["f12"] == "600519"
    assert r["f14"] == "贵州茅台"
    assert r["f2"] == 1297.40          # 现价
    assert r["f3"] == 0.39             # 涨跌%
    assert r["f8"] == 0.13             # 换手率
    assert r["f4"] == 1297.40          # 昨收(2026-09-01 修复: 缺 f4 被 is_suspended 误判停牌)
    assert r["f5"] == 16126            # 成交量(手)(2026-09-01 修复: 缺 f5 同样误判停牌)
    assert abs(r["f21"] - 16218.56 * 1e8) < 1  # 流通市值(亿→元)
    assert r["f615"] == 0.39           # 竞价涨幅≈现价涨幅
    assert r["f616"] == 208601 * 1e4   # 竞价金额≈成交额
    assert r["f617"] == 16126 * 100    # 竞价量≈成交量
    assert r["f630"] == 0
    assert r["f17"] == 1297.40         # 今开(2026-09-07 修复: 缺 f17 → 实体列全 0%)


# ---------- 2026-09-07 修复: 腾讯兜底缺 f17(今开) → 实体涨幅恒 0 ----------
def test_tencent_mapping_includes_f17_entity_change(monkeypatch):
    """生产问题回归(主人反馈「竞价选股实体列全是 0%」): 东财封禁期全市场走腾讯兜底,
    原映射缺 f17(今开) → scorer.get_entity_change(f2现价 - f17今开) 恒为 0。
    修复后 f17 = 腾讯 f[5](今开), 实体列有真实值(低开/高开时非 0)。"""
    from app.services import scorer
    body = _make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53")
    fields = _parse_tx(body)["600519"]
    fields[5] = "1280.00"    # 模拟低开(今开 1280 ≠ 现价 1297.40) → 实体应为 +1.36%
    fetcher._TENCENT_CODES_CACHE["codes"] = ["600519"]
    fetcher._TENCENT_CODES_CACHE["ts"] = 0
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519"])
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", lambda symbols: {"600519": fields})
    rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(rows) == 1
    r = rows[0]
    assert r["f17"] == 1280.00, "腾讯兜底必须带 f17(今开)"
    ec = scorer.get_entity_change(r)
    assert abs(ec - (1297.40 - 1280.00) / 1280.00 * 100) < 0.001, f"实体涨幅应≈+1.36%, 实际 {ec}"
    # 修复前回归: 若未来映射缺 f17(取不到今开), 实体退化为 0 → 测试立即失败
    assert scorer.get_entity_change({"f2": 1297.40}) == 0.0, "缺 f17 时实体应为 0(防误改回原状)"


# ---------- 2026-09-01 修复: 腾讯兜底缺 f4/f5 → is_suspended 误判停牌 → 自动锁定/system_batch 选股为 0 ----------
def test_tencent_mapping_not_suspended(monkeypatch):
    """生产事故回归: 东财熔断走腾讯兜底时, 映射行必须含 f4(昨收)/f5(成交量),
    否则 scorer.is_suspended(f4<=0 或 f5==0 判停牌) 把全部兜底数据误判停牌,
    导致 9:25 自动锁定(system_batch/auto_apply)结果为空(2026-09-01 生产机 batch 7052 = 0 只)。"""
    from app.services import scorer
    body = _make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53")
    fields = _parse_tx(body)["600519"]
    fetcher._TENCENT_CODES_CACHE["codes"] = ["600519"]
    fetcher._TENCENT_CODES_CACHE["ts"] = 0
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519"])
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", lambda symbols: {"600519": fields})
    rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(rows) == 1
    r = rows[0]
    assert r["f4"] > 0 and r["f5"] > 0, "腾讯兜底必须带 f4/f5, 否则被误判停牌"
    assert scorer.is_suspended(r) is False, "正常行情不得被判停牌"
    # 修复前回归: 若未来映射缺 f4/f5(或字段被改丢), 测试立即失败
    assert scorer.is_suspended({"f12": "600519", "f14": "茅台", "f2": 1297.40}) is True, \
        "缺 f4/f5 时必须被判停牌(证明 is_suspended 敏感性, 防误改)"


# ---------- 容灾: 东财失败自动切腾讯 ----------
def test_fallback_eastmoney_fail_uses_tencent(monkeypatch):
    """东财失败 → _fetch_market_with_fallback 自动切腾讯兜底"""
    def _boom(fs):
        raise RuntimeError("模拟东财被墙")
    monkeypatch.setattr(fetcher, "fetch_eastmoney", _boom)
    monkeypatch.setattr(fetcher, "fetch_tencent_market",
                        lambda fs: [{"f12": "600519", "f14": "茅台", "f2": 1.0}])
    rows = fetcher._fetch_market_with_fallback("m:1+t:2")
    assert len(rows) == 1
    assert rows[0]["f12"] == "600519"


def test_fallback_both_fail_raises(monkeypatch):
    """东财和腾讯都失败 → 抛异常"""
    def _boom(fs):
        raise RuntimeError("模拟东财被墙")
    def _boom2(fs):
        raise RuntimeError("模拟腾讯也挂")
    monkeypatch.setattr(fetcher, "fetch_eastmoney", _boom)
    monkeypatch.setattr(fetcher, "fetch_tencent_market", _boom2)
    with pytest.raises(RuntimeError):
        fetcher._fetch_market_with_fallback("m:1+t:2")


def test_fallback_eastmoney_ok_no_tencent(monkeypatch):
    """东财正常 → 不调腾讯"""
    called = []
    monkeypatch.setattr(fetcher, "fetch_eastmoney",
                        lambda fs: called.append("em") or [{"f12": "x"}])
    monkeypatch.setattr(fetcher, "fetch_tencent_market",
                        lambda fs: called.append("tx") or [])
    rows = fetcher._fetch_market_with_fallback("m:1+t:2")
    assert len(rows) == 1
    assert called == ["em"], "东财正常时不应调用腾讯"


# ---------- 2026-08-30 主人反馈补丁: 兜底覆盖所有全市场路径 ----------
def _to_diff(code, name, price, chg):
    """精简版东财 diff 行(测试用), 关键字段填写满足 scorer 入参"""
    mv_yi = 80.0
    return {
        "f2": str(price), "f3": str(chg), "f8": "1.0",
        "f10": "1.5", "f12": code, "f14": name,
        "f21": str(mv_yi * 1e8), "f20": str(mv_yi * 1.4 * 1e8),
        "f5": "100", "f6": "1000", "f18": str(price * 1.05),
        "f615": str(chg), "f616": "3000", "f617": "100000", "f630": "0",
        "f100": "0", "f117": "0", "f128": "0",
    }


def test_fetch_market_all_with_fallback(monkeypatch):
    """用户截图反馈(2026-08-30 中午盘中): 东财熔断异常会冒到前端。
    _fetch_market_all_with_fallback 必须自动切腾讯兜底而不冒错(覆盖 line 351/497/auction_snapshot _grab)"""
    fetcher._TENCENT_CODES_CACHE["codes"] = ["600519", "000001"]
    fetcher._TENCENT_CODES_CACHE["ts"] = 0
    monkeypatch.setattr(fetcher, "fetch_eastmoney_all",
                        lambda fs: (_ for _ in ()).throw(RuntimeError("东财数据源熔断中(故障冷却60秒内), 快速失败")))
    monkeypatch.setattr(fetcher, "fetch_tencent_market",
                        lambda fs: [_to_diff("600519", "贵州茅台", 1297.40, 0.39),
                                    _to_diff("000001", "平安银行", 11.65, 0.52)])
    rows = fetcher._fetch_market_all_with_fallback("m:1+t:2")
    assert len(rows) == 2
    assert rows[0]["f12"] == "600519"


def test_fetch_market_brief_uses_fallback(monkeypatch):
    """fetch_market_brief(原 line 351) 必须走兜底, 否则东财熔断时『两市概况』接口冒错"""
    monkeypatch.setattr(fetcher, "fetch_eastmoney_all",
                        lambda fs: (_ for _ in ()).throw(RuntimeError("熔断中")))
    monkeypatch.setattr(fetcher, "fetch_tencent_market",
                        lambda fs: [_to_diff("600000", "浦发银行", 10.0, 0.5)])
    fetcher._market_brief_cache["data"] = None
    fetcher._market_brief_cache["ts"] = 0
    data = fetcher.fetch_market_brief(max_age=0)
    assert data is not None
    assert data["stockCount"] >= 1


def test_fetch_spot_quote_map_uses_fallback(monkeypatch):
    """fetch_spot_quote_map(原 line 497) 必须走兜底, 否则盘中新版前端拿不到行情 map"""
    monkeypatch.setattr(fetcher, "fetch_eastmoney_all",
                        lambda fs: (_ for _ in ()).throw(RuntimeError("熔断中")))
    monkeypatch.setattr(fetcher, "fetch_tencent_market",
                        lambda fs: [_to_diff("300750", "宁德时代", 200.0, 1.2)])
    fetcher._quote_map_cache.clear()
    raw = fetcher.fetch_spot_quote_map("m:1+t:2")
    assert raw  # 至少 1 只
    assert "300750" in raw


# ---------- 2026-08-30 可观测性: 熔断短路日志 + 健康快照 ----------
def test_yesterday_short_circuit_logs(caplog, monkeypatch):
    """三源全熔断短路 → 单只函数快速返回 None 且不逐只打日志
    (2026-08-31 修复: 原逐只 WARNING 造成 36804 条日志风暴拖死 worker, 改为批级短路聚合日志)"""
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src: True)
    import logging
    with caplog.at_level(logging.WARNING):
        r = fetcher._fetch_yesterday_amount_one("600519")
    # 2026-09-08: 返回值扩为 (成交额对, 昨日涨跌幅%) → 短路时 (None, None)
    assert r == (None, None)
    # 新行为: 单只短路不打日志(批量短路日志在 fetch_yesterday_amounts 层聚合)
    assert not any("熔断短路" in rec.message for rec in caplog.records), "单只短路不应逐只打日志"


def test_health_serviceable_true_when_tencent_up(monkeypatch):
    """东财 clist down 但腾讯可用 → serviceable=True(服务仍可响应)"""
    fake_health = {
        "eastmoney_clist": {"ok": 10, "fail": 5, "last_ok": 0, "last_fail": 9999999999,
                            "ms_sum": 0, "ms_cnt": 0, "down_since": 9999999999},
        "eastmoney_kline": {"ok": 10, "fail": 0, "last_ok": 9999999999, "last_fail": 0,
                            "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
        "eastmoney_zt_pool": {"ok": 10, "fail": 0, "last_ok": 9999999999, "last_fail": 0,
                              "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
        "ths_kline": {"ok": 10, "fail": 0, "last_ok": 9999999999, "last_fail": 0,
                      "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
        "tencent_market": {"ok": 10, "fail": 0, "last_ok": 9999999999, "last_fail": 0,
                           "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    }
    monkeypatch.setattr(fetcher, "_HEALTH", fake_health)
    st = fetcher.get_health_status()
    assert st["serviceable"] is True
    assert st["overall"] == "degraded"


def test_health_serviceable_false_when_all_down(monkeypatch):
    """东财+腾讯都 down → serviceable=False(服务不可用, 必须告警)"""
    fake_health = {
        "eastmoney_clist": {"ok": 10, "fail": 5, "last_ok": 0, "last_fail": 9999999999,
                            "ms_sum": 0, "ms_cnt": 0, "down_since": 9999999999},
        "eastmoney_kline": {"ok": 10, "fail": 0, "last_ok": 0, "last_fail": 9999999999,
                            "ms_sum": 0, "ms_cnt": 0, "down_since": 9999999999},
        "eastmoney_zt_pool": {"ok": 10, "fail": 0, "last_ok": 0, "last_fail": 9999999999,
                              "ms_sum": 0, "ms_cnt": 0, "down_since": 9999999999},
        "ths_kline": {"ok": 10, "fail": 0, "last_ok": 0, "last_fail": 9999999999,
                      "ms_sum": 0, "ms_cnt": 0, "down_since": 9999999999},
        "tencent_market": {"ok": 10, "fail": 5, "last_ok": 0, "last_fail": 9999999999,
                           "ms_sum": 0, "ms_cnt": 0, "down_since": 9999999999},
    }
    monkeypatch.setattr(fetcher, "_HEALTH", fake_health)
    st = fetcher.get_health_status()
    assert st["serviceable"] is False
    assert st["overall"] == "down"


# ---------- 2026-09-01 腾讯兜底批失败重试 + 缺票告警 ----------
def _reset_codes_cache():
    fetcher._TENCENT_CODES_CACHE["codes"] = []
    fetcher._TENCENT_CODES_CACHE["ts"] = 0


def test_tencent_batch_retry_then_success(monkeypatch):
    """单批首次失败 → 重试 1 次成功 → 正常返回, 不误报缺票"""
    _reset_codes_cache()
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src: False)
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519"])
    calls = {"n": 0}

    def _flaky(symbols):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("首次失败(网络抖动)")
        body = _make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53")
        return _parse_tx(body)

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _flaky)
    rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(rows) == 1
    assert calls["n"] == 2, "失败后必须重试 1 次"


def test_tencent_fail_after_retry_alerts(caplog, monkeypatch):
    """单批两次都失败 → 不再静默: 记录失败批并打缺票 error 告警"""
    _reset_codes_cache()
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src: False)
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519", "000001"])

    def _boom(symbols):
        raise RuntimeError("腾讯接口挂了")

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _boom)
    with caplog.at_level(logging.ERROR):
        rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert rows == []
    assert any("缺票告警" in r.message for r in caplog.records), "严重缺票必须 error 告警"


def test_tencent_partial_missing_alerts(caplog, monkeypatch):
    """代码清单 3 只只返回 2 只(无失败批) → 比例超阈值触发缺票告警"""
    _reset_codes_cache()
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src: False)
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519", "000001", "300750"])

    def _partial(symbols):
        out = {}
        out.update(_parse_tx(_make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53")))
        out.update(_parse_tx(_make_tx_line("000001", "平安银行", "11.65", "0.52", "838126", "97322", "0.43", "2260.76", "12.75")))
        return out   # 缺 300750

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _partial)
    with caplog.at_level(logging.ERROR):
        rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(rows) == 2
    assert any("缺票告警" in r.message for r in caplog.records), "缺 1/3 属于严重缺票, 必须告警"


def test_tencent_complete_no_alert(caplog, monkeypatch):
    """完整返回无缺票 → 不产生缺票告警"""
    _reset_codes_cache()
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src: False)
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: ["600519"])

    def _full(symbols):
        return _parse_tx(_make_tx_line("600519", "贵州茅台", "1297.40", "0.39", "16126", "208601", "0.13", "16218.56", "1421.53"))

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _full)
    with caplog.at_level(logging.ERROR):
        rows = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(rows) == 1
    assert not any("缺票" in r.message for r in caplog.records), "完整返回不应告警"


# ---------- 2026-09-01 filter 缓存 TTL(修复: 原无 TTL, 一次坏缓存污染整个下午) ----------
def test_ensure_cache_filter_expired_refreshes(monkeypatch):
    """filter 缓存超过 CACHE_TTL → 自动重新拉取(不再永远命中坏缓存)"""
    _use_real_ensure_cache(monkeypatch)
    calls = []
    # 2026-09-07 fd27ebe 候选池=全市场: ensure_cache 三 action 改用
    # _fetch_market_all_with_fallback(原 _fetch_market_with_fallback 仅快照模块用)
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback",
                        lambda fs: calls.append(fs) or [{"f12": "600519"}])
    key = "m:1+t:2"
    fetcher._cache[key] = {"raw": [{"f12": "old"}], "ts": time.time() - config.CACHE_TTL - 1}
    raw, err = fetcher.ensure_cache("filter", key, True)
    assert calls, "过期缓存必须重新拉取"
    assert raw[0]["f12"] == "600519"


def test_ensure_cache_filter_fresh_hits(monkeypatch):
    """filter 缓存新鲜(< CACHE_TTL) → 命中不重拉"""
    _use_real_ensure_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback",
                        lambda fs: calls.append(fs) or [])
    key = "m:1+t:2"
    fetcher._cache[key] = {"raw": [{"f12": "old"}], "ts": time.time()}
    raw, err = fetcher.ensure_cache("filter", key, True)
    assert not calls, "新鲜缓存不应重拉"
    assert raw[0]["f12"] == "old"
