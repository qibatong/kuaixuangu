# -*- coding: utf-8 -*-
"""选股闸门测试 v3(2026-09-17 口径重做)

唯一权威口径 = picker/mode.is_pick_open。交易日**只挡两段**:
  ① [09:00:00, 09:15:00)  盘前 PREOPEN —— 名单来自**上交易日**定格;
  ② [09:25:00, 09:25:35]  当日 9_25 尚未落库 —— load_snapshot_full 会静默回退昨日;
**09:15:00-09:24:59 放行**(竞价主窗口); 非交易日 / 盘前(<9:00) 放行;
≥09:25:36 时间维放行, 但还须过**快照维**(当日 9_25 已落库)。

🔴 血泪史(改本文件前必读):
  v4.11.22 口径是「交易日 9:00-9:26 整段禁选」, 把 9:15-9:25 **竞价主窗口**
  (主人的真实选股来源)一起治死 → 9/17 早盘 9:15-9:26 完全选不了股 / 0 请求事故
  → v4.11.26 整体回退并摘除闸门本体 → v4.11.27 重做为上面两段。
  下面 test_auction_window_must_be_open_incident_regression 就是这条事故的回归防线:
  任何人把竞价窗口重新封上, 它会立刻红。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.api import stocks as stocks_api
from app.services import settings as st
from app.services.picker import mode as pm

BJ = timezone(timedelta(hours=8))
D = (2026, 9, 16)          # 周三(交易日); 09-19 = 周六, 09-20 = 周日


def ts(y, m, d, hh, mm, ss=0):
    """构造北京时间对应时间戳(服务器时区无关)"""
    return datetime(y, m, d, hh, mm, ss, tzinfo=BJ).timestamp()


def hdrs(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def guard_on():
    """把闸门开关置 1(session 级 fixture 默认写 0), 用例后恢复 0"""
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


# ------------------------------------------------------- 1. 时间维(秒级边界)
def test_pick_open_boundaries_seconds():
    """两段的**秒级**边界(第二段必须到秒, 分钟粒度表达不了)"""
    assert pm.is_pick_open(ts(*D, 8, 59, 59)) is True     # 盘前
    assert pm.is_pick_open(ts(*D, 9, 0, 0)) is False      # 第一段起(含)
    assert pm.is_pick_open(ts(*D, 9, 5, 0)) is False
    assert pm.is_pick_open(ts(*D, 9, 14, 59)) is False    # 第一段末(仍拦)
    assert pm.is_pick_open(ts(*D, 9, 15, 0)) is True      # ★ 放行(第一段末 + 1s)
    assert pm.is_pick_open(ts(*D, 9, 24, 59)) is True
    assert pm.is_pick_open(ts(*D, 9, 25, 0)) is False     # 第二段起(含)
    assert pm.is_pick_open(ts(*D, 9, 25, 35)) is False    # 第二段末(含)
    assert pm.is_pick_open(ts(*D, 9, 25, 36)) is True     # ★ 放行(第二段末 + 1s)
    assert pm.is_pick_open(ts(*D, 9, 26, 0)) is True      # v4.11.22 旧口径的开放点
    assert pm.is_pick_open(ts(*D, 9, 30, 0)) is True
    assert pm.is_pick_open(ts(*D, 14, 59, 59)) is True
    assert pm.is_pick_open(ts(*D, 23, 0, 0)) is True


def test_auction_window_must_be_open_incident_regression():
    """🔴 9/17 事故回归防线: 09:15:00-09:24:59 **逐秒**必须放行。

    v4.11.22 把这段(竞价主窗口 = 主人真实选股来源)一起封死 → 早盘 0 请求。
    这里逐秒抽样 9:15、9:16~9:24 每分钟的 0/30/59 秒、9:24 全秒, 任一时刻被拦
    都说明口径回退了。
    """
    for ss in range(60):                                  # 09:15:00-09:15:59 全秒
        assert pm.is_pick_open(ts(*D, 9, 15, ss)) is True, "9:15:%02d" % ss
    for mm in range(16, 25):                              # 09:16-09:24
        for ss in (0, 30, 59):
            assert pm.is_pick_open(ts(*D, 9, mm, ss)) is True, "9:%02d:%02d" % (mm, ss)
    for ss in range(60):                                  # 09:24:00-09:24:59 全秒
        assert pm.is_pick_open(ts(*D, 9, 24, ss)) is True, "9:24:%02d" % ss


def test_pick_open_offdays_passthrough():
    """非交易日不拦: 周末/节假日回放最近交易日定格是既有功能"""
    assert pm.is_pick_open(ts(2026, 9, 19, 9, 5)) is True       # 周六(第一段内也放)
    assert pm.is_pick_open(ts(2026, 9, 20, 9, 10)) is True      # 周日
    assert pm.is_pick_open(ts(2026, 9, 20, 9, 25, 10)) is True  # 周日(第二段内也放)
    hol = {"2026-10-01"}
    assert pm.is_pick_open(ts(2026, 10, 1, 9, 5), holidays=hol) is True


def test_pick_resume_at_matches_segments():
    """pick_resume_at 必须与 is_pick_open 同源: 拦截段给对应放行点, 放行段给空串"""
    assert pm.pick_resume_at(ts(*D, 9, 5)) == "09:15"
    assert pm.pick_resume_at(ts(*D, 9, 14, 59)) == "09:15"
    assert pm.pick_resume_at(ts(*D, 9, 15, 0)) == ""
    assert pm.pick_resume_at(ts(*D, 9, 19, 30)) == ""
    assert pm.pick_resume_at(ts(*D, 9, 25, 10)) == "09:25:36"
    assert pm.pick_resume_at(ts(*D, 9, 25, 35)) == "09:25:36"
    assert pm.pick_resume_at(ts(*D, 9, 25, 36)) == ""


# ------------------------------------------------------- 2. 双闸门组合
def test_blocked_first_segment(snap_ok):
    """第一段 9:00:00-9:14:59 一律拦(时间维), 与快照是否存在无关"""
    for t in (ts(*D, 9, 0), ts(*D, 9, 10), ts(*D, 9, 14, 59)):
        assert stocks_api._pick_blocked_reason(t) == pm.PICK_BLOCK_MSG_TIME


def test_blocked_second_segment(snap_ok):
    """第二段 9:25:00-9:25:35 一律拦(时间维)

    实测落库时刻 09:25:23~09:25:32 全在段内 —— 这正是设 09:25:35 上界的依据。
    """
    for t in (ts(*D, 9, 25, 0), ts(*D, 9, 25, 23), ts(*D, 9, 25, 32), ts(*D, 9, 25, 35)):
        assert stocks_api._pick_blocked_reason(t) == pm.PICK_BLOCK_MSG_TIME


def test_auction_window_not_blocked_even_without_snapshot(snap_missing):
    """🔴 竞价主窗口 9:15:00-9:24:59 **不拦**, 且**不查快照**。

    刻意用 snap_missing: 该时段当日必然没有 9_25 快照, 若快照维在此生效会误拦
    (这正是 _pick_blocked_reason 里 `secs >= T_PICK_OPEN` 前置条件的作用)。
    同时也是 v4.11.22 事故(竞价窗口被整段封死)的第二道回归防线。
    """
    for t in (ts(*D, 9, 15, 0), ts(*D, 9, 19, 30), ts(*D, 9, 24, 59)):
        assert stocks_api._pick_blocked_reason(t) is None


def test_blocked_when_snapshot_missing(snap_missing):
    """≥9:25:36 但当日定格未落库 → 继续拦(防重采越过放行点时静默回退昨日)"""
    for t in (ts(*D, 9, 25, 36), ts(*D, 9, 27), ts(*D, 10, 30)):
        assert stocks_api._pick_blocked_reason(t) == pm.PICK_BLOCK_MSG_SNAP


def test_pass_when_all_clear(snap_ok):
    """≥9:25:36 + 当日定格已落库 → 放行"""
    for t in (ts(*D, 9, 25, 36), ts(*D, 10, 0), ts(*D, 15, 30)):
        assert stocks_api._pick_blocked_reason(t) is None


def test_preopen_before_9_pass(snap_missing):
    """9:00 前(盘前)放行 —— PREOPEN「看上交易日定格」是设计内功能。

    刻意用 snap_missing: 盘前当日必然没有快照, 若快照维在盘前也生效, 这条会误拦。
    """
    assert stocks_api._pick_blocked_reason(ts(*D, 0, 30)) is None
    assert stocks_api._pick_blocked_reason(ts(*D, 8, 0)) is None


def test_offday_pass_even_without_snapshot(snap_missing):
    """非交易日放行, 且**不查当日快照**(周末查必然为空 → 误拦)"""
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 19, 9, 5)) is None
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 20, 9, 25, 10)) is None


def test_blocked_until_matches_segment():
    """_pick_blocked_until 与拦截段一致(前端据此提示"何时恢复")"""
    assert stocks_api._pick_blocked_until(ts(*D, 9, 5)) == "09:15"
    assert stocks_api._pick_blocked_until(ts(*D, 9, 25, 10)) == "09:25:36"
    assert stocks_api._pick_blocked_until(ts(*D, 9, 19)) == "09:25:36"   # 非拦截 → 兜底


# ------------------------------------------------------- 3. 接口级
def test_api_ping_not_blocked(client, first_user, guard_on, monkeypatch):
    """ping 在闸门之前 return: 前端登录态/时段探测不受影响"""
    token, _, _ = first_user
    monkeypatch.setattr(stocks_api, "_pick_blocked_reason",
                        lambda now=None: pm.PICK_BLOCK_MSG_TIME)
    r = client.get("/api/stocks?action=ping", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") is True and "before930" in d


def test_api_ping_exposes_gate_switch(client, first_user):
    """ping 必须透出 pickGateEnabled —— 前端置灰改由它驱动(2026-09-17)。

    9/17 早盘事故: 前端置灰是纯时间判断、不看开关 → `pick_window_guard=0` 只关掉了后端,
    前端依旧置灰**且连自动加载都不发请求**, 用户完全点不动。
    本用例把「开关 → ping 字段」的联动钉死, 并覆盖字符串假值
    (原 `bool(settings.get(...))` 会把 "0" 判成 True, 让「关开关」静默失效)。
    """
    token, _, _ = first_user
    for val in (0, "0", "false", "off", ""):
        st.set(stocks_api.PICK_WINDOW_SWITCH, val)
        d = client.get("/api/stocks?action=ping", headers=hdrs(token)).json()
        assert d.get("pickGateEnabled") is False, (val, d)
    for val in (1, "1", True):
        st.set(stocks_api.PICK_WINDOW_SWITCH, val)
        d = client.get("/api/stocks?action=ping", headers=hdrs(token)).json()
        assert d.get("pickGateEnabled") is True, (val, d)
    st.set(stocks_api.PICK_WINDOW_SWITCH, 0)      # 复原(session fixture 默认=关)


def test_api_blocked_returns_flag(client, first_user, guard_on, monkeypatch):
    """命中闸门 → HTTP 200 + ok=False/blocked=True(msg 供前端提示), 名单为空"""
    token, _, _ = first_user
    monkeypatch.setattr(stocks_api, "_pick_blocked_reason",
                        lambda now=None: pm.PICK_BLOCK_MSG_TIME)
    monkeypatch.setattr(stocks_api, "_pick_blocked_until", lambda now=None: "09:15")
    for act in ("filter", "lock", "refresh"):
        r = client.get(f"/api/stocks?action={act}&markets=sh_sz", headers=hdrs(token))
        assert r.status_code == 200, act
        d = r.json()
        assert d.get("ok") is False and d.get("blocked") is True, act
        assert d.get("msg") == pm.PICK_BLOCK_MSG_TIME, act
        assert d.get("count") == 0 and d.get("list") == [], act
        assert d.get("blockedUntil") == "09:15", act


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


# ------------------------------------------------------- 4. 前后端同口径
def _fe_time_js():
    import io
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    fe = os.path.join(here, "..", "..", "frontend", "src", "utils", "time.js")
    if not os.path.exists(fe):
        pytest.skip("前端源码不在本仓库布局内")
    return io.open(fe, encoding="utf-8").read()


def test_pick_block_msg_shared_with_frontend():
    """前后端**文案**逐字一致(否则用户在不同触发路径下看到两套说法)"""
    src = _fe_time_js()
    assert pm.PICK_BLOCK_MSG_TIME in src
    assert pm.PICK_BLOCK_MSG_SNAP in src


def test_gate_boundaries_shared_with_frontend():
    """🔴 前后端**边界常量**同口径 —— 只对拍文案不够。

    边界不一致会出现"前端置灰但后端放行"(或反之)的**静默错位**, 用户看到的现象
    与日志完全对不上, 极难排查(9/17 事故正是前后端口径分叉的一类)。这里把 time.js
    里的数值抠出来与 mode.py 逐条比对。
    """
    import re
    src = _fe_time_js()

    def num(name):
        m = re.search(name + r"\s*=\s*([0-9\s*+]+)", src)
        assert m, ("未找到前端常量 " + name)
        return eval(m.group(1))               # noqa: S307 — 仅本地源码的算术式

    assert num("PICK_BLOCK1_FROM") == pm.T_PICK_BLOCK1_FROM
    assert num("PICK_BLOCK1_TO") == pm.T_PICK_BLOCK1_TO
    assert num("PICK_BLOCK2_FROM") == pm.T_PICK_BLOCK2_FROM
    assert num("PICK_BLOCK2_TO") == pm.T_PICK_BLOCK2_TO
    # PICK_OPEN 在前端是 `PICK_BLOCK2_TO + 1` 的引用式, 单独核对定义形态 + 数值
    assert re.search(r"PICK_OPEN\s*=\s*PICK_BLOCK2_TO\s*\+\s*1", src), \
        "前端 PICK_OPEN 定义形态变了, 需人工核对"
    assert pm.T_PICK_OPEN == pm.T_PICK_BLOCK2_TO + 1
