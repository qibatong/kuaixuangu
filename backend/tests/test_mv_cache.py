# -*- coding: utf-8 -*-
"""P2-2 流通市值日频缓存回归(2026-09-12)

为什么要这张表: 市值/名称是**静态基础数据**, 但 9/11 熔断日东财一挂, 全市场市值
一起消失; TickPlus 补进来的 5000+ 只票 float_mv=0 → 被市值门槛当小盘股全误杀,
补了等于白补。本模块做三级兜底: 当日行情源 → 缓存(≤15天) → 腾讯 f44。

锁定八条性质:
  ① save/lookup 往返一致(单位: 元)
  ② 缓存回退有**窗口**(≤15天): 太旧的市值可能因股本变动失真, 必须拒绝
  ③ 每个 code 取**最近一日**(不是任一日)
  ④ fill ①已有值不动(东财最准, 不被缓存/腾讯覆盖)
  ⑤ fill ②缓存补缺
  ⑥ fill ③腾讯 f44 补缺, 且 **f44 单位是亿 → ×1e8 得元**(单位错 = 放大 1e8 倍)
  ⑦ 腾讯补值时顺带补 name
  ⑧ 全程不发真实网络请求(fetcher._fetch_tencent_batch 一律 monkeypatch)

用 MVT 前缀 + 2099 假日期, 用例后清理 —— 测试库 session 共享, 污染会波及他人。
"""
import pytest

from app.db import database
from app.services import fetcher, mv_cache

_D = "2099-03-02"        # 假日期(周二), 不与真实交易日冲突
_D_OLD = "2099-02-14"    # 17 天前 → 超出 15 天窗口
_D_NEAR = "2099-02-25"   # 5 天前 → 窗口内


def _c(i):
    return "MVT%03d" % i


def _cleanup():
    conn = database.get_conn()
    conn.execute("DELETE FROM stock_float_mv_daily WHERE code LIKE 'MVT%'")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


def test_table_exists():
    conn = database.get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stock_float_mv_daily)").fetchall()]
    conn.close()
    for c in ("date", "code", "name", "float_mv", "free_mv", "board", "src"):
        assert c in cols


def test_save_lookup_roundtrip():
    mv_cache.save(_D, {_c(1): {"name": "缓存甲", "float_mv": 5.0e9,
                               "free_mv": 4.0e9, "board": "AI", "src": "em"}})
    got = mv_cache.lookup([_c(1)], date=_D)
    assert _c(1) in got
    assert got[_c(1)]["float_mv"] == 5.0e9
    assert got[_c(1)]["name"] == "缓存甲"
    assert got[_c(1)]["src"] == "em"


def test_lookup_prefers_nearest_day():
    """同一 code 多日缓存 → 取 <= date 的**最近一日**, 不是任一日"""
    mv_cache.save(_D_NEAR, {_c(2): {"name": "近", "float_mv": 1.0e9, "src": "em"}})
    mv_cache.save(_D, {_c(2): {"name": "当天", "float_mv": 2.0e9, "src": "em"}})
    got = mv_cache.lookup([_c(2)], date=_D)
    assert got[_c(2)]["float_mv"] == 2.0e9


def test_lookup_respects_back_window():
    """20 天前的市值不得被采用(股本可能已变); 5 天前的可以"""
    mv_cache.save(_D_OLD, {_c(3): {"name": "太旧", "float_mv": 9.9e9, "src": "em"}})
    assert mv_cache.lookup([_c(3)], date=_D) == {}
    mv_cache.save(_D_NEAR, {_c(4): {"name": "近期", "float_mv": 7.7e9, "src": "em"}})
    assert _c(4) in mv_cache.lookup([_c(4)], date=_D)


def test_fill_keeps_existing_value():
    """东财已有市值 → 不动(东财最准, 缓存/腾讯都不得覆盖)"""
    raw = {_c(5): {"bid_change": 1.0, "bid_amt": 10.0, "name": "甲",
                   "float_mv": 3.3e9, "free_mv": 0, "board": ""}}
    st = mv_cache.fill(raw, date=_D)
    assert raw[_c(5)]["float_mv"] == 3.3e9
    assert st["need"] == 0 and st["from_tencent"] == 0


def test_fill_from_cache():
    mv_cache.save(_D_NEAR, {_c(6): {"name": "缓存乙", "float_mv": 6.6e9,
                                    "free_mv": 5.5e9, "board": "算力", "src": "em"}})
    raw = {_c(6): {"bid_change": 2.0, "bid_amt": 20.0, "name": "",
                   "float_mv": 0, "free_mv": 0, "board": ""}}
    st = mv_cache.fill(raw, date=_D)
    assert raw[_c(6)]["float_mv"] == 6.6e9
    assert raw[_c(6)]["name"] == "缓存乙"
    assert st["from_cache"] == 1 and st["from_tencent"] == 0


def test_fill_from_tencent_and_unit(monkeypatch):
    """腾讯 f44 单位=**亿** → ×1e8 得元。单位错会让市值放大 1e8 倍(评分全乱)"""

    def _fake(symbols):
        out = {}
        for s in symbols:
            code = s[2:]
            f = [""] * 60
            f[1] = "腾讯" + code
            f[44] = "123.45"          # 123.45 亿
            out[code] = f
        return out

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _fake)
    raw = {_c(7): {"bid_change": 3.0, "bid_amt": 30.0, "name": "",
                   "float_mv": 0, "free_mv": 0, "board": ""}}
    st = mv_cache.fill(raw, date=_D)
    assert abs(raw[_c(7)]["float_mv"] - 1.2345e10) < 1.0
    assert raw[_c(7)]["name"] == "腾讯" + _c(7)
    assert st["from_tencent"] == 1
    # 补到的值必须写回缓存, 下次不用再问腾讯
    assert _c(7) in mv_cache.lookup([_c(7)], date=_D)


def test_fetch_tencent_skips_bad_rows(monkeypatch):
    def _fake(symbols):
        out = {}
        for s in symbols:
            f = [""] * 60
            f[1] = "名"
            f[44] = ""                # 空市值
            out[s[2:]] = f
        return out

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _fake)
    assert mv_cache.fetch_tencent([_c(8)]) == {}


def test_disabled_skips_tencent(monkeypatch):
    """开关关闭 → 只写不补(退化到 P2 之前的行为), 不给腾讯发请求"""
    monkeypatch.setattr(mv_cache, "enabled", lambda: False)
    called = {"n": 0}

    def _fake(symbols):
        called["n"] += 1
        return {}

    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", _fake)
    raw = {_c(9): {"bid_change": 1.0, "bid_amt": 1.0, "name": "",
                   "float_mv": 0, "free_mv": 0, "board": ""}}
    mv_cache.fill(raw, date=_D)
    assert called["n"] == 0
    assert raw[_c(9)]["float_mv"] == 0
