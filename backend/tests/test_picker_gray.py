# -*- coding: utf-8 -*-
"""重构 P3 灰度双跑测试

铁律: **灰度绝不能影响老链路返回**。新链路旁路执行, 只打日志;
无论它成功/失败/超时, /api/stocks 的响应必须与不开灰度时完全一致。
"""
import pytest

from app.api import stocks as api
from app.services.picker import parity

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000,
}


def test_gray_disabled_by_default(monkeypatch):
    """默认关闭(未配置 picker_gray 时)"""
    from app.services import settings
    monkeypatch.setattr(settings, "get", lambda k, d=None: None)
    assert api._gray_enabled() is False


def test_gray_enabled_by_setting(monkeypatch):
    from app.services import settings
    monkeypatch.setattr(settings, "get", lambda k, d=None: "1" if k == "picker_gray" else None)
    assert api._gray_enabled() is True


def test_gray_run_swallows_all_errors(monkeypatch):
    """新链路炸了也不能冒泡到选股主流程"""
    def boom(*a, **kw):
        raise RuntimeError("新链路模拟崩溃")

    monkeypatch.setattr(parity, "compare", boom)
    api._gray_run(1, "filter", [], FULL, [{"code": "600000"}],
                  bid_amt_map={}, bid_chg_map={}, yesterday_chg_map={})


def test_gray_run_logs_diff(monkeypatch, caplog):
    """有差异时打 warning(可被日志告警捕获)"""
    called = {}

    def fake_compare(legacy_items, raw, f, **kw):
        called["legacy"] = legacy_items
        rep = parity.ParityReport(n_raw=1)
        rep.legacy = [{"code": "600000", "probability": 80, "confidence": 70}]
        rep.only_legacy = ["600000"]
        return rep

    monkeypatch.setattr(parity, "compare", fake_compare)
    with caplog.at_level("WARNING"):
        api._gray_run(1, "filter", [], FULL, [{"code": "600000"}],
                      bid_amt_map={}, bid_chg_map={}, yesterday_chg_map={})
    assert called["legacy"] == [{"code": "600000"}]
    assert "灰度对拍差异" in caplog.text


def test_compare_does_not_rerun_legacy(monkeypatch):
    """灰度场景: 老结果已算出 → 只跑新链路(不重复跑老链路)"""
    import app.services.picker.pipeline as pl
    from app.services.picker.contract import QuoteRow
    from app.services.picker.filter import FilterOutcome
    from app.services.picker.score import ScoredRow, compute_score
    from app.services import scorer

    row = QuoteRow(code="600000", name="某股", bid_change=3.0, bid_amt=5.0e7,
                   float_mv=55e8, price=10.5, yesterday_change=2.0, warn_type=2)
    out = FilterOutcome(kept=[ScoredRow(row=row, score=compute_score(row, scorer.get_scoring_cfg()))])

    monkeypatch.setattr(pl.pfilter, "apply_filters", lambda *a, **kw: out)
    monkeypatch.setattr(pl, "_fetch_list", lambda ctx, pol, f: _fake_list(row))
    monkeypatch.setattr(pl, "_fetch_patch", lambda *a, **kw: None)

    legacy = [{"code": "600000", "probability": out.kept[0].score.probability,
               "confidence": out.kept[0].score.confidence, "bidChange": 3.0,
               "bidAmt": 5000.0, "circulationMV": 55.0,
               "bidTurnover": out.kept[0].row.bid_turnover}]
    rep = parity.compare(legacy, [], FULL, day_bid_change={"600000": 3.0},
                         day_bid_amt_wan={"600000": 5000.0}, zt_codes=set())
    assert rep.identical, rep.summary()
    assert len(rep.new) == 1


def _fake_list(row):
    from app.services.picker.sources.base import SourceResult
    return SourceResult(rows={row.code: row}, label="snapshot")
