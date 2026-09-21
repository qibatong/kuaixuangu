# -*- coding: utf-8 -*-
"""板块轮动历史服务测试: 抓取落库 + 查询历史 + 多窗口排名 + 多数据源"""
import json
import os

import pytest

from app.services import kpl, sector_rotation


SAMPLE_KPL = [
    {"boardCode": "BK0001", "name": "医药",      "strength": 9969, "change": 2.3, "amount": 1.2e11, "mainNet": 5.0e8,  "volRatio": 1.5, "floatMv": 2.0e12},
    {"boardCode": "BK0002", "name": "算力",      "strength": 5895, "change": 1.8, "amount": 9.5e10, "mainNet": 4.2e8,  "volRatio": 1.3, "floatMv": 1.8e12},
    {"boardCode": "BK0003", "name": "并购重组",  "strength": 4070, "change": 1.2, "amount": 7.8e10, "mainNet": 2.1e8,  "volRatio": 1.1, "floatMv": 1.5e12},
    {"boardCode": "BK0004", "name": "AI应用",    "strength": 3763, "change": 0.9, "amount": 6.5e10, "mainNet": 1.8e8,  "volRatio": 1.0, "floatMv": 1.4e12},
    {"boardCode": "BK0005", "name": "芯片",      "strength": 2744, "change": 0.5, "amount": 5.0e10, "mainNet": 9.0e7,  "volRatio": 0.9, "floatMv": 1.0e12},
    {"boardCode": "BK0006", "name": "通信",      "strength": 2216, "change": 0.3, "amount": 4.2e10, "mainNet": 5.0e7,  "volRatio": 0.8, "floatMv": 9.0e11},
    {"boardCode": "BK0007", "name": "电力",      "strength": 1912, "change": 0.1, "amount": 3.5e10, "mainNet": 3.0e7,  "volRatio": 0.7, "floatMv": 8.0e11},
    {"boardCode": "BK0008", "name": "机器人概念", "strength": 1711, "change": -0.2, "amount": 3.0e10, "mainNet": 1.0e7,  "volRatio": 0.7, "floatMv": 7.5e11},
    {"boardCode": "BK0009", "name": "股权转让",  "strength": 1363, "change": -0.4, "amount": 2.5e10, "mainNet": -5.0e7, "volRatio": 0.6, "floatMv": 6.0e11},
    {"boardCode": "BK0010", "name": "地产链",    "strength": 1114, "change": -0.5, "amount": 2.0e10, "mainNet": -1.0e8, "volRatio": 0.6, "floatMv": 5.5e11},
]

SAMPLE_EM = [  # em 数据(用涨跌幅%×100 作 strength)
    {"boardCode": "BK1675", "name": "半导体", "strength": 320.0, "change": 3.20, "amount": 5.0e10, "mainNet": 0.0, "volRatio": 0.0, "floatMv": 0.0},
    {"boardCode": "BK1036", "name": "芯片",   "strength": 280.0, "change": 2.80, "amount": 4.5e10, "mainNet": 0.0, "volRatio": 0.0, "floatMv": 0.0},
]


def _raw_conn():
    import sqlite3
    return sqlite3.connect(os.environ["BID_DB_PATH"])


def test_record_today_top_saves_to_db(client, monkeypatch):
    """抓取当日板块 Top10 落库(默认 source=kpl)"""
    # mock kpl.fetch_board_rank 实时接口 + fetch_board_rank_by_date 历史接口,
    # 避免非交易时段拿不到数据 + record_today_top 默认会按今天走 by_date 路径
    monkeypatch.setattr(kpl, "fetch_board_rank", lambda: list(SAMPLE_KPL))
    monkeypatch.setattr(kpl, "fetch_board_rank_by_date", lambda date=None: list(SAMPLE_KPL))
    n = sector_rotation.record_today_top(top_n=10)
    assert n == 10
    conn = _raw_conn()
    row = conn.execute(
        "SELECT date, source, boards FROM daily_sector_top WHERE source='kpl' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    _, src, raw = row
    assert src == "kpl"
    boards = json.loads(raw)
    assert len(boards) == 10
    assert boards[0]["rank"] == 1
    assert boards[0]["name"] == "医药"
    assert boards[0]["strength"] == 9969


def test_record_today_top_handles_empty(monkeypatch):
    """抓取失败(返回空) 不报错也不入库"""
    monkeypatch.setattr(kpl, "fetch_board_rank", lambda: [])
    n = sector_rotation.record_today_top()
    assert n == 0


def test_record_today_top_source_em(client, monkeypatch):
    """record_today_top 支持 source 参数, em 数据独立落库"""
    monkeypatch.setattr(sector_rotation, "fetch_em_board_rank", lambda: list(SAMPLE_EM))
    n = sector_rotation.record_today_top(top_n=2, source="em")
    assert n == 2
    conn = _raw_conn()
    row = conn.execute(
        "SELECT date, source, boards FROM daily_sector_top WHERE source='em' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    _, src, raw = row
    assert src == "em"
    boards = json.loads(raw)
    assert boards[0]["name"] == "半导体"
    assert boards[0]["strength"] == 320.0


def test_query_rotation_filters_by_source(client, monkeypatch):
    """query_rotation 按 source 过滤: kpl 和 em 数据互不干扰"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for i, src in enumerate(("kpl", "em")):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        payload = [{"rank": 1, "name": "板块-" + src, "strength": 100 - i * 10}]
        conn.execute(
            "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, src, json.dumps(payload), 0))
    conn.commit(); conn.close()

    out_kpl = sector_rotation.query_rotation(days=5, source="kpl")
    out_em = sector_rotation.query_rotation(days=5, source="em")
    assert out_kpl["source"] == "kpl" and out_em["source"] == "em"
    assert "板块-kpl" not in [b["name"] for d in out_em["days"] for b in d["boards"]]
    assert "板块-em" not in [b["name"] for d in out_kpl["days"] for b in d["boards"]]


def test_query_window_ranking_filters_by_source(client, monkeypatch):
    """query_window_ranking 按 source 过滤"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for i in range(10):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for src, name in (("kpl", "医药"), ("em", "半导体")):
            boards = [{"rank": 1, "name": name, "strength": 100}]
            conn.execute(
                "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
                (d, src, json.dumps(boards), 0))
    conn.commit(); conn.close()

    out_kpl = sector_rotation.query_window_ranking((10,), top_k=3, source="kpl")
    out_em = sector_rotation.query_window_ranking((10,), top_k=3, source="em")
    kpl_names = {n for w in out_kpl["windows"] for n in [x["name"] for x in w["top"]]}
    em_names = {n for w in out_em["windows"] for n in [x["name"] for x in w["top"]]}
    assert "医药" in kpl_names and "半导体" not in kpl_names
    assert "半导体" in em_names and "医药" not in em_names


def test_query_rotation_returns_descending(client, monkeypatch):
    """查询历史: 按日期降序, dates 字段升序"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]

    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for d in dates:
        payload = [{"rank": i + 1, "name": f"板块{i+1}", "strength": 100 - i * 10}
                   for i in range(5)]
        conn.execute(
            "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, "kpl", json.dumps(payload), 0))
    conn.commit(); conn.close()

    out = sector_rotation.query_rotation(days=5, source="kpl")
    assert out["dates"] == sorted(out["dates"])
    returned_dates = [x["date"] for x in out["days"]]
    assert returned_dates == sorted(returned_dates, reverse=True)
    assert len(out["days"]) == 3


def test_query_window_ranking_aggregates(client, monkeypatch):
    """多窗口排名: 聚合近 N 日板块平均强度(按名加权)"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    for i in range(30):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        boards = [
            {"rank": 1, "name": "医药", "strength": 9000 + i},
            {"rank": 2, "name": "算力", "strength": 8000 + i},
            {"rank": 3, "name": "并购重组", "strength": 7000 + i},
            {"rank": 4, "name": "AI应用", "strength": 6000 + i},
            {"rank": 5, "name": "芯片", "strength": 5000 + i},
        ]
        conn.execute(
            "INSERT INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, "kpl", json.dumps(boards), 0))
    conn.commit(); conn.close()

    out = sector_rotation.query_window_ranking((10, 20, 30), top_k=3, source="kpl")
    assert len(out["windows"]) == 3
    win10 = [x for x in out["windows"] if x["window"] == 10][0]
    names10 = [x["name"] for x in win10["top"]]
    assert "医药" in names10 and "算力" in names10


def test_api_sector_rotation(client, first_user):
    """API /api/kpl/sector-rotation 默认 source=kpl, 可指定 source=em"""
    token, _, _ = first_user
    conn = _raw_conn()
    conn.execute("DELETE FROM daily_sector_top")
    payload_kpl = [{"rank": 1, "name": "医药", "strength": 9969}]
    payload_em = [{"rank": 1, "name": "半导体", "strength": 320}]
    conn.execute("INSERT INTO daily_sector_top (date, source, boards, ts) VALUES ('2026-08-14', 'kpl', ?, 0)",
                 (json.dumps(payload_kpl),))
    conn.execute("INSERT INTO daily_sector_top (date, source, boards, ts) VALUES ('2026-08-14', 'em', ?, 0)",
                 (json.dumps(payload_em),))
    conn.commit(); conn.close()

    # 默认 kpl
    r = client.get("/api/kpl/sector-rotation?days=10", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200 and r.json().get("ok")
    d = r.json()
    assert d["source"] == "kpl"
    assert len(d["dates"]) >= 1
    assert d["dates"][0] == "2026-08-14"
    # 返回的 boards 只含 kpl(医药), 不含 em(半导体)
    names = [b["name"] for day in d["rotation"]["days"] for b in day["boards"]]
    assert "医药" in names and "半导体" not in names

    # 指定 em
    r2 = client.get("/api/kpl/sector-rotation?source=em&days=10", headers={"Authorization": "Bearer " + token})
    d2 = r2.json()
    assert d2["source"] == "em"
    names2 = [b["name"] for day in d2["rotation"]["days"] for b in day["boards"]]
    assert "半导体" in names2 and "医药" not in names2


# ============ 东财概念题材异动榜(2026-09-21 新增) ============

EM_CONCEPT_DIFF = [
    {"f12": "BK1063", "f14": "重组蛋白", "f3": 4.69, "f6": 1.16e10, "f62": 6.9e8,
     "f104": 33, "f105": 1, "f128": "近岸蛋白", "f136": 20.0, "f140": "688137", "f222": -0.88},
    {"f12": "BK0816", "f14": "昨日连板", "f3": 5.84, "f6": 6.3e9, "f62": -2.6e7,
     "f104": 6, "f105": 3, "f128": "南华生物", "f136": 9.99, "f140": "000504", "f222": -1.22},
    {"f12": "BK0899", "f14": "CRO", "f3": 4.53, "f6": 2.86e10, "f62": 1.1e9,
     "f104": 47, "f105": 2, "f128": "百花医药", "f136": 10.04, "f140": "600721", "f222": -0.34},
]

EM_MEMBER_DIFF = [
    {"f2": 24.06, "f3": 10.01, "f6": 5.0e8, "f8": 0.72, "f11": 0.0, "f12": "001216",
     "f13": 0, "f14": "华瓷股份", "f20": 7.02e9, "f21": 5.94e9, "f62": 1.17e7},
    {"f2": 9.12, "f3": 10.01, "f6": 4.0e8, "f8": 9.84, "f11": 0.0, "f12": "600630",
     "f13": 1, "f14": "龙头股份", "f20": 3.87e9, "f21": 3.87e9, "f62": -1.30e7},
    # 北交所票(920 段): 应被 is_bse 过滤掉
    {"f2": 15.67, "f3": 5.38, "f6": 1.0e8, "f8": 2.97, "f11": 0.0, "f12": "920047",
     "f13": 0, "f14": "诺思兰德", "f20": 2.8e9, "f21": 2.8e9, "f62": -5.0e6},
]


def test_fetch_em_concept_rank_parses_and_filters(monkeypatch):
    """题材异动榜: 解析字段 + 剔除统计型板块(昨日连板) + 按涨幅降序"""
    monkeypatch.setattr(sector_rotation, "_em_clist",
                        lambda fs, fields, **kw: list(EM_CONCEPT_DIFF))
    out = sector_rotation.fetch_em_concept_rank()
    # 昨日连板 被过滤掉
    names = [b["name"] for b in out]
    assert "昨日连板" not in names
    assert "重组蛋白" in names and "CRO" in names
    assert len(out) == 2
    # 按涨幅降序: 重组蛋白 4.69 > CRO 4.53
    assert out[0]["name"] == "重组蛋白"
    assert out[0]["change"] == 4.69
    assert out[0]["speed"] == -0.88
    assert out[0]["mainNet"] == 6.9e8
    assert out[0]["upCount"] == 33 and out[0]["downCount"] == 1
    assert out[0]["leaderName"] == "近岸蛋白" and out[0]["leaderCode"] == "688137"
    assert out[0]["leaderChange"] == 20.0


def test_fetch_em_board_members_parses(monkeypatch):
    """题材异动榜成分股: 解析字段 + 按涨幅降序 + 剔除北交所(920 段)"""
    monkeypatch.setattr(sector_rotation, "_em_clist",
                        lambda fs, fields, **kw: list(EM_MEMBER_DIFF))
    out = sector_rotation.fetch_em_board_members("BK0816")
    # 920047 北交所被过滤, 只剩 2 只
    assert len(out) == 2
    codes = [s["code"] for s in out]
    assert "920047" not in codes
    assert out[0]["code"] == "001216" and out[0]["name"] == "华瓷股份"
    assert out[0]["price"] == 24.06 and out[0]["change"] == 10.01
    assert out[0]["turnover"] == 0.72 and out[0]["speed"] == 0.0
    assert out[0]["mainNet"] == 1.17e7
    assert out[0]["totalMv"] == 7.02e9 and out[0]["floatMv"] == 5.94e9


def test_fetch_em_board_members_empty_code(monkeypatch):
    """题材异动榜成分股: 空 code 直接返回空, 不发起请求"""
    out = sector_rotation.fetch_em_board_members("")
    assert out == []


def test_api_em_concept_rank(client, first_user, monkeypatch):
    """API /api/kpl/em-concept-rank: 猫爪精选板块主源优先, 返回 source=meoz"""
    token, _, _ = first_user
    monkeypatch.setattr(sector_rotation, "fetch_meoz_jx_rank",
                        lambda: [{"boardCode": "801165k", "name": "机器人", "change": 3.89, "speed": 1.2, "mainNet": 7.6e8, "strength": 101.0}])
    r = client.get("/api/kpl/em-concept-rank", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200 and r.json().get("ok")
    d = r.json()
    assert d["source"] == "meoz"
    assert d["count"] == 1 and d["list"][0]["name"] == "机器人"


def test_api_em_concept_rank_fallback_em(client, first_user, monkeypatch):
    """API /api/kpl/em-concept-rank: 猫爪精选板块为空自动降级东财, source=em"""
    token, _, _ = first_user
    monkeypatch.setattr(sector_rotation, "fetch_meoz_jx_rank", lambda: [])
    monkeypatch.setattr(sector_rotation, "fetch_em_concept_rank",
                        lambda: [{"boardCode": "BK0899", "name": "CRO", "change": 4.53, "speed": -0.34}])
    r = client.get("/api/kpl/em-concept-rank", headers={"Authorization": "Bearer " + token})
    d = r.json()
    assert d["source"] == "em" and d["list"][0]["name"] == "CRO"


def test_api_em_board_members_requires_code(client, first_user):
    """API /api/kpl/em-board-members 缺 code 返回 400"""
    token, _, _ = first_user
    r = client.get("/api/kpl/em-board-members", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 400


# ============ 猫爪板块指数(2026-09-21 主人: 东财的不行, 换猫爪板块指数) ============

def _meoz_matrix(cols, items):
    """构造猫爪矩阵响应 {"code":200,"data":{"fields":cols,"items":items}}"""
    return {"code": 200, "message": "success", "data": {"fields": cols, "items": items}}


def test_fetch_meoz_board_rank_parses(monkeypatch):
    """猫爪板块榜: theme_daily 矩阵解析 + 按涨幅降序"""
    rows = [
        ["20260921", "880729", "CXO概念", "gn", 1048.36, 4.4537, 3.67e10],
        ["20260921", "880920", "免疫治疗", "gn", 1182.08, 4.4416, 2.25e10],
        ["20260921", "880100", "", "gn", 1000.0, 1.0, 1e9],      # 无名板块应跳过
    ]
    monkeypatch.setattr(sector_rotation.meoz, "call_cached",
                        lambda apiname, params=None, fields=None, ttl=6, cache_key=None:
                        _meoz_matrix(["tradedate", "symbol", "name", "type", "close", "pct_chg", "amount"], rows))
    out = sector_rotation.fetch_meoz_board_rank("gn")
    assert len(out) == 2
    assert out[0]["boardCode"] == "880729" and out[0]["name"] == "CXO概念"
    assert out[0]["change"] == 4.45 and out[0]["amount"] == 3.67e10
    assert out[0]["close"] == 1048.36
    # 缺失字段置 0/空(前端列结构兼容)
    assert out[0]["speed"] == 0.0 and out[0]["mainNet"] == 0.0 and out[0]["leaderName"] == ""


def test_fetch_meoz_board_rank_error(monkeypatch):
    """猫爪板块榜: 调用异常返回空(由 API 层降级东财)"""
    def _boom(*a, **kw):
        raise RuntimeError("meoz down")
    monkeypatch.setattr(sector_rotation.meoz, "call_cached", _boom)
    assert sector_rotation.fetch_meoz_board_rank("gn") == []


def test_fetch_meoz_board_members_parses(monkeypatch):
    """猫爪成分股: theme_members 成员池 + screening 行情 + 北交所过滤 + 涨幅降序"""
    members = _meoz_matrix(["theme_symbol", "symbols"], [["880904", ["600519", "920047", "000002"]]])
    screening = _meoz_matrix(
        ["symbol", "name", "close", "pct_chg", "amount", "turnover_rate_f", "free_float_mv"],
        [["600519", "贵州茅台", 1500.0, 2.5, 3e9, 0.8, 1.8e12],
         ["000002", "万科A", 3.57, 7.53, 1.4e9, 6.52, 2.3e10]])
    calls = []

    def _fake(apiname, params=None, fields=None, ttl=6, cache_key=None):
        calls.append(apiname)
        return members if apiname == "theme_members" else screening

    monkeypatch.setattr(sector_rotation.meoz, "call_cached", _fake)
    out = sector_rotation.fetch_meoz_board_members("880904")
    assert "theme_members" in calls and "screening" in calls
    # 920047 北交所被过滤, 只剩 2 只; 按涨幅降序: 万科A 7.53 > 茅台 2.5
    assert len(out) == 2
    assert out[0]["code"] == "000002" and out[0]["name"] == "万科A"
    assert out[0]["change"] == 7.53 and out[0]["turnover"] == 6.52
    assert out[0]["floatMv"] == 2.3e10 and out[0]["price"] == 3.57
    assert out[1]["code"] == "600519"


def test_fetch_meoz_board_members_empty(monkeypatch):
    """猫爪成分股: 空 code / 未知板块返回空"""
    assert sector_rotation.fetch_meoz_board_members("") == []
    empty = _meoz_matrix(["theme_symbol", "symbols"], [])
    monkeypatch.setattr(sector_rotation.meoz, "call_cached",
                        lambda apiname, params=None, fields=None, ttl=6, cache_key=None: empty)
    assert sector_rotation.fetch_meoz_board_members("999999") == []


# ============ 猫爪精选板块 jx(2026-09-21 主人: 先换左栏为精选板块) ============

JX_RANK_COLS = ["tradedate", "theme_symbol", "theme_name", "strength", "pct_chg", "chg_speed",
                "amount", "main_net_amount", "turnover_rate", "volume_ratio", "circ_mv", "prev_pct_chg"]


def test_fetch_meoz_jx_rank_parses(monkeypatch):
    """猫爪精选板块榜: themedaily_jx 矩阵解析 + 字段全有值 + 按涨速降序"""
    rows = [
        ["20260921", "801653k", "霍乱概念", 343, 4.744, 0.04, 3.10e9, 328067, 4.37, 1.444, 7.08e10, 4.23],
        ["20260921", "801418k", "民营医院", 1300, 3.381, 0.05, 2.03e10, 1.99e8, 2.86, 1.824, 7.13e11, 2.92],
        ["20260921", "801391k", "美容护理", 253, 3.34, 0.314, 6.40e9, -4.42e7, 1.98, 2.087, 3.23e11, 3.12],
    ]
    monkeypatch.setattr(sector_rotation, "fetch_meoz_auc_kp", lambda: {})
    monkeypatch.setattr(sector_rotation.meoz, "call_cached",
                        lambda apiname, params=None, fields=None, ttl=6, cache_key=None:
                        _meoz_matrix(JX_RANK_COLS, rows))
    out = sector_rotation.fetch_meoz_jx_rank()
    assert len(out) == 3
    # 按涨速降序: 美容护理 0.314 > 民营医院 0.05 > 霍乱概念 0.04
    assert out[0]["boardCode"] == "801391k" and out[0]["name"] == "美容护理"
    assert out[0]["change"] == 3.34 and out[0]["speed"] == 0.31
    assert out[0]["mainNet"] == -4.42e7 and out[0]["strength"] == 253.0
    assert out[0]["volRatio"] == 2.09 and out[0]["floatMv"] == 3.23e11
    assert out[0]["prevChg"] == 3.12
    # 无竞价异动时 auc 字段置空/0
    assert out[0]["aucGroup"] == "" and out[0]["aucNet"] == 0.0


AUC_KP_COLS = ["source_day", "group", "group_rank", "theme_symbol", "theme_name",
               "bid_volume_burst", "abnormal_amount", "main_net_amount"]


def test_fetch_meoz_auc_kp_parses(monkeypatch):
    """猫爪板块竞价异动: theme_auc_kp 矩阵解析为 {theme_symbol: {...}}"""
    rows = [
        ["20260921", "List1", 1, "801046", "医疗器械", 5.5, 75458093, 8781685],
        ["20260921", "List2", 1, "801001", "芯片", 4.0, 30000000, -1000000],
        ["20260921", "List1", 2, "", None, 6.0, 1e8, 2e6],       # 无代码应跳过
    ]
    monkeypatch.setattr(sector_rotation.meoz, "call_cached",
                        lambda apiname, params=None, fields=None, ttl=6, cache_key=None:
                        _meoz_matrix(AUC_KP_COLS, rows))
    out = sector_rotation.fetch_meoz_auc_kp()
    assert set(out.keys()) == {"801046", "801001"}
    assert out["801046"]["group"] == "List1" and out["801046"]["rank"] == 1
    assert out["801046"]["burst"] == 5.5 and out["801046"]["abnormal"] == 75458093
    assert out["801046"]["net"] == 8781685 and out["801046"]["name"] == "医疗器械"
    assert out["801001"]["group"] == "List2" and out["801001"]["net"] == -1000000


def test_fetch_meoz_auc_kp_error(monkeypatch):
    """猫爪板块竞价异动: 调用异常返回空 dict(不阻断精选板块榜)"""
    def _boom(*a, **kw):
        raise RuntimeError("meoz down")
    monkeypatch.setattr(sector_rotation.meoz, "call_cached", _boom)
    assert sector_rotation.fetch_meoz_auc_kp() == {}


def test_fetch_meoz_jx_rank_auc_merge(monkeypatch):
    """精选板块榜 merge 竞价异动: 竞价代码 801xxx + 'k' 命中精选 801xxxk"""
    rows = [
        ["20260921", "801391k", "美容护理", 253, 3.34, 0.314, 6.40e9, -4.42e7, 1.98, 2.087, 3.23e11, 3.12],
        ["20260921", "801653k", "霍乱概念", 343, 4.744, 0.04, 3.10e9, 328067, 4.37, 1.444, 7.08e10, 4.23],
    ]
    monkeypatch.setattr(sector_rotation.meoz, "call_cached",
                        lambda apiname, params=None, fields=None, ttl=6, cache_key=None:
                        _meoz_matrix(JX_RANK_COLS, rows))
    # 只有 801391(美容护理) 有竞价异动; 801653(霍乱概念) 无
    monkeypatch.setattr(sector_rotation, "fetch_meoz_auc_kp",
                        lambda: {"801391": {"group": "List1", "rank": 1, "burst": 6.0,
                                            "abnormal": 1.2e8, "net": 8.5e6, "name": "美容护理"}})
    out = sector_rotation.fetch_meoz_jx_rank()
    by_code = {b["boardCode"]: b for b in out}
    # 命中: 美容护理 801391k 注入竞价字段
    assert by_code["801391k"]["aucGroup"] == "List1"
    assert by_code["801391k"]["aucRank"] == 1
    assert by_code["801391k"]["aucBurst"] == 6.0
    assert by_code["801391k"]["aucAbnormal"] == 1.2e8
    assert by_code["801391k"]["aucNet"] == 8.5e6
    # 未命中: 霍乱概念 801653k 竞价字段置空/0
    assert by_code["801653k"]["aucGroup"] == "" and by_code["801653k"]["aucNet"] == 0.0


def test_fetch_meoz_jx_members_parses(monkeypatch):
    """猫爪精选板块成分股: thememembers_jx 股票池 + screening 行情 + 北交所过滤"""
    members = _meoz_matrix(["theme_symbol", "symbols"], [["801001k", ["600519", "920047", "000002"]]])
    screening = _meoz_matrix(
        ["symbol", "name", "close", "pct_chg", "amount", "turnover_rate_f", "free_float_mv"],
        [["600519", "贵州茅台", 1500.0, 2.5, 3e9, 0.8, 1.8e12],
         ["000002", "万科A", 3.57, 7.53, 1.4e9, 6.52, 2.3e10]])
    calls = []

    def _fake(apiname, params=None, fields=None, ttl=6, cache_key=None):
        calls.append(apiname)
        return members if apiname == "thememembers_jx" else screening

    monkeypatch.setattr(sector_rotation.meoz, "call_cached", _fake)
    out = sector_rotation.fetch_meoz_jx_members("801001k")
    assert "thememembers_jx" in calls and "screening" in calls
    # 920047 北交所被过滤, 只剩 2 只; 按涨幅降序: 万科A 7.53 > 茅台 2.5
    assert len(out) == 2
    assert out[0]["code"] == "000002" and out[0]["name"] == "万科A"
    assert out[0]["change"] == 7.53 and out[0]["turnover"] == 6.52
    assert out[0]["floatMv"] == 2.3e10


def test_fetch_meoz_jx_members_empty(monkeypatch):
    """猫爪精选板块成分股: 空 code / 未知板块返回空"""
    assert sector_rotation.fetch_meoz_jx_members("") == []
    empty = _meoz_matrix(["theme_symbol", "symbols"], [])
    monkeypatch.setattr(sector_rotation.meoz, "call_cached",
                        lambda apiname, params=None, fields=None, ttl=6, cache_key=None: empty)
    assert sector_rotation.fetch_meoz_jx_members("999999k") == []


def test_api_em_board_members_jx(client, first_user, monkeypatch):
    """API /api/kpl/em-board-members: 801xxxk 代码走猫爪精选板块股票池, source=meoz"""
    token, _, _ = first_user
    monkeypatch.setattr(sector_rotation, "fetch_meoz_jx_members",
                        lambda code: [{"code": "000002", "name": "万科A", "change": 7.53}])
    r = client.get("/api/kpl/em-board-members?code=801001k",
                   headers={"Authorization": "Bearer " + token})
    d = r.json()
    assert d["source"] == "meoz" and d["list"][0]["name"] == "万科A"