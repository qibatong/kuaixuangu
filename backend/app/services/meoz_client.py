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


def _retry_after_seconds(headers, attempt: int) -> float:
    """429 退避时长: 优先取 Retry-After, 否则指数退避(1s, 2s, ...), 上限 8s。"""
    if headers:
        ra = headers.get("Retry-After")
        if ra:
            try:
                return min(8.0, max(0.5, float(ra)))
            except (TypeError, ValueError):
                pass
    return min(8.0, 1.0 * (2 ** attempt))


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
        raise _HttpCodeError(e.code, dict(e.headers or {}))
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
                        wait = _retry_after_seconds({"Retry-After": e.retry_after}, attempt)
                        log.warning("猫爪 429 限流 a=%s 退避 %.1fs 后重试同线路", apiname, wait)
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
def call_cached(apiname: str, params=None, fields=None, ttl=6, cache_key=None):
    """带缓存的调用。ttl 秒内同 key 直接读缓存, 避免竞价时段高频重复拉同一份数据。

    cache_key: 自定义缓存键; 缺省由 apiname+params 生成。
    """
    if cache_key is None:
        try:
            cache_key = apiname + ":" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        except Exception:                                      # noqa: BLE001
            cache_key = apiname
    full = "meoz:" + cache_key
    hit = store.get(full)
    if hit is not None:
        return hit
    data = call(apiname, params=params, fields=fields)
    if data is not None and ttl > 0:
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


def fundflow_map(symbols, date_offset=None, date=None):
    """主力资金 fundflow_kp: {symbol: {main_net_amount, auction_main_net_amount, ...}}。

    六字段(实测 2026-09-20 code=200):
      盘中三件套 main_net_amount / main_buy_amount / main_sell_amount  —— 盘中每分钟更新
      竞价三件套 auction_main_net_amount / auction_main_buy_amount / auction_main_sell_amount
        —— **9:25 开始更新**, 竞价定格采集(9:25:5x)时已是当日值, 供 17% 异动分因子。
    覆盖实测(2026-09-18 全市场): 竞价主力净额非零仅 32% —— 有大单才有值,
    0 = 竞价无大单异动; 盘中净额返回行内 100% 有值(收盘后=全天值)。
    ★ symbols 必传(逗号分隔批量, 实测 5904 只/次 OK); 分片 _FUNDFLOW_BATCH/次。
    ★ 单片失败仅记日志跳过(不整挂 —— 独立降级纪律, 缺片按"无信号"处理)。
    """
    syms = [str(s) for s in (symbols or []) if str(s or "").strip()]
    if not syms:
        return {}
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
    "pre_fd_amount,fd_to_yesterday"
)


def screening_map(date_offset=None, date=None):
    """实时选股(全市场)字典 {symbol: {name, free_float_mv, circ_mv, auc_amt, ...}}。

    ★ 2026-09-20 关键发现(主人指路"猫爪实时选股中，有这个数据"):
      apiname=screening(**不传 symbols = 全市场 5553 只**, 实测), 一个接口同时提供:
        free_float_mv  自由流通市值(元)   [5553/100%]  ← ★ 全市场自由流通市值唯一来源
        circ_mv        流通市值(元)       [5553/100%]
        total_mv       总市值(元)         [5553/100%]
        name           名称               [5553/100%]
        close/pre_close/pct_chg/amount/turnover_rate_f/volume_ratio  [5553/100%]
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
    params = {}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("screening", params=params, ttl=_AUC_SNAP_TTL,
                       fields=_SCREENING_FIELDS)
    return _sym_rows(data)


def daily_auc_amt(trademin="0925", date_offset=None, date=None):
    """竞价额/涨幅(含名称)字典 {symbol: {name, auc_pct_chg, auc_amt, m_price, auc_vol, ...}}。

    ★ snapshot_bid 换源主数源(2026-09-19):
      daily_auc 是**同时具备 name + 竞价额 + 竞价涨幅**的接口(5565 只全市场):
        name         → snapshot_bid.name
        auc_pct_chg  → snapshot_bid.bid_change(竞价涨幅 %)
        auc_amt      → snapshot_bid.bid_amt(竞价额, 元)
      ★ 与 auc_snapshot() 的分工: 后者走 daily_auc_detail(有时点 time 但**无 name**);
        本函数走 daily_auc(**有 name**)。snapshot_bid 落库要 name → 用本函数。
      ★ 竞价强度 bid_strength 依赖 snapshot_bid 的 bid_amt/bid_change 自算量比与加速度
        (见 services/bid_strength.py), 故本函数是 17% 权重因子的数据源头。
    """
    params = {"trademin": hhmm(trademin)}
    if date:
        params["tradedate"] = str(date).replace("-", "")
    elif date_offset is not None:
        params["tradedate_offset"] = date_offset
    data = call_cached("daily_auc", params=params, ttl=_AUC_SNAP_TTL,
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


def index_snapshot():
    """A股核心指数实时快照 -> 有序 list[{code,name,px,preClose,chg,pctChg}]。

    源: 猫爪 index_snapshot(指数分钟行情快照)。params.symbols 逗号分隔、**不带市场后缀**;
        返回 symbol 带 .SH/.SZ 后缀 → 按 code 前缀回配到固定顺序。
    字段: name/close(最新点位)/pre_close(昨收)/change(涨跌点位)/pct_chg(涨跌幅%)。
    """
    params = {"symbols": ",".join(_INDEX_CODES)}
    data = call_cached("index_snapshot", params=params, ttl=30,
                       fields="symbol,name,close,pre_close,change,pct_chg")
    rows = _sym_rows(data, key="symbol") or {}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    out = []
    for i, c in enumerate(_INDEX_CODES):
        r = rows.get(c) or rows.get(c + ".SH") or rows.get(c + ".SZ") or rows.get(c + ".CSI") or {}
        out.append({
            "code": c,
            "name": r.get("name") or _INDEX_NAMES[i],
            "px": _f(r.get("close")),
            "preClose": _f(r.get("pre_close")),
            "chg": _f(r.get("change")),
            "pctChg": _f(r.get("pct_chg")),
        })
    return out


# 情绪周期字段(2026-09-20 首页市场情绪卡换猫爪数据源, 主人指令)
_EMO_FIELDS = ("s2,s6,u5,d3,u12,fp108,l1,l2,l3,l17,l21,l22,"
               "deep_retrace_count,am,am_diff")


def emo_daily():
    """猫爪情绪周期(emoindic_daily, 默认最新交易日) -> 关键字段平铺 dict。

    字段: s2/s6 涨跌家数; u5/d3 涨停/跌停; u12/fp108 炸板; l17 最高连板;
          l21 一进二成功率(%) / l22 连板晋级率(%); deep_retrace_count 大幅回撤(亏钱效应);
          am 三市成交额(元) / am_diff 较昨日此时(正=放量)。
    兼容矩阵 {data:{fields,items}} 与平铺 {data:{...}} 两种返回结构。
    """
    data = call_cached("emoindic_daily", ttl=30, fields=_EMO_FIELDS)
    dd = (data or {}).get("data") or {}
    if isinstance(dd, dict) and dd.get("fields") and dd.get("items"):
        cols = dd["fields"]
        items = dd.get("items") or []
        if items and isinstance(items[0], (list, tuple)):
            return dict(zip(cols, items[0]))
        return {}
    return dd if isinstance(dd, dict) else {}
