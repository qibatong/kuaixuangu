# -*- coding: utf-8 -*-
"""
流通市值日频缓存 (P2-2): 把「静态基础数据」从竞价采集里剥离出来
=================================================================================
问题(9/11 熔断日实证):
    float_mv / free_mv / name / board 是**静态基础数据**(一天内几乎不变), 但每次
    竞价采集只能从当时的行情源里碰运气拿:
      * 东财给 f21(流通市值) / f117(自由流通) —— 东财一熔断, 全市场市值一起没;
      * TickPlus fullbid **完全不给市值与名称** —— 补进来的票 float_mv=0,
        会被 floatMvFloor 门槛直接踢掉(补了等于白补);
      * 开盘啦兜底只有 100~200 只, 且只在东财空时才走。

解决: 建日频缓存表 stock_float_mv_daily, 三级取值 ——
      ① 当日行情源(东财 f21/f117 优先, 最准)
      ② 本表最近一日缓存(≤15 天, 市值日内漂移 <1%)
      ③ 腾讯 qt.gtimg.cn f44(流通市值, 单位**亿**) 批量补 —— 只在①②都缺时才发请求
    → 东财正常日: 0 次腾讯请求(东财 f21 覆盖率 ~100%)
    → 东财熔断日: 只补缺失部分, 让 TickPlus 补进来的 5000+ 只票也有市值可评分

口径铁律(与 P0-3 同源):
    缓存表数值列**可空**。拿不到就写 NULL, 绝不写 0 —— 0 是"实测值为 0"(会被
    市值门槛当小盘股误判), NULL 是"不知道"。
"""
import concurrent.futures as _cf
import datetime
import time

from ..core import logger
from ..db import database
from . import fetcher, settings

log = logger.get_logger(__name__)

TABLE = "stock_float_mv_daily"
ENABLE_SWITCH = "mv_cache_enabled"

MAX_BACK_DAYS = 15      # 缓存回退天数: 超过半个月市值可能因股本变动失真
MAX_FETCH = 4000        # 单次腾讯补的最大只数(熔断日兜底上限, 防打爆腾讯)
BATCH = 300             # 腾讯每批只数(实测 600 只 190ms, 300 稳妥防超长 URL)
WORKERS = 5             # 腾讯并发批数(与 fetch_tencent_market 一致)
TIMEOUT = 15


def enabled():
    """是否启用缓存补值(默认开; 关闭则只写不补, 退化到 P2 之前的行为)"""
    return bool(settings.get(ENABLE_SWITCH, 1))


def save(date, rows):
    """写当日缓存: rows = {code: {name, float_mv, free_mv, board, src}}
    INSERT OR REPLACE(SQLite 3.7 不支持 ON CONFLICT), 覆盖同日旧值。返回写入行数。"""
    if not rows:
        return 0
    now = int(time.time())
    data = []
    for code, v in rows.items():
        if not code:
            continue
        data.append((date, code, (v.get("name") or "") or None,
                     v.get("float_mv"), v.get("free_mv"),
                     (v.get("board") or "") or None,
                     v.get("src") or "", now))
    if not data:
        return 0
    conn = None
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO %s (date, code, name, float_mv, free_mv, board, src, ts) "
            "VALUES (?,?,?,?,?,?,?,?)" % TABLE, data)
        conn.commit()
        return len(data)
    except Exception as e:                                     # noqa: BLE001
        log.warning("[市值缓存] 写入失败 date=%s n=%d err=%s", date, len(data), e)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                  # noqa: BLE001
                pass


def lookup(codes, date=None, max_back_days=MAX_BACK_DAYS):
    """读缓存: 返回 {code: {name, float_mv, free_mv, board, src, date}}
    每个 code 取 **date 之前(含)最近一日** 的非空市值行; 超出回退窗口视为无。"""
    out = {}
    codes = [c for c in (codes or []) if c]
    if not codes:
        return out
    end = date or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    try:
        d = datetime.date(*[int(x) for x in end.split("-")])
        start = (d - datetime.timedelta(days=max_back_days)).isoformat()
    except Exception:                                          # noqa: BLE001
        start = "0000-00-00"
    conn = None
    try:
        conn = database.get_conn()
        for i in range(0, len(codes), 500):        # SQLite 参数上限
            part = list(codes[i:i + 500])
            ph = ",".join("?" * len(part))
            cur = conn.execute(
                "SELECT code, date, name, float_mv, free_mv, board, src FROM %s "
                "WHERE code IN (%s) AND date BETWEEN ? AND ? "
                "ORDER BY date DESC" % (TABLE, ph), tuple(part) + (start, end))
            for code, dte, name, mv, free, board, src in cur.fetchall():
                if code in out:          # 已取到更近的一日
                    continue
                if not mv and not free and not name:
                    continue
                out[code] = {"name": name or "", "float_mv": mv, "free_mv": free,
                             "board": board or "", "src": src or "", "date": dte}
        return out
    except Exception as e:                                     # noqa: BLE001
        log.warning("[市值缓存] 读取失败 n=%d err=%s", len(codes), e)
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                  # noqa: BLE001
                pass


def fetch_tencent(codes, timeout=TIMEOUT):
    """腾讯 f44 批量取流通市值: 返回 {code: {name, float_mv(float, 元), src}}
    f44 单位=亿 → ×1e8 得元(与东财 f21 同单位)。任何单批失败只丢该批, 不抛。"""
    out = {}
    codes = [c for c in (codes or []) if c]
    if not codes:
        return out
    symbols = [fetcher._tencent_symbol(c) for c in codes]
    batches = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]

    def _grab(b):
        try:
            return fetcher._fetch_tencent_batch(b)
        except Exception:                                      # noqa: BLE001
            return {}

    try:
        with _cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for got in ex.map(_grab, batches):
                for code, f in (got or {}).items():
                    try:
                        mv = float(f[44]) * 1e8     # 亿 → 元
                    except (ValueError, IndexError, TypeError):
                        continue
                    if mv <= 0:
                        continue
                    out[code] = {"name": (f[1] if len(f) > 1 else "") or "",
                                 "float_mv": mv, "src": "tencent"}
    except Exception as e:                                     # noqa: BLE001
        log.warning("[市值缓存] 腾讯补值异常 n=%d err=%s", len(codes), e)
    return out


def fill(raw_all, date=None):
    """给采集结果补市值/名称(原地改 raw_all), 并写回缓存表。返回统计 dict。

    三级: ①已有值不动 ②缓存表(≤15天) ③腾讯 f44(仅当开关开且缺失数 ≤ MAX_FETCH)
    raw_all 元素与 auction_snapshot._fetch_market_map 同构:
        {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv, free_mv, board}}
    """
    st = {"need": 0, "from_cache": 0, "from_tencent": 0, "miss": 0, "saved": 0}
    if not raw_all:
        return st
    date = date or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    need = [c for c, v in raw_all.items() if not (v.get("float_mv") or 0)]
    st["need"] = len(need)
    got = {}          # 腾讯补到的 {code: {...}}; 下面 src 标记要用, 必须先定义

    # ② 缓存
    if need:
        cache = lookup(need, date=date)
        for code, c in cache.items():
            v = raw_all.get(code)
            if v is None:
                continue
            if not (v.get("float_mv") or 0) and (c.get("float_mv") or 0):
                v["float_mv"] = c["float_mv"]
                st["from_cache"] += 1
            if not (v.get("free_mv") or 0) and (c.get("free_mv") or 0):
                v["free_mv"] = c["free_mv"]
            if not (v.get("name") or "") and (c.get("name") or ""):
                v["name"] = c["name"]
            if not (v.get("board") or "") and (c.get("board") or ""):
                v["board"] = c["board"]

    # ③ 腾讯(只补仍缺的)
    still = [c for c in need if not (raw_all[c].get("float_mv") or 0)]
    if still and enabled():
        if len(still) > MAX_FETCH:
            log.warning("[市值缓存] 缺失%d只 > 上限%d, 只补前%d只",
                        len(still), MAX_FETCH, MAX_FETCH)
            still = still[:MAX_FETCH]
        t0 = time.time()
        got = fetch_tencent(still)
        for code, g in got.items():
            v = raw_all.get(code)
            if v is None:
                continue
            if not (v.get("float_mv") or 0) and (g.get("float_mv") or 0):
                v["float_mv"] = g["float_mv"]
                st["from_tencent"] += 1
            if not (v.get("name") or ""):
                v["name"] = g.get("name") or ""
        if got:
            log.info("[市值缓存] 腾讯补市值 %d/%d 只 耗时%.0fms",
                     len(got), len(still), (time.time() - t0) * 1000)

    # 回写缓存: 所有拿到市值的(东财源优先, 腾讯源次之)
    rows = {}
    for code, v in raw_all.items():
        mv = v.get("float_mv") or 0
        if mv <= 0 and not (v.get("name") or ""):
            continue
        rows[code] = {
            "name": v.get("name") or "",
            "float_mv": mv or None,
            "free_mv": (v.get("free_mv") or 0) or None,
            "board": v.get("board") or "",
            "src": "tencent" if code in got else "em",
        }
    st["saved"] = save(date, rows)
    st["miss"] = sum(1 for c, v in raw_all.items() if not (v.get("float_mv") or 0))
    return st
