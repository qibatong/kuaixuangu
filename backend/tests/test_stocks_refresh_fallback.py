# -*- coding: utf-8 -*-
"""休市/当日无批次回退最近交易日直读 (2026-09-05 主人需求)
「多用户反馈: 平台关闭后再打开首页就能显示关闭前选出的股, 别一进来就转圈」

场景: 开盘日 9:30 后(当日系统批次空)以及休市时间, 首屏 action=refresh 原先
find_today_reusable_batch 只找当日 → 非交易日必 miss → 全量重算 2.6s 转圈。
新增 find_recent_reusable_batch 回退 14 天窗口内最近同参批次直读, 响应带 reusedDate。
"""
import time

import pytest

from app.services import history
from app.services.cache_store import store


@pytest.fixture(autouse=True)
def _clean_stocks_cache():
    store.clear_prefix("stocks_refresh:")
    yield
    store.clear_prefix("stocks_refresh:")


F = {"markets": ["hs", "cyb"], "probLt": 65, "confLt": 65}   # 默认参数样例



def _uid(user):
    """create_user_token 返回 {uid, token, ...}; 直接取 uid"""
    return user["uid"]

def _mk_batch(conn, uid, action, date, ts, f, auto_applied=0, count=3):
    """直接插一条批次(绕过 API, 精确控制日期/参数)"""
    import json
    g = time.gmtime(ts + 8 * 3600)
    btime = "%02d:%02d:%02d" % (g.tm_hour, g.tm_min, g.tm_sec)
    cur = conn.execute(
        "INSERT INTO batches(user_id, action, batch_date, batch_time, ts, filters, markets, "
        "auto_applied, stock_count) VALUES(?,?,?,?,?,?,?,?,?)",
        (uid, action, date, btime, ts, json.dumps(f), ",".join(sorted(f["markets"])),
         auto_applied, count))
    bid = cur.lastrowid
    for i in range(count):
        conn.execute(
            "INSERT INTO batch_stocks(batch_id, code, name, rank, probability, confidence, "
            "bid_change, real_change, entity_change, bid_turnover, warn_type, "
            "circulation_mv, bid_amt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, f"60{i:04d}", f"股{i}", i + 1, 0.9, 0.8, 10.0, 5.0, 5.0, 2.0, 3, 5.0e9, 5000.0))
    conn.commit()
    return bid


@pytest.fixture
def conn():
    from app.db import database
    c = database.get_conn()
    yield c
    try:
        c.close()
    except Exception:
        pass


def test_recent_fallback_same_param_lock(conn, create_user_token):
    """当日无批次 + 窗口内有同参 lock → 回退该批次, 返回 batch_date"""
    uid = _uid(create_user_token())
    old_ts = time.time() - 3 * 86400
    _mk_batch(conn, uid, "lock", "2026-09-01", old_ts, F)
    bid, src, date = history.find_recent_reusable_batch(uid, F)
    assert bid and src == "lock" and date == "2026-09-01"


def test_recent_fallback_prefers_today_over_old(conn, create_user_token):
    """当日有批次时优先当日(回退只在当日 miss 时触发——由调用方保证, 这里验证
    recent 版自身取最近 ts 的同参 lock)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", "2026-09-01", time.time() - 3 * 86400, F)
    _mk_batch(conn, uid, "lock", "2026-09-04", time.time() - 86400, F)
    bid, src, date = history.find_recent_reusable_batch(uid, F)
    assert date == "2026-09-04", "窗口内应取最近日期的同参批次"


def test_recent_fallback_filter_when_no_lock(conn, create_user_token):
    """窗口内有同参 filter 无 lock → 回退 filter 批次"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "filter", "2026-09-03", time.time() - 2 * 86400, F)
    bid, src, date = history.find_recent_reusable_batch(uid, F)
    assert src == "filter" and date == "2026-09-03"


def test_recent_fallback_auto_when_no_manual(conn, create_user_token):
    """窗口内无任何手动批次 → 回退最近系统统一批次(auto)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, 0, "lock", "2026-09-03", time.time() - 2 * 86400, F, auto_applied=1)
    bid, src, date = history.find_recent_reusable_batch(uid, F)
    assert src == "auto", "无手动批次应回退系统统一批次"


def test_recent_no_fallback_when_param_changed(conn, create_user_token):
    """有手动批次但参数不一致(用户改过条件) → 不回退(走重算)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", "2026-09-03", time.time() - 2 * 86400, F)
    changed = dict(F, probLt=80)          # 改过阈值 → 指纹不同
    bid, src, date = history.find_recent_reusable_batch(uid, changed)
    assert bid is None, "参数不一致不应回退旧名单"


def test_recent_no_fallback_when_manual_exists_but_diff_param(conn, create_user_token):
    """窗口内有手动批次(参数不同)时, 也不允许系统批次兜底(与当日版语义一致:
    有手动批次但参数已改 → 必须重算, 否则改条件后错误直读系统名单)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", "2026-09-03", time.time() - 2 * 86400, F)
    _mk_batch(conn, 0, "lock", "2026-09-03", time.time() - 2 * 86400, F, auto_applied=1)
    changed = dict(F, confLt=80)
    bid, src, date = history.find_recent_reusable_batch(uid, changed)
    assert bid is None


def test_recent_empty_batch_skipped(conn, create_user_token):
    """空名单批次(stock_count=0)无直读价值 → 跳过"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", "2026-09-03", time.time() - 2 * 86400, F, count=0)
    bid, src, date = history.find_recent_reusable_batch(uid, F)
    assert bid is None, "空批次不应被回退命中"


def test_api_stocks_refresh_fallback_http(client, create_user_token, monkeypatch):
    """P0 端到端: 9:30 后 refresh, 当日无批次 → 直读回退批次, 响应含 reusedDate,
    全市场选股链路(picker.pipeline.run)不被调用(不转圈重算)"""
    from app.services import fetcher, scorer
    from app.services.picker import pipeline as pl

    monkeypatch.setattr(scorer, "bj_now", lambda: (10, 30, False))   # 9:30 后
    calls = {"n": 0}
    # 2026-09-11: 打桩目标从 scorer.process_all_stocks(老链路, 已退役)改为唯一链路
    monkeypatch.setattr(pl, "run",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or pl.PipelineResult())
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})

    # 3 天前同参 lock 批次(当日无任何批次)
    from app.db import database
    u = create_user_token()
    # 批次 filters 用后端 validate_filters 的**完整输出**构造 —— 请求参数经
    # validate_filters 会补全默认值, 指纹按完整 dict 计算; 测试批次若只存残缺
    # 参数则指纹必然不同, 测不出回退
    from app.services import scorer
    f_full = scorer.validate_filters({"markets": ["hs,cyb"], "probLt": ["65"], "confLt": ["65"]})
    conn = database.get_conn()
    try:
        _mk_batch(conn, u["uid"], "lock", "2026-09-01", time.time() - 3 * 86400, f_full)
    finally:
        conn.close()

    h = {"Authorization": "Bearer " + u["token"]}
    r = client.get("/api/stocks?action=refresh&strategy=auction&markets=hs,cyb&probLt=65&confLt=65",
                   headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("reused") is True and d.get("reusedDate") == "2026-09-01", d
    assert calls["n"] == 0, "回退直读不应触发全市场重算(转圈根因)"
