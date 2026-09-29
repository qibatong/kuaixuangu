# -*- coding: utf-8 -*-
"""定格提速的两项改动单测 —— 2026-09-29。

背景(2026-09-29 生产实测, 定格落库 09:26:53):
    09:26:30  定格首采时刻到了…但要等下一轮 **10s 轮询**才动手
    09:26:37  真正开始采(白等 7 秒)          ← 改动① 目标
    09:26:37~09:26:50  **猫爪 fundflow_kp(竞价净额)全市场 3 片 = 13 秒**  ← 改动② 目标
    09:26:53  落库(采集 16.6s)
本文件锁住这两项的**意图**与**边界**:
  ① 定格前后 `_SCHED_FAST_WIN` 秒内调度间隔收到 1s(到点即采), 其余仍是 10s(不给守卫加频次);
  ② 采集时**不取**竞价净额(该列评分权重为 0, 见 bid_strength.py:40-44), 改由补采通道在定格后补;
  ③ 放宽的只是"净额补采窗"(定格后仍要有轮次), **参与评分的列不受影响**:
     抢筹轮采上界仍 = 定格时刻, 定格首采时刻本身未变。
"""
import pytest

from app.services import auction_snapshot as A
from app.services import meoz_client


# ---------------- ① 到点即采 ----------------

def test_fast_tick_only_around_freeze():
    """定格 ±30s 内 = 1s; 再远 = 10s(常规粒度, 不给循环守卫加 10 倍频次)。"""
    fz = A._BID25_FREEZE_SEC
    assert A._sched_sleep_sec(fz) == 1
    assert A._sched_sleep_sec(fz - A._SCHED_FAST_WIN) == 1
    assert A._sched_sleep_sec(fz + A._SCHED_FAST_WIN) == 1
    assert A._sched_sleep_sec(fz - A._SCHED_FAST_WIN - 1) == A._SCHED_TICK_SEC
    assert A._sched_sleep_sec(fz + A._SCHED_FAST_WIN + 1) == A._SCHED_TICK_SEC
    # 竞价早段(9:20)与盘中都不该加密
    assert A._sched_sleep_sec(9 * 60 + 20) == A._SCHED_TICK_SEC
    assert A._sched_sleep_sec(10 * 60) == A._SCHED_TICK_SEC


def test_fast_tick_lets_loop_fire_on_time():
    """关键: 定格前一刻的间隔必须能让循环**在定格秒内**醒过来(而不是 10s 后才醒)。"""
    fz = A._BID25_FREEZE_SEC
    for now in (fz - 25, fz - 10, fz - 1):
        assert now + A._sched_sleep_sec(now) <= fz + 1, \
            "定格前的等待必须保证在定格秒(±1s)内醒来"


def test_fast_win_is_short():
    """加密窗口别顺手开太大(否则循环里的守卫被无谓加密)。"""
    assert 5 <= A._SCHED_FAST_WIN <= 60


# ---------------- ② 采集不取竞价净额 ----------------

def test_net_fetch_disabled_in_snapshot_by_default():
    assert A._FETCH_NET_IN_SNAPSHOT is False


def test_merge_meoz_does_not_call_fundflow(monkeypatch):
    """行为断言(不是只看常量): `_merge_meoz` 在默认开关下**一次都不打** fundflow_kp。"""
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "screening_map",
                        lambda *a, **k: {"600001": {"tradedate": "", "auc_amt": 1e6,
                                                    "auc_pct_chg": 5.0, "name": "X"}})
    monkeypatch.setattr(meoz_client, "valuation_map", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "fundflow_map",
                        lambda *a, **k: pytest.fail("默认开关下采集不得取竞价净额(它在定格关键路径上)"))

    stats = A._merge_meoz({})
    assert stats["ff"] == 0, "没取净额 ⇒ 统计里的净额非零计数必须为 0"


def test_net_fetch_switch_re_enables_old_behavior(monkeypatch):
    """开关置 True 时应恢复旧行为(采集时取净额) —— 日后恢复该列评分权重要用。"""
    monkeypatch.setattr(A, "_FETCH_NET_IN_SNAPSHOT", True)
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "screening_map",
                        lambda *a, **k: {"600001": {"tradedate": "", "auc_amt": 1e6,
                                                    "auc_pct_chg": 5.0, "name": "X"}})
    monkeypatch.setattr(meoz_client, "valuation_map", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: {})
    seen = {}

    def _ff(codes, *a, **k):
        seen["called"] = True
        return {}

    monkeypatch.setattr(meoz_client, "fundflow_map", _ff)
    A._merge_meoz({})
    assert seen.get("called") is True


# ---------------- ③ 边界: 只放宽"净额补采", 评分列不受影响 ----------------

def test_netfill_window_extends_past_freeze_for_post_rounds():
    """净额补采窗必须**晚于定格** —— 否则定格落库(≈09:26:3x)后就没有轮次补净额了。"""
    assert A.NETFILL_END_SEC == A._NETFILL_HARD_END_SEC
    assert A._NETFILL_HARD_END_SEC == 9 * 3600 + 27 * 60 + 30       # 09:27:30 = 定格 + 60s
    assert A._NETFILL_HARD_END_SEC > A._BID25_FREEZE_SEC, "必须晚于定格, 否则净额永远补不上"
    assert A.NETFILL_END_SEC <= 9 * 3600 + 30 * 60, "仍必须早于 9:30 开盘(不得影响开盘后数据)"
    assert A.NETFILL_START_SEC < A.NETFILL_END_SEC


def test_scoring_fields_still_freeze_final():
    """★ 铁律不变: 参与评分的列仍"定格即终值" —— 抢筹轮采上界与定格首采时刻都没动。"""
    assert A._BID25_FREEZE_SEC == 9 * 3600 + 26 * 60 + 30          # 定格首采仍是 09:26:30
    assert A._BID_QC_UNTIL_SEC <= A._BID25_FREEZE_SEC, "抢筹轮采不得晚于定格"
    assert A._BID25_RETRY_UNTIL == A._BID25_FREEZE_SEC, "仍是单枪定格(无重采空间)"
