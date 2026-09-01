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


def test_batch_save_keeps_qiangchou():
    """2026-09-01 抢筹口径: 落库必须保存抢筹标记, 历史批次/9:30后锁定名单回看仍显示 🔥
    (修复: batch_stocks 原无 qiangchou 列, 落库丢弃 → 竞价选股 tab 永远看不到抢筹标)"""
    from app.services import history
    f = {"markets": ["sh", "sz"]}
    result = [
        {"code": "600001", "name": "测试甲", "probability": 92, "confidence": 1,
         "bidChange": 5.0, "realChange": 3.0, "entityChange": 2.0, "bidTurnover": 30.0,
         "warnType": 1, "circulationMV": 40.0, "industry": "x", "concept": "y",
         "bidAmt": 1e8, "bidRatio": 1.2, "qiangchou": 1},
        {"code": "000002", "name": "测试乙", "probability": 60, "confidence": 1,
         "bidChange": 2.0, "realChange": 1.0, "entityChange": 0.5, "bidTurnover": 10.0,
         "warnType": 0, "circulationMV": 50.0, "industry": "x", "concept": "y",
         "bidAmt": 5e7, "bidRatio": 0.8, "qiangchou": 0},
        {"code": "300003", "name": "测试丙", "probability": 70, "confidence": 1,
         "bidChange": 3.0, "realChange": 2.0, "entityChange": 1.0, "bidTurnover": 20.0,
         "warnType": 2, "circulationMV": 60.0, "industry": "x", "concept": "y",
         "bidAmt": 6e7, "bidRatio": 1.0},   # 不带 qiangchou → 落库默认为 0
    ]
    bid = history.save_batch(9999, "filter", result, f)
    assert bid, "落库失败"
    b, stocks = history.get_batch(bid, 9999)
    assert b is not None
    qc = {s["code"]: s.get("qiangchou") for s in stocks}
    assert qc["600001"] == 1, "命中抢筹必须落库为 1"
    assert qc["000002"] == 0, "未命中必须为 0"
    assert qc["300003"] == 0, "缺省字段必须默认为 0"
    # 历史条件查询也带 qiangchou
    q = history.query_history(9999, {"date_from": ["2000-01-01"], "date_to": ["2099-12-31"],
                                     "page": ["1"], "pageSize": ["50"]})
    hit = [s for s in q["rows"] if s["code"] == "600001"]
    assert hit and hit[0].get("qiangchou") == 1


def test_batch_stocks_has_qiangchou_column():
    """2026-09-01 迁移: batch_stocks 表必须含 qiangchou 列(老库 ALTER 升级)"""
    from app.db import database
    conn = database.get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(batch_stocks)").fetchall()]
    conn.close()
    assert "qiangchou" in cols


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


# ---------- score_all_stocks 拆分一致性 (2026-08-16) ----------
def test_score_all_stocks_split_consistency():
    """process_all_stocks == score_all_stocks + apply_filters (拆分不改变行为)"""
    from app.services import scorer
    raw = [
        {"f12": "600001", "f14": "测试A", "f2": 10.0, "f3": 4.0, "f6": 50000000.0, "f616": 50000000.0,
         "f8": 3.0, "f10": 1.5, "f21": 5e9, "f100": "行业", "f103": "", "f102": "省",
         "f630": 0},
        {"f12": "300001", "f14": "测试B", "f2": 20.0, "f3": -2.0, "f6": 10000000.0, "f616": 10000000.0,
         "f8": 1.0, "f10": 0.8, "f21": 2e9, "f100": "行业2", "f103": "", "f102": "省2",
         "f630": 0},
    ]
    f = {"stSuspend": True, "limitUp": True, "bidGt": 7.0, "probLt": 0.0, "confLt": 0.0,
         "floatMvFloor": 0.0, "floatMvGt": 99999.0, "priceGt": 999.0, "bidAmtFloor": 0.0,
         "markets": ["hs"]}
    # 原入口
    r1 = scorer.process_all_stocks(raw, f)
    # 拆分后等价调用
    scored = scorer.score_all_stocks(raw)
    r2 = scorer.apply_filters(scored, f)
    for it in r2:
        it.pop("_raw", None)
    assert [s["code"] for s in r1] == [s["code"] for s in r2], "拆分前后结果必须一致"
    assert len(r1) == len(r2)
    # score_all_stocks 返回全量(未过滤)
    assert len(scored) >= len(r1)
