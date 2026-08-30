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
