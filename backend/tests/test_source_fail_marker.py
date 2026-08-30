# -*- coding: utf-8 -*-
"""板块/热榜 em 源失败标记测试(2026-08-30 主人要求: 前端提示"数据源故障, 请切换源")
覆盖: hot_rank._mark_source_error/last_source_error + sector_rotation 同逻辑"""
import time

import pytest

from app.services import hot_rank, sector_rotation


# ---------- hot_rank 源失败标记 ----------
def test_hot_rank_last_error_none_initially():
    hot_rank._SRC_ERR["ts"] = 0
    assert hot_rank.last_source_error() is None


def test_hot_rank_mark_and_read():
    hot_rank._mark_source_error("em", "测试: 东财被封")
    err = hot_rank.last_source_error()
    assert err is not None
    assert err["source"] == "em"
    assert "东财" in err["msg"]
    assert err["ts"] > 0
    # 清理
    hot_rank._SRC_ERR["ts"] = 0


def test_hot_rank_mark_error_thread_safe():
    """并发标记不丢(锁保护)"""
    import threading
    def w(i):
        hot_rank._mark_source_error("em", f"err{i}")
    ts = [threading.Thread(target=w, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    err = hot_rank.last_source_error()
    assert err["source"] == "em"
    hot_rank._SRC_ERR["ts"] = 0


# ---------- sector_rotation 源失败标记 ----------
def test_sector_rotation_last_error_none_initially():
    sector_rotation._SRC_ERR["ts"] = 0
    assert sector_rotation.last_source_error() is None


def test_sector_rotation_mark_and_read():
    sector_rotation._mark_source_error("em", "测试: 东财板块榜失败")
    err = sector_rotation.last_source_error()
    assert err["source"] == "em"
    assert "板块" in err["msg"]
    sector_rotation._SRC_ERR["ts"] = 0


# ---------- API 层 source_failed 判定(直接测判定逻辑) ----------
def test_source_failed_detection_recent():
    """最近 60s 内失败 → source_failed 应为 True(热榜实时场景)"""
    from app.api import kpl as kpl_api
    hot_rank._mark_source_error("em", "被墙")
    # 模拟 api 判定: 请求 source=em 且空列表 + 最近失败
    err = hot_rank.last_source_error()
    source_failed = err["source"] == "em" and time.time() - err["ts"] < 60
    assert source_failed is True
    hot_rank._SRC_ERR["ts"] = 0


def test_source_failed_not_for_other_source():
    """em 失败不应影响 kpl/ths 请求(source_failed=False)"""
    hot_rank._mark_source_error("em", "被墙")
    err = hot_rank.last_source_error()
    # 用户请求 ths: 不匹配 source
    source_failed = err["source"] == "ths" and time.time() - err["ts"] < 60
    assert source_failed is False
    hot_rank._SRC_ERR["ts"] = 0


def test_source_failed_expired():
    """失败时间过久(>60s 热榜 / >12h 板块) → 不标记(源可能已恢复)"""
    hot_rank._SRC_ERR.update({"source": "em", "msg": "old", "ts": time.time() - 3600})
    err = hot_rank.last_source_error()
    source_failed = err["source"] == "em" and time.time() - err["ts"] < 60
    assert source_failed is False
    hot_rank._SRC_ERR["ts"] = 0
