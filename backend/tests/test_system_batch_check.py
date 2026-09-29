# -*- coding: utf-8 -*-
"""system_batch 检查告警功能测试(2026-08-30)"""
import time

import pytest

from app.services import system_batch, aipick_scheduler
from app.services.cache_store import store


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def test_system_batch_check_when_exists(monkeypatch, client):
    """今日已落库 system batch → 检查通过, 不告警"""
    today = _today()
    # 清理当日 setnx 锁(测试隔离)
    store.delete("aipick:system_batch_check:" + today)
    notified = []
    monkeypatch.setattr(system_batch, "_has_today_system_batch", lambda d, tp: True)
    monkeypatch.setattr(aipick_scheduler.store, "setnx",
                        lambda *a, **k: True)
    monkeypatch.setattr("app.services.notify.send_text",
                        lambda text, title=None: notified.append(text))
    aipick_scheduler._check_system_batch()
    assert notified == [], "已落库时不应告警"


def test_system_batch_check_when_missing(monkeypatch, client):
    """今日未落库 → 触发补跑 + 告警"""
    today = _today()
    store.delete("aipick:system_batch_check:" + today)
    notified = []
    ran = []
    monkeypatch.setattr(system_batch, "_has_today_system_batch", lambda d, tp: False)
    monkeypatch.setattr(system_batch, "run_system_batch", lambda tp: ran.append(tp))
    monkeypatch.setattr(aipick_scheduler.store, "setnx",
                        lambda *a, **k: True)
    monkeypatch.setattr("app.services.notify.send_text",
                        lambda text, title=None: notified.append(text))
    aipick_scheduler._check_system_batch()
    assert ran == ["9_25"], "缺失时应补跑"
    assert len(notified) == 1, "缺失时应告警"
    assert "系统自动选股批次未生成" in notified[0]


def test_system_batch_check_setnx_dedup(monkeypatch, client):
    """跨进程锁: 当日只检查一次(第二次直接跳过)"""
    today = _today()
    store.setnx("aipick:system_batch_check:" + today, "1", 12 * 3600)
    called = []
    monkeypatch.setattr(system_batch, "_has_today_system_batch",
                        lambda d, tp: called.append(1) or False)
    aipick_scheduler._check_system_batch()
    assert called == [], "setnx 已占用时应直接返回, 不重复检查"
    store.delete("aipick:system_batch_check:" + today)


# =====================================================================
# 2026-08-31 修复: system batch 过滤必须与首页左视图一致
# (原 DEFAULT_FILTER 键名与 validate_filters 不匹配 → 落回后端默认 stSuspend=True/
#  floatMvGt=100/priceGt=30 → 中小盘小票池≈aipick候选池, 用户误判"锁了AI预测数据")
# =====================================================================
def test_system_filter_keys_match_validate_filters():
    """_system_filter 产出的键必须被 scorer.validate_filters 正确识别

    ⚠️ 2026-09-24 v4.11.46 补: 本用例的断言值取自**全局默认**, 而非写死字面量 ——
       原来写死 1000.0/300.0 只在"内置默认 == 线上默认"时成立; 线上管理员把
       floatMvGt 调成 500 后就名不副实了。更要紧的是它当年**漏了 scoreFloor**:
       validate_filters 对缺键有默认值(50), 所以"键是否存在"在输出上根本看不出来
       —— 详见 tests/test_system_filter_parity.py 的模块 docstring。
    """
    from app.api import admin
    from app.services import scorer
    dflt = admin.get_default_filters()
    f_raw = system_batch._system_filter()
    f = scorer.validate_filters({
        k: [str(v)]
        for k, v in f_raw.items() if k != "markets"})
    # 关键差异点: 必须与首页左视图(管理员全局默认)一致, 而非后端 validate_filters 默认
    assert f["stSuspend"] is dflt["stSuspend"], "必须剔除 ST(首页左视图语义: false=剔除)"
    assert f["floatMvGt"] == dflt["floatMvGt"], "市值上限须随全局默认(首页左视图大票策略)"
    assert f["priceGt"] == dflt["priceGt"], "股价上限须随全局默认(首页左视图大票策略)"
    assert f["bidAmtFloor"] == dflt["bidAmtFloor"], "竞价金额下限须随全局默认"
    assert f["bidGt"] == dflt["bidGt"]
    # ★ v4.11.46: scoreFloor 必须**显式存在**于系统口径(修复前缺此键 → 吃 lock 兜底 50)
    assert "scoreFloor" in f_raw, "系统口径缺 scoreFloor 会导致门槛不受管理员控制"
    assert f["scoreFloor"] == dflt["scoreFloor"]
    assert f["markets"] == ["hs", "cyb", "kcb", "bj"]      # 2026-09-29 北交所纳入


def test_system_filter_merges_admin_defaults(monkeypatch):
    """管理员后台修改 default_filters 后, system batch 自动跟随(与首页左视图同步)

    2026-09-24 v4.11.46: patch 目标从 system_batch.settings 改为
    **filter_defaults.settings** —— 后者是唯一真相源; 前者已不再自己读 settings,
    继续 patch 它会静默失效(测试照样"绿", 但什么都没测到)。
    """
    from app.services import filter_defaults
    monkeypatch.setattr(filter_defaults.settings, "get",
                        lambda key, default=None: {"floatMvGt": 800.0, "bidAmtFloor": 2000.0}
                        if key == "default_filters" else default)
    f = system_batch._system_filter()
    assert f["floatMvGt"] == 800.0
    assert f["bidAmtFloor"] == 2000.0
    # 未配置的键保持内置默认
    assert f["stSuspend"] is False
    assert f["priceGt"] == 300.0


def test_system_filter_filters_out_st_suspend():
    """stSuspend=False 时 ST/停牌应被过滤(修复前康佳这类 *ST 混入批次)

    2026-09-11: 老链路 scorer.apply_filters 已退役, 改测 picker/filter.apply_filters
    (唯一过滤实现); 过滤参数仍由 system_batch 的系统默认条件经 validate_filters 产生,
    保证"系统批次的默认条件真的能剔 ST"这条接线断言不丢。
    """
    from app.services import scorer, system_batch
    from app.services.picker.contract import QuoteRow
    from app.services.picker.filter import FilterContext
    from app.services.picker.filter import apply_filters as pfilter
    from app.services.picker.score import ScoredRow, compute_score

    f_raw = system_batch._system_filter()
    q = {k: [str(v)] for k, v in f_raw.items() if k != "markets"}
    q["markets"] = [",".join(f_raw["markets"])]
    f = scorer.validate_filters(q)

    def _mk(code, name):
        r = QuoteRow(code=code, name=name, bid_change=4.58, bid_vol=4.8e6, warn_type=2,
                     float_mv=50e8, yesterday_change=2.0, price=20.0, bid_amt=8.0e7,
                     prev_close=19.0, vol=4.8e6)
        return ScoredRow(row=r, score=compute_score(r, scorer.get_scoring_cfg()))

    rows = [_mk("600001", "*ST康佳A"), _mk("600002", "沃特股份")]
    out = pfilter(rows, f, FilterContext())
    names = [it.row.name for it in out.kept]
    assert "*ST康佳A" not in names, "ST 股不应进入 system batch"
    assert "沃特股份" in names
    assert out.stats.get("st") == 1
