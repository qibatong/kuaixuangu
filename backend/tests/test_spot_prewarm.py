# -*- coding: utf-8 -*-
"""spotMap 预热(2026-09-04)测试: 窗口判定 / 预热 fs 集合 / 线程启动。

背景: 9:30 后 refresh 直读命中后响应仍需 spotMap 覆盖实时行情, spotMap 缓存 TTL=60s
到期瞬间的请求锁内同步拉全市场(东财封禁期=腾讯 5556 只 1-4.6s) → refresh 秒级长尾。
预热线程每 40s 主动刷新 → 请求永远命中缓存。本测试不打网络, 只验证调度纯函数。
"""
import calendar
import threading
import time

import pytest

from app.services import fetcher, scorer

# conftest mock_data_source 会把 start_spot_prewarm 桩成 no-op(startup 不启线程防测试
# 进程真拉网络); 线程启动行为测试需要真函数, 在收集期(桩生效前)保存引用。
_REAL_START_PREWARM = fetcher.start_spot_prewarm


@pytest.fixture(autouse=True)
def _clear_client_cookies():
    """覆盖 conftest 同名 autouse: 本文件全是纯函数测试, 不需要 TestClient。
    避免实例化 client → 触发 app startup(yday/spot 预热线程)在测试进程产生真实网络拉取。"""
    yield

# 北京时间示例(UTC+8): 2026-09-04(周五)/2026-09-06(周日) 各时刻
def _bj_ts(day, hh, mm):
    """day: (y,m,d) 北京时间日期; 返回对应 epoch 秒"""
    y, m, d = day
    # timegm 是 UTC; 北京 = UTC+8, 故先按 UTC 时刻组再减 8h 得到"北京时钟指向该时刻"的 epoch
    return calendar.timegm((y, m, d, hh, mm, 0, 0, 0, 0)) - 8 * 3600


TUE = (2026, 9, 8)     # 周二(工作日)
SAT = (2026, 9, 5)     # 周六


class TestSpotPrewarmActive:
    def test_workday_930_active(self):
        """工作日 9:30 → 预热窗口内"""
        assert fetcher.spot_prewarm_active(_bj_ts(TUE, 9, 30)) is True

    def test_workday_midday_active(self):
        """工作日 12:00 → 窗口内"""
        assert fetcher.spot_prewarm_active(_bj_ts(TUE, 12, 0)) is True

    def test_workday_1525_active(self):
        """工作日 15:05 内 → 窗口内(收盘后 5 分钟余量)"""
        assert fetcher.spot_prewarm_active(_bj_ts(TUE, 15, 5)) is True

    def test_before_926_inactive(self):
        """工作日 9:10 → 未到窗口(9:26 起, 错开 9:25 快照采集)"""
        assert fetcher.spot_prewarm_active(_bj_ts(TUE, 9, 10)) is False

    def test_after_1505_inactive(self):
        """工作日 15:10 → 窗口已过"""
        assert fetcher.spot_prewarm_active(_bj_ts(TUE, 15, 10)) is False

    def test_weekend_inactive(self):
        """周六 10:00 → 非交易日不预热"""
        assert fetcher.spot_prewarm_active(_bj_ts(SAT, 10, 0)) is False


class TestSpotPrewarmFs:
    def _clear(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_quote_map_cache", {})

    def test_fs_includes_default_full_market(self, monkeypatch):
        """无既有缓存时 → 预热集合含默认全市场(hs+cyb+kcb)"""
        self._clear(monkeypatch)
        fs_set = fetcher._spot_prewarm_fs_set()
        assert scorer.market_fs(["hs", "cyb", "kcb"]) in fs_set
        assert len(fs_set) == 1

    def test_fs_includes_existing_keys(self, monkeypatch):
        """缓存已有北交所等组合 key → 预热集合一并覆盖"""
        self._clear(monkeypatch)
        fetcher._quote_map_cache["m:0+t:81+m:1+t:23"] = {"raw": [], "ts": 0.0}
        fs_set = fetcher._spot_prewarm_fs_set()
        assert "m:0+t:81+m:1+t:23" in fs_set
        assert scorer.market_fs(["hs", "cyb", "kcb"]) in fs_set


class TestSpotPrewarmThread:
    def test_start_launches_daemon_thread(self):
        """start_spot_prewarm → 守护线程存活且命名 spot-prewarm。
        调用收集期保存的真函数(conftest 会话桩下仍验证真实启动路径);
        once 已被会话桩空转, 线程不会拉网络。"""
        before = [t.name for t in threading.enumerate()]
        _REAL_START_PREWARM()
        found = [t for t in threading.enumerate()
                 if t.name == "spot-prewarm" and t not in before]
        assert found and found[0].is_alive() and found[0].daemon
