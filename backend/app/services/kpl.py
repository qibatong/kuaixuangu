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
        d = _call("default", {"Order": "1", "a": "MorningBiddingList", "st": "200",
                              "c": "HomeDingPan", "Index": "0", "PidType": "0",
                              "apiv": "w41", "Type": "4"})
        return _parse_bid_seal(d) if d else None
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
    """盘中人气热榜: List [[code,name,?,涨跌幅,排名,?,?], ...]"""
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
                    "change": _f(row[3]),        # 涨跌幅(%)
                    "rank": int(_num(row[4])),   # 人气排名
                })
            except (IndexError, ValueError, TypeError):
                continue
        return out
    return _cached("hot_rank", 60, loader)


# ==================== 龙虎榜 ====================
def fetch_lhb():
    """龙虎榜上榜股票(当天): [{code,name,change,limitBoards,buyIn,amount,floatMv,turnover,amplitude,totalMv,joinNum}, ...]"""
    def loader():
        d = _call("lhb", {"a": "GetStockList", "st": "500", "c": "LongHuBang",
                          "Time": "", "Index": "0", "apiv": "w44", "Type": "2"})
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
    return _cached("lhb", 120, loader)


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


# ==================== 炸板(东财 flash 公开接口, 无需 Token) ====================
_BROKEN_URL = "https://flash-api.xuangubao.cn/api/pool/detail?pool_name=limit_up_broken"


def fetch_broken_zt():
    """炸板实时列表(东财 flash): [{code,name,change,limitUpDays,breakTimes,firstLimitUp,firstBreak}, ...]"""
    def loader():
        try:
            req = urllib.request.Request(_BROKEN_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            log.warning("炸板接口失败 err=%s", e)
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
                "code": sym.split(".")[0],                        # 去掉 .SZ/.SS 后缀
                "name": str(it.get("stock_chi_name", "") or ""),  # 名称字段是stock_chi_name
                "change": _f(it.get("change_percent")) * 100,          # 涨幅(%)
                "limitUpDays": int(_num(it.get("limit_up_days"))),     # 连板数
                "breakTimes": int(_num(it.get("break_limit_up_times"))),  # 炸板次数
                "firstLimitUp": int(_num(it.get("first_limit_up"))),   # 首次涨停时间戳
                "firstBreak": int(_num(it.get("first_break_limit_up"))),  # 首次炸板时间戳
                "reason": _surge_reason(it.get("surge_reason")),       # 涨停原因
            })
        return out
    return _cached("broken_zt", 30, loader)


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
