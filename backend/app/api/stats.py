# -*- coding: utf-8 -*-
"""
战绩分析路由
"""
from fastapi import APIRouter, Depends, Request

from ..services import auction_snapshot, kpl, scorer, stats
from ..db import database
from .deps import get_uid, jr, qs

router = APIRouter()


@router.get("/api/stats/auction-overview")
def api_stats_auction_overview(request: Request, uid: int = Depends(get_uid)):
    """竞价多时点对比(最近4个交易日): 每日期 9:15/9:20/9:25 竞价涨幅均值/竞价额 + 一字涨停数
    金额单位统一为元(bid_amt 原始单位为万元, 聚合时转元)"""
    conn = database.get_conn()
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM snapshot_bid ORDER BY date DESC LIMIT 4")]
        out = []
        for date in dates:
            day = {"date": date, "points": {}, "yizi_count": None, "yizi_amt": None}
            for tp in ("9_15", "9_20", "9_25"):
                rows = conn.execute(
                    "SELECT bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point=?",
                    (date, tp)).fetchall()
                if rows:
                    chgs = [r[0] for r in rows if r[0] is not None]
                    amts = [r[1] for r in rows if r[1] is not None]
                    day["points"][tp] = {
                        "avg_change": round(sum(chgs) / len(chgs), 2) if chgs else None,
                        "total_amt": round(sum(amts) * 10000) if amts else None,  # 万元→元
                        "count": len(rows),
                    }
                else:
                    day["points"][tp] = None
            yizi = conn.execute(
                "SELECT yizi_count, bid_amt FROM daily_yizi WHERE date=?", (date,)).fetchone()
            if yizi:
                day["yizi_count"] = yizi[0]
                day["yizi_amt"] = (yizi[1] * 10000) if yizi[1] is not None else None  # 万元→元
            out.append(day)
    finally:
        conn.close()
    return jr({"ok": True, "days": out})


@router.get("/api/stats/auction-snapshot")
def api_stats_auction_snapshot(request: Request, date: str = "", time_point: str = "9_25",
                               uid: int = Depends(get_uid)):
    """某日某时点竞价快照个股列表(按竞价涨幅降序, 名称从竞价委买榜尽力补全)"""
    if not date:
        return jr({"ok": False, "msg": "缺少 date"}, 400)
    if time_point not in ("9_15", "9_20", "9_25"):
        return jr({"ok": False, "msg": "time_point 需为 9_15/9_20/9_25"}, 400)
    lst = auction_snapshot.query_snapshot(date, time_point, limit=100)
    try:
        name_map = {s["code"]: s["name"] for s in (kpl.fetch_bid_seal() or [])}
    except Exception:
        name_map = {}
    for it in lst:
        it["name"] = name_map.get(it["code"], "")
    return jr({"ok": True, "list": lst, "date": date, "time_point": time_point})


@router.get("/api/stats/performance")
def api_stats_performance(request: Request, uid: int = Depends(get_uid)):
    """战绩统计: 胜率/评分有效性/每日趋势 (按用户隔离)"""
    q = qs(request)
    date_from = scorer._q_date((q.get("date_from") or [None])[0], "2000-01-01")
    date_to = scorer._q_date((q.get("date_to") or [None])[0], "2100-12-31")
    result = stats.compute_performance(uid, date_from, date_to)
    return jr({"ok": True, "range": {"from": date_from, "to": date_to}, **result})


@router.get("/api/stats/daily-yizi")
def api_stats_daily_yizi(request: Request, uid: int = Depends(get_uid)):
    """每日一字涨停统计: 近 N 日 一字数量 + 竞价总额(市场公共数据, 登录即可看)"""
    q = qs(request)
    try:
        days = min(30, max(1, int((q.get("days") or [10])[0])))
    except (TypeError, ValueError):
        days = 10
    return jr({"ok": True, "list": stats.daily_yizi_trend(days)})


@router.get("/api/stats/bid-snapshot")
def api_stats_bid_snapshot(request: Request, uid: int = Depends(get_uid)):
    """历史竞价多时点回放: ?date=YYYY-MM-DD&time_point=9_15|9_20|9_25&limit=N
    返回当日该时点全市场快照(按竞价涨幅降序)"""
    from ..services import auction_snapshot
    q = qs(request)
    date = (q.get("date") or [""])[0]
    tp = (q.get("time_point") or ["9_25"])[0]
    if tp not in auction_snapshot.TIME_POINTS:
        return jr({"ok": False, "msg": "time_point 应为 9_15/9_20/9_25"}, 400)
    try:
        limit = min(500, max(1, int((q.get("limit") or [50])[0])))
    except (TypeError, ValueError):
        limit = 50
    rows = auction_snapshot.query_snapshot(date, tp, limit) if date else []
    return jr({"ok": True, "date": date, "time_point": tp, "count": len(rows), "list": rows})
