# -*- coding: utf-8 -*-
"""
开盘啦(龙虎榜 App)数据源: 竞价委买额/连板梯队/情绪值/涨停原因/板块强度等
========================================================================
付费接口(每日 80000 次), 用于补充东财拿不到的短线维度。
原则: 低频缓存 + 失败降级(返回 None, 绝不阻塞主流程)。
返回字段均为 App 数组格式(无字段名, 靠位置解析), 统一在此转换为 dict。
"""
import json
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..core import config, logger
from .cache_store import store

log = logger.get_logger(__name__)

# 进程级共享线程池(2026-09-01 生产线程爆炸修复): 现涨K线兜底 原每次请求新建池 +
# shutdown(wait=False) 后线程滞留后台跑网络超时, 高并发下线程只增不减拖死生产。
# 改常驻池: 线程数有界(6), 超时放弃的任务留池内排队, 不阻塞请求也不新建线程。
_EXECUTOR_FILL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="kf-fill")

# ---------- 健康监控 ----------
_HEALTH = {
    "kpl": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
}
_health_lock = threading.Lock()

# 缓存与并发信号量已外置 CacheStore(跨进程共享):
#   - 缓存: store.get/set("kpl:" + key) — 多 worker 共享, 避免付费配额 ×N
#   - 信号量: store.acquire_sem("kpl", limit=3) — 全局并发仍 3
# 旧进程内 _cache / _SEM 移除(2026-08-16 Phase1)

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def clear_cache():
    """清空全部 KPL 缓存(快照采集前强制拿当前时点新鲜数据)"""
    try:
        store.clear_prefix("kpl:")
    except Exception:
        pass


def _record(ok, ms=0):
    with _health_lock:
        h = _HEALTH["kpl"]
        now = time.time()
        if ok:
            h["ok"] += 1
            h["last_ok"] = now
            if ms > 0:
                h["ms_sum"] += ms
                h["ms_cnt"] += 1
            if h["down_since"]:
                log.info("开盘啦数据源恢复(故障%.0f秒)", now - h["down_since"])
                h["down_since"] = 0
        else:
            h["fail"] += 1
            h["last_fail"] = now
            if not h["down_since"]:
                h["down_since"] = now
                log.warning("开盘啦数据源故障(开始降级)")


def _call(host_key, params, timeout=12):
    """调用开盘啦接口, 返回解析后的 dict; 失败返回 None(不抛异常)"""
    host = config.KPL_HOSTS.get(host_key, config.KPL_HOSTS["default"])
    common = {
        "PhoneOSNew": "1",
        "DeviceID": config.KPL_DEVICEID,
        "VerSion": "5.20.0.2",
        "Token": config.KPL_TOKEN,
        "UserID": config.KPL_USERID,
    }
    common.update(params)
    url = "https://" + host + "/w1/api/index.php?" + urllib.parse.urlencode(common)
    req = urllib.request.Request(url, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": config.KPL_UA,
    })
    t0 = time.time()
    # 分布式信号量(跨进程全局并发 3): 保护每日 80000 付费配额
    sem_key = store.acquire_sem("kpl", limit=3, timeout=timeout)
    if sem_key is None:
        _record(False)
        log.warning("KPL 并发信号量获取超时(限流) a=%s", params.get("a"))
        return None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as r:
            body = r.read().decode("utf-8", "ignore")
        data = json.loads(body)
        _record(True, int((time.time() - t0) * 1000))
        if data.get("errcode") not in (None, "0"):
            log.warning("开盘啦接口返回异常 errcode=%s a=%s", data.get("errcode"), params.get("a"))
        return data
    except Exception as e:
        _record(False)
        log.warning("开盘啦调用失败 a=%s err=%s", params.get("a"), e)
        return None
    finally:
        store.release_lock(sem_key)


def _cached(key, ttl, loader):
    """带缓存的读取: TTL 内命中直接返回, 否则调 loader 刷新(跨进程共享)"""
    k = "kpl:" + key
    v = store.get(k)
    if v is not None:
        return v
    data = loader()
    if data is not None:
        store.set(k, data, ttl)
    return data


# ==================== 竞价涨停委买额 ====================
def _parse_bid_seal(data):
    """MorningBiddingList Type=4: info [[code,name,现价,实时涨幅,涨停委买额,竞价涨幅,竞价净额,
    竞价换手,竞价成交额,20分后委买,?,板块,实际流通,?,?,主力净额,连板数], ...]"""
    info = data.get("info")
    if not isinstance(info, list):
        return []
    out = []
    for row in info:
        if not isinstance(row, list) or len(row) < 17:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "realChange": _f(row[3]),
                "bidSealAmt": _f(row[4]),        # 涨停委买额(元)
                "bidChange": _f(row[5]),
                "bidNetAmt": _f(row[6]),         # 竞价净额(元)
                "bidTurnover": _f(row[7]),       # 竞价换手(%)
                "bidAmt": _f(row[8]),            # 竞价成交额(元)
                "board": str(row[11]) if len(row) > 11 else "",
                "floatMv": _f(row[12]),          # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                "mainNet": _f(row[15]),          # 主力净额(元)
                "limitBoards": _lb(str(row[16])) if len(row) > 16 else 0,  # 连板数
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


def fetch_bid_seal():
    """竞价涨停委买额(实时, 9:15-9:30 有效)"""
    def loader():
        t0 = time.time()
        d = _call("default", {"Order": "1", "a": "MorningBiddingList", "st": config.KPL_BID_ST,
                              "c": "HomeDingPan", "Index": "0", "PidType": "0",
                              "apiv": "w41", "Type": "4"})
        lst = _parse_bid_seal(d) if d else None
        ms = int((time.time() - t0) * 1000)
        if lst is None:
            log.warning("竞价委买额(Type4)返回空/解析失败 耗时%dms", ms)
        else:
            log.info("竞价委买额(Type4)返回%d只 耗时%dms", len(lst), ms)
        return lst
    return _cached("bid_seal", config.KPL_BID_TTL, loader)


def fetch_bid_net():
    """竞价净额榜(实时): docs/112 竞价大于1000万 (MorningBiddingList, apphwshhq host + w44)
    (2026-08-18 主人确认: 净额数据原封不动用 doc112 接口; 晚间接口可能为空 → default/w41 双路兜底)
    结构同 Type=4: [code,name,现价,实时涨幅,?,竞价涨幅,竞价净额,竞价换手,竞价成交额,...]"""
    def loader():
        t0 = time.time()
        # 主路: doc112 官方定义 (after=apphwshhq + w44 + PidType=1)
        d = _call("after", {"Order": "1", "a": "MorningBiddingList", "st": "300",
                            "c": "HomeDingPan", "Index": "0", "PidType": "1",
                            "apiv": "w44", "Type": "2"})
        lst = _parse_bid_seal(d) if d else None
        if not lst:
            # 兜底: default host + w41 (doc115 涨停委买额同款 host 组合, 实测晚间有数据)
            d2 = _call("default", {"Order": "1", "a": "MorningBiddingList", "st": "300",
                                   "c": "HomeDingPan", "Index": "0", "PidType": "0",
                                   "apiv": "w41", "Type": "2"})
            lst = _parse_bid_seal(d2) if d2 else None
        ms = int((time.time() - t0) * 1000)
        if not lst:
            log.warning("竞价净额(doc112)返回空/解析失败 耗时%dms", ms)
        else:
            log.info("竞价净额(doc112>1000万)返回%d只 耗时%dms", len(lst), ms)
        return lst or []
    return _cached("bid_net", config.KPL_BID_TTL, loader)


def bid_net_from_snap(date=None):
    """2026-08-22 非竞价/非交易日回退: 用 9_25 快照重建竞价净额榜(全市场竞价金额>1000万),
    与 doc112(MorningBiddingList Type=2 全额>1000万)口径一致, 按竞价额降序。
    返回 [{code,name,bidAmt(元),bidChange,bidTurnover,bidNetAmt,floatMv,board}, ...]"""
    import sqlite3
    if date is None:
        date = time.strftime("%Y-%m-%d")
    conn = sqlite3.connect(config.DB_FILE)
    try:
        row = conn.execute(
            "SELECT MAX(time_point) FROM snapshot_bid WHERE date=? "
            "AND time_point IN ('9_15','9_20','9_24','9_25')", (date,)).fetchone()
        tp = str(row[0]) if row and row[0] else None
        if not tp:
            return []
        rows = conn.execute(
            "SELECT code, name, bid_amt, bid_change, float_mv, board FROM snapshot_bid "
            "WHERE date=? AND time_point=? AND bid_amt >= 1000 ORDER BY bid_amt DESC",
            (date, tp)).fetchall()
    finally:
        conn.close()
    out = []
    for _code, _name, _amt, _chg, _fmv, _board in rows:
        amt = _amt or 0
        fmv = _fmv or 0
        out.append({
            "code": str(_code),
            "name": _name or "",
            "bidAmt": amt * 10000,          # 万元 → 元
            "bidNetAmt": amt * 10000,
            "bidChange": _chg or 0,
            "bidTurnover": round(amt * 10000 / fmv * 100, 4) if fmv else 0.0,
            "floatMv": fmv,
            "board": _board or "",
        })
    log.info("竞价净额快照重建 %d 只 date=%s (9_%s)", len(out), date, tp)
    return out


def fetch_bid_boom():
    """竞价爆量榜(2026-08-19 主人要求改版):
    **按竞价量比排序(不限条数)** — 竞价量比 = 今日竞价额 / 昨日竞价额。
    全市场计算(不再只取 Type10 竞价额前 60): snapshot_bid 表
      - 今日竞价额: 今日最新时点(9_25 > 9_24 > 9_20 > 9_15, 竞价时段自动用最近快照)
      - 昨日竞价额: 最近(严格小于今日)交易日的 9_25 快照
    过滤(2026-08-19 23:10 主人要求): 竞价量比 > 2 且 竞价成交额 > 100万(万元=100)
    返回 [{code,name,bidAmt(元),bidChange,bidRatioYest,floatMv,board}, ...] 按量比降序(全部)"""
    def loader():
        import sqlite3
        g2 = time.gmtime(time.time() + 8 * 3600)
        hm_in_bid = g2.tm_wday < 5 and (9 * 60 + 15) <= (g2.tm_hour * 60 + g2.tm_min) <= (9 * 60 + 30)
        conn = sqlite3.connect(config.DB_FILE)
        try:
            today = time.strftime("%Y-%m-%d")
            # 今日最新时点: 字典序 9_15 < 9_20 < 9_24 < 9_25, MAX 即最新
            row = conn.execute(
                "SELECT MAX(time_point) FROM snapshot_bid WHERE date=? "
                "AND time_point IN ('9_15','9_20','9_24','9_25')", (today,)).fetchone()
            cur_tp = str(row[0]) if row and row[0] else None
            if not cur_tp:
                return []          # 今日暂无快照(盘前/采集异常)
            # 昨日(最近小于今日的交易日) 9_25 竞价额
            row2 = conn.execute(
                "SELECT MAX(date) FROM snapshot_bid WHERE date < ? AND time_point='9_25'",
                (today,)).fetchone()
            yest = str(row2[0]) if row2 and row2[0] else None
            if not yest:
                return []          # 无昨日数据(首日)
            # 今日全市场: code -> (bid_amt万元, name, bid_change, 实际流通市值free_mv, board)
            today_map = {}
            for code, amt, name, chg, fmv, board in conn.execute(
                    "SELECT code, bid_amt, name, bid_change, COALESCE(NULLIF(free_mv,0), float_mv), board FROM snapshot_bid "
                    "WHERE date=? AND time_point=?", (today, cur_tp)):
                today_map[code] = (amt or 0, name or "", chg or 0, fmv or 0, board or "")
            # 昨日 9_25 竞价额(万元)
            ymap = {}
            for code, amt in conn.execute(
                    "SELECT code, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_25'",
                    (yest,)):
                ymap[code] = amt or 0
        finally:
            conn.close()
        # 实时涨幅: 东财全市场行情 map 合并(独立缓存 SPOT_CACHE_TTL, 覆盖全部 6000 只)
        # 注意: 不能用 ensure_cache("filter") — 那只有涨幅前 200 只, 量比榜多数票不在其中 → 0
        spot_map = {}
        try:
            from . import fetcher as _fetcher
            from . import scorer as _scorer
            _fs = _scorer.market_fs(["hs", "cyb", "kcb"])
            spot_map = _fetcher.fetch_spot_quote_map(_fs)
        except Exception as e:
            log.warning("竞价爆量 实时涨幅合并失败(降级0) err=%s", e)
            spot_map = {}
        out = []
        for code, (amt, name, chg, fmv, board) in today_map.items():
            ya = ymap.get(code)
            if not ya or amt <= 100:      # 竞价成交额 ≤ 100万(万元=100) 或 昨日无竞价 → 跳过
                continue
            if chg < 0.01:                # 竞价涨幅 < 0.01%(基本零涨幅/未上涨) → 跳过
                continue
            ratio = round(amt / ya, 2)
            if ratio <= 2:                # 竞价量比 ≤ 2 → 跳过
                continue
            bid_turnover = round(amt * 10000 / fmv * 100, 4) if fmv else 0.0   # 竞价换手 = 竞价额/流通市值×100
            out.append({"code": code, "name": name,
                        "realChange": (spot_map.get(code) or {}).get("realChange", 0.0),   # 实时涨幅(东财全市场map, 全天有值)
                        "bidChange": chg,
                        "bidAmt": amt * 10000,            # 万元 → 元(前端口径)
                        "bidRatioYest": ratio,            # 竞价量比(同单位万元)
                        "bidTurnover": bid_turnover,      # 竞价换手(%)
                        "floatMv": fmv, "board": board,
                        "yestBidAmt": ya * 10000})        # 昨日竞价额(元)
        out.sort(key=lambda x: x["bidRatioYest"], reverse=True)
        log.info("竞价爆量(量比榜) date=%s 时点=%s 昨日=%s 全市场候选=%d (不限条数)",
                 today, cur_tp, yest, len(out))
        return out
    return _cached("bid_boom_ratio_v3", config.KPL_BID_TTL, loader)   # v3: 实时涨幅全市场map(2026-08-19)


def _parse_bid_boom(data):
    """Type=10 竞价爆量榜(实测字段, 2026-08-13 验证):
    [code,name,现价,实时涨幅,委买额(恒0),竞价涨幅,竞价净额,0,0,0,竞价成交额,
    板块,实际流通,主买,主卖,主力净额,连板]"""
    info = data.get("info")
    if not isinstance(info, list):
        return []
    out = []
    for row in info:
        if not isinstance(row, list) or len(row) < 17:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "realChange": _f(row[3]),
                "bidChange": _f(row[5]),
                "bidNetAmt": _f(row[6]),         # 竞价净额(元)
                "bidAmt": _f(row[10]),           # 竞价成交额(元) - 爆量主指标
                "board": str(row[11]) if len(row) > 11 else "",
                "floatMv": _f(row[12]),          # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                "mainBuy": _f(row[13]),
                "mainSell": _f(row[14]),
                "mainNet": _f(row[15]),
                "limitBoards": _lb(str(row[16])) if len(row) > 16 else 0,
                # 2026-08-18: Type=10 无换手列 → 竞价换手 = 竞价成交额/自由流通市值×100
                # (与开盘啦 Type4 bidTurnover 口径一致, 中石科技验算 2.96 vs 2.97)
                "bidTurnover": round(_f(row[10]) / _f(row[12]) * 100, 4) if _f(row[12]) else 0.0,
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


# ==================== 市场情绪 ====================
def fetch_market_breadth():
    """涨跌家数分布(2026-08-16): xuangubao rise_count,fall_count 分时曲线
    今日取最新一点, 昨日取同时刻最近一点(对比用)
    返回 {rise, fall, ts, day, yesterday: {rise, fall, ts, day}}; 失败 None"""
    def _latest(fields, date=None):
        rows = _flash_line(fields, date)
        if not rows:
            return None
        return rows[-1]   # 曲线按时间升序, 最后一条最新

    now_ts = int(time.time())
    today = _latest("rise_count,fall_count")
    if not today or today.get("rise_count") is None:
        return None
    # 昨日同时刻: 前一天日期, 取与 now_ts 最接近(不晚于)的点
    from datetime import datetime, timedelta
    ydate = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yest = _latest("rise_count,fall_count", ydate)
    y = None
    if yest:
        diff = abs(now_ts - int(yest.get("ts") or 0))
        y = {"rise": int(yest["rise_count"]), "fall": int(yest["fall_count"]),
             "ts": int(yest.get("ts") or 0), "day": ydate}
    return {
        "rise": int(today["rise_count"]),
        "fall": int(today["fall_count"]),
        "ts": int(today.get("ts") or now_ts),
        "day": (datetime.now()).strftime("%Y-%m-%d"),
        "yesterday": y,
    }


def fetch_sentiment():
    """情绪值/连板高度: {ztjs 涨停家数, strong 情绪, lbgd 连板高度, df_num 大幅回撤}"""
    def loader():
        d = _call("market", {"a": "ChangeStatistics", "st": "10", "c": "HomeDingPan"})
        if not d:
            return None
        info = d.get("info")
        if isinstance(info, list) and info and isinstance(info[0], dict):
            r = info[0]
            # 跌停家数: doc35 zt_dt_line 涨跌停数曲线最后一条(2026-08-18 主人需求)
            dt_count = 0
            try:
                line = fetch_zt_dt_line()
                if line and isinstance(line[-1], dict):
                    dt_count = int(_num(line[-1].get("limit_down_count")) or 0)
            except Exception:
                pass
            return {
                "ztCount": int(_num(r.get("ztjs"))),      # 涨停家数
                "dtCount": dt_count,                       # 跌停家数(doc35 曲线最新值)
                "strong": int(_num(r.get("strong"))),     # 情绪指标(0-100)
                "lbgd": int(_num(r.get("lbgd"))),         # 连板高度
                "dfNum": int(_num(r.get("df_num"))),      # 大幅回撤
                "day": r.get("Day", ""),
                "tip": d.get("tip", ""),
            }
        return None
    return _cached("sentiment", config.KPL_SENTI_TTL, loader)


# ==================== 连板梯队 ====================
# PidType: 1=首板 2=二板 3=三板 4=四板 5=五板及以上
LADDER_LABEL = {1: "首板", 2: "二板", 3: "三板", 4: "四板", 5: "五板+"}


def _parse_ladder(data, pid_type):
    """DailyLimitPerformance: info [[row,...], ...]; row=[code,name,首次,原因,时间戳,板块,封单,
    最大封单,主力净额,主力买,主力卖,成交额,板块全,实际流通,实际换手,...,板块代码,涨停数量,今,振幅]"""
    info = data.get("info")
    if not isinstance(info, list):
        return []
    rows = info[0] if info and isinstance(info[0], list) else info
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 23:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "reason": str(row[3]) if row[3] else "",
                "limitTime": int(_num(row[4])),           # 涨停时间戳
                "boardName": str(row[5]) if row[5] else "",
                "seal": _f(row[6]),                        # 封单(元)
                "maxSeal": _f(row[7]),                     # 最大封单(元)
                "mainNet": _f(row[8]),                     # 主力净额(元)
                "mainBuy": _f(row[9]),
                "mainSell": _f(row[10]),
                "amount": _f(row[11]),                     # 成交额(元)
                "concept": str(row[12]) if row[12] else "",
                "floatMv": _f(row[13]),                    # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                "turnover": _f(row[14]),                   # 实际换手(%)
                "boardCode": str(row[19]) if len(row) > 19 else "",
                "ztCount": int(_num(row[20])) if len(row) > 20 else 0,
                "amplitude": _f(row[22]) if len(row) > 22 else 0,  # 振幅
                "ladder": pid_type,
                "ladderLabel": LADDER_LABEL.get(pid_type, ""),
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


def fetch_ladder(pid_type=1):
    """连板梯队(实时), pid_type 1~5"""
    def loader():
        d = _call("default", {"Order": "0", "a": "DailyLimitPerformance", "st": "300",
                              "c": "HomeDingPan", "Index": "0", "PidType": str(pid_type),
                              "apiv": "w39", "Type": "4"})
        return _parse_ladder(d, pid_type) if d else None
    return _cached("ladder_" + str(pid_type), config.KPL_LADDER_TTL, loader)


def fetch_ladder_all():
    """连板梯队全档(首板~五板+), 返回 {1:[...],2:[...],...}"""
    out = {}
    for pid in (1, 2, 3, 4, 5):
        out[pid] = fetch_ladder(pid) or []
    return out


# ==================== 连板梯队历史落库与回看 ====================
def save_ladder_history(date):
    """抓取当日连板梯队全档落库 ladder_history(覆盖式), 返回总条数(失败 0)"""
    all_ = fetch_ladder_all()
    total = sum(len(v or []) for v in all_.values())
    if total == 0:
        return 0
    from ..db import database
    conn = database.get_conn()
    for pid, lst in all_.items():
        conn.execute(
            "INSERT OR REPLACE INTO ladder_history (date, pid_type, list, ts) VALUES (?,?,?,?)",
            (date, pid, json.dumps(lst, ensure_ascii=False), int(time.time())))
    conn.commit()
    conn.close()
    return total


def query_ladder_history(date):
    """按日期回看连板梯队, 返回 {1:[...],2:[...],...}(无数据返回空 dict)"""
    from ..db import database
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT pid_type, list FROM ladder_history WHERE date=?", (date,)).fetchall()
    conn.close()
    out = {}
    for pid, raw in rows:
        try:
            out[int(pid)] = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            out[int(pid)] = []
    return out


# ==================== 涨停原因 ====================
def fetch_zt_reason(code):
    """个股当天/历史涨停原因: 返回 [{date, reason, sclt(龙一龙二), boom}, ...]"""
    def loader():
        d = _call("market", {"a": "GetKLineZhangTing", "apiv": "w24",
                             "c": "StockLineData", "StockID": str(code)})
        if not d:
            return None
        lst = d.get("List")
        if not isinstance(lst, list):
            return []
        out = []
        for it in lst:
            if isinstance(it, dict):
                out.append({
                    "date": it.get("Date", ""),
                    "reason": it.get("Reason", "") or it.get("GNSM", ""),
                    "sclt": it.get("SCLT", ""),        # 日内龙一/龙二
                    "boom": it.get("Boom_ZS", ""),
                })
        return out
    return _cached("zt_reason_" + str(code), 300, loader)


# ==================== 板块强度排行 ====================
def _parse_board_rank(data):
    """RealRankingInfo: list [[板块代码,名称,强度,涨幅,涨速,成交额,主力净额,主买,主卖,量比,
    流通值,300万大单,?,总市值,机构增仓,今PE,明PE,强度,涨幅], ...]"""
    lst = data.get("list")
    if not isinstance(lst, list):
        return []
    out = []
    for row in lst:
        if not isinstance(row, list) or len(row) < 17:
            continue
        try:
            out.append({
                "boardCode": str(row[0]),
                "name": str(row[1]),
                "strength": _f(row[2]),        # 强度
                "change": _f(row[3]),          # 涨幅(%)
                "speed": _f(row[4]),           # 涨速(%)
                "amount": _f(row[5]),          # 成交额(元)
                "mainNet": _f(row[6]),         # 主力净额(元)
                "mainBuy": _f(row[7]),
                "mainSell": _f(row[8]),
                "volRatio": _f(row[9]),        # 量比
                "floatMv": _f(row[10]),        # 流通值(元)
                "totalMv": _f(row[13]),        # 总市值(元)
                "instAdd": _f(row[14]),        # 机构增仓(元)
                "peNow": _f(row[15]),          # 今PE
                "peNext": _f(row[16]),         # 明PE
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


def fetch_board_rank():
    """板块强度排行(实时)"""
    def loader():
        d = _call("market", {"Order": "1", "a": "RealRankingInfo", "st": "60",
                             "apiv": "w26", "Type": "1", "c": "ZhiShuRanking",
                             "Index": "0", "ZSType": "7"})
        return _parse_board_rank(d) if d else None
    return _cached("board_rank", config.KPL_BOARD_TTL, loader)


def fetch_board_rank_by_date(date):
    """精选板块列表-历史(doc42 apiv=w41, apphis host):
    按 Date='YYYY-MM-DD' 取指定交易日 9:25-15:00 期间的板块强度 Top60
    实测保留期=最近 3 个交易日(超出日期返回空)
    返回 [{boardCode, name, strength, change, amount, mainNet, volRatio, floatMv, ...}]"""
    d = _call("his", {"Order": "1", "a": "RealRankingInfo", "st": "60", "apiv": "w41",
                      "c": "ZhiShuRanking", "PhoneOSNew": "1",
                      "Start": "0925", "VerSion": "5.20.0.2", "End": "1500",
                      "Date": date, "Type": "5", "ZSType": "7"})
    return _parse_board_rank(d) if d else []


def fetch_board_stocks(plate_id, date=None, st=30):
    """板块成分股(开盘啦 doc46 ZhiShuStockList_W8, **apphis host + apiv=w41**):
    板块强度点开看成分股.
    PlateID: 板块代码(如 801001 芯片); date: 'YYYY-MM-DD' 历史.
    2026-08-18 修复: 原实现用 apphwshhq+w44 实时模式(无 Date) — 当日数据冻结前开盘啦一律返回
    errcode=1020(实时模式被拒), 历史模式(带 Date)正常. 改走 apphis+w41 且**始终带 Date**:
    实时模式自动带上一交易日(当天未冻结前取最近交易日成分股, 消除空白).
    实测 30 条(按强度/涨幅排序); 返回 [{code, name, concept, price, change, turnover, amount,
    floatMv, mainNet, volRatio, limitTag, ladder, totalMv}, ...].
    st: 返回条数上限(默认 30; 2026-08-18 加: 昨涨停/昨断板成分需全量, 传 500)"""
    base = {
        "Order": "1", "a": "ZhiShuStockList_W8", "st": str(st),
        "c": "ZhiShuRanking", "PhoneOSNew": "1",
        "IsZZ": "0", "Index": "0", "RStart": "0925", "REnd": "1500",
        "Type": "5", "IsKZZType": "0",
        "PlateID": str(plate_id), "TSZB": "0", "TSZB_Type": "0",
    }
    if not date:
        # 盘中优先用实时接口(apphwshhq + w44, 不带Date)取当日数据;
        # 实时接口被拒或空时回退历史接口(apphis + w41 + 上一交易日Date)
        # 2026-08-30 修复: _call host_key "app" 不存在 → fallback default(apphwhq 竞价域名),
        #   对板块成分股返回空导致盘中一直回退昨日; 实时 host 应为 "after"(apphwshhq)
        params = dict(base, apiv="w44")
        d = _call("after", params)
        lst = d.get("list") if isinstance(d, dict) else None
        if not isinstance(lst, list) or not lst:
            date = _prev_trade_day()
            params = dict(base, apiv="w41", Date=date)
            d = _call("his", params)
            lst = d.get("list") if isinstance(d, dict) else None
    else:
        params = dict(base, apiv="w41", Date=date)
        d = _call("his", params)
        lst = d.get("list") if isinstance(d, dict) else None
    if not isinstance(lst, list):
        return []
    out = []
    for row in lst:
        if not isinstance(row, list) or len(row) < 12:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "concept": str(row[4] or ""),
                # 字段对照(2026-08-17 东财交叉验证):
                # [5]=最新价(元), [6]=涨跌幅%(20% 涨停板验证: 华民19.95/奥来德19.99/聚和20.01)
                # [21]=量比(2.31=东财f10), [25]=换手率%(26.91=东财f8)
                "price": _f(row[5]),          # 最新价(元)
                "change": _f(row[6]),         # 涨跌幅(%)
                "amount": _f(row[7]),         # 成交额(元)
                "floatMv": _f(row[10]),       # 流通市值(元)
                "mainNet": _f(row[11]),       # 主力净额(元)
                "volRatio": _f(row[21]) if len(row) > 21 else 0,   # 量比
                "limitTag": str(row[23] or "") if len(row) > 23 else "",   # 首板/连板标识
                "ladder": str(row[24] or "") if len(row) > 24 else "",     # 龙一/龙二等梯队
                "turnover": _f(row[25]) if len(row) > 25 else 0,          # 换手率(%)
                "totalMv": _f(row[38]) if len(row) > 38 else 0,           # 总市值(元)
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


# ==================== 尾盘竞价抢筹 ====================
def fetch_wpqc():
    """尾盘竞价抢筹(14:57 后): List [[code,name,资金标签,类型,概念,涨跌幅,抢筹委托,收盘金额,
    抢筹买,抢筹卖,抢筹净额,大单买,大单卖,连板数,涨停标识,抢筹涨幅,抢筹强度], ...]"""
    def loader():
        d = _call("after", {"Order": "1", "st": "30", "a": "GetWPQC", "Index": "0",
                            "apiv": "w44", "Type": "1"})
        if not d:
            return None
        lst = d.get("List")
        if not isinstance(lst, list):
            return []
        out = []
        for row in lst:
            if not isinstance(row, list) or len(row) < 17:
                continue
            try:
                out.append({
                    "code": str(row[0]),
                    "name": str(row[1]),
                    "concept": str(row[4]) if row[4] else "",
                    "change": _f(row[5]),           # 涨跌幅(%)
                    "qcAmt": _f(row[6]),            # 抢筹委托金额(元)
                    "qcBuy": _f(row[8]),
                    "qcSell": _f(row[9]),
                    "qcNet": _f(row[10]),           # 抢筹净额(元)
                    "limitBoards": int(_num(row[13])) if len(row) > 13 else 0,
                    "qcChange": _f(row[15]) if len(row) > 15 else 0,   # 抢筹涨幅(%)
                    "qcStrength": _f(row[16]) if len(row) > 16 else 0, # 抢筹强度
                })
            except (IndexError, ValueError, TypeError):
                continue
        return out
    return _cached("wpqc", 30, loader)


# ==================== 人气热榜 ====================
def fetch_hot_rank():
    """盘中人气热榜: List [[code,name,涨跌幅,?,排名,?,?], ...]"""
    def loader():
        d = _call("market", {"Order": "1", "a": "GetHotPHB", "st": "50",
                             "apiv": "w29", "Type": "1", "c": "StockBidYiDong"})
        if not d:
            return None
        lst = d.get("List")
        if not isinstance(lst, list):
            return []
        out = []
        for row in lst:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                out.append({
                    "code": str(row[0]),
                    "name": str(row[1]),
                    "change": _f(row[2]),        # 涨跌幅(%)
                    "rank": int(_num(row[4])),   # 人气排名
                })
            except (IndexError, ValueError, TypeError):
                continue
        return out
    return _cached("hot_rank", 60, loader)


# ==================== 龙虎榜 ====================
def fetch_lhb(date=""):
    """龙虎榜上榜股票(当天/指定历史日期): [{code,name,change,limitBoards,buyIn,amount,floatMv,turnover,amplitude,totalMv,joinNum}, ...]
    date: 空=当天; 'YYYY-MM-DD' 查历史(实测 Time 参数支持历史)"""
    def loader():
        d = _call("lhb", {"a": "GetStockList", "st": "500", "c": "LongHuBang",
                          "Time": date, "Index": "0", "apiv": "w44", "Type": "2"})
        if not d:
            return None
        lst = d.get("list")
        if not isinstance(lst, list):
            return []
        out = []
        for it in lst:
            if not isinstance(it, dict):
                continue
            out.append({
                "code": str(it.get("ID", "")),
                "name": str(it.get("Name", "")),
                "change": _pct(it.get("IncreaseAmount")),   # 涨幅(%)
                "limitBoards": int(_num(it.get("D3"))),     # 连板数
                "buyIn": _f(it.get("BuyIn")),               # 买入金额(元)
                "joinNum": int(_num(it.get("JoinNum"))),    # 上榜营业部数
                "amount": _f(it.get("Turnover")),           # 成交额(元)
                "floatMv": _f(it.get("CircPrice")),         # 流通市值(元)
                "amplitude": _f(it.get("Amplitude")),       # 振幅(%)
                "turnover": _f(it.get("TurnoverRatio")),    # 换手率(%)
                "totalMv": _f(it.get("Capitalization")),    # 总市值(元)
            })
        return out
    return _cached("lhb:" + (date or "today"), 120, loader)


def fetch_lhb_detail(code, date=""):
    """龙虎榜个股营业部明细: {name,time,change,limitBoards,buyTotal,sellTotal,upReason,
    buyList:[{name,buy,sell}], sellList:[{name,buy,sell}]}"""
    def loader():
        d = _call("lhb", {"c": "Stock", "a": "GetNewOneStockInfo", "Type": "0",
                          "Time": date, "StockID": str(code)})
        if not d:
            return None
        lst = d.get("List")
        item = {}
        if isinstance(lst, list) and lst and isinstance(lst[0], dict):
            item = lst[0]
        return {
            "name": str(d.get("Name", "")),
            "time": str(d.get("Time", "")),
            "change": _pct(d.get("QuoteChange")),
            "limitBoards": int(_num(d.get("lbnum"))),
            "buyIn": _f(d.get("BuyIn")),
            "amount": _f(d.get("Turnover")),
            "turnover": _f(d.get("TurnoverRatio")),
            "buyTotal": _f(item.get("BuyTotal")),
            "sellTotal": _f(item.get("SellTotal")),
            "upReason": str(item.get("UpReason", "") or ""),
            "buyList": [{"name": str(x.get("Name", "")), "buy": _f(x.get("Buy")), "sell": _f(x.get("Sell"))}
                        for x in (item.get("BuyList") or []) if isinstance(x, dict)],
            "sellList": [{"name": str(x.get("Name", "")), "buy": _f(x.get("Buy")), "sell": _f(x.get("Sell"))}
                         for x in (item.get("SellList") or []) if isinstance(x, dict)],
        }
    return _cached("lhb_detail_" + str(code) + "_" + str(date), 300, loader)


# ==================== 昨日涨停今表现(策略验证) ====================
def fetch_yesterday_perf():
    """昨日涨停/连板/破板今日平均表现: {zt:{change,net,date}, lb:{...}, pb:{...}}
    List[4]=平均涨幅(%), List[3]=主力净额(元)。用于验证"剔除昨日涨停"策略合理性。"""
    def loader():
        out = {}
        for pid, key in [("801900", "zt"), ("801901", "lb"), ("801902", "pb")]:
            d = _call("after", {"a": "GetPlate_Info_QJ", "apiv": "w42",
                                "c": "ZhiShuRanking", "PlateID": pid, "Date": ""})
            if not d:
                continue
            lst = d.get("List")
            if isinstance(lst, list) and len(lst) >= 5:
                out[key] = {
                    "change": _f(lst[4]),   # 今日平均涨幅(%)
                    "net": _f(lst[3]),      # 主力净额(元)
                    "date": d.get("Date", ""),
                }
        return out
    return _cached("yesterday_perf", 300, loader)


# ==================== 炸板/涨停池(东财 flash 公开接口, 无需 Token) ====================
_FLASH_BASE = "https://flash-api.xuangubao.cn/api/pool/detail?pool_name="


def _flash_pool(pool_name, date=None):
    """东财 flash 池通用请求: pool_name=limit_up_broken/limit_up_pool 等, date 可选(YYYY-MM-DD)
    返回 [{code,name,change,limitUpDays,breakTimes,reason,...}, ...]; 失败返回 []"""
    url = _FLASH_BASE + pool_name + (("&date=" + date) if date else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("flash 池请求失败 pool=%s date=%s err=%s", pool_name, date or "-", e)
        return []
    lst = d.get("data")
    if not isinstance(lst, list):
        return []
    out = []
    for it in lst:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol", "") or "")
        out.append({
            "code": sym.split(".")[0],
            "name": str(it.get("stock_chi_name", "") or ""),
            "change": _f(it.get("change_percent")) * 100,
            "limitUpDays": int(_num(it.get("limit_up_days"))),
            "breakTimes": int(_num(it.get("break_limit_up_times"))),
            "firstLimitUp": int(_num(it.get("first_limit_up"))),
            "firstBreak": int(_num(it.get("first_break_limit_up"))),
            "reason": _surge_reason(it.get("surge_reason")),
            "day": date or time.strftime("%Y-%m-%d"),
        })
    return out


def real_limit_days(date):
    """当日涨停池(封住)每只股票的真实连板数 {code: limitUpDays}。
    用于连板天梯图: 开盘啦连板梯队 pid 只分到'五板+'(≥5), 无法区分 6 板以上;
    用东财 flash 涨停池的 limit_up_days 取真实连板数, 修正显示的连板与顶部最高连板。
    失败/为空返回 {}(调用方回退到 pid 档位)。"""
    m = {}
    for it in _flash_pool("limit_up_pool", date):
        lu = int(it.get("limitUpDays") or 0)
        if lu >= 1:
            m[it.get("code")] = lu
    return m



# ==================== xuangubao 免费接口封装(kaipanla 文档收录, 无需 Token) ====================
# 16 个接口: 涨停/炸板/跌停(实时+历史) + 曲线(涨跌家数/涨停跌停/炸板率/昨涨停今表现/市场温度)
# + 热点解读/板块题材 + 直播 + 个股大单净额
# 响应格式: {code:20000, message:OK, data:...}

_FLASH_LINE = "https://flash-api.xuangubao.cn/api/market_indicator/line?fields="
_FLASH_SURGE = "https://flash-api.xuangubao.cn/api/surge_stock/"


def _flash_line(fields, date=None):
    """xuangubao 曲线接口: fields=逗号分隔指标; date 可选(YYYY-MM-DD)
    返回 [{field: value, timestamp: 秒}, ...]; 失败返回 []"""
    url = _FLASH_LINE + fields + (("&date=" + date) if date else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("xuangubao 曲线失败 fields=%s err=%s", fields, e)
        return []
    data = d.get("data")
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if isinstance(it, dict):
            row = {k: v for k, v in it.items() if k != "timestamp"}
            row["ts"] = it.get("timestamp")
            out.append(row)
    return out


def fetch_zt_pool(day=None):
    """涨停实时池(doc10): day=None 今日; YYYY-MM-DD 历史. 复用 _flash_pool"""
    def loader():
        rows = _flash_pool("limit_up", day)
        if not rows and not day:
            return []
        return rows
    key = "zt_pool" + (("_" + day.replace("-", "")) if day else "")
    return _cached(key, (30 * 60) if day else 30, loader)


def fetch_dt_pool(day=None):
    """跌停实时池(doc12): day=None 今日; YYYY-MM-DD 历史"""
    def loader():
        return _flash_pool("limit_down", day) or []
    key = "dt_pool" + (("_" + day.replace("-", "")) if day else "")
    return _cached(key, (30 * 60) if day else 30, loader)


def fetch_yest_zt_pool(day=None):
    """昨日涨停池(doc28): 默认今日的昨日; 可指定 YYYY-MM-DD"""
    def loader():
        d = day or _prev_trade_day()
        if not d:
            return []
        return _flash_pool("yesterday_limit_up", d) or []
    key = "yest_zt_pool" + (("_" + day.replace("-", "")) if day else "")
    return _cached(key, 30 * 60, loader)


def fetch_updown_line(date=None):
    """上涨/下跌家数曲线(doc34)"""
    return _cached("updown_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("rise_count,fall_count", date) or [])


def fetch_zt_dt_line(date=None):
    """涨停数与跌停数曲线(doc35)"""
    return _cached("zt_dt_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("limit_up_count,limit_down_count", date) or [])


def fetch_broken_line(date=None):
    """炸板数量曲线(doc36): limit_up_broken_count + ratio"""
    return _cached("broken_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("limit_up_broken_count,limit_up_broken_ratio", date) or [])


def fetch_yest_zt_perf_line(date=None):
    """昨日涨停今日表现曲线(doc37): yesterday_limit_up_avg_pcp"""
    return _cached("yest_zt_perf_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("yesterday_limit_up_avg_pcp", date) or [])


def fetch_market_temp_line(date=None):
    """市场温度曲线(doc38): market_temperature"""
    return _cached("mkt_temp_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("market_temperature", date) or [])


def _flash_surge(path, params=""):
    """xuangubao 热点接口: path=stocks/plates; params 查询串"""
    url = _FLASH_SURGE + path + (("?" + params) if params else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("xuangubao 热点失败 path=%s err=%s", path, e)
        return {}
    data = d.get("data")
    return data if isinstance(data, dict) else {}


def fetch_hot_stocks():
    """热点解读-强势股列表(doc39): items 是二维数组(fields 作列头)
    → [{code,name,price,change,circulation,desc,plates}, ...]"""
    d = _flash_surge("stocks", "normal=true&uplimit=true")
    fields = d.get("fields") or []
    lst = d.get("items") or []
    if not isinstance(lst, list):
        return []
    out = []
    for row in lst:
        if not isinstance(row, list):
            continue
        it = dict(zip(fields, row))
        code = str(it.get("code", "")).split(".")[0]      # 去 .SZ/.SH 后缀
        plates = it.get("plates") or []
        if isinstance(plates, list):
            plates = "、".join(str(p.get("name", "")) for p in plates if isinstance(p, dict) and p.get("name"))
        out.append({
            "code": code,
            "name": str(it.get("prod_name", "") or ""),
            "price": it.get("cur_price"),
            "change": round(_f(it.get("px_change_rate")) * 100, 2),
            "circulation": it.get("circulation_value"),     # 流通市值(元)
            "desc": str(it.get("description", "") or ""),
            "plates": plates,                               # 所属板块(拼接)
            "enterTime": it.get("enter_time"),
            "upLimit": it.get("up_limit"),
        })
    return out[:100]


def fetch_hot_plates():
    """板块名称与对应题材(doc40): [{id,name,description}, ...]"""
    d = _flash_surge("plates")
    items = d.get("items") or []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("name"):
            out.append({"id": it.get("id"), "name": it.get("name"), "description": it.get("description", "")})
    return out[:100]


def fetch_live_room():
    """涨停直播(doc32): fupanwang 实时涨停播报"""
    url = "https://api.fupanwang.com/kpl/zhibo"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("涨停直播失败 err=%s", e)
        return []
    lst = d.get("data") or d.get("list") or []
    return lst if isinstance(lst, list) else []


def fetch_dadan_net(StockID, Time=None):
    """指定个股-大单净额分时(doc75): GetStockDaDanTrendIncremental
    返回 {dadanjinge: [[时间, 大单净额], ...], max, min, ...}"""
    if not Time:
        Time = int(time.time())
    d = _call("default", {"a": "GetStockDaDanTrendIncremental", "c": "StockL2Data",
                           "apiv": "w44", "StockID": str(StockID), "Time": str(Time)})
    if not d:
        return {}
    return {
        "code": str(d.get("code", "")),
        "dadanjinge": d.get("dadanjinge") or [],
        "max": d.get("max"),
        "min": d.get("min"),
        "day": d.get("day", ""),
    }


def _prev_trade_day():
    """上一交易日: snapshot_bid 记录优先(自动跳过节假日); 失败降级为日历跳过周末"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        today = time.strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT DISTINCT date FROM snapshot_bid WHERE date < ? ORDER BY date DESC LIMIT 1",
            (today,),
        ).fetchall()
        conn.close()
        if rows:
            return rows[0][0]
    except Exception as e:
        log.warning("上一交易日查询失败(降级日历) err=%s", e)
    from datetime import datetime, timedelta
    d = datetime.now() - timedelta(days=1)
    while d.weekday() >= 5:  # 跳过周末(法定节假日由 snapshot_bid 路径覆盖)
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def fetch_broken_zt(day=None):
    """炸板列表(东财 flash, 无需Token): day=None 今日; 'yesterday' 上一交易日; 'YYYY-MM-DD' 指定日
    今日炸板: merge 昨日涨停池补连板数(今日炸板票若昨日涨停 → 显示昨日连板数)
    返回 [{code,name,change,limitUpDays,breakTimes,firstLimitUp,firstBreak,reason,day}, ...]"""
    is_hist = False
    if day == "yesterday":
        day = _prev_trade_day()
        if not day:
            return []
    if day:
        is_hist = True
    cache_key = "broken_zt" + (("_" + day.replace("-", "")) if day else "")

    def loader():
        lst = _flash_pool("limit_up_broken", day)
        if not lst:
            return lst
        if day:      # 历史日不做连板补全(无昨日池语义), 但补竞价涨幅/换手
            return _merge_broken_bid_snap(lst)
        # 今日炸板: 接口 limit_up_days 常为0, 用昨日涨停池补连板数(昨日N板 → 今日炸板显示N板)
        yest_day = _prev_trade_day()
        yest_map = {}
        if yest_day:
            yest_map = {x["code"]: x["limitUpDays"]
                        for x in _flash_pool("limit_up_pool", yest_day)}
        for it in lst:
            if not it.get("limitUpDays") and it["code"] in yest_map:
                it["limitUpDays"] = yest_map[it["code"]]
        return _merge_broken_bid_snap(lst)
    return _cached(cache_key, (30 * 60) if is_hist else 30, loader)


# ==================== 昨日涨停(flash 涨停池 + 今日竞价表现) ====================
def _seal_map():
    """竞价委买榜 code → 完整行(概念/流通/换手/净额/连板), 用于字段补全"""
    try:
        return {s["code"]: s for s in (fetch_bid_seal() or [])}
    except Exception:
        return {}


def _snap25_map(date=None):
    """指定日 9_25 全市场快照 code → {bid_change, bid_amt, name, float_mv, free_mv, board}
    (全市场5549只, 字段补全兜底); date 空=今天"""
    import sqlite3
    if not date:
        date = time.strftime("%Y-%m-%d")
    out = {}
    try:
        conn = sqlite3.connect(config.DB_FILE)
        for r in conn.execute(
                "SELECT code, bid_change, bid_amt, name, float_mv, free_mv, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (date,)):
            out[r[0]] = {"bid_change": r[1], "bid_amt": r[2], "name": r[3] or "",
                         "float_mv": r[4] or 0, "free_mv": r[5] or 0, "board": r[6] or ""}
        conn.close()
    except Exception as e:
        log.warning("9_25快照查询失败 date=%s(降级) err=%s", date, e)
    return out


def _merge_broken_bid_snap(lst, bid_date=None):
    """炸板列表补竞价涨幅/竞价换手: 按每条 day 查该日 9_25 快照;
    若传入 bid_date(形如 "2026-08-24"), 则强制用 bid_date 的快照统一补竞价字段
    (用于"昨炸板看今日竞价"场景: 股票池=昨日炸板, 但bidChange/bidTurnover/floatMv/bidAmt 用今日9_25)。
    bidTurnover = 竞价额(元)/自由流通市值(元)×100(短线侠同口径近似)
    返回补全后的列表(原地修改+返回)"""
    if not lst:
        return lst
    import time as _t
    today_default = _t.strftime("%Y-%m-%d")
    if bid_date:
        # 强制统一日期: 单次查 bid_date 快照即可, 覆盖 bidChange/bidTurnover/floatMv/bidAmt
        snap = _snap25_map(bid_date) or {}
        for it in lst:
            code = str(it.get("code") or "")
            s = snap.get(code)
            if not s:
                continue
            if s.get("bid_change") is not None:
                it["bidChange"] = s["bid_change"]
            amt = s.get("bid_amt") or 0       # 万元
            fmv = s.get("float_mv") or 0      # 元
            if fmv:
                it["floatMv"] = fmv
            if amt:
                it["bidAmt"] = amt * 10000    # 万元→元(与其他表口径一致, 前端再fmt)
            if amt > 0 and fmv > 0:
                # 精度4位: 大盘小额股不再被round到0
                it["bidTurnover"] = round(amt * 10000 / fmv * 100, 4)
        return lst
    # 按 day 分组查快照(避免重复查库) — 默认兼容行为: 按每条记录自己的 day
    by_day = {}
    for it in lst:
        d = it.get("day") or today_default
        by_day.setdefault(d, [])
    snap_cache = {d: _snap25_map(d) for d in by_day}
    for it in lst:
        s = snap_cache.get(it.get("day") or today_default, {}).get(it["code"], {})
        if not s:
            continue
        it["bidChange"] = s.get("bid_change")
        amt = s.get("bid_amt") or 0      # 万元
        fmv = s.get("free_mv") or s.get("float_mv") or 0     # 实际流通市值(元), 快照 free_mv 优先(f117), f21 兜底
        it["floatMv"] = fmv or it.get("floatMv") or 0   # 实际流通市值(元), 2026-08-19 竞价异动统一流通列改实际流通
        if amt > 0 and fmv > 0:
            it["bidTurnover"] = round(amt * 10000 / fmv * 100, 4)   # 万元→元 口径统一(精度4位, 避免大盘小额股如0.0017%显示为0)
    return lst


def fill_float_mv_from_snap(lst, date=None):
    """用 date(空=今日) 的 9_25 全市场快照给列表补实际流通市值(floatMv, 元); 已带的不覆盖
    用于历史回看快照/炸板等数据源补流通列(2026-08-17; 2026-08-19 改实际流通 free_mv 优先)"""
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        for it in lst:
            code = str(it.get("code") or "")
            if code and not it.get("floatMv") and code in snap:
                fmv = snap[code].get("free_mv") or snap[code].get("float_mv") or 0
                if fmv:
                    it["floatMv"] = fmv
    except Exception as e:
        log.warning("实际流通市值补齐失败 date=%s err=%s", date or "-", e)
    return lst


def _snap25_kpl_map():
    """9_25 快照中『已被开盘啦覆盖』的涨停股 board map: code → 开盘啦 board
    启发式: 涨停股(board 已是开盘啦概念, 短字符串 2-30 字符, 顿号或短逗号分隔)
    非涨停股的 snapshot_bid.board 是东财 f103(短线侠多概念, 字符串长), 不进入此 map
    用于 fetch_board_map 兜底: 周末/非交易时段 KPL 实时接口空时仍能给涨停股用开盘啦 board"""
    import sqlite3
    out = {}
    try:
        conn = sqlite3.connect(config.DB_FILE)
        for r in conn.execute(
                "SELECT code, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25' AND board IS NOT NULL AND board != ''",
                (time.strftime("%Y-%m-%d"),)):
            b = r[1] or ""
            # 启发式: 开盘啦概念短 (典型 2-30 字符), 东财 f103 短线侠长 (平均 60+)
            # 例: "创新药、AI应用"(9字), "股权转让、算力"(7字), "实控人变更、金融概念"(10字)
            if 2 <= len(b) <= 30:
                out[r[0]] = b
        conn.close()
    except Exception as e:
        log.warning("9_25快照KPL board 查询失败(降级) err=%s", e)
    return out


def fetch_board_map():
    """全市场个股概念 map: {code: "概念1、概念2"}
    开盘啦概念优先: 多接口合并(连板梯队 + 竞价封板 + 竞价爆量) 覆盖一字板/连板/封板/爆量
    9_25 快照 board 兜底(已含采集时的开盘啦覆盖)。
    返回 {code: board}; 覆盖不到的概念为空(前端显示东财 f103)"""
    def loader():
        out = {}
        # 1) 连板梯队(覆盖一字板/连板/封板股, 主力: 用户截图 9 只里有 8 只涨幅 200%+ 一字板在这里)
        try:
            for pid in (1, 2, 3, 4, 5):
                for s in (fetch_ladder(pid) or []):
                    b = s.get("concept") or ""
                    if b and s.get("code") not in out:
                        out[s["code"]] = b
        except Exception:
            pass
        # 2) 竞价涨停委买额(竞价时段刚封板股, Type=4 榜 - 早晨刚封涨停)
        try:
            for s in (fetch_bid_seal() or []):
                b = s.get("board") or ""
                if b and s["code"] not in out:
                    out[s["code"]] = b
        except Exception:
            pass
        # 3) 竞价爆量榜(竞价量异动非涨停股, 开盘啦也带 board)
        try:
            for s in (fetch_bid_boom() or []):
                b = s.get("board") or ""
                if b and s["code"] not in out:
                    out[s["code"]] = b
        except Exception:
            pass
        # 4) 热点解读强势股(doc39) - plates 是开盘啦风格的板块拼接
        try:
            for s in (fetch_hot_stocks() or []):
                p = s.get("plates") or ""
                if p and s["code"] not in out:
                    out[s["code"]] = p
        except Exception:
            pass
        # 4) 全天兜底: 当日 9_25 快照中已被开盘啦覆盖的 board (短字符串启发式, 避免短线侠污染)
        try:
            for code, b in _snap25_kpl_map().items():
                if code not in out:
                    out[code] = b
        except Exception:
            pass
        return out
    # 2026-08-18 性能优化: 30s → 300s — 概念归属日内稳定, 避免竞价异动页 10+ tab 每 tab 重建(冷 778ms)
    return _cached("board_map", 300, loader)


def apply_board_concept(result, log_tag="", deep=True, field="concept",
                        truncate=None, blank_if_missing=False):
    """用开盘啦概念覆盖选股/历史回看结果指定字段(2 层覆盖)
    1) 榜单合并(ladder+bid_seal+bid_boom+hot_stocks+snap25) - 快速覆盖热点/板块
    2) 按股查询(doc94 fetch_stock_plate) - 百分百覆盖, 1 天缓存限制频次
    field: 写入的目标字段, 默认 "concept"(选股/历史回看使用);
           竞价异动各 tab 传 "board", 把概念统一覆盖到 board 字段, 保证概念均来自开盘啦
    result: [{code, ...}, ...], 原地修改 field 字段; 返回覆盖数
    deep=False: 只做榜单合并层(历史回看/大列表用, 避免海量按股查询拖慢接口)
    truncate: 概念最多保留前 N 个(按 '、' 分档); None/0=不截断
    blank_if_missing: True 时, 开盘啦完全未覆盖到的股票, 把原 field(东财)清空,
                     保证概念只看开盘啦; False 则保留原值兜底"""
    if not result:
        return 0

    def _trunc(s):
        if not s:
            return s
        if truncate and truncate > 0:
            parts = [p for p in str(s).split("、") if p]
            return "、".join(parts[:truncate])
        return s

    # code -> [item,...] 索引(便于第二层精确覆盖, 避免二次遍历 result)
    by_code = {}
    for it in result:
        c = str(it.get("code"))
        by_code.setdefault(c, []).append(it)
    covered = set()  # 已被开盘啦覆盖的 code

    # 第一层: 榜单合并（全市场一次接口）
    board_map = {}
    n = 0
    try:
        board_map = fetch_board_map() or {}
        for it in result:
            code = str(it.get("code"))
            b = board_map.get(code)
            if b:
                it[field] = _trunc(b)
                covered.add(code)
                n += 1
        if n:
            log.info("选股概念开盘啦覆盖[榜单] %s 覆盖%d只/共%d只", log_tag, n, len(result))
    except Exception as e:
        log.warning("选股概念开盘啦覆盖[榜单]失败 %s err=%s", log_tag, e)

    # 第二层: 按股查询 GetStockIDPlate (开盘啦真实概念, 用户要求所有表格概念以开盘啦为准)
    # 2026-08-21 修复: 此前只对"第一层未覆盖"的股票查开盘啦, 但第一层 board_map 混合了
    # 东财板块/上榜标签(如竞价爆量表的 "昨日炸板、昨日触板" 状态词, 见 001225),
    # 导致这些污染值被当成"已覆盖"跳过开盘啦查询 → 概念来源错误。
    # 现在 deep=True 时对全部股票都走开盘啦 doc94 按股查询, 保证概念统一来自开盘啦前 N 个。
    if not deep:
        if blank_if_missing:
            for it in result:
                if str(it.get("code")) not in covered:
                    it[field] = ""
        return n
    miss_codes = [str(it.get("code")) for it in result if it.get("code")]
    if not miss_codes:
        if blank_if_missing:
            for it in result:
                if str(it.get("code")) not in covered:
                    it[field] = ""
        return n
    # 2026-08-18 性能优化: 概念 deep 结果跨 tab 共享 —
    # 竞价异动页 10+ tab 首次加载都走 deep 按股查询(冷缓存 26只=1.6s), 叠加后接口 2-3s
    # 共享池 kpl:concept_deep (TTL 1h): 任一 tab 查过的股票, 后续 tab 直接命中, 秒回
    n2 = 0
    try:
        pool = store.get("kpl:concept_deep") or {}
        # 1) 池内命中(其他 tab 已查过)
        pool_hit = [c for c in miss_codes if c in pool]
        for code in pool_hit:
            p = pool.get(code)
            if p:
                for it in by_code.get(code, []):
                    it[field] = _trunc(p)
                n2 += 1
                covered.add(code)
        miss_codes = [c for c in miss_codes if c not in pool]
        # 2) 未命中 → 按股查询, 结果写回共享池
        if miss_codes:
            import concurrent.futures
            # 并发受限流信号量(_SEM=3)保护, 分批执行避免一次开太多线程
            BATCH = 20
            new_pool = {}
            for i in range(0, len(miss_codes), BATCH):
                chunk = miss_codes[i:i + BATCH]
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                    plates = list(ex.map(fetch_stock_plate, chunk))
                for code, plate in zip(chunk, plates):
                    if plate:
                        for it in by_code.get(code, []):
                            it[field] = _trunc(plate)
                        n2 += 1
                        covered.add(code)
                        new_pool[code] = plate
            if new_pool:
                pool.update(new_pool)
                store.set("kpl:concept_deep", pool, 3600)   # 1h 共享, 概念归属日内稳定
        if n2:
            log.info("选股概念开盘啦覆盖[按股] %s 补%d只/共%d只(池命中%d)", log_tag, n2, len(result), len(pool_hit))
    except Exception as e:
        log.warning("选股概念开盘啦覆盖[按股]失败 %s err=%s", log_tag, e)
    # 未覆盖到的(开盘啦无概念): 按 blank_if_missing 决定是否清空原东财值
    if blank_if_missing:
        for it in result:
            if str(it.get("code")) not in covered:
                it[field] = ""
    return n + n2


def apply_board_concept_db(result, log_tag="", field="board", truncate=2,
                           blank_if_missing=True, date=None):
    """2026-08-21 : 从库内当日已落库的概念覆盖 result 的 field 列, 不再实时逐股查开盘啦
    =====================================================================
    背景: 概念由 concept_refresh 每半小时从开盘啦 doc94 定时回写库
    (auction_daily_history 各 tab / qc_snapshot / lhb_history 的 board 字段),
    前端竞价接口直接读库即可, 避免每次请求实时打开盘啦。
    result: [{code, ...}, ...], 原地修改 field 字段; 返回覆盖数
    field:  目标字段(竞价各 tab 用 "board")
    truncate: 概念最多保留前 N 个(按 '、' 分档); None/0=不截断
    blank_if_missing: True 时, 库内无该股概念 → 清空原值(概念只看落库的开盘啦);
                       False 则保留原值兜底
    未命中库(如早盘竞价还没落库)时: 有原值则 truncate 后保留, 避免把已有概念清空"""
    if not result:
        return 0
    codes = [str(it.get("code")) for it in result if it.get("code")]
    if not codes:
        return 0
    board_map = _load_board_map_db(codes, date)
    n = 0
    for it in result:
        c = str(it.get("code"))
        b = board_map.get(c)
        if b:
            it[field] = b
            n += 1
        elif blank_if_missing:
            # 库内确实无该股概念: 若字段带东财污染, 清空保证只看开盘啦;
            # 若无概念原本就是空则不动
            it[field] = ""
    if n:
        log.info("竞价概念读库覆盖 %s 覆盖%d只/共%d只", log_tag, n, len(result))
    return n


def _load_board_map_db(codes, date=None):
    """从当日竞价落库表读取 code -> board 映射(概念均来自开盘啦, concept_refresh 定时回写)
    读取顺序(命中即用): auction_daily_history 各 tab → qc_snapshot → lhb_history
    date: None=今日; 指定 'YYYY-MM-DD' 读历史(供回看接口)
    返回 {code: board}"""
    import sqlite3
    from ..core import config as _cfg
    if date is None:
        g = time.gmtime(time.time() + 8 * 3600)
        date = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    code_set = {str(c) for c in codes}
    if not code_set:
        return {}
    board_map = {}
    try:
        conn = sqlite3.connect(_cfg.DB_FILE)
        # 0) 优先独立概念映射表 stock_concept:
        # concept_refresh 每30分钟从**所有实时接口**采集概念全量写本表,
        # 能覆盖盘中新增股票(如竞价爆量实时407只, 而落库9:26仅97只)。
        try:
            ph = ",".join("?" * len(code_set))
            rows = conn.execute(
                f"SELECT code, board FROM stock_concept WHERE date=? AND code IN ({ph})",
                (date, *code_set)).fetchall()
            for c, b in rows:
                c = str(c).strip()
                if b:
                    board_map[c] = b
        except Exception as e:
            log.warning("读库概念[stock_concept]失败 date=%s err=%s", date, e)
        # 1) 竞价异动各 tab
        tabs = ("seal", "boom", "bid_net", "qiangcang", "yest_zt", "yest_broken",
                "broken_yest", "broken_today")
        for tab in tabs:
            try:
                row = conn.execute(
                    "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
                    (date, tab)).fetchone()
            except Exception:
                continue
            if not row or not row[0]:
                continue
            try:
                for it in json.loads(row[0]):
                    c = str(it.get("code", "")).strip()
                    b = it.get("board")
                    if c in code_set and b and c not in board_map:
                        board_map[c] = b
            except Exception:
                pass
        # 2) 竞价抢筹快照
        try:
            rows = conn.execute(
                "SELECT code, board FROM qc_snapshot WHERE date=?", (date,)).fetchall()
            for c, b in rows:
                c = str(c).strip()
                if b and c not in board_map:
                    board_map[c] = b
        except Exception:
            pass
        # 3) 龙虎榜
        try:
            row = conn.execute("SELECT list FROM lhb_history WHERE date=?",
                               (date,)).fetchone()
            if row and row[0]:
                for it in json.loads(row[0]):
                    c = str(it.get("code", "")).strip()
                    b = it.get("board")
                    if c in code_set and b and c not in board_map:
                        board_map[c] = b
        except Exception:
            pass
        conn.close()
    except Exception as e:
        log.warning("读库概念映射失败 date=%s err=%s", date, e)
    return board_map


def fetch_yest_zt():
    """昨日涨停股今日竞价表现: **flash limit_up_pool&date=昨日** (2026-08-18 主人确认:
    开盘啦 doc19/801900 的 Date 是"指数交易日"语义 — 传 8/17 返回的是 8/17 的"昨日"(8/14)涨停股,
    而当日(8/18)数据未冻结返回空 → 盘后拿不到正确的"昨日涨停"; flash 法日期直接对)
    字段补全: Type4(今日竞价涨停榜)优先 → snapshot_bid 9_25(全市场)兜底
    返回 [{code,name,yestChange,limitUpDays,stillLimit,change,bidChange,bidNetAmt,bidAmt,
           bidTurnover,floatMv,board}, ...]"""
    def loader():
        day = _prev_trade_day()
        if not day:
            return []
        # 主路: flash 昨日涨停池(日期语义直接正确: date=8/17 = 昨日涨停110只)
        lst = _flash_pool("limit_up_pool", day)
        if not lst:
            return []
        today_codes = {x["code"] for x in _flash_pool("limit_up_pool")}
        seal_map = _seal_map()
        snap25 = _snap25_map()
        out = []
        for it in lst:
            code = it["code"]
            s = seal_map.get(code, {})
            sn = snap25.get(code, {})
            bid_amt = s.get("bidAmt") or (sn["bid_amt"] * 10000 if sn and sn.get("bid_amt") else None)
            float_mv = s.get("floatMv") or (sn.get("free_mv") or sn.get("float_mv") if sn else None)
            # 竞价换手: Type4 真值优先, 无则 竞价额/自由流通市值 近似(与短线侠 0.1-0.4% 量级一致)
            # 精度4位: 大盘小额股(如58万/344亿≈0.0017%)不再被round到0
            bid_turnover = s.get("bidTurnover")
            if not bid_turnover and bid_amt and float_mv:
                bid_turnover = round(bid_amt / float_mv * 100, 4)
            out.append({
                "code": code,
                "name": it["name"],
                "yestChange": it["change"],              # 昨日涨停涨幅
                "limitUpDays": it["limitUpDays"],        # 昨日连板数
                "stillLimit": code in today_codes,       # 今日是否仍涨停(连板)
                "reason": it.get("reason", ""),          # 昨日涨停原因
                # 今日实时涨幅: Type4 实时涨幅优先, 无则 9_25 竞价涨幅
                "change": s.get("realChange") if s.get("realChange") is not None
                          else (sn.get("bid_change") if sn else None),
                "bidChange": s.get("bidChange") if s.get("bidChange") is not None
                             else (sn.get("bid_change") if sn else None),
                "bidNetAmt": s.get("bidNetAmt"),         # 竞价承接(净额,元) Type4 专有
                "bidAmt": bid_amt,
                "bidTurnover": bid_turnover,
                "floatMv": float_mv,
                "board": s.get("board") or sn.get("board") or "",   # 概念: Type4 → 9_25快照(f103/f100)
            })
        return out
    return _cached("yest_zt", 60 * 5, loader)


def fetch_yest_broken():
    """昨断板(2026-08-18 主人定义): **前一日连板(涨停≥2板)且昨日未涨停 = 昨日连板中断**
    (doc21/801902 是"破板"语义≠断板, 主人反馈作废; 恢复 flash 计算法)
    返回断板股票的今日竞价表现(从 snapshot_bid 9:25 全市场补)
    返回 [{code,name,yestChange,limitUpDays,change,bidChange,bidAmt,bidNetAmt,bidTurnover,floatMv,board}, ...]"""
    def loader():
        day = _prev_trade_day()              # 昨日(断板发生的日子)
        if not day:
            return []
        # 昨日的前一交易日
        prev2 = None
        try:
            import sqlite3
            conn = sqlite3.connect(config.DB_FILE)
            row = conn.execute(
                "SELECT DISTINCT date FROM snapshot_bid WHERE date < ? ORDER BY date DESC LIMIT 1",
                (day,)).fetchone()
            conn.close()
            if row:
                prev2 = str(row[0])
        except Exception:
            pass
        if not prev2:
            log.warning("昨断板 无法定位前一日(day=%s), 返回空", day)
            return []
        prev2_pool = _flash_pool("limit_up_pool", prev2)   # 前一日涨停池
        if not prev2_pool:
            return []
        yest_codes = {x["code"] for x in _flash_pool("limit_up_pool", day)}
        # 前一日连板≥2 + 昨日未涨停 = 昨日断板(连板中断)
        broken = [x for x in prev2_pool
                  if x["code"] not in yest_codes and (x.get("limitUpDays") or 0) >= 2]
        log.info("昨断板 前一日(%s)涨停=%d 昨日(%s)未涨停且≥2板=%d只",
                 prev2, len(prev2_pool), day, len(broken))
        # 今日竞价快照(9_25 全市场)补: 涨幅/竞额/概念
        snap = _snap25_map()
        yest_snap = _snap25_map(day)   # 昨日(断板日)快照 → 断板日竞价涨幅
        seal_map = _seal_map()
        out = []
        for it in broken:
            code = it["code"]
            s = snap.get(code, {})
            ys = yest_snap.get(code, {})
            t4 = seal_map.get(code, {})
            bid_amt = (s["bid_amt"] * 10000) if s and s.get("bid_amt") else None
            float_mv = t4.get("floatMv") or (s.get("free_mv") or s.get("float_mv") if s else None)
            # 竞价换手: Type4 真值优先, 无则 竞价额/自由流通市值 近似
            # 精度4位: 大盘小额股不再被round到0
            bid_turnover = t4.get("bidTurnover")
            if not bid_turnover and bid_amt and float_mv:
                bid_turnover = round(bid_amt / float_mv * 100, 4)
            out.append({
                "code": code,
                "name": t4.get("name") or s.get("name") or it["name"],
                "yestChange": ys.get("bid_change") if ys else None,  # 昨日(断板日)竞价涨幅 — 2026-08-18 语义修正
                "limitUpDays": it["limitUpDays"],        # 断板前连板数
                "reason": it.get("reason", ""),          # 前一日涨停原因
                "change": t4.get("realChange") if t4.get("realChange") is not None
                          else (s.get("bid_change") if s else None),   # 今日实时涨幅(9_25竞价涨幅兜底)
                "bidChange": (s.get("bid_change") if s else None),     # 今日竞价涨幅
                "bidAmt": bid_amt,
                "bidNetAmt": t4.get("bidNetAmt"),
                "bidTurnover": bid_turnover,
                "floatMv": float_mv,
                "board": t4.get("board") or s.get("board") or "",   # 概念: Type4 → 9_25快照(f103/f100)
            })
        return out
    return _cached("yest_broken", 60 * 5, loader)


def fill_reason_from_pool(lst, date=None):
    """按 date(空=今日) 的东财涨停池给列表补涨停原因(reason); 已带 reason 的不覆盖
    用于历史回看快照/龙虎榜等无 reason 字段的数据源"""
    if not lst:
        return lst
    try:
        pool = _flash_pool("limit_up_pool", date)
        if not pool:
            return lst
        m = {x["code"]: (x.get("reason") or "") for x in pool}
        for it in lst:
            code = str(it.get("code") or "")
            if code and not it.get("reason") and code in m:
                it["reason"] = m[code]
    except Exception as e:
        log.warning("涨停原因补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_change_from_snap(lst, date=None, override=False):
    """用 date(空=今日) 的 9_25 全市场快照(snapshot_bid)给列表补竞价涨幅(bidChange);
    override=False(默认) 仅补 None; override=True 强制用自采快照覆盖。
    (2026-08-24) 开盘啦 Type4 接口 bidChange(row[5]) 经核对 146 只中 124 只与
    snapshot_bid 9_25 竞价涨幅不一致(养元 list=9.99 快照=3.71 等), 竞价委买等表
    以自采快照为准, 开盘啦值仅作无快照时的兜底。"""
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        for it in lst:
            code = str(it.get("code") or "")
            if not code or code not in snap:
                continue
            if not override and it.get("bidChange") is not None:
                continue
            bc = snap[code].get("bid_change")
            if bc is not None:
                it["bidChange"] = bc
    except Exception as e:
        log.warning("竞价涨幅补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_turnover_from_snap(lst, date=None):
    """2026-08-18 主人要求: 竞价异动全部 tab 加竞价换手。
    用 date(空=今日) 9_25 快照给列表补竞价换手(bidTurnover = 竞价成交额/自由流通市值×100,
    与开盘啦 bidTurnover 口径一致); 已带的不覆盖。

    2026-08-23 修复: 老版 fetch_bid_boom(开盘啦 Type10 解析)落库时把华泰等大盘股 floatMv
    错位为极小值(如华泰=27元), 导致 bidTurnover 算出千万级荒谬百分比。此处对已带值也做
    校验: 若 float_mv 异常过小(<1e7 元, 即<1000万, A股最小流通市值也不至于此) → 视为损坏,
    用当日快照的 float_mv 覆盖并重算 bidTurnover。"""
    import math as _math
    MIN_FMV = 1e7   # 元; float_mv 低于该值(不足1000万流通市值)判定为字段错位损坏
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        n = 0
        n_repair = 0
        for it in lst:
            code = str(it.get("code") or "")
            if not code:
                continue
            s = snap.get(code)
            if not s or not s.get("float_mv"):
                continue
            # 单位: snapshot_bid.bid_amt 万元, float_mv 元 → bid_amt×10000 转元
            # 精度4位: 大盘小额股(如58万/344亿≈0.0017%)不再被round到0
            _bt_raw = (s.get("bid_amt") or 0) * 10000 / s["float_mv"] * 100
            if _bt_raw <= 0:
                continue
            bt = round(_bt_raw, 4)
            cur_fmv = it.get("floatMv") or 0
            # 竞换缺失(空/0) → 必须用快照补(不因 float_mv 正常而跳过)
            # (2026-08-24 修复: 此前 float_mv 正常(>=MIN_FMV)时直接 continue,
            #  导致 seal/boom 等开盘啦接口项 bidTurnover 恒为0 而无法补填)
            if not it.get("bidTurnover"):
                it["floatMv"] = s["float_mv"]
                it["bidTurnover"] = bt
                n += 1
                continue
            # float_mv 异常过小(<1000万) 字段错位 → 修复并重算
            if cur_fmv and cur_fmv < MIN_FMV:
                it["floatMv"] = s["float_mv"]
                it["bidTurnover"] = bt
                n_repair += 1
            elif _math.isfinite(it["bidTurnover"]) and it["bidTurnover"] > 100:
                it["floatMv"] = s["float_mv"]
                it["bidTurnover"] = bt
                n_repair += 1
        if n_repair:
            log.warning("竞价换手/流通市值修复异常 %d 只 date=%s(字段错位大盘股)", n_repair, date or "-")
        if n:
            log.info("竞价换手补齐 %d 只 date=%s", n, date or "-")
    except Exception as e:
        log.warning("竞价换手补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_amt_from_snap(lst, date=None):
    """2026-08-18 主人要求: doc112(竞价>1000万)等接口无竞价成交额字段 →
    用 9_25 快照补竞价成交额(bidAmt 元; 快照 bid_amt 万元×10000); 已带的不覆盖"""
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        n = 0
        for it in lst:
            code = str(it.get("code") or "")
            if not code or it.get("bidAmt") not in (None, "", 0):
                continue
            s = snap.get(code)
            if s and s.get("bid_amt"):
                it["bidAmt"] = (s.get("bid_amt") or 0) * 10000   # 万元 → 元
                n += 1
        if n:
            log.info("竞价成交额补齐 %d 只 date=%s", n, date or "-")
    except Exception as e:
        log.warning("竞价成交额补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_ratio_yest(lst, date=None):
    """2026-08-18 主人要求(竞价爆量): 补昨日竞价成交额(yestBidAmt 元) + 竞价量比
    (bidRatioYest = 今日竞价成交额/昨日竞价成交额); 昨日 = 最近(严格小于今日)交易日 9_25 快照"""
    if not lst:
        return lst
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        today = date or time.strftime("%Y-%m-%d")
        row = conn.execute("SELECT MAX(date) FROM snapshot_bid WHERE date <= ?", (today,)).fetchone()
        cur = str(row[0]) if row and row[0] else today
        row2 = conn.execute("SELECT MAX(date) FROM snapshot_bid WHERE date < ?", (cur,)).fetchone()
        yest = str(row2[0]) if row2 and row2[0] else None
        if not yest:
            conn.close()
            return lst
        ymap = {}
        for code, amt in conn.execute(
                "SELECT code, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_25'", (yest,)):
            ymap[code] = amt
        conn.close()
        n = 0
        for it in lst:
            code = str(it.get("code") or "")
            ya = ymap.get(code)
            if ya is None:
                continue
            ya_yuan = ya * 10000                       # 万元 → 元
            it["yestBidAmt"] = ya_yuan
            ta = it.get("bidAmt") or 0
            if ta > 0 and ya_yuan > 0:
                it["bidRatioYest"] = round(ta / ya_yuan, 2)
                n += 1
        if n:
            log.info("竞价量比补齐 %d 只 (昨日=%s)", n, yest)
    except Exception as e:
        log.warning("竞价量比补齐失败 date=%s err=%s", date or "-", e)
    return lst


def _save_qc_snapshot(date, items):
    """竞价时段抢筹结果持久化(qc_snapshot 表), 非竞价时段读库展示"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        conn.executemany(
            "INSERT OR REPLACE INTO qc_snapshot (date, code, name, real_change, bid_amt, qc_delta, "
            "bid_turnover, bid_change, float_mv, board, bid_ratio, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(date, x["code"], x.get("name", ""), x.get("realChange", 0), x.get("bidAmt", 0),
              x.get("qcDelta", 0), x.get("bidTurnover", 0), x.get("bidChange", 0),
              x.get("floatMv", 0), x.get("board", ""), x.get("bidRatio", 0) or 0, int(time.time()))
             for x in items])
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("抢筹结果落库失败 err=%s", e)


def _load_qc_snapshot(date):
    """读取某日竞价抢筹快照(按 qc_delta 降序)"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        rows = conn.execute(
            "SELECT code, name, real_change, bid_amt, qc_delta, bid_turnover, bid_change, float_mv, board, bid_ratio "
            "FROM qc_snapshot WHERE date=? ORDER BY qc_delta DESC", (date,)).fetchall()
        conn.close()
    except Exception:
        return []
    return [{
        "code": r[0], "name": r[1], "realChange": r[2], "bidAmt": r[3], "qcDelta": r[4],
        "bidTurnover": r[5], "bidChange": r[6], "floatMv": r[7], "board": r[8] or "",
        "bidRatio": r[9],
    } for r in rows]


LASTSEC_DIFF_THRESHOLD = 0.5   # 最后一秒"明显抢筹"差值阈值(%), 可调


def _calc_lastsec_qc(chg25, seq):
    """最后一秒抢筹(差值回退, 对抗接口延迟):
    seq = [(ts, bid_change, bid_amt), ...] 按 ts 升序(9:24:45-9:25:03 每秒采样)
    规则:
      ① 优先 9_25涨幅 − 最新一秒涨幅: |差|≥阈值 → 视为最后一秒抢筹
      ② 差太小(接口延迟导致最新秒已含变化, 或该秒无变化) → 向前回退:
         最新秒 − 倒数第二秒, 依此类推, 取第一个 |差|≥阈值的相邻对
      ③ 全部差值都小 → 返回差值最大的对(或 None 表示无抢筹)
    返回 (qcDeltaLast, base_ts); 无可用序列返回 (None, None)"""
    if not seq:
        return None, None
    points = sorted(seq, key=lambda x: x[0])          # 升序
    pairs = []
    cur_chg = chg25                                    # 9_25 视为最新锚点
    cur_ts = None
    for ts, chg, _amt in reversed(points):             # 从最新一秒往前
        pairs.append((round(cur_chg - chg, 2), cur_ts, ts))
        cur_chg, cur_ts = chg, ts
    # ① 找第一个 |差| ≥ 阈值的相邻对
    for diff, ts_a, ts_b in pairs:
        if abs(diff) >= LASTSEC_DIFF_THRESHOLD:
            return diff, ts_b or ts_a
    # ② 全部小 → 取差值最大的一对(仍可能有参考意义)
    if pairs:
        best = max(pairs, key=lambda x: abs(x[0]))
        return best[0], best[2] or best[1]
    return None, None


def fetch_bid_qiangcang(date=None):
    """竞价抢筹(左右双表, 对标短线侠):
    左表 list20  = 开盘啦 MorningBiddingList Type=4 全市场竞价异动(200只)
                   抢筹强度 qcDelta = 竞价净额 / 自由流通市值 * 100 (开盘啦自带"抢筹资金"指标)
                   过滤: 自由流通市值≥2亿, 抢筹强度>5%
                   竞价时段(9:15-9:30)实时拉取并持久化 qc_snapshot 表;
                   非竞价时段接口为空 → 读库展示今天已选出的结果(不丢失)
    右表 listLast= snapshot_bid 9_24(最后一秒≈9:24:4x) → 9_25 段: 抢筹幅度 = 9:25涨幅 − 9:24涨幅
    date: 空=今天; 指定 'YYYY-MM-DD' 回看历史(qc_snapshot + snapshot_bid 历史数据)
    返回 {"list20": [...], "listLast": [...]}"""
    def loader():
        t0 = time.time()
        today = date or time.strftime("%Y-%m-%d")
        hhmm = time.strftime("%H:%M")
        g = time.gmtime(time.time() + 8 * 3600)
        hm = g.tm_hour * 60 + g.tm_min
        # 竞价时段 9:15-9:30 (工作日); 注意: 非竞价时段开盘啦接口也可能返回
        # 200只"僵尸数据"(bidNetAmt=0), 必须按时间窗强制走读库, 否则 9:30 后今天结果会丢
        in_bid = (not date) and g.tm_wday < 5 and (9 * 60 + 15) <= hm <= (9 * 60 + 30)
        # 实时模式且非竞价时段: 若今天还没有竞价快照(盘前/周末/节假日), 自动回退到最近
        # 有数据的交易日, 与 bid-seal/bid-boom 等 tab 盘后仍显示最近交易日保持一致
        if not date and not in_bid:
            try:
                import sqlite3
                conn = sqlite3.connect(config.DB_FILE)
                has_today = conn.execute(
                    "SELECT COUNT(*) FROM snapshot_bid WHERE date=?", (today,)).fetchone()[0]
                if not has_today:
                    row = conn.execute(
                        "SELECT MAX(date) FROM snapshot_bid WHERE date <= ?", (today,)).fetchone()
                    if row and row[0]:
                        log.info("抢筹[回退] date=%s %s 今日无快照, 自动回退最近交易日 %s",
                                 today, hhmm, row[0])
                        today = str(row[0])
                conn.close()
            except Exception as e:
                log.warning("抢筹 交易日回退判断失败(按今天处理) err=%s", e)
        list20 = []
        if in_bid:
            # ===== 竞价时段: 实时拉取 + 落库 =====
            try:
                seal_list = fetch_bid_seal() or []
            except Exception as e:
                log.warning("抢筹 Type4 拉取失败 err=%s", e)
                seal_list = []
            if seal_list:
                log.info("抢筹[live] date=%s %s Type4返回%d只", today, hhmm, len(seal_list))
                for s in seal_list:
                    try:
                        code = str(s.get("code", ""))
                        if not code:
                            continue
                        bidNetAmt = float(s.get("bidNetAmt") or 0)      # 竞价净额(元) - 开盘啦 row[6]
                        floatMv = float(s.get("floatMv") or 0)          # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                        bidAmt = float(s.get("bidAmt") or 0)            # 竞价成交额(元) - 开盘啦 row[8]
                        if floatMv < 2e8 or bidNetAmt <= 0:             # 放宽阈值 5亿→2亿, 纳入中盘股
                            continue
                        qcDelta = round(bidNetAmt / floatMv * 100, 2)   # 抢筹强度%(开盘啦自家口径)
                        # 2026-08-18 修复: 阈值 5% 过高 — 实测强抢筹票 qcDelta 仅 0.4~3%
                        # (盈新发展0.77/日丰0.45), 5% 导致从上线起全部过滤, qc_snapshot 整表为空
                        if qcDelta <= 0.5:
                            continue
                        list20.append({
                            "code": code,
                            "name": str(s.get("name", "")),
                            "realChange": float(s.get("realChange") or 0) if s.get("realChange") is not None else None,
                            "bidAmt": bidAmt,
                            "qcDelta": qcDelta,
                            "bidTurnover": float(s.get("bidTurnover") or 0),
                            "bidChange": float(s.get("bidChange") or 0),
                            "floatMv": floatMv,
                            "board": str(s.get("board") or ""),
                        })
                    except (ValueError, TypeError):
                        continue
                list20.sort(key=lambda x: x["qcDelta"], reverse=True)
                if list20:
                    _save_qc_snapshot(today, list20)   # 竞价时段持久化, 供非竞价时段展示
                    log.info("抢筹[live] date=%s %s 过滤后list20=%d只 已落库qc_snapshot",
                             today, hhmm, len(list20))
                else:
                    log.warning("抢筹[live] date=%s %s Type4返回%d只但过滤后0只"
                                "(可能: 全部 qcDelta<=5 或 自由流通市值<2亿 或 bidNetAmt=0, 需检查阈值口径)",
                                today, hhmm, len(seal_list))
            else:
                log.warning("抢筹[live→空] date=%s %s 竞价时段内Type4返回空!"
                            "(可能 Token失效/接口限流/服务未起/非交易日)", today, hhmm)
        else:
            # ===== 非竞价时段: 忽略接口僵尸数据, 直接读库展示今天已选结果 =====
            try:
                list20 = _load_qc_snapshot(today)
            except Exception as e:
                log.warning("抢筹结果读库失败 err=%s", e)
                list20 = []
            log.info("抢筹[saved] date=%s %s 非竞价时段读库 list20=%d只(忽略Type4僵尸数据)",
                     today, hhmm, len(list20))

        # 涨幅抢筹(全市场5549只, 短线侠真实口径): qcDeltaChg = 9_25竞价涨幅 − 9_20竞价涨幅
        # 数据源 snapshot_bid 9_20/9_25(库内历史), 非竞价时段也能计算 → 全天可回看
        list20Chg = []
        try:
            import sqlite3
            conn = sqlite3.connect(config.DB_FILE)
            rows20c = conn.execute(
                "SELECT code, bid_change FROM snapshot_bid WHERE date=? AND time_point='9_20'",
                (today,)).fetchall()
            rows25c = conn.execute(
                "SELECT code, bid_change, bid_amt, COALESCE(NULLIF(free_mv,0), float_mv), name, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (today,)).fetchall()
            conn.close()
            m20c = {r[0]: r[1] for r in rows20c}
            seal_map = {} if date else _seal_map()   # 历史日期不拉今天 Type4(字段用快照自身)
            for code, chg25, amt25, fmv, name, board in rows25c:
                # 过滤: 自由流通市值≥2亿, 竞价额>0, 竞价成交额≥500万, 竞价涨幅≥5%(9_25涨幅)
                # 2026-08-18 主人要求: 竞价涨幅低于5%的去掉(原门槛 2% 提至 5%)
                if fmv < 2e8 or amt25 <= 0 or amt25 < 500 or chg25 < 5:
                    continue
                chg20 = m20c.get(code)
                if chg20 is None:
                    continue
                qcChg = round(chg25 - chg20, 2)          # 涨幅抢筹(9:20→9:25 涨幅差)
                if qcChg <= 5:
                    continue
                t4 = seal_map.get(code, {})
                bid_amt = amt25 * 10000
                bid_turnover = t4.get("bidTurnover")
                if not bid_turnover and fmv:
                    bid_turnover = round(bid_amt / fmv * 100, 2)
                # realChange: 只取开盘啦盘中实时(9:30后才持续更新), 无值不退回 9_25 竞价涨幅, 前端显示 "-"
                list20Chg.append({
                    "code": code,
                    "name": name or t4.get("name", ""),
                    "realChange": t4.get("realChange"),
                    "bidAmt": bid_amt,
                    "qcDeltaChg": qcChg,
                    "bidChange20": chg20,
                    "bidTurnover": bid_turnover,
                    "bidChange": chg25,
                    "floatMv": fmv,
                    "board": t4.get("board") or board or "",
                })
            list20Chg.sort(key=lambda x: x["qcDeltaChg"], reverse=True)
            log.info("抢筹[涨幅] date=%s %s 9_20=%d条 9_25=%d条 全市场涨幅抢筹=%d只",
                     today, hhmm, len(rows20c), len(rows25c), len(list20Chg))
        except Exception as e:
            log.warning("抢筹涨幅列表计算失败 err=%s", e)
            list20Chg = []

        # 右表"最后一秒": 优先 snapshot_lastsec 秒级序列(差值回退对抗接口延迟),
        # 无秒级数据时回退 9_24 时点(9:24:3x~4x 重采型)
        listLast = []
        try:
            import sqlite3
            conn = sqlite3.connect(config.DB_FILE)
            # 秒级序列: code -> [(ts, bid_change, bid_amt), ...] 升序
            rows_ls = conn.execute(
                "SELECT code, bid_change, bid_amt, ts FROM snapshot_lastsec WHERE date=? ORDER BY ts",
                (today,)).fetchall()
            rows24 = conn.execute(
                "SELECT code, bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_24'",
                (today,)).fetchall()
            rows25 = conn.execute(
                "SELECT code, bid_change, bid_amt, COALESCE(NULLIF(free_mv,0), float_mv), name FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (today,)).fetchall()
            conn.close()
            seq = {}
            for code, chg, amt, ts in rows_ls:
                seq.setdefault(code, []).append((ts, chg, amt))
            if not rows_ls:
                log.warning("抢筹[listLast] date=%s %s snapshot_lastsec=0条(9:24:45-9:25:03高频采样缺失!), "
                            "右表将回退 9_24 时点", today, hhmm)
            if not rows24:
                log.warning("抢筹[listLast] date=%s %s 9_24时点快照=0条(snapshot_bid采集缺失!), "
                            "右表兜底数据为空", today, hhmm)
            if not rows25:
                log.warning("抢筹[listLast] date=%s %s 9_25时点快照=0条, 右表将为空", today, hhmm)
            if rows25:
                seal_map = {} if date else _seal_map()   # 历史日期不拉今天 Type4
                m24 = {r[0]: (r[1], r[2]) for r in rows24}
                used_lastsec = 0
                for code, chg, amt25, fmv, name in rows25:
                    # 最后一秒抢筹过滤链: 自由流通市值≥5亿 + 竞价金额>500万 (2026-08-19 主人要求 1000万→500万)
                    if fmv <= 0 or amt25 <= 0 or amt25 < 500 or fmv < 5e8:
                        continue
                    t4 = seal_map.get(code, {})
                    # 竞换兜底: 开盘啦实时未覆盖(历史回看/非涨停)时, 用 9_25 快照计算
                    # bidTurnover = 竞价成交额(元)/自由流通市值(元)×100 (与 list20Chg 口径一致)
                    bid_turnover = t4.get("bidTurnover")
                    if not bid_turnover and fmv:
                        bid_turnover = round(amt25 * 10000 / fmv * 100, 2)
                    base = {
                        "code": code,
                        "name": name or t4.get("name", ""),
                        # realChange: 只取开盘啦盘中实时, 无值不退回 9_25 竞价涨幅, 前端显示 "-"
                        "realChange": t4.get("realChange"),
                        "bidAmt": amt25 * 10000,
                        "bidChange": chg,
                        "bidTurnover": bid_turnover,
                        "floatMv": fmv,
                        "board": t4.get("board", ""),
                    }
                    # ① 秒级序列差值回退(优先): 9_25 − 最新秒; 差值小则向前回退找大差值
                    s = seq.get(code)
                    if s and len(s) >= 2:
                        qc, base_ts = _calc_lastsec_qc(chg, s)
                        if qc is not None:
                            base["bidChange24"] = None   # 秒级无 9_24 语义, 标记为秒级口径
                            base["lastsecTs"] = base_ts
                            base["qcDeltaLast"] = qc
                            listLast.append(base)
                            used_lastsec += 1
                            continue
                    # ② 兜底: 9_24 时点(9:24:3x~4x 重采型)
                    v24 = m24.get(code)
                    if v24:
                        chg24, amt24 = v24
                        if amt24 > 0 and abs(amt25 - amt24) > 1e-6:
                            base["bidChange24"] = chg24
                            base["qcDeltaLast"] = round(chg - chg24, 2)
                            listLast.append(base)
                listLast.sort(key=lambda x: x["qcDeltaLast"], reverse=True)
                log.info("抢筹[listLast] date=%s %s 秒级序列=%d只 9_24=%d条 9_25=%d条 "
                         "匹配后listLast=%d只(秒级%d只/兜底%d只)",
                         today, hhmm, len(seq), len(rows24), len(rows25),
                         len(listLast), used_lastsec, len(listLast) - used_lastsec)
        except Exception as e:
            log.warning("抢筹 listLast 快照读取失败 err=%s", e)

        # 竞额/昨比: 今日竞价额(元) / 昨日全天成交额(万元) → 百分比。昨日额按 code 并发拉取(当日缓存)
        # ⚠️ 只对展示上限内(各表前100)拉昨比: listLast 全量可达5000+只, 全拉会被东财限流拖到60s+
        try:
            from . import fetcher as _fetcher
            codes = []
            for it in list20[:100] + list20Chg[:100] + listLast[:100]:
                c = str(it.get("code", ""))
                if c and c not in codes:
                    codes.append(c)
            yest_map = _fetcher.fetch_yesterday_amounts(codes) if codes else {}
        except Exception as e:
            log.warning("抢筹 昨日成交额拉取失败(昨比置空) err=%s", e)
            yest_map = {}

        def _fill_ratio(items):
            for it in items:
                if it.get("bidRatio") is not None:   # 读库项已有昨比, 不覆盖
                    continue
                pair = yest_map.get(str(it.get("code", "")))
                y_amt = pair[0] if isinstance(pair, (list, tuple)) else pair
                bid_amt = float(it.get("bidAmt") or 0)
                if y_amt and bid_amt > 0:
                    it["bidRatio"] = round(bid_amt / y_amt / 100, 2)   # 元 / 万元 / 100 → %
                else:
                    it["bidRatio"] = None
        _fill_ratio(list20)
        _fill_ratio(list20Chg)
        _fill_ratio(listLast)

        log.info("抢筹[result] date=%s %s list20=%d只 list20Chg=%d只 listLast=%d只 昨比命中=%d/%d 耗时%dms",
                 today, hhmm, len(list20[:100]), len(list20Chg[:100]), len(listLast[:100]),
                 len(yest_map), len(codes), int((time.time() - t0) * 1000))
        return {"list20": list20[:100], "list20Chg": list20Chg[:100], "listLast": listLast[:100],
                "date": today}
    return _cached("bid_qiangcang" + (("_" + date.replace("-", "")) if date else ""), 30, loader)


def get_qiangchou_codes(date=None):
    """竞价抢筹代码集合(供左视图抢筹标记, 2026-09-01):
    合并 fetch_bid_qiangcang 三表(list20 竞额强度 / list20Chg 9:20→9:25 涨幅 / listLast 最后一秒段)
    的 code 集合; 与竞价异动页"竞价抢筹"tab 数据同源(内置30s缓存), 保证左右视图口径一致。
    返回 set(code); 异常返回空 set(调用方回退旧公式兜底, 防数据源故障导致抢筹全灭)。"""
    try:
        d = fetch_bid_qiangcang(date or None) or {}
        codes = set()
        for lst in (d.get("list20") or [], d.get("list20Chg") or [], d.get("listLast") or []):
            for it in lst:
                c = str(it.get("code", "") or "")
                if c:
                    codes.add(c)
        return codes
    except Exception as e:
        log.warning("抢筹代码集获取失败(左视图抢筹按旧公式兜底) err=%s", e)
        return set()


def _surge_reason(sr):
    """surge_reason 是 dict: {stock_reason, related_plates:[{plate_name, plate_reason}]} → 拼接文本"""
    if not isinstance(sr, dict):
        return str(sr or "")
    parts = []
    if sr.get("stock_reason"):
        parts.append(str(sr["stock_reason"]))
    plates = sr.get("related_plates")
    if isinstance(plates, list):
        for p in plates:
            if isinstance(p, dict) and p.get("plate_name"):
                parts.append("%s:%s" % (p["plate_name"], p.get("plate_reason", "")))
    return "；".join(p for p in parts if p)


# ==================== 工具函数 ====================
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _num(v):
    if v is None:
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _pct(v):
    """解析百分比字符串: "10.00%" -> 10.0, "-2.95%" -> -2.95"""
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _lb(v):
    """连板数解析: "3连板"->3, "首板"->1, "6天4板"->4"""
    import re
    m = re.search(r"(\d+)连板", v or "")
    if m:
        return int(m.group(1))
    if "首板" in (v or ""):
        return 1
    m2 = re.search(r"(\d+)天(\d+)板", v or "")
    if m2:
        return int(m2.group(2))
    return 0









# ==================== 开盘啦 Kaipanla 全部接口封装 (按 /docs/{id} 编号) ====================
# 自动生成于 2026-08-13, 共 87 个 longhuvip.com 原始接口
# 调用约定: fetch_kpl_doc{N}(**extra) -> dict | None

def fetch_kpl_doc7(**extra):
    r"""k线-个股 (apphis.longhuvip.com) -> dict
    a=GetKLineDay_W14, c=StockLineData, apiv=w40 + extra
    resp 示例: {\"StockID\":\"302132\",\"name\":\"\",\"Time\":1786610805,\"x\":[\"20260305\",\"20260306\",\"20260309\",\"20260310\",\"20260311\",\"20260312\",\"20260
    """
    base = {"a": "GetKLineDay_W14", "c": "StockLineData", "apiv": "w40"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc8(**extra):
    r"""分时与、实时涨幅 (apphwhq.longhuvip.com) -> dict
    a=GetStockTrendIncremental, c=StockL2Data, apiv=w41 + extra
    resp 示例: {\"trend\":[[\"09:30\",8.07,8.07,114,0],[\"09:31\",8.04,8.054,744,0],[\"09:32\",7.99,8.01,2662,0],[\"09:33\",7.97,7.996,2032,0],[\"09:34\",7.98,7.984,
    """
    base = {"a": "GetStockTrendIncremental", "c": "StockL2Data", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc9(**extra):
    r"""盘口五档 (apphwhq.longhuvip.com) -> dict
    a=GetStockPanKou, c=StockL2Data, apiv=w41 + extra
    resp 示例: {\"day\":20260813,\"code\":\"000001\",\"name\":\"\\u5e73\\u5b89\\u94f6\\u884c\",\"preclose_px\":11.25,\"status\":86,\"real\":{\"time\":154603000,\"las
    """
    base = {"a": "GetStockPanKou", "c": "StockL2Data", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc13(**extra):
    r"""大单成交 (apphq.longhuvip.com) -> dict
    a=GetMainMonitor_w30, c=StockYiDongKanPan, apiv=w31 + extra
    resp 示例: {\"List\":[[\"2\",\"1786604400\",\"1498\",\"1044106\",\"6.97\",\"2026-08-13 15:00:00\"],[\"2\",\"1786604400\",\"3434\",\"2393498\",\"6.97\",\"2026-08-
    """
    base = {"a": "GetMainMonitor_w30", "c": "StockYiDongKanPan", "apiv": "w31"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc14(**extra):
    r"""大单委托 (apphq.longhuvip.com) -> dict
    a=GetWeiTuo_W14, c=StockL2Data, apiv=w39 + extra
    resp 示例: {\"start\":1404,\"end\":1503,\"List\":[[\"14:47:22\",\"52421517_CD\",\"11.24\",\"309\",\"347316\",\"2\",\"2\",\"0\",\"1\",\"1786603642\"],[\"14:47:22\
    """
    base = {"a": "GetWeiTuo_W14", "c": "StockL2Data", "apiv": "w39"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc15(**extra):
    r"""涨停复盘 - 复盘啦 (apphwshhq.longhuvip.com) -> dict
    a=GetPlateInfo_w38, c=DailyLimitResumption, apiv=w42 + extra
    resp 示例: {\"nums\":{\"SZJS\":1142,\"XDJS\":4317,\"ZT\":59,\"DT\":4,\"ZBL\":37.8947,\"yestRase\":1.188},\"list\":[],\"date\":\"2026-08-13\",\"Day\":[\"2026-08-1
    """
    base = {"a": "GetPlateInfo_w38", "c": "DailyLimitResumption", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc16(**extra):
    r"""涨停跌停-数量 (apphwshhq.longhuvip.com) -> dict
    a=RiseFallAnalysis, c=HomeDingPan, apiv=w43 + extra
    resp 示例: {\"info\":[[59,4,47,1,37.8947,36,\"2026-08-13\"]],\"ttag\":0.0009409999999999696,\"errcode\":\"0\"}
    """
    base = {"a": "RiseFallAnalysis", "c": "HomeDingPan", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc17(**extra):
    r"""涨停数量历史 (apphis.longhuvip.com) -> dict
    a=RiseFallAnalysis, c=HisHomeDingPan, apiv=w43 + extra
    resp 示例: {\"info\":[[62,4,47,1,37.8947,36,\"2026-08-13\"],[96,0,85,1,11.5385,12,\"2026-08-12\"],[60,2,54,4,22.6667,17,\"2026-08-11\"],[103,5,96,3,12.3894,14,\"
    """
    base = {"a": "RiseFallAnalysis", "c": "HisHomeDingPan", "apiv": "w43"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc18(**extra):
    r"""上涨/下跌家数 (apphwshhq.longhuvip.com) -> dict
    a=MoodNumCount, c=MarketMood, apiv=w43 + extra
    resp 示例: {\"list\":{\"SZJS\":1142,\"XDJS\":4317,\"ZTJS\":59,\"DTJS\":4,\"qscln\":255091673,\"q_zrcs\":215242310,\"bl\":18.51,\"color\":1},\"ttag\":0.0061760000
    """
    base = {"a": "MoodNumCount", "c": "MarketMood", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc19(**extra):
    r"""昨日涨停今表现 (apphwshhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"List\":[\"--\",-175,113518654777,-19041063,-1.26,0,0,0],\"Time\":1786610811,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"ttag\":0.00
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc20(**extra):
    r"""昨日连板今表现 (apphwshhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"List\":[\"--\",219,17928168874,0,0.32,0,0,0],\"Time\":1786610812,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"ttag\":0.0030540000000
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc21(**extra):
    r"""昨日破板今日表现 (apphwshhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"List\":[\"--\",-145,17020349482,0,-0.84,0,0,0],\"Time\":1786610812,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"ttag\":0.00369799999
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc22(**extra):
    r"""今日破板率 (apphwshhq.longhuvip.com) -> dict
    a=RiseFallAnalysis, c=HomeDingPan, apiv=w43 + extra
    resp 示例: {\"info\":[[59,4,47,1,37.8947,36,\"2026-08-13\"]],\"ttag\":0.0010620000000000074,\"errcode\":\"0\"}
    """
    base = {"a": "RiseFallAnalysis", "c": "HomeDingPan", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc23(**extra):
    r"""情绪值指标/连板高度 (apphq.longhuvip.com) -> dict
    a=ChangeStatistics, c=HomeDingPan, apiv=w41 + extra
    resp 示例: {\"info\":[{\"ztjs\":\"59\",\"Day\":\"2026-08-13\",\"df_num\":\"15\",\"strong\":\"51\",\"lbgd\":\"5\"}],\"tip\":\"\\u6e29\\u99a8\\u63d0\\u793a\\uff1a\
    """
    base = {"a": "ChangeStatistics", "c": "HomeDingPan", "apiv": "w41"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc24(**extra):
    r"""情绪-强度-历史 (apphis.longhuvip.com) -> dict
    a=ChangeStatistics, c=HisHomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[{\"strong\":\"51\",\"ztjs\":\"59\",\"lbgd\":\"5\",\"Day\":\"2026-08-13\",\"df_num\":\"15\"},{\"strong\":\"78\",\"ztjs\":\"92\",\"lbgd\":\"7
    """
    base = {"a": "ChangeStatistics", "c": "HisHomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc30(**extra):
    r"""竞价涨停委买额-历史接口 (apphis.longhuvip.com) -> dict
    a=MorningBiddingList, c=HisHomeDingPan, apiv=w41 + extra
    resp 示例: {\"info\":[[\"002579\",\"\\u4e2d\\u4eac\\u7535\\u5b50\",0,9.99,926858516,9.9889,36522513,1.26,47704956,138678558,0,\"\\u5370\\u5236\\u7535\\u8def\\u67
    """
    base = {"a": "MorningBiddingList", "c": "HisHomeDingPan", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc31(**extra):
    r"""竞价-个股竞价分时 (apphwhq.longhuvip.com) -> dict
    a=GetStockBid, c=StockL2Data, apiv=w41 + extra
    resp 示例: {\"code\":\"000785\",\"day\":20260813,\"bid\":[[\"09:15\",2.29,1,25],[\"09:15\",2.3,1,188],[\"09:16\",2.3,1,189],[\"09:16\",2.3,1,188],[\"09:17\",2.3,
    """
    base = {"a": "GetStockBid", "c": "StockL2Data", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc33(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=ZhiBoContent, c=HisConceptionPoint, apiv=w40 + extra
    resp 示例: {\"JHJJYD\":[\"\",\"\",0],\"List\":[],\"Notice\":\"\\u76f4\\u64ad\\u5373\\u5c06\\u5f00\\u59cb\\uff01\\uff01\\uff01\",\"Time\":1786550400,\"Status\":0,
    """
    base = {"a": "ZhiBoContent", "c": "HisConceptionPoint", "apiv": "w40"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc41(**extra):
    r"""精选板块列表-实时： (apphq.longhuvip.com) -> dict
    a=RealRankingInfo, c=ZhiShuRanking, apiv=w26 + extra
    resp 示例: {\"list\":[[\"801045\",\"\\u533b\\u836f\",9969,0.712,0.616,271057587087,4072089148,50934541266,-46862452118,1.218,7675361415847,0.83,1565506580,907194
    """
    base = {"a": "RealRankingInfo", "c": "ZhiShuRanking", "apiv": "w26"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc42(**extra):
    r"""精选板块列表-历史 (apphis.longhuvip.com) -> dict
    a=RealRankingInfo, c=ZhiShuRanking, apiv=w41 + extra
    resp 示例: {\"list\":[[\"801057\",\"\\u77f3\\u6cb9\\u77f3\\u5316\",7271,3.349,0.242,30557059554,1527890563,7324844425,-5796953862,2.791,2929208764309,0.6,6610840
    """
    base = {"a": "RealRankingInfo", "c": "ZhiShuRanking", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc43(**extra):
    r"""精选板块-当天历史 (apphwshhq.longhuvip.com) -> dict
    a=RealRankingInfo, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"list\":[[\"801807\",\"\\u7b97\\u529b\",2473,0.767,0,9139413402,289778705,1723841474,-1434062769,2.664,25212470958016,0,169194018,30764529295442,263
    """
    base = {"a": "RealRankingInfo", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc46(**extra):
    r"""板块成分股 (apphis.longhuvip.com) -> dict
    a=ZhiShuStockList_W8, c=ZhiShuRanking, apiv=w41 + extra
    resp 示例: {\"list\":[[\"300164\",\"\\u901a\\u6e90\\u77f3\\u6cb9\",\"\",0,\"\\u77f3\\u6cb9\\u77f3\\u5316\\u3001\\u897f\\u90e8\\u5927\\u5f00\\u53d1\",5.06,19.91,1
    """
    base = {"a": "ZhiShuStockList_W8", "c": "ZhiShuRanking", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc47(**extra):
    r"""当天涨停原因： (apphq.longhuvip.com) -> dict
    a=GetKLineZhangTing, c=StockLineData, apiv=w24 + extra
    resp 示例: {\"StockID\":\"000001\",\"List\":[],\"Time\":1786610831,\"ttag\":0.0003049999999999997,\"errcode\":\"0\"}
    """
    base = {"a": "GetKLineZhangTing", "c": "StockLineData", "apiv": "w24"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc48(**extra):
    r"""历史涨停原因： (apphis.longhuvip.com) -> dict
    a=GetKLineZhangTing, c=StockLineData, apiv=w24 + extra
    resp 示例: 
    """
    base = {"a": "GetKLineZhangTing", "c": "StockLineData", "apiv": "w24"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc49(**extra):
    r"""1，涨停的首板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"002322\",\"\\u7406\\u5de5\\u80fd\\u79d1\",0,\"\",1786584300,\"\\u4e2d\\u62a5\\u589e\\u957f\",78142528,132711224,40581357,53265627,-1268
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc50(**extra):
    r"""2，涨停的2板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"001260\",\"\\u5764\\u6cf0\\u80a1\\u4efd\",0,\"\",1786584300,\"\\u6c7d\\u8f66\\u96f6\\u90e8\\u4ef6\",292252896,310460672,17041676,394585
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc51(**extra):
    r"""3，涨停的3板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"603887\",\"\\u57ce\\u5730\\u9999\\u6c5f\",1,\"\",1786584331,\"\\u7b97\\u529b\",271131680,971528404,107874665,223678217,-115803552,22805
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc52(**extra):
    r"""4，涨停的4板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"000802\",\"\\u5317\\u4eac\\u6587\\u5316\",0,\"\",1786584300,\"\\u6587\\u5316\\u4f20\\u5a92\",167948928,350260576,-150012849,449495967,-
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc53(**extra):
    r"""5，涨停的更高 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"603758\",\"\\u79e6\\u5b89\\u80a1\\u4efd\",1,\"\",1786584333,\"\\u673a\\u5668\\u4eba\\u6982\\u5ff5\",179320240,303773449,16890234,460223
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc54(**extra):
    r"""1，历史涨停的首板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[[\"603887\",\"\\u57ce\\u5730\\u9999\\u6c5f\",0,\"\",1728955559,\"\\u5b9e\\u63a7\\u4eba\\u53d8\\u66f4\",551947392,3058554835,13281816,16827
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc55(**extra):
    r"""2，涨停的2板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[[\"002628\",\"\\u6210\\u90fd\\u8def\\u6865\",0,\"\",1728955551,\"\\u897f\\u90e8\\u5927\\u5f00\\u53d1\",125249472,170181152,371185,15692555
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc56(**extra):
    r"""3，涨停的3板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[[\"600622\",\"\\u5149\\u5927\\u5609\\u5b9d\",0,\"\",1728955551,\"\\u5730\\u4ea7\\u94fe\",330943808,1527980573,36962441,81879174,-44916733,
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc57(**extra):
    r"""4，涨停的4板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[],\"2024-10-15\"],\"ttag\":0.0009799999999999809,\"errcode\":\"0\"}
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc58(**extra):
    r"""5，涨停的更高 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[],\"2024-10-15\"],\"ttag\":0.0009430000000000271,\"errcode\":\"0\"}
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc59(**extra):
    r"""1，未涨停的首板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"920367\",\"\\u65b0\\u8d63\\u6c5f\",0,\"\",31.13,29.6,\"\\u533b\\u836f\\u3001\\u5317\\u4ea4AI\\u533b\\u7597\",0,0,0,378548380,647078331,
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc60(**extra):
    r"""2，未涨停的2板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"301602\",\"\\u8d85\\u7814\\u80a1\\u4efd\",1,\"\",20.4,10.99,\"AI\\u533b\\u7597\\u3001AI\\u5e94\\u7528\",18735671,110116516,-91380845,56
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc61(**extra):
    r"""3，未涨停的3板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"603897\",\"\\u957f\\u57ce\\u79d1\\u6280\",1,\"\",34.68,8.21,\"\\u673a\\u5668\\u4eba\\u6982\\u5ff5\\u3001\\u6241\\u7ebf\",91351854,35975
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc62(**extra):
    r"""4，未涨停的4板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"002248\",\"\\u534e\\u4e1c\\u6570\\u63a7\",0,\"\",11.74,-3.53,\"\\u5de5\\u4e1a\\u6bcd\\u673a\\u3001\\u4e00\\u5b63\\u62a5\\u589e\\u957f\"
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc63(**extra):
    r"""5，未涨停的更高 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"600721\",\"\\u767e\\u82b1\\u533b\\u836f\",0,\"\",14.5,3.35,\"CRO\\u3001\\u51cf\\u80a5\\u836f\",-309427187,594053313,-903480500,25717093
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc64(**extra):
    r"""1，未涨停首板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"688591\",\"\\u6cf0\\u51cc\\u5fae  \",0,\"\",58.47,10.57,\"\\u5e76\\u8d2d\\u91cd\\u7ec4\\u3001\\u6570\\u5b57\\u7ecf\\u6d4e\",-28478897,7
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc65(**extra):
    r"""2，未涨停2板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"688006\",\"\\u676d\\u53ef\\u79d1\\u6280\",0,\"\",30.15,17.13,\"\\u56fa\\u6001\\u7535\\u6c60\\u3001\\u9502\\u7535\\u8bbe\\u5907\",-18730
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc66(**extra):
    r"""3，未涨停3板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"000831\",\"\\u4e2d\\u56fd\\u7a00\\u571f\",0,\"\",59.12,1.37,\"\\u7a00\\u571f\\u6c38\\u78c1\\u3001\\u6709\\u8272\\u91d1\\u5c5e\",-142416
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc67(**extra):
    r"""4，未涨停4板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"002053\",\"\\u4e91\\u5357\\u80fd\\u6295\",0,\"\",14.47,-3.47,\"\\u7eff\\u8272\\u7535\\u529b\\u3001\\u5929\\u7136\\u6c14\",-7477684,2805
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc68(**extra):
    r"""5，未涨停 更高 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"002053\",\"\\u4e91\\u5357\\u80fd\\u6295\",0,\"\",14.47,-3.47,\"\\u7eff\\u8272\\u7535\\u529b\\u3001\\u5929\\u7136\\u6c14\",-7477684,2805
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc69(**extra):
    r"""百日新高-板块排序 (apphwshhq.longhuvip.com) -> dict
    a=GroupCount_w28, c=StockNewHigh, apiv=w41 + extra
    resp 示例: {\"List\":[[\"\\u533b\\u836f\",\"46,19\",801045],[\"AI\\u5e94\\u7528\",\"8,4\",803023],[\"\\u5730\\u4ea7\\u94fe\",\"4,1\",801676],[\"\\u673a\\u5668\\u
    """
    base = {"a": "GroupCount_w28", "c": "StockNewHigh", "apiv": "w41"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc70(**extra):
    r"""短线精灵 (apphq.longhuvip.com) -> dict
    a=Radar, c=HomeDingPan, apiv=w33 + extra
    resp 示例: {\"list\":[{\"time\":1786604219,\"status\":\"\\u5c01\\u6da8\\u5927\\u51cf\",\"stock_name\":\"\\u795e\\u5947\\u5236\\u836f\",\"plate_type\":1,\"status_
    """
    base = {"a": "Radar", "c": "HomeDingPan", "apiv": "w33"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc71(**extra):
    r"""盘中人气热榜 (apphq.longhuvip.com) -> dict
    a=GetHotPHB, c=StockBidYiDong, apiv=w29 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"List\":[[\"600721\",\"\\u767e\\u82b1\\u533b\\u836f\",3.35,0,1,0,0],[\"600664\",\"\\u54c8\\u836f\\u80a1\\u4efd\",0.57,0,2,0,0
    """
    base = {"a": "GetHotPHB", "c": "StockBidYiDong", "apiv": "w29"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc72(**extra):
    r"""全球指数 (apphwshhq.longhuvip.com) -> dict
    a=GlobalCommon, c=GlobalIndex, apiv=w44 + extra
    resp 示例: {\"CYWWZS\":[{\"code\":\"DJI\",\"prod_name\":\"\\u9053\\u743c\\u65af\",\"last_px\":\"53770.270\",\"turnover\":\"0.000\",\"increase_rate\":\"-0.04%\",\
    """
    base = {"a": "GlobalCommon", "c": "GlobalIndex", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc74(**extra):
    r"""历史： (apphis.longhuvip.com) -> dict
    a=GetStockChouMa_New, c=StockL2History, apiv=w41 + extra
    resp 示例: {\"List\":[[0,-30458,-578717,609153,0,0,2453334,315666,-346124,\"09:30\",0.02],[-3245806,1148760,-331922,2428867,0,8,53159424,8009574,-10106620,\"09:3
    """
    base = {"a": "GetStockChouMa_New", "c": "StockL2History", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc76(**extra):
    r"""涨停基因 (apphwhq.longhuvip.com) -> dict
    a=GetZhangTingGene, c=StockL2Data, apiv=w42 + extra
    resp 示例: {\"List\":[12,4,88.8889,64.2857,35.7143,20],\"ttag\":0.00023400000000001198,\"errcode\":\"0\"}
    """
    base = {"a": "GetZhangTingGene", "c": "StockL2Data", "apiv": "w42"}
    base.update(extra)
    return _call("after", base)


def fetch_kpl_doc116(**extra):
    r"""大面股-实时 (apphwshhq.longhuvip.com) -> dict
    a=GetPMSL_KQXY, c=FuPanLa, apiv=w35 + extra (与 doc77 历史同参, host 换实时)
    resp 示例: {"date":"2026-08-14","Time":1786760421,"List":[["000692","惠天热电","-6.15%",-14.07,"",0,"热力、股权转让"],...]}
    实测: after host 返回当日实时 9 条; doc77 走 his(历史) 需 Date 参数
    """
    base = {"a": "GetPMSL_KQXY", "c": "FuPanLa", "apiv": "w35"}
    base.update(extra)
    return _call("after", base)


def fetch_kpl_doc77(**extra):
    r"""大面股 (apphis.longhuvip.com) -> dict
    a=GetPMSL_KQXY, c=FuPanLa, apiv=w35 + extra
    resp 示例: {\"List\":[[\"002676\",\"\\u987a\\u5a01\\u80a1\\u4efd\",\"-1.18%\",-10.2,\"\",0,\"\\u805a\\u4e19\\u70ef\\u3001\\u58f3\\u8d44\\u6e90\"],[\"300889\",\"\
    """
    base = {"a": "GetPMSL_KQXY", "c": "FuPanLa", "apiv": "w35"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc78(**extra):
    r"""板块内涨停数 (apphwhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w41 + extra
    resp 示例: {\"List\":[28,295,352854517421,-1282084796,-1.97,1,72905796,35879198],\"Time\":1786610857,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc79(**extra):
    r"""板块竞价异动 (apphwhq.longhuvip.com) -> dict
    a=GetBKJJ_W36, c=StockBidYiDong, apiv=w41 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"List1\":[[\"801003\",\"5G\",12.3,969312364,163,28233453],[\"801004\",\"\\u9502\\u7535\\u6c60\",6.5,166418828,663,16296398],[
    """
    base = {"a": "GetBKJJ_W36", "c": "StockBidYiDong", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc80(**extra):
    r"""异动板块的个股 (apphwhq.longhuvip.com) -> dict
    a=GetBKJJBL, c=StockBidYiDong, apiv=w41 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"List\":[[\"301107\",\"\\u745c\\u6b23\\u7535\\u5b50\",21.1,-5.8,84,1126356,-0.62,0,0.12,863416093,\"\\u673a\\u5668\\u4eba\\u6
    """
    base = {"a": "GetBKJJBL", "c": "StockBidYiDong", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc81(**extra):
    r"""板块列表（end为当日） (apphwshhq.longhuvip.com) -> dict
    a=GetInterviewsByDateZS, c=StockLineData, apiv=w41 + extra
    resp 示例: {\"List\":[],\"Count\":0,\"ttag\":0.001762999999999959,\"errcode\":\"0\"}
    """
    base = {"a": "GetInterviewsByDateZS", "c": "StockLineData", "apiv": "w41"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc82(**extra):
    r"""全市场个股区间统计（end为当日） (apphwshhq.longhuvip.com) -> dict
    a=GetInterviewsByDateStock, c=StockLineData, apiv=w41 + extra
    resp 示例: {\"List\":[],\"Count\":0,\"ttag\":0.0018840000000000245,\"errcode\":\"0\"}
    """
    base = {"a": "GetInterviewsByDateStock", "c": "StockLineData", "apiv": "w41"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc83(**extra):
    r"""实时接口（最新季度）: (apphis.longhuvip.com) -> dict
    a=GGList_JGCC, c=ZhuLiChiCang, apiv=w41 + extra
    resp 示例: {\"List\":[[\"801001\",\"\\u82af\\u7247\",\"210781176639\",\"32.3844\",\"1230624973443\",\"36.09\",\"37.15\",\"76764146365125\",\"0\"],[\"801660\",\"\
    """
    base = {"a": "GGList_JGCC", "c": "ZhuLiChiCang", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc84(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=GGList_JGCC, c=ZhuLiChiCang, apiv=w41 + extra
    resp 示例: {\"List\":[[\"801660\",\"\\u901a\\u4fe1\",\"58971507956\",\"16.6\",\"489677063576\",\"25.45\",\"31.85\",\"25570439788797\",\"0\"],[\"801081\",\"\\u8bc
    """
    base = {"a": "GGList_JGCC", "c": "ZhuLiChiCang", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc85(**extra):
    r"""实时接口（最新季度）、历史接口： (apphis.longhuvip.com) -> dict
    a=GGList_JGCC_Plate_Stocks, c=ZhuLiChiCang, apiv=w41 + extra
    resp 示例: {\"List\":[[\"688256\",\"\\u5bd2\\u6b66\\u7eaa  \",\"13877500939\",\"15.51\",\"103140966888\",\"753951562800\",\"16181148\",\"76177030\",\"2.31\",\"27
    """
    base = {"a": "GGList_JGCC_Plate_Stocks", "c": "ZhuLiChiCang", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc86(**extra):
    r"""实时接口：（最新季度） (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"List\":[[\"801001\",\"\\u82af\\u7247\",\"85882395225\",\"35.77\",\"541406743418\",\"36.09\",\"37.15\",\"76764146365125\",\"1\"],[\"801004\",\"\\u95
    """
    base = {"a": "GGList_BXZJ", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc87(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"List\":[[\"801088\",\"\\u6709\\u8272\\u91d1\\u5c5e\",\"20122564633\",\"21.76\",\"131838432453\",\"12.3\",\"15.38\",\"8580912833302\",\"0\"],[\"8010
    """
    base = {"a": "GGList_BXZJ", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc88(**extra):
    r"""实时接口：（最新季度） (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ_Stocks, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"State\":1,\"Date\":\"2026-06-30\",\"DateList\":[\"2026-06-30\",\"2026-03-31\",\"2025-12-31\",\"2025-09-30\",\"2025-06-30\",\"2025-03-31\",\"2024-12
    """
    base = {"a": "GGList_BXZJ_Stocks", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc89(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ_Stocks, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"State\":1,\"Date\":\"2025-12-31\",\"DateList\":[\"2026-06-30\",\"2026-03-31\",\"2025-12-31\",\"2025-09-30\",\"2025-06-30\",\"2025-03-31\",\"2024-12
    """
    base = {"a": "GGList_BXZJ_Stocks", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc90(**extra):
    r"""异动实时接口 (apphwshhq.longhuvip.com) -> dict
    a=GetPianLiZhi_Index, c=StockBidYiDong, apiv=w44 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"Many_Num\":19,\"Time\":1786610872,\"List\":[[\"603221\",\"\\u7231\\u4e3d\\u5bb6\\u5c45\",0,\"\\u80a1\\u7968\\u4ea4\\u6613\\u
    """
    base = {"a": "GetPianLiZhi_Index", "c": "StockBidYiDong", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc91(**extra):
    r"""股东变更 (applhb.longhuvip.com) -> dict
    a=GuDongRenShu, c=YiDianCangWei, apiv=w44 + extra
    resp 示例: {\"DateList\":[{\"StratDate\":\"2026-08-01\",\"EndDate\":\"2026-08-15\",\"ShowDate\":\"08\\u670801\\u65e5-08\\u670815\\u65e5\"},{\"StratDate\":\"2026-
    """
    base = {"a": "GuDongRenShu", "c": "YiDianCangWei", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc92(**extra):
    r"""股东追踪，追股东 (applhb.longhuvip.com) -> dict
    a=JGStockListox, c=JGTracking, apiv=w41 + extra
    resp 示例: {\"Time\":1786610871,\"StockList\":[{\"StockID\":\"603986\",\"name\":\"\\u5146\\u6613\\u521b\\u65b0\",\"lpx\":\"404.50\",\"rate\":\"-2.10%\"}],\"List\
    """
    base = {"a": "JGStockListox", "c": "JGTracking", "apiv": "w41"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc93(**extra):
    r"""股东追踪，追个股 (applhb.longhuvip.com) -> dict
    a=GetJGNameID, c=JGTracking, apiv=w44 + extra
    resp 示例: {\"List\":[{\"JG\":\"\\u5f20\\u5f3a\",\"JGID\":\"11828\"}],\"errcode\":\"0\",\"t\":0.0020139999999999603}
    """
    base = {"a": "GetJGNameID", "c": "JGTracking", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc94(**extra):
    r"""\u4e2a\u80a1 - \u5168\u90e8\u76f8\u5173 \u6982\u5ff5\u677f\u5757 (apphwhq/apphwshhq.longhuvip.com) -> dict
    a=GetStockIDPlate, c=StockL2Data, apiv=w43, Type=2 + extra(StockID=xxx \u5fc5\u4f20)
    resp \u793a\u4f8b: {"List":[],"ListJX":[["801159","\u673a\u5668\u4eba\u6982\u5ff5",-1.343],["801273","\u80a1\u6743\u8f6c\u8ba9",-1.147],...]
    \u6ce8: Type=2 \u5fc5\u4f20, \u9ed8\u8ba4 0 \u65f6 ListJX \u8fd4\u7a7a; host \u662f default(apphwhq) \u6216 after(apphwshhq) \u90fd\u53ef
    \u6587\u6863\u793a\u4f8b URL appvipshhq.longhuvip.com \u5b9e\u9645 DNS \u65e0\u6cd5\u89e3\u6790, \u8d70 default host"""
    base = {"a": "GetStockIDPlate", "c": "StockL2Data", "apiv": "w43", "Type": "2"}
    base.update(extra)
    return _call("default", base)


def fetch_stock_plate(code, use_cache=True):
    """\u4e2a\u80a1\u5168\u90e8\u76f8\u5173\u6982\u5ff5\u677f\u5757(\u5f00\u76d8\u5566 doc94 GetStockIDPlate):
    \u8fd4\u56de\u62fc\u63a5\u7684\u677f\u5757\u5b57\u7b26\u4e32(\u5982 "\u673a\u5668\u4eba\u6982\u5ff5\u3001\u80a1\u6743\u8f6c\u8ba9\u3001\u6c7d\u8f66\u96f6\u90e8\u4ef6"), \u5931\u8d25\u8fd4\u56de ""
    \u6309\u80a1\u7f13\u5b58 1 \u5929(\u677f\u5757\u5f52\u5c5e\u53d8\u52a8\u4f4e), \u5927\u5e45\u51cf\u5c11 KPL \u8c03\u7528\u6b21\u6570
    \u9009\u80a1\u7ed3\u679c 39 \u53ea \xd7 30s \u7f13\u5b58\u5237\u65b0 \u2192 \u9996\u6b21 39 \u6b21, \u4e4b\u540e\u547d\u4e2d"""
    key = "stock_plate_" + str(code)
    def loader():
        d = fetch_kpl_doc94(StockID=str(code))
        # 接口失败/返回异常(err None 或非 "0")→ 返回 None, _cached 不缓存, 下次重试
        # 避免瞬时失败被缓存 1 天空串导致概念永远覆盖不上
        if not d:
            return None
        err = d.get("errcode")
        if err is not None and str(err) != "0":
            return None
        lst = d.get("ListJX") or []
        names = []
        for it in lst:
            if isinstance(it, list) and len(it) >= 2 and it[1]:
                nm = str(it[1]).strip()
                if nm:
                    names.append(nm)
        return "\u3001".join(names) if names else None
    return _cached(key, 86400, loader) if use_cache else loader()  # 1 \u5929\u7f13\u5b58(仅成功结果), \u677f\u5757\u5f52\u5c5e\u7a33\u5b9a


def fetch_kpl_doc95(**extra):
    r"""头条 (apparticle.longhuvip.com) -> dict
    a=GetTopList, c=PCNewsFlash, apiv=w44 + extra
    resp 示例: {\"List\":[{\"Date\":\"2026-08-13\",\"Detail\":[{\"ID\":\"80872996205972158\",\"Date\":\"2026-08-13\",\"Title\":\"DeepSeek V4 Pro\\u6b63\\u5f0f\\u7248
    """
    base = {"a": "GetTopList", "c": "PCNewsFlash", "apiv": "w44"}
    base.update(extra)
    return _call("article", base)

def fetch_kpl_doc96(**extra):
    r"""新闻 (apparticle.longhuvip.com) -> dict
    a=GetList, c=PCNewsFlash, apiv=w44 + extra
    resp 示例: {\"List\":[{\"CID\":\"1783359\",\"Time\":\"1786610643\",\"Title\":\"\",\"Type\":\"1\",\"PushUrl\":\"\",\"Source\":\"\\u534e\\u5c14\\u8857\",\"IsSDXZ\"
    """
    base = {"a": "GetList", "c": "PCNewsFlash", "apiv": "w44"}
    base.update(extra)
    return _call("article", base)

def fetch_kpl_doc97(**extra):
    r"""明天炒什么（列表） (applhb.longhuvip.com) -> dict
    a=InfoList, c=Topic, apiv=w44 + extra
    resp 示例: {\"List\":[{\"Day\":\"2026-08-12\",\"List\":[{\"ID\":\"2359\",\"Title\":\"\\u9ad8\\u6807\\uff1a\\u518d\\u6da8\\u5c31\\u505c\\u724c\\uff01\\u6bb5\\u6c3
    """
    base = {"a": "InfoList", "c": "Topic", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc98(**extra):
    r"""明天炒什么 利好个股 (applhb.longhuvip.com) -> dict
    a=InfoZS, c=Topic, apiv=w44 + extra
    resp 示例: {\"List\":[{\"StockID\":\"000066\",\"Name\":\"\\u4e2d\\u56fd\\u957f\\u57ce\",\"last_px\":\"1.61\",\"HotVal\":53766,\"HotTag\":3,\"Click\":0,\"Trad\":\
    """
    base = {"a": "InfoZS", "c": "Topic", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc99(**extra):
    r"""明天炒什么 文章内容 (applhb.longhuvip.com) -> dict
    a=InfoGet, c=Topic, apiv=w44 + extra
    resp 示例: {\"Title\":\"\\u8054\\u624b\\u82f1\\u4f1f\\u8fbe\\uff01\\u5eb7\\u5b81\\u62df\\u5341\\u500d\\u6269\\u4ea7\\u5149\\u8fde\\u63a5\\uff0c\\u5149\\u7ea4\\u4
    """
    base = {"a": "InfoGet", "c": "Topic", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc100(**extra):
    r"""上榜股票 (applhb.longhuvip.com) -> dict
    a=GetStockList, c=LongHuBang, apiv=w44 + extra
    resp 示例: {\"Time\":\"2026-08-13\",\"UserType\":0,\"list\":[{\"ID\":\"002792\",\"Name\":\"\\u901a\\u5b87\\u901a\\u8baf\",\"IncreaseAmount\":\"4.10%\",\"D3\":\"0
    """
    base = {"a": "GetStockList", "c": "LongHuBang", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc101(**extra):
    r"""买入、卖出营业部详细数据 (applhb.longhuvip.com) -> dict
    a=GetNewOneStockInfo, c=Stock, apiv=w41 + extra
    resp 示例: {\"Name\":\"\\u65b0\\u80fd\\u6cf0\\u5c71\",\"Time\":\"2026-04-02\",\"Group\":{\"Buy\":[],\"Sell\":[]},\"KlineDay\":{\"S\":\"2026-04-10\",\"E\":\"2026-
    """
    base = {"a": "GetNewOneStockInfo", "c": "Stock", "apiv": "w41"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc103(**extra):
    r"""尾盘竞价抢筹 (apphwshhq.longhuvip.com) -> dict
    a=GetWPQC, c=StockBidYiDong, apiv=w44 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"State\":0,\"List\":[[\"603***\",\"****\",\"\\u6e38\\u8d44\",0,\"\\u7b97\\u529b\\u79df\\u8d41\\u3001\\u7b97\\u529b\",4.73,731
    """
    base = {"a": "GetWPQC", "c": "StockBidYiDong", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc104(**extra):
    r"""竞价砸盘 (apphwshhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[[\"600272\",\"\\u5f00\\u5f00\\u5b9e\\u4e1a\",16,-7.35,0,-9.9,9438701,0,0,0,27413608,\"\\u533b\\u836f\\u96f6\\u552e\\u3001SPD\",1529344000,5
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc105(**extra):
    r"""竞价撮合大于2000万 (apphwshhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[[\"688825\",\"\\u957f\\u946b\\u79d1\\u6280\",52.88,-1.2,0,2.39,56546659,0,0,0,579284936,\"\\u5b58\\u50a8\\u3001\\u4e2d\\u62a5\\u589e\\u957f
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc106(**extra):
    r"""历史分时 (apphis.longhuvip.com) -> dict
    a=GetStockTrend, c=StockL2History, apiv=w41 + extra
    resp 示例: {\"trend\":[[\"09:30\",15,15,909,1],[\"09:31\",14.95,14.982,5750,0],[\"09:32\",14.91,14.962,4951,0],[\"09:33\",14.95,14.958,2505,1],[\"09:34\",14.97,1
    """
    base = {"a": "GetStockTrend", "c": "StockL2History", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc107(**extra):
    r"""指数k线 (apphis.longhuvip.com) -> dict
    a=GetZhiShuKLine, c=ZhiShuKLine, apiv=w44 + extra
    resp 示例: {\"StockID\":\"SH000001\",\"x\":[20240105,20240108,20240109,20240110,20240111,20240112,20240115,20240116,20240117,20240118,20240119,20240122,20240123,
    """
    base = {"a": "GetZhiShuKLine", "c": "ZhiShuKLine", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc108(**extra):
    r"""重点监控股票 (apphwshhq.longhuvip.com) -> dict
    a=GetYDTP_ZDJK_Today, c=StockBidYiDong, apiv=w43 + extra
    resp 示例: {\"Time\":1786610881,\"List\":[[\"600721\",\"\\u767e\\u82b1\\u533b\\u836f\",\"2026-08-13\",\"2026-08-26\",2],[\"605255\",\"\\u5929\\u666e\\u80a1\\u4ef
    """
    base = {"a": "GetYDTP_ZDJK_Today", "c": "StockBidYiDong", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc109(**extra):
    r"""多次异动个股 (apphwshhq.longhuvip.com) -> dict
    a=GetPianLiZhi_Many, c=StockBidYiDong, apiv=w43 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"Time\":1786610883,\"List\":[[\"000593\",\"\\u5fb7\\u9f99\\u6c47\\u80fd\",1,\"10\\u65e5\\u51852\\u6b21\\u5f02\\u52a8\\u4e2a\\
    """
    base = {"a": "GetPianLiZhi_Many", "c": "StockBidYiDong", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_pianli_hot(**extra):
    r"""热门股偏离值(热门度严重异常) (apphwshhq.longhuvip.com) -> dict
    a=GetPianLiZhi_Hot, c=StockBidYiDong, apiv=w44 + extra
    resp 示例: {\"Day\":\"2026-08-21\",\"Time\":1787404488,\"List\":[[\"300570\",\"\\u592a\\u8fb0\\u5149\",\"10\\u65e5100%\",0.5,53.11,\"\",30.86,30.71,\"CPO/MPO\\u3001\\u5149\\u6a21\\u5757\",0,\"8\\u65e5\",\"10\\u65e5100%\"], [\"002412\",\"\\u6c49\\u68ee\\u5236\\u836f\",\"10\\u65e5100%\",10.04,42.18,\"3\\u8fde\\u677f\",45.58,42.26,\"\\u4e2d\\u836f\\u3001\\u4e2d\\u62a5\\u589e\\u957f\",0,\"7\\u65e5\",\"10\\u65e5100%\"]], ...}
    字段([0]代码 [1]名称 [2]偏离类型 [3]今日涨跌% [4]偏离值 [5]连板/标签 [6]异动前涨幅 [7]偏离基准 [8]概念 [9]0 [10]偏离天数 [11]偏离规则)
    """
    base = {"a": "GetPianLiZhi_Hot", "c": "StockBidYiDong", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc110(**extra):
    r"""实时接口 (apphis.longhuvip.com) -> dict
    a=MarketSCLNKLine, c=HisHomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[{\"lastPoint\":\"255091673\",\"Date\":\"2026-08-13\"},{\"lastPoint\":\"215242310\",\"Date\":\"2026-08-12\"},{\"lastPoint\":\"232098591\",\"
    """
    base = {"a": "MarketSCLNKLine", "c": "HisHomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc111(**extra):
    r"""历史接口 (apphis.longhuvip.com) -> dict
    a=MarketSCLNKLine, c=HisHomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[{\"lastPoint\":\"255091673\",\"Date\":\"2026-08-13\"},{\"lastPoint\":\"215242310\",\"Date\":\"2026-08-12\"},{\"lastPoint\":\"232098591\",\"
    """
    base = {"a": "MarketSCLNKLine", "c": "HisHomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc112(**extra):
    r"""竞价大于1000万 (apphwshhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[[\"300308\",\"\\u4e2d\\u9645\\u65ed\\u521b\",921.04,0,0,4.23,130182720,0,0,0,478656000,\"\\u5149\\u6a21\\u5757\\u3001OCS\\u4ea4\\u6362\\u67
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc113(**extra):
    r"""副图688523 (apphis.longhuvip.com) -> dict
    a=GetBidVolKLine, c=StockLineData, apiv=w44 + extra
    resp 示例: {\"ZJJE\":[0,0,-715017,484999,0,0,0,0,0,0,0,0,0,0,-336154,427825,0,0,0,0,311907,60020,-307949,-3477854,-476202,0,-357327,0,-693120,0,0,359041,563813,0
    """
    base = {"a": "GetBidVolKLine", "c": "StockLineData", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc115(**extra):
    r"""竞价涨停委买额-实时接口： (apphwhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w41 + extra
    resp 示例: {\"info\":[[\"300862\",\"\\u84dd\\u76fe\\u5149\\u7535\",39.41,20.01,2352414428,20.01,17708290,1.39,76736590,65369367,76736590,\"\\u5e76\\u8d2d\\u91cd\
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)


# 共生成 87 个 fetch_kpl_doc{N} 函数

# ==================== 竞价异动日终快照(历史回看) ====================
def save_auction_history(date, phase="bid"):
    """抓当日竞价异动各 tab 落库 auction_daily_history
    phase='bid'  (9:26-9:30 竞价窗口调用, 竞价类数据必须此时落库!):
        seal(竞价委买)/boom(竞价爆量)/qiangcang(抢筹list20)
        ⚠️ 这些是竞价实时接口, 15:30 收盘后返回空 → 必须在 9:30 前落库
    phase='close' (15:30 日终调用, 非竞价类):
        yest_zt(昨日涨停)/yest_broken(昨断板)/broken_yest(昨炸板)/broken_today(今炸板)
    返回落库 tab 数; 某 tab 抓取失败不影响其他"""
    import sqlite3 as _sql
    if phase == "bid":
        items = [
            ("seal", fetch_bid_seal()),
            ("boom", fetch_bid_boom()),
            ("qiangcang", (fetch_bid_qiangcang() or {}).get("list20", [])),
            ("bid_net", fetch_bid_net()),   # 2026-08-22: 竞价净额榜加入落库, 支持历史回看
        ]
    else:
        items = [
            ("yest_zt", fetch_yest_zt()),
            ("yest_broken", fetch_yest_broken()),
            ("broken_yest", fetch_broken_zt("yesterday")),
            ("broken_today", fetch_broken_zt()),
        ]
    n = 0
    for tab, lst in items:
        if not lst:
            log.warning("竞价异动快照[%s] date=%s 抓取为空, 跳过", tab, date)
            continue
        try:
            # 2026-08-22: 落库前补竞换/竞额, 否则历史回看/非交易日回退这两列空
            if phase == "bid" and tab in ("seal", "bid_net"):
                fill_bid_turnover_from_snap(lst, date)
                if tab == "bid_net":
                    fill_bid_amt_from_snap(lst, date)
            conn = _sql.connect(config.DB_FILE)
            conn.execute(
                "INSERT OR REPLACE INTO auction_daily_history (date, tab, list, ts) VALUES (?,?,?,?)",
                (date, tab, json.dumps(lst, ensure_ascii=False), int(time.time())))
            conn.commit()
            conn.close()
            n += 1
        except Exception as e:
            log.warning("竞价异动快照落库失败 date=%s tab=%s err=%s", date, tab, e)
    log.info("竞价异动快照[%s] date=%s 落库 %d 个 tab", phase, date, n)
    return n


def query_auction_history(date, tab):
    """读取某日某 tab 竞价异动历史快照; 无数据返回 []"""
    try:
        import sqlite3 as _sql
        conn = _sql.connect(config.DB_FILE)
        row = conn.execute(
            "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
            (date, tab)).fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        log.warning("竞价异动历史查询失败 date=%s tab=%s err=%s", date, tab, e)
    return []


_CLOSE_CHG_CACHE = {}       # date -> (ts, {code: pct}) 内存热缓存
_CLOSE_CHG_TTL = 6 * 3600
# 交易日盘中被旧代码误写的脏"收盘涨幅"(实为竞价涨幅)自愈: 记录已强制重拉纠正过的日期
_CLOSE_CHG_RESYNCED = set()

try:
    import re as _re_cls_chg
    _CLOSE_CHG_JSON_RE = _re_cls_chg.compile(r"=\s*(\{[\s\S]*\})\s*;?\s*$")
except Exception:
    _CLOSE_CHG_JSON_RE = None


def _close_chg_db_get(date, codes):
    """从 close_change_history 批量读 pct 命中; 返回 {code: pct}"""
    out = {}
    if not codes or not date:
        return out
    try:
        from ..db import database
        conn = database.get_conn()
        for code in codes:
            row = conn.execute(
                "SELECT pct FROM close_change_history WHERE date=? AND code=?",
                (date, code)).fetchone()
            if row:
                out[code] = row[0]
        conn.close()
    except Exception:
        pass
    return out


def _close_chg_persist_allowed(date):
    """是否允许把 date 的收盘涨幅持久化到 close_change_history。

    根因修复(2026-08-24): 盘中(未收盘)当日 K 线的 last close 是实时价,
    此时把"当日涨幅"当"当日收盘涨幅"写入会永久污染该日数据 —— 盘后/历史
    回看 fill_close_change_from_kline 先命中库表读到脏值, 导致现涨=竞涨/
    现涨错误(用户反馈)。规则: date<今天 → 早已收盘, 允许; date==今天 →
    仅北京时间已过 15:00(收盘)才允许; 其它 → 禁止。
    """
    import time as _t
    if not date:
        return False
    today_bj = _t.strftime("%Y-%m-%d", _t.gmtime(_t.time() + 8 * 3600))
    if date < today_bj:
        return True
    if date > today_bj:
        return False
    bj = _t.gmtime(_t.time() + 8 * 3600)
    return (bj.tm_hour, bj.tm_min) >= (15, 0)


def _close_chg_db_put(date, pairs):
    """把 {code: pct} 持久化到 close_change_history, 后续历史回看免请求东财"""
    if not pairs or not date:
        return
    try:
        from ..db import database
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO close_change_history(date,code,pct) VALUES(?,?,?)",
            [(date, c, v) for c, v in pairs.items()])
        conn.commit()
        conn.close()
    except Exception:
        pass


def _close_chg_pct_sina(date, code):
    """新浪日K取某股某日收盘涨跌幅(%)兜底; 失败返回 None"""
    try:
        import urllib.request, ssl, json as _json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sym = "sh" + code if code[0] == "6" else ("bj" + code if code[0] in "48" else "sz" + code)
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData"
               f"?symbol={sym}&scale=240&ma=no&datalen=160")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            arr = _json.loads(r.read().decode("utf-8", "ignore"))
        prev = None
        for row in arr:
            d = str(row.get("day", ""))[:10]
            c = float(row.get("close") or 0)
            if d == date and prev:
                return round((c - prev) / prev * 100, 2)
            if c:
                prev = c
    except Exception:
        pass
    return None


def _close_chg_pct_tencent(date, code):
    """腾讯日K(qq-web行情)取某股某日收盘涨跌幅(%)兜底; 失败返回 None"""
    try:
        import urllib.request, ssl, json as _json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sym = "sh" + code if code[0] == "6" else ("bj" + code if code[0] in "48" else "sz" + code)
        # 腾讯 qq 日K: 最近 160 根日线足够回溯 ~8 月
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,160,qfq")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            txt = r.read().decode("utf-8", "ignore")
        obj = _json.loads(txt)
        # data.{sym}.qfqday / data.{sym}.day
        dat = (obj.get("data") or {}).get(sym) or {}
        arr = dat.get("qfqday") or dat.get("day") or []
        prev = None
        for row in arr:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            d = str(row[0])[:10]
            c = float(row[2] or 0)
            if d == date and prev:
                return round((c - prev) / prev * 100, 2)
            if c:
                prev = c
    except Exception:
        pass
    return None


def _close_chg_pct_ths(date, code):
    """同花顺(10jqka)日线接口取某股某日收盘涨跌幅(%)兜底; 失败返回 None"""
    try:
        import urllib.request, ssl, json as _json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # 同花顺 10jqka 代码: 沪=1_xxxxxx 深=0_xxxxxx 北=1_xxxxxx(保守)
        if code[0] == "6":
            secid = f"1_{code}"
        elif code[0] in "48":
            secid = f"1_{code}"
        else:
            secid = f"0_{code}"
        url = (f"https://d.10jqka.com.cn/v6/line/hs_{secid}/01/last.js")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://stockpage.10jqka.com.cn/{code}/"})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            js = r.read().decode("gbk", "ignore")
        # last.js 返回 json_hex = {...}
        m = _CLOSE_CHG_JSON_RE.search(js) if _CLOSE_CHG_JSON_RE else None
        if not m:
            return None
        obj = _json.loads(m.group(1))
        # klines: "date,open,high,low,close,vol,amount"
        rows = obj.get("data") or obj.get("klines") or []
        prev = None
        for row in rows:
            parts = row.split(",") if isinstance(row, str) else row
            if not parts or len(parts) < 5:
                continue
            d = str(parts[0])[:10]
            c = float(parts[4] or 0)
            if d == date and prev:
                return round((c - prev) / prev * 100, 2)
            if c:
                prev = c
    except Exception:
        pass
    return None


def fill_close_change_from_kline(lst, date):
    """历史回看: 把列表中股票 change/realChange 覆盖为所选交易日 date 的**当日收盘涨跌幅(%)
    数据来源优先级: 进程内存 → close_change_history 库表(持久化) → 多源日K(缺失才拉, 并写库)。
    多源顺序: fetch_stock_chart_robust(东财→腾讯→同花顺→开盘啦) → 新浪 → 腾讯 → 同花顺。
    因此历史日首次补齐后, 后续回看不再请求外部接口。返回被覆盖的股票数。"""
    if not lst or not date:
        return 0
    from ..services import fetcher
    now = time.time()
    for k, (ts, _) in list(_CLOSE_CHG_CACHE.items()):
        if now - ts > _CLOSE_CHG_TTL:
            _CLOSE_CHG_CACHE.pop(k, None)
    ent = _CLOSE_CHG_CACHE.get(date)
    if ent is None or now - ent[0] > _CLOSE_CHG_TTL:
        ent = (now, {})
        _CLOSE_CHG_CACHE[date] = ent
    table = ent[1]
    today_bj = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    # 收盘自愈(2026-08-24): 盘中旧代码把"竞价涨幅"误当"当日收盘涨幅"写入 close_change_history,
    # 导致收盘/历史回看时现涨=竞涨。针对"今天且已收盘"一次性强制重拉纠正脏值(去重, 之后走库/缓存)。
    force_resync = (date == today_bj and _close_chg_persist_allowed(date)
                    and date not in _CLOSE_CHG_RESYNCED)
    # 1) 缺的 code 先查库命中
    todo = [it for it in lst if it.get("code") and it.get("code") not in table]
    if todo:
        dbhit = _close_chg_db_get(date, [it["code"] for it in todo])
        for it in todo:
            v = dbhit.get(it["code"])
            if v is not None:
                table[it["code"]] = v
        todo = [it for it in todo if it["code"] not in table]
    # 收盘自愈(2026-08-24): 今日盘中旧代码误写的脏"收盘涨幅"=竞价涨幅, 收盘后强制纠正。
    # 优先用批量实时行情(单次分页拉全市场, 收盘后其"实时涨幅"即当日收盘涨幅, 避免逐只日K→限流熔断);
    # 未命中批量行情的才落到逐只多源日K兜底。
    fetched = {}
    if force_resync:
        todo = [it for it in lst if it.get("code")]
        if todo:
            try:
                from ..services import fetcher as _fet, scorer as _sco
                spot = _fet.fetch_spot_quote_map(_sco.market_fs(["hs", "cyb", "kcb"]))
                got = 0
                for it in todo:
                    q = spot.get(str(it["code"])) if spot else None
                    if not q:
                        continue
                    rc = q.get("realChange")
                    if rc is None:
                        rc = q.get("change")
                    if rc is not None:
                        val = float(rc) if rc else 0.0
                        table[it["code"]] = val
                        fetched[it["code"]] = val
                        got += 1
                log.info("收盘自愈: 批量实时行情纠正今日收盘涨幅 %d 只 date=%s", got, date)
            except Exception as e:
                log.warning("收盘自愈 批量行情失败(转逐只日K兜底) date=%s err=%s", date, e)
        todo = [it for it in todo if it["code"] not in table]
        _CLOSE_CHG_RESYNCED.add(date)
    # 2) 仍缺的才拉多源日K, 并写库持久化(任一源命中即写入；收盘自愈命中批量行情的也已写库)

    def _one(it):
        code = it.get("code") or ""
        try:
            # 2a) robust chart (东财→腾讯→tushare→同花顺→开盘啦)
            k = fetcher.fetch_stock_chart_robust(code, "day")
            if k and k.get("time"):
                times, closes = k["time"], k["close"]
                for i, t in enumerate(times):
                    if str(t)[:10] == date:
                        if i > 0 and closes[i - 1]:
                            v = round((closes[i] - closes[i - 1]) / closes[i - 1] * 100, 2)
                            table[code] = v
                            fetched[code] = v
                            return
            # 2b) 新浪日K兜底
            v = _close_chg_pct_sina(date, code)
            if v is not None:
                table[code] = v
                fetched[code] = v
                return
            # 2c) 腾讯日K兜底
            v = _close_chg_pct_tencent(date, code)
            if v is not None:
                table[code] = v
                fetched[code] = v
                return
            # 2d) 同花顺日K兜底
            v = _close_chg_pct_ths(date, code)
            if v is not None:
                table[code] = v
                fetched[code] = v
                return
        except Exception:
            pass

    if todo:
        import concurrent.futures as cf
        # 2026-08-31 线上事故修复: 数据源(东财/同花顺)熔断抖动时, 多源日K逐只兜底无整体超时,
        # 曾导致三时点榜接口卡 693s 占死全部 worker → 全站刷不出数据。
        # 现在整体超时 15s: 超时未完成的放弃(不阻塞当前请求), 已提交任务留在常驻池排队。
        # 2026-09-01: 线程爆炸修复 — 由每次新建池+shutdown(wait=False) 改进程级常驻池 _EXECUTOR_FILL
        _FILL_TIMEOUT = 15
        ex = _EXECUTOR_FILL
        futs = [ex.submit(_one, it) for it in todo]
        try:
            for f in cf.as_completed(futs, timeout=_FILL_TIMEOUT):
                pass
        except cf.TimeoutError:
            log.warning("现涨K线兜底整体超时 %ds, 放弃剩余 %d 只 (数据源抖动, 下次回看自动补齐)",
                        _FILL_TIMEOUT, sum(1 for f in futs if not f.done()))
    # 收盘自愈修复(2026-08-24): 批量实时行情已把纠正值写入 fetched 并把 todo 清空,
    # 持久化必须放在 if todo 之外, 保证批量命中的纠正值也能写回库表。
    if fetched and _close_chg_persist_allowed(date):
        _close_chg_db_put(date, fetched)
    n = 0
    for it in lst:
        v = table.get(it.get("code"))
        if v is None:
            continue
        it["change"] = v
        it["realChange"] = v
        it["real_change"] = v   # 2026-08-24: 统一回填 real_change(三时点表展示 key)
        n += 1
    return n
