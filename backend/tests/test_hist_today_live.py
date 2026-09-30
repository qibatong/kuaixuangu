# -*- coding: utf-8 -*-
"""🔴 2026-09-30 修「今炸板 / 昨涨停 / 昨断板 三个 tab 全天空白」防复发测试。

## 现象(生产实测)
前端 `servedDate()` 盘中恒等于**今天**(后端 `auction-overview` 下发的 `serveDate`)
⇒ 这三个 tab 都带 `date=<今天>` 请求 ⇒ 后端带 date 即走**历史回看分支**, 读
`auction_daily_history`。而 `yest_zt` / `yest_broken` / `broken_today` 这三类行是
**盘后 15:30 才落库**的(竞价后 09:27 那批只写 seal/boom/bid_net/qiangcang)
⇒ 盘前至 15:30 之间读的是**空表** ⇒ 三个 tab 全空白。

    无参            : 昨涨停 56 / 昨断板 4  / 今炸板 13
    date=2026-09-30 : 昨涨停  0 / 昨断板 0  / 今炸板  0     ← 复现

当日 `auction_daily_history` 实况(缺的正好是这三个):
    2026-09-30 : bid_net, boom, qiangcang, seal
    2026-09-29 : bid_net, boom, broken_today, broken_yest, seal, yest_broken, yest_zt

## 口径(主人 2026-09-30 拍板, 方案 A)
`date` 解析后 == **北京今天** ⇒ 回落实时链, **不读历史表**; 只有回看**别的交易日**
才读表。语义依据: 回看"今天"本来就是看实时, 不是看历史(与主人铁律
「获取为零就不要用昨天的数据」同源)。

**只**对"今天"生效 ⇒ 盘前(对齐到上一交易日)/ 非交易日 / 历史回看的行为一律不变 ——
本文件为此专门钉了"别的日期仍走历史"的用例, 防止修一个坏三个。

## 日期纪律
本文件**硬编码日期 + 显式打桩 `kpl._bj_today`**, 不依赖运行时钟
(仓内铁律: 见 `kpl._bj_today` 的 docstring 与 test_freeze_day 的说明 ——
真实时钟会让用例随时间腐烂成假红/假绿)。
交易日: 2026-09-30(周三, 本 bug 当日) / 2026-09-29(周二)。
"""
import sys

sys.path.insert(0, "backend")

from app.api import deps
from app.api import kpl as kpl_api

TODAY = "2026-09-30"
PREV = "2026-09-29"


def _pin(monkeypatch, hist_calls, hist_rows=None, today=TODAY):
    """公共桩: 钉住"今天"、把日期解析设为恒等、记录一切走历史表的调用。

    hist_calls 非空即可断言"这次请求**没有**走历史分支";
    返回的 hist_rows 用于让历史分支产出可辨识的数据。
    """
    monkeypatch.setattr(kpl_api.kpl, "_bj_today", lambda: today)
    monkeypatch.setattr(kpl_api, "_resolve_date", lambda d: d)

    def _q(d, tab):
        hist_calls.append((d, tab))
        return list(hist_rows or [])
    monkeypatch.setattr(kpl_api.kpl, "query_auction_history", _q)


def _client(client, first_user):
    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1
    token, _, _ = first_user
    return {"Authorization": "Bearer " + token}


# --------------------------------------------------------------------------- #
# 1. date == 今天 ⇒ 走实时链(不读历史表)
# --------------------------------------------------------------------------- #
def test_yest_zt_date_today_uses_live_path(client, first_user, monkeypatch):
    """★「昨涨停」: 带 date=今天 必须走实时链 —— 历史表里今天这行要到 15:30 才有。"""
    hist = []
    _pin(monkeypatch, hist)
    monkeypatch.setattr(kpl_api.kpl, "fetch_yest_zt",
                        lambda: [{"code": "600001", "name": "实时取到的票"}])
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    r = client.get("/api/kpl/yest-zt?date=" + TODAY, headers=_client(client, first_user))
    d = r.json()
    assert r.status_code == 200 and d["ok"], r.text
    assert [x["code"] for x in d["list"]] == ["600001"], \
        "date=今天 必须返回**实时**结果, 实际 %s" % (d["list"],)
    assert hist == [], "date=今天 不得读历史表(读到的必然是空表), 实际 %s" % (hist,)


def test_yest_broken_date_today_uses_live_path(client, first_user, monkeypatch):
    """★「昨断板」: 同上。"""
    hist = []
    _pin(monkeypatch, hist)
    monkeypatch.setattr(kpl_api.kpl, "fetch_yest_broken",
                        lambda: [{"code": "600002", "name": "实时取到的票"}])
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    r = client.get("/api/kpl/yest-broken?date=" + TODAY, headers=_client(client, first_user))
    d = r.json()
    assert [x["code"] for x in d["list"]] == ["600002"], d
    assert hist == [], "date=今天 不得读历史表, 实际 %s" % (hist,)


def test_broken_today_date_today_uses_live_path(client, first_user, monkeypatch):
    """★「今炸板」: date=今天(未带 day) ⇒ 走实时链。

    `broken` 的实时链有个读库 fast-path(`_read_auction_fast`), 这里打成 None
    以证明最终落到实时源 `fetch_broken_zt`。
    """
    hist = []
    _pin(monkeypatch, hist)
    monkeypatch.setattr(kpl_api, "_read_auction_fast", lambda tab: (None, ""))
    monkeypatch.setattr(kpl_api.kpl, "fetch_broken_zt",
                        lambda day=None: [{"code": "600003", "name": "实时取到的票", "day": TODAY}])
    monkeypatch.setattr(kpl_api.kpl, "fill_float_mv_from_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    r = client.get("/api/kpl/broken?date=" + TODAY, headers=_client(client, first_user))
    d = r.json()
    assert [x["code"] for x in d["list"]] == ["600003"], d
    assert hist == [], "date=今天 不得读历史表, 实际 %s" % (hist,)


# --------------------------------------------------------------------------- #
# 2. 只对"今天"生效 —— 别的日期(回看/盘前对齐日/非交易日)仍走历史表
# --------------------------------------------------------------------------- #
def test_yest_zt_past_date_still_uses_history(client, first_user, monkeypatch):
    """🔴 防止"修一个坏三个": 回看**别的交易日**必须仍读历史表。"""
    hist = []
    _pin(monkeypatch, hist, hist_rows=[{"code": "600009", "name": "历史表里的票"}])
    # 实时源若被调用即视为回归
    monkeypatch.setattr(kpl_api.kpl, "fetch_yest_zt",
                        lambda: pytest_fail("date=非今天 不得走实时链"))
    monkeypatch.setattr(kpl_api.kpl, "fill_float_mv_from_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_reason_from_pool", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    r = client.get("/api/kpl/yest-zt?date=" + PREV, headers=_client(client, first_user))
    d = r.json()
    assert [x["code"] for x in d["list"]] == ["600009"], d
    assert (PREV, "yest_zt") in hist, "回看历史必须读表, 实际 %s" % (hist,)
    assert d.get("requestedDate") == PREV


def test_broken_date_today_with_day_yesterday_still_uses_history(client, first_user, monkeypatch):
    """★「昨炸板」刻意排除在重定向之外: 它取的是**前一交易日**的池(那批有落库)。

    若哪天有人把 `day != "yesterday"` 这个条件删掉, 本用例变红。
    """
    hist = []
    _pin(monkeypatch, hist, hist_rows=[{"code": "600008", "name": "昨日炸板票", "day": PREV}])
    monkeypatch.setattr(kpl_api.kpl, "_latest_trade_snap_date", lambda *a, **k: PREV)
    monkeypatch.setattr(kpl_api.kpl, "_merge_broken_bid_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_float_mv_from_snap", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "fill_close_change_from_kline", lambda *a, **k: None)
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    r = client.get("/api/kpl/broken?date=%s&day=yesterday" % TODAY,
                   headers=_client(client, first_user))
    d = r.json()
    assert [x["code"] for x in d["list"]] == ["600008"], d
    assert (PREV, "broken_today") in hist, \
        "date=今天&day=yesterday 仍须走历史(前一交易日池), 实际 %s" % (hist,)
    assert d.get("poolDate") == PREV


def test_guard_uses_bj_today_not_serve_day(client, first_user, monkeypatch):
    """★ 判据必须是**北京今天**, 不是"服务日": 非交易日/盘前 serveDate 会是上一交易日,
    那时带 date=上一交易日 **仍须**走历史表(否则非交易日定格画面会被实时数据顶掉)。
    """
    hist = []
    # 今天钉成"上一交易日"本身 ⇒ 反向验证: 若实现误用 serveDate 判据, 这里会走实时链
    _pin(monkeypatch, hist, hist_rows=[{"code": "600007", "name": "定格画面"}], today=PREV)
    monkeypatch.setattr(kpl_api.kpl, "fetch_yest_zt",
                        lambda: pytest_fail("date==北京今天 时不得读历史, 这里应走实时"))
    monkeypatch.setattr(kpl_api.kpl, "apply_board_concept_db", lambda *a, **k: None)

    # 此刻"北京今天"= PREV, 而请求 date=TODAY(≠今天) ⇒ 仍走历史
    r = client.get("/api/kpl/yest-zt?date=" + TODAY, headers=_client(client, first_user))
    d = r.json()
    assert [x["code"] for x in d["list"]] == ["600007"], d
    assert hist == [(TODAY, "yest_zt")], hist


def pytest_fail(msg):
    raise AssertionError(msg)
