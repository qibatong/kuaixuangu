# -*- coding: utf-8 -*-
"""
战绩分析服务: 基于历史选股批次计算胜率/评分有效性/每日趋势
==============================================================
胜率定义: 入选后当日实时涨幅(real_change) > 0 视为"上涨"(赢)
"""
import sqlite3

from ..core import config
from . import scorer


def _conn():
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def compute_performance(uid, date_from, date_to):
    """综合战绩统计, 返回:
    - overview: 总入选/平均评分/平均竞价涨幅/平均实时涨幅/胜率/上涨次数
    - top3: 每批次概率前3(奖牌区核心策略)的 次数/胜率/平均涨幅
    - by_score: 评分区间胜率(验证评分有效性)
    - daily: 每日 次数/胜率/平均涨幅
    """
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    conn = _conn()

    # 1. 总览
    r = conn.execute("""
        SELECT COUNT(*) AS total,
               AVG(s.probability) AS avg_score,
               AVG(s.bid_change)  AS avg_bid,
               AVG(s.real_change) AS avg_real,
               SUM(CASE WHEN s.real_change > 0 THEN 1 ELSE 0 END) AS win_cnt
        FROM batch_stocks s JOIN batches b ON b.id = s.batch_id
        WHERE b.user_id=? AND b.batch_date BETWEEN ? AND ?
    """, (uid, date_from, date_to)).fetchone()
    total = r["total"] or 0
    win_cnt = r["win_cnt"] or 0
    overview = {
        "total": total,
        "win_cnt": win_cnt,
        "win_rate": round(win_cnt / total, 4) if total else 0,
        "avg_score": round(r["avg_score"], 1) if r["avg_score"] is not None else 0,
        "avg_bid": round(r["avg_bid"], 2) if r["avg_bid"] is not None else 0,
        "avg_real": round(r["avg_real"], 2) if r["avg_real"] is not None else 0,
    }

    # 2. 前三强(rank<=3, 奖牌区策略)
    r = conn.execute("""
        SELECT COUNT(*) AS cnt,
               SUM(CASE WHEN s.real_change > 0 THEN 1 ELSE 0 END) AS win_cnt,
               AVG(s.real_change) AS avg_real,
               AVG(s.probability) AS avg_score
        FROM batch_stocks s JOIN batches b ON b.id = s.batch_id
        WHERE b.user_id=? AND b.batch_date BETWEEN ? AND ? AND s.rank <= 3
    """, (uid, date_from, date_to)).fetchone()
    cnt3 = r["cnt"] or 0
    win3 = r["win_cnt"] or 0
    top3 = {
        "total": cnt3,
        "win_cnt": win3,
        "win_rate": round(win3 / cnt3, 4) if cnt3 else 0,
        "avg_real": round(r["avg_real"], 2) if r["avg_real"] is not None else 0,
        "avg_score": round(r["avg_score"], 1) if r["avg_score"] is not None else 0,
    }

    # 3. 评分区间
    by_score = []
    for grp, lo, hi in (("90+", 90, 999), ("80-89", 80, 90), ("70-79", 70, 80),
                        ("60-69", 60, 70), ("60-", 0, 60)):
        r = conn.execute("""
            SELECT COUNT(*) AS cnt,
                   SUM(CASE WHEN s.real_change > 0 THEN 1 ELSE 0 END) AS win_cnt,
                   AVG(s.real_change) AS avg_real
            FROM batch_stocks s JOIN batches b ON b.id = s.batch_id
            WHERE b.user_id=? AND b.batch_date BETWEEN ? AND ?
              AND s.probability >= ? AND s.probability < ?
        """, (uid, date_from, date_to, lo, hi)).fetchone()
        cnt = r["cnt"] or 0
        win = r["win_cnt"] or 0
        by_score.append({
            "range": grp, "count": cnt,
            "win_rate": round(win / cnt, 4) if cnt else 0,
            "avg_real": round(r["avg_real"], 2) if r["avg_real"] is not None else 0,
        })
    by_score = [g for g in by_score if g["count"] > 0]

    # 4. 每日趋势(最多最近60个交易日)
    daily = []
    for r in conn.execute("""
        SELECT b.batch_date AS d,
               COUNT(*) AS cnt,
               SUM(CASE WHEN s.real_change > 0 THEN 1 ELSE 0 END) AS win_cnt,
               AVG(s.real_change) AS avg_real
        FROM batch_stocks s JOIN batches b ON b.id = s.batch_id
        WHERE b.user_id=? AND b.batch_date BETWEEN ? AND ?
        GROUP BY b.batch_date ORDER BY b.batch_date DESC LIMIT 60
    """, (uid, date_from, date_to)).fetchall():
        cnt = r["cnt"] or 0
        win = r["win_cnt"] or 0
        daily.append({
            "date": r["d"], "count": cnt,
            "win_rate": round(win / cnt, 4) if cnt else 0,
            "avg_real": round(r["avg_real"], 2) if r["avg_real"] is not None else 0,
        })
    daily.reverse()   # 时间正序

    conn.close()
    return {"overview": overview, "top3": top3,
            "by_score": by_score, "daily": daily}
