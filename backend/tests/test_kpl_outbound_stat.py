# -*- coding: utf-8 -*-
"""开盘啦**出网埋点**（2026-10-01 竞价链路 P1-5 / 清单 1.3）

为什么要它：清单 §六 验收口径要"竞价窗口(09:15~09:28) 的**上游调用次数 / 429 条数**"，
而 journalctl 里只有接口层访问日志（`[app.main] <ip> GET <path> uid=N <status> <ms>ms`），
拿不到"一次页面请求打了几次开盘啦"。`_call` 是唯一出网口 ⇒ 在这里计数最准。

本文件钉住四件事（**全部不出网**：`_urlopen` 与信号量都被替身掉）：
  ① 成功调用计数；② 失败/429 计数（urllib 的 HTTPError.code）；③ 汇总行格式可 grep；
  ④ 环境变量 `KX_KPL_OUTBOUND_STAT=0` 可整体关闭（统计绝不能影响业务）。
"""
import json
import time

import pytest

from app.services import kpl


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _HTTPError(Exception):
    """模拟 urllib.error.HTTPError: 带 .code（429 限流）"""

    def __init__(self, code):
        super().__init__("HTTP %d" % code)
        self.code = code


@pytest.fixture(autouse=True)
def _reset_stat(monkeypatch):
    """每个用例独立: 清计数 + 信号量替身 + 只跑 1 个 code 不碰真实缓存"""
    with kpl._KPL_STAT_LOCK:
        kpl._KPL_STAT.clear()
        for i in range(3):
            kpl._KPL_STAT_TOT[i] = 0
        # 🔴 窗口起点也要复位: 汇总现在是**机会式**的(计数时若窗口已到期会顺带打一行),
        #   若不复位, 进程已跑 >60s 时用例自己的第 1 次 _stat_inc 就会把计数清掉。
        kpl._KPL_STAT_SINCE[0] = time.time()
    monkeypatch.setattr(kpl, "KPL_STAT_PERIOD", 3600)          # 用例内不自动汇总
    monkeypatch.setattr(kpl.store, "acquire_sem", lambda *a, **kw: "sem-key")
    monkeypatch.setattr(kpl.store, "release_lock", lambda *a, **kw: None)
    yield


def test_success_is_counted(monkeypatch):
    monkeypatch.setattr(kpl, "_urlopen", lambda *a, **kw: _FakeResp({"errcode": "0"}))
    kpl._call("default", {"a": "GetStockIDPlate", "Type": "2"})
    kpl._call("default", {"a": "GetStockIDPlate", "Type": "2"})
    kpl._call("default", {"a": "MorningBiddingList", "Type": "4"})
    assert kpl._KPL_STAT["GetStockIDPlate|T2"][0] == 2
    assert kpl._KPL_STAT["MorningBiddingList|T4"][0] == 1
    assert kpl._KPL_STAT_TOT[0] == 3 and kpl._KPL_STAT_TOT[1] == 0


def test_failure_and_429_are_counted(monkeypatch):
    """429 必须能单列 —— 它是"配额被打满"的唯一直接证据"""

    def boom(*a, **kw):
        raise _HTTPError(429)

    monkeypatch.setattr(kpl, "_urlopen", boom)
    kpl._call("default", {"a": "DailyLimitPerformance", "Type": "4"})
    kpl._call("default", {"a": "DailyLimitPerformance", "Type": "4"})
    assert kpl._KPL_STAT["DailyLimitPerformance|T4"] == [2, 2]
    assert kpl._KPL_STAT_TOT[1] == 2 and kpl._KPL_STAT_TOT[2] == 2, "429 未单独计数"


def test_timeout_and_other_errors_counted_as_fail_only(monkeypatch):
    def boom(*a, **kw):
        raise TimeoutError("timed out")

    monkeypatch.setattr(kpl, "_urlopen", boom)
    kpl._call("default", {"a": "X"})
    assert kpl._KPL_STAT_TOT[1] == 1 and kpl._KPL_STAT_TOT[2] == 0


def test_flush_line_is_greppable_and_resets():
    """汇总行必须一行可 grep（10-08 就是靠 grep 这行出报告的）"""
    kpl._stat_inc("GetStockIDPlate", "2")
    kpl._stat_inc("GetStockIDPlate", "2")
    kpl._stat_inc("MorningBiddingList", "4", ok=False, http=429)
    line = kpl.kpl_stat_flush()
    assert "KPL出网统计" in line and "total=3" in line and "http429=1" in line
    assert "GetStockIDPlate|T2=2" in line and "MorningBiddingList|T4=1(fail1)" in line
    # 清零: 再 flush 无活动 ⇒ 返回空(平时零噪声)
    assert kpl.kpl_stat_flush() == ""


def test_switch_off(monkeypatch):
    """KX_KPL_OUTBOUND_STAT=0 ⇒ 完全不计数(统计绝不能成为业务负担/风险)"""
    monkeypatch.setattr(kpl, "_KPL_STAT_ON", False)
    kpl._stat_inc("A", "1")
    assert kpl._KPL_STAT == {} and kpl._KPL_STAT_TOT == [0, 0, 0]
    assert kpl.kpl_stat_flush() == ""
