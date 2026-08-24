# -*- coding: utf-8 -*-
"""
开盘啦数据路由: 竞价委买额/连板梯队/情绪值/涨停原因/板块强度
==========================================================
所有接口均走 kpl 服务(缓存 + 降级), 失败返回空列表/None, 不影响主流程。
"""
import time as _time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import kpl, sector_rotation
from .deps import get_uid, jr, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()


# ====================================================================
# Fast-path 辅助函数 (竞价时段/非竞价时段分流)
# ====================================================================

def _bj_now():
    """当前北京时间 struct_time (服务器走 UTC, 需 +8h 才是北京时间)。
    项目惯例: 北京时间 = time.gmtime(time.time() + 8*3600)"""
    import time as _t
    return _t.gmtime(_t.time() + 8 * 3600)


def _is_auction_hours():
    """当前北京时间是否在竞价时段 (9:15 ~ 9:30), 仅工作日(2026-08-24 修复: 此前用 UTC 判断致竞价时段误判)"""
    t = _bj_now()
    if t.tm_wday >= 5:   # 周六=5, 周日=6
        return False
    h, m = t.tm_hour, t.tm_min
    if h == 9 and 15 <= m <= 30:
        return True
    return False


def _is_intraday():
    """当前北京时间是否盘中 (9:30 ~ 15:00), 仅工作日。
    只有盘中现涨(change/realChange)才用实时接口刷新; 盘后/非交易一律用当日收盘固定值。
    (2026-08-24 修复: 此前用 UTC 判断, 盘中/竞价时段全被误判为非盘中)"""
    t = _bj_now()
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


def _apply_change_for(lst, serve_date):
    """统一设置列表现涨(change/realChange)口径:
       - 盘中 且 展示的就是"今天" → 东财实时(单次批量, _update_spot_change)
       - 其余全部(历史回看/回退的上交易日/盘后今日/非交易日) → 该交易日收盘涨幅(固定, 不调实时)
    注: 盘中查看历史日期时, 现涨也应是历史那天收盘涨幅, 只有"盘中看今日"才用实时。
    serve_date: 当前展示的交易日 'YYYY-MM-DD'(历史/回退=对应日, 今日=今日)。"""
    if not lst:
        return
    today = _time.strftime("%Y-%m-%d", _time.gmtime())
    if _is_intraday() and serve_date == today:
        _update_spot_change(lst)
        return
    try:
        kpl.fill_close_change_from_kline(lst, serve_date)
    except Exception as e:
        log.warning("当日收盘涨幅覆盖失败 date=%s err=%s", serve_date, e)


def _update_spot_change(lst):
    """用东方财富实时行情覆盖列表中股票的 change / realChange 字段。
    只改 change / realChange 两个字段, 其他字段(name/board/...)绝不变动。
    返回被更新的股票数量; 任何异常都返回 0, 不崩。
    (2026-08-24 修复: 此前传 ",".join(codes) 给 fetch_spot_quote_map, 但该函数
     fs 参数需市场过滤串(如 m:0+t:6,...), 传代码串致东财返回空 → 现涨永远不更新)"""
    if not lst:
        return 0
    try:
        from ..services import fetcher, scorer
        spot_map = fetcher.fetch_spot_quote_map(scorer.market_fs(["hs", "cyb", "kcb"]))
        if not spot_map:
            return 0
        n = 0
        for it in lst:
            code = it.get("code", "")
            if code and code in spot_map:
                spot = spot_map[code]
                rc = spot.get("realChange", spot.get("change"))
                if rc is not None:
                    it["change"] = float(rc) if rc else 0
                    it["realChange"] = it["change"]
                    n += 1
        return n
    except Exception:
        return 0


def _ensure_concepts(lst, tag=""):
    """保证列表中至少有 ~30% 股票带概念/板块。
    若已有概念比例 ≥30%, 直接跳过(保护深查开销);
    否则用 apply_board_concept(deep=False) 轻量模式补齐。"""
    if not lst:
        return
    total = len(lst)
    filled = sum(1 for it in lst if (it.get("board") or "").strip())
    if total > 0 and filled / total >= 0.30:
        return  # 已足够, 跳过
    try:
        kpl.apply_board_concept(lst, log_tag=tag or "auc", deep=False,
                                field="board", truncate=2, blank_if_missing=False)
    except Exception as e:
        log.warning("竞价异动概念补齐失败 tag=%s err=%s", tag, e)


def _read_auction_fast(tab):
    """非竞价时段快速读取: 优先今日落库数据; 若无则回退最近交易日; 再无返回空。
    返回 (list, date_str) — 用于竞价异动类接口 (bid-seal/bid-boom/broken)。"""
    today = _time.strftime("%Y-%m-%d", _time.gmtime())
    try:
        lst = kpl.query_auction_history(today, tab)
        if lst:
            _apply_change_for(lst, today)
            return lst, today
    except Exception:
        pass
    # 今日无数据 → 找最近交易日
    try:
        from ..db import database
        conn = database.get_conn()
        row = conn.execute(
            "SELECT MAX(date) FROM auction_daily_history WHERE date < ?",
            (today,)).fetchone()
        conn.close()
        if row and row[0]:
            d = str(row[0])
            lst = kpl.query_auction_history(d, tab)
            if lst:
                _apply_change_for(lst, d)
                return lst, d
    except Exception:
        pass
    return [], today


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
    """竞价涨停委买额: date 空=实时, 指定 'YYYY-MM-DD' 回看历史(auction_daily_history)
    2026-08-22: 竞价时段走实时 fetch_bid_seal + deep=True; 非竞价时段走 fast-path 读库"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "seal")
        kpl.fill_bid_turnover_from_snap(d, resolved)   # 2026-08-22: 历史快照竞换可能缺, 用当日快照补
        # 2026-08-24: 开盘啦 Type4 bidChange 与自采竞价涨幅不一致 → 历史竞涨以自采快照为准强制覆盖
        kpl.fill_bid_change_from_snap(d, resolved, override=True)
        kpl.fill_close_change_from_kline(d, resolved)  # 2026-08-22: 历史回看现涨=当日收盘涨跌幅
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    if not _is_auction_hours():
        # 非竞价时段 → 从库快速读取 (当天优先, 历史回退)
        d, d_str = _read_auction_fast("seal")
        kpl.fill_bid_turnover_from_snap(d, d_str)   # 2026-08-22: 非交易日/历史回退补竞换
        # 2026-08-24: 竞涨以自采快照为准强制覆盖(开盘啦 bidChange 不可靠)
        kpl.fill_bid_change_from_snap(d, d_str, override=True)
        _ensure_concepts(d, tag="auc:bid-seal[fast]")
        # 2026-08-23 口径统一: fast-path 也按 serve_date 覆盖现涨(避免回退到历史日时仍是"最新今天涨幅")
        try:
            _apply_change_for(d, d_str)
        except Exception as e:
            log.warning("bid-seal fast-path 现涨覆盖失败 err=%s", e)
        return jr({"ok": True, "list": d, "count": len(d), "date": d_str})
    d = kpl.fetch_bid_seal() or []
    # 2026-08-24 实时接口空时兜底: 竞价时段实时返空(开盘啦 Type4 偶发/未就绪) → 回退今天已落库
    if not d:
        _today = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() + 8 * 3600))
        d = kpl.query_auction_history(_today, "seal") or []
        if d:
            log.info("bid-seal 实时为空 → 回退今日落库 %d 只", len(d))
    # 概念列统一用开盘啦接口覆盖(只取开盘啦概念, 避免东财长串多概念混入)
    try:
        kpl.apply_board_concept(d, log_tag="auc:bid-seal", deep=True,
                                field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 bid-seal err=%s", e)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(d, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("bid-seal 现涨覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/bid-boom")
def api_kpl_bid_boom(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """竞价爆量/撮合>2000万: date 空=实时, 指定日期回看历史"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "boom")
        # 2026-08-23: 老版落库把大盘股 floatMv 错位为极小值 → 竞换荒谬; 用当日快照修复
        kpl.fill_bid_turnover_from_snap(d, resolved)
    else:
        d = kpl.fetch_bid_boom() or []
        try:
            kpl.apply_board_concept_db(d, log_tag="auc:bid-boom", field="board", truncate=2, blank_if_missing=True)
            # 2026-08-18 主人要求: 竞价爆量补 竞价量比(今/昨竞价额) + 昨日竞价额
            kpl.fill_bid_ratio_yest(d, None)
        except Exception as e:
            log.warning("竞价异动概念/量比补齐失败 bid-boom err=%s", e)
    # 统一过滤: 竞价涨幅 < 0.01%(含零/负涨幅) 不展示 — 同时覆盖实时与历史回看
    # (落库历史快照可能由旧版逻辑生成, 含零/负涨幅; 接口层兜底保证展示口径一致)
    if d:
        d = [it for it in d if (it.get("bidChange") if it.get("bidChange") is not None else 0) >= 0.01]
    if date:
        # 2026-08-22 历史回看: 现涨(realChange/change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            kpl.fill_close_change_from_kline(d, resolved)
        except Exception as e:
            log.warning("bid-boom 历史现涨(当日收盘)覆盖失败 err=%s", e)
        return jr({"ok": True, "list": d, "count": len(d), "date": resolved, "requestedDate": date})
    # 现涨(realChange/change)口径: 盘中=实时涨幅; 盘后/非交易日=当日收盘涨幅固定值(不调实时接口)
    try:
        _apply_change_for(d, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("bid-boom 现涨覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/bid-net")
def api_kpl_bid_net(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """竞价净额榜(2026-08-18 主人要求): 开盘啦 MorningBiddingList Type=2(全市场竞价金额>1000万)
    替代仅从涨停封单列表按净额排序; 非竞价时段返回空 → 前端回退封单列表
    2026-08-22: 增加历史回看(date) + 非交易/非竞价时段回退上一交易日(历史快照 → 快照重建)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "bid_net") or []
        # 老快照未存竞换/竞额 → 用当日 9_25 快照补
        kpl.fill_bid_turnover_from_snap(d, resolved)
        kpl.fill_bid_amt_from_snap(d, resolved)
        kpl.fill_close_change_from_kline(d, resolved)  # 2026-08-22: 历史回看现涨=当日收盘涨跌幅
        kpl.apply_board_concept_db(d, log_tag="auc:bid-net[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": d, "count": len(d), "date": resolved, "requestedDate": date})
    if not _is_auction_hours():
        # 非竞价时段(含非交易日): 优先读库, 无则用 9_25 快照重建上一交易日竞价额>1000万
        d, d_str = _read_auction_fast("bid_net")
        if not d:
            _prev = kpl._prev_trade_day()
            if _prev:
                d = kpl.bid_net_from_snap(_prev)
                d_str = _prev
        kpl.fill_bid_turnover_from_snap(d, d_str)
        kpl.fill_bid_amt_from_snap(d, d_str)
        _apply_change_for(d, d_str)   # 现涨: 盘中=实时; 盘后/非交易=当日收盘固定值(不调实时)
        kpl.apply_board_concept_db(d, log_tag="auc:bid-net[fast]", field="board", truncate=2, blank_if_missing=True, date=d_str)
        return jr({"ok": True, "list": d, "count": len(d), "date": d_str})
    d = kpl.fetch_bid_net() or []
    # 2026-08-18 主人要求: doc112(Type=2)字段结构与Type4不同, 换手/成交额解析为0
    # → 用 9_25 快照补竞价换手 + 竞价成交额(可靠同源)
    try:
        kpl.fill_bid_turnover_from_snap(d, None)
        kpl.fill_bid_amt_from_snap(d, None)
        kpl.apply_board_concept_db(d, log_tag="auc:bid-net", field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价净额换手/成交额/概念补齐失败 err=%s", e)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(d, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("bid-net 现涨覆盖失败 err=%s", e)
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
        # 2026-08-22 历史回看: 现涨(change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            kpl.fill_close_change_from_kline(lst, resolved)
        except Exception as e:
            log.warning("broken 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(lst, log_tag="auc:broken[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": lst or [], "count": len(lst),
                   "date": resolved, "requestedDate": date,
                   "day": (lst[0].get("day") if lst else "")})
    if day == "yesterday":
        # 昨炸板: 读历史快照(优先), 无则实时接口
        # 2026-08-18 修复: 应读 prev 的 broken_today(当日炸板=昨日炸板);
        # 原读 broken_yest 是"当天存的昨日炸板" → 显示上上个交易日(8/17存8/14)
        prev = kpl._prev_trade_day()
        if prev:
            lst = kpl.query_auction_history(prev, "broken_today")
            if lst:
                kpl._merge_broken_bid_snap(lst)   # 老快照无竞价字段 → 按 day 补全
                kpl.fill_float_mv_from_snap(lst, prev)
                kpl.apply_board_concept_db(lst, log_tag="auc:broken[yest]", field="board", truncate=2, blank_if_missing=True, date=prev)
                # 2026-08-22 口径统一: 历史数据现涨=当日收盘涨幅(不调实时接口)
                try:
                    kpl.fill_close_change_from_kline(lst, prev)
                except Exception as e:
                    log.warning("昨炸板当日收盘涨幅覆盖失败 err=%s", e)
                return jr({"ok": True, "list": lst, "count": len(lst), "day": prev})
    # 非竞价时段且无 day 参数: 先尝试读库 broken_today (fast-path)
    if not _is_auction_hours() and not day:
        lst_fast, d_str = _read_auction_fast("broken_today")
        if lst_fast:
            kpl._merge_broken_bid_snap(lst_fast)
            kpl.fill_float_mv_from_snap(lst_fast, d_str)
            _ensure_concepts(lst_fast, tag="auc:broken[fast]")
            # 2026-08-23: broken fast-path 也按 serve_date 覆盖现涨(避免回退历史日时显示最新今天涨幅)
            try:
                _apply_change_for(lst_fast, d_str)
            except Exception as e:
                log.warning("broken fast-path 现涨覆盖失败 err=%s", e)
            return jr({"ok": True, "list": lst_fast, "count": len(lst_fast),
                       "date": d_str,
                       "day": (lst_fast[0].get("day") if lst_fast else "")})
    d = kpl.fetch_broken_zt(day or None)
    lst = d or []
    kpl.fill_float_mv_from_snap(lst, None)
    kpl.apply_board_concept_db(lst, log_tag="auc:broken[now]", field="board", truncate=2, blank_if_missing=True)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(lst, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("broken 现涨覆盖失败 err=%s", e)
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
        kpl.apply_board_concept_db(d, log_tag="auc:hot-rank", field="board", truncate=2, blank_if_missing=True)
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
        if not lst:
            # 2026-08-22: lhb_history 可能未落库(调度中断/新增日期) → 用盘啦历史接口兜底
            try:
                hist = kpl.fetch_lhb(resolved) or []
                if hist:
                    lst = hist
            except Exception as e:
                log.warning("龙虎榜历史兜底失败 date=%s err=%s", resolved, e)
        kpl.fill_reason_from_pool(lst, resolved)
        kpl.fill_bid_change_from_snap(lst, resolved)
        kpl.fill_float_mv_from_snap(lst, resolved)
        kpl.fill_bid_turnover_from_snap(lst, resolved)
        # 2026-08-23: 历史回看现涨(change/realChange)=当日收盘涨跌幅, 而非最新今天实时
        try:
            kpl.fill_close_change_from_kline(lst, resolved)
        except Exception as e:
            log.warning("lhb 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(lst, log_tag="auc:lhb[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": lst, "count": len(lst), "date": resolved, "requestedDate": date})
    d = kpl.fetch_lhb()
    lst = d or []
    if not lst:
        # 2026-08-22 非交易日/当日无数据 → 回退上一交易日: 优先实时查盘点啦历史, 再读 lhb_history 快照
        d_str = ""
        _prev = kpl._prev_trade_day()
        if _prev:
            hist = kpl.fetch_lhb(_prev) or []
            if hist:
                lst = hist
                d_str = _prev
        if not lst:
            import json as _json
            from ..db import database
            conn = database.get_conn()
            row = conn.execute(
                "SELECT MAX(date) FROM lhb_history WHERE date < ?",
                (_time.strftime("%Y-%m-%d", _time.gmtime()),)).fetchone()
            conn.close()
            if row and row[0]:
                conn = database.get_conn()
                r2 = conn.execute("SELECT list FROM lhb_history WHERE date=?", (str(row[0]),)).fetchone()
                conn.close()
                if r2 and r2[0]:
                    try:
                        lst = _json.loads(r2[0])
                    except (ValueError, TypeError):
                        lst = []
                if lst:
                    d_str = str(row[0])
        kpl.fill_reason_from_pool(lst, d_str or None)
        kpl.fill_bid_change_from_snap(lst, d_str)
        kpl.fill_float_mv_from_snap(lst, d_str)
        kpl.fill_bid_turnover_from_snap(lst, d_str)   # 2026-08-18: 补竞价换手
        # 2026-08-23: 回退到历史日时 现涨=当日收盘涨幅(禁止显示今日最新)
        try:
            _apply_change_for(lst, d_str)
        except Exception as e:
            log.warning("lhb fallback 现涨覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(lst, log_tag="auc:lhb[fallback]", field="board", truncate=2, blank_if_missing=True, date=d_str)
        return jr({"ok": True, "list": lst, "count": len(lst), "date": d_str})
    kpl.fill_reason_from_pool(lst, None)   # 今日涨停池补涨停原因
    kpl.fill_bid_change_from_snap(lst, None)   # 今日 9_25 快照补竞价涨幅
    kpl.fill_float_mv_from_snap(lst, None)
    kpl.fill_bid_turnover_from_snap(lst, None)   # 2026-08-18: 补竞价换手
    kpl.apply_board_concept_db(lst, log_tag="auc:lhb[now]", field="board", truncate=2, blank_if_missing=True)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(lst, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("lhb 现涨覆盖失败 err=%s", e)
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
    date 空=实时; 指定 'YYYY-MM-DD' 回看历史(qc_snapshot + snapshot_bid)
    2026-08-22: 非竞价时段用 _ensure_concepts 轻量补概念; 指定 date 用 deep=True"""
    d = kpl.fetch_bid_qiangcang(date or None) or {}
    l20 = d.get("list20") or []
    l20Chg = d.get("list20Chg") or []
    lLast = d.get("listLast") or []
    lists_to_concept = [l20, l20Chg, lLast]
    # 指定 date → 走 deep=True 深查; 非竞价时段 → _ensure_concepts 轻量补
    if date or _is_auction_hours():
        try:
            for lst, tag in zip(lists_to_concept, ["auc:qc20", "auc:qc20Chg", "auc:qcLast"]):
                kpl.apply_board_concept(lst, log_tag=tag, deep=True,
                                        field="board", truncate=2, blank_if_missing=True)
        except Exception as e:
            log.warning("竞价异动概念开盘啦覆盖失败 bid-qiangcang err=%s", e)
    else:
        # 非竞价时段: 先 db 快速合并 + _ensure_concepts 轻量补
        try:
            for lst, tag in zip(lists_to_concept, ["auc:qc20", "auc:qc20Chg", "auc:qcLast"]):
                kpl.apply_board_concept_db(lst, log_tag=tag, field="board", truncate=2, blank_if_missing=True)
                _ensure_concepts(lst, tag=tag)
        except Exception as e:
            log.warning("竞价异动概念补失败 bid-qiangcang[fast] err=%s", e)
    # 2026-08-18 修复: 抢筹三表统一 merge 东财实时涨幅(realChange) —
    # 主人反馈灿勤科技等涨幅抢筹/右表股票实时涨幅为空(开盘啦数据源无该字段)
    # 2026-08-23: 历史回看时**禁止** merge 实时涨幅(否则历史日显示最新涨幅), 改为覆盖当日收盘涨跌幅
    # 统一现涨(realChange/change)口径:
    # - 盘中且展示"今天" → 东财实时(单次批量, _apply_change_for / _update_spot_change)
    # - 其余全部(历史回看 / 回退到上交易日 / 盘后今日 / 非交易日) → 该交易日收盘涨幅(固定, 不调实时)
    # serve_date 推导: 指定 date → date(可能是快照内部 date 或 resolved); 否则今天
    today_str = _time.strftime("%Y-%m-%d", _time.gmtime())
    serve_date = date or (d.get("date") if isinstance(d, dict) else "") or today_str
    # 三表各自独立填充现涨
    for ql, tag in ((l20, "qc20"), (l20Chg, "qc20Chg"), (lLast, "qcLast")):
        try:
            _apply_change_for(ql, serve_date)
        except Exception as e:
            log.warning("抢筹现涨覆盖失败 tag=%s date=%s err=%s", tag, serve_date, e)
    # 2026-08-18 主人要求: 抢筹右表/涨幅抢筹补竞价换手(快照 bid_amt/float_mv 计算)
    try:
        kpl.fill_bid_turnover_from_snap(l20Chg, serve_date if serve_date != today_str or not _is_auction_hours() else None)
        kpl.fill_bid_turnover_from_snap(lLast, serve_date if serve_date != today_str or not _is_auction_hours() else None)
    except Exception as e:
        log.warning("抢筹竞价换手补齐失败 err=%s", e)
    return jr({"ok": True, "list20": l20, "list20Chg": l20Chg, "listLast": lLast,
               "count20": len(l20), "count20Chg": len(l20Chg), "countLast": len(lLast),
               "date": d.get("date") or serve_date or ""})


@router.get("/api/kpl/yest-zt")
def api_kpl_yest_zt(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """昨日涨停股今日竞价表现: date 空=实时, 指定日期回看历史(auction_daily_history yest_zt)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "yest_zt")
        kpl.fill_reason_from_pool(d, resolved)
        kpl.fill_float_mv_from_snap(d, resolved)
        # 2026-08-23: 历史回看现涨(change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            _apply_change_for(d, resolved)
        except Exception as e:
            log.warning("yest-zt 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(d, log_tag="auc:yest-zt[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_yest_zt() or []
    try:
        kpl.apply_board_concept_db(d, log_tag="auc:yest-zt", field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 yest-zt err=%s", e)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(d, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("yest-zt 现涨覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/yest-broken")
def api_kpl_yest_broken(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """昨断板: 昨日涨停池中今日未涨停的股票; date 空=实时, 指定日期回看历史"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "yest_broken")
        kpl.fill_reason_from_pool(d, resolved)
        kpl.fill_float_mv_from_snap(d, resolved)
        # 2026-08-23: 历史回看现涨(change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            _apply_change_for(d, resolved)
        except Exception as e:
            log.warning("yest-broken 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(d, log_tag="auc:yest-broken[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_yest_broken() or []
    try:
        kpl.apply_board_concept_db(d, log_tag="auc:yest-broken", field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 yest-broken err=%s", e)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(d, _time.strftime("%Y-%m-%d", _time.gmtime()))
    except Exception as e:
        log.warning("yest-broken 现涨覆盖失败 err=%s", e)
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


# ==================== 异动监管(doc90/doc108/doc109) ====================
@router.get("/api/kpl/yidong-realtime")
def api_kpl_yidong_realtime(request: Request, uid: int = Depends(require_vip_or_paid)):
    """异动实时接口 (开盘啦 doc90 GetPianLiZhi_Index): 返回全市场实时异动个股列表
    返回: {ok, list, manyNum, day, time}
    list 每项: code, name, type(第4字段index3), trigger(第8字段index7), triggered(末字段index12),
              change(当日涨幅, index4), days(统计天数, index5), deviation(累计偏离值, index6), target(触发阈值, index8)
    原始字段结构: [0]code [1]name [2]typeCode [3]typeDesc [4]change [5]days [6]deviation
                  [7]triggerDesc [8]triggerPct ... [12]triggered"""
    d = kpl.fetch_kpl_doc90() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        code = str(item[0]) if len(item) > 0 else ""
        name = str(item[1]) if len(item) > 1 else ""
        # 异动类型: 第4个字段(index 3)
        yd_type = str(item[3]) if len(item) > 3 else ""
        # 涨幅触发异动: 第8个字段(index 7)
        trigger = str(item[7]) if len(item) > 7 else ""
        # 是否触发异动: 最后一个字段(index 12)
        triggered = str(item[12]) if len(item) > 12 else ""
        # 偏离值字段(2026-08-19 8f5a8c2): 当日涨幅/统计天数/累计偏离值/触发阈值
        try:
            change = float(item[4]) if len(item) > 4 else None
        except (ValueError, TypeError):
            change = None
        try:
            days = int(item[5]) if len(item) > 5 else 0
        except (ValueError, TypeError):
            days = 0
        try:
            deviation = float(item[6]) if len(item) > 6 else None
        except (ValueError, TypeError):
            deviation = None
        try:
            target = float(item[8]) if len(item) > 8 else None
        except (ValueError, TypeError):
            target = None
        lst.append({"code": code, "name": name, "type": yd_type,
                     "trigger": trigger, "triggered": triggered,
                     "change": change, "days": days,
                     "deviation": deviation, "target": target})
    return jr({"ok": True, "list": lst, "count": len(lst),
               "manyNum": d.get("Many_Num", 0), "day": d.get("Day", ""),
               "time": d.get("Time", 0)})


@router.get("/api/kpl/yidong-monitor")
def api_kpl_yidong_monitor(request: Request, uid: int = Depends(require_vip_or_paid)):
    """重点监控股票 (开盘啦 doc108 GetYDTP_ZDJK_Today): 当日重点监控个股
    返回: {ok, list, count}  list 每项格式: [code, name, startDate, endDate, times]"""
    d = kpl.fetch_kpl_doc108() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        code = str(item[0]) if len(item) > 0 else ""
        name = str(item[1]) if len(item) > 1 else ""
        start = str(item[2]) if len(item) > 2 else ""
        end = str(item[3]) if len(item) > 3 else ""
        times = item[4] if len(item) > 4 else 0
        lst.append({"code": code, "name": name, "startDate": start, "endDate": end, "times": times})
    return jr({"ok": True, "list": lst, "count": len(lst)})


@router.get("/api/kpl/yidong-multi")
def api_kpl_yidong_multi(request: Request, uid: int = Depends(require_vip_or_paid)):
    """多次异动个股 (开盘啦 doc109 GetPianLiZhi_Many): 近10日内多次异动个股
    返回: {ok, list, count, day}  list 每项格式: [code, name, times, desc]"""
    d = kpl.fetch_kpl_doc109() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        code = str(item[0]) if len(item) > 0 else ""
        name = str(item[1]) if len(item) > 1 else ""
        times = item[2] if len(item) > 2 else 0
        desc = str(item[3]) if len(item) > 3 else ""
        lst.append({"code": code, "name": name, "times": times, "desc": desc})
    return jr({"ok": True, "list": lst, "count": len(lst), "day": d.get("Day", "")})


@router.get("/api/kpl/yidong-hot")
def api_kpl_yidong_hot(request: Request, uid: int = Depends(require_vip_or_paid)):
    """热门股偏离值 (开盘啦 GetPianLiZhi_Hot): 热门度严重异常个股列表
    返回: {ok, list, count, day, time}
    list 每项: code, name, type(偏离类型), change(今日涨跌%), deviation(偏离值),
              flag(连板/标签), before(异动前涨幅), base(偏离基准), concept(概念), days(偏离天数), rule(偏离规则)
    原始字段结构: [0]code [1]name [2]type [3]change [4]deviation [5]flag\n                [6]before [7]base [8]concept [9]0 [10]days [11]rule"""
    d = kpl.fetch_kpl_pianli_hot() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        def g(i): return item[i] if len(item) > i else ""
        lst.append({
            "code": str(g(0)), "name": str(g(1)), "type": str(g(2)),
            "change": g(3), "deviation": g(4), "flag": str(g(5)),
            "before": g(6), "base": g(7), "concept": str(g(8)),
            "days": str(g(10)), "rule": str(g(11)),
        })
    return jr({"ok": True, "list": lst, "count": len(lst),
               "day": d.get("Day", ""), "time": d.get("Time", 0)})


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
