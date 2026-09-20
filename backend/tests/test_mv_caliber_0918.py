# -*- coding: utf-8 -*-
"""流通市值口径回归 (v4.11.28, 2026-09-18)

生产实锤(9/17): 华瓷股份 001216 真流通 **49.07 亿**, 快照里落库 **14.75 亿**
→ 被 floatMvFloor=30 剔除。主人现象「我把下限从 30 改成 10, 就多出来一只 40 多亿的」,
那条票就是它。根因是**两处缺陷叠加**, 本文件逐条锁死防复发:

  缺陷 A(采集层) 开盘啦兜底(东财全分区失败时)把"实际流通"(≈自由流通市值)
                 写进了 `float_mv` 列 —— 该列全系统语义 = 东财 f21 **流通市值**。
     A1 委买榜/爆量榜两条路径都必须写 free_mv, float_mv 留 0
     A2 mv_cache.fill 拿不到流通市值的行**不写缓存**(旧代码 REPLACE 成 NULL 会
        把当天早先时点存下的真值冲掉 —— 缓存自我劣化)
     A3 src 精确标注: tencent / em / cache:<来源日期>(缓存补的不得伪装成 em,
        9/17 排查正是被这个假 em 误导成"东财数据本身有问题")

  缺陷 B(链路层) 粗筛在补丁源(点查)之前跑, 把"市值未知"当成"不达标"剔除
     B1 coarse_filter: 市值未知 → **放行**, 留给精筛用真值判
     B2 apply_filters: 市值未知 → 仍剔除(最终语义不变, 只是判定推迟到有真值时)
     B3 _snapshot_candidate_codes 与 picker.filter 同口径: **float_mv 优先**,
        free_mv 只是缺失时的兜底(此前相反, 又是一层误杀)

全程不发真实网络请求(腾讯/开盘啦/东财一律 monkeypatch)。
"""
import os
import sys

import pytest

os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import database
from app.api.stocks import _snapshot_candidate_codes
from app.services import auction_snapshot as asnap
from app.services import kpl, mv_cache, scorer
from app.services.picker import filter as pf
from app.services.picker.contract import QuoteRow
from app.services.picker.score import ScoredRow, compute_score

_D = "2099-03-03"        # 假日期, 不与真实交易日冲突
_D_NEAR = "2099-02-26"   # 5 天前 → 缓存回退窗口内


def _c(i):
    return "MVC%03d" % i


def _cleanup():
    conn = database.get_conn()
    conn.execute("DELETE FROM stock_float_mv_daily WHERE code LIKE 'MVC%'")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


# ==================== 缺陷 A1: 开盘啦兜底的列语义 ====================
def _stub_kpl(monkeypatch):
    """委买榜 1 只 + 爆量榜 1 只(两条写入路径都要覆盖)"""
    monkeypatch.setattr(kpl, "clear_cache", lambda: None)
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [{
        "code": "600000", "name": "委买甲", "bidChange": 3.0,
        "bidAmt": 5_000_000.0,          # 元 → 500 万元
        "bidSealAmt": 1.0e8, "floatMv": 2.0e10, "board": "银行",
    }])
    monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: [{
        "code": "600001", "name": "爆量乙", "bidChange": 2.0,
        "bidAmt": 3_000_000.0,          # 元 → 300 万元
        "floatMv": 1.0e10, "board": "",
    }])


def test_kpl_fallback_writes_free_mv_not_float_mv(monkeypatch):
    """A1: 开盘啦 '实际流通' → free_mv; float_mv 留 0 交给 mv_cache 补真流通市值

    反例(9/17 生产): 写成 float_mv → 49 亿的票落库 14.75 亿 → floatMvFloor=30
    把真大盘股系统性误剔(当日 56 只, 其中 3 只其它门槛全过)。"""
    _stub_kpl(monkeypatch)
    fb = asnap._fetch_kpl_fallback()
    # 委买榜路径
    seal = fb["600000"]
    assert seal["float_mv"] == 0, \
        "float_mv = 流通市值(f21), 开盘啦给的不是这个口径 → 必须留 0"
    assert seal["free_mv"] == pytest.approx(2.0e10), "实际流通应落在 free_mv"
    assert seal["bid_amt"] == pytest.approx(500.0), "竞价额元 → 万元(老口径不变)"
    # 爆量榜路径(同样必须修, 否则漏网)
    boom = fb["600001"]
    assert boom["float_mv"] == 0
    assert boom["free_mv"] == pytest.approx(1.0e10)
    assert boom["bid_amt"] == pytest.approx(300.0)


# ==================== 缺陷 A2/A3: 缓存不得自我劣化 ====================
def test_fill_skips_rows_without_float_mv(monkeypatch):
    """A2: 拿不到流通市值的行不写缓存(旧代码 REPLACE 成 NULL 会冲掉旧真值)"""
    monkeypatch.setattr(mv_cache, "enabled", lambda: False)     # 腾讯不参与
    code = _c(1)
    raw = {code: {"bid_change": 1.0, "bid_amt": 10.0, "name": "无市值",
                  "float_mv": 0, "free_mv": 1.5e9, "board": ""}}
    st = mv_cache.fill(raw, date=_D)
    assert st["saved"] == 0, "float_mv 缺失的行不得写缓存(会用 NULL 覆盖旧真值)"
    assert mv_cache.lookup([code], date=_D) == {}


def test_fill_does_not_null_out_known_mv(monkeypatch):
    """A2 补强: 缓存里已有的真值, 经一轮 fill 后必须还在"""
    monkeypatch.setattr(mv_cache, "enabled", lambda: False)
    code = _c(2)
    mv_cache.save(_D, {code: {"name": "真值", "float_mv": 4.907e9,
                              "free_mv": 0, "board": "", "src": "em"}})
    # 模拟 9_25 兜底行: float_mv 未知, 只有开盘啦给的"实际流通"
    raw = {code: {"bid_change": 1.0, "bid_amt": 10.0, "name": "真值",
                  "float_mv": 0, "free_mv": 1.475e9, "board": ""}}
    mv_cache.fill(raw, date=_D)
    got = mv_cache.lookup([code], date=_D)
    assert got[code]["float_mv"] == pytest.approx(4.907e9), \
        "真值不得被 NULL / 自由流通值覆盖"


def test_fill_marks_src_precisely(monkeypatch):
    """A3: 缓存补的值标 cache:<来源日期>, 不得伪装成 em"""
    monkeypatch.setattr(mv_cache, "enabled", lambda: False)
    code = _c(3)
    mv_cache.save(_D_NEAR, {code: {"name": "缓存丙", "float_mv": 6.6e9,
                                   "free_mv": 0, "board": "", "src": "em"}})
    raw = {code: {"bid_change": 1.0, "bid_amt": 1.0, "name": "",
                  "float_mv": 0, "free_mv": 0, "board": ""}}
    mv_cache.fill(raw, date=_D)
    got = mv_cache.lookup([code], date=_D)
    assert got[code]["src"].startswith("cache:"), \
        "缓存来源必须可辨识(9/17 因标成 em 误判为东财数据本身有问题)"


# ==================== 缺陷 B: 粗筛不得误杀"市值未知" ====================
FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "bidLt": 0, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000,
}


def _qrow(code="600000", **kw):
    base = dict(code=code, name="测试", bid_change=3.0, bid_vol=4.8e6,
                warn_type=2, float_mv=55e8, yesterday_change=2.0, price=10.5,
                bid_amt=5.0e7, prev_close=10.15, vol=4.8e6)
    base.update(kw)
    return QuoteRow(**base)


def test_coarse_filter_passes_unknown_mv():
    """B1: 市值未知(0/None) → 放行给精筛; 粗筛在补丁源之前, 此时判等于用
    '行情源可用性' 决定名单(9/17 东财全挂时整批被误杀)"""
    rows = [_qrow("600000", float_mv=0), _qrow("600001", float_mv=None)]
    codes = pf.coarse_filter(rows, dict(FULL))
    assert "600000" in codes and "600001" in codes


def test_coarse_filter_still_drops_small_mv():
    """B1 反向: 市值**已知且偏小** → 照常剔除(放行只针对未知, 不是放宽门槛)"""
    rows = [_qrow("600000", float_mv=14.75e8),      # 14.75 亿 < 30
            _qrow("600001", float_mv=49.07e8)]      # 49.07 亿 ✅
    codes = pf.coarse_filter(rows, dict(FULL))
    assert "600000" not in codes
    assert "600001" in codes


def test_apply_filters_still_drops_unknown_mv():
    """B2: 精筛仍剔除市值未知(最终语义不变 —— 只是判定推迟到有真值的那一刻)"""
    r = _qrow("600000", float_mv=0)
    it = ScoredRow(row=r, score=compute_score(r, scorer.get_scoring_cfg()))
    out = pf.apply_filters([it], dict(FULL))
    assert "mv_floor" in out.stats, "市值未知仍应记为 mv_floor 剔除"
    assert out.kept == []


# ==================== 缺陷 B3: 候选池与 picker.filter 同口径 ====================
def _snap_f(overrides=None):
    f = scorer.validate_filters({
        "stSuspend": ["0"], "limitUp": ["0"], "markets": ["hs,cyb,kcb"],
        "bidGt": ["7"], "floatMvFloor": ["30"], "floatMvGt": ["1000"],
        "priceGt": ["300"], "bidAmtFloor": ["3000"],
    })
    if overrides:
        f.update(overrides)
    return f


def _snap_row(code, float_mv_yi, free_mv_yi):
    return {code: {"name": "测试", "bid_change": 3.0, "bid_amt": 5000.0,
                   "float_mv": float_mv_yi * 1e8, "free_mv": free_mv_yi * 1e8,
                   "board": ""}}


def test_snapshot_candidate_prefers_free_mv():
    """B3: 门槛口径 = 自由流通市值(2026-09-20 主人拍板「所有流通市值改自由流通市值」)

    49.07 亿流通 / 14.75 亿自由流通 → 按**自由流通**判定 → 14.75 < floor=30 → 剔除。
    ★ 口径反转说明: v4.11.28(2026-09-18) 曾把此处定为"只看 float_mv", 理由是
      "free_mv 更小, 拿它判下限会误杀真大盘股"(9/17 华瓷股份 49 亿 → 14.75 亿被剔)。
      2026-09-20 主人明确拍板统一改自由流通口径 —— 这是**产品口径决策**, 不是 bug 修复:
      自由流通市值反映真实可流通筹码, 与本系统"竞价换手率/抢筹强度"口径一致。
      改口径后门槛的实际效果 = 更严格(等效收紧了 30 亿下限), 属于预期行为。
    """
    rows = _snap_row("600000", 49.07, 14.75)
    assert "600000" not in _snapshot_candidate_codes(rows, _snap_f(), set())
    # 自由流通 49.07 亿 → 达标
    rows2 = _snap_row("600009", 80.0, 49.07)
    assert "600009" in _snapshot_candidate_codes(rows2, _snap_f(), set())


def test_snapshot_candidate_falls_back_to_float_mv():
    """B3-b: free_mv 缺失 → 回退 float_mv(不因换源而整批丢票)"""
    # float_mv 49.07 亿, free_mv=0 → 回退 float_mv 判定 → 通过
    assert "600001" in _snapshot_candidate_codes(
        _snap_row("600001", 49.07, 0), _snap_f(), set())
    # 两者皆 0(未知) → 放行, 由 downstream 补丁/精筛定夺
    assert "600002" in _snapshot_candidate_codes(
        _snap_row("600002", 0, 0), _snap_f(), set())


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
