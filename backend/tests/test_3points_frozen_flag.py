# -*- coding: utf-8 -*-
"""B2: 三时点榜「定格状态」元信息(frozen / freezeAt / today)单测 —— 2026-09-29。

为什么要单测:
  * 「9:25 定格没定格」这个判据**只能来自后端**, 且必须与选股闸门**同源**
    (`auction_snapshot.has_today_snapshot`)。前端一旦按 09:26:30 这类固定时刻自己猜,
    就会重演 2026-09-16「两个用户拿到昨天名单」的事故形态。
  * `freezeAt` 必须由常量 `_BID25_FREEZE_SEC` **推导** —— 接口里不许再写一份字面量时刻,
    否则将来定格时刻提前/推后时, 提示文案会静默说谎(而且没人会发现)。
  * 判据必须落在**实际服务的那一天**(回退到历史交易日时应当算"已定格")。
纯逻辑, 不打网络、不查库。
"""
import time

from app.api import stats
from app.services import auction_snapshot as asnap


def test_frozen_true_when_snapshot_exists(monkeypatch):
    """当日已有 9_25 定格行 ⇒ frozen=True(前端不显示占位)。"""
    monkeypatch.setattr(asnap, "has_today_snapshot", lambda d=None: True)
    assert stats._threepoints_meta("2026-09-29")["frozen"] is True


def test_frozen_false_and_uses_served_date(monkeypatch):
    """当日无定格 ⇒ frozen=False; 且判据必须用**实际服务的日期**(回退日也算已定格)。"""
    seen = {}

    def _h(d=None):
        seen["d"] = d
        return False

    monkeypatch.setattr(asnap, "has_today_snapshot", _h)
    assert stats._threepoints_meta("2026-09-28")["frozen"] is False
    assert seen["d"] == "2026-09-28", "必须按 resolved(服务日期)判, 不能写死今天"


def test_frozen_goes_through_has_today_snapshot(monkeypatch):
    """判据必须**走 has_today_snapshot**(与选股闸门同一把"快照维"), 不许另起一套查询。"""
    calls = {"n": 0}

    def _h(d=None):
        calls["n"] += 1
        return True

    monkeypatch.setattr(asnap, "has_today_snapshot", _h)
    stats._threepoints_meta("2026-09-29")
    assert calls["n"] == 1


def test_freeze_at_is_derived_from_constant(monkeypatch):
    """freezeAt 随 `_BID25_FREEZE_SEC` 走(不许在接口里另写字面量)。"""
    assert stats._threepoints_meta("2026-09-29")["freezeAt"] == "09:26:30"   # 当前常量值
    monkeypatch.setattr(asnap, "_BID25_FREEZE_SEC", 9 * 3600 + 25 * 60 + 20)  # 假设提前到 09:25:20
    assert stats._threepoints_meta("2026-09-29")["freezeAt"] == "09:25:20"


def test_today_is_beijing_date():
    """today = 北京日期(项目惯例: gmtime(now + 8h))。"""
    want = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    assert stats._threepoints_meta("2026-09-29")["today"] == want


def test_endpoint_payload_carries_the_meta():
    """接口 _compute_rows 的返回体里必须真的带上这三个字段(防"写了函数没接上")。"""
    import inspect
    src = inspect.getsource(stats.api_stats_bid_snapshot_3points)
    assert "_threepoints_meta(resolved)" in src
