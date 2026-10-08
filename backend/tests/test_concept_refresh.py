# -*- coding: utf-8 -*-
"""盘中概念静默刷新 concept_refresh 测试"""
import json
import os
import sqlite3
import tempfile
import time as _time

import pytest


def _safe_unlink(path):
    """删除临时文件(Windows 兼容, 处理可能的 WAL/journal 伴随文件)"""
    for suffix in ("", "-wal", "-shm", "-journal", "-sqlite-journal"):
        p = path + suffix
        try:
            if os.path.exists(p):
                os.remove(p)
        except (FileNotFoundError, PermissionError):
            pass


def _tmp_db():
    """构造临时 SQLite, 写入 concept_refresh 需要的表, 返回路径"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)  # 关闭文件句柄, 避免 Windows 锁定
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS auction_daily_history (
        date TEXT, tab TEXT, list TEXT, ts INT, PRIMARY KEY (date, tab))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qc_snapshot (
        date TEXT, code TEXT, time TEXT, change REAL,
        bidAmt REAL, board TEXT, PRIMARY KEY (date, code, time))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lhb_history (
        date TEXT PRIMARY KEY, list TEXT, ts INT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stock_concept (
        date TEXT, code TEXT, board TEXT, ts INT, PRIMARY KEY (date, code))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshot_bid (
        date TEXT, time_point TEXT, code TEXT, bid_change REAL,
        bid_amt REAL, ts INT, name TEXT, bid_buy_amt REAL,
        float_mv REAL, board TEXT,
        PRIMARY KEY (date, time_point, code))""")
    return path, conn


def test_collect_codes_aggregates_tabs(monkeypatch):
    """_collect_codes: 落库聚合 + 实时接口返回空"""
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path

        # 实时接口 mock 为空
        import app.services.kpl as kpl
        monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_net", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda: {})
        monkeypatch.setattr(kpl, "fetch_yest_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_yest_broken", lambda: [])
        monkeypatch.setattr(kpl, "fetch_broken_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_lhb", lambda: [])
        monkeypatch.setattr(kpl, "fetch_wpqc", lambda: [])

        seal = [{"code": "600001"}, {"code": "000002"}, {"code": "  "}, {"code": None}]
        boom = [{"code": "600001"}, {"code": "300003"}]
        broken_today = [{"code": "000004"}]
        for tab, lst in [("seal", seal), ("boom", boom), ("broken_today", broken_today)]:
            conn.execute(
                "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
                ("2026-08-20", tab, json.dumps(lst, ensure_ascii=False), 123))
        conn.executemany(
            "INSERT INTO qc_snapshot(date,code,time,change,bidAmt,board) VALUES (?,?,?,?,?,?)",
            [("2026-08-20", "300003", "09:25", 3.0, 500, None),
             ("2026-08-20", "688111", "09:25", 5.0, 800, "")])
        lhb = [{"code": "000004"}, {"code": "600111"}]
        conn.execute("INSERT INTO lhb_history(date,list,ts) VALUES (?,?,?)",
                     ("2026-08-20", json.dumps(lhb, ensure_ascii=False), 123))
        conn.commit()
        conn.close()

        codes = concept_refresh._collect_codes("2026-08-20")
        assert isinstance(codes, set)
        assert "600001" in codes and "000002" in codes and "300003" in codes
        assert "000004" in codes and "688111" in codes and "600111" in codes
        assert "" not in codes and " " not in codes and None not in codes
        assert len(codes) == 6
    finally:
        config.DB_FILE = orig_db
        _safe_unlink(db_path)


def test_collect_codes_includes_bse(monkeypatch):
    """_collect_codes: 北交所(4/8/920) **已纳入**

    🔴 2026-09-29 主人拍板「北交所纳入」⇒ 原先的"全链路排除"撤销。
    (本用例 2026-09-21 曾断言"三只全部被过滤", 现按新决策反向断言。)
    """
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path
        # mock 实时接口返回空 (测试环境无网络)
        import app.services.kpl as kpl
        monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_net", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda: {})
        monkeypatch.setattr(kpl, "fetch_yest_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_yest_broken", lambda: [])
        monkeypatch.setattr(kpl, "fetch_broken_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_lhb", lambda: [])
        monkeypatch.setattr(kpl, "fetch_wpqc", lambda: [])

        # 落库表混入北交所三只(920 新段 + 8 老段 + 4 老三板)
        seal = [{"code": "600001"}, {"code": "920267"}, {"code": "830001"}, {"code": "430001"}]
        conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal", json.dumps(seal, ensure_ascii=False), 123))
        conn.commit()
        conn.close()

        codes = concept_refresh._collect_codes("2026-08-20")
        # 2026-09-29 起: 四只全都在集合里(北交所不再被过滤)
        assert codes == {"600001", "920267", "830001", "430001"}
        assert "920267" in codes and "830001" in codes and "430001" in codes
    finally:
        config.DB_FILE = orig_db
        _safe_unlink(db_path)


def test_collect_codes_empty_and_corrupt(monkeypatch):
    """日期无数据 → 空 set; 某 tab JSON 坏了 → 跳过不崩"""
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path
        # mock 实时接口返回空 (测试环境无网络)
        import app.services.kpl as kpl
        monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_net", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda: {})
        monkeypatch.setattr(kpl, "fetch_yest_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_yest_broken", lambda: [])
        monkeypatch.setattr(kpl, "fetch_broken_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_lhb", lambda: [])
        monkeypatch.setattr(kpl, "fetch_wpqc", lambda: [])

        conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal", "NOT-A-JSON{", 123))
        conn.commit()
        conn.close()
        codes = concept_refresh._collect_codes("2026-08-20")
        assert codes == set()
        codes2 = concept_refresh._collect_codes("1999-01-01")
        assert codes2 == set()
    finally:
        config.DB_FILE = orig_db
        _safe_unlink(db_path)


def test_update_lists_with_board_idempotent():
    """_update_lists_with_board: 概念新值时才更新; 旧值相同不写"""
    from app.services import concept_refresh
    from app.core import config
    orig_db = config.DB_FILE
    try:
        db_path, conn = _tmp_db()
        config.DB_FILE = db_path

        seal_src = [
            {"code": "600001", "board": "AI"},
            {"code": "000002", "board": ""},
            {"code": "300003"},
        ]
        conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal", json.dumps(seal_src, ensure_ascii=False), 100))
        conn.execute(
            "INSERT INTO qc_snapshot(date,code,time,change,bidAmt,board) VALUES (?,?,?,?,?,?)",
            ("2026-08-20", "600001", "09:25", 3, 500, "旧值"))
        conn.execute(
            "INSERT INTO qc_snapshot(date,code,time,change,bidAmt,board) VALUES (?,?,?,?,?,?)",
            ("2026-08-20", "000002", "09:25", 3, 500, None))
        lhb_src = [{"code": "600001", "board": "AI"}, {"code": "000004"}]
        conn.execute("INSERT INTO lhb_history(date,list,ts) VALUES (?,?,?)",
                     ("2026-08-20", json.dumps(lhb_src, ensure_ascii=False), 100))
        conn.commit()
        conn.close()

        mapping = {
            "600001": "AI",
            "000002": "医药、创新药",
            "300003": "芯片、半导体",
            "000004": "汽车、新能源车",
            "688999": "不存在",
        }
        n = concept_refresh._update_lists_with_board("2026-08-20", mapping)
        assert n >= 4

        conn2 = sqlite3.connect(db_path)
        row = conn2.execute(
            "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
            ("2026-08-20", "seal")).fetchone()
        seal_new = json.loads(row[0])
        assert seal_new[0]["board"] == "AI"
        assert seal_new[1]["board"] == "医药、创新药"
        assert seal_new[2]["board"] == "芯片、半导体"
        qc_rows = conn2.execute(
            "SELECT code, board FROM qc_snapshot WHERE date='2026-08-20' ORDER BY code"
        ).fetchall()
        boards = {r[0]: r[1] for r in qc_rows}
        assert boards["600001"] == "AI"
        assert boards["000002"] == "医药、创新药"
        row = conn2.execute("SELECT list FROM lhb_history WHERE date='2026-08-20'").fetchone()
        lhb_new = json.loads(row[0])
        assert lhb_new[1]["board"] == "汽车、新能源车"
        conn2.close()

        assert concept_refresh._update_lists_with_board("2026-08-20", {}) == 0
    finally:
        config.DB_FILE = orig_db
        _safe_unlink(db_path)


def test_refresh_batch_truncate_and_threadpool(monkeypatch):
    """_refresh_batch: 概念截断 + 空/异常股丢弃 + 并发"""
    from app.services import concept_refresh
    from app.services import kpl

    orig_sleep = concept_refresh.PER_STOCK_SLEEP_MS
    orig_workers = concept_refresh.MAX_WORKERS
    orig_trunc = concept_refresh.TRUNCATE_N
    concept_refresh.PER_STOCK_SLEEP_MS = 0
    concept_refresh.MAX_WORKERS = 2
    concept_refresh.TRUNCATE_N = 2

    fake_db = {
        "600001": "AI、机器人、算力、大模型",
        "000002": "医药",
        "300003": "",
        "688111": "科创、半导体、封测",
        "000004": None,
    }
    called_codes = set()

    def fake_plate(c, **kwargs):
        called_codes.add(c)
        if c == "000004":
            raise RuntimeError("boom")
        return fake_db.get(c)

    monkeypatch.setattr(kpl, "fetch_stock_plate", fake_plate)
    try:
        codes = {"600001", "000002", "300003", "688111", "000004"}
        short_map, full_map = concept_refresh._refresh_batch("2026-08-20", codes)
        assert called_codes == codes
        # short_map: 前 TRUNCATE_N=2 个概念
        assert short_map.get("600001") == "AI、机器人"
        assert short_map.get("000002") == "医药"
        assert short_map.get("688111") == "科创、半导体"
        assert "300003" not in short_map
        assert "000004" not in short_map
        assert len(short_map) == 3
        # full_map: 全量概念
        assert full_map.get("600001") == "AI、机器人、算力、大模型"
        assert concept_refresh._refresh_batch("2026-08-20", set()) == ({}, {})
    finally:
        concept_refresh.PER_STOCK_SLEEP_MS = orig_sleep
        concept_refresh.MAX_WORKERS = orig_workers
        concept_refresh.TRUNCATE_N = orig_trunc


def test_run_refresh_round_skips_non_weekend_and_timewindow(monkeypatch):
    """run_refresh_round: 周末 skip; 不在时间窗 skip; force=True 跑完整流程"""
    from app.services import concept_refresh

    g_sat = _time.struct_time((2026, 8, 22, 2, 0, 0, 5, 234, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_sat)
    status, _ = concept_refresh.run_refresh_round(force=False)
    assert status == "skip"

    g_off = _time.struct_time((2026, 8, 20, 2, 10, 0, 3, 232, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_off)
    status2, _ = concept_refresh.run_refresh_round(force=False)
    assert status2 == "skip"

    g_hit = _time.struct_time((2026, 8, 20, 2, 2, 0, 3, 232, 0))
    monkeypatch.setattr(_time, "gmtime", lambda *a, **k: g_hit)

    from app.core import config
    from app.services import kpl
    orig_db = config.DB_FILE
    db_path, tmp_conn = _tmp_db()
    config.DB_FILE = db_path
    try:
        # mock 实时接口返回空 (测试环境无网络, 防止真实 API 干扰)
        monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_net", lambda: [])
        monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda: {})
        monkeypatch.setattr(kpl, "fetch_yest_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_yest_broken", lambda: [])
        monkeypatch.setattr(kpl, "fetch_broken_zt", lambda: [])
        monkeypatch.setattr(kpl, "fetch_lhb", lambda: [])
        monkeypatch.setattr(kpl, "fetch_wpqc", lambda: [])

        status3, _ = concept_refresh.run_refresh_round(force=True)
        assert status3 == "empty"

        def fake_collect(date):
            return {"600001", "000002"}

        def fake_batch(date, codes):
            return {"600001": "AI、机器人", "000002": "医药"}

        tmp_conn.execute(
            "INSERT INTO auction_daily_history(date,tab,list,ts) VALUES (?,?,?,?)",
            ("2026-08-20", "seal",
             json.dumps([{"code": "600001"}, {"code": "000002"}], ensure_ascii=False),
             123))
        tmp_conn.commit()
        tmp_conn.close()
        monkeypatch.setattr(concept_refresh, "_collect_codes", fake_collect)
        monkeypatch.setattr(concept_refresh, "_refresh_batch", fake_batch)
        status4, msg = concept_refresh.run_refresh_round(force=True)
        assert status4 == "ok", msg
    finally:
        config.DB_FILE = orig_db
        _safe_unlink(db_path)


# ---------------- 刷新时刻：首轮让开竞价最关键窗口（2026-10-08 主人指示） ----------------
def test_first_refresh_slot_avoids_auction_critical_window():
    """🔴 2026-10-08：概念刷新首轮 09:30 → **09:35**，让开竞价最关键链路。

    依据（2026-10-08 生产实测，非推测）：
      · 本服务今日 09:29:03 起跑（调度 ±1 分钟窗落点）、09:30:02 结束，**耗时 59.1s**，
        与「定格落库 09:26:51 → 系统批次 09:27:45(48687ms) → auto_apply 09:28:16
        → AI 名单 09:30:43」这条链路完全重叠；
      · 同一时刻 09:29:15 出现「开盘啦数据源故障 + GetWPQC 调用失败」—— 本服务逐股打开盘啦
        (doc94 GetStockIDPlate, MAX_WORKERS=3) 与竞价各 tab 的实时出网**共用同一份配额**
        (跨进程信号量 sem:kpl limit=3) ⇒ 起跑时刻正是在和用户正在看的实时榜抢配额。
    故此用例把新时刻钉死（本仓库纪律：注释不会报错，断言会）。
    """
    from app.services import concept_refresh as C
    first = min(C.REFRESH_TIMES)
    assert first == 9 * 60 + 35, \
        "概念刷新首轮应让开竞价关键窗口(09:26~09:31)，当前首轮=%02d:%02d" % (first // 60, first % 60)
    # 任何时段都不得落在 09:26~09:31（定格→系统批次→auto_apply→AI 名单）
    for t in C.REFRESH_TIMES:
        assert not (9 * 60 + 26 <= t <= 9 * 60 + 31), \
            "时段与竞价关键窗口重叠: %02d:%02d" % (t // 60, t % 60)
    # 午休（11:30 之后 ~ 13:00 之前）不得有刷新时段
    assert all(t <= 11 * 60 + 30 or t >= 13 * 60 for t in C.REFRESH_TIMES), "午休时段不应刷新"
    # 除首轮外，其余 9 个时段保持不变（本次只动首轮，不扩大改动面）
    assert C.REFRESH_TIMES[1:] == [
        10 * 60, 10 * 60 + 30, 11 * 60, 11 * 60 + 30,
        13 * 60, 13 * 60 + 30, 14 * 60, 14 * 60 + 30, 15 * 60,
    ], "除首轮外的时段不应改动"