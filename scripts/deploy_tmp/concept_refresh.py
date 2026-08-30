# -*- coding: utf-8 -*-
"""
盘中概念静默刷新服务(后台 worker 执行, 不影响前端 API 调用)
================================================================
- 工作日 9:30 ~ 15:00, 每 30 分钟执行一次
- 目标: 收集当日所有竞价异动 tab + 龙虎榜 中涉及的股票代码集合,
  逐股用开盘啦 doc94 GetStockIDPlate 查询完整概念, 回写数据库
- 优点: 
  1. 前端 API 无需再逐股查概念(直接读库, <50ms)
  2. 后台分批刷新, 均匀摊薄 API 调用, 不触发限流
  3. 失败自动重试(单次跳过, 下轮再补, 不阻塞主流程)

调度规则:
  - 9:30, 10:00, 10:30, 11:00, 11:30, 13:00, 13:30, 14:00, 14:30, 15:00
  - 周末/节假日: 跳过(无当日数据可刷新)
"""
import concurrent.futures
import json
import sqlite3
import threading
import time

from ..core import logger
from ..db import database
from ..core import config
from . import kpl

log = logger.get_logger(__name__)

# 盘中刷新窗口 (北京时间, 分钟)
REFRESH_TIMES = [
    9 * 60 + 30,  10 * 60,  10 * 60 + 30,  11 * 60,  11 * 60 + 30,
    13 * 60,  13 * 60 + 30,  14 * 60,  14 * 60 + 30,  15 * 60,
]

# 每只股票间停顿(ms), 避免开盘啦限流
PER_STOCK_SLEEP_MS = 80
# 并发线程池大小(适中, 避免被封)
MAX_WORKERS = 3
# 概念截断(只保留前N个, 与前端一致)
TRUNCATE_N = 2


def _bj_time_hm():
    """返回北京时间 (小时*60+分钟)"""
    g = time.gmtime(time.time() + 8 * 3600)
    return g, g.tm_hour * 60 + g.tm_min


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _collect_codes(date):
    """收集当日所有竞价/上榜实时接口的股票代码集合
    =================================================================
    之前只看落库 auction_daily_history(=9:26 竞价时点那批, 竞价爆量仅97只),
    但盘中实时接口(如 fetch_bid_boom)返回更多(407只), 新出现的股票概念读不到。
    改为: 同时采集**所有实时接口**(竞价异动各 tab 实时版 + 抢筹三表 + 龙虎榜 +
    炸板/昨涨停/昨断板)返回的股票, 保证覆盖前端展示的全部股票。
    返回 set[str(code)]"""
    codes = set()

    def _add(lst):
        """从股票列表提取 code"""
        if not lst:
            return
        for it in lst:
            raw = it.get("code", "")
            if raw is None:
                continue
            c = str(raw).strip()
            if c and c.lower() != "none":
                codes.add(c)

    # 1) 落库快照(9:26/15:30 已落库的, 兜底)
    try:
        conn = sqlite3.connect(config.DB_FILE)
        for tab in ("seal", "boom", "bid_net", "qiangcang", "yest_zt", "yest_broken",
                    "broken_yest", "broken_today"):
            row = conn.execute(
                "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
                (date, tab)).fetchone()
            if row and row[0]:
                try:
                    _add(json.loads(row[0]))
                except Exception:
                    continue
        # qc_snapshot
        for r in conn.execute(
                "SELECT DISTINCT code FROM qc_snapshot WHERE date=?", (date,)).fetchall():
            codes.add(str(r[0]))
        # lhb_history
        row = conn.execute(
            "SELECT list FROM lhb_history WHERE date=?", (date,)).fetchone()
        if row and row[0]:
            try:
                _add(json.loads(row[0]))
            except Exception:
                pass
        conn.close()
    except Exception as e:
        log.warning("概念刷新[采集落库股票]失败 date=%s err=%s", date, e)

    # 2) 实时接口(覆盖盘中新增股票; 失败不影响主流程)
    try:
        _add(kpl.fetch_bid_boom() or [])
        _add(kpl.fetch_bid_net() or [])
        qc = kpl.fetch_bid_qiangcang() or {}
        _add(qc.get("list20") or [])
        _add(qc.get("list20Chg") or [])
        _add(qc.get("listLast") or [])
        _add(kpl.fetch_yest_zt() or [])
        _add(kpl.fetch_yest_broken() or [])
        _add(kpl.fetch_broken_zt() or [])
        _add(kpl.fetch_lhb() or [])
        _add(kpl.fetch_wpqc() or [])
    except Exception as e:
        log.warning("概念刷新[采集实时股票]失败 date=%s err=%s", date, e)
    log.info("概念刷新 date=%s 采集到 %d 只(实时+落库)", date, len(codes))
    return codes


def _update_lists_with_board(date, code_to_board):
    """把概念字典 {code: board} 回写到当日所有竞价异动 tab + 龙虎榜 + qc_snapshot
    只回写 board 字段, 不改动其他列; 返回成功更新的行数"""
    if not code_to_board:
        return 0
    n_updated = 0
    try:
        conn = sqlite3.connect(config.DB_FILE)
        # 1. 竞价异动各 tab
        for tab in ("seal", "boom", "bid_net", "qiangcang", "yest_zt", "yest_broken",
                    "broken_yest", "broken_today"):
            row = conn.execute(
                "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
                (date, tab)).fetchone()
            if not row or not row[0]:
                continue
            try:
                lst = json.loads(row[0])
                changed = 0
                for it in lst:
                    c = str(it.get("code", "")).strip()
                    nb = code_to_board.get(c)
                    if nb and it.get("board") != nb:
                        it["board"] = nb
                        changed += 1
                if changed:
                    conn.execute(
                        "UPDATE auction_daily_history SET list=?, ts=? WHERE date=? AND tab=?",
                        (json.dumps(lst, ensure_ascii=False), int(time.time()), date, tab))
                    n_updated += changed
            except Exception:
                continue
        # 2. qc_snapshot
        for code, board in code_to_board.items():
            conn.execute(
                "UPDATE qc_snapshot SET board=? WHERE date=? AND code=? AND (board IS NULL OR board='' OR board!=?)",
                (board, date, code, board))
            n_updated += conn.total_changes
        # 3. 龙虎榜
        row = conn.execute(
            "SELECT list FROM lhb_history WHERE date=?", (date,)).fetchone()
        if row and row[0]:
            try:
                lst = json.loads(row[0])
                changed = 0
                for it in lst:
                    c = str(it.get("code", "")).strip()
                    nb = code_to_board.get(c)
                    if nb and it.get("board") != nb:
                        it["board"] = nb
                        changed += 1
                if changed:
                    conn.execute(
                        "UPDATE lhb_history SET list=?, ts=? WHERE date=?",
                        (json.dumps(lst, ensure_ascii=False), int(time.time()), date))
                    n_updated += changed
            except Exception:
                pass
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("概念刷新[回写DB]失败 date=%s err=%s", date, e)
    return n_updated


def _refresh_batch(date, codes):
    """逐股查开盘啦概念
    返回 (short_map, full_map):
      short_map: {code: 前TRUNCATE_N个概念拼接串}  → 列表页/预览用
      full_map : {code: 全量概念拼接串(开盘啦原样)} → stock_concept.board_full, 悬浮展示全部"""
    if not codes:
        return {}, {}
    short_map, full_map = {}, {}
    codes_list = sorted(codes)
    total = len(codes_list)
    log.info("概念刷新 date=%s 共 %d 只待查询", date, total)
    done = 0

    def _one(c):
        try:
            # use_cache=False: 跳过 1 天缓存, 确保盘中每30分钟真正拿到开盘啦最新概念
            b = kpl.fetch_stock_plate(c, use_cache=False) or ""
            if b:
                parts = [p.strip() for p in b.split("\u3001") if p.strip()]
                short = "\u3001".join(parts[:TRUNCATE_N])
                full = "\u3001".join(parts)
            else:
                short = full = ""
            time.sleep(PER_STOCK_SLEEP_MS / 1000.0)
            return c, short, full
        except Exception:
            return c, "", ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for c, short, full in ex.map(_one, codes_list):
            if short:
                short_map[c] = short
            if full:
                full_map[c] = full
            done += 1
            if done % 20 == 0 or done == total:
                log.info("概念刷新 进度 %d/%d  已获概念 %d", done, total, len(short_map))
    return short_map, full_map


def run_refresh_round(force=False):
    """执行一轮盘中概念静默刷新
    force=True: 跳过交易日+时间窗检查(测试/手动触发)
    返回 (status, msg)"""
    g, hm = _bj_time_hm()
    date = _bj_date()
    if not force:
        # 非工作日跳过
        if g.tm_wday >= 5:
            return "skip", f"周末/节假日 {date} 跳过"
        # 不在刷新时间点(允许±5分钟宽松窗口)
        if not any(abs(hm - t) <= 5 for t in REFRESH_TIMES):
            return "skip", f"非刷新时间窗 {g.tm_hour:02d}:{g.tm_min:02d} 跳过"
    log.info("========== 概念刷新 开始 date=%s time=%02d:%02d ==========",
             date, g.tm_hour, g.tm_min)
    t0 = time.time()
    try:
        codes = _collect_codes(date)
        if not codes:
            log.warning("概念刷新 date=%s 未收集到任何股票(可能未开市), 跳过", date)
            return "empty", f"{date} 无数据"
        # 逐股查概念
        code_to_board, code_to_full = _refresh_batch(date, codes)
        t1 = time.time()
        # 回写 DB(各 tab 列表 JSON + 独立概念映射表 stock_concept)
        n_written = _update_lists_with_board(date, code_to_board)
        n_concept = _write_stock_concept(date, code_to_board, code_to_full)
        t2 = time.time()
        log.info(
            "========== 概念刷新 完成 date=%s 总耗时=%.1fs 查询=%.1fs 写库=%.1fs "
            "查询股票=%d 获得概念=%d 列表更新=%d 概念表=%d ==========",
            date, t2 - t0, t1 - t0, t2 - t1,
            len(codes), len(code_to_board), n_written, n_concept)
        return "ok", (f"{date} 完成, 查询{len(codes)}/{len(code_to_board)}只, "
                      f"写库{n_written}条, 概念表{n_concept}条, 耗时{t2-t0:.1f}s")
    except Exception as e:
        log.error("概念刷新 异常 date=%s err=%s", date, e, exc_info=True)
        return "err", str(e)


def _ensure_board_full():
    """老库迁移: 给 stock_concept 补 board_full 列(全量概念)"""
    try:
        conn = sqlite3.connect(config.DB_FILE)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(stock_concept)").fetchall()]
        if "board_full" not in cols:
            conn.execute("ALTER TABLE stock_concept ADD COLUMN board_full TEXT")
            conn.commit()
            log.info("stock_concept 迁移: 已增加 board_full 列")
        conn.close()
    except Exception as e:
        log.warning("stock_concept 迁移[board_full]失败 err=%s", e)


def _write_stock_concept(date, code_to_board, code_to_full=None):
    """把当日采集到 {code:前N概念} 与 {code:全量概念} upsert 到 stock_concept(date, code, board, board_full, ts)
    前端竞价各接口从 board 读取前N概念; board_full 供 AI 预测悬浮展示全部概念。"""
    if not code_to_board:
        return 0
    code_to_full = code_to_full or {}
    _ensure_board_full()
    try:
        conn = sqlite3.connect(config.DB_FILE)
        now = int(time.time())
        conn.executemany(
            "INSERT OR REPLACE INTO stock_concept (date, code, board, board_full, ts) VALUES (?,?,?,?,?)",
            [(date, code, board, code_to_full.get(code, board), now)
             for code, board in code_to_board.items()])
        conn.commit()
        conn.close()
        return len(code_to_board)
    except Exception as e:
        log.warning("概念刷新[写 stock_concept]失败 date=%s err=%s", date, e)
        return 0


# ---------- 调度(后台线程, 由 worker.py 启动) ----------
_last_run_hm = None   # 上次执行的时间点 hm, 防重


def _scheduler_loop():
    """轮询调度: 每 30s 检查一次, 到点就执行(单轮约2-5min, 不阻塞下一轮)"""
    global _last_run_hm
    log.info("盘中概念静默刷新 调度已启动(30min/轮 9:30~15:00 工作日)")
    while True:
        try:
            _, hm = _bj_time_hm()
            # 匹配目标时间点(±1分钟内, 且本轮未执行过)
            for t in REFRESH_TIMES:
                if abs(hm - t) <= 1 and _last_run_hm != t:
                    threading.Thread(
                        target=run_refresh_round, args=(), daemon=True,
                        name=f"concept-refresh-{t}").start()
                    _last_run_hm = t
                    break
            # 跨天/换日 清上次执行记录
            if _last_run_hm is not None and hm < min(REFRESH_TIMES) - 5:
                _last_run_hm = None
        except Exception as e:
            log.warning("概念刷新调度异常 err=%s", e)
        time.sleep(30)


def start_scheduler():
    """启动后台概念刷新线程(worker.py 调用)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="concept-refresh-sched")
    t.start()
    log.info("盘中概念静默刷新 调度线程已启动(每轮 9:30/10:00/.../15:00)")
