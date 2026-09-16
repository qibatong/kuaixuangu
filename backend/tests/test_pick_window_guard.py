# -*- coding: utf-8 -*-
"""选股闸门测试(2026-09-16 主人拍板: 开盘日 9:00-9:26 不支持选股)

覆盖三层:
  1. mode.is_pick_open —— 时间维纯函数边界(含周末/节假日放行);
  2. api/stocks._pick_blocked_reason —— 时间维 + 快照维组合(注入 now);
  3. 接口级 —— 拦截时 ok=False/blocked=True 且**不落批次**; ping 恒放行。

背景(9/16 生产实测): 9:25:14 / 9:25:29 两个用户拿到的是 9/15 的名单 ——
当日 9_25 定格 09:25:29 才落库, 期间 load_snapshot_full 静默回退昨日且只打 INFO。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.api import stocks as stocks_api
from app.services import settings as st
from app.services.picker import mode as pm

BJ = timezone(timedelta(hours=8))


def ts(y, m, d, hh, mm, ss=0):
    """构造北京时间对应时间戳(服务器时区无关)"""
    return datetime(y, m, d, hh, mm, ss, tzinfo=BJ).timestamp()


def hdrs(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def guard_on():
    """把闸门开关置 1(测试库 session 级 fixture 默认写 0), 用例后恢复 0"""
    st.set(stocks_api.PICK_WINDOW_SWITCH, 1)
    yield
    st.set(stocks_api.PICK_WINDOW_SWITCH, 0)


@pytest.fixture
def snap_ok(monkeypatch):
    """桩 has_today_snapshot → True(当日定格已落库)"""
    monkeypatch.setattr(stocks_api.auction_snapshot, "has_today_snapshot",
                        lambda date=None: True)


@pytest.fixture
def snap_missing(monkeypatch):
    """桩 has_today_snapshot → False(当日定格未落库)"""
    monkeypatch.setattr(stocks_api.auction_snapshot, "has_today_snapshot",
                        lambda date=None: False)


# 2026-09-16 = 周三(交易日), 09-19 = 周六, 09-20 = 周日
# ------------------------------------------------------------------ 1. 时间维
def test_pick_open_boundaries():
    """9:00 关闭, 9:26 开放; 分钟粒度(9:25:59 仍关)"""
    assert pm.is_pick_open(ts(2026, 9, 16, 8, 59)) is True     # 8:59 盘前
    assert pm.is_pick_open(ts(2026, 9, 16, 9, 0)) is False     # 关
    assert pm.is_pick_open(ts(2026, 9, 16, 9, 15)) is False    # 竞价开始仍关
    assert pm.is_pick_open(ts(2026, 9, 16, 9, 25)) is False    # 定格点仍关
    assert pm.is_pick_open(ts(2026, 9, 16, 9, 25, 59)) is False
    assert pm.is_pick_open(ts(2026, 9, 16, 9, 26)) is True     # 开
    assert pm.is_pick_open(ts(2026, 9, 16, 9, 30)) is True
    assert pm.is_pick_open(ts(2026, 9, 16, 14, 59)) is True
    assert pm.is_pick_open(ts(2026, 9, 16, 23, 0)) is True


def test_pick_open_offdays_passthrough():
    """非交易日不拦: 周末/节假日回放最近交易日定格是既有功能"""
    assert pm.is_pick_open(ts(2026, 9, 19, 10, 0)) is True     # 周六
    assert pm.is_pick_open(ts(2026, 9, 20, 9, 10)) is True     # 周日
    hol = {"2026-10-01"}
    assert pm.is_pick_open(ts(2026, 10, 1, 9, 10), holidays=hol) is True


# ------------------------------------------------------------------ 2. 双闸门组合
def test_blocked_in_time_window(snap_ok):
    """9:00-9:26 一律拦(时间维), 与快照是否存在无关"""
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 9, 0)) == pm.PICK_BLOCK_MSG_TIME
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 9, 10)) == pm.PICK_BLOCK_MSG_TIME
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 9, 25)) == pm.PICK_BLOCK_MSG_TIME


def test_blocked_when_snapshot_missing(snap_missing):
    """≥9:26 但当日定格未落库 → 继续拦(防重采越过 9:26 时静默回退昨日)"""
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 9, 26)) == pm.PICK_BLOCK_MSG_SNAP
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 9, 27)) == pm.PICK_BLOCK_MSG_SNAP
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 10, 30)) == pm.PICK_BLOCK_MSG_SNAP


def test_pass_when_all_clear(snap_ok):
    """9:26 后 + 当日定格已落库 → 放行"""
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 9, 26)) is None
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 10, 0)) is None
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 15, 30)) is None


def test_preopen_before_9_pass(snap_missing):
    """9:00 前(盘前)放行 —— PREOPEN「看上交易日定格」是设计内功能。

    注意此处刻意用 snap_missing: 盘前当日必然没有快照, 若快照维在盘前也生效,
    这条会误拦(这正是实现里要 `hm >= T_PICK_OPEN` 才查快照的原因)。
    """
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 0, 30)) is None
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 16, 8, 0)) is None


def test_offday_pass_even_without_snapshot(snap_missing):
    """非交易日放行, 且**不查当日快照**(周末查必然为空 → 误拦)"""
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 19, 10, 0)) is None
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 20, 9, 10)) is None


# ------------------------------------------------------------------ 3. 接口级
def test_api_ping_not_blocked(client, first_user, guard_on, monkeypatch):
    """ping 在闸门之前 return: 前端登录态/时段探测不受影响"""
    token, _, _ = first_user
    monkeypatch.setattr(stocks_api, "_pick_blocked_reason",
                        lambda now=None: pm.PICK_BLOCK_MSG_TIME)
    r = client.get("/api/stocks?action=ping", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") is True and "before930" in d


def test_api_blocked_returns_flag(client, first_user, guard_on, monkeypatch):
    """命中闸门 → HTTP 200 + ok=False/blocked=True(msg 供前端提示), 名单为空"""
    token, _, _ = first_user
    monkeypatch.setattr(stocks_api, "_pick_blocked_reason",
                        lambda now=None: pm.PICK_BLOCK_MSG_TIME)
    for act in ("filter", "lock", "refresh"):
        r = client.get(f"/api/stocks?action={act}&markets=sh_sz", headers=hdrs(token))
        assert r.status_code == 200, act
        d = r.json()
        assert d.get("ok") is False and d.get("blocked") is True, act
        assert d.get("msg") == pm.PICK_BLOCK_MSG_TIME, act
        assert d.get("count") == 0 and d.get("list") == [], act
        assert d.get("blockedUntil") == "09:26", act


def test_api_blocked_writes_no_batch(client, first_user, guard_on, monkeypatch):
    """铁律: 拦截路径**不落批次**(不跑 pipeline / 不落库 / 不推送)"""
    from app.db import database
    token, _, _ = first_user
    monkeypatch.setattr(stocks_api, "_pick_blocked_reason",
                        lambda now=None: pm.PICK_BLOCK_MSG_TIME)

    def _count():
        conn = database.get_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
        finally:
            conn.close()

    before = _count()
    for act in ("filter", "lock", "refresh"):
        client.get(f"/api/stocks?action={act}&markets=sh_sz", headers=hdrs(token))
    assert _count() == before


def test_api_normal_when_guard_off(client, first_user):
    """闸门关闭(测试库 session 默认) → 接口行为不变(既有链路可用)"""
    token, _, _ = first_user
    r = client.get("/api/stocks?action=filter&markets=sh_sz&bid_min=0", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") is True
    assert d.get("blocked") is None      # 正常路径不带该标记


def test_pick_block_msg_shared_with_frontend():
    """前后端文案同口径: 前端 utils/time.js 的常量必须与此逐字一致

    前端文件同步校验(缺失/不一致 → 提示用户看到两套说法)。
    """
    import io
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    fe = os.path.join(here, "..", "..", "frontend", "src", "utils", "time.js")
    if not os.path.exists(fe):
        pytest.skip("前端源码不在本仓库布局内")
    src = io.open(fe, encoding="utf-8").read()
    assert pm.PICK_BLOCK_MSG_TIME in src
    assert pm.PICK_BLOCK_MSG_SNAP in src
