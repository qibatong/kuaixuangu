# -*- coding: utf-8 -*-
"""
战绩分析路由
"""
import time as _time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import auction_snapshot, kpl, scorer, stats
from ..db import database
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


def _is_intraday_stats():
    """当前北京时间是否盘中 (9:30 ~ 15:00), 仅工作日(三时点封单表现涨口径判断用)"""
    t = _time.gmtime()
    if t.tm_wday >= 5:
        return False
    h, m = t.tm_hour, t.tm_min
    if h < 9 or h > 15:
        return False
    if h == 9 and m < 30:
        return False
    if h == 15 and m > 0:
        return False
    return True


def _apply_change_stats(lst, serve_date):
    """三时点封单榜/快照 现涨(real_change/realChange/change)口径统一:
    - 盘中 且 serve_date == 今天 → 东财实时 merge; 实时缺失/异常时降级为当日收盘涨幅兜底, 不允许现涨为空
    - 其余 → 该交易日收盘涨跌幅(从 close_change_history + 多源K线兜底)"""
    if not lst:
        return
    today = _time.strftime("%Y-%m-%d", _time.gmtime())
    if _is_intraday_stats() and serve_date == today:
        try:
            from app.services import fetcher
            spot = fetcher.fetch_spot_quote_map("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23")
            n = 0
            for it in lst:
                q = spot.get(str(it.get("code")))
                if q and q.get("realChange") is not None:
                    rc = q.get("realChange")
                    if it.get("real_change") is not None or "real_change" in (it.keys() if hasattr(it, "keys") else {}):
                        it["real_change"] = rc
                    if it.get("realChange") is not None or "realChange" in (it.keys() if hasattr(it, "keys") else {}):
                        it["realChange"] = rc
                    if it.get("change") is not None or "change" in (it.keys() if hasattr(it, "keys") else {}):
                        it["change"] = rc
                    n += 1
            log.info("三时点/快照 现涨(实时)覆盖 %d 只 date=%s", n, serve_date)
            # 实时缺失的股票降级: 用 close_change_history + 多源日K兜底, 避免现涨为空
            # (2026-08-24: 东财全市场接口偶发失败 → spot 为空 → 此前直接 return 导致现涨全空)
            missing = [it for it in lst if it.get("code") and it.get("real_change") is None
                       and it.get("realChange") is None and it.get("change") is None]
            if missing:
                try:
                    _fill_close_change_fallback(missing, serve_date)
                    if missing:
                        log.info("三时点/快照 现涨实时缺失降级补齐 %d 只 date=%s",
                                 sum(1 for it in missing if it.get("real_change") is not None), serve_date)
                except Exception as e2:
                    log.warning("三时点/快照 实时缺失降级补齐失败 date=%s err=%s", serve_date, e2)
            return
        except Exception as e:
            log.warning("三时点/快照 实时现涨覆盖失败, 转当日收盘涨幅兜底 err=%s", e)
            # 实时接口异常 → 走当日收盘涨幅兜底(不允许现涨为空)
    # 历史回看/盘后今日/非交易日 → 当日收盘涨跌幅(字段可能是 real_change 或 realChange/change)
    _fill_close_change_fallback(lst, serve_date)


def _fill_close_change_fallback(lst, serve_date):
    """把列表中股票的现涨用 serve_date 当日收盘涨跌幅兜底填充(close_change_history + 多源K线)。
    兼容字段: real_change / realChange / change, 兜底后统一回填 real_change(三时点表展示 key)。
    返回成功填充数; 失败返回 0 不抛异常。"""
    if not lst or not serve_date:
        return 0
    # 先复制字段到 realChange/change 让 fill_close_change_from_kline 能按既定 key 覆盖
    for it in lst:
        if "realChange" not in it and "real_change" in it:
            it["realChange"] = it["real_change"]
        if "change" not in it and "real_change" in it:
            it["change"] = it["real_change"]
    try:
        kpl.fill_close_change_from_kline(lst, serve_date)
    except Exception as e:
        log.warning("三时点/快照 现涨(当日收盘)覆盖失败 err=%s", e)
    # 把 realChange/change 同步回 real_change (三时点表展示 key)
    n = 0
    for it in lst:
        rc = it.get("realChange") if it.get("realChange") is not None else it.get("change")
        if rc is not None:
            it["real_change"] = rc
            n += 1
    return n


@router.get("/api/stats/auction-overview")
def api_stats_auction_overview(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """竞价多时点对比: date 空=最近4个交易日; 指定 'YYYY-MM-DD' 回看该日(自动对齐最近交易日)
    每日期 9:15/9:20/9:25 竞价涨幅均值/竞价额 + 一字涨停数
    金额单位统一为元(bid_amt 原始单位为万元, 聚合时转元)"""
    conn = database.get_conn()
    try:
        if date:
            # 对齐到最近交易日(与市场雷达一致: 周末/节假日回退)
            try:
                row = conn.execute(
                    "SELECT MAX(date) FROM snapshot_bid WHERE date <= ?", (date,)).fetchone()
                resolved = str(row[0]) if row and row[0] else date
            except Exception:
                resolved = date
            has = conn.execute(
                "SELECT COUNT(*) FROM snapshot_bid WHERE date=?", (resolved,)).fetchone()[0]
            dates = [resolved] if has else []
        else:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM snapshot_bid ORDER BY date DESC LIMIT 4")]
        out = []
        for d in dates:
            day = {"date": d, "points": {}, "yizi_count": None, "yizi_amt": None}
            for tp in ("9_15", "9_20", "9_25"):
                rows = conn.execute(
                    "SELECT bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point=?",
                    (d, tp)).fetchall()
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
                "SELECT yizi_count, bid_amt FROM daily_yizi WHERE date=?", (d,)).fetchone()
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
        # snapshot_bid.name 优先(新采集+回填), 历史缺失时从竞价委买榜兜底
        if not it.get("name"):
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


@router.get("/api/stats/bid-snapshot-stock")
def api_stats_bid_snapshot_stock(request: Request, uid: int = Depends(get_uid)):
    """个股三时点封单对比: ?date=YYYY-MM-DD&code=600000
    返回该股 9:15/9:20/9:25 三个时点的快照(bid_change/bid_amt/委买额/流通市值),
    一个视图直接看封单变化(无需分别点开各时点)"""
    q = qs(request)
    date = (q.get("date") or [""])[0]
    code = (q.get("code") or [""])[0].strip()
    if not date or not code:
        return jr({"ok": False, "msg": "date 与 code 必填"}, 400)
    # 周末/节假日自动对齐最近交易日(与多时点对比一致)
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE date <= ?", (date,)).fetchone()
        resolved = str(row[0]) if row and row[0] else date
    except Exception:
        resolved = date
    finally:
        conn.close()
    d = auction_snapshot.query_stock_snapshot(resolved, code)
    return jr({"ok": True, "date": resolved, "code": code, "name": d["name"], "points": d["points"]})


@router.get("/api/stats/bid-snapshot-3points")
def api_stats_bid_snapshot_3points(request: Request, uid: int = Depends(get_uid)):
    """三时点封单榜(全市场): ?date=YYYY-MM-DD&limit=100
    三层排序: ①9:25涨停(按9:25竞价额) ②9:20涨停9:25回落(按9:20) ③仅9:15涨停(按9:15)"""
    q = qs(request)
    date = (q.get("date") or [""])[0]
    if not date:
        return jr({"ok": False, "msg": "date 必填"}, 400)
    try:
        limit = min(300, max(10, int((q.get("limit") or [100])[0])))
    except (TypeError, ValueError):
        limit = 100
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE date <= ?", (date,)).fetchone()
        resolved = str(row[0]) if row and row[0] else date
    except Exception:
        resolved = date
    finally:
        conn.close()
    if resolved != date:
        log.info("三时点榜 date=%s 请求无当日快照, 回退显示 %s (说明: 当日 9:15/9:20/9:25 未采集或非交易日)",
                 date, resolved)
    else:
        log.info("三时点榜 date=%s 命中当日数据", date)
    rows = auction_snapshot.query_3points_board(resolved, limit)
    # 2026-08-18 主人要求: 三时点榜加竞价换手 = 9_25竞价成交额/流通市值×100(与开盘啦口径一致)
    for it in rows:
        try:
            p25 = (it.get("points") or {}).get("9_25") or {}
            amt = p25.get("bid_amt") or 0
            fmv = p25.get("float_mv") or 0
            if amt > 0 and fmv > 0:
                # 注意单位: points 的 bid_amt 万元, float_mv 元 → amt×10000 转元
                it["bidTurnover"] = round(amt * 10000 / fmv * 100, 2)
        except Exception:
            pass
    # 2026-08-23 现涨(real_change)口径统一:
    # - 盘中今日 → 东财实时 merge
    # - 历史回看 / 回退 prev / 盘后今日 / 非交易日 → 该交易日收盘涨跌幅(close_change_history 优先, 再多源K线兜底)
    #   (之前无条件 merge 今日实时, 导致历史日也显示最新今天涨幅 → 主人反馈 三时点封单表历史日期不正确)
    _apply_change_stats(rows, resolved)
    # 概念列: snapshot_bid.board 已是采集时开盘啦 overlay(无实时查询);
    # 只做"取前 2 个"归一, 不再逐股实时打开盘啦(概念由 concept_refresh 定时落库)
    try:
        for it in rows:
            b = it.get("board") or ""
            if b:
                parts = [p for p in str(b).split("、") if p]
                it["board"] = "、".join(parts[:2])
    except Exception as e:
        log.warning("三时点榜概念归一失败 err=%s", e)
    return jr({"ok": True, "date": resolved, "count": len(rows), "list": rows})


@router.get("/api/stats/seal-quality")
def api_stats_seal_quality(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """封单数据质量报表: ?date=YYYY-MM-DD(空=最近交易日)
    返回该日各时点: 非涨停股挂封单数/涨停股缺封单数/封单流通比异常数
    开发自查用: 采集后 curl 即可确认数据是否健康, 不需要人肉逐只核对"""
    q = qs(request)
    date = (q.get("date") or [""])[0]
    conn = database.get_conn()
    try:
        if not date:
            row = conn.execute("SELECT MAX(date) FROM snapshot_bid").fetchone()
            date = str(row[0]) if row and row[0] else ""
    except Exception:
        pass
    finally:
        conn.close()
    if not date:
        return jr({"ok": False, "msg": "无快照数据"}, 404)
    out = {"date": date, "points": {}}
    for tp in ("9_15", "9_20", "9_24", "9_25"):
        r = auction_snapshot.check_seal_quality(date, tp, force=True)
        if r is not None:
            out["points"][tp] = r
    return jr({"ok": True, "report": out,
               "hint": "非涨停股挂封单数应为0; 涨停股缺封单数应远小于涨停数"})
