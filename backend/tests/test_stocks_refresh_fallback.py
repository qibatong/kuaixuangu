# -*- coding: utf-8 -*-
"""休市/当日无批次回退最近交易日直读 (2026-09-05 主人需求)
「多用户反馈: 平台关闭后再打开首页就能显示关闭前选出的股, 别一进来就转圈」

场景: 开盘日 9:30 后(当日系统批次空)以及休市时间, 首屏 action=refresh 原先
find_today_reusable_batch 只找当日 → 非交易日必 miss → 全量重算 2.6s 转圈。
新增 find_recent_reusable_batch 回退 14 天窗口内最近同参批次直读, 响应带 reusedDate。
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services import history
from app.services.cache_store import store


# 🔴 2026-09-17 (v4.11.27): 本文件全部用例改为**注入固定锚点时刻**。
#
# 原实现把 `batch_date` 写成字面量("2026-09-01"/"2026-09-03")而 `ts` 与**窗口**都按
# 真实"今天"算 —— `find_recent_reusable_batch` 的窗口是按 `batch_date` **字符串**比的
# (`batch_date >= 今天-14天`), 于是字面量随日历推进**必然出窗** → 用例必红
# (2026-09-17 时窗口起点 = 09-03, 写死的 09-01 出窗; 且还会命中毒到的 uid=0 系统批次
#  拿回 09-03, 表现为"实得 09-03 期望 09-01")。放久了每天都在红。
#
# 现在: 用 `_ANCHOR` 固定"现在", 日期一律由锚点相对推算 + 显式 `now_ts=` 注入,
# 与运行日彻底解耦; 并补一条**窗口边界契约**用例把 batch_date 口径钉住。
_BJ = timezone(timedelta(hours=8))
_ANCHOR = datetime(2026, 9, 15, 10, 30, tzinfo=_BJ)      # 锚点"现在"(北京时间)
_ANCHOR_TS = _ANCHOR.timestamp()


def _d(delta_days):
    """锚点前后 N 天的北京时间日期 —— 取代字面量, 防日历漂移"""
    return (_ANCHOR + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def _ts(delta_days, hh=10, mm=30):
    """锚点前后 N 天的时刻 epoch(供 _mk_batch 算 batch_time)"""
    dt = (_ANCHOR + timedelta(days=delta_days)).replace(hour=hh, minute=mm)
    return dt.timestamp()


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
    _mk_batch(conn, uid, "lock", _d(-3), _ts(-3), F)
    bid, src, date = history.find_recent_reusable_batch(uid, F, now_ts=_ANCHOR_TS)
    assert bid and src == "lock" and date == _d(-3)


def test_recent_window_boundary_is_batch_date_based(conn, create_user_token):
    """**窗口口径契约**: 窗口按 `batch_date` 字符串比(`>= 今天-14天`), **不是按 ts**。

    把这条口径钉成显式用例 —— 此前正因为"ts 是 3 天前但 batch_date 写在窗口外"
    导致用例随日历漂移必红, 而失败信息完全看不出是窗口口径问题。
    """
    # ① 出窗: ts 极"新"(锚点当天), 但 batch_date = 锚点-15 天 → SQL 就把它排除了
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", _d(-15), _ts(0), F)
    bid, src, date = history.find_recent_reusable_batch(uid, F, now_ts=_ANCHOR_TS)
    assert date != _d(-15), "batch_date 出窗者不应被回退命中(窗口按 batch_date 算, 不看 ts)"

    # ② 边界: 锚点-14 天恰好在窗口内(`>=` 闭区间) → 必命中
    uid2 = _uid(create_user_token())
    _mk_batch(conn, uid2, "lock", _d(-14), _ts(-14), F)
    bid2, src2, date2 = history.find_recent_reusable_batch(uid2, F, now_ts=_ANCHOR_TS)
    assert bid2 and date2 == _d(-14), "锚点-14 天应恰在窗口内(闭区间)"


def test_recent_fallback_prefers_today_over_old(conn, create_user_token):
    """当日有批次时优先当日(回退只在当日 miss 时触发——由调用方保证, 这里验证
    recent 版自身取最近 ts 的同参 lock)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", _d(-3), _ts(-3), F)
    _mk_batch(conn, uid, "lock", _d(-1), _ts(-1), F)
    bid, src, date = history.find_recent_reusable_batch(uid, F, now_ts=_ANCHOR_TS)
    assert date == _d(-1), "窗口内应取最近日期的同参批次"


def test_recent_fallback_filter_when_no_lock(conn, create_user_token):
    """窗口内有同参 filter 无 lock → 回退 filter 批次"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "filter", _d(-2), _ts(-2), F)
    bid, src, date = history.find_recent_reusable_batch(uid, F, now_ts=_ANCHOR_TS)
    assert src == "filter" and date == _d(-2)


def test_recent_fallback_auto_when_no_manual(conn, create_user_token):
    """窗口内无任何手动批次 → 回退最近系统统一批次(auto)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, 0, "lock", _d(-2), _ts(-2), F, auto_applied=1)
    bid, src, date = history.find_recent_reusable_batch(uid, F, now_ts=_ANCHOR_TS)
    assert src == "auto", "无手动批次应回退系统统一批次"


def test_recent_no_fallback_when_param_changed(conn, create_user_token):
    """有手动批次但参数不一致(用户改过条件) → 不回退(走重算)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", _d(-2), _ts(-2), F)
    changed = dict(F, probLt=80)          # 改过阈值 → 指纹不同
    bid, src, date = history.find_recent_reusable_batch(uid, changed, now_ts=_ANCHOR_TS)
    assert bid is None, "参数不一致不应回退旧名单"


def test_recent_no_fallback_when_manual_exists_but_diff_param(conn, create_user_token):
    """窗口内有手动批次(参数不同)时, 也不允许系统批次兜底(与当日版语义一致:
    有手动批次但参数已改 → 必须重算, 否则改条件后错误直读系统名单)"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", _d(-2), _ts(-2), F)
    _mk_batch(conn, 0, "lock", _d(-2), _ts(-2), F, auto_applied=1)
    changed = dict(F, confLt=80)
    bid, src, date = history.find_recent_reusable_batch(uid, changed, now_ts=_ANCHOR_TS)
    assert bid is None


def test_recent_empty_batch_skipped(conn, create_user_token):
    """空名单批次(stock_count=0)无直读价值 → 跳过"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", _d(-2), _ts(-2), F, count=0)
    bid, src, date = history.find_recent_reusable_batch(uid, F, now_ts=_ANCHOR_TS)
    assert bid is None, "空批次不应被回退命中"


def test_api_stocks_refresh_fallback_http(client, create_user_token, monkeypatch):
    """P0 端到端: 9:30 后 refresh, 当日无批次 → 直读回退批次, 响应含 reusedDate,
    全市场选股链路(picker.pipeline.run)不被调用(不转圈重算)

    🔴 v4.11.27 起: 该用例走的是 **HTTP 链路**, 而 handler 内的
    `find_recent_reusable_batch(uid, f)` **不传 now_ts**(用真实时间)。为保持与真实
    日历解耦, 这里把模块属性包一层注入锚点时刻 —— **被测逻辑仍是线上那份**
    (只换 now_ts), 不是打桩替换。
    """
    from app.services import fetcher, scorer
    from app.services.picker import pipeline as pl

    monkeypatch.setattr(scorer, "bj_now", lambda: (10, 30, False))   # 9:30 后
    calls = {"n": 0}
    # 2026-09-11: 打桩目标从 scorer.process_all_stocks(老链路, 已退役)改为唯一链路
    monkeypatch.setattr(pl, "run",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or pl.PipelineResult())
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})

    # ① 注入锚点时刻: 真函数 + now_ts
    _real_recent = history.find_recent_reusable_batch
    monkeypatch.setattr(history, "find_recent_reusable_batch",
                        lambda uid, f, **k: _real_recent(uid, f, now_ts=_ANCHOR_TS))
    # ② 隔离「当日系统统一名单」这一步(v4.11.25 新增): 它用**真实今天**查库, 会被
    #    别的用例建的 uid=0 当日批次命中 → 走进新分支、reusedDate 变 None, 干扰本用例
    #    对「跨日回退」路径的断言。该优先级自身由 test_today_system_fallback.py 覆盖。
    monkeypatch.setattr(history, "find_today_system_batch", lambda **k: (None, None))

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
        _mk_batch(conn, u["uid"], "lock", _d(-3), _ts(-3), f_full)
    finally:
        conn.close()

    h = {"Authorization": "Bearer " + u["token"]}
    r = client.get("/api/stocks?action=refresh&strategy=auction&markets=hs,cyb&probLt=65&confLt=65",
                   headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("reused") is True and d.get("reusedDate") == _d(-3), d
    assert calls["n"] == 0, "回退直读不应触发全市场重算(转圈根因)"
