# -*- coding: utf-8 -*-
"""2026-10-09 第1批换源：`kpl.zt_pool_rows`（猫爪主 + 选股宝兜底）契约测试。

对拍依据（7 个真交易日 / 356 只样本）：涨停名单选股宝独有 = 0、连板数/涨幅/首封时间全等；
猫爪多北交所 ⇒ 默认过滤；猫爪无 reason/firstBreak ⇒ 恒为空/0。
"""
from app.services import kpl
from app.services import meoz_client as M


def _no_cache(monkeypatch):
    """旁路缓存：直接执行 loader（cache_store 在测试里是替身，别让它干扰断言）。"""
    monkeypatch.setattr(kpl, "_cached", lambda key, ttl, loader: loader())


def test_meoz_first_maps_fields(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, limit_type=None: {
        "600519": {"name": "贵州茅台", "type": "u", "pct_chg": 10.03, "limit_times": 2,
                   "open_times": 1, "first_time": "09:25:00"}})
    rows = kpl.zt_pool_rows("2026-10-08")
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "600519"
    assert r["name"] == "贵州茅台"
    assert r["limitUpDays"] == 2          # limit_times → limitUpDays
    assert r["breakTimes"] == 1           # open_times → breakTimes
    assert abs(r["change"] - 10.03) < 1e-9
    assert r["firstLimitUp"] > 0          # "09:25:00" → Unix 秒
    assert r["day"] == "2026-10-08"
    assert r["reason"] == "" and r["firstBreak"] == 0   # 猫爪无此两列


def test_symbol_suffix_stripped(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, limit_type=None: {
        "600519.SH": {"name": "x", "type": "u", "limit_times": 1}})
    assert kpl.zt_pool_rows("2026-10-08")[0]["code"] == "600519"


def test_bj_filtered_by_default(monkeypatch):
    """北交所默认过滤 —— 与选股宝名单逐只一致（零行为变化）。"""
    _no_cache(monkeypatch)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, limit_type=None: {
        "600519": {"type": "u", "limit_times": 1}, "920627": {"type": "u", "limit_times": 1}})
    assert [x["code"] for x in kpl.zt_pool_rows("2026-10-08")] == ["600519"]


def test_bj_kept_when_switch_on(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(kpl, "_MEOZ_POOL_KEEP_BJ", True)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, limit_type=None: {
        "600519": {"type": "u", "limit_times": 1}, "920627": {"type": "u", "limit_times": 1}})
    assert len(kpl.zt_pool_rows("2026-10-08")) == 2


def test_fallback_to_flash_when_meoz_empty(monkeypatch):
    """猫爪空/失败 ⇒ 回退选股宝（主备纪律）。"""
    _no_cache(monkeypatch)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, limit_type=None: {})
    monkeypatch.setattr(kpl, "_flash_pool_raw", lambda pn, d=None: [
        {"code": "600000", "name": "浦发银行", "change": 9.98, "limitUpDays": 1,
         "breakTimes": 0, "firstLimitUp": 0, "firstBreak": 0, "reason": "银行", "day": d}])
    rows = kpl.zt_pool_rows("2026-10-08")
    assert [x["code"] for x in rows] == ["600000"]
    assert rows[0]["reason"] == "银行"


def test_meoz_exception_falls_back(monkeypatch):
    _no_cache(monkeypatch)

    def boom(date=None, limit_type=None):
        raise RuntimeError("猫爪 502")
    monkeypatch.setattr(M, "limit_pool_map", boom)
    monkeypatch.setattr(kpl, "_flash_pool_raw", lambda pn, d=None: [
        {"code": "600000", "name": "x", "change": 9.98, "limitUpDays": 1, "breakTimes": 0,
         "firstLimitUp": 0, "firstBreak": 0, "reason": "", "day": d}])
    assert [x["code"] for x in kpl.zt_pool_rows("2026-10-08")] == ["600000"]


def test_real_limit_days_uses_meoz_rows(monkeypatch):
    """连板数（本批主收益点）: 猫爪行 → {code: 连板数}，且 ≥1 才收。"""
    _no_cache(monkeypatch)
    monkeypatch.setattr(M, "limit_pool_map", lambda date=None, limit_type=None: {
        "600519": {"type": "u", "limit_times": 3}, "000001": {"type": "u", "limit_times": 0}})
    assert kpl.real_limit_days("2026-10-08") == {"600519": 3}


def test_hhmmss_to_ts_roundtrip(monkeypatch):
    """'HH:MM:SS' → Unix 秒，且能被本地时区还原回同一时刻（口径对齐选股宝）。"""
    import time
    ts = kpl._hhmmss_to_ts("2026-10-08", "10:43:00")
    assert ts > 0
    assert time.strftime("%H:%M", time.localtime(ts)) == "10:43"
    assert kpl._hhmmss_to_ts("2026-10-08", "") == 0
    assert kpl._hhmmss_to_ts("2026-10-08", None) == 0
