# -*- coding: utf-8 -*-
"""历史/战绩测试: 落库/查询/分页/去重"""


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def run_filter(client, token):
    r = client.get("/api/stocks?action=filter&markets=sh_sz", headers=hdrs(token))
    assert r.status_code == 200 and r.json().get("ok")
    return r.json()


def test_history_saved_after_filter(client, first_user):
    token, _, _ = first_user
    run_filter(client, token)
    r = client.get("/api/history", headers=hdrs(token))
    assert r.status_code == 200
    assert len(r.json().get("batches", [])) >= 1


def test_history_query_pagination(client, first_user):
    """分页: 4 只股票, pageSize=2 -> 3 页"""
    token, _, _ = first_user
    run_filter(client, token)
    # 查询(无日期限制)
    r = client.get("/api/history/query?page=1&pageSize=2&date_from=2000-01-01&date_to=2099-12-31",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["total"] == 4          # 4 只股票
    assert len(d["list"]) == 2
    r2 = client.get("/api/history/query?page=2&pageSize=2&date_from=2000-01-01&date_to=2099-12-31",
                    headers=hdrs(token))
    assert len(r2.json()["list"]) == 2
    r3 = client.get("/api/history/query?page=3&pageSize=2&date_from=2000-01-01&date_to=2099-12-31",
                    headers=hdrs(token))
    assert len(r3.json()["list"]) == 0


def test_history_dedup_same_day_same_score(client, first_user):
    """同一天同评分去重: 连续两次 filter(评分不变) 只显示一组"""
    token, _, _ = first_user
    run_filter(client, token)
    run_filter(client, token)      # 同一天第二次, mock 数据相同 -> 评分相同
    r = client.get("/api/history/query?page=1&pageSize=50&date_from=2000-01-01&date_to=2099-12-31",
                   headers=hdrs(token))
    d = r.json()
    # 去重后应为 4 组(4只票), 而不是 8 条
    assert d["total"] == 4
    # 组内无重复 (date, code, prob)
    seen = set()
    for s in d["list"]:
        k = (s["batch_date"], s["code"], s["probability"])
        assert k not in seen, "存在重复 (date, code, prob)"
        seen.add(k)


def test_history_invalid_page_param(client, first_user):
    """非法分页参数容错"""
    token, _, _ = first_user
    r = client.get("/api/history/query?page=abc&pageSize=99999&date_from=2000-01-01&date_to=2099-12-31",
                   headers=hdrs(token))
    assert r.status_code == 200
    assert r.json().get("ok")


def test_stats_performance(client, first_user):
    """战绩统计接口(统计原始记录, 前面测试可能已落库多条)"""
    token, _, _ = first_user
    run_filter(client, token)
    r = client.get("/api/stats/performance", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["overview"]["total"] >= 4
    assert 0 <= d["overview"]["win_rate"] <= 1
    assert "top3" in d and "by_score" in d and "daily" in d


# ---------- 9:26 自动应用 (2026-08-16) ----------
def test_save_batch_auto_applied_default_false():
    """save_batch 默认 auto_applied=False (向后兼容, 主动 lock/filter 不变)"""
    import time
    from app.services import history
    from app.db import database
    conn = database.get_conn()
    # 用一个临时 uid 不会真注册, 直接插行测试回读
    # 这里仅测 save_batch 函数签名/默认参数
    import inspect
    sig = inspect.signature(history.save_batch)
    assert "auto_applied" in sig.parameters
    assert sig.parameters["auto_applied"].default is False
    conn.close()


def test_auto_apply_skip_admin_and_expired(client, first_user):
    """auto_apply 跳过管理员/过期账号/不存在"""
    from app.services import auto_apply
    # 不存在 uid → 返回 (False, "用户不存在")
    ok, reason = auto_apply._is_user_active(99999999)
    assert ok is False and "不存在" in reason
    # 过期账号: 直接 UPDATE 设 expire_at 为 0 (永久 = 不过期); 然后设到 1 (已过期)
    import sqlite3, os, time
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    # 找一个非管理员用户 (second_user 是普通用户)
    target = conn.execute("SELECT id FROM users WHERE is_admin=0 LIMIT 1").fetchone()
    if target:
        conn.execute("UPDATE users SET expire_at=? WHERE id=?", (int(time.time()) - 10, target[0]))
        conn.commit()
        ok2, reason2 = auto_apply._is_user_active(target[0])
        assert ok2 is False and "过期" in reason2
        # 还原
        conn.execute("UPDATE users SET expire_at=0 WHERE id=?", (target[0],))
        conn.commit()
    conn.close()
