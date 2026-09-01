# -*- coding: utf-8 -*-
"""
历史批次服务: 选股结果落库 / 批次列表 / 批次明细 / 条件分页查询
==============================================================
"""
import json
import sqlite3
import time

from ..core import config, logger
from ..db import database
from . import scorer

log = logger.get_logger(__name__)


def _conn():
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def save_batch(user_id, action, result, f, auto_applied=False):
    """把一次选股结果存为一个历史批次(归属指定用户), 返回批次 id; 失败返回 None
    auto_applied=True 用于 9:26 系统自动应用 (区别用户主动 lock/filter)"""
    t = time.time()
    g = time.gmtime(t + 8 * 3600)   # 北京时间
    bdate = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    btime = "%02d:%02d:%02d" % (g.tm_hour, g.tm_min, g.tm_sec)
    filters_json = json.dumps({k: v for k, v in f.items() if k != "markets"}, ensure_ascii=False)
    markets = ",".join(f["markets"])
    try:
        conn = database.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO batches (batch_date, batch_time, ts, action, markets, filters, stock_count, user_id, auto_applied) VALUES (?,?,?,?,?,?,?,?,?)",
            (bdate, btime, int(t), action, markets, filters_json, len(result), user_id,
             1 if auto_applied else 0))
        batch_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO batch_stocks (batch_id, rank, code, name, probability, confidence, bid_change, real_change, entity_change, bid_turnover, warn_type, circulation_mv, industry, concept, bid_amt, bid_ratio, qiangchou) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(batch_id, i + 1, s["code"], s["name"], s["probability"], s["confidence"],
              s["bidChange"], s["realChange"], s["entityChange"], s["bidTurnover"],
              s["warnType"], s["circulationMV"], s["industry"], s["concept"], s["bidAmt"],
              s.get("bidRatio"), 1 if s.get("qiangchou") else 0)
             for i, s in enumerate(result)])
        conn.commit()
        conn.close()
        return batch_id
    except Exception as e:
        log.error("批次落库失败 user_id=%s action=%s 数量%d err=%s",
                  user_id, action, len(result), e)
        return None


def list_batches(user_id, limit=200):
    """历史批次列表(2026-08-30 主人需求: 即使用户没点选股, 也要看 system 自动存的批次)
    合并返回: 当前用户自己的批次 + system 公共批次(user_id=0, auto_applied=1)
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM batches WHERE user_id=? OR (user_id=0 AND auto_applied=1) "
        "ORDER BY ts DESC LIMIT ?", (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_batch(batch_id, user_id):
    """获取批次明细: 允许查看自己的或 system 公共批次(user_id=0)
    注: get_batch 旧实现只允许 user_id 匹配, 这里扩展支持 system 批次
    """
    conn = _conn()
    b = conn.execute(
        "SELECT * FROM batches WHERE id=? AND (user_id=? OR (user_id=0 AND auto_applied=1))",
        (batch_id, user_id)).fetchone()
    if not b:
        conn.close()
        return None, None
    stocks = conn.execute("SELECT * FROM batch_stocks WHERE batch_id=? ORDER BY rank", (batch_id,)).fetchall()
    conn.close()
    return dict(b), [dict(s) for s in stocks]


def query_history(uid, q):
    """历史条件查询(分页)。
    q: {k: [v,...]} 形式的 parse_qs 结果, 支持 date_from/date_to/bid_min/bid_max/
       mv_min/mv_max/prob_min/conf_min/action/page/pageSize
    返回 {total, page, pageSize, rows}
    """
    date_from = scorer._q_date((q.get("date_from") or [None])[0], "2000-01-01")
    date_to = scorer._q_date((q.get("date_to") or [None])[0], "2100-12-31")
    bid_min = scorer._opt_float(q, "bid_min")
    bid_max = scorer._opt_float(q, "bid_max")
    mv_min = scorer._opt_float(q, "mv_min")
    mv_max = scorer._opt_float(q, "mv_max")
    prob_min = scorer._opt_float(q, "prob_min")
    conf_min = scorer._opt_float(q, "conf_min")
    action = (q.get("action") or [""])[0]
    if action not in ("lock", "filter"):
        action = ""
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    conds = []
    params = []
    if bid_min is not None:
        conds.append("s.bid_change >= ?"); params.append(bid_min)
    if bid_max is not None:
        conds.append("s.bid_change <= ?"); params.append(bid_max)
    if mv_min is not None:
        conds.append("s.circulation_mv >= ?"); params.append(mv_min)
    if mv_max is not None:
        conds.append("s.circulation_mv <= ?"); params.append(mv_max)
    if prob_min is not None:
        conds.append("s.probability >= ?"); params.append(prob_min)
    if conf_min is not None:
        conds.append("s.confidence >= ?"); params.append(conf_min)
    if action:
        conds.append("b.action = ?"); params.append(action)
    cond_sql = " AND ".join(conds) if conds else "1=1"

    try:
        page = max(1, int((q.get("page") or [1])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int((q.get("pageSize") or [100])[0])))
    except (TypeError, ValueError):
        page_size = 100

    # 同一日期内, 同一股票 + 相同评分 视为"重复入选", 只保留一条(去重)
    # 2026-08-30 主人需求: 查询条件页面也合并 system 公共批次(user_id=0, auto_applied=1)
    base_sql = """
        FROM batch_stocks s
        JOIN batches b ON b.id = s.batch_id
        WHERE (b.user_id = ? OR (b.user_id = 0 AND b.auto_applied = 1))
              AND b.batch_date BETWEEN ? AND ? AND %s
        GROUP BY b.batch_date, s.code, s.probability
    """ % cond_sql
    base_params = [uid, date_from, date_to] + params

    sql = """
        SELECT b.batch_date, b.batch_time, b.action,
               s.code, s.name, s.probability, s.confidence,
               s.bid_change, s.real_change, s.entity_change,
               s.bid_amt, s.circulation_mv, s.industry, s.concept, s.warn_type,
               s.bid_ratio, s.qiangchou
        %s
        ORDER BY b.batch_date DESC, s.probability DESC, s.code
        LIMIT ? OFFSET ?
    """ % base_sql

    conn = _conn()
    try:
        # 注意: GROUP BY 后 COUNT(*) 是每组行数, 必须子查询包裹才算组数
        total = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 " + base_sql + ")", base_params).fetchone()[0]
        rows = conn.execute(sql, base_params + [page_size, (page - 1) * page_size]).fetchall()
    except Exception:
        conn.close()
        raise
    conn.close()
    return {"total": total, "page": page, "pageSize": page_size,
            "rows": [dict(r) for r in rows]}
