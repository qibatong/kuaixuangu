# -*- coding: utf-8 -*-
"""腾讯兜底缺票告警限频测试 (2026-09-03 降噪):
原实现每次拉取缺票都打 ERROR, 东财封锁期腾讯兜底为常态(稳定缺 ~8 只/0.14%),
每 20-30s 刷一条, 实测单日 676 条占 ERROR 总量 72%。
新语义: 状态(缺票数+失败批)变化才即时 ERROR; 同状态 10 分钟最多 1 条 WARNING;
完全恢复后重置, 下次缺票视为新变化重新 ERROR。
"""
import time

import pytest

from app.services import fetcher


# ---------------- fixture: 每次重置告警状态 ----------------
@pytest.fixture(autouse=True)
def _reset_miss_state():
    fetcher._tencent_miss_state.update(ts=0.0, key=None, repeat=0)
    yield
    fetcher._tencent_miss_state.update(ts=0.0, key=None, repeat=0)


# ---------------- 纯函数: _miss_alert_decision ----------------

def test_first_miss_returns_error():
    """首次缺票(或状态从无到有) → error(即时告警, 不静默)"""
    st = {"ts": 0.0, "key": None, "repeat": 0}
    act = fetcher._miss_alert_decision(st, (8, ()), 1000.0)
    assert act == "error"
    assert st["key"] == (8, ()) and st["ts"] == 1000.0 and st["repeat"] == 1


def test_same_miss_within_interval_silent():
    """同状态缺票且距上次告警 < 10min → silent(静默只计数)"""
    st = {"ts": 1000.0, "key": (8, ()), "repeat": 1}
    act = fetcher._miss_alert_decision(st, (8, ()), 1000.0 + 300)
    assert act == "silent"
    assert st["repeat"] == 2          # 静默仍累计触发次数
    assert st["ts"] == 1000.0         # 时间戳不动(未产生日志)


def test_same_miss_after_interval_repeat_warn():
    """同状态缺票持续超过 10min → repeat_warn(限频兜底: 证明仍在刷但不刷屏)"""
    st = {"ts": 1000.0, "key": (8, ()), "repeat": 1}
    act = fetcher._miss_alert_decision(st, (8, ()), 1000.0 + 601)
    assert act == "repeat_warn"
    assert st["ts"] == 1000.0 + 601   # 发完汇总后刷新时间戳
    assert st["repeat"] == 2


def test_changed_miss_immediate_error():
    """缺票状态变化(如 8→50 只) → 无论距上次多久都立即 error"""
    st = {"ts": 0.0, "key": (8, ()), "repeat": 5}
    act = fetcher._miss_alert_decision(st, (50, (300,)), 10.0)
    assert act == "error"
    assert st["key"] == (50, (300,)) and st["repeat"] == 1   # 变化后重置计数


def test_repeat_warn_then_change_again_error():
    """repeat_warn 后状态再次变化 → 立即 error"""
    st = {"ts": 0.0, "key": (8, ()), "repeat": 1}
    assert fetcher._miss_alert_decision(st, (8, ()), 100.0) == "silent"
    act = fetcher._miss_alert_decision(st, (9, ()), 101.0)   # 9≠8 变化
    assert act == "error"


def test_sig_failed_batches_change_is_change():
    """失败批数量变化也算状态变化(即便缺票数相同)"""
    st = {"ts": 0.0, "key": (8, ()), "repeat": 1}
    act = fetcher._miss_alert_decision(st, (8, (300, 300)), 5.0)
    assert act == "error"   # (8,()) → (8,(300,300)) 是变化


# ---------------- 集成: fetch_tencent_market 真实日志行为 ----------------

_MISS_CODES = {"600005", "600010", "600015"}   # 稳定缺失 3 只 (3/600=0.5%>0.1% → ERROR 级)


def _gen_codes(n=600):
    return ["600%03d" % i for i in range(n)]


def _make_fields(code):
    """构造腾讯 88 字段 raw(下标0起, 解析用到 1/3/4/32/36/37/38/44)"""
    f = [""] * 60
    f[0], f[1] = code, "测试" + code
    f[3], f[4] = "10.00", "9.90"      # 现价 / 昨收
    f[32] = "1.01"                    # 涨跌%
    f[36] = "1000"                    # 成交量(手)
    f[37] = "5000"                    # 成交额(万)
    f[38] = "2.00"                    # 换手率
    f[44] = "50.0"                    # 流通市值(亿)
    return f


def _patch_tencent_downstream(monkeypatch, missing=True, codes=None):
    """patch 腾讯兜底链路, 返回 codes 与日志记录器"""
    codes = codes or _gen_codes()
    log_errors, log_warnings = [], []

    def fake_batch(batch):
        out = {}
        for sym in batch:
            code = sym[2:]
            if missing and code in _MISS_CODES:
                continue
            out[code] = _make_fields(code)
        return out

    monkeypatch.setattr(fetcher, "_check_circuit", lambda src="eastmoney_clist": False)
    monkeypatch.setattr(fetcher, "_all_market_codes", lambda: list(codes))
    monkeypatch.setattr(fetcher, "_tencent_symbol", lambda c: ("sh" if c.startswith("6") else "sz") + c)
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", fake_batch)
    monkeypatch.setattr(fetcher, "_record", lambda *a, **k: None)
    monkeypatch.setattr(fetcher.log, "error", lambda msg, *a: log_errors.append(msg % a if a else msg))
    monkeypatch.setattr(fetcher.log, "warning", lambda msg, *a: log_warnings.append(msg % a if a else msg))
    return codes, log_errors, log_warnings


def test_fetch_tencent_market_first_miss_error_then_silent(monkeypatch):
    """真实拉取: 首次缺票打 ERROR; 立即再拉同状态缺票 → 静默(0 新日志)"""
    _, errs, warns = _patch_tencent_downstream(monkeypatch, missing=True)
    fetcher.fetch_tencent_market("m:1+t:2")
    assert len(errs) == 1 and "缺票告警" in errs[0]
    fetcher.fetch_tencent_market("m:1+t:2")     # 同状态, 距上次 <10min
    assert len(errs) == 1                        # 不再新增 ERROR
    assert len(warns) == 0                       # 也不新增 WARNING


def test_fetch_tencent_market_repeat_warn_after_interval(monkeypatch):
    """同状态缺票持续超过 10min → 追加 1 条 WARNING 汇总(仍不刷 ERROR)"""
    _, errs, warns = _patch_tencent_downstream(monkeypatch, missing=True)
    fetcher.fetch_tencent_market("m:1+t:2")
    fetcher._tencent_miss_state["ts"] -= 601    # 模拟时间流逝 10min+
    fetcher.fetch_tencent_market("m:1+t:2")
    assert len(errs) == 1                         # ERROR 仍只有首次 1 条
    assert len(warns) == 1 and "缺票持续中" in warns[0]


def test_fetch_tencent_market_recovery_resets_then_alert_again(monkeypatch):
    """缺票恢复后状态重置; 再出现缺票视为新变化重新 ERROR"""
    _, errs, warns = _patch_tencent_downstream(monkeypatch, missing=True)
    fetcher.fetch_tencent_market("m:1+t:2")
    assert len(errs) == 1
    # 恢复: 不再缺票 → 无日志, 状态被重置
    missing_box = {"v": True}
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", lambda batch: {
        sym[2:]: _make_fields(sym[2:]) for sym in batch if not (missing_box["v"] and sym[2:] in _MISS_CODES)
    })
    missing_box["v"] = False
    fetcher.fetch_tencent_market("m:1+t:2")
    assert len(errs) == 1 and len(warns) == 0
    assert fetcher._tencent_miss_state["key"] is None     # 已重置
    # 再缺票(同 3 只) → 新变化 → 重新 ERROR
    missing_box["v"] = True
    fetcher.fetch_tencent_market("m:1+t:2")
    assert len(errs) == 2


def test_fetch_tencent_market_full_ok_no_log(monkeypatch):
    """完全无缺票 → 不产生任何 ERROR/WARNING"""
    _, errs, warns = _patch_tencent_downstream(monkeypatch, missing=False)
    out = fetcher.fetch_tencent_market("m:1+t:2")
    assert len(errs) == 0 and len(warns) == 0
    assert len(out) == 600
