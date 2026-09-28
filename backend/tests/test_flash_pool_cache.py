# -*- coding: utf-8 -*-
"""`_flash_pool` 缓存化 的仓库级单测（2026-09-28）

背景: 「龙虎榜」实时路径里 `fill_reason_from_pool` 单步 119ms —— 每请求都重拉一次
今日涨停池(选股宝 flash), 而它只用到 `reason` 一个字段; 全站 16 处调用点绝大多数也在裸打上游。

★ 本文件不打网络、不碰真库: 缓存层与原始请求全部替换。
★ 硬编码日期 + 固定「今天」, 不依赖运行时刻。
"""
import sys

sys.path.insert(0, "backend")

from app.services import kpl                                  # noqa: E402

TODAY = "2026-09-28"
HIST = "2026-09-24"


def _capture(monkeypatch, raw_ret):
    """替换缓存层与原始请求, 记录 (key, ttl, loader 的返回值)"""
    seen = {}

    def fake_cached(key, ttl, loader):
        seen["key"] = key
        seen["ttl"] = ttl
        seen["loader_ret"] = loader()
        return seen["loader_ret"]

    monkeypatch.setattr(kpl, "_cached", fake_cached)
    monkeypatch.setattr(kpl, "_flash_pool_raw", lambda p, d=None: raw_ret)
    return seen


# =========================================================================== #
# 一、缓存键与 TTL
# =========================================================================== #
def test_today_pool_ttl_60s(monkeypatch):
    """今日池(不传日期) 60s —— 与既有 real_limit_days 同口径"""
    monkeypatch.setattr(kpl, "_bj_today", lambda: TODAY)
    seen = _capture(monkeypatch, [{"code": "600000"}])
    out = kpl._flash_pool("limit_up_pool", None)
    assert seen["ttl"] == 60
    assert seen["key"] == "flash_limit_up_pool_today"
    assert out == [{"code": "600000"}]


def test_today_pool_explicit_date_also_60s(monkeypatch):
    """显式传今天也按今日池处理(60s), 不能误判成不可变历史池"""
    monkeypatch.setattr(kpl, "_bj_today", lambda: TODAY)
    seen = _capture(monkeypatch, [{"code": "600000"}])
    kpl._flash_pool("limit_up_pool", TODAY)
    assert seen["ttl"] == 60


def test_historical_pool_ttl_6h(monkeypatch):
    """历史池不可变 → 长 TTL, 且键按日期区分(不得与今日池互相污染)"""
    monkeypatch.setattr(kpl, "_bj_today", lambda: TODAY)
    seen = _capture(monkeypatch, [{"code": "600000"}])
    kpl._flash_pool("limit_up_pool", HIST)
    assert seen["ttl"] == 6 * 3600
    assert seen["key"] == "flash_limit_up_pool_" + HIST


def test_different_pools_use_different_keys(monkeypatch):
    """不同池(涨停/炸板)不得共用键"""
    monkeypatch.setattr(kpl, "_bj_today", lambda: TODAY)
    seen = _capture(monkeypatch, [])
    kpl._flash_pool("limit_up_broken", TODAY)
    k1 = seen["key"]
    kpl._flash_pool("limit_up_pool", TODAY)
    assert k1 != seen["key"]


# =========================================================================== #
# 二、★ 分界: 「有效空池」要缓存, 「失败」不要
# =========================================================================== #
def test_empty_pool_is_cached(monkeypatch):
    """★ 有效空池必须照常进缓存(loader 返回 [] 而非 None)。

    理由: 该接口当日数据 15:50 后才发布(见 kpl.py 顶部注释), 盘中返回空是**合法**结果。
    若把它当作失败不缓存, 盘中每个请求都会白打一次上游 —— 那正是本次要治的问题。
    """
    monkeypatch.setattr(kpl, "_bj_today", lambda: TODAY)
    seen = _capture(monkeypatch, [])
    assert kpl._flash_pool("limit_up_pool", None) == []
    assert seen["loader_ret"] == []
    assert seen["loader_ret"] is not None, "空池不得返回 None(否则不会被缓存)"


def test_failure_is_not_cached(monkeypatch):
    """★ 失败必须返回 None(缓存层不缓存 → 下次重试), 但对调用方仍表现为 []"""
    monkeypatch.setattr(kpl, "_bj_today", lambda: TODAY)
    seen = _capture(monkeypatch, None)
    assert kpl._flash_pool("limit_up_pool", None) == [], "返回值契约必须与改造前一致"
    assert seen["loader_ret"] is None, "失败必须为 None 才能真正不缓存"


# =========================================================================== #
# 三、行为回归
# =========================================================================== #
def test_fill_reason_from_pool_unchanged(monkeypatch):
    """`fill_reason_from_pool` 语义不变: 空缺的补上 / 已有不覆盖 / 池外不动"""
    monkeypatch.setattr(kpl, "_flash_pool",
                        lambda p, d=None: [{"code": "600000", "reason": "算力"},
                                           {"code": "600001", "reason": "机器人"}])
    lst = [{"code": "600000", "reason": ""},
           {"code": "600001", "reason": "已存在"},
           {"code": "600002"}]
    kpl.fill_reason_from_pool(lst, None)
    assert lst[0]["reason"] == "算力", "空缺的 reason 应被补上"
    assert lst[1]["reason"] == "已存在", "已有 reason 不得被覆盖"
    assert "reason" not in lst[2], "池外股票不应被塞入 reason"


def test_fill_reason_from_pool_tolerates_empty_pool(monkeypatch):
    """池为空(盘中常态)时原样返回, 不抛异常"""
    monkeypatch.setattr(kpl, "_flash_pool", lambda p, d=None: [])
    lst = [{"code": "600000", "reason": ""}]
    assert kpl.fill_reason_from_pool(lst, None) == lst
    assert lst[0]["reason"] == ""
