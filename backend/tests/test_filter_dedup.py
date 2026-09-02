# -*- coding: utf-8 -*-
"""2026-09-02 后端同参去重: 同一用户 60s 内相同筛选参数(action=filter)不重复落库

背景: 外部自动化脚本循环"登录→应用筛选", 一天刷出 50 条相同参数的历史批次。
方案: stocks.py filter 落库前调用 history.recent_same_filter(uid, f, window=60),
      命中最近批次则跳过落库。lock/system_batch(auto)/auto_apply 不受影响。
"""
import json
import time

from app.services import history
from app.db import database


def _mk_result(n=3):
    return [
        {"code": "60000%d" % i, "name": "测试%d" % i, "probability": 80 + i,
         "confidence": 1, "bidChange": 5.0, "realChange": 3.0, "entityChange": 2.0,
         "bidTurnover": 20.0, "warnType": 1, "circulationMV": 50.0,
         "industry": "x", "concept": "y", "bidAmt": 1e8, "bidRatio": 1.0}
        for i in range(1, n + 1)]


def _f(**over):
    f = {"markets": ["hs", "cyb", "kcb"], "bidGt": 7, "probLt": 65, "confLt": 65,
         "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000,
         "stSuspend": True, "limitUp": True}
    f.update(over)
    return f


def _count_filter(uid, ts_from=0):
    conn = database.get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM batches WHERE user_id=? AND action='filter' AND ts>=?",
        (uid, ts_from)).fetchone()[0]
    conn.close()
    return n


def test_same_filter_within_window_returns_batch():
    """60s 窗口内同 uid 同 filter 参数 → recent_same_filter 命中刚才落的批次"""
    uid = 991001
    f = _f()
    bid = history.save_batch(uid, "filter", _mk_result(), f)
    assert bid
    hit = history.recent_same_filter(uid, f, window=60)
    assert hit == bid, "同参数应立即命中"


def test_same_filter_markets_order_insensitive():
    """markets 顺序不同但集合相同(前端 hs,cyb 与 cyb,hs) → 仍命中"""
    uid = 991002
    bid = history.save_batch(uid, "filter", _mk_result(), _f(markets=["hs", "cyb", "kcb"]))
    hit = history.recent_same_filter(uid, _f(markets=["kcb", "cyb", "hs"]), window=60)
    assert hit == bid


def test_different_filter_value_not_dedup():
    """参数值不同(如 bidGt 7→8) → 不命中, 正常再落一条"""
    uid = 991003
    history.save_batch(uid, "filter", _mk_result(), _f(bidGt=7))
    assert history.recent_same_filter(uid, _f(bidGt=8), window=60) is None
    assert history.recent_same_filter(uid, _f(bidGt=7), window=60), "原参数仍应命中"


def test_different_user_not_dedup():
    """不同 uid 即使参数相同也不互相去重(用户隔离)"""
    bid_a = history.save_batch(991004, "filter", _mk_result(), _f())
    assert history.recent_same_filter(991005, _f(), window=60) is None
    assert history.recent_same_filter(991004, _f(), window=60) == bid_a


def test_window_expired_not_dedup():
    """超过 60s 窗口的老批次不拦新落库(人为造一条 ts=now-120 的旧批次)"""
    uid = 991006
    f = _f()
    filters_json = json.dumps({k: v for k, v in f.items() if k != "markets"},
                              ensure_ascii=False)
    markets = ",".join(f["markets"])
    t = int(time.time())
    conn = database.get_conn()
    cur = conn.execute(
        "INSERT INTO batches (batch_date, batch_time, ts, action, markets, filters, stock_count, user_id, auto_applied) "
        "VALUES ('2026-09-02', '10:00:00', ?, 'filter', ?, ?, 1, ?, 0)",
        (t - 120, markets, filters_json, uid))
    conn.commit()
    old_id = cur.lastrowid
    conn.close()
    # 窗口 60s: 老批次(120s 前)不在窗口内 → 不命中
    assert history.recent_same_filter(uid, f, window=60) is None
    # 窗口放宽到 300s → 命中老批次
    assert history.recent_same_filter(uid, f, window=300) == old_id


def test_lock_not_checked_by_recent_same_filter():
    """lock 批次不影响 filter 去重判断(函数只查 action='filter')"""
    uid = 991007
    f = _f()
    history.save_batch(uid, "lock", _mk_result(), f)
    # 只有 lock 无 filter → filter 去重查不到
    assert history.recent_same_filter(uid, f, window=60) is None


def test_api_filter_same_params_saves_once(client, create_user_token):
    """API 集成: 连续两次同参 filter 只落 1 条历史(第二次被 60s 去重拦截)"""
    u = create_user_token()
    token, uid = u["token"], u["uid"]
    hdrs = {"Authorization": "Bearer " + token}
    base = "/api/stocks?action=filter&markets=hs,cyb,kcb&bidGt=7&probLt=65&confLt=65"
    r1 = client.get(base + "&floatMvGt=100&priceGt=30&bidAmtFloor=3000", headers=hdrs)
    assert r1.status_code == 200 and r1.json().get("ok")
    assert _count_filter(uid) == 1, "第一次 filter 应落 1 条"
    # 同参立即再点一次(60s 内) → 去重, 不新增
    r2 = client.get(base + "&floatMvGt=100&priceGt=30&bidAmtFloor=3000", headers=hdrs)
    assert r2.status_code == 200 and r2.json().get("ok")
    assert _count_filter(uid) == 1, "第二次同参应被去重, 不新增批次"
    # 改一个参数(超窗口判定改为不同参数) → 应正常新增第 2 条
    r3 = client.get(base.replace("bidGt=7", "bidGt=8") + "&floatMvGt=100&priceGt=30&bidAmtFloor=3000",
                    headers=hdrs)
    assert r3.status_code == 200 and r3.json().get("ok")
    assert _count_filter(uid) == 2, "不同参数应新增批次"


def test_api_lock_still_saves_each_time(client, create_user_token, monkeypatch):
    """lock 语义不受影响: 连续两次同参 lock 每次都会落库(9:30 前唯一锁定+推送场景)"""
    u = create_user_token()
    token, uid = u["token"], u["uid"]
    hdrs = {"Authorization": "Bearer " + token}
    base = "/api/stocks?action=lock&markets=hs,cyb,kcb&bidGt=7&probLt=65&confLt=65" \
           "&floatMvGt=100&priceGt=30&bidAmtFloor=3000"
    from app.services import scorer
    # conftest mock: lock 在 before930=True 时放行; 这里强制当前视为盘前场景仅验证落库次数
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 20, True))
    r1 = client.get(base, headers=hdrs)
    r2 = client.get(base, headers=hdrs)
    assert r1.status_code == 200 and r2.status_code == 200
    conn = database.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM batches WHERE user_id=? AND action='lock'",
                     (uid,)).fetchone()[0]
    conn.close()
    assert n == 2, "lock 两次同参应各落一条(不去重)"
