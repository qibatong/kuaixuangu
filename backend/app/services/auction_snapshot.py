# -*- coding: utf-8 -*-
"""
竞价多时点快照归档服务: 9:15 / 9:20 / 9:25 全市场快照每日自动采集
================================================================
- 工作日 9:15 / 9:20 / 9:25 三个时点, 后台线程自动抓取全市场行情 → snapshot_bid 表
- 每日积累 → 形成"历史多时点回放库"(短线侠式核心壁垒)
- 9:25 lock 时读取 9:20 快照, 计算涨幅加速度
"""
import concurrent.futures
import json
import threading
import time

from ..core import logger
from ..db import database
from . import fetcher, scorer

log = logger.get_logger(__name__)

# 时点 -> (开始分钟, 结束分钟) 北京时间(每 10 秒轮询, 窗口 1 分钟防漏)
# 9_24 用于"最后一秒抢筹"兜底: 调度器在 9:24:30 后每轮询重采覆盖, 最后一份≈9:24:5x
# 真正秒级用 snapshot_lastsec(9:24:55-9:25:03 每秒高频采样 + 差值回退, 见 _lastsec_loop)
TIME_POINTS = {
    "9_15": (9 * 60 + 15, 9 * 60 + 16),
    "9_20": (9 * 60 + 20, 9 * 60 + 21),
    "9_24": (9 * 60 + 24, 9 * 60 + 25),
    "9_25": (9 * 60 + 25, 9 * 60 + 26),
}
DEFAULT_POINT = "9_20"     # 加速度计算使用的时点

# 最后一秒高频采样窗口: (9:24:55) ~ (9:25:03), 每秒一次(ts 记实际时刻)
LASTSEC_START = 9 * 3600 + 24 * 60 + 55
LASTSEC_END = 9 * 3600 + 25 * 60 + 3

_sched_lock = threading.Lock()
_fetch_lock = threading.Lock()       # 东财拉取串行化(时点快照 vs 秒级采样 双线程防并发限流)
_sched_done = set()        # {(date, time_point)} 已抓取, 防重复
_sched_checked = set()     # {date} 已做采集盘点(9:31 后一次)
_lastsec_done = set()      # {(date, ts秒)} 该秒已采, 防重复


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _fetch_market_map(full=False):
    """抓取全市场快照, 返回 {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv}}
    过滤异常涨幅(±30% 外, A股涨跌停上限20%/新股44%, 非交易时段字段可能异常)
    full=True : fetch_eastmoney_all 分页全市场(~5500只, 按代码f12排序, 时点快照用)
    full=False: fetch_eastmoney 单页200只×3分区(按涨幅倒序=竞价最强前600, 秒级采样用,
                9:24:55-9:25:03 仅8秒窗口, 分页全市场需60s+, 无法每秒完成)
    并发: 沪深创科 3 分区 ThreadPoolExecutor 并发拉取(全市场分页内部仍串行防限流),
          单页模式耗时 3×~1.5s → ~1.5s, 秒级采样 8 秒窗口可采 5-8 个点
    """
    with _fetch_lock:
        raw_all = {}

        def _grab(m):
            try:
                if full:
                    return m, fetcher.fetch_eastmoney_all(scorer.market_fs([m]))
                return m, fetcher.fetch_eastmoney(scorer.market_fs([m]))
            except Exception as e:
                log.warning("快照拉取失败 market=%s full=%s err=%s", m, full, e)
                return m, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            for m, raw in ex.map(_grab, ("hs", "cyb", "kcb")):
                if not raw:
                    continue
                for s in raw:
                    code = s.get("f12")
                    if not code:
                        continue
                    bc = scorer.get_bid_change(s)
                    if bc < -30 or bc > 30:    # 明显异常数据(非交易时段字段污染)
                        continue
                    raw_all[code] = {
                        "bid_change": bc,
                        "bid_amt": scorer.get_bid_amt(s),
                        "name": str(s.get("f14") or ""),          # 名称
                        # 竞价封单额(元) = f10 买一量(手) × f5 买一价 × 100(股/手)
                        # 涨停时买一委托即封单; 非涨停时=买一委托金额(竞价强弱参考)
                        "bid_buy_amt": scorer.parse_float(s.get("f10")) * scorer.parse_float(s.get("f5")) * 100,
                        "float_mv": scorer.parse_float(s.get("f21")),             # 流通市值(元) - f21 才是流通市值, f6 是成交额!
                        "board": str(s.get("f103") or s.get("f100") or ""),       # 概念(f103优先, 行业f100兜底)
                    }
        return raw_all


def snapshot_at(time_point):
    """抓取并归档某时点全市场快照, 返回入库数量; 失败返回 0
    时点快照用全市场分页(fetch_eastmoney_all ~5500只), 非单页600只"""
    if time_point not in TIME_POINTS:
        return 0
    date = _bj_date()
    raw_all = _fetch_market_map(full=True)
    if not raw_all:
        log.warning("快照拉取为空 time=%s date=%s (东财全市场接口无返回, 该时点数据缺失!)",
                    time_point, date)
        return 0
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, board, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(date, time_point, code, v["bid_change"], v["bid_amt"], v.get("name", ""),
              v.get("bid_buy_amt", 0), v.get("float_mv", 0), v.get("board", ""), int(time.time()))
             for code, v in raw_all.items()])
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("快照落库失败 time=%s err=%s", time_point, e)
        return 0
    log.info("快照已存 date=%s time=%s 数量%d", date, time_point, len(raw_all))
    return len(raw_all)


def snapshot_lastsec_at(ts_sec):
    """最后一秒高频采样: 抓取当前全市场快照存入 snapshot_lastsec(ts=实际时刻秒)
    返回入库数量; 失败返回 0(该秒跳过, 序列仍可用)
    秒级采样用单页600只(8秒窗口限制, 分页全市场需60s+), 覆盖竞价最强前600"""
    date = _bj_date()
    raw_all = _fetch_market_map(full=False)
    if not raw_all:
        log.warning("最后一秒采样为空 ts=%d (东财接口无返回, 该秒跳过)", ts_sec)
        return 0
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_lastsec (date, code, bid_change, bid_amt, ts) "
            "VALUES (?,?,?,?,?)",
            [(date, code, v["bid_change"], v["bid_amt"], ts_sec)
             for code, v in raw_all.items()])
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("最后一秒采样落库失败 ts=%d err=%s", ts_sec, e)
        return 0
    log.info("最后一秒采样已存 date=%s ts=%d 数量%d", date, ts_sec, len(raw_all))
    return len(raw_all)


def _lastsec_loop():
    """最后一秒高频采样线程: 9:24:55-9:25:03 窗口内每秒尝试一次(接口耗时>1s时自动降频),
    ts 记录实际采集时刻 → 序列用于"差值回退"计算最后一秒抢筹"""
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            ts_total = g.tm_hour * 3600 + g.tm_min * 60 + g.tm_sec
            if g.tm_wday < 5 and LASTSEC_START <= ts_total <= LASTSEC_END:
                # 秒级去重: 同一秒只采一次(接口耗时>1s时自然降频, 不会并发堆积)
                sec_key = (date, ts_total)
                if sec_key not in _lastsec_done:
                    if snapshot_lastsec_at(ts_total):
                        _lastsec_done.add(sec_key)
            time.sleep(1)
        except Exception as e:
            log.error("最后一秒采样调度异常 err=%s", e)
            time.sleep(1)


def load_snapshot(date=None, time_point=DEFAULT_POINT):
    """读取某日某时点快照, 返回 {code: {bid_change, bid_amt}}; 无数据返回 {}"""
    date = date or _bj_date()
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, time_point)).fetchall()
        conn.close()
    except Exception:
        return {}
    return {r[0]: {"bid_change": r[1], "bid_amt": r[2]} for r in rows}


def query_snapshot(date, time_point, limit=50):
    """历史回放: 某日某时点全市场快照(按竞价涨幅降序, 带名称)"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt, name, ts FROM snapshot_bid "
            "WHERE date=? AND time_point=? ORDER BY bid_change DESC LIMIT ?",
            (date, time_point, min(limit, 500))).fetchall()
        conn.close()
    except Exception:
        return []
    return [{"code": r[0], "bid_change": r[1], "bid_amt": r[2], "name": r[3] or ""} for r in rows]


# 个股三时点对比展示的时点(9:15/9:20/9:25)
STOCK_POINTS = ["9_15", "9_20", "9_25"]


def query_stock_snapshot(date, code):
    """个股三时点封单对比: 某日该股 9:15/9:20/9:25 各时点快照(bid_change/bid_amt/委买额/市值)
    返回 {name, points: {9_15: {...}, ...}}, 缺时点为 None"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT time_point, code, bid_change, bid_amt, bid_buy_amt, float_mv, name FROM snapshot_bid "
            "WHERE date=? AND code=?", (date, code)).fetchall()
        conn.close()
    except Exception:
        return {"name": "", "points": {}}
    name = ""
    points = {}
    for tp, c, bc, amt, buy, mv, nm in rows:
        if tp in STOCK_POINTS:
            if not name and nm:
                name = nm
            points[tp] = {
                "code": c,
                "bid_change": bc,
                "bid_amt": amt,
                "bid_buy_amt": buy,
                "float_mv": mv,
            }
    return {"name": name, "points": points}


def _is_zt(code, bid_change):
    """竞价涨停判断(按板块涨停幅度, 涨幅达到阈值视为涨停):
    创业/科创(30/68) 20%, 北交所(8/4) 30%, 主板 10%"""
    if bid_change is None:
        return False
    if code[:2] in ("30", "68"):
        return bid_change >= 19.9
    if code[:1] in ("8", "4"):
        return bid_change >= 29.9
    return bid_change >= 9.9


# 榜单分层: 1=9:25 涨停(封死) / 2=9:20 涨停(9:25 回落) / 3=仅 9:15 涨停(9:20/9:25 回落)
LAYER_TAGS = {1: "9:25封死", 2: "9:20封板回落", 3: "9:15封板回落"}


def query_3points_board(date, limit=100):
    """三时点封单榜: 全市场按三层原则排序(短线侠式)
    1. 9:25 涨停 → 按 9:25 封单额排序(无封单降级竞价额)
    2. 9:25 未涨停但 9:20 涨停 → 按 9:20 封单额排序
    3. 9:25/9:20 均未涨停但 9:15 涨停 → 按 9:15 封单额排序
    返回 [{code, name, layer, tag, sort_amt, points:{9_15..}}]"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, board FROM snapshot_bid "
            "WHERE date=? AND time_point IN ('9_15','9_20','9_25')", (date,)).fetchall()
        conn.close()
    except Exception:
        return []
    agg = {}
    for tp, code, bc, amt, name, buy, mv, board in rows:
        d = agg.setdefault(code, {"code": code, "name": name or "", "board": board or "", "points": {}})
        if name:
            d["name"] = name
        if board:
            d["board"] = board
        d["points"][tp] = {"bid_change": bc, "bid_amt": amt, "bid_buy_amt": buy, "float_mv": mv}
    out = []
    for code, d in agg.items():
        p15, p20, p25 = d["points"].get("9_15"), d["points"].get("9_20"), d["points"].get("9_25")
        layer = None
        sort_amt = 0.0
        if p25 and _is_zt(code, p25["bid_change"]):
            layer = 1
            sort_amt = (p25["bid_buy_amt"] or 0) or (p25["bid_amt"] or 0)
        elif p20 and _is_zt(code, p20["bid_change"]):
            layer = 2
            sort_amt = (p20["bid_buy_amt"] or 0) or (p20["bid_amt"] or 0)
        elif p15 and _is_zt(code, p15["bid_change"]):
            layer = 3
            sort_amt = (p15["bid_buy_amt"] or 0) or (p15["bid_amt"] or 0)
        if layer is None:
            continue
        out.append({"code": code, "name": d["name"], "layer": layer,
                    "tag": LAYER_TAGS[layer], "sort_amt": round(sort_amt, 2),
                    "board": d["board"], "points": d["points"]})
    # 分层优先(小→大), 层内按封单额降序
    out.sort(key=lambda x: (x["layer"], -x["sort_amt"]))
    return out[: min(limit, 300)]


def _has_snapshot(date, time_point):
    """某日某时点是否已有快照数据(9_24 重采型时点用, 不依赖 _sched_done 标记)"""
    try:
        conn = database.get_conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, time_point)).fetchone()[0]
        conn.close()
        return n > 0
    except Exception:
        return False


def _scheduler_loop():
    """后台调度: 工作日按时点窗口抓取一次, 每 10 秒轮询; 9:31 后盘点当日采集情况"""
    _qc_done = set()   # {(date)} 抢筹结果快照已抓取(9:29-9:30 窗口)
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            hm = g.tm_hour * 60 + g.tm_min
            for tp, (start, end) in TIME_POINTS.items():
                key = (date, tp)
                if g.tm_wday < 5 and start <= hm <= end and key not in _sched_done:
                    if tp == "9_24":
                        # 最后一秒专用时点: 9:24:00-9:25:00 窗口内每轮询重采覆盖,
                        # 最后一份快照 ≈ 9:24:5x(真正"最后一秒", 而非整分钟差)
                        # 注意: 此处判断必须 < 9*60+25(窗口内), 写 9*60+30(9:30) 会导致永不采集!
                        if hm < 9 * 60 + 25:
                            snapshot_at(tp)
                    else:
                        with _sched_lock:
                            if key not in _sched_done:
                                if snapshot_at(tp):
                                    _sched_done.add(key)
            # 9:29-9:30 抢筹结果快照: 触发 fetch_bid_qiangcang 落库(竞价结束前最后一份,
            # 非竞价时段页面读库展示不丢失)
            if (g.tm_wday < 5 and 9 * 60 + 29 <= hm <= 9 * 60 + 30
                    and date not in _qc_done):
                try:
                    from . import kpl
                    kpl._cache.clear()
                    d = kpl.fetch_bid_qiangcang()
                    n = len((d or {}).get("list20", []))
                    log.info("竞价抢筹结果快照已存 date=%s list20=%d只", date, n)
                    _qc_done.add(date)
                except Exception as e:
                    log.warning("竞价抢筹结果快照失败 err=%s", e, exc_info=True)
            # 15:30-15:35 板块轮动日终快照: 抓当日板块强度 Top10 落库(多数据源), 形成轮动数据基础
            if g.tm_wday < 5 and 15 * 60 + 30 <= hm <= 15 * 60 + 35:
                try:
                    from . import sector_rotation
                    for src in ("kpl", "em", "ths"):
                        if ("sector_" + src + "_" + date) not in _sched_done:
                            sector_rotation.record_today_top(source=src)
                            _sched_done.add("sector_" + src + "_" + date)
                    # 人气热榜历史快照(三源): 供人气榜回看历史
                    from . import hot_rank
                    for src in ("kpl", "em", "ths"):
                        if ("hot_" + src + "_" + date) not in _sched_done:
                            hot_rank.save_hot_rank_history(date, source=src)
                            _sched_done.add("hot_" + src + "_" + date)
                    # 龙虎榜当日快照: 供龙虎榜回看历史(接口支持 Time 参数, 但落库保证数据在)
                    if ("lhb_" + date) not in _sched_done:
                        from . import kpl
                        lst = kpl.fetch_lhb(date)
                        if lst:
                            from ..db import database
                            conn = database.get_conn()
                            conn.execute(
                                "INSERT OR REPLACE INTO lhb_history (date, list, ts) VALUES (?,?,?)",
                                (date, json.dumps(lst, ensure_ascii=False), int(time.time())))
                            conn.commit()
                            conn.close()
                            _sched_done.add("lhb_" + date)
                    # 连板梯队当日快照: 供连板天梯回看历史(接口不支持历史日期, 必须落库)
                    if ("ladder_" + date) not in _sched_done:
                        from . import kpl as _kpl
                        n = _kpl.save_ladder_history(date)
                        if n:
                            _sched_done.add("ladder_" + date)
                            log.info("连板梯队快照已存 date=%s 共%d只", date, n)
                    # 竞价异动日终快照(全部 tab): 供竞价异动页按日期回看历史
                    # (竞价委买/爆量/昨日涨停/昨断板/炸板 接口不支持历史日期, 必须落库)
                    if ("auction_" + date) not in _sched_done:
                        from . import kpl as _kpl2
                        n2 = _kpl2.save_auction_history(date)
                        if n2:
                            _sched_done.add("auction_" + date)
                            log.info("竞价异动日终快照已存 date=%s 共%d个tab", date, n2)
                except Exception as e:
                    log.warning("板块轮动日终快照失败 err=%s", e, exc_info=True)
            # 9:31-9:35 盘点当日采集: 缺失时点告警(排查关键, 数据过了点无法补)
            if g.tm_wday < 5 and 9 * 60 + 31 <= hm <= 9 * 60 + 35 and date not in _sched_checked:
                missing = []
                for tp in TIME_POINTS:
                    if (date, tp) in _sched_done:
                        continue
                    if tp == "9_24":
                        # 9_24 是重采型时点(不标记 _sched_done), 用数据存在性判断
                        if not _has_snapshot(date, tp):
                            missing.append(tp)
                    else:
                        missing.append(tp)
                if missing:
                    log.warning("今日快照采集缺失时点: %s (date=%s), 相关功能(加速度/回放)会缺数据",
                                ",".join(missing), date)
                else:
                    log.info("今日快照采集完整: %s (date=%s)", ",".join(TIME_POINTS), date)
                _sched_checked.add(date)
        except Exception as e:
            log.error("快照调度异常 err=%s", e)
        time.sleep(10)


def start_scheduler():
    """main.py startup 调用: 启动后台抓取线程(单 worker 下唯一实例)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=_lastsec_loop, daemon=True)
    t2.start()
    log.info("竞价多时点快照调度已启动(9:15/9:20/9:24/9:25/9:29抢筹结果快照/9:24:55-9:25:03最后一秒高频采样)")
