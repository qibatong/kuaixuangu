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
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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


def _host_blocked(host):
    ts = _broken_hosts.get(host)
    return ts is not None and time.time() - ts < _HOST_COOLDOWN


def _mark_host_broken(host):
    _broken_hosts[host] = time.time()


def _fetch_yesterday_amount_ths(code):
    """同花顺日K兜底源: 返回昨日成交额(万元); 失败返回 None
    接口: d.10jqka.com.cn/v6/line/hs_{code}/01/last.js (全部历史K线, 含成交额)
    字段: 日期,今开,最高,最低,收盘,成交量(股),成交额(元),换手率...
    """
    today = _bj_date_str().replace("-", "")
    for proto in ("https", "http"):
        url = "%s://d.10jqka.com.cn/v6/line/hs_%s/01/last.js" % (proto, code)
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
            # 最后一根是今天(盘中)则取倒数第二根(昨日), 否则最后一根即最近交易日
            last = segs[-1]
            if len(segs) >= 2 and last.split(",")[0] == today:
                last = segs[-2]
            parts = last.split(",")
            if len(parts) < 7:
                continue
            amt = float(parts[6])   # 成交额(元)
            if not math.isfinite(amt) or amt <= 0:
                continue
            return amt / 10000.0    # 万元
        except Exception:
            continue
    return None


def _fetch_yesterday_amount_one(code):
    """拉单只股票昨日成交额(万元): 东财日K(多域名轮询)失败后自动切同花顺兜底; 都失败返回 None"""
    qs = urllib.parse.urlencode({
        "secid": _secid(code), "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": 101, "fqt": 0, "end": "20500101", "lmt": 2,
    })
    for host in config.KLINE_HOSTS:
        if _host_blocked(host):
            continue
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
            # 取"最近完整交易日"的成交额: 若最后一根是今天, 取倒数第二根(昨日)
            last = klines[-1]
            if len(klines) >= 2 and last.split(",")[0] == _bj_date_str():
                last = klines[-2]
            parts = last.split(",")
            if len(parts) < 7:
                continue
            amt = float(parts[6])   # 成交额(元)
            if not math.isfinite(amt) or amt <= 0:
                continue
            return amt / 10000.0    # 万元
        except Exception:
            _mark_host_broken(host)
            continue
    # 东财全失败 → 同花顺兜底
    return _fetch_yesterday_amount_ths(code)


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
