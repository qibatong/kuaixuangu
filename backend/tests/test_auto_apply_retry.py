# -*- coding: utf-8 -*-
"""auto_apply 幂等锁缺陷回归 (2026-09-17)

## 缺陷
auction_snapshot 原先用

    store.setnx("sched:auto_apply:" + date, 1, ttl=86400)

作为 **每日一次性** 锁守卫 9:26 自动应用, 但它在 **成功之前** 就被消费:

  - `auto_apply_all_users()` 里 `_pick_result()` 无票时 `return {"error": ...}` 提前退出;
  - 锁却已经烧掉 → 9:26-9:30 剩余 ~24 轮(10s 一轮) 全部被 `setnx` 挡掉;
  - **当日系统统一批次永久缺失** → 用户刷新直读只能跨日回退到**上一个交易日**的名单。

这正是 2026-09-17 早盘「9:30 后出来的数据好像是昨天的」事故的放大部分。

## 修复
拆成两把键, 并把判据收敛到 `auto_apply.should_trigger()` 便于测试:

  - `sched:auto_apply:done:<date>` —— **成功算出非空名单后**才置, 当日幂等(防重复扇出);
  - `sched:auto_apply:try:<date>`  —— 60s **节流**(不是每日锁), 失败后仍可重试。

## 本文件覆盖
1. 三个原子操作 (should_trigger / mark_done / already_done) 的语义;
2. 失败不置 done + 节流过后可重试(核心回归);
3. 成功才置 done + 置 done 后不再触发;
4. 端到端: auto_apply_all_users 无票 → 不置 done; 有票 → 置 done。
"""
import time

import pytest

from app.services import auto_apply
from app.services.cache_store import store


_DONE = auto_apply._AUTO_APPLY_DONE_KEY
_TRY = auto_apply._AUTO_APPLY_TRY_KEY


@pytest.fixture
def bdate():
    """真实北京当日 —— auto_apply_all_users() 内部用真实当天, 保持一致"""
    g = time.gmtime(time.time() + 8 * 3600)
    d = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    store.delete(_DONE + d)
    store.delete(_TRY + d)
    yield d
    store.delete(_DONE + d)
    store.delete(_TRY + d)


def test_should_trigger_first_call_then_throttled(bdate):
    """首次可触发(并占住节流位); 节流窗口内再次调用被挡"""
    assert auto_apply.should_trigger(bdate) is True
    assert auto_apply.should_trigger(bdate) is False, "节流窗口内不应重复触发"


def test_should_trigger_retries_after_throttle_expires(bdate):
    """**核心回归**: 未成功时不置 done, 节流过期后仍能再次触发(可重试)

    旧实现用 ttl=86400 的每日锁 —— 这一条必红。
    """
    assert auto_apply.should_trigger(bdate) is True
    assert auto_apply.should_trigger(bdate) is False
    store.delete(_TRY + bdate)                     # 模拟节流自然过期(等 60s)
    assert auto_apply.should_trigger(bdate) is True, "失败后必须仍可重试"


def test_should_trigger_blocked_once_done(bdate):
    """成功置 done 后, 即使节流已过期也不再触发(防当天重复扇出)"""
    auto_apply.mark_done(bdate)
    assert auto_apply.already_done(bdate) is True
    store.delete(_TRY + bdate)                     # 节流位清掉也不该再触发
    assert auto_apply.should_trigger(bdate) is False


def test_already_done_absent_by_default(bdate):
    assert auto_apply.already_done(bdate) is False


def test_all_users_does_not_mark_done_when_no_result(bdate, monkeypatch):
    """无票(error 分支) → **不置 done** → 9:26-9:30 内还能重试"""
    monkeypatch.setattr(auto_apply, "_pick_result",
                        lambda: ([], "名单源无数据"))
    r = auto_apply.auto_apply_all_users(max_users=0)
    assert r.get("error"), r
    assert auto_apply.already_done(bdate) is False, "无票不应置 done(否则当日永不重试)"


def test_all_users_marks_done_when_result_non_empty(bdate, monkeypatch):
    """算出非空名单 → 置 done(即使候选用户为 0 也算本轮有效, 不再空转重试)"""
    monkeypatch.setattr(auto_apply, "_pick_result",
                        lambda: ([{"code": "600000", "name": "浦发银行"}], ""))
    r = auto_apply.auto_apply_all_users(max_users=0)
    assert not r.get("error"), r
    assert r["total"] == 0
    assert auto_apply.already_done(bdate) is True, "成功一轮后应置 done"


def test_full_flow_retry_then_success(bdate, monkeypatch):
    """端到端串起调用点判据: 第一轮无票(不 done, 可重试) → 第二轮有票(置 done 收工)

    对应 9/17 事故的真实时序: 9:26 首轮因行情源限流无票 —— 修复后 9:26-9:30
    的后续轮次仍能拿到名单, 而不是当天彻底没有系统批次。
    """
    state = {"n": 0}

    def _fake_pick():
        state["n"] += 1
        if state["n"] == 1:
            return [], "名单源无数据"
        return [{"code": "600000", "name": "浦发银行"}], ""

    monkeypatch.setattr(auto_apply, "_pick_result", _fake_pick)

    # 第 1 轮: 判据放行 → 跑 → 无票 → 未 done
    assert auto_apply.should_trigger(bdate) is True
    r1 = auto_apply.auto_apply_all_users(max_users=0)
    assert r1.get("error")
    assert auto_apply.already_done(bdate) is False

    # 节流窗口内: 调用点判据仍为 False(等下一轮轮询)
    assert auto_apply.should_trigger(bdate) is False

    # 节流过期 + 下一轮轮询: 判据放行 → 跑 → 有票 → done 收工
    store.delete(_TRY + bdate)
    assert auto_apply.should_trigger(bdate) is True
    r2 = auto_apply.auto_apply_all_users(max_users=0)
    assert not r2.get("error")
    assert auto_apply.already_done(bdate) is True

    # done 之后彻底收工
    store.delete(_TRY + bdate)
    assert auto_apply.should_trigger(bdate) is False
    assert state["n"] == 2, "恰好跑两轮(1 失败 + 1 成功)"
