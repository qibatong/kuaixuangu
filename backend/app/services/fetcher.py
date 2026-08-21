# -*- coding: utf-8 -*-
"""
数据抓取服务: 东方财富行情拉取 + 分区缓存 + 昨日成交额(日K) + 域名熔断
======================================================================
"""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core import config, logger
from . import scorer   # 仅复用 parse_float / market_fs (无循环: scorer 不依赖 fetcher)

log = logger.get_logger(__name__)

# ---------- 按市场范围(fs)分区的行情缓存 ----------
# fs -> {"raw": [...], "ts": epoch}; 锁只保护"拉数据/更新缓存", 评分计算不持锁
_cache = {}
_fetch_lock = threading.Lock()

# 昨日全天成交额(万元): code -> [缓存日期, 金额], 当日有效
_yesterday_cache = {}
_yesterday_lock = threading.Lock()

# 域名熔断: 请求失败/被限流时冷却, 避免反复重试拖慢响应
_broken_hosts = {}
_HOST_COOLDOWN = 300   # 冷却 5 分钟

# ---------- 数据源健康监控 ----------
# src -> {ok, fail, last_ok, last_fail, ms_sum, ms_cnt, down_since}
# down_since>0 表示自该时刻起处于"故障中"(失败后尚未成功恢复)
_HEALTH = {
    "eastmoney_clist": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "eastmoney_kline": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "eastmoney_zt_pool": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "ths_kline":       {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
}
_health_lock = threading.Lock()


def _record(src, ok, ms=0):
    """记录一次数据源调用结果; 状态翻转时打告警/恢复日志"""
    with _health_lock:
        h = _HEALTH[src]
        now = time.time()
        if ok:
            h["ok"] += 1
            h["last_ok"] = now
            if ms > 0:
                h["ms_sum"] += ms
                h["ms_cnt"] += 1
            if h["down_since"]:
                log.info("数据源恢复: %s 恢复正常(故障%.0f秒)", src, now - h["down_since"])
                h["down_since"] = 0
        else:
            h["fail"] += 1
            h["last_fail"] = now
            if not h["down_since"]:
                h["down_since"] = now
                log.error("数据源故障: %s 调用失败, 进入异常状态", src)


def _src_status(h):
    """单个数据源状态: ok / degraded / down"""
    if h["down_since"] and h["last_ok"] < h["down_since"]:
        return "down"
    if h["fail"] and h["last_fail"] > h["last_ok"]:
        return "degraded"
    return "ok"


def get_health_status():
    """数据源健康快照: {overall, sources:{src:{status,ok,fail,last_ok,last_fail,avg_ms}}}"""
    with _health_lock:
        sources = {}
        for src, h in _HEALTH.items():
            st = _src_status(h)
            sources[src] = {
                "status": st,
                "ok": h["ok"], "fail": h["fail"],
                "last_ok": h["last_ok"], "last_fail": h["last_fail"],
                "avg_ms": round(h["ms_sum"] / h["ms_cnt"]) if h["ms_cnt"] else 0,
            }
        statuses = [s["status"] for s in sources.values()]
        if all(st == "down" for st in statuses):
            overall = "down"          # 全部数据源故障
        elif "down" in statuses or "degraded" in statuses:
            overall = "degraded"      # 部分故障/降级(可能由兜底源覆盖)
        else:
            overall = "ok"
    return {"overall": overall, "sources": sources}


def _bj_date_str():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _secid(code):
    """沪市(6/9开头)用 1. 前缀, 深市/北交用 0. 前缀"""
    return ("1." if code.startswith(("6", "9")) else "0.") + code


def _fetch_clist_page(fs, page, fid="f3"):
    """拉取 clist 单页(200只); 失败抛异常。
    fid: "f3"=按涨幅排序(竞价模式取强票榜) / "f12"=按代码排序(全市场分页, 稳定不漏票)。"""
    qs = urllib.parse.urlencode({
        "fs": fs, "fltt": 2, "invt": 2, "fields": config.FIELDS,
        "fid": fid, "po": 1, "pn": page, "pz": 200, "np": 1, "ut": config.EASTMONEY_UT,
    })
    req = urllib.request.Request(config.EASTMONEY_URL + "?" + qs, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
        raise RuntimeError("东方财富接口返回异常")
    return data["data"]["diff"]


def fetch_eastmoney(fs):
    """拉取一个市场分区的全部股票快照(至多 200 只, 按涨幅倒序)"""
    t0 = time.time()
    try:
        diff = _fetch_clist_page(fs, 1)
    except Exception as e:
        _record("eastmoney_clist", False)
        raise
    _record("eastmoney_clist", True, int((time.time() - t0) * 1000))
    log.info("东财行情拉取成功 fs=%s 数量%d", fs, len(diff))
    return diff


def fetch_eastmoney_all(fs):
    """盘中实时模式: 分页拉取全市场股票快照(默认每页 200, 共 ~20 页),
    让过滤参数(涨幅/量比/换手)真正作用于全市场, 而不是只取涨幅前 200。
    按代码(f12)排序分页: 位置稳定, 任一分页失败只跳过该页, 不漏已跌出榜单的票。
    并发拉取(2026-08-19 性能优化): 30 页 ThreadPoolExecutor 并发, 冷缓存 3s→0.5s;
    空页=到底(提前结束), 任一页失败跳过该页, 全部失败抛异常。"""
    t_all = time.time()
    # 先并发拉前 N 页, 根据空页/短页判定真实页数
    pages_data = {}   # page -> diff list(失败/空为 None)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_clist_page, fs, p, "f12"): p
                for p in range(1, config.SPOT_MAX_PAGES + 1)}
        for fut in as_completed(futs):
            p = futs[fut]
            t0 = time.time()
            try:
                diff = fut.result()
                _record("eastmoney_clist", True, int((time.time() - t0) * 1000))
                pages_data[p] = diff
            except Exception as e:
                _record("eastmoney_clist", False, int((time.time() - t0) * 1000))
                log.warning("全市场拉取分页失败 fs=%s page=%d err=%s", fs, p, e)
                pages_data[p] = None
    # 按 page 顺序合并, 遇到空页/短页即终止(后续页不会有效数据)
    out = []
    last_page = 0
    for p in range(1, config.SPOT_MAX_PAGES + 1):
        diff = pages_data.get(p)
        if not diff:
            if diff is None:
                continue   # 该页失败, 跳过(不终止, 后续页可能成功)
            break          # 空页 = 到底
        out.extend(diff)
        last_page = p
        if len(diff) < 200:
            break          # 最后一页
    log.info("全市场行情拉取成功 fs=%s 共%d只(%d页) 并发耗时%.0fms",
             fs, len(out), last_page, (time.time() - t_all) * 1000)
    if not out:
        raise RuntimeError("东方财富接口返回异常")
    return out


# 两市市场概况缓存(2026-08-16): 全市场股票数 + 成交额, 5 分钟新鲜度
_market_brief_cache = {"ts": 0.0, "data": None}


def fetch_market_brief(max_age=300):
    """两市概况: {stockCount, amount(亿), date}
    - stockCount = 全市场股票数(沪+深+北, fetch_eastmoney_all 返回列表长度)
    - amount     = sum(f6) 全市场成交额(元 -> 亿)
    - 非交易时段(周末/收盘后) f6 可能全 0 -> amount 0, 由调用方决定展示
    5 分钟缓存, 首次全市场分页拉取 5-10s, 之后命中缓存"""
    now = time.time()
    if _market_brief_cache["data"] and now - _market_brief_cache["ts"] < max_age:
        return _market_brief_cache["data"]
    try:
        raw = fetch_eastmoney_all(scorer.market_fs(["hs", "cyb", "kcb", "bj"]))
    except Exception as e:
        log.warning("两市概况拉取失败 err=%s", e)
        return _market_brief_cache["data"] or None
    total_amt = sum(scorer.parse_float(s.get("f6")) for s in raw)
    g = time.gmtime(now + 8 * 3600)   # 北京时间
    d = {
        "stockCount": len(raw),
        "amount": round(total_amt / 1e8, 2),          # 亿元
        "date": "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday),
    }
    _market_brief_cache.update({"ts": now, "data": d})
    return d


def record_intraday_snapshot(date=None):
    """记录当日分时快照到 settings(2026-08-16): worker 每 5 分钟调用,
    供次日做"两市成交额较昨日同一时点"对比; value 是 [{ts, amount, stockCount}] 列表
    返回本次快照 (单条)"""
    from . import settings as settings_svc
    if date is None:
        g = time.gmtime(now + 8 * 3600) if False else time.gmtime((time.time() + 8 * 3600))
        date = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    brief = fetch_market_brief(max_age=0)   # 强制刷新拉取(用于实时累计)
    if not brief:
        return None
    key = "market_brief_intraday_" + date
    arr = settings_svc.get(key) or []
    snap = {"ts": int(time.time()),
            "amount": brief["amount"],
            "stockCount": brief["stockCount"]}
    arr.append(snap)
    settings_svc.set(key, arr)
    return snap


def get_same_time_yesterday(date=None):
    """取昨日同一时点的成交额(用于'两市较昨日同一时点'对比);
    今日 10:30 → 查昨日 intraday list, 找 ts <= 当前 ts 的最新点
    返回 {amount, stockCount, ts} 或 None (无昨日数据)"""
    from . import settings as settings_svc
    from datetime import datetime, timedelta
    now = int(time.time())
    ydate = (datetime.fromtimestamp(now + 8 * 3600) - timedelta(days=1)).strftime("%Y-%m-%d")
    # 处理非交易日: 昨日=周六 → 周五数据(但周五数据可能也没有, 取更早)
    for offset in range(0, 5):    # 最多回溯 5 天
        cur = (datetime.fromtimestamp(now + 8 * 3600) - timedelta(days=1 + offset)).strftime("%Y-%m-%d")
        arr = settings_svc.get("market_brief_intraday_" + cur)
        if not arr:
            continue
        # 找 ts <= now 的最新点
        cand = [s for s in arr if s.get("ts", 0) <= now]
        if cand:
            return {"amount": cand[-1]["amount"], "stockCount": cand[-1]["stockCount"],
                    "ts": cand[-1]["ts"], "date": cur}
        # 全部都比当前 ts 新(跨日?) → 取最后一条
        return {"amount": arr[-1]["amount"], "stockCount": arr[-1]["stockCount"],
                "ts": arr[-1]["ts"], "date": cur}
    return None


def ensure_cache(action, fs, before930):
    """在锁内保证缓存可用且新鲜, 返回 (raw, 错误信息)。
    - lock:    9:30 前强制重新拉取(锁定期权)
    - refresh: 缓存过期(超过 CACHE_TTL 秒)才重新拉取
    - filter:  无缓存时拉取一次
    """
    with _fetch_lock:
        now = time.time()
        if action == "lock":
            if not before930:
                return None, "9:30 后禁止重新选股"
            _cache[fs] = {"raw": fetch_eastmoney(fs), "ts": now}
            log.info("缓存锁定 fs=%s", fs)
        elif action == "refresh":
            entry = _cache.get(fs)
            if entry is None or now - entry["ts"] > config.CACHE_TTL:
                _cache[fs] = {"raw": fetch_eastmoney(fs), "ts": now}
                log.info("缓存刷新 fs=%s", fs)
            else:
                log.info("缓存命中 fs=%s 年龄%.0fs", fs, now - entry["ts"])
        else:  # filter
            if fs not in _cache:
                _cache[fs] = {"raw": fetch_eastmoney(fs), "ts": now}
                log.info("缓存初建 fs=%s", fs)
            else:
                log.info("缓存命中 fs=%s", fs)
        return _cache[fs]["raw"], None


def ensure_spot_cache(action, fs, before930):
    """盘中实时模式缓存: 不受 9:30 限制, 缓存新鲜度用 SPOT_CACHE_TTL。
    拉取全市场(分页), 返回 (raw, 错误信息); 拉取失败沿用旧缓存(降级不报错)。"""
    with _fetch_lock:
        now = time.time()
        entry = _cache.get(fs)
        if entry is None or now - entry["ts"] > config.SPOT_CACHE_TTL:
            try:
                _cache[fs] = {"raw": fetch_eastmoney_all(fs), "ts": now}
                log.info("盘中全市场缓存刷新 fs=%s", fs)
            except Exception as e:
                if entry is not None:
                    log.warning("盘中拉取失败, 沿用旧缓存 fs=%s err=%s", fs, e)
                    return entry["raw"], None
                raise
        else:
            log.info("盘中缓存命中 fs=%s 年龄%.0fs", fs, now - entry["ts"])
        return _cache[fs]["raw"], None


# 全市场实时行情 map 的独立缓存(避免与竞价200只缓存共用 key 串数据)
_quote_map_cache = {}
_quote_map_lock = threading.Lock()


def fetch_spot_quote_map(fs):
    """9:30 后竞价模式: 全市场实时行情 map(code -> {realChange, entityChange, price, volRatio, turnover, name})。
    独立缓存(SPOT_CACHE_TTL), 不参与评分, 供前端锁定名单 merge + 按当前筛选条件过滤。"""
    with _quote_map_lock:
        now = time.time()
        ent = _quote_map_cache.get(fs)
        if ent is None or now - ent["ts"] > config.SPOT_CACHE_TTL:
            try:
                raw = fetch_eastmoney_all(fs)
                _quote_map_cache[fs] = {"raw": raw, "ts": now}
                log.info("全市场行情map刷新 fs=%s 共%d只", fs, len(raw))
            except Exception as e:
                if ent is not None:
                    log.warning("全市场行情map拉取失败, 沿用旧缓存 fs=%s err=%s", fs, e)
                else:
                    raise
        else:
            log.info("全市场行情map命中 fs=%s 年龄%.0fs", fs, now - ent["ts"])
        raw = _quote_map_cache[fs]["raw"]
    out = {}
    for s in raw:
        out[s.get("f12")] = {
            "realChange": _parse_float(s.get("f3")),
            "entityChange": _entity_change(s),
            "price": _parse_float(s.get("f2")),
            "volRatio": _parse_float(s.get("f10")),
            "turnover": _parse_float(s.get("f8")),
            "name": s.get("f14") or "",
        }
    return out


def _parse_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _entity_change(s):
    """实体涨幅 = (现价-今开)/今开*100; 与 scorer 保持一致"""
    c = _parse_float(s.get("f2"))
    o = _parse_float(s.get("f17"))
    return 0.0 if o == 0 else (c - o) / o * 100


def _host_blocked(host):
    ts = _broken_hosts.get(host)
    return ts is not None and time.time() - ts < _HOST_COOLDOWN


def _mark_host_broken(host):
    _broken_hosts[host] = time.time()


def _fetch_yesterday_amount_ths(code):
    """同花顺日K兜底源: 返回最近两交易日成交额 [T日, T-1日] 万元; 失败返回 None
    接口: d.10jqka.com.cn/v6/line/hs_{code}/01/last.js (全部历史K线, 含成交额)
    字段: 日期,今开,最高,最低,收盘,成交量(股),成交额(元),换手率...
    """
    for proto in ("https", "http"):
        url = "%s://d.10jqka.com.cn/v6/line/hs_%s/01/last.js" % (proto, code)
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "http://stockpage.10jqka.com.cn/",
            })
            with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "ignore")
            m = re.search(r"\{.*\}", body, re.S)
            if not m:
                continue
            data = json.loads(m.group(0)).get("data") or ""
            segs = [s for s in str(data).split(";") if s]
            if not segs:
                continue
            pair = _kline_amount_pair(segs)
            if pair is None:
                continue
            _record("ths_kline", True, int((time.time() - t0) * 1000))
            return pair
        except Exception:
            continue
    _record("ths_kline", False)
    return None


def _fetch_yesterday_amount_one(code):
    """拉单只股票最近两交易日成交额(万元): 返回 [T日, T-1日] (T=最近已收盘交易日);
    东财日K(多域名轮询)失败后自动切同花顺兜底; 完全失败返回 None"""
    qs = urllib.parse.urlencode({
        "secid": _secid(code), "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": 101, "fqt": 0, "end": "20500101", "lmt": 2,
    })
    for host in config.KLINE_HOSTS:
        if _host_blocked(host):
            continue
        t0 = time.time()
        try:
            req = urllib.request.Request(host + "/api/qt/stock/kline/get?" + qs, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://quote.eastmoney.com/",
            })
            with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            klines = data.get("data", {}).get("klines") or []
            if not klines:
                _mark_host_broken(host)
                continue
            pair = _kline_amount_pair(klines)
            if pair is None:
                continue
            _record("eastmoney_kline", True, int((time.time() - t0) * 1000))
            return pair
        except Exception:
            _mark_host_broken(host)
            continue
    _record("eastmoney_kline", False)
    # 东财全失败 → 同花顺兜底
    return _fetch_yesterday_amount_ths(code)


def _kline_amount_pair(klines):
    """从日K行(逗号分隔, 第0字段=日期, 第7字段=成交额元)提取 [最近已收盘T日万元, T-1日万元];
    自动跳过"今天"(未收盘)的K线, 保证 pair[0] 恒为最近已收盘交易日全天额。
    东财日期格式 YYYY-MM-DD, 同花顺 YYYYMMDD, 两种都兼容; 不足/无效返回 None。
    (修复: 东财盘中含今天未收盘K线, 同花顺不含 → 两源 pair 语义曾不一致, 导致分母错位)"""
    today = _bj_date_str()
    def amt_of(row):
        parts = row.split(",")
        if len(parts) < 7:
            return None, None
        try:
            v = float(parts[6])
            if not (math.isfinite(v) and v > 0):
                return None, None
            return v / 10000.0, parts[0]
        except (TypeError, ValueError):
            return None, None
    def is_today(dstr):
        if not dstr:
            return False
        d = dstr.replace("-", "")
        return d == today.replace("-", "")
    # 收集所有 (日期, 金额), 跳过今天
    rows = []
    for row in klines:
        amt, dstr = amt_of(row)
        if amt is not None and not is_today(dstr):
            rows.append((dstr, amt))
    if not rows:
        return None
    # 最近已收盘 = 最后一行(按日期), 取它和它前一行
    t = rows[-1][1]
    t1 = rows[-2][1] if len(rows) >= 2 else None
    if t is None and t1 is None:
        return None
    return [t, t1]


def fetch_yesterday_amounts(codes):
    """并发获取一批股票的昨日成交额(万元), 带当日缓存; 返回 {code: 金额万元}
    (2026-08-17 修复: ex.map 会等所有并发完成, 东财限流时单个 code 遍历多域名+同花顺兜底
     可拖 30-60s → 抢筹 listLast 阻塞 62s; 改 as_completed + 整体超时, 超时未完成跳过(昨比置空)"""
    if not codes:
        return {}
    today = _bj_date_str()
    need = []
    with _yesterday_lock:
        for c in codes:
            ent = _yesterday_cache.get(c)
            if ent is None or ent[0] != today:
                need.append(c)
    if need:
        ok_cnt = 0
        fail_cnt = len(need)
        with ThreadPoolExecutor(max_workers=config.YESTERDAY_FETCH_WORKERS) as ex:
            futs = {ex.submit(_fetch_yesterday_amount_one, c): c for c in need}
            try:
                for f in as_completed(futs, timeout=config.YESTERDAY_FETCH_TIMEOUT):
                    c = futs[f]
                    try:
                        v = f.result()
                        if v is not None:
                            ok_cnt += 1
                            with _yesterday_lock:
                                _yesterday_cache[c] = [today, v]
                    except Exception:
                        pass
                    fail_cnt -= 1
            except concurrent.futures.TimeoutError:
                # 超时未完成: 跳过(不等待慢 code), 昨比对该 code 置空
                done = len(need) - fail_cnt
                log.warning("昨日成交额拉取超时(%ds) 已完成%d/%d, 超时跳过",
                            config.YESTERDAY_FETCH_TIMEOUT, done, len(need))
                fail_cnt = len(need) - ok_cnt
        if fail_cnt:
            log.warning("昨日成交额拉取: 需%d 成功%d 失败%d", len(need), ok_cnt, fail_cnt)
    out = {}
    with _yesterday_lock:
        for c in codes:
            ent = _yesterday_cache.get(c)
            if ent and ent[0] == today:
                out[c] = ent[1]
    return out


# ---------- 盘中实时选股: 东财涨停池(封单/连板/炸板) ----------
# date -> {"raw": {"code": zt_info}, "ts": epoch}; 涨停池数据当日有效, 盘中按 TTL 刷新
_zt_cache = {}
_zt_lock = threading.Lock()


def fetch_zt_pool(date=None):
    """拉取东财涨停池(含封单额/封板时间/炸板次数/连板数), 带缓存。
    date: YYYYMMDD, 默认今天(北京); 返回 {code: {fund, fb, lb, zbc, zdp}} 或 {}
    失败返回空 dict(不影响选股主流程, 盘中封单因子降级为无数据)。
    """
    date = date or _bj_date_str().replace("-", "")
    with _zt_lock:
        ent = _zt_cache.get(date)
        if ent and time.time() - ent["ts"] < config.ZT_CACHE_TTL:
            return ent["raw"]
    qs = urllib.parse.urlencode({
        "ut": config.EASTMONEY_ZT_UT, "dpt": "wz.ztzt",
        "Pageindex": 0, "pagesize": 1000, "sort": "fbt:asc", "date": date,
    })
    t0 = time.time()
    try:
        req = urllib.request.Request(config.EASTMONEY_ZT_URL + "?" + qs, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pool = (data.get("data") or {}).get("pool") or []
        out = {}
        for p in pool:
            code = str(p.get("c") or "")
            if not code:
                continue
            out[code] = {
                "fund": float(p.get("fund") or 0) / 1e8,   # 封单金额(亿)
                "fb": int(p.get("fbt") or 0),              # 封板时间 HHMMSS
                "lb": int(p.get("lbc") or 0),              # 连板数
                "zbc": int(p.get("zbc") or 0),             # 炸板次数
                "zdp": float(p.get("zdp") or 0),           # 涨停涨幅(%)
            }
        _record("eastmoney_zt_pool", True, int((time.time() - t0) * 1000))
        with _zt_lock:
            _zt_cache[date] = {"raw": out, "ts": time.time()}
        log.info("涨停池拉取成功 date=%s 涨停数%d", date, len(out))
        return out
    except Exception as e:
        _record("eastmoney_zt_pool", False)
        log.warning("涨停池拉取失败 date=%s err=%s", date, e)
        return {}


# ==================== 个股图表数据(分时/K线) ====================
# 东财标准 kline 接口: klt=101日K / 102周K / 103月K
# 分时 trends2 接口: 当日分时轨迹(价格+均价+成交量)
_CHART_CACHE = {}
_CHART_LOCK = threading.Lock()
_CHART_CACHE_TTL = 60     # 分时 60s 缓存, K线 1800s 缓存


def fetch_stock_chart(code, period="day"):
    """获取个股图表数据
    period: minute(当日分时) / day(日K, 默认120根) / week(周K, 120根) / month(月K, 60根)
    返回 {
      period, code,
      minute 情况: {time: [...], price: [...], avg: [...], volume: [...], preClose: float}
      K线 情况:   {time: [...], open: [...], close: [...], high: [...], low: [...],
                   volume: [...], amount: [...], preClose: float}
    }  失败返回 {}
    """
    if not code:
        return {}
    period = (period or "day").lower()
    if period == "minute":
        return _fetch_minute_trend(code)
    klt = {"day": 101, "week": 102, "month": 103}.get(period)
    if not klt:
        log.warning("stock_chart 未知 period=%s code=%s", period, code)
        return {}
    lmt = 60 if period == "month" else 120
    cache_key = f"kline:{code}:{period}"
    with _CHART_LOCK:
        ent = _CHART_CACHE.get(cache_key)
        ttl = 1800 if period == "month" else 1800 if period == "week" else 300  # 月/周K 30min, 日K 5min
        if ent and time.time() - ent["ts"] < ttl:
            return ent["data"]
    secid = _secid(code)
    qs = urllib.parse.urlencode({
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",  # date,open,close,high,low,volume,amount,amplitude
        "klt": klt, "fqt": 1,           # 前复权
        "end": "20500101", "lmt": lmt,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    })
    for host in config.KLINE_HOSTS:
        if _host_blocked(host):
            continue
        t0 = time.time()
        try:
            url = host + "/api/qt/stock/kline/get?" + qs
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://quote.eastmoney.com/",
            })
            with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            klines = data.get("data", {}).get("klines") or []
            meta = data.get("data", {}) or {}
            preClose = float(meta.get("preKPrice") or meta.get("f60") or 0)
            times, opens, closes, highs, lows, volumes, amounts = [], [], [], [], [], [], []
            for row in klines:
                parts = row.split(",")
                if len(parts) < 7:
                    continue
                times.append(parts[0])
                try:
                    opens.append(float(parts[1]))
                    closes.append(float(parts[2]))
                    highs.append(float(parts[3]))
                    lows.append(float(parts[4]))
                    volumes.append(float(parts[5]))
                    amounts.append(float(parts[6]))
                except (TypeError, ValueError):
                    # 尾行丢弃
                    times.pop()
                    continue
            result = {
                "period": period, "code": code,
                "time": times, "open": opens, "close": closes, "high": highs, "low": lows,
                "volume": volumes, "amount": amounts, "preClose": preClose,
                "name": meta.get("name", ""),
            }
            _record("eastmoney_kline", True, int((time.time() - t0) * 1000))
            with _CHART_LOCK:
                _CHART_CACHE[cache_key] = {"data": result, "ts": time.time()}
            return result
        except Exception:
            _mark_host_broken(host)
            continue
    _record("eastmoney_kline", False)
    log.warning("K线拉取失败 code=%s period=%s (东财全HOST熔断)", code, period)
    return {}


def _fetch_minute_trend(code):
    """当日分时轨迹(价格+均价+成交量) via 东财 trends2
    返回 {period:'minute', code, time:[], price:[], avg:[], volume:[], preClose, name}
    非交易时段接口仍会返回上一个交易日的分时 → 前端提示'非交易时段'即可"""
    cache_key = f"trend:{code}"
    with _CHART_LOCK:
        ent = _CHART_CACHE.get(cache_key)
        if ent and time.time() - ent["ts"] < _CHART_CACHE_TTL:
            return ent["data"]
    secid = _secid(code)
    qs = urllib.parse.urlencode({
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ndays": 1, "iscr": 0, "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    })
    for host in config.KLINE_HOSTS:
        if _host_blocked(host):
            continue
        t0 = time.time()
        try:
            url = host + "/api/qt/stock/trends2/get?" + qs
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://quote.eastmoney.com/",
            })
            with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tr = data.get("data", {}).get("trends") or []
            meta = data.get("data", {}) or {}
            preClose = float(meta.get("preClose") or 0)
            name = meta.get("name", "")
            times, prices, avgs, volumes = [], [], [], []
            for row in tr:
                parts = row.split(",")
                if len(parts) < 5:
                    continue
                # parts[0]=HHMM 或 YYYY-MM-DD HH:MM, parts[1]=价格, parts[2]=成交量(手),
                # parts[3]=均价(成交额/成交量), parts[4]=成交额(元)
                try:
                    ts = parts[0]
                    # 截断日期前缀, 只保留 HH:MM
                    if " " in ts:
                        ts = ts.split(" ", 1)[1]
                    times.append(ts)
                    # 东财 trends2 行格式: time, 价格, 成交量(手), 均价(成交额/量), 成交额(元)
                    # 之前 price/volume 数组索引写反导致: 价格序列塞的是成交量, 成交量塞的是价格
                    # → 高成交量票价格序列>10万手 触发校验拒绝("无分时图"), 且低量票图也画错
                    prices.append(float(parts[1]))
                    avgs.append(float(parts[3]) if parts[3] else None)
                    volumes.append(float(parts[2]))
                except (TypeError, ValueError):
                    continue
            result = {
                "period": "minute", "code": code, "name": name,
                "time": times, "price": prices, "avg": avgs, "volume": volumes,
                "preClose": preClose,
            }
            _record("eastmoney_kline", True, int((time.time() - t0) * 1000))
            with _CHART_LOCK:
                _CHART_CACHE[cache_key] = {"data": result, "ts": time.time()}
            return result
        except Exception:
            _mark_host_broken(host)
            continue
    _record("eastmoney_kline", False)
    log.warning("分时拉取失败 code=%s", code)
    return {}


# ==================== 多数据源 fallback (2026-08-20) ====================
# 当东财接口熔断时, 按顺序 fallback: 同花顺 → 开盘啦(kpl) → Tushare → 日线聚合
# 覆盖所有周期: 分时/日K/周K/月K


def _fetch_kline_from_ths(code, period="day"):
    """同花顺 K-line 兜底源: day/week/month
    URL 格式: https://d.10jqka.com.cn/v6/line/hs_{code}/{type}/last.js
    type: 01=日K, 02=周K, 03=月K
    返回标准格式 dict, 失败返回 {}"""
    type_map = {"day": "01", "week": "02", "month": "03"}
    tp = type_map.get(period, "01")
    for proto in ("https", "http"):
        url = "%s://d.10jqka.com.cn/v6/line/hs_%s/%s/last.js" % (proto, code, tp)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "http://stockpage.10jqka.com.cn/",
            })
            with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "ignore")
            m = re.search(r"\{.*\}", body, re.S)
            if not m:
                continue
            data = json.loads(m.group(0)).get("data") or ""
            if not data:
                continue
            segs = [s for s in str(data).split(";") if s]
            if not segs:
                continue
            times, opens, closes, highs, lows, volumes, amounts = [], [], [], [], [], [], []
            for seg in segs:
                parts = seg.split(",")
                if len(parts) < 7:
                    continue
                try:
                    d = parts[0].replace("-", "")
                    if len(d) == 8:
                        d = d[:4] + "-" + d[4:6] + "-" + d[6:8]
                    times.append(d)
                    opens.append(float(parts[1]))
                    highs.append(float(parts[2]))
                    lows.append(float(parts[3]))
                    closes.append(float(parts[4]))
                    vol = float(parts[5])
                    volumes.append(vol if vol < 10000000 else vol / 100.0)
                    amounts.append(float(parts[6]))
                except (TypeError, ValueError, IndexError):
                    continue
            if not times:
                continue
            preClose = 0
            if len(closes) >= 2:
                preClose = closes[-2]
            elif closes:
                preClose = closes[0]
            result = {
                "period": period, "code": code,
                "time": times, "open": opens, "close": closes,
                "high": highs, "low": lows,
                "volume": volumes, "amount": amounts,
                "preClose": preClose, "name": "",
            }
            log.info("同花顺K-line拉取成功 code=%s period=%s 条数=%d", code, period, len(times))
            return result
        except Exception as e:
            log.debug("同花顺K-line拉取失败 code=%s err=%s", code, e)
            continue
    log.warning("同花顺K-line拉取失败 code=%s period=%s", code, period)
    return {}


def _fetch_chart_from_kpl(code, period="day"):
    """开盘啦(kpl) chart 兜底源: 分时/日K (周K/月K 不支持)"""
    try:
        from . import kpl
    except ImportError:
        return {}
    if period == "minute":
        d = kpl.fetch_kpl_doc8(StockID=code)
        if not d:
            log.warning("kpl分时拉取失败 code=%s", code)
            return {}
        preClose = float(d.get("preclose_px") or d.get("preClose") or d.get("pre_close") or 0)
        name = d.get("name") or ""
        trend = d.get("trend") or []
        if not trend:
            log.warning("kpl分时无数据 code=%s", code)
            if preClose > 0:
                return {"period": "minute", "code": code, "name": name,
                        "time": [], "price": [], "avg": [], "volume": [], "preClose": preClose}
            return {}
        times, prices, avgs, volumes = [], [], [], []
        for row in trend:
            if not isinstance(row, list) or len(row) < 4:
                continue
            try:
                ts = str(row[0])
                if " " in ts:
                    ts = ts.split(" ", 1)[1]
                times.append(ts)
                prices.append(float(row[1]))
                avgs.append(float(row[2]) if row[2] else None)
                volumes.append(float(row[3]))
            except (TypeError, ValueError, IndexError):
                continue
        if not times:
            return {}
        result = {"period": "minute", "code": code, "name": name,
                  "time": times, "price": prices, "avg": avgs, "volume": volumes,
                  "preClose": preClose}
        log.info("kpl分时拉取成功 code=%s 条数=%d preClose=%.2f", code, len(times), preClose)
        return result
    if period == "day":
        d = kpl.fetch_kpl_doc7(StockID=code, T="W8", RStart="0925", old="1")
        if not d or d.get("errcode") not in (None, "0"):
            log.warning("kpl日K拉取失败 code=%s errcode=%s", code, d.get("errcode") if d else "None")
            return {}
        x = d.get("x") or []
        y = d.get("y") or []
        if not x or not y:
            log.warning("kpl日K无数据 code=%s x=%d y=%d", code, len(x), len(y))
            return {}
        vol_arr = d.get("vol") or []
        bal_arr = d.get("bal") or []
        times, opens, closes, highs, lows, volumes, amounts = [], [], [], [], [], [], []
        n = min(len(x), len(y))
        for i in range(n):
            yi = y[i]
            if not isinstance(yi, list) or len(yi) < 4:
                continue
            try:
                times.append(str(x[i]))
                opens.append(float(yi[0]))
                closes.append(float(yi[1]))
                highs.append(float(yi[2]))
                lows.append(float(yi[3]))
                volumes.append(float(vol_arr[i]) if i < len(vol_arr) else 0)
                amounts.append(float(bal_arr[i]) if i < len(bal_arr) else 0)
            except (TypeError, ValueError, IndexError):
                times.pop()
                continue
        if not times:
            return {}
        if len(closes) >= 2:
            preClose = closes[-2]
        elif closes:
            preClose = closes[0]
        else:
            preClose = 0
        result = {"period": "day", "code": code,
                  "time": times, "open": opens, "close": closes,
                  "high": highs, "low": lows,
                  "volume": volumes, "amount": amounts,
                  "preClose": preClose, "name": d.get("name", "")}
        log.info("kpl日K拉取成功 code=%s 条数=%d preClose=%.2f", code, len(times), preClose)
        return result
    log.info("kpl不支持周期 period=%s, code=%s", period, code)
    return {}


def _fetch_chart_from_tushare(code, period="day"):
    """Tushare 代理网关 chart 兜底源 (K-line only, 分时不支持)

    网关返回两种兼容格式:
    A) 行格式(主流): { code:0, msg:'ok', data:{fields:[...], items:[[...],[...]]}, count, api_name }
    B) 宽格式(文档):   { api_name, count, trade_date:[...], close:[...], ... }
    两种格式同时兼容解析, 任一格式命中即返回.
    """
    if not config.TUSHARE_API_KEY:
        return {}
    if period not in ("day", "week", "month"):
        return {}
    if code.startswith(("6", "9", "5")):
        ts_code = code + ".SH"
    elif code.startswith(("0", "3", "2", "1")):
        ts_code = code + ".SZ"
    elif code.startswith(("8", "4")):
        ts_code = code + ".BJ"
    else:
        ts_code = code + ".SZ"
    api_map = {"day": "daily", "week": "weekly", "month": "monthly"}
    api = api_map.get(period)
    if not api:
        return {}
    path = "/tushare/pro/" + api
    params = {"ts_code": ts_code}
    try:
        qs = urllib.parse.urlencode(params)
        url = config.TUSHARE_BASE_URL + path + "?" + qs
        req = urllib.request.Request(url, headers={
            "X-API-Key": config.TUSHARE_API_KEY, "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        # 先检查网关层错误
        if raw.get("ok") is False and raw.get("error"):
            log.warning("tushare 网关拒绝 code=%s period=%s err=%s msg=%s",
                        code, period, raw.get("error"), raw.get("message"))
            return {}
        if raw.get("code") and raw.get("code") != 0:
            log.warning("tushare 返回错误 code=%s period=%s code_val=%s msg=%s",
                        code, period, raw.get("code"), raw.get("msg"))
            return {}
        count = raw.get("count", 0)
        rows = []  # 统一转成 rows: list of dicts
        # ===== 格式 A: data.fields + data.items (行格式) =====
        ddata = raw.get("data")
        if isinstance(ddata, dict) and isinstance(ddata.get("fields"), list) and isinstance(ddata.get("items"), list):
            fields = ddata["fields"]
            items = ddata["items"]
            idx = {}
            for i, fn in enumerate(fields):
                idx[fn] = i
            count = len(items) if not count else count
            for it in items:
                if not isinstance(it, list):
                    continue
                row = {}
                for fn, i in idx.items():
                    if i < len(it):
                        row[fn] = it[i]
                rows.append(row)
        # ===== 格式 B: 宽格式 (key -> 平行数组) =====
        if not rows and count:
            array_fields = {}
            for key, val in raw.items():
                if key in ("api_name", "count", "code", "msg", "ts_code", "request_id", "data"):
                    continue
                if isinstance(val, list) and len(val) == count:
                    array_fields[key] = val
            if array_fields:
                keys = list(array_fields.keys())
                for i in range(count):
                    row = {k: array_fields[k][i] for k in keys}
                    rows.append(row)
        if not rows:
            return {}
        times, opens, closes, highs, lows, volumes, amounts = [], [], [], [], [], [], []
        preClose = 0
        first_close = None
        for row in rows:
            try:
                td = str(row.get("trade_date") or row.get("ann_date") or "")
                if not td:
                    continue
                if len(td) == 8:
                    td = td[:4] + "-" + td[4:6] + "-" + td[6:8]
                c = float(row.get("close") or 0)
                if not c:
                    continue
                times.append(td)
                opens.append(float(row.get("open") or 0))
                closes.append(c)
                highs.append(float(row.get("high") or 0))
                lows.append(float(row.get("low") or 0))
                v = row.get("vol") or row.get("volume") or 0
                volumes.append(float(v))
                amounts.append(float(row.get("amount") or 0))
                if first_close is None:
                    first_close = c
                pc = row.get("pre_close")
                if not preClose and pc:
                    preClose = float(pc)
            except (TypeError, ValueError):
                if times: times.pop()
                continue
        if not times:
            return {}
        # 按时间升序 (Tushare 默认可能倒序)
        if len(times) >= 2 and times[0] > times[-1]:
            times  = list(reversed(times))
            opens  = list(reversed(opens))
            closes = list(reversed(closes))
            highs  = list(reversed(highs))
            lows   = list(reversed(lows))
            volumes= list(reversed(volumes))
            amounts= list(reversed(amounts))
        if not preClose and len(closes) >= 2:
            preClose = closes[-2]
        elif not preClose and closes:
            preClose = closes[0]
        result = {"period": period, "code": code,
                  "time": times, "open": opens, "close": closes,
                  "high": highs, "low": lows,
                  "volume": volumes, "amount": amounts,
                  "preClose": preClose, "name": ""}
        log.info("tushare K-line拉取成功 code=%s period=%s 条数=%d", code, period, len(times))
        return result
    except Exception as e:
        log.warning("tushare K-line拉取失败 code=%s period=%s err=%s", code, period, e)
        return {}


def _validate_chart_data(data, period, source=None):
    """验证图表数据合理性, 过滤异常数据(如同花顺累积前复权价)"""
    if not data:
        return False
    
    if period == "minute":
        prices = data.get("price") or []
        if not prices:
            return False
        valid_prices = [p for p in prices if p and p > 0]
        if not valid_prices:
            return False
        max_p = max(valid_prices)
        min_p = min(valid_prices)
        if max_p > 100000 or min_p < 0.01:
            return False
        return True
    closes = data.get("close") or []
    if not closes:
        return False
    valid_closes = [c for c in closes if c and c > 0]
    if not valid_closes:
        return False
    max_c = max(valid_closes)
    min_c = min(valid_closes)
    if max_c > 100000 or min_c < 0.01:
        return False
    if source == "ths" and period in ("week", "month"):
        if min_c > 500:
            return False
    if max_c > min_c * 100:
        return False
    volumes = data.get("volume") or []
    valid_vols = [v for v in volumes if v and v > 0]
    if not valid_vols and period != "minute":
        return False
    return True


def _fetch_quote_tencent(code):
    """腾讯实时行情(最新交易日 OHLCV/A)
    返回 {code, date:'YYYY-MM-DD', open,high,low,close,volume(手),amount(元),preclose} 失败返回 None
    """
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = "http://qt.gtimg.cn/q=%s%s" % (prefix, code)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT) as resp:
            body = resp.read().decode("gbk", "ignore")
        m = re.search(r'="(.*)"', body)
        if not m:
            return None
        f = m.group(1).split("~")
        if len(f) < 38 or not (f[30] or "").strip():
            return None
        ts = f[30].strip()  # YYYYMMDDHHMMSS
        if len(ts) < 8:
            return None
        date_str = ts[:4] + "-" + ts[4:6] + "-" + ts[6:8]
        close = float(f[3])
        preclose = float(f[4])
        open_ = float(f[5])
        high = float(f[33])
        low = float(f[34])
        volume = float(f[6])  # 手
        amount = 0.0
        try:
            amount = float(str(f[35]).split("/")[2])
        except (IndexError, ValueError, AttributeError):
            amount = float(f[37]) * 10000.0
        return {"code": code, "date": date_str, "open": open_, "high": high,
                "low": low, "close": close, "volume": volume, "amount": amount,
                "preclose": preclose}
    except Exception as e:
        log.debug("腾讯行情拉取失败 code=%s err=%s", code, e)
        return None


def _norm_kline_date(s):
    """把 YYYYMMDD 归一化成 YYYY-MM-DD"""
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return s[:4] + "-" + s[4:6] + "-" + s[6:8]
    return s


def _refresh_last_kline(data, q):
    """用当日实时行情刷新最后一根K线(high取较大, low取较小)"""
    for key, val in (("close", q["close"]), ("volume", q["volume"]), ("amount", q["amount"])):
        lst = data.get(key)
        if isinstance(lst, list) and lst:
            lst[-1] = val
    highs = data.get("high")
    if isinstance(highs, list) and highs:
        highs[-1] = max(float(highs[-1]), float(q["high"]))
    lows = data.get("low")
    if isinstance(lows, list) and lows:
        lows[-1] = min(float(lows[-1]), float(q["low"]))


def _ensure_latest_period(data, code):
    """确保周K/月K/日K包含最新交易日(今天), 用腾讯实时行情刷新/追加最后一根
    - day: 最后一根缺当天→追加; ==当天→刷新
    - week/month: 最后一根属于当前周期→刷新; 缺当前周期→追加一根(用当日数据近似, 保证最新周期可见)
    失败或分时数据原样返回
    """
    if not data:
        return data
    period = data.get("period")
    if period not in ("day", "week", "month"):
        return data
    times = data.get("time") or []
    if not times:
        return data
    q = _fetch_quote_tencent(code)
    if not q:
        return data
    qdate = q["date"]
    last_norm = _norm_kline_date(times[-1])

    def same_bar(a, b):
        if period == "month":
            return a[:7] == b[:7]
        if period == "week":
            from datetime import datetime
            try:
                return datetime.strptime(a, "%Y-%m-%d").isocalendar()[:2] == \
                       datetime.strptime(b, "%Y-%m-%d").isocalendar()[:2]
            except ValueError:
                return a[:10] == b[:10]
        return a == b

    append_bar = (q["date"], q["open"], q["close"], q["high"], q["low"],
                  q["volume"], q["amount"])
    keys = ("time", "open", "close", "high", "low", "volume", "amount")

    if period == "day":
        if last_norm < qdate:
            for k, v in zip(keys, append_bar):
                lst = data.get(k)
                if isinstance(lst, list):
                    lst.append(v)
        elif last_norm == qdate:
            _refresh_last_kline(data, q)
    else:
        if same_bar(last_norm, qdate):
            _refresh_last_kline(data, q)
        else:
            # 缺当前周期: 追加一根(用当日数据近似, 至少让最新周期可见)
            for k, v in zip(keys, append_bar):
                lst = data.get(k)
                if isinstance(lst, list):
                    lst.append(v)
    return data


def _aggregate_kpl_daily_to_period(code, target_period):
    """用 kpl 日线数据聚合成周K/月K (所有主源失败时最终兜底)"""
    try:
        from . import kpl
    except ImportError:
        return {}
    d = kpl.fetch_kpl_doc7(StockID=code, T="W8", RStart="0925", old="1")
    if not d or d.get("errcode") not in (None, "0"):
        return {}
    x = d.get("x") or []
    y = d.get("y") or []
    if not x or not y:
        return {}
    vol_arr = d.get("vol") or []
    bal_arr = d.get("bal") or []
    MAX_DAILY = 300
    daily_data = []
    total = min(len(x), len(y))
    start = max(0, total - MAX_DAILY)
    from datetime import datetime
    for i in range(start, total):
        yi = y[i]
        if not isinstance(yi, list) or len(yi) < 4:
            continue
        try:
            date_str = str(x[i])
            daily_data.append({"date": date_str, "open": float(yi[0]), "close": float(yi[1]),
                "high": float(yi[2]), "low": float(yi[3]),
                "volume": float(vol_arr[i]) if i < len(vol_arr) else 0,
                "amount": float(bal_arr[i]) if i < len(bal_arr) else 0})
        except (TypeError, ValueError, IndexError):
            continue
    if not daily_data:
        return {}
    # 用腾讯实时行情补最新交易日, 保证周K/月K包含当天
    _q = _fetch_quote_tencent(code)
    if _q:
        _qd8 = _q["date"].replace("-", "")
        if daily_data[-1]["date"] < _qd8:
            daily_data.append({"date": _qd8, "open": _q["open"], "close": _q["close"],
                               "high": _q["high"], "low": _q["low"],
                               "volume": _q["volume"], "amount": _q["amount"]})
    if target_period == "week":
        groups = {}
        for item in daily_data:
            try:
                dt = datetime.strptime(item["date"], "%Y%m%d")
                year, week, _ = dt.isocalendar()
                key = "%d-%02d" % (year, week)
                if key not in groups:
                    groups[key] = []
                groups[key].append(item)
            except:
                continue
        agg_times, agg_opens, agg_closes, agg_highs, agg_lows = [], [], [], [], []
        agg_volumes, agg_amounts = [], []
        for key in sorted(groups.keys()):
            items = groups[key]
            if not items:
                continue
            last_date = max(it["date"] for it in items)
            formatted = last_date[:4] + "-" + last_date[4:6] + "-" + last_date[6:8]
            agg_times.append(formatted)
            agg_opens.append(items[0]["open"])
            agg_closes.append(items[-1]["close"])
            agg_highs.append(max(it["high"] for it in items))
            agg_lows.append(min(it["low"] for it in items))
            agg_volumes.append(sum(it["volume"] for it in items))
            agg_amounts.append(sum(it["amount"] for it in items))
        if not agg_times:
            return {}
        preClose = daily_data[-2]["close"] if len(daily_data) >= 2 else daily_data[0]["close"]
        return {"period": target_period, "code": code,
                "time": agg_times, "open": agg_opens, "close": agg_closes,
                "high": agg_highs, "low": agg_lows,
                "volume": agg_volumes, "amount": agg_amounts,
                "preClose": preClose, "name": ""}
    elif target_period == "month":
        groups = {}
        for item in daily_data:
            key = item["date"][:6]
            if key not in groups:
                groups[key] = []
            groups[key].append(item)
        agg_times, agg_opens, agg_closes, agg_highs, agg_lows = [], [], [], [], []
        agg_volumes, agg_amounts = [], []
        for key in sorted(groups.keys()):
            items = groups[key]
            if not items:
                continue
            last_date = max(it["date"] for it in items)
            formatted = last_date[:4] + "-" + last_date[4:6] + "-" + last_date[6:8]
            agg_times.append(formatted)
            agg_opens.append(items[0]["open"])
            agg_closes.append(items[-1]["close"])
            agg_highs.append(max(it["high"] for it in items))
            agg_lows.append(min(it["low"] for it in items))
            agg_volumes.append(sum(it["volume"] for it in items))
            agg_amounts.append(sum(it["amount"] for it in items))
        if not agg_times:
            return {}
        preClose = daily_data[-2]["close"] if len(daily_data) >= 2 else daily_data[0]["close"]
        return {"period": target_period, "code": code,
                "time": agg_times, "open": agg_opens, "close": agg_closes,
                "high": agg_highs, "low": agg_lows,
                "volume": agg_volumes, "amount": agg_amounts,
                "preClose": preClose, "name": ""}
    return {}


def fetch_stock_chart_robust(code, period="day"):
    """多数据源 chart 拉取 (替代原 fetch_stock_chart):
    顺序: 东财 → 同花顺 → kpl → tushare → 自聚合
    任一数据源成功即返回, 全部失败返回 {}"""
    if not code:
        return {}
    period = (period or "day").lower()
    if period not in ("minute", "day", "week", "month"):
        return {}
    cache_key = "chart_robust:%s:%s" % (code, period)
    with _CHART_LOCK:
        ent = _CHART_CACHE.get(cache_key)
        ttl = 60 if period == "minute" else 1800
        if ent and time.time() - ent["ts"] < ttl:
            return ent["data"]
    t0 = time.time()
    sources = ["eastmoney"]
    if period != "minute":
        sources.append("ths")
    sources.append("kpl")
    if period != "minute" and config.TUSHARE_API_KEY:
        sources.append("tushare")
    for src in sources:
        try:
            if src == "eastmoney":
                d = fetch_stock_chart(code, period)
                if d and _validate_chart_data(d, period, source="eastmoney"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=eastmoney code=%s period=%s 耗时%.0fms",
                             code, period, (time.time() - t0) * 1000)
                    return _ensure_latest_period(d, code)
            elif src == "ths":
                d = _fetch_kline_from_ths(code, period)
                if d and _validate_chart_data(d, period, source="ths"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=ths code=%s period=%s 耗时%.0fms",
                             code, period, (time.time() - t0) * 1000)
                    return _ensure_latest_period(d, code)
            elif src == "kpl":
                d = _fetch_chart_from_kpl(code, period)
                if d and _validate_chart_data(d, period, source="kpl"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=kpl code=%s period=%s 耗时%.0fms",
                             code, period, (time.time() - t0) * 1000)
                    return _ensure_latest_period(d, code)
            elif src == "tushare":
                d = _fetch_chart_from_tushare(code, period)
                if d and _validate_chart_data(d, period, source="tushare"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=tushare code=%s period=%s 耗时%.0fms",
                             code, period, (time.time() - t0) * 1000)
                    return _ensure_latest_period(d, code)
        except Exception as e:
            log.warning("chart[robust]源=%s 异常 code=%s err=%s", src, code, e)
            continue
    if period in ("week", "month"):
        try:
            log.info("chart[robust]主源失败, 尝试日线聚合 code=%s period=%s", code, period)
            d = _aggregate_kpl_daily_to_period(code, period)
            if d:
                with _CHART_LOCK:
                    _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                log.info("chart[robust]源=aggregate code=%s period=%s 耗时%.0fms",
                         code, period, (time.time() - t0) * 1000)
                return _ensure_latest_period(d, code)
        except Exception as e:
            log.warning("chart[robust]聚合失败 code=%s err=%s", code, e)
    log.error("chart[robust]全部数据源失败 code=%s period=%s", code, period)
    return {}
