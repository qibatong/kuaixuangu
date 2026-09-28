# -*- coding: utf-8 -*-
"""二期测试: 9:20 快照存取 + 竞价排查日志

(2026-09-11: 原"涨幅加速度 accel"4 条用例直测已退役的 scorer.process_all_stocks,
 accel 语义已迁到新链路测试 test_picker_pipeline.py::test_accel_*。)
"""
import logging

import pytest

from app.services import auction_snapshot, scorer, stats

# 与 conftest MOCK_RAW 兼容的行情样本
RAW = {"f2": 18.50, "f3": 3.20, "f4": 3.10, "f5": 150000.0, "f6": 2800.0,
       "f8": 5.50, "f10": 1.80, "f12": "600001", "f14": "测试甲",
       "f17": 18.90, "f18": 17.90, "f20": 5.0e10, "f21": 4.0e9,
       "f100": "软件服务", "f102": "广东", "f103": "AI概念",
       "f615": 4.0, "f616": 5.0e7, "f617": 300.0, "f618": 400.0, "f630": 3}


# ---------- 快照存取 ----------
def test_snapshot_save_load(client, monkeypatch):
    """snapshot_at 抓取全市场并落库, load 可读回(幂等覆盖)"""
    calls = {"n": 0}

    def fake_fetch(fs):
        calls["n"] += 1
        a = dict(RAW)
        a["f12"] = "600001"
        b = dict(RAW)
        b.update({"f12": "000002", "f14": "测试乙", "f615": 1.2})
        return [a, b]

    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", fake_fetch)
    n = auction_snapshot.snapshot_at("9_20", force=True)
    assert n == 2 and calls["n"] == 3   # hs/cyb/kcb 三分区
    snap = auction_snapshot.load_snapshot()
    assert snap["600001"]["bid_change"] == 4.0
    assert snap["000002"]["bid_change"] == 1.2
    # 幂等: 再次抓取覆盖同日期同时点, 行数不变
    auction_snapshot.snapshot_at("9_20", force=True)
    assert len(auction_snapshot.load_snapshot()) == 2


def test_snapshot_multi_time_points(client, monkeypatch):
    """9:15/9:20/9:25 三时点独立归档, 互不覆盖"""
    seq = {"n": 0}

    def fake_fetch2(fs):
        seq["n"] += 1
        a = dict(RAW)
        a["f12"] = "600001"
        a["f615"] = [1.0, 2.5, 4.0][min((seq["n"] - 1) // 3, 2)]   # 每时点三分区同值, 依次 1.0/2.5/4.0
        return [a]

    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", fake_fetch2)
    auction_snapshot.snapshot_at("9_15", force=True)
    auction_snapshot.snapshot_at("9_20", force=True)
    auction_snapshot.snapshot_at("9_25", force=True)
    assert auction_snapshot.load_snapshot(time_point="9_15")["600001"]["bid_change"] == 1.0
    assert auction_snapshot.load_snapshot(time_point="9_20")["600001"]["bid_change"] == 2.5
    assert auction_snapshot.load_snapshot(time_point="9_25")["600001"]["bid_change"] == 4.0


def test_snapshot_load_empty(client):
    """无数据日期返回空 map"""
    assert auction_snapshot.load_snapshot("2000-01-01") == {}


def test_snapshot_fetch_fail_returns_0(client, monkeypatch):
    """三分区全失败时返回 0

    🔴 2026-09-29 补严: 本用例原先只桩了东财/腾讯两个分区, 但主源之后还有**兜底链**
      (① 猫爪 screening → ② 开盘啦竞价榜 `_fetch_kpl_fallback`), 后者能从预热行情缓存
      /测试桩里拿到 1 只票 ⇒ "全失败"这个前提不成立, 断言 `== 0` 拿到 1 而失败。
      实测: 这是**既有**的顺序耦合 —— 用改动前代码跑同一文件组合同样失败(单跑则通过,
      因为那时 spot 缓存还是冷的)。这里显式关掉 ②, 让"三源全失败"真的成立。
    """
    def boom(fs):
        raise RuntimeError("network down")
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", boom)
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_tencent_market", boom)
    monkeypatch.setattr(auction_snapshot, "_fetch_kpl_fallback", lambda: [])
    assert auction_snapshot.snapshot_at("9_20", force=True) == 0


def test_snapshot_invalid_time_point(client):
    assert auction_snapshot.snapshot_at("9_99", force=True) == 0


def test_snapshot_filters_abnormal_change(client, monkeypatch):
    """归档过滤异常涨幅(±30%外, 防非交易时段字段污染)"""
    def fake_fetch(fs):
        a = dict(RAW); a["f12"] = "600001"; a["f615"] = 4.0       # 正常
        b = dict(RAW); b.update({"f12": "000002", "f615": 360.5})  # 异常
        return [a, b]
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", fake_fetch)
    n = auction_snapshot.snapshot_at("9_25", force=True)
    assert n == 1
    snap = auction_snapshot.load_snapshot(time_point="9_25")
    assert "600001" in snap and "000002" not in snap


# ---------- 历史回放 ----------
def test_query_snapshot_sorted(client, monkeypatch):
    """query_snapshot 按竞价涨幅降序 + limit

    ⚠️ 断言只覆盖**本用例写入的 3 个代码**，不假设 `(今天, 9_25)` 只有这 3 行：
    `snapshot_bid` 落库走 `INSERT OR REPLACE`、主键 `(date, time_point, code)`，
    **不会清除同组下别的 code** —— 别的用例往同一 `(date, time_point)` 写过的合成行会被并进来。
    历史误红（2026-09-28）：`_bj_date()` 由周日翻到周一后恰好与另一用例的污染日期重合，
    原来的「整表精确相等」断言因此失败（实测恰多 2 行 `600002`/`600003`，**非真实采集** ——
    真实采集一次是 5561 行、`limit=50` 会顶满）。本用例要验的是「降序 + limit」，
    与「同组里有没有别的代码」无关，故改为按本用例代码取子序列断言。
    """
    def fake_fetch(fs):
        a = dict(RAW); a["f12"] = "600001"; a["f615"] = 1.0
        b = dict(RAW); b.update({"f12": "000002", "f615": 6.0})
        c = dict(RAW); c.update({"f12": "300003", "f615": 3.0})
        return [a, b, c]
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", fake_fetch)
    auction_snapshot.snapshot_at("9_25", force=True)
    date = auction_snapshot._bj_date()
    rows = auction_snapshot.query_snapshot(date, "9_25", 50)
    mine = ("600001", "000002", "300003")
    got = [r["code"] for r in rows if r["code"] in mine]
    assert got == ["000002", "300003", "600001"]   # 6.0 > 3.0 > 1.0
    assert len(auction_snapshot.query_snapshot(date, "9_25", 2)) == 2


# ---------- 竞价排查日志 ----------
def test_lock_missing_snapshot_warns(client, first_user, monkeypatch, caplog):
    """竞价窗口内 lock 但当日 9:20 快照缺失 → 必须产生 warning 告警(排查关键)
    (2026-09-02 当日幂等: 无 force 自动 lock 若命中当日同参批次会直读返回跳过告警,
     本用例验证的是重算路径的告警, 显式 force=1 锁定重算)"""
    token, _, _ = first_user
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 25, True))
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda *a, **k: {})
    with caplog.at_level(logging.WARNING, logger="app"):
        r = client.get("/api/stocks?action=lock&markets=sh_sz&force=1",
                       headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("9:20 快照缺失" in m for m in msgs)


def test_lock_context_log(client, first_user, monkeypatch, caplog):
    """lock 上下文日志包含 窗口/快照/昨日额 状态
    (2026-09-02 当日幂等: 同参自动 lock 直读时无重算日志, 本用例验证重算路径, force=1)"""
    token, _, _ = first_user
    monkeypatch.setattr(scorer, "in_auction_window", lambda: True)
    monkeypatch.setattr(scorer, "bj_now", lambda: (9, 25, True))
    with caplog.at_level(logging.INFO, logger="app"):
        r = client.get("/api/stocks?action=lock&markets=sh_sz&force=1",
                       headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("选股上下文" in m and "auction_window=True" in m for m in msgs)


def test_daily_yizi_logs_result(client, caplog):
    """一字涨停统计成功落库后必须记结果日志"""
    yizi_a = dict(RAW)
    yizi_a.update({"f12": "600001", "f18": 10.0, "f17": 11.0, "f616": 2.0e7})
    with caplog.at_level(logging.INFO, logger="app"):
        stats.record_daily_yizi([yizi_a])
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("一字涨停统计" in m and "数量1" in m for m in msgs)


def test_snapshot_non_zt_seal_zero(client, monkeypatch):
    """回归(2026-08-16): 非涨停时点 bid_buy_amt 必须为 0!
    之前 _fetch_market_map 给所有股票算了东财 f10×f5 默认值,
    开板股若不在 KPL 榜会保留非零值 → 前端显示'封单'(用户反馈问题)"""
    from app.db import database
    import app.services.kpl as kpl_mod

    def fake_fetch(fs):
        rows = [
            {**RAW, "f12": "600001", "f615": 10.0},      # 涨停(主板≥9.9)
            {**RAW, "f12": "600002", "f615": 5.0, "f10": 2000, "f5": 10.5},  # 非涨停
        ]
        return rows

    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", fake_fetch)
    # KPL 榜只包含 600001(涨停); 600002 非涨停不在榜
    monkeypatch.setattr(kpl_mod, "fetch_bid_seal",
                        lambda: [{"code": "600001", "bidSealAmt": 1.2e8, "board": "测试"}])
    monkeypatch.setattr(kpl_mod, "clear_cache", lambda: None)

    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    conn.commit()
    conn.close()

    n = auction_snapshot.snapshot_at("9_25", force=True)
    assert n >= 2, "应至少写入 2 只股票"
    conn = database.get_conn()
    zt = conn.execute("SELECT bid_buy_amt FROM snapshot_bid WHERE code='600001' AND time_point='9_25'").fetchone()
    nonzt = conn.execute("SELECT bid_buy_amt FROM snapshot_bid WHERE code='600002' AND time_point='9_25'").fetchone()
    conn.close()
    assert zt and zt[0] == 1.2e8, "涨停股应有 KPL 封单"
    assert nonzt and nonzt[0] == 0, "非涨停股封单必须为 0(否则前端显示假封单)"


# ---------- 最后一秒高频采样 (2026-08-21 补充) ----------
def test_snapshot_lastsec_at_ok(client, monkeypatch):
    """最后一秒采样: 单页模式抓取并落库, 返回数量"""
    import time as _time
    # patch gmtime 为工作日(周一), 绕过周末防御
    def fake_gmtime(sec=None):
        return _time.struct_time((2026, 8, 17, 9, 30, 0, 0, 0, -1))
    monkeypatch.setattr(_time, "gmtime", fake_gmtime)

    def fake_fetch(fs):
        a = dict(RAW); a["f12"] = "600001"; a["f615"] = 4.0
        b = dict(RAW); b.update({"f12": "000002", "f615": 6.0})
        return [a, b]
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", fake_fetch)
    monkeypatch.setattr(auction_snapshot, "_bj_date", lambda: "2026-08-20")
    n = auction_snapshot.snapshot_lastsec_at(34200)   # ts 9:30:00
    assert n == 2
    from app.db import database
    conn = database.get_conn()
    rows = conn.execute("SELECT code, bid_change FROM snapshot_lastsec "
                        "WHERE date='2026-08-20' AND ts=34200").fetchall()
    conn.close()
    assert len(rows) == 2


def test_snapshot_lastsec_at_empty(client, monkeypatch):
    """拉取为空 → 返回 0"""
    import time as _time

    def fake_gmtime(sec=None):
        return _time.struct_time((2026, 8, 17, 9, 30, 0, 0, 0, -1))
    monkeypatch.setattr(_time, "gmtime", fake_gmtime)
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney", lambda fs: None)
    monkeypatch.setattr(auction_snapshot, "_bj_date", lambda: "2026-08-20")
    assert auction_snapshot.snapshot_lastsec_at(34200) == 0


def test_query_stock_snapshot_missing(client):
    """无数据 → 空 points, 名称为空"""
    d = auction_snapshot.query_stock_snapshot("2000-01-01", "600001")
    assert d["name"] == "" and d["points"] == {}


def test_check_seal_quality_nonzt_seal(client, monkeypatch):
    """非涨停股挂封单 → 告警且 ok=False"""
    from app.services import notify
    sent = []
    monkeypatch.setattr(notify, "send_text", lambda msg, **k: sent.append(msg))
    monkeypatch.setattr(auction_snapshot, "_bj_date", lambda: "2026-08-20")
    # 直接构造 DB 数据: 600001 涨停有封单, 000002 非涨停但封单>0
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    conn.executemany(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,bid_buy_amt,float_mv) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [("2026-08-20", "9_25", "600001", 10.0, 5e7, 1, "测A", 5e8, 4e9),   # 涨停+正常封单
         ("2026-08-20", "9_25", "000002", 5.0, 3e7, 1, "测B", 1e7, 5e9)])   # 非涨停+挂封单
    conn.commit()
    conn.close()
    r = auction_snapshot.check_seal_quality("2026-08-20", "9_25", force=True)
    assert r is not None and r["ok"] is False
    assert r["n_nonzt_seal"] == 1
    assert sent, "应推送告警"


def test_check_seal_quality_ok(client, monkeypatch):
    """数据健康 → ok=True, 不推送"""
    from app.services import notify
    sent = []
    monkeypatch.setattr(notify, "send_text", lambda msg, **k: sent.append(msg))
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    conn.executemany(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,bid_buy_amt,float_mv) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [("2026-08-20", "9_25", "600001", 10.0, 5e7, 1, "测A", 5e8, 4e9),
         ("2026-08-20", "9_25", "000002", 6.0, 3e7, 1, "测B", 0, 5e9)])   # 非涨停无封单
    conn.commit()
    conn.close()
    r = auction_snapshot.check_seal_quality("2026-08-20", "9_25", force=True)
    assert r is not None and r["ok"] is True
    assert r["n_zt"] == 1 and r["n_nonzt_seal"] == 0
    assert not sent


def test_check_seal_quality_abnormal_ratio(client, monkeypatch):
    """封单/流通比异常 → 进入 abnormal_ratio 告警"""
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    # 涨停股封单 3e9 / 流通 1e9 = 3 > 0.5 → 异常
    conn.execute(
        "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,bid_buy_amt,float_mv) "
        "VALUES ('2026-08-20','9_25','600001',10.0,5e7,1,'测A',3e9,1e9)")
    conn.commit()
    conn.close()
    r = auction_snapshot.check_seal_quality("2026-08-20", "9_25", force=True)
    assert r is not None and r["ok"] is False
    assert any("封单/流通比异常" in p for p in r["problems"])


# ---------- 三时点榜分层 (2026-08-21 补充) ----------
def _seed_3points(conn, code, chgs):
    """为一只股票写入三时点数据; chgs: {tp: (bid_change, bid_buy_amt)}"""
    conn.execute("PRAGMA busy_timeout=5000")
    import time as _t
    for tp, (bc, buy) in chgs.items():
        conn.execute(
            "INSERT INTO snapshot_bid(date,time_point,code,bid_change,bid_amt,ts,name,bid_buy_amt,float_mv) "
            "VALUES ('2026-08-20',?,?,?,?,?,?,?,?)",
            (tp, code, bc, 0, int(_t.time()), "股" + code[-3:], buy, 4e9))
    conn.commit()


def test_3points_layer1_925zt(client, monkeypatch):
    """9:25 涨停 → layer 1 (封死)"""
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    _seed_3points(conn, "600001", {"9_15": (9.0, 1e8), "9_20": (9.5, 2e8), "9_25": (10.0, 3e8)})
    _seed_3points(conn, "000002", {"9_15": (5.0, 0), "9_20": (5.0, 0), "9_25": (5.5, 0)})
    conn.close()
    rows = auction_snapshot.query_3points_board("2026-08-20", 100)
    first = rows[0]
    assert first["code"] == "600001" and first["layer"] == 1
    assert first["tag"] == "9:25封死"


def test_3points_layer3_915only(client, monkeypatch):
    """仅 9:15 涨停 → layer 3"""
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    _seed_3points(conn, "600001", {"9_15": (10.0, 1e8), "9_20": (5.0, 0), "9_25": (5.5, 0)})
    conn.close()
    rows = auction_snapshot.query_3points_board("2026-08-20", 100)
    assert rows and rows[0]["layer"] == 3


def test_3points_degraded_ge5(client, monkeypatch):
    """无涨停 → 降级展示 9:25 涨幅≥5%"""
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    _seed_3points(conn, "600001", {"9_15": (5.0, 0), "9_20": (5.0, 0), "9_25": (6.0, 0)})
    conn.close()
    rows = auction_snapshot.query_3points_board("2026-08-20", 100)
    assert rows and rows[0]["layer"] == 4 and rows[0]["degraded"] is True


def test_3points_second_degrade_ge3(client, monkeypatch):
    """连 ≥5% 都没有 → 二次降级到 9:25 涨幅≥3%"""
    from app.db import database
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid")
    _seed_3points(conn, "600001", {"9_15": (3.0, 0), "9_20": (3.0, 0), "9_25": (3.5, 0)})
    conn.close()
    rows = auction_snapshot.query_3points_board("2026-08-20", 100)
    assert rows and rows[0]["layer"] == 5 and rows[0]["degraded"] is True
