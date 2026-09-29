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
from ..core import trade_calendar as _tc   # 2026-09-26: yday 期望 T 日需要交易日历(无循环: 只依赖 stdlib)
from . import scorer   # 仅复用 parse_float / market_fs (无循环: scorer 不依赖 fetcher)
from .cache_store import store   # 2026-09-04: 两市概况改跨进程缓存(无循环: cache_store 只依赖 core)
from .cache_store import cached_singleflight   # 2026-09-28: 上市日期(静态数据)缓存用
from ..db import database as _database   # 2026-09-10: 昨日成交额落库/读库(无循环: database 只依赖 core)

# 部分行情网关(走代理/自签名)证书校验失败, 仅关校验不关加密
_NO_VERIFY_CTX = ssl.create_default_context()
_NO_VERIFY_CTX.check_hostname = False
_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE

# ---------- 出站 IP 轮询(实现已下沉到 app.core.net, 2026-09-09) ----------
# 历史: 本文件的 IP 轮换是 2026-09-07 为"双网卡防东财封单 IP"加的, 但只有 fetcher 自己
#       走轮换 —— kpl(开盘啦, 竞价数据唯一来源)/hot_rank/sector_rotation 等模块的裸
#       urlopen 全都单 IP 裸奔。故把 rotator 抽到 core.net, 各数据源共用同一份健康状态
#       (否则两个 rotator 各罚各的, 惩罚信息割裂)。
# 实测: 东财对两个出口都封(IDC 级, 换 IP 无效); 新浪 eth0=403/eth1=200 → 轮换对
#       "按 IP 封禁"的源有效, 是出口容灾 + 分散限流的现成手段。
from ..core import net as _net

_ip_binding = _net.ip_binding          # 兼容旧名(测试/外部引用)


def _http_get(req, timeout, context=None):
    """带 IP 轮询的 urlopen 包装(替换 fetcher 内所有 urllib.request.urlopen)。"""
    return _net.http_get(req, timeout=timeout, context=context)


# ---------- 换源 WP3/WP4/WP5 共用小工具(2026-09-24) ----------
def _meoz_enabled():
    """猫爪源是否可用。

    延迟导入: 猫爪客户端与 fetcher 之间不能有模块级互相 import
    (meoz_client 只依赖 core + cache_store, 这里保持同一方向性)。
    异常一律判**不可用** —— 本条链路的失败语义是"降级到东财/腾讯", 不是崩。
    """
    try:
        from . import meoz_client
        return bool(meoz_client.enabled())
    except Exception:                                          # noqa: BLE001
        return False


def _num(v, default=0.0):
    """宽松取数: 非有限数 / None / 空串 → default。

    为什么不直接用 float(): 上游 JSON 里同一列可能是 int/float/字符串/None,
    且实测出现过 NaN/Infinity —— 让它们进评分或落库是本仓历史事故源
    (落库列 NOT NULL + 被 _safe_num 兜成 0 → "未知"被显示成"已知为 0")。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _hhmm_digits(v):
    """'0930' / '09:30' / 930 → '0930'; 无法解析返回 ''。"""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    if not s:
        return ""
    return s.zfill(4)[:4]


def _hhmm_colon(v):
    """'0930' → '09:30'(图表 time 轴格式); 无法解析返回 ''。"""
    s = _hhmm_digits(v)
    return (s[:2] + ":" + s[2:]) if len(s) == 4 else ""


def _hhmmss_int(v):
    """'09:25:00' → 92500(HHMMSS 整数, 与东财 fbt 字段同口径); 缺值返回 0。"""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(s) < 5:
        return 0
    return int(s[:6])


def _norm_d8(v):
    """任意日期表示 → `YYYYMMDD`; 无法识别返回 `''`。

    2026-09-26 新增: 东财日K的日期列是 `YYYY-MM-DD`, 猫爪的 `tradedate` 是 `YYYYMMDD`
    —— 校验"这根K线到底属于哪天"时必须先归一, 否则字符串比较会因分隔符判决错误。
    """
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    return s[:8] if len(s) >= 8 else ""

log = logger.get_logger(__name__)

# ---------- 按市场范围(fs)分区的行情缓存 ----------
# fs -> {"raw": [...], "ts": epoch}; 锁只保护"拉数据/更新缓存", 评分计算不持锁
_cache = {}
_fetch_lock = threading.Lock()

# 昨日全天成交额(万元): code -> [缓存日期, 金额(pair) 或 None(失败), 写入时间戳], 当日有效
_yesterday_cache = {}
_yesterday_lock = threading.Lock()

# ---------- 昨日成交额落库(2026-09-10: 收盘后写一次, 全天读库, 去掉多源兜底链) ----------
# 说明见 database.init_db 中 yday_amount 建表注释: 按 code 覆盖写。
# 兼容 CentOS 7 SQLite 3.7(不支持 ON CONFLICT) → 统一用 INSERT OR REPLACE。
#
# ★ 2026-09-26 修正一处**错误注释**(它的错误正是本次生产事故的源头):
#   原文写「按 code 覆盖写, **读时无需算"昨日是哪天"**」—— 此判据不成立。
#   行的 tdate 只有在"落库那一刻恰好等于正确的 T 日"时才可信, 而这一点**此前没有任何
#   代码去校验**, 于是叠加出「数据冻结但标签每天前进」: 读侧拿到一行就当天已拉到、
#   写侧又把同一批值原样回写并推进 tdate。现改为**读时按"期望 T 日"逐行比对**
#   (见 _yday_expected_tdate / yday_db_get 的 expect_tdate 参数)。
_YDAY_MAX_AGE_DAYS = 5   # tdate 距今超过 5 天(跨周末/长假)视为过期, 回落到实时源


def _yday_tdate_fresh(tdate):
    """tdate(YYYYMMDD) 是否足够新鲜(未被长假/停更拖成脏数据)

    ⚠ 这只是**粗粒度兜底**(防"几个月前的行被当成有效"), 不是正确性判据 ——
    真正决定"这行能不能用"的是 `_yday_expected_tdate()` 的逐行相等比对。
    两年前这里只有本函数, 5 天窗口太宽: 09-14~09-24 的脏行 tdate 天天"新鲜",
    却全是 09-10 的值。
    """
    if not tdate or len(tdate) < 8:
        return False
    try:
        t = time.mktime(time.strptime(tdate[:8], "%Y%m%d"))
    except (ValueError, OverflowError):
        return False
    age = (time.time() - t) / 86400.0
    return -1.0 <= age <= _YDAY_MAX_AGE_DAYS


def _yday_expected_tdate(now=None):
    """`yday_amount` 表此刻应当指向的 T 日(`YYYYMMDD`); 算不出返回 `""`。

    语义(与 `_after_close()` / `_kline_amount_pair` 的"T 日"口径严格一致):
      * **交易日**且已过收盘缓冲(>=15:05) → T = **今天**(今天的日K已定格);
      * 其余时刻(盘中/盘前/非交易日)      → T = **上一个交易日**(不含今天)。

    ★ 为什么要它(2026-09-26, 生产 v4.11.53): 生产 `yday_amount` 自 2026-09-10 首次
      落库后再没更新过 —— 09-14~09-24 共 9 个交易日, 每天 09:25 读到的都是同一批值
      (与 09-10 收盘涨幅逐位相同率 99.6%), 而 tdate 标签每天照常前进。
      根因是「读侧拿到行就认为今天已拉到」+「写侧原样回写只推进 tdate」形成闭环;
      断开闭环的**唯一可靠判据**就是"行的 tdate 是否等于此刻应有的 T 日"。

    为什么不用"当天 -1 天"粗算: 周末/长假会指错日期 —— 周六的"上一个交易日"是周五,
    长假后第一天的是节前最后一天。故一律走 `core/trade_calendar`(单一事实来源)。
    """
    ts = time.time() if now is None else float(now)
    g = time.gmtime(ts + 8 * 3600)
    today = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    if (g.tm_hour, g.tm_min) >= (15, 5) and _tc.is_trade_day(today):
        return today.replace("-", "")
    prev = _tc.prev_trade_date(today)
    return prev.replace("-", "") if prev else ""


def yday_db_put(rows):
    """批量写昨日成交额(按 code 覆盖)。rows: [(code, tdate, amount, prev_amount, chg), ...]
    返回成功写入条数; 失败只告警不抛出(落库是加速手段, 不是主链路)"""
    if not rows:
        return 0
    now = int(time.time())
    conn = None
    try:
        conn = _database.get_conn()
        cur = conn.cursor()
        cur.executemany(
            "INSERT OR REPLACE INTO yday_amount (code, tdate, amount, prev_amount, chg, ts) "
            "VALUES (?,?,?,?,?,?)",
            [(c, td, a, pa, ch, now) for (c, td, a, pa, ch) in rows])
        conn.commit()
        return len(rows)
    except Exception as e:                                    # noqa: BLE001
        log.warning("昨日成交额落库失败 err=%s", e)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()   # sqlite3 的 with 不关连接, 必须显式 close(2026-09-09 fd 泄漏事故)
            except Exception:  # noqa: BLE001
                pass


def yday_db_get(codes, expect_tdate=None):
    """批量读昨日成交额: 返回 {code: (amount, prev_amount, chg)} (amount 为空或 tdate 不可信的不返回)

    expect_tdate: `YYYYMMDD` 或 None。
      * **给出时(推荐, 生产调用路径一律走这条)**: 只返回 `tdate` 与它**逐位相等**的行
        —— 这是"这行数据是不是此刻该有的那一份"的正确性判据, 缺了它就会把
        陈年旧值当成"今天的昨日成交额"。
      * None: 只做 `_yday_tdate_fresh` 的 5 天粗粒度兜底。仅供"不知道期望 T 日"的
        场景(单测/离线工具)使用, **业务路径不要用**。
    """
    if not codes:
        return {}
    out = {}
    conn = None
    try:
        conn = _database.get_conn()
        cur = conn.cursor()
        for i in range(0, len(codes), 500):        # SQLite 参数上限, 分批
            batch = list(codes[i:i + 500])
            ph = ",".join("?" * len(batch))
            cur.execute(
                "SELECT code, tdate, amount, prev_amount, chg FROM yday_amount "
                "WHERE code IN (%s)" % ph, batch)
            for code, tdate, amount, prev_amount, chg in cur.fetchall():
                if amount is None or not _yday_tdate_fresh(tdate):
                    continue
                if expect_tdate and str(tdate or "")[:8] != str(expect_tdate):
                    continue               # 不是此刻应有的 T 日 → 这行的值不可信, 当没有
                out[code] = (amount, prev_amount, chg)
    except Exception as e:                                    # noqa: BLE001
        log.warning("昨日成交额读库失败 err=%s", e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out
# 昨比批量拉取互斥(2026-09-02 生产事故): 并发请求同时进 need 判定都判定为空 → 各自全量拉取
# (14s 内 5 次全量 5000 只), 失败缓存只挡串行挡不住并发。批锁: 同时只允许一个全量拉取,
# 其余请求直接返回当前缓存(可能为空), 避免并发重复打爆数据源。
_yday_batch_lock = threading.Lock()
# 🔴 2026-09-29 (P0-c): 上面那把锁是**进程内**的 —— 生产 uvicorn `--workers 2` ⇒ 两个进程
# 各持一把, 同时都认为"该我拉", 于是同一批 need(全市场 5561 只 ÷ 500/片 ≈ 12 片猫爪 `daily`)
# **被两个进程各拉一遍**, 上游调用直接翻倍。竞价时段(09:15~09:25)请求最密集时最贵, 也是
# 2026-09-28 实测 429(web 进程 / a=daily)的**剩余**来源之一(「分片顺序不稳 ⇒ 缓存打不中」
# 那一半已由分片排序消除)。
# 跨进程单飞令牌: 独立键名 —— 不与 `sem:meoz`(板块异动/竞价快照在用) 共用闸, 避免挤占槽位。
_YDAY_SEM = "yday"          # 锁键 sem:yday:0
_YDAY_SEM_TTL = 180         # 秒; 12 片全市场拉取的量级上限(进程崩溃也会自动过期, 不会死锁)
_YDAY_SEM_TRY = 0.05        # 秒; 异步路径的"试一次"等待 —— 必须是**极小正数**:
#                            `acquire_sem(timeout=0)` 因 `while now < deadline` 一次都不试,
#                            会永远返回 None(等于永久关闭异步昨比)。

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
# down_threshold: 连续失败多少次才真正熔断(抖动保护, tencent=2, 其余=1)
# fails_in_row: 连续失败计数(成功清零)
_HEALTH = {
    "eastmoney_clist": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "eastmoney_kline": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "eastmoney_zt_pool": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                          "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "tencent_market":  {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 60, "base_cooldown": 60, "down_threshold": 1, "fails_in_row": 0},
    "tencent_kline":   {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                        "cooldown": 30, "base_cooldown": 30, "down_threshold": 2, "fails_in_row": 0},
    # TickPlus 竞价源(P2-1): 第二源, 单次全推成本高于东财分页 → 抖动保护阈值 2,
    # 冷却 120s(一个竞价时点内不再重试, 避免 9:15-9:25 反复空转)
    "tickplus_fullbid": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0,
                         "cooldown": 120, "base_cooldown": 120, "down_threshold": 2, "fails_in_row": 0},
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


def _circuit_open(h, now=None):
    """熔断是否仍处于生效期(纯函数, 调用方须持 _health_lock)
    生效期 = down_since 已设 且 距故障起点尚未超过当前冷却时长。
    2026-09-10 生产雪崩根因修复: 此前没有这个概念, _record 见一次成功就清 down_since,
    导致同批次内"失败页触发熔断 / 成功页立刻解除"反复横跳(实测 66 次故障 ↔ 65 次恢复,
    恢复日志全是"故障0秒"), 熔断形同虚设, 每次选股都完整重试 30 页 × 15s 超时。"""
    if not h or not h.get("down_since"):
        return False
    now = now if now is not None else time.time()
    return (now - h["down_since"]) < h.get("cooldown", _CIRCUIT_OPEN_SECONDS)


def _check_circuit(src="eastmoney_clist"):
    """检查数据源是否熔断中; 熔断时快速失败, 不等超时(防单 worker 卡死雪崩)
    返回 True=熔断中(应快速失败), False=可请求(正常或半开探测)"""
    with _health_lock:
        h = _HEALTH.get(src)
        if not h or not h["down_since"]:
            return False
        if _circuit_open(h):
            return True
    return False


def _record(src, ok, ms=0):
    """记录一次数据源调用结果; 状态翻转时打告警/恢复日志
    2026-09-01 加固: ①抖动保护 down_threshold>1 的源(tencent)连续失败
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
            if h["down_since"] and _circuit_open(h, now):
                # 熔断生效期内到达的成功: 只更新统计, 不解除熔断、不重置退避计数。
                # 2026-09-10 事故: 东财全市场分页 30 页并发, 各页独立 _record —— 失败页刚把
                # down_since 设上, 同批成功页紧接着就清零(日志"故障0秒"), 熔断在同一调用内
                # 反复横跳, 下次请求照旧完整重试 30 页(每页 15s 超时) → 单次选股 30s+。
                # 正确语义: 必须冷却结束后的半开探测成功才恢复, 冷却期内的成功一律不认。
                return
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


def _after_close():
    """当前北京时间是否已过收盘 —— 决定"今天"的 K 线算不算已收盘交易日。

    2026-09-08 语义修正: 此前所有日K解析**无条件跳过今天**(盘中口径正确: 今天未收盘,
    T 必须是最近已收盘交易日)。但**收盘后到午夜前**仍在跳过 → "昨日涨幅"取的是前一
    交易日, 整整滞后一天(盘中正确、收盘后错)。改为收盘后把今天视为已收盘。

    留 5 分钟缓冲(15:05): 15:00 收盘后数据商日K落库有延迟, 缓冲期内仍按盘中处理,
    避免拿到"今天未落库 → 静默退化成前天"的错位数据。
    非交易日(周末/节假日)数据源本就不返回今天的行 → 自动无影响。
    """
    g = time.gmtime(time.time() + 8 * 3600)
    return (g.tm_hour, g.tm_min) >= (15, 5)


# ==================== 昨日涨停池 (2026-09-07) ====================
# 背景: 腾讯兜底行**无 f103 概念字段**(东财被墙走腾讯全市场时), 评分层 is_first_board
#       (原只认 f103 "昨日涨停/连板"标签)恒为 False → 勾选「昨涨停」筛出空名单。
# 方案: 东财 push2ex 域名(getTopicZTPool, 与 clist 的 push2 域名不同, 生产实测畅通)
#       返回**指定交易日**涨停池(含连板/一字), 作为"昨日涨停"权威名单 —— 与数据源无关,
#       东财/腾讯任一行都能判断昨日是否涨停。f103 标签仅作名单不可用时的降级。
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


def _meoz_zt_codes_date(date_str):
    """猫爪指定交易日的**收盘涨停**代码集合(set)。

    返回值三分语义(**调用方必须区分, 这是本函数存在的意义**):
      * `None`  —— 猫爪不可用 / 调用失败(含非交易日返回的 code=1002) ⇒ **该日改走东财**
      * `set()` —— 调用成功但该日没有涨停票 ⇒ **继续往前找交易日**
      * 非空 set —— 命中

    与东财 getTopicZTPool 的语义对齐(2026-09-24 实测):
      * 猫爪限制 `tradedate` 显式传日期(offset 只接受 ≤0, 本函数不用 offset ——
        与 daily_auc 的"串日"教训同款: 判「哪一天」必须显式指定);
      * 池内 `type` 只有 'u'(涨停) / 'd'(跌停), 且 **is_break 恒 False**(炸板不留池)
        ⇒ 只取 `type=='u'`, **必须排掉 'd'**, 否则"昨涨停"名单会混入跌停票。
    """
    if not _meoz_enabled():
        return None
    try:
        from . import meoz_client
        rows = meoz_client.limit_pool_map(date=date_str)
    except Exception as e:                                     # noqa: BLE001
        log.warning("[猫爪] 涨停池拉取异常 date=%s err=%s", date_str, str(e)[:100])
        return None
    if not rows:
        return None                     # 不可用 / 非交易日(code=1002) → 交东财判
    return {str(c) for c, m in rows.items()
            if str(c) and str(m.get("type") or "") == "u"}


def _em_zt_codes_date(date_str):
    """东财指定交易日涨停池代码集合; 网络异常 → 空 set(不抛)。

    2026-09-24 换源 WP3 起**降为备源**: 只在猫爪不可用时才被调用。
    """
    try:
        return _fetch_zt_pool_date(date_str)
    except Exception as e:                                     # noqa: BLE001
        log.warning("昨涨停池拉取失败(备源东财) date=%s err=%s", date_str, str(e)[:100])
        return set()


def get_yesterday_zt_codes():
    """昨日涨停代码集合(权威名单); 失败/无可用交易日返回 None(调用方降级 f103 概念标签)。
    探测: 从昨天起往前最多 15 自然日(覆盖周末/长假), 取第一个返回**非空池**的交易日;
    保护: 绝不探测今天(盘中取"今天"返回的是"今日已涨停", 语义不符)。
    缓存: 成功 600s / 失败 120s 冷却, 跨日自动重探测。

    ★ 2026-09-24 换源 WP3: 主源由东财 push2ex 换成**猫爪 limit_pool**; 东财保留为
      同一日内的备源(猫爪不可用时逐日回退, 不是整轮放弃)。"往前找最近交易日"的
      15 日窗口容错**原样保留** —— 周末/长假第一个非空池才是答案这一点没变。

    ⚠️ 消费面提示(排障必读): 名单只在 `limitUp` 为**假值**时才会被装载
      (api/stocks.py: `zt_codes=_safe_zt_codes() if not f.get("limitUp") else None`)。
      线上全局默认 `limitUp=True`, 因此本函数当前**不参与**首页名单判定。
    """
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
        codes = _meoz_zt_codes_date(ds)     # None=猫爪不可用; set()=该日无涨停
        if codes is None:
            codes = _em_zt_codes_date(ds)   # 备源东财(同一天内回退)
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


_CLIST_PZ = 200        # clist 单页条数(全市场分页固定 200)
_EM_RC_END = 102       # 东财"没有更多数据"(翻过末页)返回码 —— 正常语义, 不是故障


class _ClistPage(list):
    """clist 单页结果: 兼作 list(diff), 额外携带响应里的 total 总条数(用于算真实页数)。

    2026-09-19: 用 list 子类而非新结构, 是为了**不改变既有调用方语义**
    (len()/extend()/真值判断全部照旧); mock 返回普通 list 时 total 缺失 →
    调用方回退固定页数上限, 老用例零改动。
    """
    __slots__ = ("total",)

    def __init__(self, diff, total=0):
        super().__init__(diff)
        self.total = int(total or 0)


def _fetch_clist_page(fs, page, fid="f3", fields=None):
    """拉取 clist 单页(200只)。

    返回 _ClistPage(见上)。**空列表 = 该页没有数据(翻过末页)**, 不是错误:
      - rc=102: 东财明确表示"没有更多数据"(pn 超过末页时必然出现)
      - rc=0 且 diff 为空: 同样按"到底"处理
    只有**明确异常**(rc 既非 0 也非 102)才抛 RuntimeError。

    2026-09-19 修复(竞价窗口熔断事故根因): 原实现把 `rc != 0` 一律当"接口返回异常"抛,
    而全市场分页固定请求 SPOT_MAX_PAGES(30) 页 —— 各板块真实页数只有 ceil(total/200)
    (实测沪深主板 18 页 / 创业板 8 页 / 科创板 4 页), 越界页必然命中 rc=102 →
    被判"整批故障"(失败页 ≥ 成功页) → 触发 eastmoney_clist 熔断, 且每轮轮询复现,
    于是**每个交易日 09:15:12 起熔断到 09:29** —— 正好覆盖整个竞价窗口,
    9_25 定格只能降级用兜底源(无 f630 异动字段)。
    逐页实测：失败**只出现在第 19 页以后, 1~18 页零失败**, 150ms 慢速串行同样复现
    → 确定性行为, 与请求频率、出口 IP 均无关(不是限流)。
    详见 docs/diagnosis-20260919-clist-paging-circuit-breaker.md

    fid: "f3"=按涨幅排序(竞价模式取强票榜) / "f12"=按代码排序(全市场分页, 稳定不漏票)。
    fields: 字段表, 默认 `config.FIELDS`(实时行情口径)。2026-09-28 起可传自定义字段表
      (如只取 f12,f26 拉上市日期) —— 复用同一份 URL/分页/rc 处理, 不必为静态数据另写一遍。"""
    qs = urllib.parse.urlencode({
        "fs": fs, "fltt": 2, "invt": 2, "fields": fields or config.FIELDS,
        "fid": fid, "po": 1, "pn": page, "pz": _CLIST_PZ, "np": 1, "ut": config.EASTMONEY_UT,
    })
    req = urllib.request.Request(config.EASTMONEY_URL + "?" + qs, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    })
    # 服务器缺 CA 证书 → clash 校验失败; 用 unverified context(保留TLS加密), 否则竞价全市场快照全挂
    with _http_get(req, timeout=10, context=_NO_VERIFY_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rc = data.get("rc")
    d = data.get("data") or {}
    diff = d.get("diff") or []
    if diff:
        return _ClistPage(diff, d.get("total"))
    if rc in (0, _EM_RC_END):
        # 翻过末页 / 空数据页: 正常"到底"语义, 返回空(旧实现此处抛异常 → 误熔断)
        return _ClistPage([], d.get("total"))
    raise RuntimeError("东方财富接口返回异常 rc=%s" % rc)


# ---- 上市日期（2026-09-28 新增）----
# 用途：dev_risk 落实交易所规则「新股上市后**前 5 个交易日不设涨跌幅限制**，异动从第 6 个
#   交易日起算」—— 见 services/dev_risk.py 的 _sixth_trade_day()。本仓库原先**没有任何**
#   上市日期数据源，故这里新增一条（东财 clist f26）。
_LISTING_FIELDS = "f12,f26"      # f12=代码, f26=上市日期(YYYYMMDD)
# 沪深主板 + 创业板 + 科创板 + 北交所（与 dev_risk 的板别划分对齐；北交所需带上）
_LISTING_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_LISTING_MAX_PAGES = 30          # 每页 200 ⇒ 30 页够 6000 只（与 SPOT 全市场分页同口径）


def fetch_listing_dates():
    """全市场 `code -> 上市日期 'YYYY-MM-DD'`（东财 clist f26；取不到日期的票不出现在结果里）。

    ★ 上市日期是**静态**数据 ⇒ 12h 跨进程缓存（`cached_singleflight`，防并发击穿），
      一天最多打 2 次、每次 ~28 页，成本可忽略。
    ★ **不**计入 `eastmoney_clist` 熔断统计：那是「全市场实时行情」链路的健康指标，
      把这条静态查询混进去会把行情熔断的门槛算歪。本函数失败即返回已取到的部分（通常 {}）。
    ★ 失败语义：调用方（dev_risk）拿不到日期时**不做新股校验**（保守回退到原行为），
      绝不因为这条辅助数据把正常票判成不可算。
    """
    def _load():
        out = {}
        for page in range(1, _LISTING_MAX_PAGES + 1):
            try:
                rows = _fetch_clist_page(_LISTING_FS, page, fid="f12", fields=_LISTING_FIELDS)
            except Exception as e:                             # noqa: BLE001
                log.warning("上市日期取数失败 page=%d err=%s（返回已取到的 %d 只）", page, e, len(out))
                break
            if not rows:
                break
            for r in rows:
                code = str(r.get("f12") or "")
                d = str(r.get("f26") or "")
                if code and len(d) == 8 and d.isdigit():
                    out[code] = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
        return out

    try:
        return cached_singleflight(store, "listing_dates:v1", 12 * 3600, _load) or {}
    except Exception as e:                                     # noqa: BLE001
        log.warning("上市日期读取失败（本次不做新股校验） err=%s", e)
        return {}


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
    if not diff:
        # 2026-09-19: _fetch_clist_page 现在把"空数据"当正常返回(到底语义, 见其 docstring),
        # 但本函数只取第 1 页 —— 第 1 页为空即"该分区一只都没有", 属真故障。
        # 在此显式判失败, 保持本函数原有"空即异常"契约(上游按异常走兜底)。
        _record("eastmoney_clist", False)
        raise RuntimeError("东方财富接口返回异常(首页无数据)")
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
        "f4": pre_close, "f5": vol_hand,   # 2026-09-01 修复: 缺 f4/f5 会被停牌判定误判(f4<=0 或 f5==0)
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
    2026-09-01 修复: 原映射缺 f4/f5, 停牌判定(f4<=0 或 f5==0)把兜底数据全误杀
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


# 全市场分页快速失败阈值(2026-09-10 生产雪崩加固)
# 背景: 原实现 as_completed 整体超时 40s 且无失败率判定 —— 东财故障态每页 15s 超时、
# 30 页 8 并发最坏 ~56s, 两个 uvicorn worker 被占满后**连登录都排队 38 分钟**。
# 正常态实测 30 页仅 0.4s(生产 2026-09-10 10:22 实测), 故阈值可定得很激进而不误伤。
_SPOT_ALL_TIMEOUT = 12      # 整体超时(秒): 正常态 30x 余量, 故障态快速放弃交腾讯兜底
_SPOT_FAST_FAIL = 5         # 完成顺序连续失败 ≥N 页且无一成功 → 判定整源故障(秒拒场景)
_SPOT_FAIL_SAMPLE = 8       # 已完成 ≥N 页且失败过半 → 判定整源故障(部分超时场景)


def _page_count(total, pz=_CLIST_PZ):
    """真实页数 = ceil(total / pz); total 缺失或非正 → None(调用方回退固定上限)。"""
    try:
        t = int(total or 0)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return max(1, -(-t // pz))


def fetch_eastmoney_all(fs):
    """盘中实时模式: 分页拉取全市场股票快照(每页 200, 页数按 total 动态算),
    让过滤参数(涨幅/量比/换手)真正作用于全市场, 而不是只取涨幅前 200。
    按代码(f12)排序分页: 位置稳定, 任一分页失败只跳过该页, 不漏已跌出榜单的票。
    并发拉取(2026-08-19 起): 第 1 页串行(取数据 + 取 total), 其余页 ThreadPoolExecutor 并发。
    空页=到底(提前结束), 任一页失败跳过该页, 首页失败/过半失败抛异常交兜底。

    2026-09-19 修复(竞价窗口熔断根因): 原实现**固定并发请求 SPOT_MAX_PAGES(30) 页**,
    而各板块真实页数只有 ceil(total/200)(实测沪深主板 18 / 创业板 8 / 科创板 4 页)
    → 越界页命中东财 rc=102「没有更多数据」→ 被当"接口异常"计入失败页 →
    失败页 ≥ 成功页 → 判整批故障 → eastmoney_clist 熔断; 每轮轮询复现,
    于是**每交易日 09:15:12 起熔断到 09:29**, 正好覆盖竞价窗口
    (9_25 定格降级用兜底源 → 无 f630 异动字段)。
    现改为: ① 第 1 页串行取回 total, 只请求必要页数(+1 页探测页兜住 total 少报);
    ② 越界/空页在 _fetch_clist_page 层已是正常"到底"而非异常(双保险)。
    实测请求量: 90 次/轮 → 33 次/轮(18/8/4 → 19/9/5, 各含 1 探测页)。
    详见 docs/diagnosis-20260919-clist-paging-circuit-breaker.md"""
    if _check_circuit():
        raise RuntimeError("东财数据源熔断中, 快速失败(交腾讯兜底)")
    t_all = time.time()
    # ① 第 1 页串行: 既取数据, 也用它带回来的 total 算真实页数
    try:
        first = _fetch_clist_page(fs, 1, "f12")
    except Exception as e:
        log.warning("全市场首页拉取失败 fs=%s err=%s", fs, str(e)[:80])
        _record("eastmoney_clist", False, int((time.time() - t_all) * 1000))
        raise
    if not first:
        # 首页为空 = 该分区无数据, 属真故障(第 ≥2 页为空才是"到底")
        _record("eastmoney_clist", False, int((time.time() - t_all) * 1000))
        raise RuntimeError("东方财富接口返回异常(首页无数据)")
    total_raw = getattr(first, "total", 0)
    real_pages = _page_count(total_raw)
    # +1 探测页: total 若少报一档, 该页会拿到真实末页; 若 total 准确, 该页返回空(不报错)
    n_pages = config.SPOT_MAX_PAGES if real_pages is None else min(real_pages + 1,
                                                                 config.SPOT_MAX_PAGES)
    # ② 其余页并发
    pages_data = {1: first}   # page -> diff list(失败为 None)
    done_ok, done_fail, streak_fail = 1, 0, 0
    aborted = ""
    futs = {}
    if n_pages > 1:
        ex = _EXECUTOR_CLIST
        futs = {ex.submit(_fetch_clist_page, fs, p, "f12"): p
                for p in range(2, n_pages + 1)}
        try:
            for fut in as_completed(futs, timeout=_SPOT_ALL_TIMEOUT):
                p = futs[fut]
                try:
                    pages_data[p] = fut.result()
                    done_ok += 1
                    streak_fail = 0
                except Exception as e:
                    log.warning("全市场拉取分页失败 fs=%s page=%d err=%s", fs, p, str(e)[:80])
                    pages_data[p] = None
                    done_fail += 1
                    streak_fail += 1
                # 快速失败: 故障态没必要等满所有页, 越早放弃越早切腾讯兜底
                if streak_fail >= _SPOT_FAST_FAIL and done_ok <= 1:
                    aborted = "首页后连续%d页失败" % streak_fail
                    break
                if (done_ok + done_fail) >= _SPOT_FAIL_SAMPLE and done_fail * 2 > done_ok + done_fail:
                    aborted = "已完成%d页中失败%d页(过半)" % (done_ok + done_fail, done_fail)
                    break
        except TimeoutError:
            aborted = "整体超时%ds(已完成%d页/失败%d页)" % (_SPOT_ALL_TIMEOUT, done_ok, done_fail)
    if aborted:
        # 取消尚未启动的排队页, 减少故障期外网无效请求; 已在运行的页无法中断, 由线程池自然回收
        for f in futs:
            f.cancel()
        log.warning("全市场分页快速失败 fs=%s %s → 取消剩余页, 交腾讯兜底", fs, aborted)
    # 整批只记一次熔断采样(2026-09-10): 原逐页 _record 让 30 个采样点各自判定,
    # 单次调用内"失败页熔断 + 成功页解除"交错 → 熔断横跳; 且 down_threshold=1 的源
    # 会被任一页抖动误熔断。改为按整批成败判定: 失败页 ≥ 成功页 才判整批失败。
    if aborted or (done_fail and done_fail >= done_ok):
        _record("eastmoney_clist", False, int((time.time() - t_all) * 1000))
    else:
        _record("eastmoney_clist", True, int((time.time() - t_all) * 1000))
    if aborted:
        raise RuntimeError("东财全市场分页快速失败(%s)" % aborted)
    # 按 page 顺序合并, 遇到空页/短页即终止(后续页不会有效数据)
    out = []
    last_page = 0
    for p in range(1, n_pages + 1):
        diff = pages_data.get(p)
        if not diff:
            if diff is None:
                continue   # 该页失败, 跳过(不终止, 后续页可能成功)
            break          # 空页 = 到底
        out.extend(diff)
        last_page = p
        if len(diff) < _CLIST_PZ:
            break          # 最后一页
    log.info("全市场行情拉取成功 fs=%s 共%d只(%d页/请求%d页 total=%s) 并发耗时%.0fms",
             fs, len(out), last_page, n_pages, total_raw or "-", (time.time() - t_all) * 1000)
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
        raw = _fetch_market_all_with_fallback(scorer.market_fs(list(scorer.ALL_MARKETS)))
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
    """全市场行情(2026-09-10 主人拍板: 去掉所有兜底, 单一东财源, 失败即失败)。

    历史(已废弃): 原为 东财 → 腾讯 → 量脉 三级兜底链。废弃原因:
      1) 兜底源无 f615/f616/f617 竞价专属字段, 只能用"现价涨幅/成交额"近似填充,
         竞价时段一旦切兜底, 竞价数据即为编造值 → 选股结果失真(主人反馈"选出来不对");
      2) 东财是滑动风控窗口会自愈, 但熔断一旦打开即整段冷却期"不敢试",
         流量全推给兜底源 → 主源与兜底源横跳, 数据口径在两次请求间跳变;
      3) 2026-09-10 实测: 主力源 push2dycalc 盘中 11-14 时成功率 99-100%,
         兜底触发集中在 eth1 网卡事故时段与收盘批量时段, 兜底并未提升真实可用性。
    现行为: 只调东财, 失败直接抛出(由上层决定沿用旧缓存或报错)。"""
    return fetch_eastmoney(fs)


def _fetch_market_all_with_fallback(fs):
    """全市场分页行情(2026-09-10 主人拍板: 去掉所有兜底, 单一东财源)。
    覆盖 ensure_spot_cache 外的路径(351/497/auction_snapshot); 废弃原因见
    _fetch_market_with_fallback。失败直接抛出。"""
    return fetch_eastmoney_all(fs)


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
                # 2026-09-10 去兜底: 不再切腾讯。失败时仅沿用本地旧缓存(纯本地行为,
                # 不引入异源语义), 无旧缓存则直接抛出, 让上层如实报错。
                log.warning("盘中全市场拉取失败(东财) fs=%s err=%s", fs, str(e)[:120])
                if entry is not None:
                    log.warning("盘中拉取失败, 沿用旧缓存 fs=%s", fs)
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
                raw, reused = _spot_fetch_or_share(fs)
                _quote_map_cache[fs] = {"raw": raw, "map": _build_quote_map(raw), "ts": now}
                log.info("全市场行情map刷新 fs=%s 共%d只%s", fs, len(raw),
                         "(复用另一进程, 未出网)" if reused else "")
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


# ---------- 按代码点查实时行情 map（2026-09-29 P0③）----------
# 背景: 竞价异动页现涨(51 只)/三时点现涨(51 只)/竞价爆量(几十~几百只)原来**各自**调
#   `fetch_spot_quote_map` ⇒ 为这几十一几百只票拉**全市场 5561 只(33 个 HTTP 请求)**,
#   而且都在**用户请求线程**里同步做。生产实测(2026-09-29): 09:14~09:29 竞价窗口内
#   全市场拉取 8 次、`429|限流` 日志 241 条 —— 这段窗口同时要跑定格采集/净额量比补采/名单,
#   是最不该被浪费的一段(主人口径: 竞价及时性 ＞ 其它)。
# 现改为: 按代码点查东财 ulist(`fetch_raw_by_codes`: 每批 60、多域名顺序重试、**整段复用
#   config.FIELDS 与 clist 完全同构**) ⇒ 51 只 = 1 个请求, 407 只 = 7 个请求;
#   逐 code 缓存 `_CODE_QUOTE_TTL`(= SPOT_CACHE_TTL 口径, 前端 30s 轮询 ⇒ 高命中)。
# 失败返回 {} ⇒ 调用方自行回退全市场 spot map(旧路径保留, 可用性不降低)。
# 附带收益: ulist 路径不受 `fetch_eastmoney_all` 的 clist 熔断开关影响 ⇒ 东财 clist
#   熔断窗口(如 09:15 那段)里现涨仍有独立来源。
_CODE_QUOTE_TTL = 60
_code_quote_cache = {}          # {code: (quote_entry, ts)}
_code_quote_lock = threading.Lock()


def fetch_spot_quote_map_by_codes(code_list):
    """按代码点查实时行情: 返回 {code: {realChange, entityChange, price, volRatio, turnover, name}}。
    只取需要的代码, 不再为几十只票拉全市场; 失败返回 {}(调用方回退全市场路径)。"""
    if not code_list:
        return {}
    uniq = [c for c in dict.fromkeys(str(c) for c in code_list) if c]
    now = time.time()
    with _code_quote_lock:
        need = [c for c in uniq
                if c not in _code_quote_cache or now - _code_quote_cache[c][1] > _CODE_QUOTE_TTL]
    if need:
        try:
            rows = fetch_raw_by_codes(need)
        except Exception as e:                                  # noqa: BLE001
            log.warning("按code点查实时行情失败(调用方回退全市场) %d只 err=%s",
                        len(need), str(e)[:120])
            rows = []
        if rows:
            built = _build_quote_map(rows)
            got = time.time()
            with _code_quote_lock:
                for c, q in built.items():
                    _code_quote_cache[c] = (q, got)
            log.info("按code点查实时行情 共%d只(本次需%d) 返回%d只",
                     len(uniq), len(need), len(built))
    with _code_quote_lock:
        return {c: _code_quote_cache[c][0] for c in uniq if c in _code_quote_cache}


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
        base = scorer.market_fs(list(scorer.ALL_MARKETS))
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
#
# 🔴 2026-09-19 真因订正: **封的是域名, 不是接口**。同一 path、同一批参数实测(测试机):
#     https://push2.eastmoney.com/api/qt/ulist.np/get      → RemoteDisconnected(48ms 秒断)
#     https://push2dycalc.eastmoney.com/api/qt/ulist.np/get → rc=0 正常返回, **且带 f630**
#                                                             (000001/300434/002584 实测均为 4)
#   原先写死 push2 ⇒ fetcher.fetch_raw_by_codes / picker「eastmoney_realtime」补丁源**必然失败**
#   ⇒ 每次降级腾讯点查, 而**腾讯无 f630** ⇒ 生产日志长期刷「选股快照候选池东财点查失败→腾讯
#   点查兜底成功」。当初「换完整浏览器特征仍失败」的结论之所以错, 是因为**域名根本没换**
#   —— 特征换一百遍也救不了一个被封的域名(详见 docs/legacy-baseline-audit.md §5.3)。
#   ⇒ 改为**多域名顺序重试**(与 config.KLINE_HOSTS / hot_rank._fetch_em_quotes 同一套路):
#     连接失败的域名进 _broken_hosts 冷却 300s, 上游域名恢复后自动重新探测。
#   旁证: hot_rank.py 早已把 push2dycalc 版 ulist 排首选并注释「测试机可用」——该知识存在过,
#   只是没同步到 fetcher。
_ULIST_HOSTS = (
    "https://push2dycalc.eastmoney.com",   # 首选: 与全市场 clist 同域名, 实测畅通
    "https://push2.eastmoney.com",         # 备用: 长期 RemoteDisconnected, 留作域名切换兜底
)
_ULIST_PATH = "/api/qt/ulist.np/get"
_ULIST_BATCH = 60     # 每批 ≤60 只(实测 200 只 URL 过长; 60 稳)
_ULIST_TIMEOUT = 10


def _fetch_ulist_batch(qs):
    """单批 ulist 点查: 按 _ULIST_HOSTS 顺序重试, 成功即返回 diff(可能为空列表)。

    - 连接层异常 → `_mark_host_broken` 标记该域名(冷却 300s)后换下一个域名;
    - `rc != 0` 属**数据层**错误(域名是通的) → 不标记域名, 仅换下一个域名重试;
    - 全部域名都失败 → 抛 RuntimeError, 调用方降级腾讯点查 / 全市场;
    - 全部域名都在冷却中 → 仍逐一尝试(fail-open: 否则上游恢复后无人探测、永久哑火)。

    ⚠️ 顺序重试只在**同一次调用**内生效; 跨调用靠 _broken_hosts 冷却避免反复打被封域名。
    """
    hosts = [h for h in _ULIST_HOSTS if not _host_blocked(h)] or list(_ULIST_HOSTS)
    err = None
    for host in hosts:
        try:
            req = urllib.request.Request(host + _ULIST_PATH + "?" + qs, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://quote.eastmoney.com/"})
            with _http_get(req, timeout=_ULIST_TIMEOUT, context=_NO_VERIFY_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:                                    # noqa: BLE001
            err = e
            _mark_host_broken(host)
            log.warning("东财 ulist 点查连接失败 host=%s err=%s", host, e)
            continue
        if data.get("rc") != 0:
            err = RuntimeError("东财 ulist 返回异常 rc=%s" % data.get("rc"))
            log.warning("东财 ulist 返回异常 host=%s rc=%s", host, data.get("rc"))
            continue
        return (data.get("data") or {}).get("diff") or []
    raise RuntimeError("东财 ulist 点查失败(已试域名 %s): %s" % (",".join(hosts), err))


def fetch_raw_by_codes(code_list, extra_fields=None):
    """按 code 批量拉**完整行情 diff**(盘后 filter 快照候选补评分用, 2026-09-07):

    `extra_fields`: 2026-09-28 新增(供 /api/yijiner 取 **f26 上市日期** —— 它不在全局
    `config.FIELDS` 里)。**只允许追加**: 最终 fields 恒为
    `config.FIELDS + "," + extra_fields`; 默认 `None` ⇒ 请求参数与改动前**逐字节一致**。
    ⚠️ **绝不可用它替换 config.FIELDS**(见文末"漏字段"生产事故)。
    候选池先用 9:25 快照表初筛(免费), 命中几十只再这里点查, 替代"实时拉全市场 28 页"。
    返回与 fetch_eastmoney_all/clist **完全同构**的 diff 列表(fields=config.FIELDS, 与
    _fetch_clist_page 同用), 可直接喂 picker 契约层做完整评分; 逐批直拉东财
    ulist(不依赖 spotMap 缓存, 评分字段全), 任一批失败抛异常(调用方降级回全市场)。
    走 _http_get(自动出站 IP 轮询)。
    ⚠️ 2026-09-19 修: 原写死 push2.eastmoney.com(整站 RST) → 本函数**必然失败**、补丁源形同
    虚设。现按 _ULIST_HOSTS(push2dycalc 首选)顺序重试, 见该常量注释。
    ⚠️ 2026-09-07 晚修: 曾只列 15 字段漏 **f615(竞价涨幅)/f17(今开)/f630 等** → scorer
    get_bid_change 无 f615 退 f3(现价/收盘涨幅) → 竞涨列=现涨列 + 「涨幅≤bidGt」过滤按
    现价判 → 盘后筛出一批大跌票(生产事故)。必须整段复用 config.FIELDS 防再次漏字段。"""
    if not code_list:
        return []
    out = []
    t0 = time.time()
    # 整段复用 config.FIELDS, 仅允许在其后追加 extra_fields; 默认 None ⇒ 与改动前一致
    fields = config.FIELDS + ("," + extra_fields if extra_fields else "")
    for i in range(0, len(code_list), _ULIST_BATCH):
        chunk = code_list[i:i + _ULIST_BATCH]
        secids = ",".join(_secid(c) for c in chunk)
        qs = urllib.parse.urlencode({
            "fltt": 2, "invt": 2,
            "fields": fields,            # 与全市场 clist 同构(必须整段复用, 勿手写子集!)
            "secids": secids, "ut": config.EASTMONEY_UT,
        })
        out.extend(_fetch_ulist_batch(qs))
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
    返回与 fetch_raw_by_codes **完全同构**的 diff 列表, 可直接喂 picker 契约层。

    ⚠️ 竞价字段近似: 腾讯无竞价专属字段, f615=现价涨幅 / f616=累计成交额(元)。竞价模式
    窗口外会被 9:25 定格 day_bid_change/day_bid_amt map 覆写(见 picker/pipeline.py),
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

# 🔴 2026-09-29 (P0① 竞价窗口让路 + 跨进程共享一次出网)
#   1) 窗口起点 09:26 → **09:28**: 09:26:30~09:26:38 是 9_25 定格采集、≈09:26:45/09:27:20 是
#      净额/量比补采 —— spot 全市场(28 页)挤进来会与"定格/补采/名单"抢上游配额与 SQLite 写锁。
#      改到 09:28 起仍保证 09:30 首个 refresh 命中缓存(40s 周期 ⇒ 09:28 / 09:28:40 / 09:29:20 三轮)。
#   2) 共享 raw: 原实现"每个 web worker 各拉一份全市场"(生产 2 worker ⇒ 上游调用翻倍)。
#      `_quote_map_cache` 是**进程级**的 ⇒ 不能简单"加锁只让一个进程拉"(另一进程缓存会凉、
#      请求路径又要同步拉一次, 长尾回来)。故改为: 谁真出网就把**裁剪后的原始行情**发布到 kv
#      (TTL 45s), 另一进程直接复用重建 map ⇒ 零上游、零外网, 两边缓存都热。
#      裁剪字段 = 重建 map 所需(f2 价/f3 现涨/f8 换手/f10 量比/f12 代码/f14 名/f17 今开)。
_SPOT_SHARE_TTL = 45
_SPOT_SHARE_FIELDS = ("f12", "f14", "f2", "f3", "f8", "f10", "f17")


def _spot_share_trim(raw):
    """裁剪成重建 map 所需的最小字段集(全量 raw 有 20+ 字段, 原样进 kv 每 40s 要写几 MB)。"""
    return [{k: (r or {}).get(k) for k in _SPOT_SHARE_FIELDS} for r in (raw or [])]


def _spot_share_get(fs):
    """复用**另一个 web worker** 刚拉的全市场原始行情; 无/过期返回 None。"""
    try:
        hit = store.get("spot:raw:" + fs)
    except Exception:                                          # noqa: BLE001
        return None
    if isinstance(hit, dict) and hit.get("raw") \
            and time.time() - (hit.get("ts") or 0) < _SPOT_SHARE_TTL:
        return hit["raw"]
    return None


def _spot_share_put(fs, raw):
    """把刚拉到的原始行情发布给另一个 worker(45s < 预热周期 40s + 缓存 TTL 60s)。"""
    try:
        store.set("spot:raw:" + fs, {"raw": _spot_share_trim(raw), "ts": time.time()},
                  ttl=_SPOT_SHARE_TTL)
    except Exception:                                          # noqa: BLE001
        pass


def _spot_lease_try(fs):
    """尝试抢"本轮出网令牌"; 异常视为**直接出网**(共享只是优化, 不是依赖)。"""
    try:
        return bool(store.setnx("spot:lease:" + fs, 1, ttl=10))
    except Exception:                                          # noqa: BLE001
        return True


def _spot_fetch_or_share(fs, wait_for_peer=False):
    """取全市场原始行情: 优先复用另一进程刚拉的(kv 共享), 否则真出网并发布。

    返回 (raw, reused): reused=True 表示本轮**未出网**, 用的是另一进程的成果。

    🔴 2026-09-29 (P0① 第二轮): 加"本轮出网令牌"(setnx) —— 生产实测两个 web worker 同时
    重启时相位对齐(11:28:10 两个进程同秒 start), 会**同一瞬间**判定"无共享"而各拉一份
    (实测 3 轮里有 1 轮双出网)。现在: 抢到令牌的进程出网并发布; 没抢到的 —— 仅预热线程
    (`wait_for_peer=True`)最多等 4s 读共享, 拿到就零出网; 等不到才自己拉(兜底, 不因令牌
    丢失而不预热)。**请求路径不等待**(wait_for_peer=False): 它持着 `_quote_map_lock`,
    在里面 sleep 会阻塞同进程其它请求。令牌 TTL 10s 自过期 ⇒ 持有者崩溃不会永久挡住对端。
    """
    raw = _spot_share_get(fs)
    if raw is not None:
        return raw, True
    if not wait_for_peer or _spot_lease_try(fs):
        raw = _fetch_market_all_with_fallback(fs)
        _spot_share_put(fs, raw)
        return raw, False
    for _ in range(8):                       # 最多等 4s(东财全市场实测 0.6~4s)
        time.sleep(0.5)
        raw = _spot_share_get(fs)
        if raw is not None:
            return raw, True
    raw = _fetch_market_all_with_fallback(fs)      # 兜底: 令牌持有者久未发布也不空转
    _spot_share_put(fs, raw)
    return raw, False


def spot_prewarm_active(now_ts):
    """是否处于 spotMap 预热窗口: 工作日北京时间 **9:28**-15:05。
    9:28 起预热: 避开 9_25 定格采集(09:26:30~38)与净额/量比补采(≈09:26:45/09:27:20),
    又保证 9:30 首个 refresh 直读即命中缓存。"""
    g = time.gmtime(now_ts + 8 * 3600)
    if g.tm_wday >= 5:
        return False
    hm = g.tm_hour * 60 + g.tm_min
    return 9 * 60 + 28 <= hm <= 15 * 60 + 5


def _spot_prewarm_fs_set():
    """预热 fs 集合: 默认**全市场**(含北交所, 见 scorer.ALL_MARKETS) + 缓存中出现过的其它 fs"""
    base = scorer.market_fs(list(scorer.ALL_MARKETS))
    return sorted(set(list(_quote_map_cache.keys())) | {base})


def _spot_prewarm_once():
    """刷新一次全部预热 fs 的 spotMap 缓存; 单 fs 失败不影响其它/下轮自愈"""
    for fs in _spot_prewarm_fs_set():
        try:
            raw, reused = _spot_fetch_or_share(fs, wait_for_peer=True)
            with _quote_map_lock:
                _quote_map_cache[fs] = {"raw": raw, "map": _build_quote_map(raw), "ts": time.time()}
            log.info("spotMap预热完成 fs=%s 共%d只%s", fs, len(raw),
                     "(复用另一进程, 未出网)" if reused else "")
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
    log.info("spotMap预热线程已启动(工作日9:28-15:05每%ss刷新一次, 跨进程共享一次出网)",
             _SPOT_PREWARM_PERIOD)


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


def _fetch_yesterday_amount_tencent(code, after_close=None):
    """腾讯日K兜底源(第三源, 2026-08-31 东财K线被生产机IP封禁后新增):
    返回最近两交易日成交额 [T日, T-1日] 万元 + T日涨跌幅%; 失败返回 None
    接口: proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get
    qfqday 行: [date, open, close, high, low, volume, {}, 涨跌, 成交额(万元), '']
    盘中跳过今天(未收盘)行, **收盘后(≥15:05)不跳过**(与东财/同花顺语义一致,
    见 _after_close) → 保证 T = 最近已收盘交易日, 收盘后即今天。
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
        if after_close is None:
            after_close = _after_close()
        pairs = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 9:
                continue
            dstr = str(row[0])[:10].replace("-", "")
            if dstr == today and not after_close:
                continue  # 盘中: 今天未收盘, 跳过; 收盘后: 已定格, 参与计算
            try:
                amt = float(row[8])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(amt) and amt > 0):
                continue
            try:
                close = float(row[2])
                if not (math.isfinite(close) and close > 0):
                    close = None
            except (TypeError, ValueError):
                close = None
            pairs.append((dstr, amt, close))
        if len(pairs) >= 2:
            # ★ 2026-09-26 修复(生产 v4.11.53): 收盘后 T **必须确实是今天**。
            #   与东财(_kline_amount_pair)/猫爪(_yday_pair_from_daily)同一条纪律 ——
            #   腾讯是第三源, 但"源不同、纪律必须相同", 否则换源即复发。
            #   注意此处**不记 _record(成功)**: 拿不到今天的行属"源尚未更新", 不是
            #   源故障, 记失败会误伤熔断统计。
            #   2026-09-26 补丁(v4.11.55): 校验目标改 `_yday_expected_tdate()`
            #   (非交易日盘后 = 上一交易日), 修掉"周末/节假日盘后恒失败"。
            if after_close:
                want = _yday_expected_tdate()
                if want and pairs[-1][0] != want:
                    return None, None
            _record("tencent_kline", True, int((time.time() - t0) * 1000))
            # 2026-09-08: 顺带返回 T 日真实涨跌幅 —— 腾讯 qfqday 无涨跌幅列
            # (row[7] 是换手率), 用**收盘价环比自算**(实测 600127: 14.79/13.53 → 9.31%)。
            # 返回值契约与东财/同花顺一致: (成交额对, 涨跌幅% 或 None)
            c1, c0 = pairs[-1][2], pairs[-2][2]
            chg = round((c1 - c0) / c0 * 100, 2) if (c1 and c0 and c0 > 0) else None
            return [pairs[-1][1], pairs[-2][1]], chg
    except Exception as e:
        log.warning("腾讯日K成交额拉取失败 code=%s err=%s", code, str(e)[:100])
    _record("tencent_kline", False)
    return None, None


def _fetch_yesterday_amount_one(code):
    """拉单只股票最近两交易日成交额(万元): 返回 (成交额对, T日涨跌幅%); 完全失败返回 (None, None)

    🔴 最终口径（2026-09-28 + 09-29 主人两轮拍板）：
      链路 = 猫爪 daily（主，批量见 _yday_fill_from_meoz）→ **腾讯 qfqday（首选备源）**
             → **东财 push2his（末位兜底）**。即：**东财已退出主链**，只在腾讯也拿不到时才走。

      · 为什么腾讯排前面：qfqday 第 9 列 = **真实成交额(万元)**（2026-09-28 两台机实测
        600519 → [348872.06, 386731.09]，与东财同量纲），且实测稳定、无风控。
      · 为什么**仍然保留东财**（主人 09-29："如果影响逻辑计算，还可以考虑使用东财"）：
        有两处**确实会改计算口径**的差异，只有东财能补 ——
          ① **涨跌幅来源**：东财 `parts[7]` = **官方 f58 涨跌幅**；腾讯 qfqday 没有涨跌幅列，
             只能用**前复权收盘价环比自算** ⇒ 平日等价，但**除权除息日会与官方值有偏差**。
             而"昨日涨幅"是评分因子(权重 6%)，偏差会直接改分档。
          ② **覆盖率**：腾讯稳定缺 ~8 只(0.14%)，东财可补。
        代价可控：只在腾讯返回 None 后调用一次；东财自身熔断(down_threshold=1/冷却60s)与
        域名冷却(_HOST_COOLDOWN=300s)会把后续调用短路，不会退化成"每次都打"。
      历史：原四级链(东财→同花顺→腾讯→量脉) 在 2026-09-10 二审删到「东财+腾讯」；
            09-28 曾把东财整体摘除；09-29 按"不得影响逻辑计算"复位为**末位兜底**。
      · 保留"收盘后 T 必须确实是今天"的纪律（两个备源各自都有，见其注释）。
    """
    pair, chg = _yday_fallback_tencent(code)
    if pair is not None:
        return pair, chg
    return _yday_fallback_eastmoney(code)


def _yday_fallback_tencent(code):
    """昨比首选备源: 腾讯 qfqday 第 9 列 = **真实成交额(万元)**, 份额为真, 非"编造值"。

    2026-09-10 二审修订背景: 东财 push2his 是接口级时段性风控(实测 chart 命中率约 5%),
    只留东财会让「收盘落库」永远填不上库 → 次日全天昨比仍为空, 落库机制空转。
    腾讯是不可编造的真实 OHLC + 成交额, 与本轮被删除的"竞价字段 f615/f616 用现价假造"
    性质不同(那是换源换数据, 这里是换源不换数据)。
    腾讯也在熔断中时直接放弃(评分层容忍昨比缺失)。
    """
    if _check_circuit("tencent_kline"):
        return None, None
    try:
        return _fetch_yesterday_amount_tencent(code)
    except Exception:
        return None, None


def _yday_fallback_eastmoney(code):
    """昨比**末位兜底**: 东财 push2his 日K —— 仅当腾讯也失败/缺票时才走（2026-09-29 复位）。

    位置纪律：东财**不在主链**，只作为"会改计算口径"的补偿手段存在 ——
    它提供**官方 f58 涨跌幅**（腾讯只能按前复权收盘价环比自算，除权日会偏），且覆盖更全。
    性能纪律：熔断(eastmoney_kline, down_threshold=1/冷却60s) + 域名冷却(300s)
    保证风控期不会反复重试、坏域名不拖慢整批；返回契约与腾讯腿完全一致。
    """
    if _check_circuit("eastmoney_kline"):
        return None, None
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
            return pair, chg      # 官方 f58 涨跌幅（不依赖复权价环比）
        except Exception:
            _mark_host_broken(host)
            continue
    _record("eastmoney_kline", False)
    return None, None


def _kline_amount_pair(klines, close_idx=2, chg_idx=7, after_close=None):
    """从日K行(逗号分隔)提取 ([最近已收盘T日万元, T-1日万元], T日涨跌幅%);
    自动跳过"今天"(未收盘)的K线, 保证 pair[0] 恒为最近已收盘交易日全天额。
    东财日期格式 YYYY-MM-DD, 同花顺 YYYYMMDD, 两种都兼容; 不足/无效返回 (None, None)。
    (修复: 东财盘中含今天未收盘K线, 同花顺不含 → 两源 pair 语义曾不一致, 导致分母错位)

    after_close: None(默认)=按当前北京时间自动判定(见 _after_close); True/False 可显式
      指定(测试/回放用)。**收盘后(≥15:05)今天的K线已定格 → 不跳过, T 取今天**, 修掉
      "收盘后昨日涨幅整整滞后一天"的语义 bug。

    2026-09-08 昨日涨幅真实化: 东财日K fields2=f51..f58 → parts[7]=涨跌幅(f58),
    与成交额同一次请求返回, **零额外网络开销**。此前评分的"昨日涨幅"因子用的是
    当日 f3 冒充(详见 picker/score.py 缺失值语义), 语义错误; 本函数顺带把 T 日真实涨幅
    带出, 供 fetch_yesterday_changes 使用。

    2026-09-08 兜底源自算涨幅(实测踩坑, 必须区分列序):
      东财行 日期,开,收,高,低,量,额,涨跌幅...   → close_idx=2, chg_idx=7(官方 f58)
      同花顺 日期,开,高,低,收,量,额,**换手率**... → close_idx=4, chg_idx=None(必须自算!)
      ⚠ 同花顺 parts[7] 是**换手率不是涨跌幅**(实测 600127: 20260908 收14.66/昨收13.53
        → 真涨幅 8.35%, 而 parts[7]=30.118 是换手率)。此前沿用东财列序把换手率当涨幅
        喂进 yesterday 因子, 30.118 落进 "9.5~99 → 0.65" 档 → 假数据。
    故: 官方涨跌幅列(chg_idx)缺失时, 一律用**收盘价环比自算**, 口径跨源统一。

    2026-09-11: 同花顺昨比源 _fetch_yesterday_amount_ths 已整体删除 → 其 close_idx=4
    列序配置**暂无生产调用者**(说明保留仅为记录列序语义与上述踩坑);
    现行调用方只有东财(`close_idx=2, chg_idx=7`)与腾讯(默认参数)。
    """
    today = _bj_date_str()
    if after_close is None:
        after_close = _after_close()
    def amt_of(row):
        parts = row.split(",")
        if len(parts) < 7:
            return None, None, None, None
        try:
            v = float(parts[6])
            if not (math.isfinite(v) and v > 0):
                return None, None, None, None
        except (TypeError, ValueError):
            return None, None, None, None
        chg = None
        if chg_idx is not None and len(parts) > chg_idx:
            try:
                c = float(parts[chg_idx])
                if math.isfinite(c):
                    chg = c
            except (TypeError, ValueError):
                chg = None
        close = None
        if close_idx is not None and len(parts) > close_idx:
            try:
                c = float(parts[close_idx])
                if math.isfinite(c) and c > 0:
                    close = c
            except (TypeError, ValueError):
                close = None
        return v / 10000.0, parts[0], chg, close
    def is_today(dstr):
        if not dstr or after_close:
            # 收盘后今天已定格 → 不再跳过(否则"昨日涨幅"滞后一整天)
            return False
        d = dstr.replace("-", "")
        return d == today.replace("-", "")
    # 收集所有 (日期, 金额, 官方涨跌幅, 收盘价), 跳过今天(仅盘中)
    rows = []
    for row in klines:
        amt, dstr, chg, close = amt_of(row)
        if amt is not None and not is_today(dstr):
            rows.append((dstr, amt, chg, close))
    if not rows:
        return None, None
    # ★ 2026-09-26 修复(生产 v4.11.53): 收盘后 T **必须确实是今天**。
    #   上面 is_today() 在 after_close=True 时无条件返回 False(= 不跳过今天), 这是为修
    #   "收盘后昨日涨幅滞后一整天"的语义 bug, 但它隐含假设"今天那根一定拿得到"。
    #   数据源当天还没更新日K时该假设不成立: rows[-1] 会是"昨天那根"却被当作 T 日返回
    #   ⇒ 收盘落库把**旧值标成新 tdate** ⇒ 次日读库命中 ⇒ 数据永久冻结(本次生产事故)。
    #   法定休市日更甚: 数据源永远不会有"今天"的行 ⇒ 每天都把上一交易日复制一份。
    #   故宁可判定失败(返回 (None,None) ⇒ 本只不落库, 收盘窗口内稍后重试), 也绝不输出
    #   **冠错日期**的数据 —— 「没有数据」永远好过「日期错的数据」。
    #   2026-09-26 补丁(v4.11.55): 校验目标由**字面今天**改为 `_yday_expected_tdate()`
    #   (非交易日盘后 = 上一交易日)。原实现用字面 today ⇒ **周末/节假日盘后恒失败**
    #   (今天本就没有 K 线), 而该时刻要取的 T 日恰恰就是上一交易日。语义与本函数开头
    #   "T = 最近已收盘交易日" 完全一致。
    if after_close:
        want = _yday_expected_tdate()
        if want and _norm_d8(rows[-1][0]) != want:
            return None, None
    # 最近已收盘 = 最后一行(按日期), 取它和它前一行
    t = rows[-1][1]
    t1 = rows[-2][1] if len(rows) >= 2 else None
    chg_t = rows[-1][2]                  # T 日(最近已收盘交易日)真实涨跌幅
    if chg_t is None and len(rows) >= 2:
        # 兜底源无官方涨跌幅列(同花顺的 parts[7] 是换手率)→ 用**收盘价环比自算**
        c1, c0 = rows[-1][3], rows[-2][3]
        if c1 and c0 and c0 > 0:
            chg_t = round((c1 - c0) / c0 * 100, 2)
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
    # 2026-09-10 落库优先: 昨日成交额是静态历史数据, 收盘后已批量落库 → 直接读库(零网络)。
    # 只有库里没有的(新股/停牌/落库任务未跑)才进入下面的实时拉取路径。
    all_codes = list(codes)
    pending = _yday_hydrate_from_db(all_codes, today, now)
    need = _collect_yday_need(pending, today, now)
    if need and not _meoz_enabled() \
            and _check_circuit("tencent_kline") and _check_circuit("eastmoney_kline"):
        # 2026-08-31 线上事故: 全源熔断时逐只短路打 WARNING → 36804 条日志风暴,
        # 日志 I/O 阻塞 worker 导致 /api/stocks 674s、health 超时。改为批级短路: 一条聚合日志 + 直接返回
        # 2026-09-10 二审: 短路条件 = 东财日K 与 腾讯K线 **均**熔断(腾讯为同语义备源)
        # 2026-09-24 换源 WP4: 再叠加「猫爪也不可用」 —— 猫爪已成主源, 它可用时
        #   短路会把唯一的活路(批量预填)一起掐掉; 只有全不可用才是"真无源可拉"。
        # 🔴 2026-09-29: 东财复位为**末位兜底** ⇒ 短路条件 = 猫爪不可用 + 腾讯 + 东财 全挂。
        log.warning("昨日成交额: 猫爪/腾讯K线/东财日K 均不可用, 本批%d只短路(昨比置空)", len(need))
        with _yesterday_lock:
            for c in need:              # 短路也写失败缓存, 避免下个请求重复判定
                _yesterday_cache[c] = [today, None, now, None]
        need = []                       # 置空后下面 if need 直接跳过(不再加锁/起空线程)
    if need:
        if wait:
            # 同步路径(后台任务): 等待批锁, 前一个拉取完成后可能已填充缓存 → 重新判定
            # P0-c: 再等**跨进程**令牌(timeout 20s)。另一 worker 拉完后缓存已填, 下面
            #   _collect_yday_need 复查通常得到空 need2 ⇒ 重复拉取自然消失(不等也正确, 只是多拉一遍)。
            tok = store.acquire_sem(_YDAY_SEM, limit=1, timeout=20, expire=_YDAY_SEM_TTL)
            _yday_batch_lock.acquire()
            try:
                need2 = _collect_yday_need(codes, today, now)
                if need2:
                    ok_cnt, fail_cnt = _do_fetch_yesterday(need2, today)
                    if fail_cnt:
                        log.warning("昨日成交额(同步)拉取: 需%d 成功%d 失败%d",
                                    len(need2), ok_cnt, fail_cnt)
            finally:
                _yday_batch_lock.release()
                if tok:
                    store.release_lock(tok)
        else:
            # 异步路径(用户请求): 非阻塞拿锁, 拿到就后台拉; 拿不到说明已在拉, 直接返回缓存
            if _yday_batch_lock.acquire(blocking=False):
                # P0-c: 进程内锁之后再拿**跨进程**令牌(几乎非阻塞) —— 拿不到 = 另一 worker
                #   正在拉同一批 ⇒ 本轮直接返回现有缓存(与"拿不到进程内锁"同一语义,
                #   请求永不因昨比卡顿; 昨比缺失本就被评分层容忍)。
                # 🔴 这里**不能**传 timeout=0: `CacheStore.acquire_sem` 的循环是
                #   `while time.time() < deadline:` —— timeout=0 时 deadline==now ⇒ 循环
                #   体一次都不执行、**永远返回 None** ⇒ 异步路径会永久跳过昨比(实测被
                #   tests/test_yesterday_cache 抓到)。必须给一个极小正数 = "试一次就够"。
                tok = store.acquire_sem(_YDAY_SEM, limit=1,
                                        timeout=_YDAY_SEM_TRY, expire=_YDAY_SEM_TTL)
                if tok is None:
                    _yday_batch_lock.release()
                    log.info("昨比跨进程单飞: 另一 worker 在拉, 本轮跳过 需%d只(用现有缓存)",
                             len(need))
                else:
                    threading.Thread(target=_yday_background_fetch, args=(need, today, tok),
                                     daemon=True, name="yday-bg").start()
    out = {}
    with _yesterday_lock:
        for c in all_codes:
            ent = _yesterday_cache.get(c)
            if ent and ent[0] == today and ent[1] is not None:
                out[c] = ent[1]
    return out


def _yday_hydrate_from_db(codes, today, now):
    """用收盘后落库的昨日成交额填充当日缓存(零网络), 返回仍未命中的 code 列表。

    2026-09-10 新增: 昨日成交额为静态历史数据, 原实现每次选股实时逐只拉东财日K,
    既触发风控又是"多源兜底链"混乱的源头。改为收盘后落库 + 全天读库后, 稳态下
    本函数即可命中全部候选, 完全不发网络请求。

    ★ 2026-09-26 关键修复(生产 v4.11.53): 读库必须带**期望 T 日**(见
      `_yday_expected_tdate`)。此前无条件接受任意"5 天内"的行, 且把库值写成
      `[today, pair, now, chg]` —— **等于向 _collect_yday_need 宣称"今天已经拉到了"**,
      于是 need 恒为空 → 永不重拉; 而收盘落库又把同一批值原样回写、只推进 tdate →
      **数据自首次落库起永久冻结、标签每天前进**(09-14~09-24 实测 9 个交易日)。
      现在: 库里 tdate 与期望 T 日不符 ⇒ 视为没命中 ⇒ 正常进入实时拉取路径。
    """
    if not codes:
        return []
    expect = _yday_expected_tdate(now)
    try:
        dbmap = yday_db_get(codes, expect_tdate=expect)
    except Exception as e:                                    # noqa: BLE001
        log.warning("昨日成交额读库异常(回落实时源) err=%s", e)
        return list(codes)
    if not dbmap:
        return list(codes)
    rest = []
    with _yesterday_lock:
        for c in codes:
            v = dbmap.get(c)
            if v is None:
                rest.append(c)
                continue
            amount, prev_amount, chg = v
            ent = _yesterday_cache.get(c)
            if ent and ent[0] == today and ent[1] is not None:
                continue          # 当日已有成功缓存(刚实时拉过), 不覆盖
            pair = [amount, prev_amount] if amount is not None else None
            _yesterday_cache[c] = [today, pair, now, chg]
    log.info("昨日成交额读库命中 %d/%d 只(零网络, 期望tdate=%s), 剩余%d只走实时源",
             len(dbmap), len(codes), expect or "-", len(rest))
    return rest


def _collect_yday_need(codes, today, now):
    """判定哪些 code 需要(重新)拉取昨比 —— 唯一判定入口, 同步/异步两条路径共用。

    三类需要拉取:
      ① 无缓存 / 缓存不是今天
      ② 成交额对缺失(pair=None)且过了失败重试窗口(YESTERDAY_RETRY_TTL)
      ③ 2026-09-08 新增: 成交额对成功但**涨跌幅缺失**且过了补齐窗口
         (YDAY_CHG_RETRY_TTL) —— 原实现只按 pair 判定, 预热若走了无涨幅的兜底源
         (同花顺/腾讯改造前), chg 整天都是 None, 后来东财恢复也不会重试 →
         yesterday 因子恒 default 0.15, 与"命中"状态差 1.5~4.5 分造成评分漂移。
    """
    need = []
    with _yesterday_lock:
        for c in codes:
            ent = _yesterday_cache.get(c)
            if ent is None or ent[0] != today:
                need.append(c)
            elif ent[1] is None and now - ent[2] >= config.YESTERDAY_RETRY_TTL:
                need.append(c)          # 失败缓存过期, 允许重试(窗口内不再打扰数据源)
            elif ent[1] is not None and _chg_missing(ent) \
                    and now - ent[2] >= config.YDAY_CHG_RETRY_TTL:
                need.append(c)          # 只差涨跌幅 → 按更懒的补齐窗口重拉
    return need


def _chg_missing(ent) -> bool:
    """缓存条目是否缺涨跌幅(ent = [date, pair, ts, chg])"""
    return len(ent) <= 3 or ent[3] is None


def fetch_yesterday_changes(codes, min_coverage=None):
    """真实昨日涨幅 map {code: 涨跌幅%} — 供评分"昨日涨幅"因子使用。

    2026-09-08 语义修正: 此前老链路"昨日涨幅"因子取的是**当日 f3**
    (现价涨幅)冒充, 与因子分档语义(昨日强势 3~9.5% 给高分)完全不符。真实值来自东财
    日K 的 f58 涨跌幅, 与成交额**同一次请求**返回(见 _kline_amount_pair), 故本函数
    只读缓存、**零额外网络请求**。

    缺失语义(契约铁律1): 未拉到 / 走的是无收盘价的兜底源 → 该 code 不出现在返回 map
    中, 调用方按"缺失"处理(不得填 0 冒充)。
    调用顺序: 须在 fetch_yesterday_amounts 之后调用(由其填充缓存)。

    批级一致性(2026-09-08 修「top3 有时90分有时93分」):
      该因子权重 6%, default 0.15 vs 命中 0.4/0.65/0.9 → 单票概率差 1.5~4.5 分。
      命中率随"缓存回填进度 + 日K源熔断"在 0%~100% 之间跳 → 同参数两次请求分数不同。
      故当本批命中率 < min_coverage(默认 config.YDAY_CHG_MIN_COVERAGE) 时**整批返回空**,
      全部走 default —— 名单内可比性优先于单票精度(半有半无最糟: 有的票加分有的不加)。
      min_coverage=0 关闭该保护(测试/特殊场景)。
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
    thr = config.YDAY_CHG_MIN_COVERAGE if min_coverage is None else min_coverage
    if thr > 0 and len(out) < len(codes) * thr:
        log.info("昨日涨幅覆盖率 %d/%d < %.0f%% → 本批统一按缺失处理(防同批评分漂移)",
                 len(out), len(codes), thr * 100)
        return {}
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


def _yday_background_fetch(need, today, tok=None):
    """后台昨比拉取线程(异步路径)

    tok: P0-c 跨进程单飞令牌(acquire_sem 返回的锁键名)。与进程内批锁**一起**在 finally 释放 ——
    两把锁必须同生共死, 否则会出现"进程内锁已放、跨进程令牌仍被占"⇒ 另一个 worker 一直等到
    令牌 TTL 过期才开始拉, 白白空转 _YDAY_SEM_TTL 秒。
    """
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
        if tok:
            store.release_lock(tok)


# ---------- 昨比主源: 猫爪 daily 批量(2026-09-24 换源 WP4) ----------
# 分片大小: 实测(2026-09-24 测试机) 800 只/次 0.32s **无截断**, 取 500 保守。
# ⚠️ 曾有"上限 20 只"的误判 —— 那是 `_sym_rows()` 按 symbol 建字典把多日/多票折叠
#    造成的假象, 不是接口限制。故 daily 的封装(daily_history_map)刻意**返回 list**。
_YDAY_MEOZ_BATCH = 500
# 多日回溯天数: 至少 2 个**已收盘**交易日(T 与 T-1)。取 3 是因为盘中要把"今日那根
# 未收盘K线"跳过, 跳掉后仍需剩 2 根; 收盘后(≥15:05)不跳, 3 根里前 2 根即答案。
_YDAY_MEOZ_DAYS = 3


def _yday_pair_from_daily(rows, today, after_close=None):
    """猫爪 daily 多日行 → ([T日万元, T-1日万元], T日涨跌幅%) —— 与 _kline_amount_pair 同语义。

    **纯函数**(无网络/无全局状态), 边界由单测钉死 —— 本仓历史教训: 时间/口径类阈值
    只写在注释里必然漂移, 必须能被断言。

    语义要点(与东财日K路径逐条对齐):
      * 今日那根在**盘中**必须跳过(未收盘, 额不完整); **收盘后不跳**(否则"昨日涨幅"
        会整整滞后一天 —— 这是 2026-09-08 修过的语义 bug, 换源不得复发)。
      * 行序: 上游实测**最新在前**, 本函数自行按日期升序排, 不依赖上游顺序。
      * amount 单位实测 = **元** ⇒ /1e4 得万元(与 _kline_amount_pair 的万元口径一致)。
      * 官方 pct_chg 为 None 时用**收盘价环比自算**(兜底口径跨源统一)。
      * 不足 2 行时 pair[1]=None(与东财一致: 只有一根K线时 T-1 就是 None)。
    """
    if not rows:
        return None, None
    if after_close is None:
        after_close = _after_close()
    today_d = str(today or "").replace("-", "")
    keep = []
    for m in rows:
        d = str((m or {}).get("tradedate") or "").replace("-", "")
        if len(d) != 8 or not d.isdigit():
            continue
        if not after_close and d == today_d:
            continue                       # 盘中: 今日未收盘 → 跳过
        amt = _num(m.get("amount"), 0.0)
        if amt <= 0:
            continue
        chg = m.get("pct_chg")
        chg = None if chg in (None, "") else _num(chg, None)
        close = _num(m.get("close"), 0.0) or None
        keep.append((d, amt, chg, close))
    if not keep:
        return None, None
    keep.sort(key=lambda x: x[0])          # 升序: 末位 = 最近已收盘交易日(T)
    t = keep[-1]
    # ★ 2026-09-26 修复(生产 v4.11.53): 与 _kline_amount_pair 同一条纪律 ——
    #   收盘后 T **必须确实是今天**, 否则宁可返回 (None,None) 也不拿"昨天那根"冒充今天。
    #   猫爪 daily 已是昨日成交额的主源, 这条校验是本次"数据冻结"事故在换源后
    #   仍会复发的唯一入口(旧实现里 `not after_close` 的跳过条件不对称地留了这个豁口)。
    #   2026-09-26 补丁(v4.11.55): 同 _kline_amount_pair —— 校验目标改 `_yday_expected_tdate()`,
    #   否则**非交易日盘后**(周六/节假日)今天根本无 K 线, 会恒返回 (None,None)。
    if after_close:
        want = _yday_expected_tdate()
        if want and t[0] != want:
            return None, None
    t1 = keep[-2] if len(keep) >= 2 else None
    pair = [t[1] / 10000.0, (t1[1] / 10000.0) if t1 else None]
    chg_t = t[2]
    if chg_t is None and t1 and t[3] and t1[3] and t1[3] > 0:
        chg_t = round((t[3] - t1[3]) / t1[3] * 100, 2)
    return pair, chg_t


def _yday_fill_from_meoz(codes, today, now):
    """用猫爪 daily **批量**预填昨日成交额/涨跌幅, 返回**仍未填到**的 code 列表。

    为什么要批量而不是逐只: 原路径 `_fetch_yesterday_amount_one` 是**逐只**拉日K
    (yday_prewarm 常驻预热 daemon 每批 200 只、4 线程) —— 换成猫爪后单次请求能带
    500 只, 请求数从 N 降到 ceil(N/500)。这不是优化偏好, 是配额现实:
    猫爪有并发信号量(limit=3)与 429 退避, 逐只打会把额度耗在"同一份数据"上。

    未填到的(新股/停牌/上游缺该票)交给原 东财→腾讯 逐只路径兜底, **其语义不变**。
    """
    codes = [c for c in (codes or []) if c]
    if not codes or not _meoz_enabled():
        return list(codes)
    from . import meoz_client
    rest = []
    got = 0
    for i in range(0, len(codes), _YDAY_MEOZ_BATCH):
        chunk = codes[i:i + _YDAY_MEOZ_BATCH]
        try:
            hist = meoz_client.daily_history_map(chunk, days=_YDAY_MEOZ_DAYS)
        except Exception as e:                                 # noqa: BLE001
            log.warning("[猫爪] 昨日成交额预填失败(%d只) err=%s", len(chunk), str(e)[:120])
            hist = {}
        for c in chunk:
            pair, chg = _yday_pair_from_daily(hist.get(c), today)
            if pair is None:
                rest.append(c)
                continue
            with _yesterday_lock:
                _yesterday_cache[c] = [today, pair, now, chg]
            got += 1
    if got:
        log.info("[猫爪] 昨日成交额预填命中 %d/%d 只(批量, 免逐只请求)", got, len(codes))
    return rest


def _do_fetch_yesterday(need, today):
    """实际批量拉取(批锁内执行): 返回 (成功数, 失败数)
    2026-09-02 超时后 cancel 队列中未运行任务: 原实现超时后任务滞留线程池队列
    (5000 只 4 线程 12s 只完成部分, 剩余全排队) → 后续抢筹/其他拉取排队等线程 → 全站卡顿

    2026-09-24 换源 WP4: 先走猫爪 daily **批量**预填(主源), 剩下的才进东财→腾讯逐只池。
    成功计数把预填命中的也算上 —— 否则日志与"进池数量"会对不上。
    """
    ok_cnt = 0
    need = list(need or [])
    if need:
        rest = _yday_fill_from_meoz(need, today, time.time())
        ok_cnt += len(need) - len(rest)
        need = rest
    fail_cnt = len(need)
    if not need:
        return ok_cnt, fail_cnt
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


def _fetch_zt_pool_meoz(date):
    """猫爪涨停池 → 与东财同构的 {code: {fund, fb, lb, zbc, zdp}}(2026-09-24 换源 WP3)。

    字段映射(实测 2026-09-24 limit_pool 16 列):
      fd_amount(元)      → fund(亿)       东财 fund 也是"亿", 故 /1e8
      first_time("09:25:00") → fb(HHMMSS) 东财 fbt 是整数 HHMMSS
      limit_times        → lb(连板数)
      open_times         → zbc(炸板次数)
      pct_chg            → zdp(涨停涨幅%)
    只取 `type=='u'`: 猫爪池含跌停('d'), 不排掉会把跌停票当涨停票。
    """
    if not _meoz_enabled():
        return {}
    try:
        from . import meoz_client
        rows = meoz_client.limit_pool_map(date=date)
    except Exception as e:                                     # noqa: BLE001
        log.warning("[猫爪] 涨停池拉取异常 date=%s err=%s", date, str(e)[:100])
        return {}
    out = {}
    for code, m in (rows or {}).items():
        c = str(code or "")
        if not c or str(m.get("type") or "") != "u":
            continue
        out[c] = {
            "fund": _num(m.get("fd_amount")) / 1e8,
            "fb": _hhmmss_int(m.get("first_time")),
            "lb": int(_num(m.get("limit_times"))),
            "zbc": int(_num(m.get("open_times"))),
            "zdp": _num(m.get("pct_chg")),
        }
    return out


def _fetch_zt_pool_em(date):
    """东财涨停池(push2ex getTopicZTPool) → 同构 dict; 失败返回 {}。

    2026-09-24 换源 WP3 起**降为备源**(主源猫爪 limit_pool)。
    注意本域名与 clist 的 push2 不同, 历史记录显示它"生产实测畅通";
    保留它作备源是因为它不共享 push2 的封禁面。
    """
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
        return out
    except Exception as e:
        _record("eastmoney_zt_pool", False)
        log.warning("涨停池拉取失败(备源东财) date=%s err=%s", date, e)
        return {}


def fetch_zt_pool(date=None):
    """拉取涨停池(含封单额/封板时间/炸板次数/连板数), 带缓存。
    主源: 猫爪 limit_pool(2026-09-24 换源 WP3); 备源: 东财 push2ex。
    date: YYYYMMDD, 默认今天(北京); 返回 {code: {fund, fb, lb, zbc, zdp}} 或 {}
    失败返回空 dict(不影响选股主流程, 盘中封单因子降级为无数据)。
    """
    date = date or _bj_date_str().replace("-", "")
    with _zt_lock:
        ent = _zt_cache.get(date)
        if ent and time.time() - ent["ts"] < config.ZT_CACHE_TTL:
            return ent["raw"]
    out = _fetch_zt_pool_meoz(date)
    src = "meoz"
    if not out:
        out = _fetch_zt_pool_em(date)
        src = "eastmoney"
    if not out:
        return {}                       # 全源失败: 不写缓存(与原实现一致, 下次立即重试)
    with _zt_lock:
        _zt_cache[date] = {"raw": out, "ts": time.time()}
    log.info("涨停池拉取成功 date=%s 源=%s 涨停数%d", date, src, len(out))
    return out


# ==================== 个股图表数据(分时/K线) ====================
# 东财标准 kline 接口: klt=101日K / 102周K / 103月K
# 分时 trends2 接口: 当日分时轨迹(价格+均价+成交量)
_CHART_CACHE = {}
_CHART_LOCK = threading.Lock()
_CHART_CACHE_TTL = 60     # 分时 60s 缓存, K线 1800s 缓存

# 🔴 日K 根数 = 200 —— 与**旧链实际供数源**对齐, 不是拍脑袋 (v4.11.48)
#   旧链 `fetch_stock_chart`(东财) 名义 120 根(`lmt`), 但东财 chart 存在接口级
#   时段性风控(常 502) ⇒ 线上长期实际落的是备源腾讯 `count=200`。
#   换首源(猫爪)后若写死 120, 用户看到的日K 会从 ~200 根缩到 ~120 根(约 10 个月
#   → 约 6 个月) —— 这是**静默退化**, 换源验收(等价替换)直接判负。
#   取 max(东财 120, 腾讯 200) = 200 ⇒ 对任何一种旧表现都不缩水。
#   猫爪 daily 的 recentdays 实测 120/200/250/300 均足量返回(数据没问题)。
_MEOZ_DAY_K_BARS = 200


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
    # 🔴 2026-09-28 主人拍板「日K 不用东财」：**本函数的 K 线腿已从链路摘除** ——
    #   fetch_stock_chart_robust 的 sources 现为 ["meoz", "tencent"]，K 线路径已无生产调用方。
    #   保留而非删除的理由：① `period=="minute"` 那一腿（_fetch_minute_trend，走东财 trends2）
    #   仍被 robust 调用，两者同处一块；② 测试仍引用本函数名做"东财不再被调用"的守卫。
    #   若将来要整体删除，请连同 _fetch_minute_trend 与其测试一并处理。
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
    # 2026-09-10 二审配套修正: 本函数只是"东财一源", 外层 fetch_stock_chart_robust 会切
    # 腾讯同语义备源。原措辞 "K线拉取失败" + WARNING 在恢复备源后**会误导运维**
    # (东财 push2his 风控期是常态 → 每只票一条 WARNING 刷屏, 且让人误判 K 线整体不可用;
    #  实测生产 23:31 一分钟就 12 条)。降为 debug, 外层已有权威表述:
    # 成功 log.info("chart[robust]源=xxx") / 全源失败 log.error("chart[robust]全部数据源失败")。
    log.debug("东财K线源失败(全HOST熔断) code=%s period=%s, 交由同语义备源接管", code, period)
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


# ==================== chart 源链与校验 (2026-08-20 起, 2026-09-11 收敛) ====================
# 现源链: 东财 push2his(主, 多节点轮换) → 腾讯(同语义备源); 周K/月K 主源全失败时走日线聚合。
# 历史: 本处原有 同花顺 / 开盘啦(kpl) / Tushare 三个 fallback 实现, v4.11 去兜底重构
#   把 sources 收窄为两源后, 它们的 elif 分支运行时不可达 → 属"仅源码可达"死代码,
#   已于 v4.11.8 删除(判据: 引用它的分支运行时到不了 = 死代码)。
# 下方 _validate_chart_data 是源链共用的数据合理性校验, 仍在生产使用, 故保留于此。


def _validate_chart_data(data, period, source=None):
    """验证图表数据合理性, 过滤异常数据(如复权错乱导致的量级突变)"""
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
    # 复权错乱检测: 替代 max>min*100 这一类粗暴判据(会误伤长期高价股的完整K线,
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


def _meoz_pre_close(code):
    """猫爪 screening 取该票**昨收**与名称 —— 分时图的 0% 基准线要用 preClose。

    返回 (preClose, name); 取不到返回 (0.0, "") —— 与东财路径一致
    (拿不到就是 0, 由前端决定怎么显示, 不编造)。
    """
    try:
        from . import meoz_client
        sm = meoz_client.screening_map(symbols=[code]) or {}
        for _c, m in sm.items():
            return _num(m.get("pre_close"), 0.0), str(m.get("name") or "")
    except Exception:                                          # noqa: BLE001
        pass
    return 0.0, ""


def _fetch_chart_from_meoz(code, period):
    """猫爪图表源(2026-09-24 换源 WP5: **追加为首源**, 多源链保留)。

    返回结构与 `fetch_stock_chart` 一致(直接喂 `_validate_chart_data` 与前端):
      minute → {period, code, name, time[], price[], avg[], volume[], preClose}
      day    → {period, code, name, time[], open[], close[], high[], low[],
                volume[], amount[], preClose}
    周K/月K 猫爪**没有** → 返回 {} 交给原链条(它本来就只有东财/腾讯/自聚合三条路)。

    🔴 复权口径已对拍(2026-09-24 测试机真跑): 猫爪 daily 与现生产链(腾讯 qfq)在
      重叠区间上**逐日收盘 100% 相等**, 且跨除权事件 ⇒ 同为**前复权**。因此把它提为
      日K首源**不会**让除权票出现K线断层(这是本包唯一需要前置验证的正确性风险;
      不验就提首源等于赌)。
    🔴 深度口径: 拉取根数取 `_MEOZ_DAY_K_BARS`(=200), 与旧链实际供数源(腾讯 200)
      对齐 —— 写死 120 会让日K 从 ~200 根缩到 ~120 根(见该常量处的说明)。
    """
    if not _meoz_enabled():
        return {}
    from . import meoz_client
    code = str(code or "")
    if period == "minute":
        rows = meoz_client.minute_rows(code)
        if not rows:
            return {}
        times, prices, avgs, vols = [], [], [], []
        for m in rows:
            tm = _hhmm_colon(m.get("trademin"))
            p = _num(m.get("close"), 0.0)
            if not tm or p <= 0:
                continue
            v = _num(m.get("vol"), 0.0)
            a = _num(m.get("amount"), 0.0)
            times.append(tm)
            prices.append(p)
            vols.append(v)
            # 分钟均价 = 该分钟成交额 / 成交量(股); vol 单位实测=手 ⇒ ×100
            avgs.append(round(a / (v * 100.0), 3) if (v > 0 and a > 0) else None)
        if not times:
            return {}
        pre_close, name = _meoz_pre_close(code)
        return _trim_minute_to_now({
            "period": "minute", "code": code, "name": name,
            "time": times, "price": prices, "avg": avgs, "volume": vols,
            "preClose": pre_close,
        })
    if period == "day":
        hist = meoz_client.daily_history_map([code], days=_MEOZ_DAY_K_BARS)
        rows = hist.get(code) or []
        if not rows:
            return {}
        rows.sort(key=lambda m: str(m.get("tradedate") or ""))   # 上游最新在前 → 升序
        times, opens, closes, highs, lows, vols, amts = [], [], [], [], [], [], []
        last_pct = None
        for m in rows:
            d = _norm_kline_date(m.get("tradedate"))
            c = _num(m.get("close"), 0.0)
            if not d or c <= 0:
                continue
            times.append(d)
            opens.append(_num(m.get("open"), 0.0))
            closes.append(c)
            highs.append(_num(m.get("high"), 0.0))
            lows.append(_num(m.get("low"), 0.0))
            vols.append(_num(m.get("vol"), 0.0))
            amts.append(_num(m.get("amount"), 0.0))
            last_pct = None if m.get("pct_chg") in (None, "") else _num(m.get("pct_chg"), None)
        if not times:
            return {}
        pre_close = 0.0
        if last_pct is not None and (1 + last_pct / 100.0) > 0:
            pre_close = round(closes[-1] / (1 + last_pct / 100.0), 3)
        return {
            "period": "day", "code": code,
            "name": str((rows[-1] or {}).get("name") or ""),
            "time": times, "open": opens, "close": closes,
            "high": highs, "low": lows, "volume": vols, "amount": amts,
            "preClose": pre_close,
        }
    return {}


def fetch_stock_chart_robust(code, period="day"):
    """多源 chart 拉取 (替代原 fetch_stock_chart):
    顺序: 猫爪(2026-09-24 换源 WP5 首源) → 东财(push2his) → 腾讯(同语义备源)
          → 自聚合(仅周/月K)
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
    # 2026-09-10 二审修订(去兜底 ≠ 删同语义真实源):
    #   · 东财 push2his 是**接口级时段性风控**(与出口 IP 无关), 实测 chart 命中
    #     东财 27 / 腾讯 485 / ths 2 —— 单东财等于 K 线功能整体瘫痪
    #     (测试机实测 5/5 返回 502), 这是不能接受的功能性回归。
    #   · 腾讯 K 线是**同语义真实 OHLC**(与东财同为前复权日/周/月线, 经
    #     _validate_chart_data + _kline_amount_pair 口径统一), 与「用现价涨幅假造
    #     竞价字段 f615/f616」性质完全不同 —— 属"换源不换数据", 保留。
    #   · 其余非真实同语义源(tushare / ths / kpl 拼接)已下线; 自聚合仅保留为周K/月K 的
    #     最终兜底(见函数末 _aggregate_kpl_daily_to_period)。
    # 2026-09-11: ths / kpl / tushare 三个分支的**函数实现已删除**——此前 sources 收窄为
    #   两源后, 这三个 elif 分支运行时永不执行(仅源码可达), 属死代码。
    # 顺序: 猫爪(主, 2026-09-24 换源 WP5) → 腾讯(首选备源) → **东财(末位兜底)**。
    # 🔴 2026-09-28/29 两轮拍板后的口径：**东财退出主链、仅作末位兜底** ——
    #   · 主用腾讯：day/week/month 全支持（count 200/700/300），实测无风控；
    #   · 仍保留东财末位：它带**官方 f58 涨跌幅**、覆盖更全（腾讯稳定缺 ~8 只），主人明确
    #     "如果影响逻辑计算，还可以考虑使用东财"；东财自身有熔断 + 域名冷却，只在腾讯失败时
    #     被调用一次，不会退回"每次都打"。
    #   · 新浪日K/周K 实测可取但**无成交额**，故不接入（需要时再作第 4 源）。
    #   · **分时图**（_fetch_minute_trend，走东财 trends2）不属"日K"，本次不动。
    sources = ["meoz", "tencent", "eastmoney"]
    for src in sources:
        try:
            if src == "meoz":
                d = _fetch_chart_from_meoz(code, period)
                if d and _validate_chart_data(d, period, source="meoz"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=meoz code=%s period=%s 耗时%.0fms",
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
            elif src == "eastmoney":
                # 末位兜底（2026-09-29 复位）：只在腾讯失败后才走到；带官方涨跌幅、覆盖更全
                d = fetch_stock_chart(code, period)
                if d and _validate_chart_data(d, period, source="eastmoney"):
                    with _CHART_LOCK:
                        _CHART_CACHE[cache_key] = {"data": d, "ts": time.time()}
                    log.info("chart[robust]源=eastmoney(末位兜底) code=%s period=%s 耗时%.0fms",
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
