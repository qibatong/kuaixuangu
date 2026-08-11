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
from concurrent.futures import ThreadPoolExecutor

from ..core import config, logger

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


def fetch_eastmoney(fs):
    """拉取一个市场分区的全部股票快照(至多 200 只, 按涨幅倒序)"""
    qs = urllib.parse.urlencode({
        "fs": fs, "fltt": 2, "invt": 2, "fields": config.FIELDS,
        "fid": "f3", "po": 1, "pn": 1, "pz": 200, "np": 1, "ut": config.EASTMONEY_UT,
    })
    req = urllib.request.Request(config.EASTMONEY_URL + "?" + qs, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        _record("eastmoney_clist", False)
        raise
    _record("eastmoney_clist", True, int((time.time() - t0) * 1000))
    if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
        raise RuntimeError("东方财富接口返回异常")
    diff = data["data"]["diff"]
    log.info("东财行情拉取成功 fs=%s 数量%d", fs, len(diff))
    return diff


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
    返回 (raw, 错误信息); 拉取失败沿用旧缓存(降级不报错)。"""
    with _fetch_lock:
        now = time.time()
        entry = _cache.get(fs)
        if entry is None or now - entry["ts"] > config.SPOT_CACHE_TTL:
            try:
                _cache[fs] = {"raw": fetch_eastmoney(fs), "ts": now}
                log.info("盘中缓存刷新 fs=%s", fs)
            except Exception as e:
                if entry is not None:
                    log.warning("盘中拉取失败, 沿用旧缓存 fs=%s err=%s", fs, e)
                    return entry["raw"], None
                raise
        else:
            log.info("盘中缓存命中 fs=%s 年龄%.0fs", fs, now - entry["ts"])
        return _cache[fs]["raw"], None


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
    """并发获取一批股票的昨日成交额(万元), 带当日缓存; 返回 {code: 金额万元}"""
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
        with ThreadPoolExecutor(max_workers=config.YESTERDAY_FETCH_WORKERS) as ex:
            results = list(ex.map(_fetch_yesterday_amount_one, need))
        ok_cnt = sum(1 for v in results if v is not None)
        fail_cnt = len(need) - ok_cnt
        if fail_cnt:
            log.warning("昨日成交额拉取: 需%d 成功%d 失败%d", len(need), ok_cnt, fail_cnt)
        with _yesterday_lock:
            for c, v in zip(need, results):
                if v is not None:
                    _yesterday_cache[c] = [today, v]
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
