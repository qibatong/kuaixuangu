# -*- coding: utf-8 -*-
"""2026-09-02 lock 当日幂等: 9:25 后同参手动 lock 直读当日批次, 不再全量重拉/重复落库

背景: 每次重新登录都会触发一次 lock(action=lock 9:30 前唯一锁定+推送), 重算全市场行情并堆 lock 历史。
方案(主人确认): 9:30 前页面自动 lock 若当日已存在「9:25 后落库 + 同筛选参数」的手动 lock 批次
  (auto_applied=0) → 直读该批次返回(idempotent=True), 跳过重拉/落库/推送;
  用户主动点「锁定」(force=1)、参数已改、9:25 前落库的批次、异日批次 → 均不幂等, 正常重算。

测试策略:
- 幂等判定用「库内批次 batch_time>=09:25:00 + 与请求同参」, save_batch 写真实时间(与 mock 的
  scorer.bj_now 无关) → 通过 monkeypatch time.time 固定"当前时刻"(北京时间 09:20/09:26),
  使落库批次与匹配查询都落在同一确定时刻, 用例与真实运行时刻无关。
- 纯函数用例直接 SQL 种批次行(batch_date/batch_time 可控) + now_ts 注入, 单测匹配门控。
"""
import calendar
import json

import pytest

from app.db import database
from app.services import history

# ---- 固定"当前时刻": 2026-09-09(周三) 北京时间 09:20 / 09:26 (UTC 前一日 01:20/01:26) ----
TS_0920 = calendar.timegm((2026, 9, 9, 1, 20, 0, 0, 0, 0))   # 北京 09:20:00 (9:25 前, 不幂等)
TS_0926 = calendar.timegm((2026, 9, 9, 1, 26, 0, 0, 0, 0))   # 北京 09:26:00 (9:25 后, 可幂等)
BDATE = "2026-09-09"                                          # 上面两时刻对应的北京时间日期


@pytest.fixture(autouse=True)
def _no_global_side_effects(monkeypatch):
    """屏蔽 API lock 路径的全局副作用:
    - stats.record_daily_yizi: 本文件用固定虚拟日期(2026-09-09)跑 lock, 若不屏蔽会向共享
      daily_yizi 统计表写入该日期行, 污染同 session 里 test_phase1 的"当日仅1行"断言
    - notify.push_result_async 由各用例自行按需打桩(需计数断言)"""
    from app.services import stats
    monkeypatch.setattr(stats, "record_daily_yizi", lambda raw: {"yizi_count": 0, "bid_amt": 0.0})


def _mk_result(n=3):
    return [
        {"code": "60000%d" % i, "name": "测试%d" % i, "probability": 80 + i,
         "confidence": 1, "bidChange": 5.0, "realChange": 3.0, "entityChange": 2.0,
         "bidTurnover": 20.0, "warnType": 1, "circulationMV": 50.0,
         "industry": "x", "concept": "y", "bidAmt": 1e8, "bidRatio": 1.0}
        for i in range(1, n + 1)]


def _f(**over):
    """与 scorer.validate_filters 输出同构的筛选参数(纯函数用例用, 幂等指纹需一致)"""
    f = {"stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
         "bidGt": 7, "probLt": 65, "confLt": 65, "floatMvFloor": 30, "floatMvGt": 100,
         "priceGt": 30, "bidAmtFloor": 3000, "chgFloor": 0, "chgGt": 9.5,
         "volRatioFloor": 1, "turnoverFloor": 1, "turnoverGt": 0, "spotExcludeZT": False}
    f.update(over)
    return f


def _seed_lock(uid, f, bdate=BDATE, btime="09:26:00", auto_applied=0, ts=TS_0926, n=2):
    """直接种一条 lock 批次行(batch_date/batch_time 可控), 返回 batch_id"""
    filters_json = json.dumps({k: v for k, v in f.items() if k != "markets"},
                              ensure_ascii=False)
    markets = ",".join(f["markets"])
    conn = database.get_conn()
    cur = conn.execute(
        "INSERT INTO batches (batch_date, batch_time, ts, action, markets, filters, "
        "stock_count, user_id, auto_applied) VALUES (?,?,?,?,?,?,?,?,?)",
        (bdate, btime, ts, "lock", markets, filters_json, n, uid,
         1 if auto_applied else 0))
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid


def _count_lock(uid, auto_applied=None):
    conn = database.get_conn()
    if auto_applied is None:
        n = conn.execute("SELECT COUNT(*) FROM batches WHERE user_id=? AND action='lock'",
                         (uid,)).fetchone()[0]
    else:
        n = conn.execute(
            "SELECT COUNT(*) FROM batches WHERE user_id=? AND action='lock' AND auto_applied=?",
            (uid, int(auto_applied))).fetchone()[0]
    conn.close()
    return n


def _last_lock_id(uid):
    conn = database.get_conn()
    r = conn.execute(
        "SELECT id FROM batches WHERE user_id=? AND action='lock' "
        "AND auto_applied=0 ORDER BY ts DESC LIMIT 1", (uid,)).fetchone()
    conn.close()
    return r[0] if r else None


# ============================================================
# 纯函数: find_today_lock_matching / find_today_lock
# ============================================================

def test_matching_same_params_returns_batch():
    """9:25 后落库 + 同筛选参数的手动 lock → 幂等命中"""
    uid = 992001
    f = _f()
    bid = _seed_lock(uid, f, btime="09:26:00")
    hit = history.find_today_lock_matching(uid, f, now_ts=TS_0926)
    assert hit and hit["id"] == bid, "同参 9:25 后 lock 应命中"
    assert hit["auto_applied"] == 0


def test_matching_markets_order_insensitive():
    """markets 存储顺序与请求顺序不同但集合相同 → 仍命中"""
    uid = 992002
    bid = _seed_lock(uid, _f(markets=["kcb", "cyb", "hs"]))   # 库里顺序 kcb,cyb,hs
    hit = history.find_today_lock_matching(uid, _f(markets=["hs", "cyb", "kcb"]),
                                           now_ts=TS_0926)
    assert hit and hit["id"] == bid


def test_matching_param_value_changed_miss():
    """参数值不同(如 bidGt 7→6) → 不命中(参数改了重算是对的)"""
    uid = 992003
    _seed_lock(uid, _f(bidGt=7))
    assert history.find_today_lock_matching(uid, _f(bidGt=6), now_ts=TS_0926) is None
    assert history.find_today_lock_matching(uid, _f(bidGt=7), now_ts=TS_0926), "原参数仍命中"


def test_matching_before925_row_not_match():
    """9:25 前落库的 lock(数据未定型) → 不幂等, 9:25 后重新进入页面应重算"""
    uid = 992004
    _seed_lock(uid, _f(), btime="09:20:00", ts=TS_0920)
    assert history.find_today_lock_matching(uid, _f(), now_ts=TS_0926) is None


def test_matching_other_day_not_match():
    """异日批次(昨日的同参 lock) → 不幂等, 今天应重新锁定"""
    uid = 992005
    _seed_lock(uid, _f(), bdate="2026-09-08", btime="09:26:00", ts=TS_0920 - 86400)
    assert history.find_today_lock_matching(uid, _f(), now_ts=TS_0926) is None


def test_matching_auto_applied_excluded():
    """9:26 系统自动应用(auto_applied=1)的批次不算用户手动 lock → 不拦截手动 lock"""
    uid = 992006
    _seed_lock(uid, _f(), auto_applied=1)
    assert history.find_today_lock_matching(uid, _f(), now_ts=TS_0926) is None
    # 补一条手动 lock 后命中
    bid = _seed_lock(uid, _f(), auto_applied=0)
    hit = history.find_today_lock_matching(uid, _f(), now_ts=TS_0926)
    assert hit and hit["id"] == bid


def test_matching_no_row_returns_none():
    """当日无任何 lock → None"""
    assert history.find_today_lock_matching(992007, _f(), now_ts=TS_0926) is None


def test_find_today_lock_manual_only():
    """find_today_lock 只认当日最近手动 lock(auto_applied=0), 自动批次不返回"""
    uid = 992008
    _seed_lock(uid, _f(), btime="09:26:00", auto_applied=1)
    assert history.find_today_lock(uid, now_ts=TS_0926) is None
    bid = _seed_lock(uid, _f(), btime="09:27:00", auto_applied=0)
    hit = history.find_today_lock(uid, now_ts=TS_0926)
    assert hit and hit["id"] == bid


def test_get_batch_stocks_mapped_camelcase():
    """幂等直读映射: 数据库 snake_case → 前端 camelCase 结构"""
    uid = 992009
    f = _f()
    bid = _seed_lock(uid, f, n=2)
    conn = database.get_conn()
    conn.execute(
        "INSERT INTO batch_stocks (batch_id, rank, code, name, probability, confidence, "
        "bid_change, real_change, entity_change, bid_turnover, warn_type, circulation_mv, "
        "industry, concept, bid_amt, bid_ratio, qiangchou) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bid, 1, "600001", "测试甲", 92.5, 1, 5.1, 3.2, 2.0, 18.5, 2, 50.0,
         "软件", "AI概念", 1.2e8, 2.5, 1))
    conn.commit()
    conn.close()
    rows = history.get_batch_stocks_mapped(bid)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "600001" and r["name"] == "测试甲"
    assert r["probability"] == 92.5 and r["confidence"] == 1
    assert r["bidChange"] == 5.1 and r["realChange"] == 3.2 and r["entityChange"] == 2.0
    assert r["bidTurnover"] == 18.5 and r["warnType"] == 2
    assert r["circulationMV"] == 50.0 and r["industry"] == "软件" and r["concept"] == "AI概念"
    assert r["bidAmt"] == 1.2e8 and r["bidRatio"] == 2.5 and r["qiangchou"] == 1


# ============================================================
# API 集成: stocks.py 幂等拦截(固定"当前时刻", 与真实运行时间无关)
# ============================================================

def _api_lock(client, token, force=False, bidGt=7, tail=""):
    hdrs = {"Authorization": "Bearer " + token}
    url = ("/api/stocks?action=lock&markets=hs,cyb,kcb&bidGt=%d&probLt=65&confLt=65"
           "&floatMvGt=100&priceGt=30&bidAmtFloor=3000" % bidGt)
    if force:
        url += "&force=1"
    url += tail
    return client.get(url, headers=hdrs)


def test_api_lock_same_params_idempotent_after925(client, create_user_token, monkeypatch):
    """核心场景: 9:25 后(固定 09:26)同参自动 lock 第二次 → 幂等直读, 不重拉/不重落/不重复推送"""
    from app.services import notify, scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    pushes = []
    monkeypatch.setattr(notify, "push_result_async", lambda result, f: pushes.append(1))
    u = create_user_token()
    token, uid = u["token"], u["uid"]

    r1 = _api_lock(client, token)
    d1 = r1.json()
    assert r1.status_code == 200 and d1.get("ok") and d1.get("list"), "首次 lock 应正常返回名单"
    assert not d1.get("idempotent"), "首次无同参批次, 不应命中幂等"
    assert _count_lock(uid) == 1 and len(pushes) == 1
    bid1 = _last_lock_id(uid)          # 正常 lock 响应不含 batch_id, 从库取首次批次
    assert bid1, "首次 lock 应已落库"

    # 同参再锁一次(自动请求, 无 force) → 幂等直读当日批次, 不新增/不推送
    r2 = _api_lock(client, token)
    d2 = r2.json()
    assert r2.status_code == 200 and d2.get("ok")
    assert d2.get("idempotent") is True and d2.get("batch_id") == bid1
    assert _count_lock(uid) == 1, "同参幂等后不应新增批次"
    assert len(pushes) == 1, "幂等直读不应重复推送"
    assert d2.get("count") == len(d2.get("list", [])) and d2["count"] > 0
    assert d2.get("dataTime") == TS_0926, "dataTime 应为当日批次落库时刻"
    # 直读名单与首次计算名单同码集(实时字段缺失由前端容错, 核心字段保留)
    c1 = sorted({s["code"] for s in d1["list"]})
    c2 = sorted({s["code"] for s in d2["list"]})
    assert c1 == c2, "幂等直读名单应与首次锁定名单一致"
    assert d2["list"][0].get("probability") is not None, "核心评分字段应存在"


def test_api_lock_force_bypasses_idempotent(client, create_user_token, monkeypatch):
    """用户主动点「锁定」(force=1) → 绕过幂等, 每次重算落新批次(推送语义不变)"""
    from app.services import notify, scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    pushes = []
    monkeypatch.setattr(notify, "push_result_async", lambda result, f: pushes.append(1))
    u = create_user_token()
    token, uid = u["token"], u["uid"]

    d1 = _api_lock(client, token, force=True).json()
    assert d1.get("ok") and not d1.get("idempotent")
    d2 = _api_lock(client, token, force=True).json()
    assert d2.get("ok") and not d2.get("idempotent"), "force 重锁不应命中幂等"
    assert _count_lock(uid) == 2, "force 两次同参应各落一条"
    assert len(pushes) == 2, "force 重锁每次都应推送"


def test_api_lock_before925_not_idempotent(client, create_user_token, monkeypatch):
    """9:25 前(固定 09:20)每次进入页面自动 lock 都重算落库(数据未定型, 拿最新)"""
    from app.services import scorer
    monkeypatch.setattr("time.time", lambda: TS_0920)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 20, True))
    u = create_user_token()
    token, uid = u["token"], u["uid"]

    d1 = _api_lock(client, token).json()
    assert d1.get("ok") and not d1.get("idempotent")
    d2 = _api_lock(client, token).json()
    assert d2.get("ok") and not d2.get("idempotent"), "9:25 前不应幂等, 应重算拿最新"
    assert _count_lock(uid) == 2, "9:25 前两次同参 lock 应各落一条"


def test_api_lock_param_changed_not_idempotent(client, create_user_token, monkeypatch):
    """参数已改(bidGt 7→6)的自动 lock → 不幂等, 重算落新批次"""
    from app.services import scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    u = create_user_token()
    token, uid = u["token"], u["uid"]

    d1 = _api_lock(client, token, bidGt=7).json()
    assert d1.get("ok")
    d2 = _api_lock(client, token, bidGt=6).json()
    assert d2.get("ok") and not d2.get("idempotent"), "参数不同应重算"
    assert _count_lock(uid) == 2, "不同参数应新增批次"
    # 再以原参数 lock → 命中原 7 的批次(参数指纹一致才幂等)
    d3 = _api_lock(client, token, bidGt=7).json()
    assert d3.get("ok") and d3.get("idempotent") is True
    assert _count_lock(uid) == 2, "回到原参数应幂等直读, 不新增"


def test_api_lock_auto_applied_not_block_manual(client, create_user_token, monkeypatch):
    """当日已有 9:26 系统自动应用(auto_applied=1)同参批次 → 不拦截用户手动 lock"""
    from app.services import scorer
    monkeypatch.setattr("time.time", lambda: TS_0926)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 26, True))
    u = create_user_token()
    token, uid = u["token"], u["uid"]

    _seed_lock(uid, _f(), btime="09:26:00", auto_applied=1)   # 种一条系统自动应用
    assert _count_lock(uid, auto_applied=0) == 0
    d = _api_lock(client, token).json()
    assert d.get("ok") and not d.get("idempotent"), "auto_applied 批次不应拦截手动 lock"
    assert _count_lock(uid, auto_applied=0) == 1, "手动 lock 应正常落一条"
