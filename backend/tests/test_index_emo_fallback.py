# -*- coding: utf-8 -*-
"""首页指数带/情绪卡兜底(2026-09-29)测试。

背景: `GET /api/kpl/index-brief` 原先**裸调**猫爪两个接口(`index_snapshot` / `emo_daily`),
上游一 429/失败, 首页第一屏的指数带与情绪卡就整块空。兜底顺序(下沉到 meoz_client):
  指数: 猫爪(src=meoz) → 腾讯简版(src=tencent) → 上次成功值(src=stale);
  情绪: 猫爪 → 上次成功值(**仅当此刻仍然成立**, 见 `_emo_stale_ok`, 标 stale=1)。

源的选择是实测结论(2026-09-29 两机同验): 腾讯 `qt.gtimg.cn` 200 可用; 新浪 `hq.sinajs.cn`
**403**(该出口 IP 被拉黑, core/net.py:18); 东财 `push2/ulist.np` 时通时 RST(net.py:16)。

本文件锁住: 兜底顺序与 `src` 标记、腾讯行的**字段位/昨收自算**、以及"绝不用隔夜家数
冒充当日"这条纪律(情绪卡在首页第一屏, 错值比空值危害大)。
"""
import calendar
import time

import pytest

from app.services import meoz_client


def _bj_ts(y, m, d, hh, mm):
    """北京时刻 → unix 秒。"""
    return calendar.timegm((y, m, d, hh, mm, 0, 0, 0, 0)) - 8 * 3600


class _FrozenTime:
    """冻结 `time.time()` 的 shim(其余函数转发真 time 模块)。"""

    def __init__(self, now):
        self._now = now

    def time(self):
        return self._now

    def __getattr__(self, name):
        return getattr(time, name)


class _KvStore:
    def __init__(self, ini=None):
        self.kv = dict(ini or {})
        self.set_calls = []

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def set(self, key, value, ttl=0):
        self.set_calls.append((key, value, ttl))
        self.kv[key] = value
        return True


def _meoz_payload(rows):
    return {"data": {"fields": ["symbol", "name", "close", "pre_close", "change", "pct_chg"],
                     "items": rows}}


def _by_code(out):
    return {x["code"]: x for x in out}


# ---------------- 腾讯简版解析器(位置错一位就全错) ----------------

def test_tencent_index_parser_positions_and_preclose(monkeypatch):
    """字段位 + 昨收自算: 3823.62-(-64.75)=3888.37(与腾讯K线 9/24 收盘逐位一致)。"""
    raw = ('v_s_sh000001="1~上证指数~000001~3823.62~-64.75~-1.67~'
           '452350675~80454370~~679205.78~ZS~";')

    class _Resp:
        def read(self):
            return raw.encode("gbk")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(meoz_client._net, "http_get", lambda req, timeout=None: _Resp())
    monkeypatch.setattr(meoz_client, "_tx_index_cache", {"ts": 0.0, "rows": {}})

    r = meoz_client._tx_index_rows()["000001"]
    assert r["name"] == "上证指数"
    assert r["px"] == 3823.62
    assert r["chg"] == -64.75
    assert r["pctChg"] == -1.67
    assert r["preClose"] == 3888.37


def test_tencent_index_failure_returns_empty_not_raises(monkeypatch):
    """腾讯也挂 ⇒ 返空 dict(不抛) —— 兜底链自己不能成为新的失败点。"""
    def _boom(req, timeout=None):
        raise OSError("timeout")

    monkeypatch.setattr(meoz_client._net, "http_get", _boom)
    monkeypatch.setattr(meoz_client, "_tx_index_cache", {"ts": 0.0, "rows": {}})
    assert meoz_client._tx_index_rows() == {}


# ---------------- 指数: 三级兜底 ----------------

def test_index_snapshot_prefers_meoz_and_never_calls_tencent(monkeypatch):
    """猫爪 8 只全给 ⇒ 全部 src=meoz, 且**不碰**腾讯(兜底不该在正常路径上白打上游)。"""
    monkeypatch.setattr(meoz_client, "store", _KvStore())
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: _meoz_payload([
        ["000001.SH", "上证指数", 3823.62, 3888.37, -64.75, -1.67],
        ["399001.SZ", "深证成指", 12858.75, 13316.97, -458.22, -3.44],
        ["399006.SZ", "创业板指", 3139.82, 3288.95, -149.13, -4.53],
        ["000016.SH", "上证50", 2806.94, 2842.74, -35.80, -1.26],
        ["000300.SH", "沪深300", 4340.76, 4439.14, -98.38, -2.22],
        ["000688.SH", "科创50", 1555.98, 1621.87, -65.89, -4.06],
        ["000852.SH", "中证1000", 7294.17, 7570.14, -275.97, -3.65],
        ["932000.CSI", "中证2000", 2000.00, 2020.00, -20.00, -0.99],
    ]))
    monkeypatch.setattr(meoz_client, "_tx_index_rows",
                        lambda: pytest.fail("猫爪 8 只齐备时不得打腾讯"))

    out = _by_code(meoz_client.index_snapshot())
    assert {x["src"] for x in out.values()} == {"meoz"}
    assert out["000001"]["px"] == 3823.62 and out["000001"]["preClose"] == 3888.37


def test_index_snapshot_falls_back_to_tencent_per_row(monkeypatch):
    """猫爪全空 ⇒ 腾讯补缺并标 src=tencent; 腾讯没给的那只(如中证2000)仍为空, 不硬编。"""
    monkeypatch.setattr(meoz_client, "store", _KvStore())
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})
    monkeypatch.setattr(meoz_client, "_tx_index_rows", lambda: {
        "000001": {"name": "上证指数", "px": 3823.62, "preClose": 3888.37,
                   "chg": -64.75, "pctChg": -1.67}})

    out = _by_code(meoz_client.index_snapshot())
    assert out["000001"]["src"] == "tencent" and out["000001"]["px"] == 3823.62
    assert out["932000"]["px"] is None, "腾讯没给的指数不得臆造"


def test_index_snapshot_tencent_does_not_override_meoz(monkeypatch):
    """只补**缺口** —— 猫爪已给的那只不许被腾讯覆盖(口径以猫爪为准)。"""
    monkeypatch.setattr(meoz_client, "store", _KvStore())
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: _meoz_payload(
        [["000001.SH", "上证指数", 9999.99, 3888.37, 6111.62, 157.1]]))
    monkeypatch.setattr(meoz_client, "_tx_index_rows", lambda: {
        "000001": {"name": "上证指数", "px": 3823.62, "preClose": 3888.37,
                   "chg": -64.75, "pctChg": -1.67}})

    row = _by_code(meoz_client.index_snapshot())["000001"]
    assert row["px"] == 9999.99 and row["src"] == "meoz"


def test_index_snapshot_falls_back_to_last_good(monkeypatch):
    """猫爪 + 腾讯都空 ⇒ 上次成功值顶上并标 stale(首页仍有点位可看)。"""
    store = _KvStore({"index_brief:last": {
        "_ts": 1.0,
        "000001": {"px": 3800.0, "preClose": 3888.37, "chg": -88.37, "pctChg": -2.27}}})
    monkeypatch.setattr(meoz_client, "store", store)
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})
    monkeypatch.setattr(meoz_client, "_tx_index_rows", lambda: {})

    row = _by_code(meoz_client.index_snapshot())["000001"]
    assert row["src"] == "stale" and row["px"] == 3800.0


def test_index_snapshot_persists_merged_last_good(monkeypatch):
    """落盘只存真值(不含 stale)且与上次**合并** —— 部分缺时不得把别的好值冲掉。"""
    store = _KvStore({"index_brief:last": {
        "_ts": 1.0,
        "399001": {"px": 12858.75, "preClose": 13316.97, "chg": -458.22, "pctChg": -3.44}}})
    monkeypatch.setattr(meoz_client, "store", store)
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})
    monkeypatch.setattr(meoz_client, "_tx_index_rows", lambda: {
        "000001": {"name": "上证指数", "px": 3823.62, "preClose": 3888.37,
                   "chg": -64.75, "pctChg": -1.67}})

    meoz_client.index_snapshot()

    saved = store.kv["index_brief:last"]
    assert saved["000001"]["px"] == 3823.62, "本次真值必须落盘(供下次救急)"
    assert saved["399001"]["px"] == 12858.75, "上次的好值必须保留(合并而非覆盖)"


def test_index_snapshot_stale_does_not_wipe_cache(monkeypatch):
    """三源全空(只剩 stale 值)时不得覆盖缓存 —— 否则把救命的兜底值冲掉。"""
    store = _KvStore({"index_brief:last": {
        "_ts": 1.0,
        "000001": {"px": 3800.0, "preClose": 3888.37, "chg": -88.37, "pctChg": -2.27}}})
    monkeypatch.setattr(meoz_client, "store", store)
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})
    monkeypatch.setattr(meoz_client, "_tx_index_rows", lambda: {})

    meoz_client.index_snapshot()
    assert store.kv["index_brief:last"]["000001"]["px"] == 3800.0


# ---------------- 情绪: stale 可用性(纯函数边界 + 接口行为) ----------------

def test_emo_stale_ok_intraday_requires_same_trading_day():
    """盘中(2026-09-29 周二 10:00): 同日值可用, 隔夜值一律拒绝。"""
    ts = _bj_ts(2026, 9, 29, 10, 0)
    assert meoz_client._emo_stale_ok(ts - 60, "20260929", ts) is True
    assert meoz_client._emo_stale_ok(ts - 60, "20260928", ts) is False


def test_emo_stale_ok_offhours_uses_freshness_window():
    """盘前(08:00, 数据本就静态): 12h 内可用, 超过则拒绝。"""
    ts = _bj_ts(2026, 9, 29, 8, 0)
    assert meoz_client._emo_stale_ok(ts - 6 * 3600, "20260928", ts) is True
    assert meoz_client._emo_stale_ok(ts - 13 * 3600, "20260928", ts) is False


def test_emo_stale_ok_weekend_is_offhours():
    """周六(2026-10-03)不走盘中口径 —— 否则周末永远读不到兜底值。"""
    ts = _bj_ts(2026, 10, 3, 10, 0)
    assert meoz_client._emo_stale_ok(ts - 6 * 3600, "20260930", ts) is True


def test_emo_daily_falls_back_to_stale_same_day(monkeypatch):
    """猫爪空返回 ⇒ 当日上次成功值顶上, 标 stale=1(内部 _ts 不外泄)。"""
    now = _bj_ts(2026, 9, 29, 10, 0)
    monkeypatch.setattr(meoz_client, "time", _FrozenTime(now))
    monkeypatch.setattr(meoz_client, "store", _KvStore(
        {"emo_brief:last": {"tradedate": "20260929", "s2": 3000, "_ts": now - 30}}))
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})

    out = meoz_client.emo_daily()
    assert out.get("stale") == 1
    assert out.get("s2") == 3000
    assert "_ts" not in out


def test_emo_daily_rejects_stale_from_previous_day(monkeypatch):
    """隔夜值不得冒充当日 ⇒ 返 {} 让前端显示 '-'(首页第一屏, 错值比空值危害大)。"""
    now = _bj_ts(2026, 9, 29, 10, 0)
    monkeypatch.setattr(meoz_client, "time", _FrozenTime(now))
    monkeypatch.setattr(meoz_client, "store", _KvStore(
        {"emo_brief:last": {"tradedate": "20260928", "s2": 3000, "_ts": now - 30}}))
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})

    assert meoz_client.emo_daily() == {}


def test_emo_daily_never_overwrites_good_cache_with_empty(monkeypatch):
    """空返回**绝不落盘** —— 否则一次抖动就把兜底缓存冲掉, 从此兜不回来。"""
    now = _bj_ts(2026, 9, 29, 10, 0)
    monkeypatch.setattr(meoz_client, "time", _FrozenTime(now))
    store = _KvStore({"emo_brief:last": {"tradedate": "20260929", "s2": 3000, "_ts": now - 30}})
    monkeypatch.setattr(meoz_client, "store", store)
    monkeypatch.setattr(meoz_client, "call_cached", lambda *a, **k: {"data": {}})

    meoz_client.emo_daily()
    assert store.kv["emo_brief:last"]["s2"] == 3000, "好缓存被空值冲掉了"


def test_emo_daily_persists_successful_payload(monkeypatch):
    """成功时把这次的值落盘(供后续抖动兜底), 且带 _ts 供新鲜度判定。"""
    now = _bj_ts(2026, 9, 29, 10, 0)
    monkeypatch.setattr(meoz_client, "time", _FrozenTime(now))
    store = _KvStore()
    monkeypatch.setattr(meoz_client, "store", store)
    fields = ["tradedate", "s2", "s6", "u5", "d3", "u12", "fp108", "l17",
              "l21", "l22", "am", "am_diff"]
    item = ["20260929", 3000, 1200, 40, 3, 25, 5, 55.5, 62.0, 4, 2e12, 1e11]
    monkeypatch.setattr(meoz_client, "call_cached",
                        lambda *a, **k: {"data": {"fields": fields, "items": [item]}})

    out = meoz_client.emo_daily()
    assert out.get("tradedate") == "20260929" and out.get("s2") == 3000
    saved = store.kv.get("emo_brief:last")
    assert saved and saved["s2"] == 3000 and saved["_ts"] == now
