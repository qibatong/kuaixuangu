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
