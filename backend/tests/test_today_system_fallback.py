# -*- coding: utf-8 -*-
"""9/17 事故回归: 当日名单全部 miss 时, **当日系统统一名单优先于跨日回退**
(主人 2026-09-17 拍板「先修④」)。

事故路径(9/17 实测): 用户当日**点过** lock/filter 但拿到**空名单批次**
(当日全市场评分被压到 `scoreFloor` 之下 → `system_batch[9_25] 候选=7 入选=0`) →
`find_today_reusable_batch` 的 ①② 因 `stock_count=0` 跳过、③ 被 `if not rows` 挡住 →
直落 `find_recent_reusable_batch` → **交易日却显示昨日名单**
(日志「回退最近交易日直读 date=2026-09-16」累计 756 次)。

修复: 在「跨日回退」之前插入一步「当日系统名单」(`history.find_today_system_batch`)。

⚠️ 本文件所有日期**按今天相对推算**, 不写字面量日期 —— 既有
`test_stocks_refresh_fallback.py` 就是因为写死 `2026-09-01` 而随日历永久漂红。
⚠️ 本文件会临时创建 **user_id=0** 的系统批次(全局共享), 故 teardown 里**必须按 id 删除**,
否则会污染同 session 其它用例(例如让它们的跨日回退命中今天的系统批次)。
"""
import json
import time

import pytest

from app.services import history
from app.services.cache_store import store


def _bj_day(delta_days=0):
    """北京日期字符串(相对今天) —— 用 delta 而非字面量, 避免日期漂移。"""
    g = time.gmtime(time.time() + 8 * 3600 + delta_days * 86400)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _uid(user):
    return user["uid"]


@pytest.fixture(autouse=True)
def _clean_stocks_cache():
    store.clear_prefix("stocks_refresh:")
    yield
    store.clear_prefix("stocks_refresh:")


@pytest.fixture
def conn():
    from app.db import database
    c = database.get_conn()
    yield c
    try:
        c.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_uid0_residue():
    """清理**本用例新建的** user_id=0 批次(全局共享, 必须回收)。
    用 id 水位线精确定位, 避免误删其它测试文件已建的 uid=0 批次。"""
    from app.db import database
    c = database.get_conn()
    try:
        mark = c.execute("SELECT COALESCE(MAX(id), 0) FROM batches").fetchone()[0]
    finally:
        c.close()
    yield
    c = database.get_conn()
    try:
        ids = [r[0] for r in c.execute(
            "SELECT id FROM batches WHERE user_id=0 AND id>?", (mark,)).fetchall()]
        for bid in ids:
            c.execute("DELETE FROM batch_stocks WHERE batch_id=?", (bid,))
            c.execute("DELETE FROM batches WHERE id=?", (bid,))
        c.commit()
    finally:
        c.close()


F = {"markets": ["hs", "cyb"], "probLt": 65, "confLt": 65}


@pytest.fixture
def freeze(monkeypatch):
    """当日 9:25 定格**已落库** —— v4.11.29 起是「当日系统名单」的前置条件。

    🔴 2026-09-19 补记(本文件当时未同步 v4.11.29, 且从未上传测试机 → 缺陷双盲):
    v4.11.25 写本文件时, `find_today_system_batch` 没有定格判据, 建个 uid=0 批次即可命中;
    v4.11.29 给它加了 `ts >= _freeze_landing_ts(当日)` 之后, 旧用例的结局是:
      · **正向 3 例 → 真红**: 当日无 snapshot_bid 9_25 行 → land=None → 恒不命中;
      · **否定 2 例 → 侥幸全绿**: 也因 land=None 恒返回 None —— 断言虽过, 但**覆盖已失效**,
        它们不再能证明"跨日批次不被误认"或"空名单被跳过"。
    现由本 fixture 显式提供 land(定格落库时刻), 两类用例才真正测到各自语义。
    建"当日系统批次"时 ts **必须 >= land**(返回的 land 即该阈值)。
    """
    land = time.time() - 7200                    # 定格落库于 2 小时前
    monkeypatch.setattr(history, "_freeze_landing_ts",
                        lambda bdate: land if bdate == _bj_day(0) else None)
    return land


def _mk_batch(conn, uid, action, date, ts, f, auto_applied=0, count=3):
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


# ---------------- history.find_today_system_batch 单元 ----------------

def test_find_today_system_batch_hit(conn, freeze):
    """定格落库后建立的当日系统批次 → 命中(ts 晚于 freeze)"""
    bid = _mk_batch(conn, 0, "lock", _bj_day(0), freeze + 60, F, auto_applied=1, count=2)
    got_bid, src = history.find_today_system_batch()
    assert got_bid == bid and src == "auto"


def test_find_today_system_batch_ignores_other_days(conn, freeze):
    """只有昨日/前日的系统批次 → 不算"当日", 必须返回 None(否则跨日回退形同虚设)"""
    _mk_batch(conn, 0, "lock", _bj_day(-1), freeze - 86400, F, auto_applied=1, count=2)
    _mk_batch(conn, 0, "lock", _bj_day(-3), freeze - 3 * 86400, F, auto_applied=1, count=2)
    assert history.find_today_system_batch() == (None, None)


def test_find_today_system_batch_skips_empty(conn, freeze):
    """当日的系统批次若为空名单(stock_count=0) → 无直读价值, 跳过"""
    _mk_batch(conn, 0, "lock", _bj_day(0), freeze + 60, F, auto_applied=1, count=0)
    assert history.find_today_system_batch() == (None, None)


def test_find_today_system_batch_ignores_manual(conn, create_user_token, freeze):
    """用户当日有手动批次**不影响**本函数(它刻意不看 `rows`)—— 这正是与当日版 ③ 的区别"""
    uid = _uid(create_user_token())
    _mk_batch(conn, uid, "lock", _bj_day(0), freeze + 120, F, count=0)
    sysbid = _mk_batch(conn, 0, "lock", _bj_day(0), freeze + 60, F, auto_applied=1, count=2)
    got_bid, src = history.find_today_system_batch()
    assert got_bid == sysbid and src == "auto"


# ---------------- v4.11.29 定格判据（此前零覆盖） ----------------

def test_system_batch_before_freeze_is_rejected(conn, freeze):
    """🔴 v4.11.29 核心防线: 定格**之前**落库的系统批次必须被排除。

    这类批次的竞价字段整批取自上一交易日 9_25(当日 9_25 尚不存在 → 回退最近交易日),
    而复用等价于"拿昨日名单当今日权威名单"(9/18 主人现象, 样本 #1674)。
    """
    _mk_batch(conn, 0, "lock", _bj_day(0), freeze - 60, F, auto_applied=1, count=2)
    assert history.find_today_system_batch() == (None, None)


def test_no_freeze_means_no_today_system_batch(conn, monkeypatch):
    """当日 9_25 尚未落库(land=None) → 任何当日系统批次都不可用(不得侥幸命中)"""
    monkeypatch.setattr(history, "_freeze_landing_ts", lambda bdate: None)
    _mk_batch(conn, 0, "lock", _bj_day(0), time.time() - 60, F, auto_applied=1, count=2)
    assert history.find_today_system_batch() == (None, None)


def test_list_batches_marks_freeze_ready(conn, freeze):
    """v4.11.29: 批次列表附 `freeze_ready`, 供前端首屏排除"定格前批次"回显"""
    before = _mk_batch(conn, 0, "lock", _bj_day(0), freeze - 120, F, auto_applied=1, count=2)
    after = _mk_batch(conn, 0, "lock", _bj_day(0), freeze + 60, F, auto_applied=1, count=2)
    got = {r["id"]: r["freeze_ready"] for r in history.list_batches(999999, limit=200)
           if r["id"] in (before, after)}
    assert got.get(before) is False, "定格前批次应标 freeze_ready=False"
    assert got.get(after) is True, "定格后批次应标 freeze_ready=True"


# ---------------- 端到端: 事故场景正/反向 ----------------

def _http_setup(monkeypatch):
    from app.services import fetcher, scorer
    from app.services.picker import pipeline as pl
    monkeypatch.setattr(scorer, "bj_now", lambda: (10, 30, False))    # 9:30 后
    calls = {"n": 0}
    monkeypatch.setattr(pl, "run",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or pl.PipelineResult())
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})
    return calls


def test_api_refresh_uses_today_system_when_today_manual_is_empty(
        client, create_user_token, conn, monkeypatch, freeze):
    """🔴 9/17 事故回归(正向): 用户当日点了 lock 但落的是**空名单批次**,
    且存在跨日同参批次 → **必须给当日系统名单, 绝不回退昨日**"""
    from app.services import scorer
    calls = _http_setup(monkeypatch)
    u = create_user_token()
    f_full = scorer.validate_filters({"markets": ["hs,cyb"], "probLt": ["65"], "confLt": ["65"]})

    # 昨日 同参 lock(3 只) —— 修复前会命中它 → 用户看到"昨天的名单"
    _mk_batch(conn, u["uid"], "lock", _bj_day(-1), freeze - 86400, f_full, count=3)
    # 今日 用户手动 lock, 但**空名单**(stock_count=0) —— 事故的触发条件
    _mk_batch(conn, u["uid"], "lock", _bj_day(0), freeze + 120, f_full, count=0)
    # 今日 系统统一名单(2 只) —— 应当命中它
    sysbid = _mk_batch(conn, 0, "lock", _bj_day(0), freeze + 60, f_full,
                       auto_applied=1, count=2)

    h = {"Authorization": "Bearer " + u["token"]}
    r = client.get("/api/stocks?action=refresh&strategy=auction&markets=hs,cyb&probLt=65&confLt=65",
                   headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("reused") is True, d
    assert d.get("batch_id") == sysbid, ("必须用当日系统名单, 而不是昨日批次", d)
    assert d.get("source") == "auto", d
    assert d.get("reusedDate") is None, ("当日名单不得带历史日期提示", d)
    assert d.get("count") == 2, d
    assert calls["n"] == 0, "直读不应触发全市场重算"


def test_api_refresh_still_uses_recent_when_no_today_system(
        client, create_user_token, conn, monkeypatch, freeze):
    """反向对照: 当日**没有**系统名单时, 跨日回退必须照旧生效(别把 2026-09-05 的需求改坏)

    这里同样提供 freeze(当日定格已落库) —— 否则会因 land=None 而"侥幸通过",
    证明不了"在定格已就绪的正常场景下, 找不到当日名单时也会正确回退昨日"。
    """
    from app.services import scorer
    calls = _http_setup(monkeypatch)
    u = create_user_token()
    f_full = scorer.validate_filters({"markets": ["hs,cyb"], "probLt": ["65"], "confLt": ["65"]})
    yday = _bj_day(-1)
    ybid = _mk_batch(conn, u["uid"], "lock", yday, freeze - 86400, f_full, count=3)

    h = {"Authorization": "Bearer " + u["token"]}
    r = client.get("/api/stocks?action=refresh&strategy=auction&markets=hs,cyb&probLt=65&confLt=65",
                   headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("reused") is True, d
    assert d.get("batch_id") == ybid and d.get("reusedDate") == yday, d
    assert calls["n"] == 0, "直读不应触发全市场重算"
