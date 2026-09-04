# -*- coding: utf-8 -*-
"""2026-09-04 refresh 9:30 后直读历史批次: 打开/刷新页面不再全量重算

背景: 用户反馈首页竞价选股每次进入加载数秒。实测根因: 行情缓存 CACHE_TTL=30s 恰与前端
30s 轮询同周期 → 9:30 后每次 refresh 都"恰好过期"触发锁内全市场拉取(东财封禁期=腾讯
5548只), 多用户在 _fetch_lock 排队 → 长尾(生产实测最慢 57.97s); 而重算结果唯一用途是
给早已落库的当日锁定名单 merge 实时行情(前端 mergeSpotIntoLocked 的 listMap + spotMap)。
方案(主人确认): 9:30 后 mode=auction + action=refresh(页面打开/30s轮询)若当日存在可复用
批次 → 直读批次名单 + spotMap(60s TTL 缓存)实时覆盖返回, 跳过全市场重拉/全量重评分。
选批优先级(history.find_today_reusable_batch): ①用户当日同参手动 lock → ②同参手动
filter → ③用户当日无任何手动批次时的 9:26 系统批次(auto_applied=1); 参数指纹/markets
集合不一致(用户改过条件未应用)或无批次 → 走原全量计算兜底(语义正确)。

测试策略(同 test_lock_idempotent):
- 纯函数: 直接 SQL 种批次行(batch_date/batch_time/ts 可控) + now_ts 注入, 单测选批逻辑。
- API 集成: monkeypatch time.time 固定"当前时刻"北京 09:26(lock 落库) / 09:31(refresh
  直读, before930=False), 与真实运行时间无关; fetch_spot_quote_map 打桩防真实拉全市场。
"""
import calendar
import json

import pytest

from app.db import database
from app.services import history

# ---- 固定"当前时刻": 2026-09-09(周三) 北京 09:26 / 09:31 (UTC 01:26 / 01:31) ----
TS_0926 = calendar.timegm((2026, 9, 9, 1, 26, 0, 0, 0, 0))   # 北京 09:26 (lock 落库)
TS_0931 = calendar.timegm((2026, 9, 9, 1, 31, 0, 0, 0, 0))   # 北京 09:31 (9:30 后 refresh)
BDATE = "2026-09-09"


@pytest.fixture(autouse=True)
def _no_global_side_effects(monkeypatch):
    """屏蔽 API lock 路径写共享 daily_yizi 统计表(同 test_lock_idempotent, 防污染 test_phase1)"""
    from app.services import stats
    monkeypatch.setattr(stats, "record_daily_yizi", lambda raw: {"yizi_count": 0, "bid_amt": 0.0})


@pytest.fixture(autouse=True)
def _clean_shared_batches():
    """清理跨用例共享数据(用例 body 前执行):
    - user_id=0 系统批次: find_today_reusable_batch ③级查询跨用例共享, 不清理会残留污染
      (如"无批次→None"用例被前面用例种的 auto 批次误命中返回 (id,'auto'))
    - 本文件测试 uid 段(992101-992199): 防重复运行残留"""
    conn = database.get_conn()
    conn.execute("DELETE FROM batches WHERE user_id=0 AND auto_applied=1 AND batch_date=?",
                 (BDATE,))
    conn.execute("DELETE FROM batches WHERE user_id BETWEEN 992101 AND 992199")
    conn.commit()
    conn.close()
    yield


def _f(**over):
    """与 scorer.validate_filters 输出同构的筛选参数(幂等/直读指纹比对用)"""
    f = {"stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
         "bidGt": 7, "probLt": 65, "confLt": 65, "floatMvFloor": 30, "floatMvGt": 100,
         "priceGt": 30, "bidAmtFloor": 3000, "chgFloor": 0, "chgGt": 9.5,
         "volRatioFloor": 1, "turnoverFloor": 1, "turnoverGt": 0, "spotExcludeZT": False}
    f.update(over)
    return f


def _seed_batch(uid, f, action="lock", auto_applied=0, bdate=BDATE, btime="09:26:00",
                ts=TS_0926, n=2):
    """种一条批次行(batch_date/batch_time/ts 可控)。auto_applied=1 且为系统批次时 uid 传 0"""
    filters_json = json.dumps({k: v for k, v in f.items() if k != "markets"},
                              ensure_ascii=False)
    markets = ",".join(f["markets"])
    conn = database.get_conn()
    cur = conn.execute(
        "INSERT INTO batches (batch_date, batch_time, ts, action, markets, filters, "
        "stock_count, user_id, auto_applied) VALUES (?,?,?,?,?,?,?,?,?)",
        (bdate, btime, ts, action, markets, filters_json, n, uid,
         1 if auto_applied else 0))
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid


def _batch_count(uid, action=None):
    conn = database.get_conn()
    if action is None:
        n = conn.execute("SELECT COUNT(*) FROM batches WHERE user_id=?", (uid,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM batches WHERE user_id=? AND action=?",
                         (uid, action)).fetchone()[0]
    conn.close()
    return n


def _last_batch_id(uid, action):
    conn = database.get_conn()
    r = conn.execute(
        "SELECT id FROM batches WHERE user_id=? AND action=? AND auto_applied=0 "
        "ORDER BY ts DESC LIMIT 1", (uid, action)).fetchone()
    conn.close()
    return r[0] if r else None


# ============================================================
# 纯函数: find_today_reusable_batch 选批逻辑
# ============================================================

def test_reuse_lock_same_params():
    """① 当日同参手动 lock → 直读命中 lock"""
    uid = 992101
    f = _f()
    bid = _seed_batch(uid, f)
    hit = history.find_today_reusable_batch(uid, f, now_ts=TS_0931)
    assert hit == (bid, "lock")


def test_reuse_lock_preferred_over_filter():
    """lock 与 filter 同参并存 → lock 优先(与前端 merge 权威名单同源)"""
    uid = 992102
    f = _f()
    _seed_batch(uid, f, action="filter", btime="09:40:00", ts=TS_0931 + 540)
    bid = _seed_batch(uid, f, action="lock", btime="09:26:00")   # lock 更早但权威
    assert history.find_today_reusable_batch(uid, f, now_ts=TS_0931) == (bid, "lock")


def test_reuse_filter_when_no_lock():
    """② 当日无 lock 仅同参 filter → 命中 filter"""
    uid = 992103
    f = _f()
    bid = _seed_batch(uid, f, action="filter", btime="09:40:00", ts=TS_0931 + 540)
    assert history.find_today_reusable_batch(uid, f, now_ts=TS_0931) == (bid, "filter")


def test_reuse_filter_latest_when_multi():
    """多条同参 filter → 取当日最近一条(ts desc 首个命中)"""
    uid = 992104
    f = _f()
    _seed_batch(uid, f, action="filter", btime="09:31:00", ts=TS_0931)
    bid = _seed_batch(uid, f, action="filter", btime="10:05:00", ts=TS_0931 + 2100)
    assert history.find_today_reusable_batch(uid, f, now_ts=TS_0931) == (bid, "filter")


def test_reuse_auto_skipped_when_empty_batch():
    """系统批次为空名单(stock_count=0) → 无直读价值, 跳过走原重算(避免直读空名单)"""
    uid = 992110
    _seed_batch(0, _f(), action="lock", auto_applied=1, n=0)   # stock_count=0
    assert history.find_today_reusable_batch(uid, _f(), now_ts=TS_0931) == (None, None)
    bid = _seed_batch(0, _f(), action="lock", auto_applied=1, n=5)   # 非空才可直读
    assert history.find_today_reusable_batch(uid, _f(), now_ts=TS_0931) == (bid, "auto")


def test_reuse_auto_when_no_manual_batch():
    """③ 用户当日无任何手动批次 + 当日系统批次存在 → 命中 auto(任意参数均可)"""
    uid = 992105
    f = _f(bidGt=9)                       # 参数与系统批次不必一致
    bid = _seed_batch(0, _f(), action="lock", auto_applied=1, btime="09:26:00")
    assert history.find_today_reusable_batch(uid, f, now_ts=TS_0931) == (bid, "auto")


def test_reuse_param_changed_with_own_batch_none():
    """用户当日有手动批次但参数已改(改条件未应用) → 不直读(即使系统批次存在也不 auto)"""
    uid = 992106
    _seed_batch(uid, _f(bidGt=7), action="lock")
    _seed_batch(0, _f(), action="lock", auto_applied=1)          # 系统批次也在
    assert history.find_today_reusable_batch(uid, _f(bidGt=6), now_ts=TS_0931) == (None, None)


def test_reuse_markets_order_insensitive():
    """markets 存储/请求顺序不同但集合相同 → 命中"""
    uid = 992107
    bid = _seed_batch(uid, _f(markets=["kcb", "cyb", "hs"]))     # 库里序 kcb,cyb,hs
    hit = history.find_today_reusable_batch(uid, _f(markets=["hs", "cyb", "kcb"]),
                                            now_ts=TS_0931)
    assert hit == (bid, "lock")


def test_reuse_other_day_not_match():
    """异日批次(昨日同参 lock) → 今日不直读"""
    uid = 992108
    _seed_batch(uid, _f(), bdate="2026-09-08", ts=TS_0926 - 86400)
    assert history.find_today_reusable_batch(uid, _f(), now_ts=TS_0931) == (None, None)


def test_reuse_no_batch_none():
    """当日无任何批次(新用户/无系统批次) → (None,None) → 原重算兜底"""
    assert history.find_today_reusable_batch(992109, _f(), now_ts=TS_0931) == (None, None)


# ============================================================
# API 集成: stocks.py refresh 直读拦截
# ============================================================

def _api_refresh(client, token, bidGt=7, tail=""):
    hdrs = {"Authorization": "Bearer " + token}
    url = ("/api/stocks?action=refresh&markets=hs,cyb,kcb&bidGt=%d&probLt=65&confLt=65"
           "&floatMvGt=100&priceGt=30&bidAmtFloor=3000" % bidGt)
    url += tail
    return client.get(url, headers=hdrs)


def _api_lock(client, token, bidGt=7):
    hdrs = {"Authorization": "Bearer " + token}
    url = ("/api/stocks?action=lock&markets=hs,cyb,kcb&bidGt=%d&probLt=65&confLt=65"
           "&floatMvGt=100&priceGt=30&bidAmtFloor=3000" % bidGt)
    return client.get(url, headers=hdrs)


def test_api_refresh_after930_reuses_lock(client, create_user_token, monkeypatch):
    """核心: 9:26 lock 落库后, 9:31 同参 refresh → 直读 lock 批次(reused), 不重落/不新增"""
    from app.services import fetcher, scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    u = create_user_token()
    token, uid = u["token"], u["uid"]

    d_lock = _api_lock(client, token).json()
    assert d_lock.get("ok") and d_lock.get("list"), "9:26 lock 应正常落库返回"
    bid_lock = _last_batch_id(uid, "lock")
    assert bid_lock, "lock 应已落库"
    codes_lock = sorted({s["code"] for s in d_lock["list"]})

    # 切到 9:31(9:30 后): 同参 refresh → 直读 lock 批次
    monkeypatch.setattr("time.time", lambda: TS_0931)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 31, False))
    # 打桩防真实拉全市场(直读路径的实时覆盖数据源)
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: {"600001": {"price": 10.5, "realChange": 3.2,
                                               "entityChange": 2.0, "volRatio": 2.1,
                                               "turnover": 5.5}})
    r = _api_refresh(client, token)
    d = r.json()
    assert r.status_code == 200 and d.get("ok")
    assert d.get("reused") is True and d.get("source") == "lock"
    assert d.get("batch_id") == bid_lock
    assert d.get("before930") is False
    codes_reuse = sorted({s["code"] for s in d.get("list", [])})
    assert codes_reuse == codes_lock, "直读名单应与 lock 批次一致"
    assert _batch_count(uid) == 1, "refresh 直读不应新增批次"
    assert d["list"][0].get("probability") is not None
    # 实时覆盖已生效(打桩的 600001 若在名单中则 price 为实时值而非 None)
    hit600 = next((s for s in d["list"] if s["code"] == "600001"), None)
    if hit600:
        assert hit600.get("price") == 10.5, "直读名单应带实时 price 覆盖"


def test_api_refresh_reuse_spotmap_fail_degrades(client, create_user_token, monkeypatch):
    """直读路径全市场行情拉取失败 → 降级返回定格名单(不带实时覆盖), 不 500(下轮轮询自愈)"""
    from app.services import fetcher, scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    u = create_user_token()
    token, uid = u["token"], u["uid"]
    assert _api_lock(client, token).json().get("ok")

    monkeypatch.setattr("time.time", lambda: TS_0931)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 31, False))
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map",
                        lambda fs: (_ for _ in ()).throw(RuntimeError("行情源全挂")))
    r = _api_refresh(client, token)
    d = r.json()
    assert r.status_code == 200 and d.get("ok"), "spotMap 失败应降级不 500"
    assert d.get("reused") is True and d.get("source") == "lock"
    assert d.get("spotMap") == {}, "降级时 spotMap 应为空"
    assert d.get("list"), "定格名单仍应返回"
    assert _batch_count(uid) == 1, "直读不应新增批次"


def test_api_refresh_after930_param_changed_recomputes(client, create_user_token, monkeypatch):
    """9:31 参数已改(bidGt 7→6)的 refresh → 指纹不匹配 → 不直读, 走原重算兜底"""
    from app.services import scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    u = create_user_token()
    token, uid = u["token"], u["uid"]
    assert _api_lock(client, token).json().get("ok")
    bid_lock = _last_batch_id(uid, "lock")
    assert bid_lock

    monkeypatch.setattr("time.time", lambda: TS_0931)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 31, False))
    r = _api_refresh(client, token, bidGt=6)
    d = r.json()
    assert r.status_code == 200 and d.get("ok"), "参数不同应正常重算返回"
    assert not d.get("reused"), "参数已改不应直读"
    assert _batch_count(uid) == 1, "refresh 兜底重算不落库(语义同现状)"
