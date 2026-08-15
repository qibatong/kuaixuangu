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

from ..core import config, logger

log = logger.get_logger(__name__)

# ---------- 健康监控 ----------
_HEALTH = {
    "kpl": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
}
_health_lock = threading.Lock()

# ---------- 内存缓存: key -> {"data": ..., "ts": epoch} ----------
_cache = {}
_cache_lock = threading.Lock()

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


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


def _cached(key, ttl, loader):
    """带缓存的读取: TTL 内命中直接返回, 否则调 loader 刷新"""
    now = time.time()
    with _cache_lock:
        ent = _cache.get(key)
        if ent and now - ent["ts"] < ttl:
            return ent["data"]
    data = loader()
    if data is not None:
        with _cache_lock:
            _cache[key] = {"data": data, "ts": time.time()}
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
                "floatMv": _f(row[12]),          # 实际流通(元)
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


def fetch_bid_boom():
    """竞价爆量/竞价成交额榜(实时): Type=10; 涨停委买额从 Type=4 榜单按代码合并补充"""
    def loader():
        d = _call("after", {"Order": "1", "a": "MorningBiddingList", "st": "60",
                            "c": "HomeDingPan", "Index": "0", "PidType": "1",
                            "apiv": "w44", "Type": "10"})
        lst = _parse_bid_boom(d) if d else None
        if lst is None:
            return None
        # Type=10 无委买额(恒0), 用 Type=4(涨停委买额榜, st=200) 按代码补齐
        try:
            seal_map = {s["code"]: s.get("bidSealAmt") or 0 for s in (fetch_bid_seal() or [])}
        except Exception:
            seal_map = {}
        for it in lst:
            it["bidSealAmt"] = seal_map.get(it["code"], 0)
        return lst
    return _cached("bid_boom", config.KPL_BID_TTL, loader)


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
                "floatMv": _f(row[12]),
                "mainBuy": _f(row[13]),
                "mainSell": _f(row[14]),
                "mainNet": _f(row[15]),
                "limitBoards": _lb(str(row[16])) if len(row) > 16 else 0,
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


# ==================== 市场情绪 ====================
def fetch_sentiment():
    """情绪值/连板高度: {ztjs 涨停家数, strong 情绪, lbgd 连板高度, df_num 大幅回撤}"""
    def loader():
        d = _call("market", {"a": "ChangeStatistics", "st": "10", "c": "HomeDingPan"})
        if not d:
            return None
        info = d.get("info")
        if isinstance(info, list) and info and isinstance(info[0], dict):
            r = info[0]
            return {
                "ztCount": int(_num(r.get("ztjs"))),      # 涨停家数
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
                "floatMv": _f(row[13]),                    # 实际流通(元)
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
        if not lst or day:      # 历史日不做连板补全(无昨日池语义)
            return lst
        # 今日炸板: 接口 limit_up_days 常为0, 用昨日涨停池补连板数(昨日N板 → 今日炸板显示N板)
        yest_day = _prev_trade_day()
        yest_map = {}
        if yest_day:
            yest_map = {x["code"]: x["limitUpDays"]
                        for x in _flash_pool("limit_up_pool", yest_day)}
        for it in lst:
            if not it.get("limitUpDays") and it["code"] in yest_map:
                it["limitUpDays"] = yest_map[it["code"]]
        return lst
    return _cached(cache_key, (30 * 60) if is_hist else 30, loader)


# ==================== 昨日涨停(flash 涨停池 + 今日竞价表现) ====================
def _seal_map():
    """竞价委买榜 code → 完整行(概念/流通/换手/净额/连板), 用于字段补全"""
    try:
        return {s["code"]: s for s in (fetch_bid_seal() or [])}
    except Exception:
        return {}


def _snap25_map():
    """今日 9_25 全市场快照 code → {bid_change, bid_amt, name, float_mv, board}
    (全市场5549只, 字段补全兜底)"""
    import sqlite3
    out = {}
    try:
        conn = sqlite3.connect(config.DB_FILE)
        for r in conn.execute(
                "SELECT code, bid_change, bid_amt, name, float_mv, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (time.strftime("%Y-%m-%d"),)):
            out[r[0]] = {"bid_change": r[1], "bid_amt": r[2], "name": r[3] or "",                         "float_mv": r[4] or 0, "board": r[5] or ""}
        conn.close()
    except Exception as e:
        log.warning("9_25快照查询失败(降级) err=%s", e)
    return out


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
    return _cached("board_map", 30, loader)


def fetch_yest_zt():
    """昨日涨停股今日竞价表现: flash limit_up_pool&date=上一交易日(95只) + merge Type4
    字段补全: Type4(今日竞价涨停榜)优先 → snapshot_bid 9_25(全市场)兜底
    返回 [{code,name,yestChange,limitUpDays,stillLimit,change,bidChange,bidNetAmt,bidAmt,
           bidTurnover,floatMv,board}, ...]"""
    def loader():
        day = _prev_trade_day()
        if not day:
            return []
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
            float_mv = s.get("floatMv") or (sn.get("float_mv") if sn else None)
            # 竞价换手: Type4 真值优先, 无则 竞价额/流通市值 近似(与短线侠 0.1-0.4% 量级一致)
            bid_turnover = s.get("bidTurnover")
            if not bid_turnover and bid_amt and float_mv:
                bid_turnover = round(bid_amt / float_mv * 100, 2)
            out.append({
                "code": code,
                "name": it["name"],
                "yestChange": it["change"],              # 昨日涨停涨幅
                "limitUpDays": it["limitUpDays"],        # 昨日连板数
                "stillLimit": code in today_codes,       # 今日是否仍涨停(连板)
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
    """昨断板: 昨日涨停池中今日未涨停的股票(今日竞价表现从 snapshot_bid 9:25 全市场补)
    返回 [{code,name,yestChange,limitUpDays,change,bidChange,bidAmt,bidNetAmt,bidTurnover,floatMv,board}, ...]"""
    def loader():
        day = _prev_trade_day()
        if not day:
            return []
        yest = _flash_pool("limit_up_pool", day)
        if not yest:
            return []
        today_codes = {x["code"] for x in _flash_pool("limit_up_pool")}
        broken = [x for x in yest if x["code"] not in today_codes]
        # 今日竞价快照(9_25 全市场)补: 涨幅/竞额/概念
        snap = _snap25_map()
        seal_map = _seal_map()
        out = []
        for it in broken:
            code = it["code"]
            s = snap.get(code, {})
            t4 = seal_map.get(code, {})
            bid_amt = (s["bid_amt"] * 10000) if s and s.get("bid_amt") else None
            float_mv = t4.get("floatMv") or (s.get("float_mv") if s else None)
            # 竞价换手: Type4 真值优先, 无则 竞价额/流通市值 近似
            bid_turnover = t4.get("bidTurnover")
            if not bid_turnover and bid_amt and float_mv:
                bid_turnover = round(bid_amt / float_mv * 100, 2)
            out.append({
                "code": code,
                "name": t4.get("name") or s.get("name") or it["name"],
                "yestChange": it["change"],              # 昨日涨停涨幅
                "limitUpDays": it["limitUpDays"],        # 昨日连板数
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
    seq = [(ts, bid_change, bid_amt), ...] 按 ts 升序(9:24:55-9:25:03 每秒采样)
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
                   抢筹强度 qcDelta = 竞价净额 / 流通市值 * 100 (开盘啦自带"抢筹资金"指标)
                   过滤: 流通市值≥2亿, 抢筹强度>5%
                   竞价时段(9:15-9:30)实时拉取并持久化 qc_snapshot 表;
                   非竞价时段接口为空 → 读库展示今天已选出的结果(不丢失)
    右表 listLast= snapshot_bid 9_24(最后一秒≈9:24:5x) → 9_25 段: 抢筹幅度 = 9:25涨幅 − 9:24涨幅
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
                        floatMv = float(s.get("floatMv") or 0)          # 流通市值(元) - 开盘啦 row[12]
                        bidAmt = float(s.get("bidAmt") or 0)            # 竞价成交额(元) - 开盘啦 row[8]
                        if floatMv < 2e8 or bidNetAmt <= 0:             # 放宽阈值 5亿→2亿, 纳入中盘股
                            continue
                        qcDelta = round(bidNetAmt / floatMv * 100, 2)   # 抢筹强度%(开盘啦自家口径)
                        if qcDelta <= 5:
                            continue
                        list20.append({
                            "code": code,
                            "name": str(s.get("name", "")),
                            "realChange": float(s.get("realChange") or 0),
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
                                "(可能: 全部 qcDelta<=5 或 流通市值<2亿 或 bidNetAmt=0, 需检查阈值口径)",
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
                "SELECT code, bid_change, bid_amt, float_mv, name, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (today,)).fetchall()
            conn.close()
            m20c = {r[0]: r[1] for r in rows20c}
            seal_map = {} if date else _seal_map()   # 历史日期不拉今天 Type4(字段用快照自身)
            for code, chg25, amt25, fmv, name, board in rows25c:
                # 过滤: 流通市值≥2亿, 竞价额>0, 竞价成交额≥500万, 竞价涨幅>2%(9_25涨幅)
                if fmv < 2e8 or amt25 <= 0 or amt25 < 500 or chg25 <= 2:
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
                list20Chg.append({
                    "code": code,
                    "name": name or t4.get("name", ""),
                    "realChange": t4.get("realChange") if t4.get("realChange") is not None else chg25,
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
        # 无秒级数据时回退 9_24 时点(9:24:5x 重采型)
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
                "SELECT code, bid_change, bid_amt, float_mv, name FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (today,)).fetchall()
            conn.close()
            seq = {}
            for code, chg, amt, ts in rows_ls:
                seq.setdefault(code, []).append((ts, chg, amt))
            if not rows_ls:
                log.warning("抢筹[listLast] date=%s %s snapshot_lastsec=0条(9:24:55-9:25:03高频采样缺失!), "
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
                    if fmv <= 0 or amt25 <= 0 or fmv < 5e8:
                        continue
                    t4 = seal_map.get(code, {})
                    base = {
                        "code": code,
                        "name": name or t4.get("name", ""),
                        "realChange": t4.get("realChange") or chg,
                        "bidAmt": amt25 * 10000,
                        "bidChange": chg,
                        "bidTurnover": t4.get("bidTurnover"),
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
                    # ② 兜底: 9_24 时点(9:24:5x 重采型)
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
        try:
            from . import fetcher as _fetcher
            codes = []
            for it in list20 + list20Chg + listLast:
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
        return {"list20": list20[:100], "list20Chg": list20Chg[:100], "listLast": listLast[:100]}
    return _cached("bid_qiangcang" + (("_" + date.replace("-", "")) if date else ""), 30, loader)


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


def fetch_stock_plate(code):
    """\u4e2a\u80a1\u5168\u90e8\u76f8\u5173\u6982\u5ff5\u677f\u5757(\u5f00\u76d8\u5566 doc94 GetStockIDPlate):
    \u8fd4\u56de\u62fc\u63a5\u7684\u677f\u5757\u5b57\u7b26\u4e32(\u5982 "\u673a\u5668\u4eba\u6982\u5ff5\u3001\u80a1\u6743\u8f6c\u8ba9\u3001\u6c7d\u8f66\u96f6\u90e8\u4ef6"), \u5931\u8d25\u8fd4\u56de ""
    \u6309\u80a1\u7f13\u5b58 1 \u5929(\u677f\u5757\u5f52\u5c5e\u53d8\u52a8\u4f4e), \u5927\u5e45\u51cf\u5c11 KPL \u8c03\u7528\u6b21\u6570
    \u9009\u80a1\u7ed3\u679c 39 \u53ea \xd7 30s \u7f13\u5b58\u5237\u65b0 \u2192 \u9996\u6b21 39 \u6b21, \u4e4b\u540e\u547d\u4e2d"""
    key = "stock_plate_" + str(code)
    def loader():
        d = fetch_kpl_doc94(StockID=str(code))
        if not d:
            return ""
        lst = d.get("ListJX") or []
        names = []
        for it in lst:
            if isinstance(it, list) and len(it) >= 2 and it[1]:
                nm = str(it[1]).strip()
                if nm:
                    names.append(nm)
        return "\u3001".join(names)
    return _cached(key, 86400, loader)  # 1 \u5929\u7f13\u5b58, \u677f\u5757\u5f52\u5c5e\u7a33\u5b9a


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
def save_auction_history(date):
    """抓当日竞价异动各 tab 落库 auction_daily_history(15:30 调度调用)
    tab: seal(竞价委买)/boom(竞价爆量)/qiangcang(抢筹list20)/
         yest_zt(昨日涨停)/yest_broken(昨断板)/broken_yest(昨炸板)/broken_today(今炸板)
    返回落库 tab 数; 某 tab 抓取失败不影响其他"""
    import sqlite3 as _sql
    items = [
        ("seal", fetch_bid_seal()),
        ("boom", fetch_bid_boom()),
        ("qiangcang", (fetch_bid_qiangcang() or {}).get("list20", [])),
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
            conn = _sql.connect(config.DB_FILE)
            conn.execute(
                "INSERT OR REPLACE INTO auction_daily_history (date, tab, list, ts) VALUES (?,?,?,?)",
                (date, tab, json.dumps(lst, ensure_ascii=False), int(time.time())))
            conn.commit()
            conn.close()
            n += 1
        except Exception as e:
            log.warning("竞价异动快照落库失败 date=%s tab=%s err=%s", date, tab, e)
    log.info("竞价异动日终快照 date=%s 落库 %d 个 tab", date, n)
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
