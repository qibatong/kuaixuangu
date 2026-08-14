# -*- coding: utf-8 -*-
"""
板块轮动历史服务: 每日保存板块强度 Top10, 提供历史轮动查询(表格+趋势线+量能柱)
========================================================================
- 工作日 15:30 后由 scheduler 调 record_today_top() 落库 daily_sector_top
- query_rotation(days) 返回最近 N 个交易日的数据
"""
import json
import time

from ..core import logger
from ..db import database
from . import kpl

log = logger.get_logger(__name__)


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def record_today_top(top_n=10):
    """抓取当日板块强度 TopN 落库 daily_sector_top(覆盖式)
    返回入库条数; 抓取失败返回 0"""
    date = _bj_date()
    try:
        boards = kpl.fetch_board_rank() or []
    except Exception as e:
        log.warning("板块轮动抓取失败 date=%s err=%s", date, e)
        return 0
    if not boards:
        log.warning("板块轮动抓取为空 date=%s", date)
        return 0
    boards = boards[:top_n]
    # 转 amount/amount 单位为元(开盘啦已是元), 落库时保留原始精度
    payload = []
    for i, b in enumerate(boards, start=1):
        payload.append({
            "rank": i,
            "boardCode": b.get("boardCode") or "",
            "name": b.get("name") or "",
            "strength": float(b.get("strength") or 0),
            "change": float(b.get("change") or 0),
            "amount": float(b.get("amount") or 0),
            "mainNet": float(b.get("mainNet") or 0),
            "volRatio": float(b.get("volRatio") or 0),
            "floatMv": float(b.get("floatMv") or 0),
        })
    try:
        conn = database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO daily_sector_top (date, boards, ts) VALUES (?,?,?)",
            (date, json.dumps(payload, ensure_ascii=False), int(time.time())))
        conn.commit()
        conn.close()
        log.info("板块轮动已存 date=%s 数量%d", date, len(payload))
        return len(payload)
    except Exception as e:
        log.error("板块轮动落库失败 err=%s", e)
        return 0


def query_rotation(days=10):
    """返回最近 N 个交易日(按日期降序)的板块 Top10 数据
    返回 {dates:[...], days: [{date, boards:[...]}], window_avg: {boardName: avg_strength}}"""
    days = max(1, min(int(days or 10), 60))
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date, boards FROM daily_sector_top ORDER BY date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    out = []
    for d, raw in rows:
        try:
            boards = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            boards = []
        out.append({"date": d, "boards": boards})
    dates = [x["date"] for x in reversed(out)]   # 升序(便于按日期从左到右展示)
    # 多窗口排名均值: 用于前端"近10/20/30/50日排名变化趋势"叠加显示
    # 这里直接返回 rows, 前端按需聚合
    return {"dates": dates, "days": out}


def query_window_ranking(days_list=(10, 20, 30, 50), top_k=5):
    """返回多个时间窗口(近 N 日)的板块平均强度 Top K
    用于底部多窗口排名折线图
    返回 {windows: [{window, top: [{name, avgStrength}]}], common_names: [...]}
    """
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date, boards FROM daily_sector_top ORDER BY date DESC LIMIT ?",
        (max(days_list),)
    ).fetchall()
    conn.close()
    result = {"windows": [], "common_names": []}
    name_pool = set()
    for win in days_list:
        win_rows = rows[:win]
        if not win_rows:
            result["windows"].append({"window": win, "top": []})
            continue
        agg = {}     # name -> [strength, count]
        for _date, raw in win_rows:
            try:
                boards = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                continue
            for b in boards:
                name = b.get("name") or ""
                if not name:
                    continue
                # 排名加权: rank 1=10 分, rank 2=9 分... rank 10=1 分; 加权强度更高 = 更强
                weight = (top_k + 1 - b.get("rank", 99)) if b.get("rank", 99) <= top_k else 0
                if weight <= 0:
                    continue
                if name not in agg:
                    agg[name] = [0.0, 0]
                agg[name][0] += float(b.get("strength") or 0) * weight
                agg[name][1] += weight
        top = []
        for name, (s_sum, w_sum) in agg.items():
            avg = s_sum / w_sum if w_sum else 0
            top.append({"name": name, "avgStrength": round(avg, 2)})
            name_pool.add(name)
        top.sort(key=lambda x: x["avgStrength"], reverse=True)
        top = top[:top_k]
        result["windows"].append({"window": win, "top": top})
    result["common_names"] = sorted(name_pool)
    return result