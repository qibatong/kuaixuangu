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
    """_system_filter 产出的键必须被 scorer.validate_filters 正确识别"""
    from app.services import scorer
    f_raw = system_batch._system_filter()
    f = scorer.validate_filters({
        k: [str(v)]
        for k, v in f_raw.items() if k != "markets"})
    # 关键差异点: 必须与首页左视图(管理员全局默认)一致, 而非后端 validate_filters 默认
    assert f["stSuspend"] is False, "必须剔除 ST(首页左视图语义: false=剔除)"
    assert f["floatMvGt"] == 1000.0, "市值上限应为 1000 亿(首页左视图大票策略)"
    assert f["priceGt"] == 300.0, "股价上限应为 300 元(首页左视图大票策略)"
    assert f["bidAmtFloor"] == 1000.0, "竞价金额下限 1000 万(管理员全局默认)"
    assert f["bidGt"] == 7.0
    assert f["markets"] == ["hs", "cyb", "kcb"]


def test_system_filter_merges_admin_defaults(monkeypatch):
    """管理员后台修改 default_filters 后, system batch 自动跟随(与首页左视图同步)"""
    monkeypatch.setattr(system_batch.settings, "get",
                        lambda key, default=None: {"floatMvGt": 800.0, "bidAmtFloor": 2000.0}
                        if key == "default_filters" else default)
    f = system_batch._system_filter()
    assert f["floatMvGt"] == 800.0
    assert f["bidAmtFloor"] == 2000.0
    # 未配置的键保持内置默认
    assert f["stSuspend"] is False
    assert f["priceGt"] == 300.0


def test_system_filter_filters_out_st_suspend(monkeypatch):
    """stSuspend=False 时 ST/停牌应被过滤(修复前康佳这类 *ST 混入批次)"""
    from app.services import scorer
    f_raw = system_batch._system_filter()
    f = scorer.validate_filters({
        k: [str(v)]
        for k, v in f_raw.items() if k != "markets"})
    items = [
        {"name": "*ST康佳A", "bidChange": 0.0, "probability": 80, "confidence": 80,
         "circulationMV": 50, "price": 5, "bidAmt": 5000, "_raw": {}},
        {"name": "沃特股份", "bidChange": 4.58, "probability": 80, "confidence": 80,
         "circulationMV": 50, "price": 20, "bidAmt": 8000, "_raw": {}},
    ]
    from app.services.scorer import is_st
    monkeypatch.setattr(scorer, "is_suspended", lambda raw: False)
    ok = scorer.apply_filters(items, f)
    names = [x["name"] for x in ok]
    assert "*ST康佳A" not in names, "ST 股不应进入 system batch"
    assert "沃特股份" in names
