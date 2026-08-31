# -*- coding: utf-8 -*-
"""
数据抓取服务: 东方财富行情拉取 + 分区缓存 + 昨日成交额(日K) + 域名熔断
======================================================================
"""
import json
import math
import re
import sqlite3
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _FutTimeout
import concurrent.futures

from ..core import config, logger
from . import scorer   # 仅复用 parse_float / market_fs (无循环: scorer 不依赖 fetcher)

# 部分行情网关(走代理/自签名)证书校验失败, 仅关校验不关加密
_NO_VERIFY_CTX = ssl.create_default_context()
_NO_VERIFY_CTX.check_hostname = False
_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE

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

# 数据源熔断器: 故障后 _CIRCUIT_OPEN_SECONDS 内直接快速失败, 不等超时(防单 worker 卡死)
# 冷却期过后允许半开探测(一次请求), 成功则恢复, 失败继续熔断
_CIRCUIT_OPEN_SECONDS = 60

# ---------- 数据源健康监控 ----------
# src -> {ok, fail, last_ok, last_fail, ms_sum, ms_cnt, down_since}
# down_since>0 表示自该时刻起处于"故障中"(失败后尚未成功恢复)
_HEALTH = {
    "eastmoney_clist": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "eastmoney_kline": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "eastmoney_zt_pool": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "ths_kline":       {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "tencent_market":  {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
    "tencent_kline":   {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
}
_health_lock = threading.Lock()


def _check_circuit(src="eastmoney_clist"):
    """检查数据源是否熔断中; 熔断时快速失败, 不等超时(防单 worker 卡死雪崩)
    返回 True=熔断中(应快速失败), False=可请求(正常或半开探测)"""
    with _health_lock:
        h = _HEALTH.get(src)
        if not h or not h["down_since"]:
            return False
        if time.time() - h["down_since"] < _CIRCUIT_OPEN_SECONDS:
            return True
    return False


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
            elif now - h["down_since"] >= _CIRCUIT_OPEN_SECONDS:
                # 半开探测失败: 冷却期过后重新熔断
                h["down_since"] = now
                log.error("数据源熔断器半开探测失败, 重新熔断: %s", src)


def _src_status(h):
    """单个数据源状态: ok / degraded / down"""
    if h["down_since"] and h["last_ok"] < h["down_since"]:
        return "down"
    if h["fail"] and h["last_fail"] > h["last_ok"]:
        return "degraded"
    return "ok"


def get_health_status():
    """数据源健康快照: {overall, serviceable, sources:{src:{status,ok,fail,last_ok,last_fail,avg_ms}}}
    - overall:     ok(全部正常) / degraded(部分故障, 可能由兜底源覆盖) / down(全部故障)
    - serviceable: True=任一全市场行情源(eastmoney_clist/tencent_market)可用, 服务可继续响应
                   2026-08-30 可观测性补充: 东财故障腾讯顶班时, 运维一眼确认服务可用性"""
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
        # 全市场行情主链可用性: 东财 clist 或腾讯 tencent_market 任一能服务即算可服务
        market_srcs = ["eastmoney_clist", "tencent_market"]
        serviceable = any(sources.get(s, {}).get("status") != "down" for s in market_srcs)
    return {"overall": overall, "serviceable": serviceable, "sources": sources}


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
    # 服务器缺 CA 证书 → clash 校验失败; 用 unverified context(保留TLS加密), 否则竞价全市场快照全挂
    with urllib.request.urlopen(req, timeout=10, context=_NO_VERIFY_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
        raise RuntimeError("东方财富接口返回异常")
    return data["data"]["diff"]


def fetch_eastmoney(fs):
    """拉取一个市场分区的全部股票快照(至多 200 只, 按涨幅倒序)"""
    if _check_circuit():
        raise RuntimeError("东财数据源熔断中(故障冷却%d秒内), 快速失败" % _CIRCUIT_OPEN_SECONDS)
    t0 = time.time()
    try:
        diff = _fetch_clist_page(fs, 1)
    except Exception as e:
        _record("eastmoney_clist", False)
        raise
    _record("eastmoney_clist", True, int((time.time() - t0) * 1000))
    log.info("东财行情拉取成功 fs=%s 数量%d", fs, len(diff))
    return diff


# ==================== 腾讯行情兜底源 (2026-08-30 主人要求: 东财被墙时用其他源采集) ====================
# 东财在生产机被墙(Remote end closed), 腾讯 qt.gtimg.cn 畅通 → 作为全市场行情兜底。
# 接口: https://qt.gtimg.cn/q=sh600519,sz000001,...  (GBK 编码, ~ 分隔 88 字段)
# 字段(下标从1起): [1]名称 [2]代码 [3]现价 [4]昨收 [5]今开 [32]涨跌% [36]成交量(手)
#   [37]成交额(万) [38]换手率 [44]流通市值(亿) [45]总市值(亿) [47]涨停价
TENCENT_URL = "https://qt.gtimg.cn/q="
_TENCENT_BATCH = 300       # 每批拉 300 只(实测 600 只 190ms, 300 稳妥防超长 URL)
_TENCENT_CODES_CACHE = {"ts": 0, "codes": []}   # 全市场代码清单缓存(当日)
_TENCENT_CODES_TTL = 12 * 3600


def _tencent_symbol(code):
    """股票代码 → 腾讯符号: 沪(6/9)sh / 深(0/3)sz / 北(4/8)bj"""
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def _all_market_codes():
    """全市场股票代码清单(当日缓存): 优先读快选 snapshot_bid 最近一日全量代码;
    无快照时用东财缓存 raw 的 f12。"""
    now = time.time()
    if _TENCENT_CODES_CACHE["codes"] and now - _TENCENT_CODES_CACHE["ts"] < _TENCENT_CODES_TTL:
        return _TENCENT_CODES_CACHE["codes"]
    codes = []
    # 1) snapshot_bid 最近一日全市场(权威, 含北交所)
    try:
        conn = sqlite3.connect(config.DB_FILE)
        row = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE time_point='9_25'").fetchone()
        if row and row[0]:
            rows = conn.execute(
                "SELECT DISTINCT code FROM snapshot_bid WHERE date=? AND time_point='9_25'",
                (row[0],)).fetchall()
            codes = [r[0] for r in rows if r[0]]
        conn.close()
    except Exception:
        pass
    # 2) 兜底: 任何缓存 raw 的 f12
    if not codes:
        seen = set()
        for entry in _cache.values():
            for s in entry.get("raw") or []:
                c = s.get("f12")
                if c and c not in seen:
                    seen.add(c)
                    codes.append(c)
    codes = sorted(set(codes))
    if codes:
        _TENCENT_CODES_CACHE["codes"] = codes
        _TENCENT_CODES_CACHE["ts"] = now
    return codes


def _fetch_tencent_batch(symbols):
    """腾讯单批拉取: symbols 形如 ['sh600519', ...]; 返回 {code: fields_list}; 失败抛异常"""
    url = TENCENT_URL + ",".join(symbols)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    with urllib.request.urlopen(req, timeout=15, context=_NO_VERIFY_CTX) as resp:
        body = resp.read().decode("gbk", "ignore")
    out = {}
    for line in body.split("\n"):
        m = re.search(r'v_([a-z]{2}\d{6})="(.*)"', line)
        if not m:
            continue
        code = m.group(1)[2:]
        fields = m.group(2).split("~")
        if len(fields) > 47:
            out[code] = fields
    return out


def fetch_tencent_market(fs):
    """腾讯全市场行情兜底: 以全市场代码清单分批发拉 → 映射为东财 diff 格式
    (f2现价/f3涨跌%/f8换手/f12代码/f14名称/f21流通市值/f615竞价涨幅≈f3/
     f616竞价额≈成交额/f617量≈成交量/f630异动=0), 返回与 fetch_eastmoney 同构的列表。
    竞价专属字段(f615/f616/f617)腾讯无精确值, 用现价涨幅/成交额近似(盘中口径一致)。
    2026-08-31 加速: 原串行 19 批(5500/300) + 每批 sleep0.1 ≈ 5-18s, 改 5 并发 ≈ 1-2s
    (qt.gtimg.cn 批量接口抗并发, 东财故障兜底时快照/选股时点更准)"""
    if _check_circuit("tencent_market"):
        raise RuntimeError("腾讯数据源熔断中(故障冷却%d秒内), 快速失败" % _CIRCUIT_OPEN_SECONDS)
    codes = _all_market_codes()
    if not codes:
        raise RuntimeError("无可用代码清单, 腾讯兜底无法执行")
    t0 = time.time()
    symbols = [_tencent_symbol(c) for c in codes]
    result = []

    def _grab(batch):
        """单批拉取并解析为东财 diff 格式; 失败返回空(单批失败跳过, 不影响其他批)"""
        out = []
        try:
            got = _fetch_tencent_batch(batch)
        except Exception:
            return out
        for code, f in got.items():
            try:
                mv_yi = float(f[44])          # 流通市值(亿)
                price = float(f[3])
                chg = float(f[32])            # 涨跌%
                turnover = float(f[38])       # 换手率
                amt_wan = float(f[37])        # 成交额(万)
                vol_hand = float(f[36])        # 成交量(手)
            except (ValueError, IndexError):
                continue
            out.append({
                "f2": price, "f3": chg, "f8": turnover,
                "f12": code, "f14": f[1],
                "f21": mv_yi * 1e8,            # 元
                "f615": chg,                    # 竞价涨幅(近似)
                "f616": amt_wan * 1e4,          # 竞价金额(元, 近似成交额)
                "f617": vol_hand * 100,         # 竞价量(股, 近似成交量)
                "f630": 0,
            })
        return out

    batches = [symbols[i:i + _TENCENT_BATCH] for i in range(0, len(symbols), _TENCENT_BATCH)]
    try:
        # 5 并发拉批(实测 qt.gtimg.cn 批量抗并发, 5500 只 19 批 ≈ 1-2s)
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=5) as ex:
            for chunk in ex.map(_grab, batches):
                result.extend(chunk)
    except Exception as e:
        _record("tencent_market", False, int((time.time() - t0) * 1000))
        raise
    _record("tencent_market", True, int((time.time() - t0) * 1000))
    log.info("腾讯兜底行情拉取成功 代码%d只 返回%d只 耗时%.0fms", len(codes), len(result),
             (time.time() - t0) * 1000)
    return result


def fetch_eastmoney_all(fs):
    """盘中实时模式: 分页拉取全市场股票快照(默认每页 200, 共 ~20-30 页),
    让过滤参数(涨幅/量比/换手)真正作用于全市场, 而不是只取涨幅前 200。
    按代码(f12)排序分页: 位置稳定, 任一分页失败只跳过该页, 不漏已跌出榜单的票。
    并发拉取(2026-08-19 起): 30 页 ThreadPoolExecutor 并发, 实测 386ms/次(2026-08-31 生产);
    空页=到底(提前结束), 任一页失败跳过该页, 全部失败抛异常。
    注意: 18-28s 级耗时仅出现在东财故障走腾讯全市场兜底时(串行已改 5 并发 ≈ 1-2s)。"""
    if _check_circuit():
        raise RuntimeError("东财数据源熔断中(故障冷却%d秒内), 快速失败" % _CIRCUIT_OPEN_SECONDS)
    t_all = time.time()
    # 先并发拉前 N 页, 根据空页/短页判定真实页数
    pages_data = {}   # page -> diff list(失败/空为 None)
    # 2026-08-31 线上加固: as_completed 无整体超时, 东财半死(每页 15s 超时)时
    # 30 页 8 并发最坏 ~56s, 腾讯兜底再来一轮会拖死 worker → 整体 40s, 超时放弃剩余页
    ex = ThreadPoolExecutor(max_workers=8)
    futs = {ex.submit(_fetch_clist_page, fs, p, "f12"): p
            for p in range(1, config.SPOT_MAX_PAGES + 1)}
    try:
        for fut in as_completed(futs, timeout=40):
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
    except TimeoutError:
        log.warning("全市场分页整体超时 40s, 放弃未完成页 (数据源抖动, 走腾讯兜底/部分数据)")
    finally:
        ex.shutdown(wait=False)
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
        raw = _fetch_market_all_with_fallback(scorer.market_fs(["hs", "cyb", "kcb", "bj"]))
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


def _fetch_market_with_fallback(fs):
    """全市场行情容灾(2026-08-30 主人要求): 东财失败自动切腾讯兜底。
    腾讯无 f615/f616/f617 竞价专属字段, 用现价涨幅/成交额/成交量近似(盘中口径一致)。
    东财与腾讯都失败时抛异常。"""
    try:
        return fetch_eastmoney(fs)
    except Exception as e:
        log.warning("东财拉取失败, 切换腾讯兜底 fs=%s err=%s", fs, str(e)[:120])
        try:
            rows = fetch_tencent_market(fs)
            # 2026-08-30 可观测性: 兜底成功后留成功痕迹(否则只有 warning 无结果确认)
            log.info("腾讯兜底成功 fs=%s 返回%d只 (东财故障时自动切换)", fs, len(rows))
            return rows
        except Exception as e2:
            log.error("腾讯兜底也失败 fs=%s err=%s", fs, str(e2)[:120])
            raise RuntimeError("全市场行情数据源全部失败(东财+腾讯): %s" % str(e)[:80])


def _fetch_market_all_with_fallback(fs):
    """2026-08-30 容灾加固(主人反馈用户截图): fetch_eastmoney_all 的腾讯兜底版。
    覆盖 ensure_spot_cache 外的路径(351/497/auction_snapshot), 防止熔断异常冒到前端。"""
    try:
        return fetch_eastmoney_all(fs)
    except Exception as e:
        log.warning("东财全市场拉取失败, 切换腾讯兜底 fs=%s err=%s", fs, str(e)[:120])
        try:
            rows = fetch_tencent_market(fs)
            # 2026-08-30 可观测性: 兜底成功后必须留成功痕迹, 否则日志只有 warning 没有结果
            log.info("腾讯全市场兜底成功 fs=%s 返回%d只 (东财故障时自动切换)", fs, len(rows))
            return rows
        except Exception as e2:
            log.error("腾讯全市场兜底也失败 fs=%s err=%s", fs, str(e2)[:120])
            raise RuntimeError("全市场分页数据源全部失败(东财+腾讯): %s" % str(e)[:80])


def ensure_cache(action, fs, before930):
    """在锁内保证缓存可用且新鲜, 返回 (raw, 错误信息)。
    - lock:    9:30 前强制重新拉取(锁定期权)
    - refresh: 缓存过期(超过 CACHE_TTL 秒)才重新拉取
    - filter:  无缓存时拉取一次
    2026-08-30 容灾(主人要求): 东财失败自动切腾讯兜底(腾讯无 f615 竞价字段, 用近似)
    """
    with _fetch_lock:
        now = time.time()
        if action == "lock":
            if not before930:
                return None, "9:30 后禁止重新选股"
            _cache[fs] = {"raw": _fetch_market_with_fallback(fs), "ts": now}
            log.info("缓存锁定 fs=%s", fs)
        elif action == "refresh":
            entry = _cache.get(fs)
            if entry is None or now - entry["ts"] > config.CACHE_TTL:
                _cache[fs] = {"raw": _fetch_market_with_fallback(fs), "ts": now}
                log.info("缓存刷新 fs=%s", fs)
            else:
                log.info("缓存命中 fs=%s 年龄%.0fs", fs, now - entry["ts"])
        else:  # filter
            if fs not in _cache:
                _cache[fs] = {"raw": _fetch_market_with_fallback(fs), "ts": now}
                log.info("缓存初建 fs=%s", fs)
            else:
                log.info("缓存命中 fs=%s", fs)
        return _cache[fs]["raw"], None


def ensure_spot_cache(action, fs, before930):
    """盘中实时模式缓存: 不受 9:30 限制, 缓存新鲜度用 SPOT_CACHE_TTL。
    拉取全市场(分页), 返回 (raw, 错误信息); 拉取失败沿用旧缓存(降级不报错)。
    2026-08-30 容灾: 东财失败自动切腾讯兜底(fetch_tencent_market 已映射为东财 diff 结构)。"""
    with _fetch_lock:
        now = time.time()
        entry = _cache.get(fs)
        if entry is None or now - entry["ts"] > config.SPOT_CACHE_TTL:
            try:
                _cache[fs] = {"raw": fetch_eastmoney_all(fs), "ts": now}
                log.info("盘中全市场缓存刷新 fs=%s", fs)
            except Exception as e:
                log.warning("东财盘中拉取失败, 切换腾讯兜底 fs=%s err=%s", fs, str(e)[:120])
                try:
                    _cache[fs] = {"raw": fetch_tencent_market(fs), "ts": now}
                    log.info("腾讯兜底盘中全市场刷新 fs=%s", fs)
                except Exception as e2:
                    log.error("腾讯盘中兜底也失败 fs=%s err=%s", fs, str(e2)[:120])
                    if entry is not None:
                        log.warning("盘中双源失败, 沿用旧缓存 fs=%s", fs)
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
                raw = _fetch_market_all_with_fallback(fs)
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


def _fetch_yesterday_amount_tencent(code):
    """腾讯日K兜底源(第三源, 2026-08-31 东财K线被生产机IP封禁后新增):
    返回最近两交易日成交额 [T日, T-1日] 万元; 失败返回 None
    接口: proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get
    qfqday 行: [date, open, close, high, low, volume, {}, 涨跌, 成交额(万元), '']
    跳过今天(未收盘)行, 保证 T = 最近已收盘交易日(与东财/同花顺语义一致)
    """
    prefix = "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
    secid = prefix + code
    t0 = time.time()
    try:
        url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param="
               + urllib.parse.quote(secid) + ",day,,,8,qfq")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        data = raw.get("data", {}).get(secid, {})
        rows = data.get("qfqday") or data.get("day") or []
        today = _bj_date_str().replace("-", "")
        pairs = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 9:
                continue
            dstr = str(row[0])[:10].replace("-", "")
            if dstr == today:
                continue  # 今天未收盘, 跳过
            try:
                amt = float(row[8])
            except (TypeError, ValueError):
                continue
            if math.isfinite(amt) and amt > 0:
                pairs.append((dstr, amt))
        if len(pairs) >= 2:
            _record("tencent_kline", True, int((time.time() - t0) * 1000))
            return [pairs[-1][1], pairs[-2][1]]
    except Exception as e:
        log.warning("腾讯日K成交额拉取失败 code=%s err=%s", code, str(e)[:100])
    _record("tencent_kline", False)
    return None


def _fetch_yesterday_amount_one(code):
    """拉单只股票最近两交易日成交额(万元): 返回 [T日, T-1日] (T=最近已收盘交易日);
    东财日K(多域名轮询)失败后自动切同花顺兜底; 完全失败返回 None

    2026-08-30 容灾加固(主人反馈用户中午盘中截图): 当东财日 K 与 ths_kline
    全部处于熔断中, 立即 return None 不浪费 5s×多 host 超时(5554 只全量会卡到 nginx 504)"""
    # 快速短路: 三源(东财/同花顺/腾讯日K)都熔断中 → 立即跳过(昨比对该 code 置空, 评分时容忍缺失)
    # 2026-08-31: 去掉逐只 WARNING(36804 条日志风暴拖死 worker), 聚合统计在 fetch_yesterday_amounts 批级短路处理
    if (_check_circuit("eastmoney_kline") and _check_circuit("ths_kline")
            and _check_circuit("tencent_kline")):
        return None
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
    # 东财全失败 → 同花顺兜底 → 腾讯日K兜底(2026-08-31: 东财K线被IP封禁, 腾讯作第三源)
    v = _fetch_yesterday_amount_ths(code)
    if v is not None:
        return v
    return _fetch_yesterday_amount_tencent(code)


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
        # 2026-08-31 线上事故: 全源熔断时逐只短路打 WARNING → 36804 条日志风暴,
        # 日志 I/O 阻塞 worker 导致 /api/stocks 674s、health 超时。改为批级短路: 一条聚合日志 + 直接返回
        # 三源(东财/同花顺/腾讯日K)全部熔断才短路; 任一源可用则继续尝试(腾讯作第三源)
        if (_check_circuit("eastmoney_kline") and _check_circuit("ths_kline")
                and _check_circuit("tencent_kline")):
            log.warning("昨日成交额: 东财+同花顺+腾讯 三源全部熔断中, 本批%d只全部短路(昨比置空)", len(need))
            return {}
        ok_cnt = 0
        fail_cnt = len(need)
        # 2026-08-31: shutdown(wait=False) — 整体超时后不再等待慢 code 线程(原 with 块 wait=True 仍会阻塞)
        ex = ThreadPoolExecutor(max_workers=config.YESTERDAY_FETCH_WORKERS)
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
        except _FutTimeout:
            # 超时未完成: 跳过(不等待慢 code), 昨比对该 code 置空
            done = len(need) - fail_cnt
            log.warning("昨日成交额拉取超时(%ds) 已完成%d/%d, 超时跳过",
                        config.YESTERDAY_FETCH_TIMEOUT, done, len(need))
            fail_cnt = len(need) - ok_cnt
        finally:
            ex.shutdown(wait=False)
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
        with urllib.request.urlopen(req, timeout=10, context=_NO_VERIFY_CTX) as resp:
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


def _trim_minute_to_now(res):
    """分时图规范化: 盘中(工作日9:30-15:00)用全天交易时间网格(09:30-11:30 / 13:00-15:00)对齐,
    把"尚未交易到"的时段(当日下午、午休之后的未来分钟)数值置空(None), 使分时线只画到当前时刻、
    未交易位置留白(不再因类目轴把已有点拉满整宽)。
    盘前 / 收盘后 / 周末: 不改动(来源已给全天)。"""
    try:
        now = time.gmtime(time.time() + 8 * 3600)        # 当前北京时间
        if now.tm_wday >= 5:
            return res
        mins = now.tm_hour * 60 + now.tm_min
        if not (9 * 60 + 30 <= mins <= 15 * 60 + 1):     # 非 9:30-15:00: 全天原样
            return res
        if not isinstance(res, dict):
            return res
        src_times = res.get("time") or []
        src_price = res.get("price") or []
        src_avg = res.get("avg") or []
        src_vol = res.get("volume") or []
        if not src_times:
            return res
        # 全天交易时间网格 (类目轴固定长度, 未交易段置空 → 右侧留白)
        grid = []
        for hm in range(0, 121):                          # 09:30..11:30
            tt = 9 * 60 + 30 + hm
            grid.append("%02d:%02d" % (tt // 60, tt % 60))
        for hm in range(0, 121):                          # 13:00..15:00
            tt = 13 * 60 + hm
            grid.append("%02d:%02d" % (tt // 60, tt % 60))
        idx = {str(t)[:5]: i for i, t in enumerate(src_times)}
        now_hm = "%02d:%02d" % (now.tm_hour, now.tm_min)
        times, prices, avgs, vols = [], [], [], []
        for g in grid:
            times.append(g)
            if g <= now_hm and g in idx:
                i = idx[g]
                prices.append(src_price[i] if i < len(src_price) else None)
                avgs.append(src_avg[i] if i < len(src_avg) else None)
                vols.append(src_vol[i] if i < len(src_vol) else None)
            else:
                prices.append(None); avgs.append(None); vols.append(None)
        res["time"] = times
        res["price"] = prices
        res["avg"] = avgs
        res["volume"] = vols
        return res
    except Exception:
        return res


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
            result = _trim_minute_to_now(result)
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
    from datetime import date
    # 传 end_date=今天: 让 weekly/monthly 返回最近一周/本月(含至今), 而非止于上一完整周期
    params = {"ts_code": ts_code, "end_date": date.today().strftime("%Y%m%d")}
    try:
        qs = urllib.parse.urlencode(params)
        url = config.TUSHARE_BASE_URL + path + "?" + qs
        req = urllib.request.Request(url, headers={
            "X-API-Key": config.TUSHARE_API_KEY, "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=15, context=_NO_VERIFY_CTX) as resp:
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
    # 复权错乱检测: 替代 max>min*100 / ths 高价特判(二者会误伤长期高价股的完整K线,
    # 如茅台周K从 ~21 到 ~2600 跨多个除息周期)。仅当某根相对前一根出现数量级突变才判异常
    prev = None
    for c in valid_closes:
        if prev is not None and c > 0:
            if c > prev:
                if c / prev > 1000:
                    return False
            else:
                if prev / c > 1000:
                    return False
        prev = c
    volumes = data.get("volume") or []
    valid_vols = [v for v in volumes if v and v > 0]
    if not valid_vols and period != "minute":
        return False
    return True


def _fetch_minute_from_tencent(secid, code):
    """腾讯当日分时(web.ifzq.gtimg.cn/appstock/app/minute/query)
    每项 "HHMM 价格 累计成交量(手) 累计成交额(元)"; 均价=累计额/累计量(股)
    返回 {period:'minute', code, time:[HH:MM], price[], avg[], volume[], preClose, name}
    """
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + secid
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        })
        with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        d = raw.get("data", {}).get(secid, {}).get("data", {})
        rows = d.get("data") if isinstance(d, dict) else None
        if not isinstance(rows, list):
            return {}
        times, prices, avgs, volumes = [], [], [], []
        for r in rows:
            try:
                p = str(r).split()
                if len(p) < 4:
                    continue
                hm = p[0]
                if len(hm) == 4 and hm.isdigit():
                    hm = hm[:2] + ":" + hm[2:]
                price = float(p[1])
                cumvol = float(p[2])       # 手
                cumamt = float(p[3])       # 元
                times.append(hm)
                prices.append(price)
                volumes.append(cumvol)
                shares = cumvol * 100.0
                avgs.append(round(cumamt / shares, 3) if shares > 0 else price)
            except (TypeError, ValueError, IndexError):
                continue
        if not times:
            return {}
        # preClose/name 用腾讯实时行情补充(失败则为0)
        preClose = 0
        q = _fetch_quote_tencent(code)
        if q:
            preClose = q["preclose"]
        return {"period": "minute", "code": code,
                "time": times, "price": prices, "avg": avgs, "volume": volumes,
                "preClose": preClose, "name": ""}
    except Exception as e:
        log.warning("腾讯分时拉取失败 code=%s err=%s", code, e)
        return {}


def _fetch_chart_from_tencent(code, period="day"):
    """腾讯K线接口(web.ifzq.gtimg.cn): day/week/month + 分时 minute
    实时含当前周期; minute 走 _fetch_minute_from_tencent
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    secid = prefix + code
    if period == "minute":
        return _fetch_minute_from_tencent(secid, code)
    kp = {"day": "day", "week": "week", "month": "month"}.get(period)
    if not kp:
        return {}
    count = {"day": 200, "week": 700, "month": 300}.get(period, 200)
    try:
        url = ("https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param="
               + urllib.parse.quote(secid) + "," + kp + ",,," + str(count))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        })
        with urllib.request.urlopen(req, timeout=config.KLINE_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        data = raw.get("data", {}).get(secid, {})
        if not data:
            return {}
        rows = data.get(kp)
        # 兼容 data = {"qfq": {"week": [...]}} 的嵌套结构(直接结构取不到时尝试 qfq 嵌套)
        if not isinstance(rows, list):
            for rk in ("qfq", kp):
                rr = data.get(rk)
                if isinstance(rr, dict):
                    rows = rr.get("day") or rr.get(kp)
                    if rows:
                        break
        if not isinstance(rows, list) or not rows:
            return {}
        times, opens, closes, highs, lows, volumes = [], [], [], [], [], []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                times.append(str(row[0]))
                opens.append(float(row[1]))
                closes.append(float(row[2]))
                highs.append(float(row[3]))
                lows.append(float(row[4]))
                volumes.append(float(row[5]))
            except (TypeError, ValueError):
                continue
        if not times:
            return {}
        preClose = closes[-2] if len(closes) >= 2 else (closes[0] if closes else 0)
        return {"period": period, "code": code,
                "time": times, "open": opens, "close": closes,
                "high": highs, "low": lows,
                "volume": volumes, "amount": [],
                "preClose": preClose, "name": ""}
    except Exception as e:
        log.warning("腾讯K线拉取失败 code=%s period=%s err=%s", code, period, e)
        return {}


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
    # 分时: 不做 K 线补期, 只做"未交易时段置空"(盘中只显示到当前时刻, 避免画满整天)
    if period == "minute":
        return _trim_minute_to_now(data)
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
        # 盘中日K最后一根是"今日实时", 需高频刷新: minute 60s / day 120s; 周K/月K变化慢仍 30min
        ttl = 60 if period == "minute" else 120 if period == "day" else 1800
        if ent and time.time() - ent["ts"] < ttl:
            return ent["data"]
    t0 = time.time()
    sources = ["eastmoney", "tencent"]
    if period != "minute":
        sources.append("tushare")
        sources.append("ths")
    sources.append("kpl")
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
            elif src == "tencent":
                d = _fetch_chart_from_tencent(code, period)
                if d and _validate_chart_data(d, period, source="tencent"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=tencent code=%s period=%s 耗时%.0fms",
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
