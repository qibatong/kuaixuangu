# -*- coding: utf-8 -*-
"""选股闸门测试 v4(2026-09-18 口径)

唯一权威口径 = picker/mode.is_pick_open。交易日**只挡一段**:
  [09:15:00, 09:25:50]  竞价进行中 / 当日 9_25 定格尚未落库。
    · 竞价过程数据每 10 秒在变 ⇒ 排出来的名单不成立;
    · 该段取数还会回退**上一交易日** 9_25(竞涨幅/竞价额整批错位, 竞涨幅权重 34%);
    · ≥09:25:51 时间维放行, 但还须过**快照维**(当日 9_25 已落库)。
  00:00-09:14:59 **盘前放行**(用上交易日定格是设计内功能, 由顶栏标注来源日期明示);
  非交易日(周末/节假日)放行(回放最近交易日定格是既有功能)。

🔴 血泪史(改本文件前必读):
  v4.11.22 口径「交易日 9:00-9:26 整段禁选」, 且当时前端置灰是纯时间判断、不看开关 →
  9/17 早盘导致该时段 0 请求 + 页面完全点不动 → v4.11.26 整体回退。
  **v4.11.22 真正的错不是"挡了竞价段", 而是**:
    ① 把**盘前**(PREOPEN/上交易日定格, 设计内功能)与**竞价段**混为一段;
    ② 前端不看开关、连自动加载都不发请求 → 用户看不到任何提示也点不动。
  v4.11.29(本版)只挡竞价段、盘前放行, 且前端置灰由 `pickGateEnabled` 开关驱动 +
  20s 定时器到点自动解禁(见 stores/stocks.refreshPickGate) ⇒ 拦得住但**看得见、会自愈**。
  下面 test_preopen_window_must_be_open_incident_regression 是 ① 的回归防线:
  任何人把盘前重新封上, 它会立刻红。
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
    """拦截段**秒级**边界(必须到秒, 分钟粒度表达不了) + 盘前放行"""
    assert pm.is_pick_open(ts(*D, 0, 30)) is True         # 盘前
    assert pm.is_pick_open(ts(*D, 8, 59, 59)) is True
    assert pm.is_pick_open(ts(*D, 9, 0, 0)) is True       # v4.11.27 曾是拦截点 → v4 放行
    assert pm.is_pick_open(ts(*D, 9, 14, 59)) is True     # ★ 盘前最后一秒仍放行
    assert pm.is_pick_open(ts(*D, 9, 15, 0)) is False     # ★ 拦截段起(含) — 竞价开始
    assert pm.is_pick_open(ts(*D, 9, 19, 30)) is False
    assert pm.is_pick_open(ts(*D, 9, 24, 59)) is False
    assert pm.is_pick_open(ts(*D, 9, 25, 0)) is False     # 落库前仍在段内
    assert pm.is_pick_open(ts(*D, 9, 25, 35)) is False    # 旧口径末点: 现仍在段内
    assert pm.is_pick_open(ts(*D, 9, 25, 50)) is False    # ★ 拦截段末(含, 2026-09-19 顺延)
    assert pm.is_pick_open(ts(*D, 9, 25, 51)) is True     # ★ 放行(段末 + 1s)
    assert pm.is_pick_open(ts(*D, 9, 26, 0)) is True      # v4.11.22 旧口径的开放点
    assert pm.is_pick_open(ts(*D, 9, 30, 0)) is True
    assert pm.is_pick_open(ts(*D, 14, 59, 59)) is True
    assert pm.is_pick_open(ts(*D, 23, 0, 0)) is True


def test_auction_window_must_be_blocked():
    """🔴 v4 核心口径: 竞价进行中 09:15:00-09:24:59 **逐秒必须拦**。

    主人 2026-09-18 拍板:「选股本来就是竞价结束后才选, 竞价过程数据都在变化,
    选的股也没意义」。实证动机是 9/18 早盘 09:15/09:22 两批因当日 9_25 未落库,
    竞涨幅/竞价额**整批回退昨日**(黑猫 3.35=昨日值, 今日实为 1.00), 而竞涨幅占
    评分权重 34% → 名单与评分双双失真。
    """
    for ss in range(60):                                  # 09:15:00-09:15:59 全秒
        assert pm.is_pick_open(ts(*D, 9, 15, ss)) is False, "9:15:%02d" % ss
    for mm in range(16, 25):                              # 09:16-09:24
        for ss in (0, 30, 59):
            assert pm.is_pick_open(ts(*D, 9, mm, ss)) is False, "9:%02d:%02d" % (mm, ss)
    for ss in range(60):                                  # 09:24:00-09:24:59 全秒
        assert pm.is_pick_open(ts(*D, 9, 24, ss)) is False, "9:24:%02d" % ss
    for ss in range(51):                                  # 09:25:00-09:25:50 全秒(2026-09-19 顺延)
        assert pm.is_pick_open(ts(*D, 9, 25, ss)) is False, "9:25:%02d" % ss
    for ss in range(51, 60):                              # 09:25:51-09:25:59 全秒放行
        assert pm.is_pick_open(ts(*D, 9, 25, ss)) is True, "9:25:%02d" % ss


def test_preopen_window_must_be_open_incident_regression():
    """🔴 v4.11.22 事故回归防线: 盘前 **09:00:00-09:14:59** 逐秒必须放行。

    v4.11.22 把盘前(用上交易日定格, 设计内功能)与竞价段混成一段 9:00-9:26 一起封死;
    v4.11.27 又反向只挡盘前。v4 主人拍板"盘前保留但强制标注" ⇒ 盘前必须放行,
    由 API 透出定格来源日期 + 前端顶栏标注消除误认, 而不是靠禁选。
    """
    for ss in range(60):                                  # 09:00:00-09:00:59 全秒
        assert pm.is_pick_open(ts(*D, 9, 0, ss)) is True, "9:00:%02d" % ss
    for mm in range(1, 15):                               # 09:01-09:14
        for ss in (0, 30, 59):
            assert pm.is_pick_open(ts(*D, 9, mm, ss)) is True, "9:%02d:%02d" % (mm, ss)
    for ss in range(60):                                  # 09:14:00-09:14:59 全秒
        assert pm.is_pick_open(ts(*D, 9, 14, ss)) is True, "9:14:%02d" % ss


def test_pick_open_offdays_passthrough():
    """非交易日不拦: 周末/节假日回放最近交易日定格是既有功能"""
    assert pm.is_pick_open(ts(2026, 9, 19, 9, 5)) is True       # 周六(拦截段内也放)
    assert pm.is_pick_open(ts(2026, 9, 20, 9, 10)) is True      # 周日
    assert pm.is_pick_open(ts(2026, 9, 20, 9, 25, 10)) is True  # 周日(拦截段内也放)
    hol = {"2026-10-01"}
    assert pm.is_pick_open(ts(2026, 10, 1, 9, 5), holidays=hol) is True


def test_pick_resume_at_matches_segments():
    """pick_resume_at 必须与 is_pick_open 同源: 拦截段给放行点, 放行段给空串"""
    assert pm.pick_resume_at(ts(*D, 8, 0)) == ""
    assert pm.pick_resume_at(ts(*D, 9, 5)) == ""          # 盘前已放行
    assert pm.pick_resume_at(ts(*D, 9, 14, 59)) == ""
    assert pm.pick_resume_at(ts(*D, 9, 15, 0)) == "09:25:51"
    assert pm.pick_resume_at(ts(*D, 9, 19, 30)) == "09:25:51"
    assert pm.pick_resume_at(ts(*D, 9, 25, 10)) == "09:25:51"
    assert pm.pick_resume_at(ts(*D, 9, 25, 50)) == "09:25:51"
    assert pm.pick_resume_at(ts(*D, 9, 25, 51)) == ""


# ------------------------------------------------------- 2. 双闸门组合
def test_blocked_auction_segment(snap_ok):
    """竞价段 9:15:00-9:25:50 一律拦(时间维), 与快照是否存在无关

    实测落库时刻 09:25:23~09:25:32 全在段内 —— 这正是设 09:25:50 上界的依据。
    """
    for t in (ts(*D, 9, 15, 0), ts(*D, 9, 19, 30), ts(*D, 9, 24, 59),
              ts(*D, 9, 25, 0), ts(*D, 9, 25, 23), ts(*D, 9, 25, 32), ts(*D, 9, 25, 50)):
        assert stocks_api._pick_blocked_reason(t) == pm.PICK_BLOCK_MSG_TIME


def test_preopen_not_blocked_even_without_snapshot(snap_missing):
    """盘前(00:00-09:14:59)**不拦**, 且**不查快照**(盘前当日必然无快照)。

    刻意用 snap_missing: 若快照维在盘前生效会误拦 —— 这正是 _pick_blocked_reason
    里 `secs >= T_PICK_OPEN` 前置条件的作用。
    """
    for t in (ts(*D, 0, 30), ts(*D, 8, 0), ts(*D, 9, 5), ts(*D, 9, 14, 59)):
        assert stocks_api._pick_blocked_reason(t) is None


def test_blocked_when_snapshot_missing(snap_missing):
    """≥9:25:51 但当日定格未落库 → 继续拦(防重采越过放行点时静默回退昨日)"""
    for t in (ts(*D, 9, 25, 51), ts(*D, 9, 27), ts(*D, 10, 30)):
        assert stocks_api._pick_blocked_reason(t) == pm.PICK_BLOCK_MSG_SNAP


def test_pass_when_all_clear(snap_ok):
    """≥9:25:51 + 当日定格已落库 → 放行"""
    for t in (ts(*D, 9, 25, 51), ts(*D, 10, 0), ts(*D, 15, 30)):
        assert stocks_api._pick_blocked_reason(t) is None


def test_offday_pass_even_without_snapshot(snap_missing):
    """非交易日放行, 且**不查当日快照**(周末查必然为空 → 误拦)"""
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 19, 9, 5)) is None
    assert stocks_api._pick_blocked_reason(ts(2026, 9, 20, 9, 25, 10)) is None


def test_blocked_until_matches_segment():
    """_pick_blocked_until 与拦截段一致(前端据此提示"何时恢复")"""
    assert stocks_api._pick_blocked_until(ts(*D, 9, 5)) == "09:25:51"     # 非拦截 → 兜底
    assert stocks_api._pick_blocked_until(ts(*D, 9, 19)) == "09:25:51"
    assert stocks_api._pick_blocked_until(ts(*D, 9, 25, 10)) == "09:25:51"


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
    monkeypatch.setattr(stocks_api, "_pick_blocked_until", lambda now=None: "09:25:36")
    for act in ("filter", "lock", "refresh"):
        r = client.get(f"/api/stocks?action={act}&markets=sh_sz", headers=hdrs(token))
        assert r.status_code == 200, act
        d = r.json()
        assert d.get("ok") is False and d.get("blocked") is True, act
        assert d.get("msg") == pm.PICK_BLOCK_MSG_TIME, act
        assert d.get("count") == 0 and d.get("list") == [], act
        assert d.get("blockedUntil") == "09:25:36", act


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
    txt = io.open(fe, encoding="utf-8").read()
    # 服务器上可能残留旧副本(实测有 08-25 版 frontend/src) → 与旧副本对拍是假结果。
    # v4.11.29 闸门文案是"当前版本"的标志, 缺失即视为陈旧副本。
    if "竞价进行中" not in txt:
        pytest.skip("本机前端源码非最新副本(缺 v4.11.29 闸门文案), 对拍无意义")
    return txt


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

    assert num("PICK_BLOCK_FROM") == pm.T_PICK_BLOCK_FROM
    assert num("PICK_BLOCK_TO") == pm.T_PICK_BLOCK_TO
    # PICK_OPEN 在前端是 `PICK_BLOCK_TO + 1` 的引用式, 单独核对定义形态 + 数值
    assert re.search(r"PICK_OPEN\s*=\s*PICK_BLOCK_TO\s*\+\s*1", src), \
        "前端 PICK_OPEN 定义形态变了, 需人工核对"
    assert pm.T_PICK_OPEN == pm.T_PICK_BLOCK_TO + 1
