# -*- coding: utf-8 -*-
"""
战绩分析路由
"""
from fastapi import APIRouter, Depends, Request

from ..services import scorer, stats
from .deps import get_uid, jr, qs

router = APIRouter()


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
