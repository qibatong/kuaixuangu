# -*- coding: utf-8 -*-
"""腾讯行情兜底源测试(2026-08-30 主人要求: 东财被墙时用其他数据源采集)
注意: 直接测 fetcher._fetch_market_with_fallback / fetch_tencent_market,
避开 conftest 对 ensure_cache 的 fake patch(否则测到的是假数据)。"""
import re

import pytest

from app.services import fetcher


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
    assert abs(r["f21"] - 16218.56 * 1e8) < 1  # 流通市值(亿→元)
    assert r["f615"] == 0.39           # 竞价涨幅≈现价涨幅
    assert r["f616"] == 208601 * 1e4   # 竞价金额≈成交额
    assert r["f617"] == 16126 * 100    # 竞价量≈成交量
    assert r["f630"] == 0


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
