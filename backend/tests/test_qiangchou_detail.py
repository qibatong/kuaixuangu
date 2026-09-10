# -*- coding: utf-8 -*-
"""抢筹明细(2026-09-09): 左视图要区分竞额/涨幅/末秒抢筹并看到幅度

事故背景: 选股结果"抢筹"列只有一个 🔥, 主人反馈"分不清是竞额抢筹还是涨幅抢筹、
看不到幅度" —— 与右视图竞价异动(带 qcDelta 幅度)信息量不对等。
本文件锁定"三表 code + 幅度一并带出"的行为, 防止将来改回纯代码集合。
"""
import pytest

from app.services import history, kpl


def _mk(code, **kw):
    it = {"code": code, "name": "测试" + code}
    it.update(kw)
    return it


# ==================== kpl.get_qiangchou_detail ====================
def test_detail_merges_three_lists(monkeypatch):
    """三张表各自的幅度字段分别落到 amt/chg/last, types 记录命中来源"""
    monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda date=None: {
        "list20": [_mk("300001", qcDelta=1.23)],
        "list20Chg": [_mk("300001", qcDeltaChg=0.85), _mk("300002", qcDeltaChg=2.1)],
        "listLast": [_mk("300001", qcDeltaLast=0.4)],
    })
    d = kpl.get_qiangchou_detail()
    assert set(d.keys()) == {"300001", "300002"}
    one = d["300001"]
    assert one["types"] == ["amt", "chg", "last"]
    assert one["amt"] == 1.23 and one["chg"] == 0.85 and one["last"] == 0.4
    # 只命中涨幅抢筹: 其余幅度保持 None, 不串表
    assert d["300002"]["types"] == ["chg"]
    assert d["300002"]["amt"] is None and d["300002"]["chg"] == 2.1


def test_detail_skips_blank_and_bad_value(monkeypatch):
    """空 code / 空值 / 非数值不崩, 幅度置 None 但 code 仍算命中"""
    monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda date=None: {
        "list20": [_mk("", qcDelta=1.0), _mk("300003", qcDelta=None), _mk("300004", qcDelta="abc")],
    })
    d = kpl.get_qiangchou_detail()
    assert set(d.keys()) == {"300003", "300004"}
    assert d["300003"]["amt"] is None and d["300004"]["amt"] is None


def test_codes_reuse_detail_same_source(monkeypatch):
    """get_qiangchou_codes 必须复用 detail —— 代码集与明细同源, 不许两套口径"""
    calls = {"n": 0}

    def _fake(date=None):
        calls["n"] += 1
        return {"list20": [_mk("300001", qcDelta=1.0)], "listLast": [_mk("300009")]}

    monkeypatch.setattr(kpl, "fetch_bid_qiangcang", _fake)
    codes = kpl.get_qiangchou_codes()
    assert codes == {"300001", "300009"}
    assert calls["n"] == 1


def test_detail_and_codes_empty_on_exception(monkeypatch):
    """数据源异常: detail={} / codes=set(), 调用方回退旧公式, 抢筹不会全灭"""
    def _boom(date=None):
        raise RuntimeError("开盘啦挂了")

    monkeypatch.setattr(kpl, "fetch_bid_qiangcang", _boom)
    assert kpl.get_qiangchou_detail() == {}
    assert kpl.get_qiangchou_codes() == set()


# ==================== 抢筹输出字段 (picker.pipeline._qc_of) ====================
# 2026-09-11: 原用例直测 scorer._qc_fields(已随老链路退役)。抢筹打标唯一实现
# 现为 picker/pipeline._qc_of; 新链路**不做公式兜底**(明细与集合皆无 → 不打标,
# 宁缺勿造假), 故原 formula_fallback 用例随该分支一并删除。
def _ctx(detail=None, codes=None):
    from app.services.picker import pipeline as pl
    return pl.PickContext(qiangchou_detail=detail, qiangchou_codes=codes)


def test_qc_of_detail_preferred():
    """明细优先: 带类型 + 三张表各自幅度 + 中文摘要"""
    from app.services.picker import pipeline as pl
    detail = {"300001": {"types": ["amt", "last"], "amt": 1.23, "chg": None, "last": 0.4}}
    out = pl._qc_of("300001", _ctx(detail, {"300001"}))
    assert out["qiangchou"] == 1
    assert out["qcType"] == "amt+last"
    assert out["qcAmt"] == 1.23 and out["qcLast"] == 0.4 and out["qcChg"] is None
    assert "竞额抢筹 1.23%" in out["qcText"] and "末秒抢筹 0.4个百分点" in out["qcText"]
    assert out["qcFallback"] == 0


def test_qc_of_codes_only():
    """只有代码集合(旧调用): 仍打标, 但无类型无幅度, 不许误报 fallback"""
    from app.services.picker import pipeline as pl
    out = pl._qc_of("300001", _ctx(None, {"300001"}))
    assert out["qiangchou"] == 1 and out["qcType"] == "qc"
    assert out["qcAmt"] is None and out["qcFallback"] == 0
    assert pl._qc_of("300002", _ctx(None, {"300001"}))["qiangchou"] == 0


def test_qc_of_no_source_never_fabricates():
    """明细与集合皆无(数据源故障) → 不打标(新链路不做公式兜底: 抢筹是事实, 不许编)"""
    from app.services.picker import pipeline as pl
    out = pl._qc_of("300001", _ctx(None, None))
    assert out["qiangchou"] == 0 and out["qcType"] == "" and out["qcFallback"] == 0


# ==================== 落库往返 ====================
def test_qc_pack_unpack_roundtrip():
    """历史回看能还原细分: 落库 JSON → 输出字段"""
    item = {"qiangchou": 1, "qcType": "amt+chg", "qcAmt": 1.23, "qcChg": 0.85,
            "qcLast": None, "qcText": "竞额抢筹 1.23%；涨幅抢筹 0.85个百分点", "qcFallback": 0}
    packed = history._qc_pack(item)
    assert packed and "amt+chg" in packed
    got = history._qc_unpack(packed)
    assert got["qcType"] == "amt+chg"
    assert got["qcAmt"] == 1.23 and got["qcChg"] == 0.85 and got["qcLast"] is None
    assert "涨幅抢筹" in got["qcText"]


def test_qc_pack_none_when_not_hit():
    """未命中抢筹不写 JSON(省空间), 旧批次(null)回看退化为只有 🔥"""
    assert history._qc_pack({"qiangchou": 0, "qcType": ""}) is None
    assert history._qc_pack({"qiangchou": 1}) is None          # 老批次无细分
    assert history._qc_unpack(None)["qcType"] == ""
    assert history._qc_unpack("{坏JSON")["qcAmt"] is None      # 脏数据不炸
