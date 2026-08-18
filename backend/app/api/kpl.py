# -*- coding: utf-8 -*-
"""
开盘啦数据路由: 竞价委买额/连板梯队/情绪值/涨停原因/板块强度
==========================================================
所有接口均走 kpl 服务(缓存 + 降级), 失败返回空列表/None, 不影响主流程。
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import kpl, sector_rotation
from .deps import get_uid, jr, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()


def _resolve_date(date):
    """把用户选的日期对齐到最近交易日(返回对齐后的 'YYYY-MM-DD')
    原理: 周末/节假日/未开盘日没有落库数据, 查 daily_sector_top 中
    <= 所选日期的最大日期即为最近交易日 —— 无需任何节假日日历, 天然准确
    无任何历史时返回原日期"""
    if not date:
        return ""
    try:
        from ..db import database
        conn = database.get_conn()
        row = conn.execute(
            "SELECT MAX(date) FROM daily_sector_top WHERE date <= ?", (date,)).fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return date


@router.get("/api/kpl/sentiment")
def api_kpl_sentiment(request: Request, uid: int = Depends(get_uid)):
    """市场情绪: 涨停家数/情绪指标/连板高度/大幅回撤"""
    d = kpl.fetch_sentiment()
    return jr({"ok": True, "sentiment": d})


@router.get("/api/kpl/market-brief")
def api_kpl_market_brief(request: Request, uid: int = Depends(get_uid)):
    """市场概览(2026-08-16):
    - breadth: 涨跌家数分布(今日最新 + 昨日同时刻, xuangubao 开盘啦生态)
    - market: 两市股票总数 + 成交额(东财全市场, 5min 缓存)
    - last_same_time: 上一交易日同一时点的成交额 + 股票数(2026-08-16 新增,
      由 worker 每 5 分钟滚动存 market_brief_intraday_{date}, 次日起可对比)
    - last: 上一交易日收盘全天快照(settings market_brief_last, 15:30 存)
    前端据此展示 '两市总量 + 较昨日同时' 与 '涨跌家数分布'"""
    from ..db import database
    from ..services import fetcher
    import time as _time
    breadth = kpl.fetch_market_breadth()
    market = fetcher.fetch_market_brief()
    # 昨日同一时点(优先; 24h 内积累的分时快照)
    last_same_time = fetcher.get_same_time_yesterday()
    # 昨日全天(15:30 收盘快照, 兜底)
    last = None
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key='market_brief_last'").fetchone()
        conn.close()
        if row and row[0]:
            import json
            last = json.loads(row[0])
    except Exception:
        last = None
    return jr({"ok": True,
               "breadth": breadth,
               "market": market,
               "last_same_time": last_same_time,
               "last": last,
               "ts": int(_time.time())})


@router.get("/api/kpl/bid-seal")
def api_kpl_bid_seal(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """竞价涨停委买额: date 空=实时, 指定 'YYYY-MM-DD' 回看历史(auction_daily_history)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "seal")
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_bid_seal() or []
    # 概念列统一用开盘啦接口覆盖(只取开盘啦概念, 避免东财长串多概念混入)
    try:
        kpl.apply_board_concept(d, log_tag="auc:bid-seal", deep=False, field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 bid-seal err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/bid-boom")
def api_kpl_bid_boom(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """竞价爆量/撮合>2000万: date 空=实时, 指定日期回看历史"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "boom")
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_bid_boom() or []
    try:
        kpl.apply_board_concept(d, log_tag="auc:bid-boom", deep=False, field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 bid-boom err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/broken")
def api_kpl_broken(request: Request, day: str = "", date: str = "",
                   uid: int = Depends(require_vip_or_paid)):
    """炸板(东财 flash, 无需Token): 默认今日; day=yesterday 上一交易日; day=YYYY-MM-DD 指定日;
    date 参数统一回看历史(优先 date, 读 auction_daily_history broken_yest/broken_today)"""
    if date:
        resolved = _resolve_date(date)
        lst = kpl.query_auction_history(resolved, "broken_today")
        kpl._merge_broken_bid_snap(lst)   # 老快照无竞价字段 → 按 day 补全
        kpl.fill_float_mv_from_snap(lst, resolved)
        kpl.apply_board_concept(lst, log_tag="auc:broken[hist]", deep=True, field="board", truncate=2, blank_if_missing=True)
        return jr({"ok": True, "list": lst or [], "count": len(lst),
                   "date": resolved, "requestedDate": date,
                   "day": (lst[0].get("day") if lst else "")})
    if day == "yesterday":
        # 昨炸板: 读历史快照(优先), 无则实时接口
        prev = kpl._prev_trade_day()
        if prev:
            lst = kpl.query_auction_history(prev, "broken_yest")
            if lst:
                kpl._merge_broken_bid_snap(lst)   # 老快照无竞价字段 → 按 day 补全
                kpl.fill_float_mv_from_snap(lst, prev)
                kpl.apply_board_concept(lst, log_tag="auc:broken[yest]", deep=True, field="board", truncate=2, blank_if_missing=True)
                return jr({"ok": True, "list": lst, "count": len(lst), "day": prev})
    d = kpl.fetch_broken_zt(day or None)
    lst = d or []
    kpl.fill_float_mv_from_snap(lst, None)
    kpl.apply_board_concept(lst, log_tag="auc:broken[now]", deep=True, field="board", truncate=2, blank_if_missing=True)
    return jr({"ok": True, "list": lst, "count": len(lst),
               "day": (lst[0].get("day") if lst else "")})


@router.get("/api/kpl/ladder")
def api_kpl_ladder(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """连板梯队; date 空=实时(首板~五板+), 指定 'YYYY-MM-DD' 回看历史(ladder_history 快照)
    周末/节假日自动对齐到最近交易日"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_ladder_history(resolved)
        return jr({"ok": True, "ladder": d, "date": resolved, "requestedDate": date})
    d = kpl.fetch_ladder_all()
    # 2026-08-18 修复: 开盘啦 DailyLimitPerformance 无涨幅字段 → 东财全市场实时行情 merge
    # (前端"实时涨幅"列读 change/realChange, 之前梯队页涨幅全空)
    try:
        from ..services import fetcher
        spot = fetcher.fetch_spot_quote_map("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23")
        n = 0
        for pid in d:
            for it in (d[pid] or []):
                q = spot.get(str(it.get("code")))
                if q and q.get("realChange") is not None:
                    it["change"] = q.get("realChange")
                    it["realChange"] = q.get("realChange")
                    n += 1
        log.info("ladder 实时涨幅 merge 完成 覆盖%d只", n)
    except Exception as e:
        log.warning("ladder 实时涨幅 merge 失败 err=%s", e)
    return jr({"ok": True, "ladder": d, "date": ""})


@router.get("/api/kpl/board-rank")
def api_kpl_board_rank(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """板块强度排行; date 空=实时, 指定 'YYYY-MM-DD' 查历史(开盘啦 doc42 保留最近 5 交易日)
    周末/节假日自动对齐到最近交易日(resolvedDate)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.fetch_board_rank_by_date(resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_board_rank()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0, "date": ""})


@router.get("/api/kpl/board-stocks")
def api_kpl_board_stocks(request: Request, uid: int = Depends(get_uid), code: str = "",
                         date: str = ""):
    """板块成分股(2026-08-17 主人需求): 板块强度点开看成分股
    code=板块代码(board-rank 的 boardCode, 如 801001); date 空=实时, 指定回看历史"""
    if not code:
        return jr({"ok": False, "msg": "缺少板块代码 code"}, 400)
    if date:
        resolved = _resolve_date(date)
        d = kpl.fetch_board_stocks(code, resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_board_stocks(code)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0, "date": ""})


@router.get("/api/kpl/hot-rank")
def api_kpl_hot_rank(request: Request, uid: int = Depends(get_uid), source: str = "kpl", date: str = ""):
    """人气热榜; source: kpl/em/ths; date 空=实时, 指定日期回看历史(hot_rank_history)
    周末/节假日自动对齐到最近交易日"""
    from ..services import hot_rank
    source = (source or "kpl").lower()
    if source not in ("kpl", "em", "ths"):
        source = "kpl"
    if date:
        resolved = _resolve_date(date)
        d = hot_rank.query_hot_rank_history(resolved, source)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "source": source, "date": resolved, "requestedDate": date})
    d = hot_rank.fetch_hot_rank(source)
    # 2026-08-18 修复: 热点榜补开盘啦概念(此前 board/concept 全空)
    try:
        kpl.apply_board_concept(d, log_tag="auc:hot-rank", deep=False, field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("hot-rank 概念覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
               "source": source, "date": ""})


@router.get("/api/kpl/lhb")
def api_kpl_lhb(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """龙虎榜上榜股票; date 空=当天实时, 指定 'YYYY-MM-DD' 回看历史(lhb_history 快照)
    周末/节假日自动对齐到最近交易日"""
    if date:
        resolved = _resolve_date(date)
        import json as _json
        from ..db import database
        conn = database.get_conn()
        row = conn.execute("SELECT list FROM lhb_history WHERE date=?", (resolved,)).fetchone()
        conn.close()
        lst = []
        if row and row[0]:
            try:
                lst = _json.loads(row[0])
            except (ValueError, TypeError):
                lst = []
        kpl.fill_reason_from_pool(lst, resolved)
        kpl.fill_bid_change_from_snap(lst, resolved)
        kpl.fill_float_mv_from_snap(lst, resolved)
        kpl.apply_board_concept(lst, log_tag="auc:lhb[hist]", deep=True, field="board", truncate=2, blank_if_missing=True)
        return jr({"ok": True, "list": lst, "count": len(lst), "date": resolved, "requestedDate": date})
    d = kpl.fetch_lhb()
    lst = d or []
    kpl.fill_reason_from_pool(lst, None)   # 今日涨停池补涨停原因
    kpl.fill_bid_change_from_snap(lst, None)   # 今日 9_25 快照补竞价涨幅
    kpl.fill_float_mv_from_snap(lst, None)
    kpl.apply_board_concept(lst, log_tag="auc:lhb[now]", deep=True, field="board", truncate=2, blank_if_missing=True)
    return jr({"ok": True, "list": lst, "count": len(lst), "date": ""})


@router.get("/api/kpl/lhb-detail")
def api_kpl_lhb_detail(request: Request, code: str = "", date: str = "", uid: int = Depends(require_vip_or_paid)):
    """龙虎榜个股营业部明细(买入/卖出营业部)"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code"}, 400)
    d = kpl.fetch_lhb_detail(code, date)
    return jr({"ok": True, "detail": d})


@router.get("/api/kpl/zt-reason")
def api_kpl_zt_reason(request: Request, code: str = "", uid: int = Depends(require_vip_or_paid)):
    """个股涨停原因(当天/历史)"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code"}, 400)
    d = kpl.fetch_zt_reason(code)
    return jr({"ok": True, "reason": d or []})


@router.get("/api/kpl/wpqc")
def api_kpl_wpqc(request: Request, uid: int = Depends(require_vip_or_paid)):
    """尾盘竞价抢筹(14:57 后)"""
    d = kpl.fetch_wpqc()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/bid-qiangcang")
def api_kpl_bid_qiangcang(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """竞价抢筹(左右双表): list20=9:20→9:25 竞额抢筹(开盘啦净额强度),
    list20Chg=9:20→9:25 涨幅抢筹(全市场快照涨幅差), listLast=9:24→9:25 最后1秒段
    date 空=实时; 指定 'YYYY-MM-DD' 回看历史(qc_snapshot + snapshot_bid)"""
    d = kpl.fetch_bid_qiangcang(date or None) or {}
    l20 = d.get("list20") or []
    l20Chg = d.get("list20Chg") or []
    lLast = d.get("listLast") or []
    # 概念列统一用开盘啦接口覆盖(涨幅抢筹 list20Chg 可能回退东财快照, 强制开盘啦)
    try:
        kpl.apply_board_concept(l20, log_tag="auc:qc20", deep=False, field="board", truncate=2, blank_if_missing=True)
        kpl.apply_board_concept(l20Chg, log_tag="auc:qc20Chg", deep=True, field="board", truncate=2, blank_if_missing=True)
        kpl.apply_board_concept(lLast, log_tag="auc:qcLast", deep=False, field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 bid-qiangcang err=%s", e)
    return jr({"ok": True, "list20": l20, "list20Chg": l20Chg, "listLast": lLast,
               "count20": len(l20), "count20Chg": len(l20Chg), "countLast": len(lLast),
               "date": d.get("date") or date or ""})


@router.get("/api/kpl/yest-zt")
def api_kpl_yest_zt(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """昨日涨停股今日竞价表现: date 空=实时, 指定日期回看历史(auction_daily_history yest_zt)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "yest_zt")
        kpl.fill_reason_from_pool(d, resolved)
        kpl.fill_float_mv_from_snap(d, resolved)
        kpl.apply_board_concept(d, log_tag="auc:yest-zt[hist]", deep=True, field="board", truncate=2, blank_if_missing=True)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_yest_zt() or []
    try:
        kpl.apply_board_concept(d, log_tag="auc:yest-zt", deep=True, field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 yest-zt err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/yest-broken")
def api_kpl_yest_broken(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """昨断板: 昨日涨停池中今日未涨停的股票; date 空=实时, 指定日期回看历史"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "yest_broken")
        kpl.fill_reason_from_pool(d, resolved)
        kpl.fill_float_mv_from_snap(d, resolved)
        kpl.apply_board_concept(d, log_tag="auc:yest-broken[hist]", deep=True, field="board", truncate=2, blank_if_missing=True)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_yest_broken() or []
    try:
        kpl.apply_board_concept(d, log_tag="auc:yest-broken", deep=True, field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 yest-broken err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/yesterday-perf")
def api_kpl_yesterday_perf(request: Request, uid: int = Depends(get_uid)):
    """昨日涨停/连板/破板今日平均表现(策略验证)"""
    d = kpl.fetch_yesterday_perf()
    return jr({"ok": True, "perf": d})


# ==================== xuangubao 免费接口(无需 Token) ====================
@router.get("/api/kpl/zt-pool")
def api_kpl_zt_pool(request: Request, uid: int = Depends(get_uid), day: str = ""):
    """涨停池: day 可选(YYYY-MM-DD 历史)"""
    d = kpl.fetch_zt_pool(day or None)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/dt-pool")
def api_kpl_dt_pool(request: Request, uid: int = Depends(get_uid), day: str = ""):
    """跌停池: day 可选"""
    d = kpl.fetch_dt_pool(day or None)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/yest-zt-pool")
def api_kpl_yest_zt_pool(request: Request, uid: int = Depends(get_uid), day: str = ""):
    """昨日涨停池: day 可选"""
    d = kpl.fetch_yest_zt_pool(day or None)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/market-line")
def api_kpl_market_line(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """市场曲线全家桶: 涨跌家数/涨停跌停数/炸板率/昨涨停今表现/市场温度"""
    return jr({
        "ok": True,
        "updown": kpl.fetch_updown_line(date or None),
        "zt_dt": kpl.fetch_zt_dt_line(date or None),
        "broken": kpl.fetch_broken_line(date or None),
        "yest_perf": kpl.fetch_yest_zt_perf_line(date or None),
        "temperature": kpl.fetch_market_temp_line(date or None),
    })


@router.get("/api/kpl/hot-stocks")
def api_kpl_hot_stocks(request: Request, uid: int = Depends(get_uid)):
    """热点解读-强势股(涨停原因/题材/封单时间)"""
    d = kpl.fetch_hot_stocks()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/hot-plates")
def api_kpl_hot_plates(request: Request, uid: int = Depends(get_uid)):
    """板块名称与对应题材"""
    d = kpl.fetch_hot_plates()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/live-room")
def api_kpl_live_room(request: Request, uid: int = Depends(get_uid)):
    """涨停直播"""
    d = kpl.fetch_live_room()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/dadan-net")
def api_kpl_dadan_net(request: Request, uid: int = Depends(get_uid), code: str = ""):
    """指定个股大单净额分时"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code 参数"})
    d = kpl.fetch_dadan_net(code)
    return jr({"ok": True, **d})


@router.get("/api/kpl/sector-rotation")
def api_kpl_sector_rotation(request: Request, uid: int = Depends(get_uid), days: int = 10, source: str = "kpl"):
    """板块轮动历史: 返回最近 N 个交易日的板块强度 Top10(表格+趋势) + 多窗口排名(近10/20/30/50日)
    source: 数据源 kpl(开盘啦)/ em(东方财富), 默认 kpl"""
    days = max(1, min(int(days or 10), 60))
    source = (source or "kpl").lower()
    if source not in ("kpl", "em", "ths"):
        source = "kpl"
    rot = sector_rotation.query_rotation(days, source)
    win = sector_rotation.query_window_ranking((10, 20, 30, 50), source=source)
    return jr({"ok": True, "rotation": rot, "windows": win,
               "dates": rot.get("dates") or [], "source": source})


@router.get("/api/kpl/interfaces")
def api_kpl_interfaces(request: Request, uid: int = Depends(get_uid)):
    """已封装开盘啦接口索引(开发调试用):
    返回 kpl 模块所有 fetch_* 函数的:
    name / 第一行 docstring(功能描述) / 是否被其他代码调用
    相关文档: docs/kpl-interfaces.md (自动生成脚本 scripts/kpl_interface_index.py)"""
    import ast
    import inspect
    import os
    # 统计整个 backend 目录的调用点(跨模块, 如 stocks.py 里 kpl.fetch_board_map())
    def _called_count(name):
        cnt = 0
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", "venv", ".venv", "tests")]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                try:
                    t2 = ast.parse(open(os.path.join(root, fn), encoding="utf-8").read())
                except Exception:
                    continue
                for node in ast.walk(t2):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    if (isinstance(f, ast.Name) and f.id == name) or \
                       (isinstance(f, ast.Attribute) and f.attr == name):
                        cnt += 1
        return cnt
    out = []
    for name, fn in vars(kpl).items():
        if not name.startswith("fetch_"):
            continue
        if not inspect.isfunction(fn):
            continue
        doc = (inspect.getdoc(fn) or "").strip()
        title = doc.splitlines()[0] if doc else ""
        called = _called_count(name) > 0
        # doc 编号
        m = name[len("fetch_kpl_doc"):] if name.startswith("fetch_kpl_doc") else ""
        doc_no = int(m) if m.isdigit() else None
        out.append({"name": name, "doc_no": doc_no, "title": title, "called": called})
    out.sort(key=lambda x: (0 if x["doc_no"] is None else 1, x["doc_no"] or 0, x["name"]))
    return jr({"ok": True, "count": len(out), "interfaces": out,
               "hint": "可用 scripts/kpl_interface_index.py 生成 docs/kpl-interfaces.md 文档"})
