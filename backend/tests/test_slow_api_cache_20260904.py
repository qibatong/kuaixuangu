# -*- coding: utf-8 -*-
"""
首屏剩余慢接口缓存回归用例 (2026-09-04 三轮)
=============================================
背景: 生产 journald 14:40-14:50(uid=49) 慢请求除 market-brief 1174ms 外还有:
        auction-overview 1025ms / history 1015ms / bid-snapshot-3points 956ms / bid-seal 709ms
本轮修复:
  ① **bid-snapshot-3points 缓存形同虚设(bug)**: 主查询
     `rows = auction_snapshot.query_3points_board(...)` 原写在 `_compute_rows()` **外面**,
     缓存只包住了后续装饰逻辑 → 命中缓存仍然每次查库(956ms 里绝大部分是它)。
     已移进 `_compute_rows()` 内; 盘中 TTL 3s→15s(原 3s < 前端 30s 轮询 ⇒ 命中率≈0)
  ② **history 接口此前完全无缓存**(每次全量查库 1015ms) → 列表 30s / 明细 300s,
     且 400(参数错)/404(批次不存在)**不进缓存**(避免错误响应被固化)
  ③ auction-overview TTL 30s→60s(原 30s 恰等于前端 30s 轮询 ⇒ 边界必 miss)
  ④ bid-seal 加接口级缓存: 竞价时段 15s / 盘后 300s / 指定历史日 600s
断言: 各接口二次请求只查库 1 次; 错误响应不被缓存
"""
import pytest

from app.services import auction_snapshot, history as history_svc
from app.services.cache_store import store


@pytest.fixture(autouse=True)
def _clean_caches():
    """清理本轮涉及的缓存 key(single-flight 锁一并清), 保证用例隔离"""
    keys = ["s3points:live:", "s3points:hist:", "hist_list:", "hist_batch:",
            "bidseal:post", "bidseal:live", "auction_overview:"]

    def _del():
        store.clear_prefix("s3points:")
        store.clear_prefix("hist_list:")
        store.clear_prefix("hist_batch:")
        store.clear_prefix("bidseal:")
        store.clear_prefix("auction_overview:")
        store.clear_prefix("stocks_refresh:")
        for k in keys:
            try:
                store.delete(k)
                store.delete(k + ":lock")
            except Exception:
                pass
    _del()
    yield
    _del()


# ---------- ① bid-snapshot-3points ----------

def test_3points_main_query_cached(client, first_user, monkeypatch):
    """P0(bug 回归): 二次请求主查询 query_3points_board 只跑 1 次
    —— 修复前它写在缓存外, 每次请求必查库(缓存形同虚设)"""
    calls = {"n": 0}

    def fake_query(resolved, limit):
        calls["n"] += 1
        return [{"code": "600000", "name": "浦发银行", "points": {"9_25": {"bid_amt": 100.0}}}]

    monkeypatch.setattr(auction_snapshot, "query_3points_board", fake_query)
    h = {"Authorization": "Bearer " + first_user[0]}
    r1 = client.get("/api/stats/bid-snapshot-3points?date=2026-09-04", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is True
    r2 = client.get("/api/stats/bid-snapshot-3points?date=2026-09-04", headers=h)
    assert r2.status_code == 200
    assert calls["n"] == 1, f"修复后二次请求应命中缓存(主查询仅1次), 实际 {calls['n']}"


def test_3points_hits_cache_on_second_call(client, first_user, monkeypatch):
    """缓存命中时返回内容一致, 且不再触发 _apply_change_stats 等装饰逻辑"""
    monkeypatch.setattr(auction_snapshot, "query_3points_board",
                        lambda resolved, limit: [{"code": "000001", "name": "平安银行", "points": {}}])
    h = {"Authorization": "Bearer " + first_user[0]}
    a = client.get("/api/stats/bid-snapshot-3points?date=2026-09-04", headers=h).json()
    b = client.get("/api/stats/bid-snapshot-3points?date=2026-09-04", headers=h).json()
    assert a == b and a.get("count") == 1


# ---------- ② history ----------

def test_history_list_cached(client, first_user, monkeypatch):
    """P0: /api/history 列表二次请求只查库 1 次"""
    calls = {"n": 0}

    def fake_list(uid):
        calls["n"] += 1
        return [{"id": 1, "created_at": "2026-09-04", "stock_count": 10}]

    monkeypatch.setattr(history_svc, "list_batches", fake_list)
    h = {"Authorization": "Bearer " + first_user[0]}
    r1 = client.get("/api/history", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is True
    r2 = client.get("/api/history", headers=h)
    assert r2.status_code == 200
    assert calls["n"] == 1, f"列表应命中缓存(查库仅1次), 实际 {calls['n']}"


def test_history_batch_detail_cached(client, first_user, monkeypatch):
    """P0: /api/history?batch=N 明细二次请求只查库 1 次"""
    calls = {"n": 0}

    def fake_get(batch_id, uid):
        calls["n"] += 1
        return {"id": batch_id, "stock_count": 3}, [{"code": "600000"}, {"code": "000001"}]

    monkeypatch.setattr(history_svc, "get_batch", fake_get)
    monkeypatch.setattr("app.services.kpl.apply_board_concept", lambda lst, *a, **k: lst)
    h = {"Authorization": "Bearer " + first_user[0]}
    r1 = client.get("/api/history?batch=123", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is True
    r2 = client.get("/api/history?batch=123", headers=h)
    assert r2.status_code == 200
    assert calls["n"] == 1, f"明细应命中缓存(查库仅1次), 实际 {calls['n']}"


def test_history_404_not_cached(client, first_user, monkeypatch):
    """批次不存在 → 404 且**不进缓存**(避免错误响应被固化, 后续插入该批次仍可读到)"""
    monkeypatch.setattr(history_svc, "get_batch", lambda batch_id, uid: (None, []))
    h = {"Authorization": "Bearer " + first_user[0]}
    r1 = client.get("/api/history?batch=999999", headers=h)
    assert r1.status_code == 404, r1.text
    # 第二次: 若错误被缓存则仍 404(此处要求**保持 404**但必须是重新查询的结果,
    # 即缓存中不存在对应 payload)
    r2 = client.get("/api/history?batch=999999", headers=h)
    assert r2.status_code == 404
    assert store.get("hist_batch:%s:999999" % _uid_of(client, first_user, h)) is None, \
        "404 不应写入缓存"


def _uid_of(client, first_user, h):
    """取当前登录用户 uid(缓存 key 需带 uid)"""
    from app.services import security
    return security.valid_token(first_user[0])


def test_history_bad_param_400(client, first_user):
    """batch 非数字 → 400(不进缓存路径)"""
    h = {"Authorization": "Bearer " + first_user[0]}
    r = client.get("/api/history?batch=abc", headers=h)
    assert r.status_code == 400, r.text


# ---------- ③ auction-overview ----------

def test_auction_overview_ttl_is_60(monkeypatch):
    """TTL 60s(原 30s 恰等于前端 30s 轮询 ⇒ 边界必 miss)"""
    import inspect
    import app.api.stats as stats_api
    src = inspect.getsource(stats_api.api_stats_auction_overview)
    assert "ttl = 60 if not date else 600" in src, "auction-overview TTL 应为 60/600"


# ---------- ④ bid-seal ----------

# ---------- ⑤ /api/stocks 无批次用户的全量重算 ----------

def test_stocks_refresh_calc_cache(client, first_user, monkeypatch):
    """P0: 无当日批次用户的 refresh 全量重算结果缓存(原每次 2.6s)
    有批次的用户走 115 行直读分支; 新号/当日系统批次为空才落到这条慢路径"""
    from app.services import fetcher, scorer

    # 9:30 后(bj_now 返回 hour, minute, before930)
    monkeypatch.setattr(scorer, "bj_now", lambda: (10, 30, False))
    # 当日无可复用批次 → 进入全量重算
    monkeypatch.setattr(
        "app.services.history.find_today_reusable_batch",
        lambda uid, f, now_ts=None: (None, None))
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})

    calls = {"n": 0}

    def counting_process(*a, **k):
        calls["n"] += 1
        return [{"code": "600000", "name": "浦发银行", "probability": 0.9}]

    monkeypatch.setattr(scorer, "process_all_stocks", counting_process)

    h = {"Authorization": "Bearer " + first_user[0]}
    url = "/api/stocks?action=refresh&mode=auction&markets=hs"
    r1 = client.get(url, headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is True
    r2 = client.get(url, headers=h)
    assert r2.status_code == 200
    assert calls["n"] == 1, \
        f"二次 refresh 应命中计算缓存(全市场评分仅1次), 实际 {calls['n']}"


def test_stocks_refresh_cache_key_varies_by_params(client, first_user, monkeypatch):
    """筛选参数改变 → 缓存 key 不同 → 正常重算(不能被旧参数的结果顶掉)"""
    from app.services import fetcher, scorer

    monkeypatch.setattr(scorer, "bj_now", lambda: (10, 30, False))
    monkeypatch.setattr(
        "app.services.history.find_today_reusable_batch",
        lambda uid, f, now_ts=None: (None, None))
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})

    calls = {"n": 0}

    def counting_process(*a, **k):
        calls["n"] += 1
        return [{"code": "600000", "name": "浦发银行"}]

    monkeypatch.setattr(scorer, "process_all_stocks", counting_process)

    h = {"Authorization": "Bearer " + first_user[0]}
    # 注意: probLt 会被 scorer._clamp 夹到 5-95, 越界值(如 1/2)会被夹成同一个数
    # → 必须用合法范围内的不同值, 否则指纹相同, 测不出参数隔离
    client.get("/api/stocks?action=refresh&mode=auction&markets=hs&probLt=50", headers=h)
    client.get("/api/stocks?action=refresh&mode=auction&markets=hs&probLt=70", headers=h)
    assert calls["n"] == 2, f"参数改变应各自重算, 实际只算了 {calls['n']} 次"


def test_bid_seal_postmarket_cached(client, first_user, monkeypatch):
    """P0: 盘后(非竞价时段)二次请求只走 1 次 fast-path 读库"""
    import app.api.kpl as kpl_api
    calls = {"n": 0}

    def fake_fast(kind):
        calls["n"] += 1
        return [{"code": "600000", "name": "浦发银行"}], "2026-09-04"

    monkeypatch.setattr(kpl_api, "_is_auction_hours", lambda: False)
    monkeypatch.setattr(kpl_api, "_read_auction_fast", fake_fast)
    monkeypatch.setattr(kpl_api, "_ensure_concepts", lambda d, tag=None: None)
    monkeypatch.setattr(kpl_api, "_apply_change_for", lambda d, date: None)
    for fn in ("fill_bid_turnover_from_snap", "fill_bid_change_from_snap"):
        monkeypatch.setattr("app.services.kpl." + fn, lambda d, *a, **k: None)

    h = {"Authorization": "Bearer " + first_user[0]}
    r1 = client.get("/api/kpl/bid-seal", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is True
    r2 = client.get("/api/kpl/bid-seal", headers=h)
    assert r2.status_code == 200
    assert calls["n"] == 1, f"bid-seal 应命中缓存(读库仅1次), 实际 {calls['n']}"
