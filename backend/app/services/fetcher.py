# -*- coding: utf-8 -*-
"""
数据抓取服务: 东方财富行情拉取 + 分区缓存 + 昨日成交额(日K) + 域名熔断
======================================================================
"""
import contextlib
import json
import math
import re
import socket as _socket_mod
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
from .cache_store import store   # 2026-09-04: 两市概况改跨进程缓存(无循环: cache_store 只依赖 core)

# 部分行情网关(走代理/自签名)证书校验失败, 仅关校验不关加密
_NO_VERIFY_CTX = ssl.create_default_context()
_NO_VERIFY_CTX.check_hostname = False
_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE

# ---------- 出站 IP 轮询池(2026-09-07 主人加辅助网卡防东财封单 IP) ----------
# 配置: config.OUTBOUND_IPS(逗号分隔, 默认空走 OS 默认)
# 行为: 每次请求严格 RR 轮询(请求1→IP A, 请求2→IP B, ...); 某 IP 连续失败
#       _IP_FAIL_THRESHOLD 次进入"惩罚"(后续请求跳过它, 在健康 IP 间轮询),
#       成功一次即恢复。monkey-patch socket.create_connection 注入 source_address。
#       _ip_binding() context manager 负责取 IP+报告; _http_get() 包装 urlopen。
# RR index 进程级共享锁; bind_ip 用 thread-local(仅本次 urlopen, 出 context 清空)。
_IP_LOCAL = threading.local()
_IP_FAIL_THRESHOLD = 2


class _IPRotator:
    """IP 轮询: 进程级严格 RR(每次 acquire 切下一个) + 失败惩罚(连续失败 N 次跳过)。
    report_success/report_fail 需传入实际使用的 ip(每请求独立, 无跨请求状态)。"""

    def __init__(self, ips):
        self.ips = list(ips) if ips else []
        self._rr_lock = threading.Lock()
        self._rr_index = -1
        self._penalty = {}   # ip -> 连续失败次数

    def acquire(self):
        """严格 RR: 每次调用切下一个 IP(跳过惩罚中 IP); 全部被惩罚则放行 RR 下一个。"""
        if not self.ips:
            return None
        n = len(self.ips)
        with self._rr_lock:
            for _ in range(n):
                self._rr_index = (self._rr_index + 1) % n
                ip = self.ips[self._rr_index]
                if self._penalty.get(ip, 0) < _IP_FAIL_THRESHOLD:
                    return ip
            # 全部 IP 都在惩罚中 → 放行(保证有 IP 可用, 不硬卡请求)
            return self.ips[self._rr_index]

    def report_success(self, ip):
        if ip in self._penalty:
            self._penalty.pop(ip, None)

    def report_fail(self, ip):
        self._penalty[ip] = self._penalty.get(ip, 0) + 1


_IP_ROTATOR = _IPRotator(config.OUTBOUND_IPS)

# monkey-patch socket.create_connection: 读 thread-local 绑定 IP 注入 source_address
# 覆盖 socket 模块本身 + urllib.request.socket(后者是同一引用, 双保险)
_orig_create_connection = _socket_mod.create_connection


def _patched_create_connection(address, timeout=None, source_address=None, **kw):
    ip = getattr(_IP_LOCAL, "bind_ip", None)
    if ip:
        source_address = (ip, 0)
    return _orig_create_connection(address, timeout, source_address, **kw)


_socket_mod.create_connection = _patched_create_connection
urllib.request.socket.create_connection = _patched_create_connection


@contextlib.contextmanager
def _ip_binding():
    """带 IP 轮询的 urlopen 上下文管理器。
    取 IP(严格 RR)绑 thread-local; 成功 report_success(ip), 失败 report_fail(ip)。
    无 IP 池时为 no-op。"""
    if not _IP_ROTATOR.ips:
        yield
        return
    ip = _IP_ROTATOR.acquire()
    _IP_LOCAL.bind_ip = ip
    try:
        yield
        _IP_ROTATOR.report_success(ip)
    except Exception:
        _IP_ROTATOR.report_fail(ip)
        raise
    finally:
        _IP_LOCAL.bind_ip = None


def _http_get(req, timeout, context=None):
    """带 IP 轮询的 urlopen 包装(替换 fetcher 内所有 urllib.request.urlopen)。"""
    with _ip_binding():
        return urllib.request.urlopen(req, timeout=timeout, context=context)

log = logger.get_logger(__name__)

# ---------- 按市场范围(fs)分区的行情缓存 ----------
# fs -> {"raw": [...], "ts": epoch}; 锁只保护"拉数据/更新缓存", 评分计算不持锁
_cache = {}
_fetch_lock = threading.Lock()

# 昨日全天成交额(万元): code -> [缓存日期, 金额(pair) 或 None(失败), 写入时间戳], 当日有效
_yesterday_cache = {}
_yesterday_lock = threading.Lock()
# 昨比批量拉取互斥(2026-09-02 生产事故): 并发请求同时进 need 判定都判定为空 → 各自全量拉取
# (14s 内 5 次全量 5000 只), 失败缓存只挡串行挡不住并发。批锁: 同时只允许一个全量拉取,
# 其余请求直接返回当前缓存(可能为空), 避免并发重复打爆数据源。
_yday_batch_lock = threading.Lock()

# 域名熔断: 请求失败/被限流时冷却, 避免反复重试拖慢响应
_broken_hosts = {}
_HOST_COOLDOWN = 300   # 冷却 5 分钟

# 数据源熔断器: 故障后 _CIRCUIT_OPEN_SECONDS 内直接快速失败, 不等超时(防单 worker 卡死)
# 冷却期过后允许半开探测(一次请求), 成功则恢复, 失败继续熔断
_CIRCUIT_OPEN_SECONDS = 60
# 连续失败指数退避上限: 确定性故障(如东财K线接口被风控秒拒)60→120→240→480→600s,
# 避免每 60s 半开探测刷日志(2026-09-01 生产 8/30 起 eastmoney_kline 3316 条故障日志)
_CIRCUIT_MAX_COOLDOWN = 600

# ---------- 数据源健康监控 ----------
# src -> {ok, fail, last_ok, last_fail, ms_sum, ms_cnt, down_since}
# down_since>0 表示自该时刻起处于"故障中"(失败后尚未成功恢复)
# cooldown: 当前熔断冷却秒(连续失败指数退避); base_cooldown: 恢复后重置的基准冷却
# down_threshold: 连续失败多少次才真正熔断(抖动保护, ths/tencent=2, 量脉限流=3, 其余=1)
# fails_in_row: 连续失败计数(成功清零)
_HEALTH = {
    "eastmoney_clist": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "eastmoney_kline": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "eastmoney_zt_pool": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                          "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "ths_kline":       {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 30, "base_cooldown": 30, "down_threshold": 2, "fails_in_row": 0},
    "tencent_market":  {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "tencent_kline":   {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 30, "base_cooldown": 30, "down_threshold": 2, "fails_in_row": 0},
    "liangmai_kline":  {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 3, "fails_in_row": 0},
}
_health_lock = threading.Lock()

# 进程级共享线程池(2026-09-01 生产线程爆炸修复): 全市场分页/昨比 原每次请求新建
# ThreadPoolExecutor + shutdown(wait=False) — 整体超时后线程仍滞留后台跑网络超时
# (昨比单只链路最长 ~30-60s), 高并发选股请求下线程只增不减(实测 2 worker × 2000+
# 线程, 内存耗尽 + 负载 19 拖死生产, 全站 nginx upstream timeout)。
# 改常驻池: 线程数有界(8+8), 超时放弃的任务留池内排队, 不阻塞请求也不新建线程。
_EXECUTOR_CLIST = ThreadPoolExecutor(max_workers=8, thread_name_prefix="kf-clist")
# 2026-09-02 生产事故: 昨比并发 8 在高并发选股下持续打爆东财/同花顺K线(2h 失败 3万次),
# 每请求重复拉 3950 只 → /api/stocks 20-43s。降为 4 线程, 配合失败缓存(见 fetch_yesterday_amounts)
_EXECUTOR_YDAY = ThreadPoolExecutor(max_workers=config.YESTERDAY_FETCH_WORKERS,
                                    thread_name_prefix="kf-yday")

# 腾讯兜底缺票告警限频(2026-09-03): 东财封锁期腾讯全市场兜底为常态, 稳定缺 ~8 只(0.14%)
# 若每次拉取都打 ERROR 会每 20-30s 刷一条(实测单日 676 条, 占 ERROR 总量 72%), 淹没真实故障。
# 语义: 缺票状态(缺票数+失败批)变化时即时 ERROR; 同状态 10 分钟最多 1 条 WARNING 汇总;
# 完全恢复(缺票=0 且失败批=空)后重置, 下次缺票视为新变化重新 ERROR。
_TENCENT_MISS_ALERT_INTERVAL = 600   # 同状态缺票 10 分钟最多报 1 条
_tencent_miss_state = {"ts": 0.0, "key": None, "repeat": 0}


def _miss_alert_decision(state, sig, now):
    """腾讯兜底缺票告警限频判定(纯函数, 便于单测)。
    sig: (缺票数, tuple(重试后仍失败批大小, 已排序))
    返回动作字符串(同时原地更新 state{ts, key, repeat}):
      'error'       — 状态变化或首次缺票 → 调用方立即按严重度打 ERROR/WARNING
      'repeat_warn' — 同状态且距上次告警 >= interval → 打一条 WARNING 汇总(证明仍在刷, 但限频)
      'silent'      — 同状态且限频内 → 静默只计数, 不产生日志"""
    changed = sig != state["key"]
    if changed:
        state.update(ts=now, key=sig, repeat=1)
        return "error"
    state["repeat"] += 1
    if now - state["ts"] >= _TENCENT_MISS_ALERT_INTERVAL:
        state["ts"] = now
        return "repeat_warn"
    return "silent"


def _check_circuit(src="eastmoney_clist"):
    """检查数据源是否熔断中; 熔断时快速失败, 不等超时(防单 worker 卡死雪崩)
    返回 True=熔断中(应快速失败), False=可请求(正常或半开探测)"""
    with _health_lock:
        h = _HEALTH.get(src)
        if not h or not h["down_since"]:
            return False
        if time.time() - h["down_since"] < h.get("cooldown", _CIRCUIT_OPEN_SECONDS):
            return True
    return False


def _record(src, ok, ms=0):
    """记录一次数据源调用结果; 状态翻转时打告警/恢复日志
    2026-09-01 加固: ①抖动保护 down_threshold>1 的源(ths/tencent/量脉)连续失败
    达阈值才熔断, 单次抖动不误伤; ②连续失败指数退避 cooldown(上限600s), 确定性
    故障(如东财K线秒拒)不再每 60s 空转探测刷日志"""
    with _health_lock:
        h = _HEALTH[src]
        now = time.time()
        if ok:
            h["ok"] += 1
            h["last_ok"] = now
            if ms > 0:
                h["ms_sum"] += ms
                h["ms_cnt"] += 1
            h["fails_in_row"] = 0
            h["cooldown"] = h.get("base_cooldown", _CIRCUIT_OPEN_SECONDS)
            if h["down_since"]:
                log.info("数据源恢复: %s 恢复正常(故障%.0f秒)", src, now - h["down_since"])
                h["down_since"] = 0
        else:
            h["fail"] += 1
            h["last_fail"] = now
            h["fails_in_row"] += 1
            threshold = h.get("down_threshold", 1)
            if h["fails_in_row"] < threshold:
                # 抖动保护: 未达阈值只计数不熔断
                log.warning("数据源抖动: %s 连续失败%d/%d 次(暂不熔断)", src, h["fails_in_row"], threshold)
                return
            # 每次失败按连续次数指数退避冷却(上限600s); 冷却期内不重置 down_since, 但冷却时长持续拉长
            h["cooldown"] = min(_CIRCUIT_OPEN_SECONDS * (2 ** (h["fails_in_row"] - 1)), _CIRCUIT_MAX_COOLDOWN)
            if not h["down_since"]:
                h["down_since"] = now
                log.error("数据源故障: %s 调用失败, 进入异常状态(冷却%d秒)", src, h["cooldown"])
            elif now - h["down_since"] >= h.get("cooldown", _CIRCUIT_OPEN_SECONDS):
                # 半开探测失败: 冷却期过后重新熔断
                h["down_since"] = now
                log.error("数据源熔断器半开探测失败, 重新熔断: %s(冷却%d秒)", src, h["cooldown"])


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


# ==================== 昨日涨停池 (2026-09-07) ====================
# 背景: 腾讯兜底行**无 f103 概念字段**(东财被墙走腾讯全市场时), 评分层 is_first_board
#       (原只认 f103 "昨日涨停/连板"标签)恒为 False → 勾选「昨涨停」筛出空名单。
# 方案: 东财 push2ex 域名(getTopicZTPool, 与 clist 的 push2 域名不同, 生产实测畅通)
#       返回**指定交易日**涨停池(含连板/一字), 作为"昨日涨停"权威名单 —— 与数据源无关,
#       东财/腾讯/量脉任一行都能判断昨日是否涨停。f103 标签仅作名单不可用时的降级。
_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_ZT_CACHE = {"codes": None, "ts": 0}        # 成功缓存
_ZT_FAIL = {"ts": 0}                        # 失败冷却(防反复打网络)
_ZT_OK_TTL = 600                            # 成功缓存 10min(交易日间数据冻结, 足够; 跨日自动重探)
_ZT_FAIL_TTL = 120                          # 失败冷却 2min


def _fetch_zt_pool_date(date_str):
    """拉指定交易日(YYYYMMDD)涨停池, 返回 code set(空池返回空 set); 网络异常向上抛。"""
    codes = set()
    for page in range(5):                    # 至多 5 页(极端普涨 ~800 只)
        qs = urllib.parse.urlencode({
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": page, "pagesize": 200, "sort": "fbt:asc", "date": date_str})
        req = urllib.request.Request(_ZT_POOL_URL + "?" + qs, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://quote.eastmoney.com/"})
        with _http_get(req, timeout=8, context=_NO_VERIFY_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pool = ((data.get("data") or {}).get("pool")) or []
        for x in pool:
            codes.add(str(x.get("c")))
        if len(pool) < 200:
            break
    return codes


def get_yesterday_zt_codes():
    """昨日涨停代码集合(权威名单); 失败/无可用交易日返回 None(调用方降级 f103 概念标签)。
    探测: 从昨天起往前最多 15 自然日(覆盖周末/长假), 取第一个返回**非空池**的交易日;
    保护: 绝不探测今天(盘中 getTopicZTPool(date=今天) 返回的是"今日已涨停", 语义不符)。
    缓存: 成功 600s / 失败 120s 冷却, 跨日自动重探测。"""
    now = time.time()
    if _ZT_CACHE["codes"] is not None and now - _ZT_CACHE["ts"] < _ZT_OK_TTL:
        return _ZT_CACHE["codes"]
    if now - _ZT_FAIL["ts"] < _ZT_FAIL_TTL:
        return None
    today = time.strftime("%Y%m%d")
    for back in range(1, 16):
        ds = time.strftime("%Y%m%d", time.localtime(now - back * 86400))
        if ds >= today:
            continue                        # 防时区边缘误探今天
        try:
            codes = _fetch_zt_pool_date(ds)
        except Exception as e:
            log.warning("昨涨停池拉取失败 date=%s err=%s", ds, str(e)[:100])
            continue
        if codes:
            _ZT_CACHE.update({"codes": codes, "ts": now})
            log.info("昨涨停池 date=%s 涨停%d只", ds, len(codes))
            return codes
        # 空池(非交易日/当日零涨停) → 继续往前找最近交易日
    _ZT_FAIL["ts"] = now
    log.warning("昨涨停池 15 天窗口内无可用交易日数据(判据降级 f103)")
    return None


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
    with _http_get(req, timeout=10, context=_NO_VERIFY_CTX) as resp:
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
    with _http_get(req, timeout=15, context=_NO_VERIFY_CTX) as resp:
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


def _tencent_diff(code, fields):
    """腾讯单行 88 字段 → 东财 diff 同构(与 fetch_eastmoney_all/fetch_raw_by_codes 同构)。
    fields 为 _fetch_tencent_batch 返回的 ~ 分隔列表(0-based):
      [1]名称 [3]现价 [4]昨收 [5]今开 [32]涨跌% [36]成交量(手) [37]成交额(万)
      [38]换手率 [44]流通市值(亿)
    2026-09-08 提取(方案 A+ 腾讯点查与全市场兜底共用): 关键数值缺/异常返回 None,
    调用方跳过该行 — 行为与原 fetch_tencent_market._grab 内联解析完全等价。"""
    try:
        mv_yi = float(fields[44])          # 流通市值(亿)
        price = float(fields[3])
        chg = float(fields[32])            # 涨跌%
        turnover = float(fields[38])       # 换手率
        amt_wan = float(fields[37])        # 成交额(万)
        vol_hand = float(fields[36])       # 成交量(手)
        pre_close = float(fields[4])       # 昨收
        open_price = float(fields[5])      # 今开 (2026-09-07 修复: 缺 f17 实体涨幅恒为 0)
    except (ValueError, IndexError):
        return None
    return {
        "f2": price, "f3": chg, "f8": turnover,
        "f4": pre_close, "f5": vol_hand,   # 2026-09-01 修复: 缺 f4/f5 会被 is_suspended 误判停牌
        "f17": open_price,                 # 2026-09-07 修复: 腾讯兜底缺今开 → 实体列全 0%
        "f12": code, "f14": fields[1],
        "f21": mv_yi * 1e8,            # 元
        "f615": chg,                    # 竞价涨幅(近似)
        "f616": amt_wan * 1e4,          # 竞价金额(元, 近似成交额)
        "f617": vol_hand * 100,         # 竞价量(股, 近似成交量)
        "f630": 0,
    }


def fetch_tencent_market(fs):
    """腾讯全市场行情兜底: 以全市场代码清单分批发拉 → 映射为东财 diff 格式
    (f2现价/f3涨跌%/f4昨收/f5成交量/f8换手/f12代码/f14名称/f21流通市值/f615竞价涨幅≈f3/
     f616竞价额≈成交额/f617量≈成交量/f630异动=0), 返回与 fetch_eastmoney 同构的列表。
    2026-09-01 修复: 原映射缺 f4/f5, scorer.is_suspended(f4<=0 或 f5==0 判停牌)
    会把全部腾讯兜底数据误判为停牌 → 东财熔断时选股/自动锁定(system_batch/auto_apply)结果为空。
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
    _failed_batches = []   # 重试后仍失败的批次(记录批大小, 用于缺票告警)

    def _grab(batch):
        """单批拉取并解析为东财 diff 格式。
        2026-09-01: 单批失败重试 1 次(间隔0.3s), 仍失败记入 _failed_batches(不再静默跳过)"""
        got = None
        for attempt in range(2):
            try:
                got = _fetch_tencent_batch(batch)
                break
            except Exception:
                if attempt == 0:
                    time.sleep(0.3)
        if got is None:
            _failed_batches.append(len(batch))
            return []
        out = []
        for code, f in got.items():
            d = _tencent_diff(code, f)
            if d is not None:
                out.append(d)
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
    # 2026-09-01 可观测性: 缺票告警(2026-09-01 17:00 曾静默丢 8 只导致左视图缺票污染整个下午)
    # 2026-09-03 降噪: 东财封锁期腾讯兜底为常态, 稳定缺 ~8 只(0.14%), 原每次 ERROR 单日刷 676 条;
    #   状态(缺票数+失败批)变化才即时 ERROR; 同状态 10 分钟最多 1 条 WARNING(见 _miss_alert_decision)
    miss = len(codes) - len(result)
    if _failed_batches or miss > 0:
        ratio = miss / len(codes) if codes else 0.0
        sig = (miss, tuple(sorted(_failed_batches)))
        act = _miss_alert_decision(_tencent_miss_state, sig, time.time())
        if act == "error":
            if miss >= 5 or ratio > 0.001:
                log.error("腾讯兜底缺票告警! 代码清单%d只 实际返回%d只 缺%d只(%.2f%%) 重试后失败%d批=%s fs=%s",
                          len(codes), len(result), miss, ratio * 100, len(_failed_batches), _failed_batches, fs)
            else:
                log.warning("腾讯兜底轻微缺票: 代码清单%d只 实际返回%d只 缺%d只 失败%d批",
                            len(codes), len(result), miss, len(_failed_batches))
        elif act == "repeat_warn":
            log.warning("腾讯兜底缺票持续中: 代码%d只 返回%d只 缺%d只(%.2f%%) 失败%d批 (同状态第%d次触发, 10分钟内不重复告警)",
                        len(codes), len(result), miss, ratio * 100, len(_failed_batches),
                        _tencent_miss_state["repeat"])
        # act == "silent": 同状态且限频内 → 静默(降噪换取 journald 信号纯度)
    else:
        # 完全恢复: 重置告警状态, 下次缺票视为新变化重新 ERROR 告警
        _tencent_miss_state.update(ts=0.0, key=None, repeat=0)
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
    ex = _EXECUTOR_CLIST
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
# 2026-09-04: 由「进程内 dict」改为「跨进程 cache_store」——uvicorn --workers 2 下
# 两 worker 原本各持一份 dict, 各自到期各拉一次全市场(外网重复翻倍); 跨进程共享后
# 全市场拉取次数减半, 且与 spotMap 预热错峰。保留 dict 仅为降级回退(拉取失败时)。
_market_brief_cache = {"ts": 0.0, "data": None}
_MARKET_BRIEF_KEY = "market_brief_amt"      # 跨进程缓存 key
_MARKET_BRIEF_TTL = 300                     # 默认新鲜度(秒), 与 max_age 默认值一致


def fetch_market_brief(max_age=300):
    """两市概况: {stockCount, amount(亿), date}
    - stockCount = 全市场股票数(沪+深+北, fetch_eastmoney_all 返回列表长度)
    - amount     = sum(f6) 全市场成交额(元 -> 亿)
    - 非交易时段(周末/收盘后) f6 可能全 0 -> amount 0, 由调用方决定展示
    5 分钟缓存, 首次全市场分页拉取 5-10s, 之后命中缓存
    2026-09-04: 缓存改跨进程 cache_store(见上), max_age=0 语义保持——
    跳过读缓存强制重拉, 拉完仍回写(ttl 取默认 300)供其他 worker 复用"""
    now = time.time()
    if max_age > 0:
        cached = store.get(_MARKET_BRIEF_KEY)
        if cached is not None:
            _market_brief_cache.update({"ts": now, "data": cached})   # 同步进程内降级副本
            return cached
    try:
        raw = _fetch_market_all_with_fallback(scorer.market_fs(["hs", "cyb", "kcb", "bj"]))
    except Exception as e:
        log.warning("两市概况拉取失败 err=%s", e)
        # 降级顺序: 跨进程缓存(可能刚过期) → 进程内上次值
        return store.get(_MARKET_BRIEF_KEY) or _market_brief_cache["data"] or None
    total_amt = sum(scorer.parse_float(s.get("f6")) for s in raw)
    # 2026-09-07 修复(主人反馈"两市资金 0亿"): 收盘后/数据源异常时 f6 全 0 →
    # amount=0 会被**写进 5 分钟缓存**并展示(上一版把 0 缓存了 300s)。
    # 无效值: ① 不写缓存 ② 沿用上次有效值(收盘后仍显示收盘成交额)
    if total_amt <= 0:
        _last = store.get(_MARKET_BRIEF_KEY) or _market_brief_cache.get("data")
        if _last and (_last.get("amount") or 0) > 0:
            log.info("两市概况本次 amount=0(非交易时段/源异常), 沿用上次有效值 %.0f亿",
                     _last.get("amount"))
            return _last
        log.warning("两市概况 amount=0 且无历史有效值(首次拉取异常)")
    g = time.gmtime(now + 8 * 3600)   # 北京时间
    d = {
        "stockCount": len(raw),
        "amount": round(total_amt / 1e8, 2),          # 亿元
        "date": "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday),
    }
    ttl = max_age if max_age > 0 else _MARKET_BRIEF_TTL
    store.set(_MARKET_BRIEF_KEY, d, ttl=ttl)
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


def _hm_of_ts(ts):
    """时间戳 → 北京时间的"日内分钟数"(9:30 → 570), 用于跨日的同一时刻匹配"""
    g = time.gmtime(ts + 8 * 3600)
    return g.tm_hour * 60 + g.tm_min


def get_same_time_yesterday(date=None, now=None):
    """取昨日同一时点的成交额(用于'两市较昨日同一时点'对比);
    返回 {amount, stockCount, ts, date} 或 None (无昨日数据)

    2026-09-07 修复(主人"盘中不是和上个交易日同一时间比较"): 原逻辑
    `[s for s in arr if s['ts'] <= now]` —— 昨日所有快照 ts 都早于"今天此刻",
    条件恒真 → cand[-1] 永远取到**昨日最后一条(15:00 收盘=全天)**, 与"同一时点"
    名不符实(实测 9/4 返回 15:00 的 20304 亿)。现按**日内时刻**匹配:
    今日 14:00 → 取昨日 14:00(或之前最近)的快照; 收盘后则自然取昨日全天。
    now 可注入(测试用)。"""
    from . import settings as settings_svc
    from datetime import datetime, timedelta
    now = int(now if now is not None else time.time())
    # 2026-09-07 修复(主人反馈"两市放量 0 亿"): 原用 datetime.fromtimestamp(now + 8*3600)
    # —— 服务器时区已是 **CST(+8)**, fromtimestamp 按本地时区转换, 再加 8h 会**多加 8 小时**
    # → 北京时间 19:16 被算成次日 03:16, "昨日"= 明天-1天 = **今天** → 拿今日 intraday
    # 自我对比 → 差额恒 0 → 前端显示"放量 0 亿"。
    # 统一用 gmtime(UTC)+8h 取北京时间(与 scorer.in_auction_window 等一致, 与服务器时区无关)
    bj = time.gmtime(now + 8 * 3600)
    ydate = (datetime(*bj[:6]) - timedelta(days=1)).strftime("%Y-%m-%d")
    # 处理非交易日: 昨日=周六 → 周五数据(但周五数据可能也没有, 取更早)
    for offset in range(0, 5):    # 最多回溯 5 天
        cur = (datetime(*bj[:6]) - timedelta(days=1 + offset)).strftime("%Y-%m-%d")
        arr = settings_svc.get("market_brief_intraday_" + cur)
        if not arr:
            continue
        # 按**日内时刻**匹配: 今日 14:30 → 昨日 <=14:30 的最后一点(14:00/14:25)
        now_hm = _hm_of_ts(now)
        cand = [s for s in arr if _hm_of_ts(s.get("ts", 0)) <= now_hm]
        pick = cand[-1] if cand else arr[0]   # 早于昨日首条(竞价时段) → 取首条
        return {"amount": pick["amount"], "stockCount": pick["stockCount"],
                "ts": pick["ts"], "date": cur}
    return None


def _fetch_market_with_fallback(fs):
    """全市场行情容灾(2026-08-30 主人要求): 东财失败自动切腾讯兜底。
    腾讯无 f615/f616/f617 竞价专属字段, 用现价涨幅/成交额/成交量近似(盘中口径一致)。
    2026-08-31: 再加量脉第3源(liangmai market_snapshot_all, 字段更全 5901 只)。
    东财/腾讯/量脉都失败时抛异常。"""
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
            log.warning("腾讯兜底也失败, 切量脉第3源 fs=%s err=%s", fs, str(e2)[:120])
            try:
                from . import liangmai
                rows = liangmai.fetch_market_all()
                if rows:
                    log.info("量脉全市场兜底成功 fs=%s 返回%d只", fs, len(rows))
                    return rows
                raise RuntimeError("量脉返回空")
            except Exception as e3:
                log.error("全市场行情数据源全部失败(东财+腾讯+量脉) fs=%s err=%s", fs, str(e3)[:100])
                raise RuntimeError("全市场行情数据源全部失败(东财+腾讯+量脉): %s" % str(e)[:80])


def _fetch_market_all_with_fallback(fs):
    """2026-08-30 容灾加固(主人反馈用户截图): fetch_eastmoney_all 的腾讯兜底版。
    覆盖 ensure_spot_cache 外的路径(351/497/auction_snapshot), 防止熔断异常冒到前端。
    2026-08-31: 再加量脉第3源。"""
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
            log.warning("腾讯全市场兜底失败, 切量脉第3源 fs=%s err=%s", fs, str(e2)[:120])
            try:
                from . import liangmai
                rows = liangmai.fetch_market_all()
                if rows:
                    log.info("量脉全市场兜底成功 fs=%s 返回%d只 (东财/腾讯均故障)", fs, len(rows))
                    return rows
                raise RuntimeError("量脉返回空")
            except Exception as e3:
                log.error("全市场行情全部失败(东财+腾讯+量脉) fs=%s err=%s", fs, str(e3)[:100])
                raise RuntimeError("全市场行情数据源全部失败(东财+腾讯+量脉): %s" % str(e)[:80])


def ensure_cache(action, fs, before930):
    """在锁内保证缓存可用且新鲜, 返回 (raw, 错误信息)。
    - lock:    9:30 前强制重新拉取(锁定期权; 10s 内复用防多用户并发全市场风暴)
    - refresh: 缓存过期(超过 CACHE_TTL 秒)才重新拉取
    - filter:  无缓存时拉取一次
    2026-08-30 容灾(主人要求): 东财失败自动切腾讯兜底(腾讯无 f615 竞价字段, 用近似)
    2026-09-07 候选池=全市场(主人拍板): 原 _fetch_market_with_fallback 只取涨幅榜
    Top200(f3 倒序), 与默认"涨幅≤7%"筛选条件反向错配 → 默认竞价额 3000万+涨幅≤7%
    只交集出 5 只(全市场同条件 22 只)。改全市场分页(f12, 30页并发, 实测 386ms)。"""
    with _fetch_lock:
        now = time.time()
        entry = _cache.get(fs)
        if action == "lock":
            if not before930:
                return None, "9:30 后禁止重新选股"
            if entry is None or now - entry["ts"] > 10:
                _cache[fs] = {"raw": _fetch_market_all_with_fallback(fs), "ts": now}
            log.info("缓存锁定 fs=%s (候选池=全市场, 复用10s内缓存)", fs)
        elif action == "refresh":
            if entry is None or now - entry["ts"] > config.CACHE_TTL:
                _cache[fs] = {"raw": _fetch_market_all_with_fallback(fs), "ts": now}
                log.info("缓存刷新 fs=%s (候选池=全市场)", fs)
            else:
                log.info("缓存命中 fs=%s 年龄%.0fs", fs, now - entry["ts"])
        else:  # filter
            if entry is None or now - entry["ts"] > config.CACHE_TTL:
                _cache[fs] = {"raw": _fetch_market_all_with_fallback(fs), "ts": now}
                log.info("缓存初建/过期刷新 fs=%s (候选池=全市场)", fs)
            else:
                log.info("缓存命中 fs=%s 年龄%.0fs", fs, now - entry["ts"])
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


def _build_quote_map(raw):
    """把全市场原始行情行构建成 code -> quote dict(供 fetch_spot_quote_map 缓存复用)。"""
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


def fetch_spot_quote_map(fs):
    """9:30 后竞价模式: 全市场实时行情 map(code -> {realChange, entityChange, price, volRatio, turnover, name})。
    独立缓存(SPOT_CACHE_TTL), 不参与评分; 2026-09-05 起构建好的 dict 一并缓存(entry['map']),
    /api/quotes 按需过滤复用, 不再每次请求重建 ~5000 条。"""
    with _quote_map_lock:
        now = time.time()
        ent = _quote_map_cache.get(fs)
        if ent is None or now - ent["ts"] > config.SPOT_CACHE_TTL:
            try:
                raw = _fetch_market_all_with_fallback(fs)
                _quote_map_cache[fs] = {"raw": raw, "map": _build_quote_map(raw), "ts": now}
                log.info("全市场行情map刷新 fs=%s 共%d只", fs, len(raw))
            except Exception as e:
                if ent is not None:
                    log.warning("全市场行情map拉取失败, 沿用旧缓存 fs=%s err=%s", fs, e)
                else:
                    raise
        else:
            if "map" not in ent:   # 旧格式缓存(仅 raw)兜底补建 map
                ent["map"] = _build_quote_map(ent["raw"])
                _quote_map_cache[fs] = ent
            log.info("全市场行情map命中 fs=%s 年龄%.0fs", fs, now - ent["ts"])
        return _quote_map_cache[fs]["map"]


def fetch_spot_quotes_by_codes(code_list):
    """按需取实时行情(2026-09-05 B 方案: 拆分独立行情接口): 仅回 code_list 中命中缓存的 code->quote,
    不触发全市场拉取。供 /api/quotes 使用 —— 前端 9:30 后 merge 只对"不在返回名单的锁定 code"
    要实时价, 全市场下发已从 /api/stocks 拆走; 缓存由预热线程(40s)+refresh 保证常新鲜。
    读各 fs 已缓存的 spotMap(60s TTL); 仅当缓存从未预热(空)时才兜底拉一次全市场建 base。"""
    if not code_list:
        return {}
    code_set = set(code_list)
    with _quote_map_lock:
        snap = [ent["map"] for ent in _quote_map_cache.values() if ent.get("map")]
        cold = not _quote_map_cache
    if not snap and cold:
        base = scorer.market_fs(["hs", "cyb", "kcb"])
        snap = [fetch_spot_quote_map(base)]
    out = {}
    for m in snap:
        for code in list(code_set):
            if code in m:
                out[code] = m[code]
                code_set.discard(code)
        if not code_set:
            break
    return out


# 东财按 code 批量拉完整行情接口(2026-09-07 新增): ulist.np/get 返回与 clist 同构的 diff 列表
_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_ULIST_BATCH = 60     # 每批 ≤60 只(实测 200 只 URL 过长; 60 稳)


def fetch_raw_by_codes(code_list):
    """按 code 批量拉**完整行情 diff**(盘后 filter 快照候选补评分用, 2026-09-07):
    候选池先用 9:25 快照表初筛(免费), 命中几十只再这里点查, 替代"实时拉全市场 28 页"。
    返回与 fetch_eastmoney_all/clist **完全同构**的 diff 列表(fields=config.FIELDS, 与
    _fetch_clist_page 同用), 可直接喂 scorer.process_all_stocks 完整评分; 逐批直拉东财
    ulist(不依赖 spotMap 缓存, 评分字段全), 任一批失败抛异常(调用方降级回全市场)。
    走 _http_get(自动出站 IP 轮询)。
    ⚠️ 2026-09-07 晚修: 曾只列 15 字段漏 **f615(竞价涨幅)/f17(今开)/f630 等** → scorer
    get_bid_change 无 f615 退 f3(现价/收盘涨幅) → 竞涨列=现涨列 + 「涨幅≤bidGt」过滤按
    现价判 → 盘后筛出一批大跌票(生产事故)。必须整段复用 config.FIELDS 防再次漏字段。"""
    if not code_list:
        return []
    out = []
    t0 = time.time()
    for i in range(0, len(code_list), _ULIST_BATCH):
        chunk = code_list[i:i + _ULIST_BATCH]
        secids = ",".join(_secid(c) for c in chunk)
        qs = urllib.parse.urlencode({
            "fltt": 2, "invt": 2,
            "fields": config.FIELDS,     # 与全市场 clist 同构(必须整段复用, 勿手写子集!)
            "secids": secids, "ut": config.EASTMONEY_UT,
        })
        req = urllib.request.Request(_ULIST_URL + "?" + qs, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://quote.eastmoney.com/"})
        with _http_get(req, timeout=10, context=_NO_VERIFY_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("rc") != 0:
            raise RuntimeError("东财 ulist 返回异常 rc=%s" % data.get("rc"))
        diff = (data.get("data") or {}).get("diff") or []
        out.extend(diff)
    if not out:
        raise RuntimeError("东财 ulist 返回空")
    log.info("东财按code点查 %d只(共%d批) 耗时%.0fms 返回%d只",
             len(code_list), (len(code_list) + _ULIST_BATCH - 1) // _ULIST_BATCH,
             (time.time() - t0) * 1000, len(out))
    return out


def fetch_tencent_by_codes(code_list):
    """腾讯按 code 点查兜底(2026-09-08 方案 A+, 主人拍板): 东财 ulist(fetch_raw_by_codes)
    断连时, 用 qt.gtimg.cn 批量接口按候选 code 拉**真实行情** — 名单仍由 9:25 快照池粗筛
    固定(幂等稳定), 但实时字段(现价/涨跌幅/今开/换手)真实 → 评分/过滤/展示与正常路径一致,
    不再出现「快照行直出」的现价 0 + 双低/价格过滤失效导致名单虚胖(9/8 早 70 vs 35 只)。
    返回与 fetch_raw_by_codes **完全同构**的 diff 列表, 可直接喂 scorer.process_all_stocks。

    ⚠️ 竞价字段近似: 腾讯无竞价专属字段, f615=现价涨幅 / f616=累计成交额(元)。竞价模式
    窗口外会被 9:25 定格 day_bid_change/day_bid_amt map 覆写(见 score_all_stocks 647-668),
    评分不受影响 — 此函数只负责把「展示类实时字段」补真实。全部失败/空返回抛 RuntimeError,
    调用方再降级快照行直出(保名单非空)。"""
    if _check_circuit("tencent_market"):
        raise RuntimeError("腾讯数据源熔断中(故障冷却%d秒内), 快速失败" % _CIRCUIT_OPEN_SECONDS)
    if not code_list:
        return []
    t0 = time.time()
    out = []
    symbols = [_tencent_symbol(c) for c in code_list]
    for i in range(0, len(symbols), _TENCENT_BATCH):
        batch = symbols[i:i + _TENCENT_BATCH]
        got = None
        for attempt in range(2):
            try:
                got = _fetch_tencent_batch(batch)
                break
            except Exception:
                if attempt == 0:
                    time.sleep(0.3)
        if got is None:
            raise RuntimeError("腾讯按code点查失败(批次重试后仍失败)")
        for code, fields in got.items():
            d = _tencent_diff(code, fields)
            if d is not None:
                out.append(d)
    if not out:
        raise RuntimeError("腾讯按code点查返回空")
    _record("tencent_market", True, int((time.time() - t0) * 1000))
    log.info("腾讯按code点查兜底 %d只 返回%d只 耗时%.0fms",
             len(code_list), len(out), (time.time() - t0) * 1000)
    return out


# ---------- spotMap 预热(2026-09-04) ----------
# 背景: 9:30 后 refresh 直读命中历史批次后, 响应仍需 spotMap 覆盖实时行情; spotMap 缓存
# TTL=60s, 到期瞬间的请求要在锁内同步拉全市场(东财封禁期=腾讯 5556 只 1-4.6s) → refresh
# 出现秒级长尾, 用户感知"打开/刷新还在计算选股转几秒"。交易时段后台线程每 40s 主动刷新
# 缓存(周期 < TTL 60s), 请求路径永远命中缓存(<50ms), 长尾消除。
_SPOT_PREWARM_PERIOD = 40        # 秒; < SPOT_CACHE_TTL(60) 保证缓存常新鲜
_spot_prewarm_started = False     # 幂等: uvicorn reload/重复 startup 不叠线程


def spot_prewarm_active(now_ts):
    """是否处于 spotMap 预热窗口: 工作日北京时间 9:26-15:05。
    9:26 起预热(错开 9:25 快照采集高峰), 保证 9:30 首个 refresh 直读即命中缓存。"""
    g = time.gmtime(now_ts + 8 * 3600)
    if g.tm_wday >= 5:
        return False
    hm = g.tm_hour * 60 + g.tm_min
    return 9 * 60 + 26 <= hm <= 15 * 60 + 5


def _spot_prewarm_fs_set():
    """预热 fs 集合: 默认全市场(hs+cyb+kcb) + 缓存中出现过的其它 fs(北交所等组合)"""
    base = scorer.market_fs(["hs", "cyb", "kcb"])
    return sorted(set(list(_quote_map_cache.keys())) | {base})


def _spot_prewarm_once():
    """刷新一次全部预热 fs 的 spotMap 缓存; 单 fs 失败不影响其它/下轮自愈"""
    for fs in _spot_prewarm_fs_set():
        try:
            raw = _fetch_market_all_with_fallback(fs)
            with _quote_map_lock:
                _quote_map_cache[fs] = {"raw": raw, "map": _build_quote_map(raw), "ts": time.time()}
            log.info("spotMap预热完成 fs=%s 共%d只", fs, len(raw))
        except Exception as e:
            log.warning("spotMap预热失败 fs=%s err=%s", fs, str(e)[:120])


def _spot_prewarm_loop():
    """常驻后台循环(main.py startup 启动; 每个 web worker 各跑一份, 与 yday-prewarm 同模式)"""
    while True:
        try:
            if spot_prewarm_active(time.time()):
                _spot_prewarm_once()
        except Exception as e:
            log.warning("spotMap预热循环异常 err=%s", str(e)[:120])
        time.sleep(_SPOT_PREWARM_PERIOD)


def start_spot_prewarm():
    """启动 spotMap 预热线程: 9:30 后直读响应所需的全市场实时行情常新鲜, 请求永不阻塞。
    幂等: 重复调用不起第二线程(main.py startup 每 worker 进程调一次)"""
    global _spot_prewarm_started
    if _spot_prewarm_started:
        return
    _spot_prewarm_started = True
    t = threading.Thread(target=_spot_prewarm_loop, daemon=True, name="spot-prewarm")
    t.start()
    log.info("spotMap预热线程已启动(工作日9:26-15:05每%ss刷新一次)", _SPOT_PREWARM_PERIOD)


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
            with _http_get(req, timeout=config.KLINE_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "ignore")
            m = re.search(r"\{.*\}", body, re.S)
            if not m:
                continue
            data = json.loads(m.group(0)).get("data") or ""
            segs = [s for s in str(data).split(";") if s]
            if not segs:
                continue
            # 2026-09-08: _kline_amount_pair 返回 (成交额对, T日涨跌幅%), 必须拆包。
            # 漏拆会把二元组当成成交额对透传 → scorer 取 pair[0] 拿到 list →
            # bid_amt / list 抛 TypeError, 选股接口 500(测试机部署实证教训)。
            pair, chg = _kline_amount_pair(segs)
            if pair is None:
                continue
            _record("ths_kline", True, int((time.time() - t0) * 1000))
            return pair, chg
        except Exception:
            continue
    _record("ths_kline", False)
    return None, None


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
        with _http_get(req, timeout=config.KLINE_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
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
    # 快速短路: 四源(东财/同花顺/腾讯日K/量脉)都熔断中 → 立即跳过(昨比对该 code 置空, 评分时容忍缺失)
    # 2026-09-01: 原三源短路会跳过第4源量脉(量脉正常时也置空) → 改为含量脉判断; 三源 down 但量脉可用时继续走量脉兜底
    if (_check_circuit("eastmoney_kline") and _check_circuit("ths_kline")
            and _check_circuit("tencent_kline") and _check_circuit("liangmai_kline")):
        return None, None       # 2026-09-08: 统一返回 (成交额对, 昨日涨跌幅)
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
            with _http_get(req, timeout=config.KLINE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            klines = data.get("data", {}).get("klines") or []
            if not klines:
                _mark_host_broken(host)
                continue
            pair, chg = _kline_amount_pair(klines)
            if pair is None:
                continue
            _record("eastmoney_kline", True, int((time.time() - t0) * 1000))
            return pair, chg      # 2026-09-08: 顺带返回 T 日真实涨跌幅(f58)
        except Exception:
            _mark_host_broken(host)
            continue
    _record("eastmoney_kline", False)
    # 东财全失败 → 同花顺兜底 → 腾讯日K兜底 → 量脉日K兜底(2026-08-31: 第4源, kline_vip_history 含成交额)
    # 返回值契约: 所有源统一返回 (成交额对 list, T日涨跌幅% 或 None)。
    # ths 走 _kline_amount_pair 故自带涨跌幅; 腾讯/量脉只有成交额 → chg=None(不捏造)。
    res = _fetch_yesterday_amount_ths(code)
    if res is not None:
        return res
    v = _fetch_yesterday_amount_tencent(code)
    if v is not None:
        return v, None
    try:
        from . import liangmai
        t1 = time.time()
        v = liangmai._fetch_yesterday_amount_one(code, _bj_date_str().replace("-", ""))
        if v is not None:
            _record("liangmai_kline", True, int((time.time() - t1) * 1000))
            log.info("昨比量脉兜底成功 code=%s", code)
            return v, None
        _record("liangmai_kline", False)
    except Exception:
        _record("liangmai_kline", False)
    return None, None


def _kline_amount_pair(klines):
    """从日K行(逗号分隔)提取 ([最近已收盘T日万元, T-1日万元], T日涨跌幅%);
    自动跳过"今天"(未收盘)的K线, 保证 pair[0] 恒为最近已收盘交易日全天额。
    东财日期格式 YYYY-MM-DD, 同花顺 YYYYMMDD, 两种都兼容; 不足/无效返回 (None, None)。
    (修复: 东财盘中含今天未收盘K线, 同花顺不含 → 两源 pair 语义曾不一致, 导致分母错位)

    2026-09-08 昨日涨幅真实化: 东财日K fields2=f51..f58 → parts[7]=涨跌幅(f58),
    与成交额同一次请求返回, **零额外网络开销**。此前评分的"昨日涨幅"因子用的是
    当日 f3 冒充(详见 scorer.compute_score), 语义错误; 本函数顺带把 T 日真实涨幅
    带出, 供 fetch_yesterday_changes 使用。
    (注: 兜底源 ths/腾讯/量脉只返回成交额对, 无涨跌幅 → chg=None, 缺失即缺失, 不捏造)"""
    today = _bj_date_str()
    def amt_of(row):
        parts = row.split(",")
        if len(parts) < 7:
            return None, None, None
        try:
            v = float(parts[6])
            if not (math.isfinite(v) and v > 0):
                return None, None, None
        except (TypeError, ValueError):
            return None, None, None
        chg = None
        if len(parts) >= 8:
            try:
                c = float(parts[7])
                if math.isfinite(c):
                    chg = c
            except (TypeError, ValueError):
                chg = None
        return v / 10000.0, parts[0], chg
    def is_today(dstr):
        if not dstr:
            return False
        d = dstr.replace("-", "")
        return d == today.replace("-", "")
    # 收集所有 (日期, 金额, 涨跌幅), 跳过今天
    rows = []
    for row in klines:
        amt, dstr, chg = amt_of(row)
        if amt is not None and not is_today(dstr):
            rows.append((dstr, amt, chg))
    if not rows:
        return None, None
    # 最近已收盘 = 最后一行(按日期), 取它和它前一行
    t = rows[-1][1]
    t1 = rows[-2][1] if len(rows) >= 2 else None
    chg_t = rows[-1][2]                  # T 日(最近已收盘交易日)真实涨跌幅
    if t is None and t1 is None:
        return None, None
    return [t, t1], chg_t


def fetch_yesterday_amounts(codes, wait=False):
    """读取昨比缓存并(可选)触发批量拉取; 返回 {code: [T日, T-1日]万元}
    - wait=False(默认, 用户请求路径): 有 need 时后台线程拉取, 当前请求立即返回现有缓存
      (昨比部分/空, 评分容忍缺失) → 请求永不因昨比卡顿(2026-09-02 生产事故核心修复)
    - wait=True(auto_apply/system_batch 等后台任务): 同步等待拉取完成(最多超时), 保证
      锁定/自动应用名单昨比完整
    其余(失败缓存/批锁/超时cancel)见 _do_fetch_yesterday。
    """
    if not codes:
        return {}
    today = _bj_date_str()
    now = time.time()
    need = []
    with _yesterday_lock:
        for c in codes:
            ent = _yesterday_cache.get(c)
            if ent is None or ent[0] != today:
                need.append(c)
            elif ent[1] is None and now - ent[2] >= config.YESTERDAY_RETRY_TTL:
                need.append(c)          # 失败缓存过期, 允许重试(窗口内不再打扰数据源)
    if need:
        # 2026-08-31 线上事故: 全源熔断时逐只短路打 WARNING → 36804 条日志风暴,
        # 日志 I/O 阻塞 worker 导致 /api/stocks 674s、health 超时。改为批级短路: 一条聚合日志 + 直接返回
        # 四源(东财/同花顺/腾讯日K/量脉)全部熔断才短路; 任一源可用则继续尝试(量脉作第4源兜底)
        # 2026-09-01: 原三源短路会跳过量脉(量脉正常时昨比也全置空, 9/1 短路1207次 vs 量脉兜底仅21次) → 含量脉
        if (_check_circuit("eastmoney_kline") and _check_circuit("ths_kline")
                and _check_circuit("tencent_kline") and _check_circuit("liangmai_kline")):
            log.warning("昨日成交额: 东财+同花顺+腾讯+量脉 四源全部熔断中, 本批%d只全部短路(昨比置空)", len(need))
            with _yesterday_lock:
                for c in need:          # 短路也写失败缓存, 避免下个请求重复判定
                    _yesterday_cache[c] = [today, None, now, None]
            return {}
        if wait:
            # 同步路径(后台任务): 等待批锁, 前一个拉取完成后可能已填充缓存 → 重新判定
            _yday_batch_lock.acquire()
            try:
                need2 = []
                with _yesterday_lock:
                    for c in codes:
                        ent = _yesterday_cache.get(c)
                        if ent is None or ent[0] != today:
                            need2.append(c)
                        elif ent[1] is None and now - ent[2] >= config.YESTERDAY_RETRY_TTL:
                            need2.append(c)
                if need2:
                    ok_cnt, fail_cnt = _do_fetch_yesterday(need2, today)
                    if fail_cnt:
                        log.warning("昨日成交额(同步)拉取: 需%d 成功%d 失败%d",
                                    len(need2), ok_cnt, fail_cnt)
            finally:
                _yday_batch_lock.release()
        else:
            # 异步路径(用户请求): 非阻塞拿锁, 拿到就后台拉; 拿不到说明已在拉, 直接返回缓存
            if _yday_batch_lock.acquire(blocking=False):
                threading.Thread(target=_yday_background_fetch, args=(need, today),
                                 daemon=True, name="yday-bg").start()
    out = {}
    with _yesterday_lock:
        for c in codes:
            ent = _yesterday_cache.get(c)
            if ent and ent[0] == today and ent[1] is not None:
                out[c] = ent[1]
    return out


def fetch_yesterday_changes(codes):
    """真实昨日涨幅 map {code: 涨跌幅%} — 供评分"昨日涨幅"因子使用。

    2026-09-08 语义修正: 此前 scorer.compute_score 的"昨日涨幅"因子取的是**当日 f3**
    (现价涨幅)冒充, 与因子分档语义(昨日强势 3~9.5% 给高分)完全不符。真实值来自东财
    日K 的 f58 涨跌幅, 与成交额**同一次请求**返回(见 _kline_amount_pair), 故本函数
    只读缓存、**零额外网络请求**。

    缺失语义(契约铁律1): 未拉到 / 走的是 ths·腾讯·量脉兜底源(只返回成交额无涨跌幅)
    → 该 code 不出现在返回 map 中, 调用方按"缺失"处理(不得填 0 冒充)。
    调用顺序: 须在 fetch_yesterday_amounts 之后调用(由其填充缓存)。
    """
    if not codes:
        return {}
    today = _bj_date_str()
    out = {}
    with _yesterday_lock:
        for c in codes:
            ent = _yesterday_cache.get(c)
            if ent and ent[0] == today and len(ent) > 3 and ent[3] is not None:
                out[c] = ent[3]
    return out


def _split_yday(res):
    """拆分单只票昨日拉取结果为 (成交额对 list|None, T日涨跌幅% |None)。

    契约: 上游各源统一返回 (pair, chg), pair 形如 [T日万元, T-1日万元]。
    防御剥壳(2026-09-08 测试机部署实证教训): 曾有兜底源漏拆包, 返回 ((pair, chg), None)
    双层结构 → 成交额对变成 tuple → scorer 侧 `bid_amt / pair[0]` 拿 list 做除法抛
    TypeError, 选股接口直接 500。此处检测并剥掉多余一层: **宁可丢涨跌幅, 也绝不让
    成交额对形状污染**(成交额对参与量比计算, 形状错 = 接口崩; 涨跌幅缺失只是评分降级)。
    """
    if res is None:
        return None, None
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], (list, tuple)):
        pair, chg = res
        # 剥壳: pair 本应形如 [万元T, 万元T-1]; 若里面还嵌着 (pair, chg) 则再取一层
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[0], (list, tuple)):
            pair, chg = pair[0], pair[1]
        return (list(pair) if isinstance(pair, (list, tuple)) else None), chg
    if isinstance(res, (list, tuple)):
        return list(res), None          # 旧格式: 纯成交额对
    return None, None


def _yday_background_fetch(need, today):
    """后台昨比拉取线程(异步路径)"""
    try:
        ok_cnt, fail_cnt = _do_fetch_yesterday(need, today)
        if fail_cnt:
            log.info("昨比后台拉取完成: 需%d 成功%d 失败%d", len(need), ok_cnt, fail_cnt)
        elif ok_cnt:
            log.info("昨比后台拉取完成: %d 只全部成功", ok_cnt)
    except Exception as e:
        log.warning("昨比后台拉取异常 err=%s", e)
    finally:
        _yday_batch_lock.release()


def _do_fetch_yesterday(need, today):
    """实际批量拉取(批锁内执行): 返回 (成功数, 失败数)
    2026-09-02 超时后 cancel 队列中未运行任务: 原实现超时后任务滞留线程池队列
    (5000 只 4 线程 12s 只完成部分, 剩余全排队) → 后续抢筹/其他拉取排队等线程 → 全站卡顿"""
    ok_cnt = 0
    fail_cnt = len(need)
    # 2026-09-01: 改进程级常驻池(原 shutdown(wait=False) 后线程滞留后台跑网络超时,
    # 高并发下线程只增不减拖死生产)。超时未完成的任务留在池内排队, 不阻塞请求。
    ex = _EXECUTOR_YDAY
    futs = {ex.submit(_fetch_yesterday_amount_one, c): c for c in need}
    try:
        for f in as_completed(futs, timeout=config.YESTERDAY_FETCH_TIMEOUT):
            c = futs[f]
            try:
                res = f.result()
            except Exception:
                res = None
            # 2026-09-08: 返回值扩为 (成交额对, T日涨跌幅%); 兼容旧格式(纯 pair)
            v, vchg = _split_yday(res)
            with _yesterday_lock:
                _yesterday_cache[c] = [today, v, time.time(), vchg]   # 成功/失败都缓存
            if v is not None:
                ok_cnt += 1
            fail_cnt -= 1
    except _FutTimeout:
        # 超时未完成: cancel 队列中未运行任务(立即释放线程给后续拉取), 已运行任务继续(完成写缓存)
        done = len(need) - fail_cnt
        log.warning("昨日成交额拉取超时(%ds) 已完成%d/%d, 取消队列任务(写失败缓存)",
                    config.YESTERDAY_FETCH_TIMEOUT, done, len(need))
        with _yesterday_lock:
            for f, c in futs.items():
                if not f.done():
                    f.cancel()          # 未开始任务从队列移除, 线程立即空闲
                    _yesterday_cache[c] = [today, None, time.time(), None]
        fail_cnt = len(need) - ok_cnt
    return ok_cnt, fail_cnt


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
        with _http_get(req, timeout=10, context=_NO_VERIFY_CTX) as resp:
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
            with _http_get(req, timeout=config.KLINE_TIMEOUT) as resp:
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
            with _http_get(req, timeout=config.KLINE_TIMEOUT) as resp:
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
            with _http_get(req, timeout=config.KLINE_TIMEOUT) as resp:
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
        with _http_get(req, timeout=15, context=_NO_VERIFY_CTX) as resp:
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
        with _http_get(req, timeout=config.KLINE_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
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
        with _http_get(req, timeout=config.KLINE_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
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
        with _http_get(req, timeout=config.KLINE_TIMEOUT) as resp:
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
