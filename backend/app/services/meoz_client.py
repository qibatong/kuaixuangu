# -*- coding: utf-8 -*-
"""
猫爪(meoz.cn)数据源客户端
========================================================================
替代开盘啦(kpl)成为竞价的唯一数据来源。严格对齐官方 SDK(meoz.py)的实测语义:

  铁律1 —— 请求体顶层键 = {"apikey","apiname","fields","params"};
           **apikey 是请求体顶层字段**(payload["apikey"]=key), 不是 Authorization 头
           (实测放头里 → 422 "Field required: body.apikey")。
           业务参数必须嵌在 params 子对象里, 平铺会被网关拒绝(code=422)。
  铁律2 —— 线路切换**只在网络层故障**(DNS失败/连不上/超时/连接中断)时才切另一条;
           429 / 5xx → 退避重试**同一条线路**(官方 SDK 行为, attempt<2);
           401/403 (认证失败) → 直接抛出, 不重试不切线路;
           业务错误(code!=200) / 空结果 **一律不切线路**。

其他关键约定(实测踩坑得出, 不可回退):
  * trademin 是 **HHMM 字符串**("0925"), 不是整数 925 —— 传整数报
    "params.trademin 必须为 HHMM, 例如 0917"。
  * 只支持 **JSON POST**: GET / form 编码一律返回 500。
  * `fields` 传字符串可只取需要的字段(减小响应体)。
  * 业务成功判定: `code == 200`(官方 SDK: code 为 int 且 != 200 即错误)。

设计原则(与 kpl.py 一致): 低频缓存 + 失败降级(返回 None, 绝不阻塞主流程)。
缓存与并发信号量复用 CacheStore, 保证多 worker 共享同一份配额视图。
"""
import json
import threading
import time
import urllib.error
import urllib.request

from ..core import config, logger
from ..core import net as _net
from .cache_store import store

log = logger.get_logger(__name__)

# ---------------- 专线地址(官方 SDK DEDICATED_API_URLS) ----------------
# 优先走专线: sz/sh 双线互为备份, 公网 https://numcat.net/api 证书验证失败不可用。
DEFAULT_LINES = (
    "http://sz.numcat.net:8866/api",
    "http://sh.numcat.net:8866/api",
)

# 请求超时(秒): 竞价时段对时效敏感, 不宜过长
_TIMEOUT = 12
# 单次调用最大尝试次数(仅网络层故障才换线, 故上限=线路数)
_MAX_ATTEMPTS = 2

_LOCKS = threading.Lock()


# ---------- 健康监控(与 kpl._HEALTH 同构, 便于统一盘面) ----------
_HEALTH = {
    "meoz": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
}
_health_lock = threading.Lock()


def _record(ok, ms=0):
    with _health_lock:
        h = _HEALTH["meoz"]
        now = time.time()
        if ok:
            h["ok"] += 1
            h["last_ok"] = now
            if ms > 0:
                h["ms_sum"] += ms
                h["ms_cnt"] += 1
            if h["down_since"]:
                log.info("猫爪数据源恢复(故障%.0f秒)", now - h["down_since"])
                h["down_since"] = 0
        else:
            h["fail"] += 1
            h["last_fail"] = now
            if not h["down_since"]:
                h["down_since"] = now
                log.warning("猫爪数据源故障(开始降级)")


def health() -> dict:
    """健康快照(供监控/诊断接口读取), 附成功率与平均耗时。"""
    with _health_lock:
        h = dict(_HEALTH["meoz"])
    ms_cnt = h.get("ms_cnt") or 0
    h["avg_ms"] = int(h["ms_sum"] / ms_cnt) if ms_cnt else 0
    total = (h.get("ok") or 0) + (h.get("fail") or 0)
    h["success_rate"] = round(h["ok"] / total, 4) if total else None
    return h


# ---------------- 配置读取(settings 优先, 回落 config) ----------------
def _apikey() -> str:
    try:
        from . import settings
        v = settings.get("meoz_apikey")
        if v:
            return str(v)
    except Exception:                                          # noqa: BLE001
        pass
    return str(getattr(config, "MEOZ_APIKEY", "") or "")


def _lines():
    """线路列表: settings.meoz_lines 可覆盖(测试机/切公网场景)。"""
    try:
        from . import settings
        v = settings.get("meoz_lines")
        if isinstance(v, list) and v:
            return tuple(str(x) for x in v)
    except Exception:                                          # noqa: BLE001
        pass
    return tuple(getattr(config, "MEOZ_LINES", DEFAULT_LINES) or DEFAULT_LINES)


def enabled() -> bool:
    """是否启用猫爪源。settings `use_meoz` 未显式置 0 且有 apikey 即视为可用。

    与 kpl 不同: 猫爪是**新主源**, 默认开(只要配了 key)。显式 `use_meoz=0` 可关。
    """
    if str(_get_setting("use_meoz") or "1") in ("0", "false", "False"):
        return False
    return bool(_apikey())


def _get_setting(key):
    try:
        from . import settings
        return settings.get(key)
    except Exception:                                          # noqa: BLE001
        return None


# ---------------- 网络层故障判定(决定是否切线路) ----------------
_NETWORK_ERRORS = (
    urllib.error.URLError,      # DNS失败 / 连接被拒 / 超时(其 reason 亦为此类)
    TimeoutError,
    ConnectionError,
    OSError,                    # 连接中断/复位
)


class _HttpCodeError(Exception):
    """对端返回了 HTTP 状态码(非网络层故障) —— 记录状态码与 Retry-After。"""

    def __init__(self, code, retry_after=None):
        super().__init__("HTTP %s" % code)
        self.code = code
        self.retry_after = retry_after


class _AuthError(Exception):
    """认证失败(401/403): 不重试、不切线路, 直接暴露问题。"""


_RETRY_AFTER_CAP = 30.0     # 官方 SKILL.md:116 —— "429 遵循 Retry-After, 单次等待最多 30 秒"
_RETRY_AFTER_TOTAL = 30.0   # 单次 call() 的**累计**等待预算(同上口径; 防 3 次退避叠加成 60s)


def _retry_after_seconds(retry_after, attempt: int) -> float:
    """429 退避时长: 优先取上游 `Retry-After`, 否则指数退避(1s, 2s, ...), 上限 30s。

    🔴 2026-09-28 修复(原先**违反官方规范**的 bug): 本函数原先只接受 headers **映射**,
      而 `_post_one` 抛错时塞进去的是 `dict(e.headers or {})` **整个字典** ⇒ 429 分支里
      `float(<dict>)` 抛 TypeError, 又被 `except (TypeError, ValueError)` 静默吞掉
      ⇒ **上游的 Retry-After 从未生效**, 我们固定按 1s/2s 连打同一条线路继续敲。
      这不只是"没兜底", 而是**主动放大限流** —— 生产实测: 竞价时段单分钟 429 达 163~286 条
      (见 deploy/meoz-429-fallback-analysis-20260928.md)。

    现在: ① `_post_one` 只保留 `Retry-After` 头的值; ② 本函数兼容"字符串/数字"与
    "headers 映射"两种入参(向后兼容); ③ 上限由 8s 提到官方口径的 30s。
    """
    ra = None
    if isinstance(retry_after, dict):                 # 兼容旧调用方: 直接传 headers 映射
        ra = retry_after.get("Retry-After")
    elif retry_after is not None:
        ra = retry_after
    if ra is not None and str(ra).strip() != "":
        try:
            return min(_RETRY_AFTER_CAP, max(0.5, float(str(ra).strip())))
        except (TypeError, ValueError):
            pass
    return min(_RETRY_AFTER_CAP, 1.0 * (2 ** attempt))


def _post_one(url: str, payload: dict, timeout: int):
    """对单条线路发一次 JSON POST。

    返回 (code, data) 成功; 抛 _HttpCodeError / _AuthError / _NETWORK_ERRORS。

    注: apikey 已在 payload 顶层(铁律1), 不加 Authorization 头(加了反而 422)。
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8",
    })
    try:
        with _net.http_get(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
            return json.loads(raw)
    except urllib.error.HTTPError as e:                        # 带状态码: 非网络层
        if e.code in (401, 403):
            raise _AuthError("HTTP %s" % e.code)
        # 只留 Retry-After 的值(整个 headers 字典会让下游 float() 抛错 ⇒ 退避失效)
        raise _HttpCodeError(e.code, (e.headers or {}).get("Retry-After"))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        raise                                                  # 网络层: 由上层决定切线路


def call(apiname: str, params=None, fields=None, timeout: int = _TIMEOUT):
    """调用猫爪接口, 返回解析后的 dict; 失败返回 None(永不抛异常)。

    apiname: 接口名(如 "tick_history" / "daily_auc_fd")
    params:  业务参数 dict(会自动嵌进 params 子对象 —— 铁律1)
    fields:  可选字段裁剪(字符串或"a,b,c"), 减小响应体

    线路策略(铁律2): 依 lines 顺序尝试, **仅网络层故障**才切下一条;
    429 / 5xx 退避后重试**同一条线路**(最多 2 次); 401/403 直接失败;
    业务错误(code!=200) 直接返回 None。
    """
    if not enabled():
        log.debug("猫爪未启用(无 apikey 或 use_meoz=0), 跳过 %s", apiname)
        return None

    key = _apikey()
    payload = {"apikey": key, "apiname": apiname}              # 铁律1: apikey 顶层
    if fields:
        payload["fields"] = fields if isinstance(fields, str) else ",".join(fields)
    payload["params"] = dict(params or {})                     # 铁律1: 业务参数嵌 params

    lines = _lines()
    t0 = time.time()

    sem_key = store.acquire_sem("meoz", limit=3, timeout=timeout)
    if sem_key is None:
        _record(False)
        log.warning("猫爪并发信号量获取超时(限流) a=%s", apiname)
        return None

    try:
        idx = 0
        waited = 0.0                    # 本次 call() 的累计 429 等待(见 _RETRY_AFTER_TOTAL)
        while idx < min(len(lines), _MAX_ATTEMPTS):
            url = lines[idx]
            attempt = 0
            while attempt < 3:                                  # 同线路最多 3 次(429/5xx 退避)
                try:
                    data = _post_one(url, payload, timeout)
                    break
                except _AuthError as e:
                    _record(False)
                    log.error("猫爪认证失败 a=%s err=%s(检查 apikey)", apiname, e)
                    return None
                except _HttpCodeError as e:
                    if e.code == 429 and attempt < 2:
                        wait = _retry_after_seconds(e.retry_after, attempt)
                        if waited + wait > _RETRY_AFTER_TOTAL:   # 预算用尽 ⇒ 不再敲上游
                            wait = max(0.0, _RETRY_AFTER_TOTAL - waited)
                        if wait <= 0:
                            _record(False)
                            log.warning("猫爪 429 限流 a=%s 已用完 %.0fs 等待预算(官方口径), 放弃本次",
                                        apiname, _RETRY_AFTER_TOTAL)
                            return None
                        waited += wait
                        log.warning("猫爪 429 限流 a=%s 退避 %.1fs(遵循 Retry-After) 后重试同线路",
                                    apiname, wait)
                        time.sleep(wait)
                        attempt += 1
                        continue
                    if 500 <= e.code < 600 and attempt < 2:
                        wait = min(2 ** attempt, 4)
                        log.warning("猫爪 %s 服务端错误 a=%s 退避 %ds 后重试同线路", e.code, apiname, wait)
                        time.sleep(wait)
                        attempt += 1
                        continue
                    _record(False)
                    log.warning("猫爪返回状态码 a=%s code=%s(不切线路, 放弃本次)", apiname, e.code)
                    return None
                except _NETWORK_ERRORS as e:
                    log.warning("猫爪网络故障 a=%s line=%s err=%s → 切线路", apiname, url, e)
                    break                                           # 跳出内层, 切下一条线路
            else:
                idx += 1
                continue
            if attempt >= 3:                                        # 内层耗尽仍未拿到
                idx += 1
                continue

            # ---- 成功拿到 HTTP 200 响应 ----
            ms = int((time.time() - t0) * 1000)
            if not isinstance(data, dict):
                _record(False)
                log.warning("猫爪响应非 JSON 对象 a=%s type=%s", apiname, type(data).__name__)
                return None
            code = data.get("code")
            # 官方 SDK: code 为 int 且 != 200 即业务错误; 兼容 code 缺失(视为成功)
            if isinstance(code, bool):
                code = None
            if isinstance(code, int) and code != 200:
                _record(False)
                log.warning("猫爪业务错误 a=%s code=%s msg=%s", apiname, code,
                            data.get("message") or data.get("msg"))
                return None            # 业务错误: 返回 None, 不切线路
            _record(True, ms)
            return data

        _record(False)
        log.warning("猫爪全部线路失败 a=%s(已尝试 %d 条)", apiname, idx)
        return None
    except Exception as e:                                     # noqa: BLE001 - 兜底, 绝不外抛
        _record(False)
        log.warning("猫爪调用异常 a=%s err=%s", apiname, e)
        return None
    finally:
        store.release_lock(sem_key)


# ---------------- 便捷封装(带缓存) ----------------
_HIST_TTL = 3600        # 历史日上游数据的长 TTL(秒); 判定规则见 _hist_ttl_for


def _hist_ttl_for(params, ttl) -> float:
    """历史**绝对日期**的请求 → 长 TTL; 其余 → 原 ttl。

    ★ 为什么需要(2026-09-28 生产实证):
      回看日的数据**不可变**(那天的竞价早已结束), 但 call_cached 一律套用快照
      TTL(_AUC_SNAP_TTL=30) ⇒ 结果层(600s)一过期, 上游层几乎必然也已过期 ⇒
      每次重算都要重打那几个**全市场 5000+ 行**的猫爪接口
      (screening 5557 / free_mv_map 5904 / auc_snapshot 5567 / auc_open_bid 5569)。
      实测:「上游热 / 结果冷」重建仅 **345ms**, 而默认(上游也冷)**5.5~6.9s**, 差 16~20 倍。
      主人报的「竞价抢筹 Tab 首次加载要等 5 秒」正源于此。

    ★ 安全性只依赖三件事(逐条都是硬约束, 改动前请复核):
      1. 键里是**绝对** `tradedate=20260924` → 按日隔离、该日值不可变 ⇒ 长 TTL 安全;
      2. 实时路径传的是 `tradedate_offset=0`(**相对键**, 跨自然日复用同一键)
         ⇒ 绝不能长 TTL。本函数只认 `tradedate`, 对 offset 天然不匹配;
      3. 与**今天**同日时用原 TTL —— 今天的竞价数据仍在变。
         ⚠️ 比较前必须先归一化: `2026-09-28`(带横线) 与 `20260928` 是同一个日子,
            漏掉归一化会把"带横线的今天"误判成历史日 ⇒ 盘中数据被缓存 1 小时。
            (该边界有专门用例, 见 backend/tests/test_perf_v41174.py ——
             含"撤销归一化 / 短路条件置 False / 丢掉今天守卫 / 边界放宽"四组变异验证)
    """
    td = (params or {}).get("tradedate")
    if not td:
        return ttl
    digits = "".join(ch for ch in str(td) if ch.isdigit())
    if len(digits) != 8:                      # 非法/异常格式: 不动, 用原 TTL(安全侧)
        return ttl
    today = time.strftime("%Y%m%d", time.gmtime(time.time() + 8 * 3600))
    return max(float(_HIST_TTL), float(ttl)) if digits < today else ttl


def _bj_today8() -> str:
    """北京时间今天 YYYYMMDD（与 _hist_ttl_for 内同一口径）。"""
    return time.strftime("%Y%m%d", time.gmtime(time.time() + 8 * 3600))


def _norm_symbols_param(symbols) -> str:
    """把 symbols 归一成**排序后**的逗号串 —— "同一批票命中同一个缓存键"的前提。

    ★ 为什么必须排序（2026-09-28 实测根因）:
      缓存键 = apiname + json(params)（见 call_cached）。symbols 顺序不同则键逐字不同
      ⇒ 同一批 5000+ 行的数据被**重复拉取**，把上游打成 429：
      生产实测竞价时段 09:24→163 条、09:25→286 条 429，且两台机的 429 **全部**来自
      这类重复的 `a=daily`（昨比 500 只/片 × 多调用方 × 顺序各异）。
      排序只让"同一批票"落到同一个键；**子集**调用（如"名单前 100 只"）params 本就不同，
      不受影响。

    ★ 安全性: 上游按 symbols 批量返回，**行序无契约价值** —— 本模块出口一律用
      `_sym_rows()` 转成 {symbol: row} 字典，顺序天然被抹掉。
    """
    if isinstance(symbols, (list, tuple, set)):
        seq = [str(x).strip() for x in symbols]
    else:
        seq = [x.strip() for x in str(symbols or "").split(",")]
    return ",".join(sorted(x for x in seq if x))


def call_cached(apiname: str, params=None, fields=None, ttl=6, cache_key=None,
                fresh: bool = False):
    """带缓存的调用。ttl 秒内同 key 直接读缓存, 避免竞价时段高频重复拉同一份数据。

    cache_key: 自定义缓存键; 缺省由 apiname+params 生成。
    fresh: True 时**既不读也不写**缓存, 强制打上游。补采场景用它替代
        "靠间隔 > TTL 绕开缓存"的脆弱做法 —— 轮询间隔与缓存 TTL 从此互不约束
        (2026-09-24 WP2b; 该不变量原先只写在 auction_snapshot.py 的注释里)。
    """
    if cache_key is None:
        try:
            # 🔴 2026-09-28: **fields 必须进键**。原先只拼 apiname+params ⇒ 同一 apiname+params
            #   但 fields 不同的调用会"串味"：先写缓存的那次字段集被后调用者读到（缺列），
            #   例如题材榜只要 3 列、screening 要 54 列 ⇒ 属**正确性**问题，不只是浪费。
            fld = fields if isinstance(fields, str) else ",".join(fields or [])
            cache_key = "%s|%s:%s" % (apiname, fld,
                                      json.dumps(params or {}, sort_keys=True, ensure_ascii=False))
        except Exception:                                      # noqa: BLE001
            cache_key = apiname
    # 2026-09-28: 历史绝对日期改用长 TTL —— 结果层过期时上游仍热, 重建从 5.5s 降到 ~0.35s
    ttl = _hist_ttl_for(params, ttl)
    full = "meoz:" + cache_key
    if not fresh:
        hit = store.get(full)
        if hit is not None:
            return hit
    data = call(apiname, params=params, fields=fields)
    if data is not None and ttl > 0 and not fresh:
        store.set(full, data, ttl)
    return data


def clear_cache():
    """清空全部猫爪缓存(快照采集前强制拿当前时点新鲜数据)。"""
    try:
        store.clear_prefix("meoz:")
    except Exception:                                          # noqa: BLE001
        pass


# ---------------- 数据取用助手 ----------------
def rows_of(data):
    """从响应里取出数据行列表(兼容 data / rows / list 三种封装)。"""
    if not isinstance(data, dict):
        return []
    for k in ("data", "rows", "list", "result"):
        v = data.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for kk in ("rows", "list", "data"):
                vv = v.get(kk)
                if isinstance(vv, list):
                    return vv
    return []


def hhmm(value) -> str:
    """把时间参数规整成 HHMM 字符串(铁律: trademin 必须是 "0925" 而非 925)。"""
    s = str(value or "").strip()
    if not s:
        return ""
    if ":" in s:                    # "09:25" → "0925"
        s = s.replace(":", "")
    s = "".join(ch for ch in s if ch.isdigit())
    if len(s) == 3:                 # "925" → "0925"
        s = "0" + s
    return s.zfill(4)[:4]


# =====================================================================
# 集合竞价三张抢筹表 —— 猫爪专用取数(2026-09-19 主人: 能猫爪拿的都走猫爪, 不自造)
# =====================================================================
#
# 实测(专业版 apikey, 2026-09-17 全市场)确认的字段与语义:
#
#   list20 竞额抢筹  ← auc_kp:      auc_net_amount(竞价主力净额, 元) / free_float_mv(自由流通市值, 元)
#                    ★ 与开盘啦 bidNetAmt/floatMv 口径逐字一致, 只需一步除法
#   list20Chg 涨幅抢筹 ← daily_auc_detail 快照模式两个时点的 auc_pct_chg 相减
#                    ★ 猫爪无"涨幅差"成品字段, 但给两个时点的成品涨幅值
#   listLast 末秒抢筹 ← daily_auc:   open_bid_pct(开盘抢筹幅度, %)
#                    ★ 官方原生字段 = 9:25开盘价相对 9:24 最后一笔的涨跌幅, 零计算
#
# 「快照模式」要点(daily_auc_detail):
#   * 传 trademin(HHMM) + side(before/after) 且**不传 symbols** → 返回全市场 5565 只
#   * before 取严格早于边界的最后一条(≈9:24:5x); after 取等于/晚于边界的第一条(=9:25:00)
#   * 9:15 整点无数据(code=1002), 9:16 起可用

_AUC_SNAP_TTL = 30      # 时点快照缓存(秒): 竞价时段避免高频重复拉同一分钟

# 各接口缓存 TTL 登记表(2026-09-24 实测 grep 全量 ttl= 用法得出)。
#   _AUC_SNAP_TTL(=30): auc_kp / fundflow_kp / daily_auc / daily_auc_detail
#                       / daily_auc_fd / valuation / screening
#                       + limit_pool / limit_pool_yes / daily / minute
#                         (2026-09-24 换源 WP3/WP4/WP5 新增; 同样是「实时快照」性质 ——
#                          外层调用方各自还有更长的缓存(图表 60s/120s、昨额按批),
#                          这里统一取一档, **不再新造第二个 30**)
#   字面量 30:          index_snapshot(:711) / emoindic(:812) ← 原先两个独立的 30, 此处归口
# ★ 跨模块需要「错开缓存」时(典型: 补采轮询间隔)**请调 cache_ttl()**, 不要在调用方
#   硬编码秒数 —— TTL 与轮询间隔的先后关系是本仓历史的静默失效点,
#   守卫断言见 tests/test_netfill_interval.py。
_TTL_BY_API = {
    "auc_kp": _AUC_SNAP_TTL,
    "fundflow_kp": _AUC_SNAP_TTL,
    "daily_auc": _AUC_SNAP_TTL,
    "daily_auc_detail": _AUC_SNAP_TTL,
    "daily_auc_fd": _AUC_SNAP_TTL,
    "valuation": _AUC_SNAP_TTL,
    "screening": _AUC_SNAP_TTL,
    "limit_pool": _AUC_SNAP_TTL,        # 换源 WP3: 涨停池
    "limit_pool_yes": _AUC_SNAP_TTL,    # 换源 WP3: 涨停池(含昨日维度)
    "daily": _AUC_SNAP_TTL,             # 换源 WP4/WP5: 日K(昨额/涨跌幅、日K图)
    "minute": _AUC_SNAP_TTL,            # 换源 WP5: 分时
    "index_snapshot": 30,
    "emoindic": 30,
}
_DEFAULT_TTL = 6        # call_cached 的默认 ttl


def cache_ttl(apiname: str) -> float:
    """返回某接口的缓存 TTL(秒); 未登记接口回 _DEFAULT_TTL。

    ★ 跨模块需要「错开缓存」时**请调用本函数**, 不要在调用方硬编码秒数 ——
      TTL 与轮询间隔的先后关系是本仓历史的静默失效点,
      守卫断言见 tests/test_netfill_interval.py。

    Args:
        apiname: 猫爪接口名。

    Returns:
        该接口 TTL(秒); 未登记接口回 _DEFAULT_TTL。
    """
    return float(_TTL_BY_API.get(apiname, _DEFAULT_TTL))


def _sym_rows(data, key="symbol"):
    """把 items 矩阵响应转成 {symbol: {字段: 值}} 映射(cols 顺序对齐)。

    screening/daily_auc/auc_kp 等均返回 {"data": {"fields": [...], "items": [[...]]}}。
    """
    if not isinstance(data, dict):
        return {}
    dd = data.get("data")
    if not isinstance(dd, dict):
        return {}
    cols = dd.get("fields") or []
    items = dd.get("items") or []
    if not cols or not isinstance(items, list):
        return {}
    try:
        ki = cols.index(key)
    except ValueError:
        return {}
    out = {}
    for row in items:
        if not isinstance(row, (list, tuple)) or len(row) <= ki:
            continue
        sym = str(row[ki] or "")
        if not sym:
            continue
        out[sym] = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
    return out


def auc_qc_net(date_offset=None, date=None):
    """list20 竞额抢筹原始数据: {symbol: {auc_net_amount, free_float_mv, name, auc_pct_chg, ...}}

    源: auc_kp(涨停委买, 实测可用, 138 只涨停/异动池)。
    ★ 拼写注意: apiname 是 **auc_kp**(下划线), "auc-kp" 会返回 1004 不支持的 apiname。
    """
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("auc_kp", params=params, ttl=_AUC_SNAP_TTL,
                       fields="tradedate,symbol,name,auc_net_amount,auc_pct_chg,ztwme,ztwme20,"
                              "auc_vol_ratio,auc_turnover,free_float_mv,fd_amount,limit_times,is_st")
    return _sym_rows(data)


# fundflow_kp 单次请求分片大小: 实测(2026-09-20) 5904 只一次 0.5s 可通, 但保守分片,
# 单片失败只丢该片且响应体更小。2000/片 → 全市场 3 次调用(有 30s 缓存, 采集链一天只多 2 次)。
_FUNDFLOW_BATCH = 2000


def fundflow_map(symbols, date_offset=None, date=None, fresh=False):
    """主力资金 fundflow_kp: {symbol: {main_net_amount, auction_main_net_amount, ...}}。

    六字段(实测 2026-09-20 code=200):
      盘中三件套 main_net_amount / main_buy_amount / main_sell_amount  —— 盘中每分钟更新
      竞价三件套 auction_main_net_amount / auction_main_buy_amount / auction_main_sell_amount
        —— **9:25 开始更新、实测 09:25:35~09:26:16 才出满** ⇒ 定格枪固定在 09:26:30
           (auction_snapshot._BID25_FREEZE_SEC), 采集时已是当日值, 供 17% 异动分因子。
    覆盖实测(2026-09-18 全市场): 竞价主力净额非零仅 32% —— 有大单才有值,
    0 = 竞价无大单异动; 盘中净额返回行内 100% 有值(收盘后=全天值)。
    ★ symbols 必传(逗号分隔批量, 实测 5904 只/次 OK); 分片 _FUNDFLOW_BATCH/次。
    ★ 单片失败仅记日志跳过(不整挂 —— 独立降级纪律, 缺片按"无信号"处理)。
    fresh: True 时跳过缓存直打上游 —— 补采取数专用(见 call_cached 的 fresh 说明);
           普通链路保持 False(默认), 行为与改动前一致。
    """
    syms = [str(s).strip() for s in (symbols or []) if str(s or "").strip()]
    if not syms:
        return {}
    # 🔴 2026-09-28: 分片前排序 ⇒ 同一批票恒定落到同一个缓存键(理由见 _norm_symbols_param)。
    #   原先调用方一处传 sorted(codes)、一处传未排序的候选序 ⇒ 同一份数据各打一遍上游。
    syms.sort()
    out = {}
    for i in range(0, len(syms), _FUNDFLOW_BATCH):
        chunk = syms[i:i + _FUNDFLOW_BATCH]
        params = {"symbols": ",".join(chunk)}
        if date:
            params["tradedate"] = str(date).replace("-", "")
        elif date_offset is not None:
            params["tradedate_offset"] = date_offset
        try:
            data = call_cached("fundflow_kp", params=params, ttl=_AUC_SNAP_TTL,
                               fresh=fresh,
                               fields="tradedate,symbol,name,"
                                      "main_net_amount,main_buy_amount,main_sell_amount,"
                                      "auction_main_net_amount,auction_main_buy_amount,"
                                      "auction_main_sell_amount")
            out.update(_sym_rows(data))
        except Exception as e:                                      # noqa: BLE001
            log.warning("[猫爪] fundflow_kp 分片失败(%d只) err=%s", len(chunk), str(e)[:120])
    return out


def auc_snapshot(trademin, side="before", date_offset=None, date=None):
    """竞价分笔【快照模式】: {symbol: {m_price, auc_pct_chg, auc_vol, auc_amt, um_vol, um_side, ...}}

    源: daily_auc_detail, 传 trademin+side 且不传 symbols → 全市场 5565 只。
    before=边界前最后一条(≈9:2x:5x); after=边界首条(=9:25:00)。
    """
    params = {"trademin": hhmm(trademin), "side": side}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("daily_auc_detail", params=params, ttl=_AUC_SNAP_TTL,
                       fields="tradedate,symbol,time,m_price,auc_pct_chg,auc_vol,auc_amt,"
                              "um_vol,um_side,auc_turnover")
    return _sym_rows(data)


def auc_open_bid(trademin="0925", date_offset=None, date=None):
    """竞价指标 daily_auc: {symbol: {open_bid_pct, auc_pct_chg, m_price, um_vol, auc_vol, ...}}

    open_bid_pct = 开盘抢筹幅度(%) = 9:25开盘价相对9:24最后一笔有效竞价价的涨跌幅
                 → listLast 末秒抢筹的**官方原生字段**, 不必自算。
    """
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("daily_auc", params=params, ttl=_AUC_SNAP_TTL,
                       fields="tradedate,symbol,name,m_price,auc_pct_chg,open_bid_pct,auc_vol,"
                              "auc_amt,um_vol,auc_vol_ratio,auc_turnover,auc_to_pre_vol_pct")
    return _sym_rows(data)


def free_mv_map(date_offset=None, date=None, auc_kp_map=None):
    """全市场自由流通市值字典 {symbol: 自由流通市值(元)} —— 抢筹表/竞价换手市值门槛用。

    ★ 2026-09-20 升级: 主源改为 **screening(实时选股) 的 free_float_mv** ——
      全市场 5553 只 100% 覆盖, 不再受 auc_kp(138只) 限制, 也**不必自算**。
    取值优先级(先权威后兜底, 绝不反序):
      ① 猫爪 screening.free_float_mv   全市场 5553 只(权威)
      ② 猫爪 auc_kp.free_float_mv      涨停委买池 138 只(同口径, 补 screening 偶缺)
      ③ 本地 snapshot_bid 的 free_mv/float_mv  (离线/历史回放兜底)
    返回: {symbol: 元}。取不到市值的票不会出现在字典里(调用方按"缺市值"处理)。
    """
    out = {}
    # ① 猫爪 screening 全市场自由流通市值(权威主源, 5553只)
    try:
        sc = screening_map(date=date, date_offset=date_offset)
        for code, r in (sc or {}).items():
            v = float(r.get("free_float_mv") or 0)
            if v > 0:
                out[str(code)] = v
    except Exception as e:                                     # noqa: BLE001
        log.debug("free_mv_map 猫爪 screening 读取跳过 err=%s", e)
    # ② 猫爪 auc_kp 市值覆盖(同口径, 补 screening 偶缺)
    try:
        kp = auc_kp_map if auc_kp_map is not None else auc_qc_net(date=date, date_offset=date_offset)
        for code, r in (kp or {}).items():
            v = float(r.get("free_float_mv") or 0)
            if v > 0 and str(code) not in out:
                out[str(code)] = v
    except Exception as e:                                     # noqa: BLE001
        log.debug("free_mv_map 猫爪 auc_kp 读取跳过 err=%s", e)
    # ③ 本地全市场快照兜底(离线/历史回放)
    try:
        import sqlite3
        from ..core import config
        conn = sqlite3.connect(config.DB_FILE)
        try:
            if date:
                row = conn.execute(
                    "SELECT code, free_mv, float_mv FROM snapshot_bid "
                    "WHERE date=? AND time_point='9_25'",
                    (str(date),)).fetchall()
            else:
                row = conn.execute(
                    "SELECT code, free_mv, float_mv FROM snapshot_bid "
                    "WHERE date=(SELECT MAX(date) FROM snapshot_bid WHERE time_point='9_25') "
                    "AND time_point='9_25'").fetchall()
            for code, fmv, flmv in row:
                code = str(code)
                if code in out:
                    continue
                v = float(fmv or 0) or float(flmv or 0)
                if v > 0:
                    out[code] = v
        finally:
            conn.close()
    except Exception as e:                                     # noqa: BLE001
        log.debug("free_mv_map 本地快照读取跳过 err=%s", e)
    return out


def valuation_map(date_offset=None, date=None):
    """全市场估值/市值字典 {symbol: {name, circ_mv, total_mv, pe, pb, ...}}。

    ★ 2026-09-19 新增(snapshot_bid 换源关键发现):
      apiname=valuation 覆盖**全市场 5553 只**(实测), 提供
        name / circ_mv(流通市值, 元) / total_mv(总市值, 元) / pe / pb / ps / turnover_rate / pe_ttm。
    ★ 注意: valuation **不支持** free_float_mv / float_market_value(实测 422)。
      自由流通市值请用 screening_map()(全市场 5553 只) —— 见下。
    """
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("valuation", params=params, ttl=_AUC_SNAP_TTL,
                       fields="tradedate,symbol,name,circ_mv,total_mv,pe,pb")
    return _sym_rows(data)


# screening(实时选股) 字段: 一次性覆盖 snapshot_bid 全部所需 + 自由流通市值 + 真实竞价换手。
_SCREENING_FIELDS = (
    "tradedate,symbol,name,close,pre_close,pct_chg,amount,turnover_rate_f,volume_ratio,"
    "free_float_mv,circ_mv,total_mv,type,limit_times,pre_limit_times,is_st,"
    "fd_amount,auc_pct_chg,auc_net_amount,auc_amt,auc_vol,auc_turnover,"
    "ztwme,fa_0915,fa_0920f,fa_0925l,theme_names_kpl,reason_main_kpl,"
    # 2026-09-20 新增: 昨日封单额 + 封昨比(官方原生, 免自算)。
    #   🔴 血泪教训: 先前误加 pre_fd_break_amount/pre_fd_break_times —— 官方文档**无此字段**,
    #     传入即 422「不支持的 fields」→ **整个 screening 调用失败**(猫爪主源全挂)。
    #     screening 封单族字段以 openapi.json 为准, 只有:
    #       fd_amount(今日封单) / pre_fd_amount(昨日封单) / prev_fd_amount(前日封单)
    #       fd_to_turnover(封成比) / fd_to_yesterday(封昨比) / ztwme(涨停委买额)
    #     **新增字段前必须先查 openapi.json, 或单字段试调确认 code==200**。
    "pre_fd_amount,fd_to_yesterday,"
    # 2026-09-24 WP0 新增: open(今开) / vol(成交量, **单位=手**)。picker 适配层
    # (QuoteRow.from_meoz) 要落 open/vol 两列 —— 原先只在本清单外的点查里才取,
    # 导致 meoz 源 open/vol 恒 None(与东财源能力不对等)。
    #   🔴 两者均在 openapi.json 的 screening 字段表内(已核对 54 项清单), 传之安全。
    #      单位实测定论(2026-09-24 真跑 5557 样本): vol×100×close == amount,
    #      越界 0 例 ⇒ **vol 是手**, 契约 vol 是股, 适配层必须 ×100。
    "open,vol"
)


def screening_map(date_offset=None, date=None, symbols=None):
    """实时选股字典 {symbol: {name, free_float_mv, circ_mv, auc_amt, ...}}。

    symbols: 可选, 单个/逗号分隔/列表 —— 传则**点查**(只回这些代码), 不传=全市场。
        🔴 点查模式的语义与全市场**同源同字段**(实测 2026-09-24: 传 symbols 回
        指定行, 字段齐全), 故 picker 的点查源与全市场源共用本函数、共用 TTL。

    """
    """实时选股(全市场)字典 {symbol: {name, free_float_mv, circ_mv, auc_amt, ...}}。

    ★ 2026-09-20 关键发现(主人指路"猫爪实时选股中，有这个数据"):
      apiname=screening(**不传 symbols = 全市场 5553 只**, 实测), 一个接口同时提供:
        free_float_mv  自由流通市值(元)   [5553/100%]  ← ★ 全市场自由流通市值唯一来源
        circ_mv        流通市值(元)       [5553/100%]
        total_mv       总市值(元)         [5553/100%]
        name           名称               [5553/100%]
        close/pre_close/pct_chg/amount/turnover_rate_f/volume_ratio  [5553/100%]
        open/vol       今开(元)/成交量(**手**)[2026-09-24 WP0 新纳入本清单]
        auc_amt        竞价金额(元)        [5444/98%]
        auc_pct_chg    竞价涨幅(%)         [4818/87%]
        auc_turnover   真实竞价换手率(%)    [5440/98%]  ← ★ 官方成品, 免自算
        auc_net_amount 竞价净额(元)        [全市场多为 0; auc_kp 才有值]
        fd_amount      封单额(元)          [仅盘口有效票]
        ztwme/fa_0915/fa_0920f/fa_0925l    委买额/封单额分时
        type/limit_times/pre_limit_times  涨跌停池类型(u/d/ub/db)/连板数
        pre_fd_amount       昨日封单额(元)          ← 2026-09-20 新增
        fd_to_yesterday     封昨比(今日封单/昨日)  ← 2026-09-20 新增(官方成品, 免自算)
        theme_names_kpl/reason_main_kpl   开盘啦题材/关键因素
    ★ 覆盖了原先 valuation + daily_auc + auc_kp + daily_auc_fd 四个接口的并集,
      且**独有 free_float_mv 全市场**。snapshot_bid 采集首选本接口。
    ★ 历史日: 传 tradedate/startdate/enddate/recentdays/tradedate_offset, 支持回填。
    """
    # 🔴 2026-09-28 归一: "全市场实时"有三种等价写法 —— `{}` / `{tradedate:今天}` /
    #   `{tradedate_offset:0}`。键不同 ⇒ 同一份 5000+ 行数据被分别拉取（竞价时段实测
    #   最多 4 份：auction_snapshot / picker.meoz / kpl 兜底 / free_mv_map）。
    #   统一成**相对键 tradedate_offset=0**（历史绝对日仍走 tradedate，以享长 TTL）。
    if date is None and date_offset is None:
        date_offset = 0
    elif date is not None and str(date).replace("-", "") == _bj_today8():
        date, date_offset = None, 0
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    if symbols:
        # 点查: 列表 → 逗号分隔(**排序**) —— openapi 两种形态都接受，排序只为稳定缓存键
        params["symbols"] = _norm_symbols_param(symbols)
    data = call_cached("screening", params=params, ttl=_AUC_SNAP_TTL,
                       fields=_SCREENING_FIELDS)
    return _sym_rows(data)


def daily_auc_amt(trademin="0925", date_offset=None, date=None, fresh=False):
    """竞价额/涨幅(含名称)字典 {symbol: {name, auc_pct_chg, auc_amt, m_price, auc_vol, ...}}。

    ★ snapshot_bid 换源主数源(2026-09-19):
      daily_auc 是**同时具备 name + 竞价额 + 竞价涨幅**的接口(5565 只全市场):
        name         → snapshot_bid.name
        auc_pct_chg  → snapshot_bid.bid_change(竞价涨幅 %)
        auc_amt      → snapshot_bid.bid_amt(竞价额, 元)
        auc_vol_ratio→ snapshot_bid.auc_vol_ratio(竞价量比 = 竞价成交量÷近5日每分钟量)
      ★ 与 auc_snapshot() 的分工: 后者走 daily_auc_detail(有时点 time 但**无 name**);
        本函数走 daily_auc(**有 name**)。snapshot_bid 落库要 name → 用本函数。
      ★ 竞价强度 bid_strength 依赖 snapshot_bid 的 bid_amt/bid_change 自算量比与加速度
        (见 services/bid_strength.py), 故本函数是 17% 权重因子的数据源头。

    ★ 串日语义(2026-09-24 实测, 必须知情): **不传 date / date_offset=0 时, 若目标交易日的
      该分钟尚未产出, 上游会返回"最近可用"那份**(实测 09:15 取 trademin=0925 拿到的是
      上一交易日的 9:25 数据, 5567 行) —— 早盘直接把当日定格写成昨日值。故:
        - 定格链路必须用 `_merge_meoz` 的 **tradedate 防串日**过滤(已加);
        - 「就绪判定」/补采必须**显式传 date**, 不能靠 date_offset=0 猜今天。

    Args:
        trademin: 竞价分钟(HHMM, 如 "0925")。
        date_offset: 相对交易日偏移(0=今天; 仅在不知确切日期时用, 见上方串日语义)。
        date: 目标交易日(YYYY-MM-DD 或 YYYYMMDD); 显式传它可**杜绝串日**。
        fresh: True 时绕过本地缓存直打上游(补采/就绪探测专用)。

    Returns:
        {symbol: {字段: 值}}(含 tradedate, 供调用方校验是否为目标日)。
    """
    params = {"trademin": hhmm(trademin)}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("daily_auc", params=params, ttl=_AUC_SNAP_TTL, fresh=fresh,
                       fields="tradedate,symbol,name,m_price,auc_pct_chg,open_bid_pct,auc_vol,"
                              "auc_amt,um_vol,auc_vol_ratio,auc_turnover,auc_to_pre_vol_pct")
    return _sym_rows(data)


# daily_auc_fd 的封单字段: fa_0915(隔夜) / fa_0916..fa_0925(逐分钟) / fa_0920f(9:20后首笔)
# / fa_0925l(9:25后末笔)。全部 = 竞价阶段「匹配价=涨停价」的竞价金额。
_AUC_FD_FIELDS = ("fa_0915,fa_0916,fa_0917,fa_0918,fa_0919,fa_0920,fa_0921,fa_0922,"
                  "fa_0923,fa_0924,fa_0925,fa_0920f,fa_0925l")


def auc_fd_map(trademin="0925", date_offset=None, date=None):
    """竞价封单字典 {symbol: {name, fa_0915..fa_0925l, auc_pct_chg, auc_amt, ...}}。

    ★ 2026-09-20 新增 —— 补齐 snapshot_bid 最后一个缺口 bid_buy_amt(封单额)。
      源: daily_auc_fd(竞价一字), 覆盖竞价一字/涨停竞价股(实测 121~129 只)。
      字段语义(官方文档): fa_MMDD = 该时刻前最后一笔「匹配价=涨停价」的竞价金额(元)。
      → **fa_0925 就是 9:25 时刻的涨停封单额**, 与 snapshot_bid 9_25 时点严格对应。
      ★ 口径对拍(实测 2026-09-17/09-16): 猫爪 fa_0925 vs 东财 bid_buy_amt(f10×f5×100)
        比值 0.9861 ~ 1.0000(603139/601218 完全一致) → 口径本质相同, 可直接替换。
      ★ 覆盖范围: 猫爪只给「真一字/涨停竞价」股(6~8 只/日), 东财给所有票买一委托(138 只)。
        语义差异: 非涨停股的「买一委托额」在业务上只是参考值(涨停股才叫"封单"),
        故仅涨停票有值符合真实语义。
      ★ 额外红利: 本接口还带 name / auc_turnover(真实竞价换手率, 按自由流通股本) /
        theme_names_kpl(开盘啦题材) → 可作 board 的补充源。
    """
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("daily_auc_fd", params=params, ttl=_AUC_SNAP_TTL,
                       fields="tradedate,symbol,name,auc_pct_chg,auc_amt,auc_turnover,"
                              "is_st,theme_names_kpl," + _AUC_FD_FIELDS)
    return _sym_rows(data)


# =====================================================================
# 换源 WP3/WP4/WP5 新增接口封装(2026-09-24)
# =====================================================================
# 与上面几张竞价表的分工: 这批接口服务的是 **picker/fetcher 的通用数据面**
# (涨停池 / 日K / 分时), 不是集合竞价三张抢筹表。全部为**纯新增**, 切换调用方
# 是各自 WP 包的事(改 fetcher 与 mode.POLICIES)。
#
# 🔴 字段白名单纪律(踩过): `fields` 传官方 openapi **未登记**的字段 → 直接 422,
#    而且失败的是**整个调用**(实测 screening 加 pre_fd_break_amount 导致主源全挂)。
#    下面每个白名单都取自本文件实测返回的列, **新增字段前先单字段试调确认 code==200**。

# limit_pool 字段白名单(实测 2026-09-24 返回 16 列, 此处只取所需 —— 略去 limit_detail
# (逐笔涨停时间线, 体量大且无消费方) 与 limit_type / pre_limit_times)。
_LIMIT_POOL_FIELDS = ("symbol,name,tradedate,type,is_break,limit_times,open_times,"
                      "fd_amount,first_time,last_time,pct_chg,close,amount")

# daily 字段白名单(实测 10 列, 全部需要)
_DAILY_FIELDS = "symbol,name,tradedate,open,high,low,close,pct_chg,vol,amount"

# minute 字段白名单(实测 10 列)
_MINUTE_FIELDS = "symbol,tradedate,trademin,time,open,high,low,close,vol,amount"


def limit_pool_map(date=None, limit_type=None):
    """涨停池 {symbol: {type, is_break, limit_times, open_times, fd_amount, ...}}。

    源: limit_pool。实测(2026-09-24 测试机)语义:
      * **不传 tradedate = 当日**; 传 `tradedate=YYYYMMDD` **可取历史交易日**
        (实测 20260923 / 20260922 均正常返回该日池, 且 tradedate 字段与请求一致)。
      * `tradedate_offset` **必须 ≤ 0**(传 1/2 返 422); 本函数只用显式 tradedate,
        不用 offset —— 与 daily_auc 的「串日」教训同款: 判「哪一天」必须显式传日期。
      * **非交易日返 code=1002「未找到涨跌停池数据」** → call() 返回 None
        ⇒ 上层「往前找最近交易日」的循环要靠**空结果**推进, 不能靠异常。
      * `type` 实测只有 **'u'(涨停) / 'd'(跌停)** 两种取值, 且 **is_break 恒 False**
        (炸板不留在池里, 池是"收盘时的涨跌停集合") ⇒ 「昨日涨停」取 `type=='u'`,
        与东财 getTopicZTPool(涨停池) 语义对齐; 跌停必须过滤掉, 否则昨涨停名单会混入跌停票。

    Args:
        date: 目标交易日 YYYY-MM-DD 或 YYYYMMDD; None = 当日。
        limit_type: 可选 'u' / 'd' —— 仅作为**上游侧**过滤, 上层仍应自行判 type。

    Returns:
        {symbol: {字段: 值}}; 失败/非交易日/未启用返回空 dict。
    """
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    if limit_type:
        params["type"] = str(limit_type)
    data = call_cached("limit_pool", params=params, ttl=_AUC_SNAP_TTL,
                       fields=_LIMIT_POOL_FIELDS)
    return _sym_rows(data)


def limit_pool_yes_map(date=None):
    """涨停池(含昨日维度) {symbol: {... pre_limit_times, pre_pct_chg, pre_fd_amount ...}}。

    源: limit_pool_yes。实测 2026-09-24: 31 列, 当日 65 行; 相比 limit_pool 多出
    `pre_*` 一族(昨日连板 / 昨日涨幅 / 昨日封单)与 `auc_vol_ratio`。
    ★ 附注(与新认知有关): 本接口**也**返回 `auc_vol_ratio` —— 此前记录「竞价量比唯一
      来源 daily_auc」并不完整, 这里存在**第二个来源**(仅覆盖当日涨停票)。
    本函数当前**无生产调用方**(WP3 只用 limit_pool); 保留为已封装能力,
    供后续"连板梯队/昨日涨停成因"场景复用。

    Args:
        date: 目标交易日 YYYY-MM-DD 或 YYYYMMDD; None = 当日。

    Returns:
        {symbol: {字段: 值}}; 失败/未启用返回空 dict。
    """
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    data = call_cached("limit_pool_yes", params=params, ttl=_AUC_SNAP_TTL)
    return _sym_rows(data)


def daily_history_map(symbols, days=3, date=None, fresh=False):
    """日K(**可多日**) {symbol: [行, ...]} —— **返回 list, 不是单行 dict**。

    🔴 为什么必须是 list(2026-09-24 踩): 猫爪返回矩阵结构, `_sym_rows()` 按 symbol
      建字典 —— 多日结果会被**折叠成最后一行**(静默丢数据)。本函数因此改为
      「一行一个 dict、按 symbol 归组」, 不经过 `_sym_rows`。

    实测语义(2026-09-24 测试机):
      * `recentdays=N` → **每只 N 行, 最新在前**(含今日盘中那根 K 线, 若当日有数据)。
      * `tradedate=YYYYMMDD` → 该日单行。
      * `startdate/enddate` → 区间内逐日, 同样最新在前。
      * `limit` 单独用无效(仍 1 行), 且**与 recentdays 同传时被忽略**。
      * 批量: 实测 **800 只/次 0.32s 无截断**(曾误判上限 20 —— 那是 _sym_rows 折叠
        造成的假象, 不是接口限制)。分片仍保守, 见 fetcher._YDAY_MEOZ_BATCH。
      * 字段单位: `vol` = **手**, `amount` = **元**(实测 vol×100×close ≈ amount)。
      * **复权口径**: 与东财 fqt=1 / 腾讯 qfq 同口径(实测 120/120 逐日收盘全等,
        区间跨除权) ⇒ 可直接作日K首源, 不会让除权票出现断层。

    Args:
        symbols: 单个 / 逗号分隔 / 列表。
        days: 取最近多少个交易日(仅多日模式; 传 date 时忽略)。
        date: 显式指定单日交易日 YYYY-MM-DD/YYYYMMDD。
        fresh: True 时绕过本地缓存直打上游(补采/回填场景)。

    Returns:
        {symbol: [ {tradedate, open, high, low, close, pct_chg, vol, amount, name}, ... ]};
        失败/未启用返回空 dict。
    """
    syms = symbols
    if isinstance(syms, (list, tuple, set)):
        seq = [str(x) for x in syms]
    else:
        seq = str(syms or "").split(",")
    # 入口也与 minute_rows 对称地剥掉交易所后缀: 上游 daily 只认纯 6 位,
    # 调用方若传 '600519.SH' 会被上游整批拒(表现为"该票没数据", 极难排查)。
    # 🔴 2026-09-28: 拼串前**排序** —— 昨比按 500 只分片，顺序不稳会让每一片的缓存键都不同；
    #   生产实测竞价时段 429 的主源正是本接口(daily) 的重复拉取。理由见 _norm_symbols_param。
    syms = ",".join(sorted(_strip_market_suffix(x.strip()) for x in seq if x.strip()))
    if not syms:
        return {}
    params = {"symbols": syms}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    else:
        params["recentdays"] = int(days)
    data = call_cached("daily", params=params, ttl=_AUC_SNAP_TTL, fields=_DAILY_FIELDS,
                       fresh=fresh)
    dd = (data or {}).get("data") or {}
    cols = dd.get("fields") or []
    items = dd.get("items") or []
    if not cols or not isinstance(items, list):
        return {}
    out = {}
    for row in items:
        if not isinstance(row, (list, tuple)):
            continue
        m = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
        sym = str(m.get("symbol") or "")
        if not sym:
            continue
        out.setdefault(_strip_market_suffix(sym), []).append(m)
    return out


def minute_rows(symbol, date=None, trademin=None, fresh=False):
    """单股分时 [行, ...] —— 时间升序, 全天 241 根。

    源: minute。实测(2026-09-24 测试机):
      * 不传 trademin → 全天 **241 根**(trademin 0930..1130 共 121 + 1301..1500 共 120;
        注意**没有 1300 这根**)。
      * `tradedate` 可指定历史交易日; `trademin=HHMM` 只取那一根。
      * 🔴 返回的 `symbol` **带交易所后缀**(实测 `600519.SH`), 而 `daily` 返回的是
        纯 6 位 —— **同一数据商两个接口 code 格式不统一**, 故本函数出口统一剥后缀。
      * `trademin` = 该分钟戳("0930"), `time` = 该分钟内的成交时刻("09:25:02" /
        "09:30:59")。画图用 trademin, 不要用 time(它是分钟内的瞬时时刻)。
      * `vol` = 手, `amount` = 元。
      * ⚠️ 单只查询, 不支持批量(传多只会被上游按第一个处理), 故仅用于个股图表。

    Args:
        symbol: 单个代码(可带后缀)。
        date: 目标交易日 YYYY-MM-DD/YYYYMMDD; None = 最近交易日。
        trademin: 只取某一分钟(HHMM, 如 "0930")。
        fresh: True 时绕过本地缓存。

    Returns:
        [ {trademin, time, open, high, low, close, vol, amount}, ... ] 时间升序;
        失败/未启用返回 []。
    """
    sym = _strip_market_suffix(str(symbol or ""))
    if not sym:
        return []
    params = {"symbols": sym}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    if trademin:
        params["trademin"] = hhmm(trademin)
    data = call_cached("minute", params=params, ttl=_AUC_SNAP_TTL, fields=_MINUTE_FIELDS,
                       fresh=fresh)
    dd = (data or {}).get("data") or {}
    cols = dd.get("fields") or []
    items = dd.get("items") or []
    if not cols or not isinstance(items, list):
        return []
    rows = []
    for row in items:
        if not isinstance(row, (list, tuple)):
            continue
        m = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
        if str(m.get("trademin") or ""):
            rows.append(m)
    # 上游已是时间升序(实测 0930→1500); 这里显式排序, 不依赖上游顺序
    rows.sort(key=lambda m: str(m.get("trademin") or ""))
    return rows


def _strip_market_suffix(sym: str) -> str:
    """`600519.SH` / `600519.SZ` / `600519.BJ` → `600519`。

    同一数据商两个接口 code 格式不一致(实测: minute 带后缀, daily 不带) ⇒
    只在**出口**统一剥掉, 避免调用方各写一遍 split。
    """
    s = str(sym or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def tick_fd(symbol, tradedate=None, trademin=None):
    """单股分笔封单(涨停侧 Tick 快照) [{trademin,time,fd_amount,bid1,bid_vol1,...}] 时间升序。

    ★ 2026-09-20 新增: 盘中实时封单查询(仅涨停侧, 单股)。
      ⚠️ trademin 必须为**盘中分钟**(HHMM, >=0930) —— 传 0925 会 422
         ("params.trademin 必须为盘中分钟 HHMM"); 竞价封单请用 auc_fd_map()。
      ⚠️ 单只查询, 不支持批量 → 仅用于个股详情/诊断, 不做全市场采集。
    """
    params = {"symbol": str(symbol)}
    if tradedate:
        params["tradedate"] = str(tradedate).replace("-", "")
    if trademin:
        params["trademin"] = hhmm(trademin)
    r = call("tick_fd", params=params,
             fields="symbol,tradedate,trademin,time,fd_amount,bid1,bid_vol1")
    dd = (r or {}).get("data") or {}
    cols = dd.get("fields") or []
    return [dict(zip(cols, row)) for row in (dd.get("items") or [])]


# ---- 指数快照(2026-09-20 首页指数带) ----
_INDEX_CODES = ("000001", "399001", "399006", "000016",
                "000300", "000688", "000852", "932000")
_INDEX_NAMES = ("上证指数", "深证成指", "创业板指", "上证50",
                "沪深300", "科创50", "中证1000", "中证2000")

# ---- 指数兜底(2026-09-29): 猫爪挂掉时首页指数带不能整块空 ----
# 🔴 源是**实测**挑出来的(2026-09-29 测试机 + 生产机同时验证), 不是随手选:
#   * 腾讯 `qt.gtimg.cn` 简版(**就是它**): 两机 HTTP 200、一次请求拿全部指数、字段齐全
#     (名称/点位/涨跌/涨跌幅), 且与腾讯自家K线**逐位一致**(上证 3823.62 = K线 9/28 收盘)。
#   * 新浪 `hq.sinajs.cn`: **403** —— 该出口 IP 早已被新浪拉黑(core/net.py:18 有记录),
#     且生产机现只剩 eth0 一个出口 ⇒ **不可用**, 别把兜底挂到这里。
#   * 东财 `push2`/`ulist.np`: net.py:16 记「两出口均 HTTP 000(0.05s RST)」, 今日实测时通
#     时不通 ⇒ 不作兜底源(东财只在 push2dycalc / push2ex 域可用)。
_TX_INDEX_URL = "https://qt.gtimg.cn/q="
_TX_INDEX_CODES = {
    "000001": "s_sh000001", "399001": "s_sz399001", "399006": "s_sz399006",
    "000016": "s_sh000016", "000300": "s_sh000300", "000688": "s_sh000688",
    "000852": "s_sh000852",
    # 932000(中证2000): 腾讯简版**不返回**这个代码(实测) ⇒ 该只仍只能靠猫爪/上次值。
}
_TX_INDEX_TTL = 5                       # 秒; 兜底结果的进程内微缓存(防首屏并发打爆腾讯)
_tx_index_cache = {"ts": 0.0, "rows": {}}
_INDEX_LAST_KEY = "index_brief:last"    # 上次成功值(跨进程), 供两级兜底都失败时用
_INDEX_LAST_TTL = 86400                 # 秒


def _fnum(v):
    """宽松转 float: 失败返 None(指数/情绪兜底共用)。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tx_index_rows():
    """腾讯简版指数快照 -> {code: {name,px,preClose,chg,pctChg}}; 失败返 {}。

    行格式(实测): ``v_s_sh000001="1~上证指数~000001~3823.62~-64.75~-1.67~452350675~80454370~~679205.78~ZS~";``
    字段位: [1]名称 [2]代码 [3]最新点位 [4]涨跌点位 [5]涨跌幅% —— **昨收自算 [3]-[4]**
    (实测 3823.62-(-64.75)=3888.37, 与腾讯K线里 9/24 收盘 3888.370 吻合, 故可信)。
    编码是 **GBK**(不是 UTF-8) —— 不解码就得到乱码名。
    """
    now = time.time()
    if _tx_index_cache["rows"] and now - _tx_index_cache["ts"] < _TX_INDEX_TTL:
        return _tx_index_cache["rows"]
    out = {}
    try:
        codes = [_TX_INDEX_CODES[c] for c in _INDEX_CODES if c in _TX_INDEX_CODES]
        req = urllib.request.Request(_TX_INDEX_URL + ",".join(codes), headers={
            "Referer": "https://gu.qq.com/",
            "User-Agent": "Mozilla/5.0",
        })
        with _net.http_get(req, timeout=6) as r:
            txt = r.read().decode("gbk", "ignore")
        for line in txt.split(";"):
            if '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('"')
            f = body.split("~")
            if len(f) < 6:
                continue
            px = _fnum(f[3])
            if px is None or not px:
                continue                       # 点位缺失/为 0 → 视为该只没有
            chg = _fnum(f[4])
            out[f[2]] = {
                "name": f[1] or "",
                "px": px,
                "preClose": round(px - chg, 2) if chg is not None else None,
                "chg": chg,
                "pctChg": _fnum(f[5]),
            }
    except Exception as e:                                     # noqa: BLE001
        log.warning("指数兜底(腾讯简版)失败 err=%s", str(e)[:120])
        return _tx_index_cache["rows"] or {}     # 失败时返回上一次(可能为空), 不抛
    _tx_index_cache["rows"] = out
    _tx_index_cache["ts"] = now
    return out


def index_snapshot():
    """A股核心指数实时快照 -> 有序 list[{code,name,px,preClose,chg,pctChg,src}]。

    源: 猫爪 index_snapshot(指数分钟行情快照)。params.symbols 逗号分隔、**不带市场后缀**;
        返回 symbol 带 .SH/.SZ 后缀 → 按 code 前缀回配到固定顺序。
    字段: name/close(最新点位)/pre_close(昨收)/change(涨跌点位)/pct_chg(涨跌幅%)。

    2026-09-29 兜底(首页第一屏不能看猫爪脸色): 逐只填空, 顺序
      **猫爪(src=meoz) → 腾讯简版(src=tencent) → 上次成功值(src=stale)**。
    只对"确实缺的"那只做兜底 ⇒ 猫爪正常时结果与改动前**逐字段相同**(只多了 src 字段),
    且成功值会落盘(`index_brief:last`, 24h)供下次救急 —— 三源全down 时首页仍有点位可看。
    """
    params = {"symbols": ",".join(_INDEX_CODES)}
    data = call_cached("index_snapshot", params=params, ttl=30,
                       fields="symbol,name,close,pre_close,change,pct_chg")
    rows = _sym_rows(data, key="symbol") or {}

    out = []
    for i, c in enumerate(_INDEX_CODES):
        r = rows.get(c) or rows.get(c + ".SH") or rows.get(c + ".SZ") or rows.get(c + ".CSI") or {}
        px = _fnum(r.get("close"))
        out.append({
            "code": c,
            "name": r.get("name") or _INDEX_NAMES[i],
            "px": px,
            "preClose": _fnum(r.get("pre_close")),
            "chg": _fnum(r.get("change")),
            "pctChg": _fnum(r.get("pct_chg")),
            "src": "meoz" if px else None,
        })

    # ① 二级兜底: 腾讯简版(只补缺口, 不覆盖猫爪的值)
    if any(x["px"] is None for x in out):
        tx = _tx_index_rows()
        for x in out:
            t = tx.get(x["code"]) if x["px"] is None else None
            if t:
                x.update({"name": t["name"] or x["name"], "px": t["px"],
                          "preClose": t["preClose"], "chg": t["chg"],
                          "pctChg": t["pctChg"], "src": "tencent"})

    # ② 三级兜底: 上次成功值(标 src=stale, 让前端/排查一眼看出这是缓存不是实时)
    last = store.get(_INDEX_LAST_KEY)
    if any(x["px"] is None for x in out) and isinstance(last, dict) and last:
        ts = last.get("_ts")
        for x in out:
            l = last.get(x["code"]) if x["px"] is None else None
            if isinstance(l, dict) and l.get("px") is not None:
                x.update({"px": l.get("px"), "preClose": l.get("preClose"),
                          "chg": l.get("chg"), "pctChg": l.get("pctChg"),
                          "src": "stale", "staleTs": ts})

    # ③ 存本次成功值(与上次**合并** —— 部分缺时不要把好值冲掉); 全空则不写, 免得覆盖好缓存
    if any(x["px"] is not None for x in out):
        merged = dict(last) if isinstance(last, dict) else {}
        for x in out:
            if x["px"] is not None and x.get("src") != "stale":
                merged[x["code"]] = {"px": x["px"], "preClose": x["preClose"],
                                     "chg": x["chg"], "pctChg": x["pctChg"]}
        merged["_ts"] = time.time()
        store.set(_INDEX_LAST_KEY, merged, ttl=_INDEX_LAST_TTL)
    return out


# 情绪周期字段(2026-09-20 首页市场情绪卡换猫爪数据源, 主人指令)
# 2026-09-21 修正: apiname 必须是 **emoindic**(不带 _daily)。实测对比(同一账户同一时点):
#   - emoindic_daily: am_diff 恒为 null(即使指定历史 tradedate 也是 null) → "较昨日增量"永远显示 "-"
#   - emoindic:       am_diff 历史日有值; 当日盘中为空, 但同时给出
#                     am_pred(当日成交额预测终值) / am_pred_pct(预测较昨日 %) / am_pred_diff。
#   两接口字段名完全一致, 且 **emoindic 是超集**(另有 s3~s10 分裂家数/mf_*/l2up_rate 等),
#   所以统一改走 emoindic。tradedate 铁律: 必须 YYYYMMDD(传 YYYY-MM-DD 返 422)。
_EMO_FIELDS = ("tradedate,s2,s6,u5,d3,u12,fp108,l1,l2,l3,l17,l21,l22,"
               "deep_retrace_count,am,am_diff,am_pred,am_pred_pct,am_pred_diff")


def _emo_unixts_to_ymd(ts) -> str:
    """秒级/毫秒级 unix 时间戳 → 'YYYYMMDD'(北京时区, 无外部依赖)。"""
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return ""
    if v > 1e11:                       # 毫秒
        v /= 1000.0
    if v <= 0:
        return ""
    import time as _t
    return _t.strftime("%Y%m%d", _t.gmtime(v + 8 * 3600))


def _emo_prev_amt(ymd: str, back: int = 10):
    """取 ymd **之前最近一个有数据的交易日** 的三市成交额 am(元); 找不到返 None。

    🔴 铁律(2026-09-21 实测踩坑): 不能只回退 1 个自然日 —— 周末/节假日上游返回
    code=1002「未找到情绪周期数据」。必须逐日回退直到拿到数据为止(周日 20260920 →
    回退到 20260918 周五才拿到 20931.53 亿)。回退上限 back 天, 覆盖国庆/春节长假。

    历史日的 am 不可变 ⇒ 长 TTL 无风险(2026-09-29 起: 本函数**自己**走 call_cached(ttl=600),
    不再依赖那个从来不存在的"外层缓存")。
    """
    if not ymd:
        return None
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(ymd, "%Y%m%d")
    except ValueError:
        return None
    for i in range(1, back + 1):
        probe = (d - timedelta(days=i)).strftime("%Y%m%d")
        try:
            # 🔴 2026-09-29: 由裸 `call()` 改为 `call_cached(ttl=600)` —— 本函数 docstring
            #   一直宣称"由外层 call_cached(ttl=600) 统一缓存", 但**那个外层并不存在**
            #   (唯一调用点是 emo_daily 里的裸调) ⇒ 每当 am_diff 需要兜底, 都要逐日回退
            #   打上游、最多 10 次**完全不吃缓存**, 是首页情绪卡的 429 放大源之一。
            #   历史日的 am 不可变 ⇒ 长 TTL 无风险(`_hist_ttl_for` 还会对历史 tradedate 再放宽)。
            #   params 里带 tradedate ⇒ 键与主链 emoindic(无 params)**不是同一个**, 不污染主链。
            r = call_cached("emoindic", params={"tradedate": probe}, ttl=600,
                            fields="tradedate,am")
        except Exception:                                      # noqa: BLE001
            continue
        dd = (r or {}).get("data") or {}
        if isinstance(dd, dict) and dd.get("fields") and dd.get("items"):
            try:
                v = dict(zip(dd["fields"], (dd["items"] or [])[0])).get("am")
            except (IndexError, TypeError):
                v = None
            if v is not None:
                return v
    return None


_EMO_LAST_KEY = "emo_brief:last"     # 上次成功的情绪周期(跨进程), 猫爪抖动时顶上
_EMO_LAST_TTL = 86400                # 秒


def _emo_stale_ok(stored_ts, stored_td, now_ts) -> bool:
    """「上次成功的情绪值」此刻是否仍然成立(纯函数, 便于单测)。

    🔴 情绪卡的 s2/s6(涨跌家数)是**盘中实时变化**的量, 不是静态历史数据 ⇒ 不能无脑拿它兜底
      (会把 10 分钟前的家数当此刻的, 属误导)。规则:
        * **盘中(工作日 09:15~15:05)**: 只接受**同一交易日**的存量值 —— 猫爪 8 秒抖动/单次
          失败正是这个场景; 跨日一律拒绝(隔夜家数必然完全不同)。
        * **非盘中**: 数据本来就静态(刚收盘/盘前/夜里都是同一份), 只要求 12 小时内即可。
    """
    td = "".join(ch for ch in str(stored_td or "") if ch.isdigit())[:8]
    now_bj = time.gmtime(now_ts + 8 * 3600)
    hm = now_bj.tm_hour * 60 + now_bj.tm_min
    if now_bj.tm_wday < 5 and 9 * 60 + 15 <= hm <= 15 * 60 + 5:
        return bool(td) and td == time.strftime("%Y%m%d", now_bj)
    try:
        return now_ts - float(stored_ts or 0) < 12 * 3600
    except (TypeError, ValueError):
        return False


def _emo_stale():
    """取上次成功的情绪值(标 `stale=1`); 不可用则返 {} ⇒ 前端照旧显示 '-'。"""
    try:
        last = store.get(_EMO_LAST_KEY)
    except Exception:                                          # noqa: BLE001
        return {}
    if not isinstance(last, dict) or not last:
        return {}
    if not _emo_stale_ok(last.get("_ts"), last.get("tradedate"), time.time()):
        return {}
    out = {k: v for k, v in last.items() if k != "_ts"}
    out["stale"] = 1
    return out


def emo_daily():
    """猫爪情绪周期(emoindic, 默认最新交易日) -> 关键字段平铺 dict。

    字段: s2/s6 涨跌家数; u5/d3 涨停/跌停; u12/fp108 炸板; l17 最高连板;
          l21 一进二成功率(%) / l22 连板晋级率(%); deep_retrace_count 大幅回撤(亏钱效应);
          am 三市成交额(元) / am_diff 较昨日增量(正=放量, 负=缩量)。

    2026-09-21 增量兜底: 当日盘中 am_diff 上游为空, 但 am_pred(预测终值)可用,
    因此按此优先级补出 am_diff / am 口径:
      ① am_diff 有值           → 原样(历史日)
      ② am_diff 空 & am_pred 有 → 用 am_pred − 昨 am 自算, 并置 am_is_pred=1
      ③ 都缺 → am_diff 保持 None(前端显示 "-")
    增量口径统一为 **今 − 昨**, 与 am_diff 上游定义一致, 故可直接比较。

    2026-09-29 兜底(首页情绪卡): 上述两步都拿不到数据时(猫爪抖动/单次失败), 用**上次成功值**
    顶上并标 `stale=1`; 但仅当"该值此刻仍然成立"才用 —— 判定见 `_emo_stale_ok`
    (盘中要求同一交易日, 非盘中要求 12 小时内)。不成立就返 `{}` ⇒ 前端照旧显示 '-',
    **绝不拿隔夜家数冒充当日**(情绪卡在首页第一屏, 错值比空值危害大)。
    """
    try:
        import time as _t
        _today = _t.strftime("%Y%m%d", _t.gmtime(_t.time() + 8 * 3600))
    except Exception:                                          # noqa: BLE001
        _today = ""
    data = call_cached("emoindic", ttl=30, fields=_EMO_FIELDS)
    dd = (data or {}).get("data") or {}
    if isinstance(dd, dict) and dd.get("fields") and dd.get("items"):
        cols = dd["fields"]
        items = dd.get("items") or []
        if items and isinstance(items[0], (list, tuple)):
            out = dict(zip(cols, items[0]))
        else:
            return _emo_stale()      # 空 data → 上次成功值顶上(是否可用见 _emo_stale_ok)
    elif isinstance(dd, dict):
        out = dict(dd)
    else:
        return _emo_stale()
    if not out:
        # 🔴 必须在这里拦: 猫爪失败/空返回时 `dd`={} 属 dict ⇒ 上面走了 `out = dict(dd)` = {}。
        #   若不拦, ① 情绪卡不会用上次成功值兜底; ② 下面存本次成功值会把**空 dict** 写进
        #   `emo_brief:last` ⇒ 把好缓存冲掉(一次抖动就再也兜不回来)。
        return _emo_stale()

    # ---- 增量兜底(仅当日需要: 历史日 am_diff 上游已给) ----
    if out.get("am_diff") is None:
        pred = out.get("am_pred")
        cur = out.get("am")
        base = pred if pred is not None else cur
        if base is not None:
            try:
                tv = out.get("tradedate")
                # 上游 tradedate 是 **字符串 'YYYYMMDD'**(实测 "20260921");
                # 兼容 unix 时间戳(秒/毫秒)客户端。
                if isinstance(tv, (int, float)) or (isinstance(tv, str) and tv.isdigit()
                                                    and len(tv) > 8):
                    td = _emo_unixts_to_ymd(tv)
                else:
                    td = "".join(ch for ch in str(tv or "") if ch.isdigit())[:8]
                    if len(td) != 8:
                        td = ""
                prev = _emo_prev_amt(td)          # 内部逐日回退, 自动跳过周末/节假日
                if prev is not None:
                    out["am_diff"] = float(base) - float(prev)
                    if pred is not None and cur is not None and pred != cur:
                        out["am_is_pred"] = 1      # 用预测终值算的, 前端可标注"预测"
            except Exception:                                  # noqa: BLE001
                pass
    try:
        if out:      # 空 dict 绝不落盘(否则会把好缓存冲掉 —— 见上面的空返回拦截)
            # 存本次成功值供 _emo_stale 顶上(_ts 供新鲜度判定, 读出时会剥掉)
            store.set(_EMO_LAST_KEY, dict(out, _ts=time.time()), ttl=_EMO_LAST_TTL)
    except Exception:                                          # noqa: BLE001
        pass
    return out

