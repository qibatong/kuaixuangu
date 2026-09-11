# -*- coding: utf-8 -*-
"""P2-1 TickPlus 第二源回归(2026-09-12)

锁定九条性质 —— 每一条都对应实测踩过的坑, 不是为覆盖而写:

  decode_payload
    ① **响应体是 ZIP**(PK 开头) —— 直接 json.loads 得 0 条(补齐脚本踩过)
    ② 裸 JSON list / {data:[...]} 也能解(防上游改封装)
  normalize
    ③ p<=0(竞价无成交)**必须过滤** —— 其 zf 恒 -100%, 混进来就是"全市场暴跌"
    ④ 单位: je 是**元**, bid_amt 口径是**万元** → /1e4
    ⑤ 异常涨幅 |zf|>30% 过滤(新股首日 zf 可达 188%)
  fetch / snapshot_map
    ⑥ 无 token → 不发请求(空 map), 且不影响主链路
    ⑦ 熔断中 → 直接返回空(不重试, 防竞价窗口内空转)
    ⑧ 请求异常 → 记熔断 + 返回空, **绝不抛给采集主链路**
  _merge_tickplus
    ⑨ 东财缺失的 code → 补票(市值留给 mv_cache 后补)
    ⑩ 东财 bid_change/bid_amt 为 0 → 补值
    ⑪ 东财已有正常值 → **绝不覆盖**(两源口径不同, 覆盖=同一时点两套数)

全程不联网: 一律 monkeypatch。
"""
import io
import json
import zipfile

import pytest

from app.services import auction_snapshot as AS
from app.services import fetcher, tickplus


def _zip_bytes(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data.json", json.dumps(rows, ensure_ascii=False))
    return buf.getvalue()


_ROWS = [
    {"code": "000001", "t": "2026-09-11 09:25:00", "p": 11.50, "pc": 11.20,
     "zf": 2.68, "jv": 1200000, "je": 13800000.0, "bs": 1},
    {"code": "600002", "t": "2026-09-11 09:25:00", "p": 20.00, "pc": 20.00,
     "zf": 0.0, "jv": 500000, "je": 10000000.0, "bs": -1},
    {"code": "300003", "t": "2026-09-11 09:25:00", "p": 0, "pc": 30.00,
     "zf": -100.0, "jv": 0, "je": 0, "bs": 0},          # p=0 竞价无成交 → 过滤
    {"code": "688004", "t": "2026-09-11 09:25:00", "p": 88.0, "pc": 44.0,
     "zf": 188.0, "jv": 100, "je": 8800.0, "bs": 1},     # 新股首日 188% → 过滤
    {"code": "", "t": "", "p": 10.0, "zf": 1.0, "je": 1.0},   # 空 code → 过滤
]


# ---------- decode ----------
def test_zip_payload():
    rows = tickplus.decode_payload(_zip_bytes(_ROWS))
    assert len(rows) == 5


def test_plain_json_and_data_wrapper():
    assert len(tickplus.decode_payload(json.dumps(_ROWS).encode())) == 5
    wrapped = json.dumps({"code": 0, "data": _ROWS}).encode()
    assert len(tickplus.decode_payload(wrapped)) == 5


def test_broken_payload_returns_empty():
    assert tickplus.decode_payload(b"") == []
    assert tickplus.decode_payload(b"<html>502</html>") == []


# ---------- normalize ----------
def test_normalize_filters_no_deal_and_crazy():
    m = tickplus.normalize(_ROWS)
    assert set(m) == {"000001", "600002"}          # 300003(p=0) 与 688004(188%) 被滤


def test_normalize_unit_wan_yuan():
    m = tickplus.normalize(_ROWS)
    assert abs(m["000001"]["bid_amt"] - 1380.0) < 1e-6      # 1380万元
    assert m["000001"]["bid_vol"] == 1200000
    assert abs(m["000001"]["bid_change"] - 2.68) < 1e-6


def test_normalize_keeps_zero_change():
    """zf=0 是**真实值**(平开), 不得被当成缺失过滤"""
    m = tickplus.normalize(_ROWS)
    assert m["600002"]["bid_change"] == 0.0


# ---------- fetch / snapshot_map ----------
def test_no_token_returns_empty(monkeypatch):
    monkeypatch.setattr(tickplus, "token", lambda: "")
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src="x": False)
    assert tickplus.snapshot_map() == {}


def test_circuit_open_returns_empty(monkeypatch):
    monkeypatch.setattr(tickplus, "token", lambda: "T")
    monkeypatch.setattr(tickplus, "enabled", lambda: True)
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src="x": True)
    assert tickplus.snapshot_map() == {}


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(tickplus, "enabled", lambda: False)
    assert tickplus.snapshot_map() == {}


def test_request_error_never_raises(monkeypatch):
    """网络异常: 吞掉 + 记熔断, 绝不把异常抛给竞价采集主链路"""
    called = {"record": 0}

    def _boom(*a, **kw):
        raise RuntimeError("timeout")

    monkeypatch.setattr(tickplus, "token", lambda: "T")
    monkeypatch.setattr(tickplus, "enabled", lambda: True)
    monkeypatch.setattr(fetcher, "_check_circuit", lambda src="x": False)
    monkeypatch.setattr(tickplus, "fetch_fullbid", _boom)
    monkeypatch.setattr(fetcher, "_record", lambda *a, **kw: called.__setitem__("record", 1))
    assert tickplus.snapshot_map() == {}
    assert called["record"] == 1


def test_fetch_fullbid_end_to_end(monkeypatch):
    """打桩 urlopen → 走完整 decode+normalize 链路(验证真实调用路径而非仅单函数)"""
    class _Resp:
        def read(self):
            return _zip_bytes(_ROWS)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(tickplus, "token", lambda: "T")
    monkeypatch.setattr(tickplus.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp())
    m = tickplus.fetch_fullbid()
    assert set(m) == {"000001", "600002"}


# ---------- _merge_tickplus ----------
def _em(**kw):
    base = {"bid_change": 0.0, "bid_amt": 0.0, "name": "甲",
            "bid_buy_amt": 0, "float_mv": 0, "free_mv": 0, "board": ""}
    base.update(kw)
    return base


def test_merge_adds_missing_code():
    raw = {"000001": _em(bid_change=1.0, bid_amt=100.0)}
    tp = {"000001": {"bid_change": 1.0, "bid_amt": 100.0, "bid_vol": 1},
          "600519": {"bid_change": 3.2, "bid_amt": 2500.0, "bid_vol": 2}}
    st = AS._merge_tickplus(raw, tp)
    assert st["added"] == 1
    assert raw["600519"]["bid_change"] == 3.2
    assert raw["600519"]["float_mv"] == 0          # 市值留给 mv_cache.fill 后补


def test_merge_fills_zero_only():
    raw = {"000002": _em(bid_change=0.0, bid_amt=0.0)}
    tp = {"000002": {"bid_change": 5.1, "bid_amt": 880.0, "bid_vol": 3}}
    st = AS._merge_tickplus(raw, tp)
    assert st["filled"] == 2
    assert raw["000002"]["bid_change"] == 5.1
    assert raw["000002"]["bid_amt"] == 880.0


def test_merge_never_overrides_good_value():
    """东财已有正常值 → 不覆盖(两源口径不同, 覆盖会让同一时点出现两套数)"""
    raw = {"000003": _em(bid_change=2.0, bid_amt=300.0)}
    tp = {"000003": {"bid_change": 9.9, "bid_amt": 9999.0, "bid_vol": 4}}
    st = AS._merge_tickplus(raw, tp)
    assert (st["added"], st["filled"]) == (0, 0)
    assert raw["000003"]["bid_change"] == 2.0 and raw["000003"]["bid_amt"] == 300.0


def test_merge_skips_new_code_without_change():
    """TickPlus 无涨幅的行不新增: 没涨幅进不了评分, 只会污染名单"""
    raw = {}
    tp = {"600000": {"bid_change": None, "bid_amt": 100.0, "bid_vol": 5}}
    st = AS._merge_tickplus(raw, tp)
    assert st["added"] == 0 and raw == {}
