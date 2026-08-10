# -*- coding: utf-8 -*-
"""
评分与筛选服务: 选股算法(核心机密, 只在服务端)
==============================================
"""
import math
import re
import time

from . import settings

# ---------- 评分权重配置(管理员可调, 存 settings 表 "scoring") ----------
# 结构: 五项因子权重 + 各因子打分明细(buckets: [下限, 上限, 得分], 左闭右开) + 置信度加成
# 默认值与重构前硬编码逻辑完全一致, 行为零变化
DEFAULT_SCORING = {
    "w_bid": 0.34,        # 竞价分权重
    "w_activity": 0.32,   # 活跃度(竞价换手/量比)权重
    "w_warn": 0.17,       # 异动(封单/抢筹)权重
    "w_market": 0.11,     # 流通市值权重
    "w_yesterday": 0.06,  # 昨日涨幅权重
    "conf_warn_high": 10,  # 置信度: 强异动加成
    "conf_turnover": 8,    # 置信度: 高换手加成
    "conf_bid": 7,         # 置信度: 竞价温和区间加成
    # 各因子打分明细: buckets 为 [下限, 上限, 得分] 列表, 命中条件 下限<=x<上限, 按顺序首个命中
    "factors": {
        "bid": {
            "label": "竞价涨幅", "unit": "%",
            "buckets": [["3", "5.5", 1.0], ["5.5", "99", 0.88], ["2", "3", 0.88],
                        ["1.5", "2", 0.65], ["0.001", "1.5", 0.4]],
            "default": 0.1,
        },
        "activity": {
            "label": "竞价换手率", "unit": "%",
            "buckets": [["0.8", "99", 1.0], ["0.4", "0.8", 0.88], ["0.2", "0.4", 0.72],
                        ["0.08", "0.2", 0.5], ["0.001", "0.08", 0.3]],
            "default": 0.1,
        },
        "warn": {
            "label": "异动等级", "unit": "级",
            "buckets": [["5", "6", 1.0], ["4", "5", 0.85], ["3", "4", 0.6]],
            "default": 0.18,
        },
        "market": {
            "label": "流通市值", "unit": "亿",
            "buckets": [["0", "30", 1.0], ["30", "60", 0.88], ["60", "120", 0.68],
                        ["120", "250", 0.45]],
            "default": 0.22,
        },
        "yesterday": {
            "label": "昨日涨幅", "unit": "%",
            "buckets": [["3", "9.5", 0.9], ["9.5", "99", 0.65], ["1", "3", 0.65],
                        ["0", "1", 0.4], ["-3", "0", 0.25]],
            "default": 0.15,
        },
    },
}
_scoring_cfg = None


def get_scoring_cfg(force=False):
    """读取评分配置(权重+打分明细; 内存缓存; 管理端更新后调 reload 生效)"""
    global _scoring_cfg
    if _scoring_cfg is None or force:
        cfg = settings.get("scoring")
        if isinstance(cfg, dict):
            merged = dict(DEFAULT_SCORING)
            num_keys = ("w_bid", "w_activity", "w_warn", "w_market", "w_yesterday",
                        "conf_warn_high", "conf_turnover", "conf_bid")
            for k, v in cfg.items():
                if k in num_keys:
                    try:
                        merged[k] = float(v)
                    except (TypeError, ValueError):
                        pass
            # factors 逐层合并, 缺省因子/分档用默认
            if isinstance(cfg.get("factors"), dict):
                fac = dict(DEFAULT_SCORING["factors"])
                for fk, fv in cfg["factors"].items():
                    if fk in fac and isinstance(fv, dict):
                        fac[fk] = dict(fac[fk], **fv)
                merged["factors"] = fac
            _scoring_cfg = merged
        else:
            _scoring_cfg = dict(DEFAULT_SCORING)
    return _scoring_cfg


def reload_scoring_cfg():
    """管理端更新配置后强制刷新内存缓存, 返回新配置"""
    return get_scoring_cfg(force=True)


def get_factor_score(cfg, factor, value):
    """按配置分档表打分: 命中 [下限, 上限) 返回得分, 未命中返回 default"""
    f = (cfg.get("factors") or {}).get(factor)
    if not f or not f.get("buckets"):
        return 0.1
    for b in f["buckets"]:
        try:
            lo, hi, sc = float(b[0]), float(b[1]), float(b[2])
        except (TypeError, ValueError, IndexError):
            continue
        if lo <= value < hi:
            return sc
    try:
        return float(f.get("default", 0.1))
    except (TypeError, ValueError):
        return 0.1


# ---------- 工具 ----------
def parse_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def js_round(x):
    """复刻 JS Math.round 语义(正数)"""
    return int(math.floor(x + 0.5))


def bj_now():
    """返回是否在北京时间 9:30 之前(服务器时区无关, 用 UTC+8 计算)"""
    t = time.gmtime(time.time() + 8 * 3600)
    before930 = (t.tm_hour < 9) or (t.tm_hour == 9 and t.tm_min < 30)
    return t.tm_hour, t.tm_min, before930


def in_auction_window():
    """是否处于竞价数据窗口: 工作日 9:15-9:31(北京时间)。
    此时 f616(竞价成交额)为当日真实竞价额, bidRatio=当日竞价/昨日全天 语义正确;
    非窗口(收盘后/周末/盘前) f616 会退回最近交易日数据, 再算会变成"同日自比"误导, 故返回 None。
    """
    t = time.gmtime(time.time() + 8 * 3600)
    if t.tm_wday >= 5:               # 周六/周日
        return False
    hm = t.tm_hour * 60 + t.tm_min
    return 9 * 60 + 15 <= hm < 9 * 60 + 31


def market_fs(markets):
    """根据市场范围生成东财 fs 参数"""
    if not markets:
        return "m:1+t:2,m:0+t:6"
    parts = []
    for m in markets:
        if m == "hs":
            parts.append("m:1+t:2")
            parts.append("m:0+t:6")
        elif m == "cyb":
            parts.append("m:0+t:80")
        elif m == "kcb":
            parts.append("m:1+t:23")
    return ",".join(dict.fromkeys(parts))   # 去重且保持顺序


# ---------- 行情字段提取 ----------
def get_bid_change(s):
    """竞价涨幅: f615 优先, 缺失/异常退回 f3"""
    v = s.get("f615")
    if v is not None:
        try:
            f = float(v)
            if not math.isnan(f):
                return f
        except (TypeError, ValueError):
            pass
    return parse_float(s.get("f3"))


def get_entity_change(s):
    """实体涨幅: 今开 f17 -> 现价 f2"""
    o = parse_float(s.get("f17"))
    c = parse_float(s.get("f2"))
    return 0.0 if o == 0 else (c - o) / o * 100


def get_bid_turnover(s):
    """竞价换手率估算"""
    bv = parse_float(s.get("f5"))
    mv = parse_float(s.get("f21"))
    p = parse_float(s.get("f2"))
    if mv <= 0 or p <= 0 or bv <= 0:
        return 0.0
    t = (bv * 100 * p) / mv * 100
    return 0.0 if not math.isfinite(t) else t


def get_bid_amt(s):
    """竞价成交额(万元): f616 固定竞价额优先, 缺失退回 f6"""
    amt = parse_float(s.get("f616"))
    if not amt > 0:
        amt = parse_float(s.get("f6"))
    return 0.0 if (not math.isfinite(amt) or amt <= 0) else amt / 10000


def get_warn_type(s):
    return int(parse_float(s.get("f630")))


def is_first_board(s):
    """昨日涨停判断: f103 概念标签含 昨日涨停/昨日连板(含一字)
    (原实现用 f630>=5, 但实测 f630 取值只有 0/1/2, 该条件永不成立, 过滤从未生效)"""
    concept = s.get("f103") or ""
    return ("昨日涨停" in concept) or ("昨日连板" in concept)


def limit_pct(code, name, pre_close):
    """涨停幅度: ST 5% / 创业板·科创板 20% / 主板 10%"""
    if "ST" in (name or ""):
        return 0.05
    if (code or "").startswith(("300", "301", "688")):
        return 0.20
    return 0.10


def is_yizi(s):
    """一字涨停: 开盘价 f17 直接封在涨停价(交易所四舍五入到分)"""
    f17 = parse_float(s.get("f17"))
    f18 = parse_float(s.get("f18"))
    if f17 <= 0 or f18 <= 0:
        return False
    pct = limit_pct(s.get("f12", ""), s.get("f14", ""), f18)
    limit_price = int(f18 * (1 + pct) * 100 + 0.5) / 100.0   # 四舍五入到分
    return f17 >= limit_price - 0.005


def is_st(name):
    return "ST" in name or "*ST" in name


def is_suspended(s):
    """f4<=0 或 f5(成交量)==0 视为停牌/无成交"""
    return parse_float(s.get("f4")) <= 0 or parse_float(s.get("f5")) == 0


# ---------- 评分 ----------
def compute_score(s):
    bid_change = get_bid_change(s)
    bid_turnover = get_bid_turnover(s)
    bid_vol_ratio = 0.0
    warn_type = get_warn_type(s)
    circ_mv = parse_float(s.get("f21")) / 1e8          # 流通市值(亿)
    yesterday_approx = parse_float(s.get("f3"))        # 原策略的"昨日涨幅"口径: f3

    # 竞价分(按配置分档表)
    cfg = get_scoring_cfg()
    bid_score = get_factor_score(cfg, "bid", bid_change)

    # 活跃度分(竞价换手 + 量比加成)
    activity_score = get_factor_score(cfg, "activity", bid_turnover)
    if bid_vol_ratio >= 0.3:
        activity_score = min(1.0, activity_score + 0.1)

    # 异动分
    warn_score = get_factor_score(cfg, "warn", warn_type)

    # 市值分
    market_score = get_factor_score(cfg, "market", circ_mv)

    # 昨日涨幅分
    yesterday_score = get_factor_score(cfg, "yesterday", yesterday_approx)

    base = (bid_score * cfg["w_bid"] + activity_score * cfg["w_activity"]
            + warn_score * cfg["w_warn"] + market_score * cfg["w_market"]
            + yesterday_score * cfg["w_yesterday"])
    prob = max(5.0, min(95.0, base * 100))

    conf = 65.0
    if warn_type >= 4:
        conf += cfg["conf_warn_high"]
    if bid_turnover >= 0.4:
        conf += cfg["conf_turnover"]
    if 2 <= bid_change <= 6.5:
        conf += cfg["conf_bid"]
    conf = min(90.0, max(55.0, conf))

    return {
        "probability": js_round(prob),
        "confidence": js_round(conf),
        "bidTurnover": bid_turnover,
        "bidVolRatio": bid_vol_ratio,
    }


# ---------- 过滤 ----------
def apply_filters(items, f):
    result = []
    for it in items:
        name = it["name"]
        bid_chg = it["bidChange"]
        prob, conf = it["probability"], it["confidence"]
        mv = it["circulationMV"]
        price = it["price"]
        bid_amt = it["bidAmt"]

        if f["stSuspend"]:
            if is_st(name):
                continue
            if is_suspended(it["_raw"]):
                continue
        if f["limitUp"] and is_first_board(it["_raw"]):
            continue
        if bid_chg > f["bidGt"]:
            continue
        if prob < f["probLt"] and conf < f["confLt"]:
            continue
        if mv < f["floatMvFloor"]:
            continue
        if mv > f["floatMvGt"]:
            continue
        if bid_amt < f["bidAmtFloor"]:
            continue
        if price > f["priceGt"]:
            continue
        result.append(it)
    return result


def is_qiangchou(bid_change, bid_ratio):
    """竞价抢筹信号: 竞价涨幅>=2% 且 竞价成交额/昨日成交额占比>=20%
    (资金在竞价阶段显著抢筹; 二期加入 9:20 加速度后再增强)"""
    if bid_ratio is None or bid_ratio < 20:
        return False
    return bid_change >= 2


def process_all_stocks(raw, f, yesterday_map=None, snapshot_map=None):
    """yesterday_map: code -> [T日全天额, T-1日全天额](万元), 用于计算竞价成交额占比
    snapshot_map: code -> {bid_change, bid_amt} (9:20 时点快照), 用于计算涨幅加速度"""
    yesterday_map = yesterday_map or {}
    snapshot_map = snapshot_map or {}
    scored = []
    # 竞价数据窗口内: f616=当日竞价额, 分母取最近交易日(T=昨日, 今天无日K)
    # 非窗口: f616=最近交易日竞价额, 分母取 T 的前一交易日(T-1), 避免"同日自比"
    auction_ok = in_auction_window()
    for s in raw:
        sc = compute_score(s)
        bid_amt = get_bid_amt(s)   # 万元
        pair = yesterday_map.get(s.get("f12"))   # [T日全天额, T-1日全天额] 万元
        y_amt = pair[0] if (auction_ok and pair) else (pair[1] if pair else None)
        bid_ratio = round(bid_amt / y_amt * 100, 2) if y_amt else None  # 竞价/前一交易日成交额占比(%)
        # 涨幅加速度: 9:25 竞价涨幅 - 9:20 竞价涨幅(最后5分钟抢筹; 仅竞价窗口内有意义)
        accel = None
        if auction_ok:
            snap = snapshot_map.get(s.get("f12"))
            if snap and snap.get("bid_change") is not None:
                accel = round(get_bid_change(s) - snap["bid_change"], 2)
        scored.append({
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "probability": sc["probability"],
            "confidence": sc["confidence"],
            "bidChange": get_bid_change(s),
            "realChange": parse_float(s.get("f3")),
            "entityChange": get_entity_change(s),
            "bidTurnover": sc["bidTurnover"],
            "bidVolRatio": sc["bidVolRatio"],
            "speed": parse_float(s.get("f8")),
            "warnType": get_warn_type(s),
            "circulationMV": parse_float(s.get("f21")) / 1e8,
            "industry": s.get("f100") or "-",
            "concept": s.get("f103") or "-",
            "province": s.get("f102") or "-",
            "bidAmt": bid_amt,          # 万元
            "bidRatio": bid_ratio,      # 竞价成交额/前一交易日成交额 (%)
            "accel": accel,             # 9:25-9:20 涨幅加速度(%)
            "price": parse_float(s.get("f2")),
            "qiangchou": 1 if is_qiangchou(get_bid_change(s), bid_ratio) else 0,
            "_raw": s,
        })
    scored.sort(key=lambda x: x["probability"], reverse=True)
    result = apply_filters(scored, f)
    for it in result:
        it.pop("_raw", None)
    return result


# ---------- 筛选参数校验 ----------
def _clamp(v, lo, hi, dflt):
    try:
        v = float(v)
        return lo if v < lo else (hi if v > hi else v)
    except (TypeError, ValueError):
        return dflt


def _truthy(v):
    return v not in ("0", "false", "False", "")


def _q_date(v, default):
    """校验 YYYY-MM-DD 格式日期, 非法返回默认值"""
    if v and re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        return v
    return default


def _opt_float(q, key):
    """可选的浮点参数, 缺失/非法返回 None (q 为 {k: [v,...]} 形式)"""
    v = (q.get(key) or [None])[0]
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_filters(q):
    raw_markets = (q.get("markets") or ["hs,cyb,kcb"])[0].split(",")
    markets = [m for m in raw_markets if m in ("hs", "cyb", "kcb")] or ["hs", "cyb", "kcb"]
    return {
        "stSuspend": _truthy((q.get("stSuspend") or ["1"])[0]),
        "limitUp": _truthy((q.get("limitUp") or ["1"])[0]),
        "markets": markets,
        "bidGt": _clamp((q.get("bidGt") or ["7"])[0], 0, 20, 7),
        "probLt": _clamp((q.get("probLt") or ["65"])[0], 5, 95, 65),
        "confLt": _clamp((q.get("confLt") or ["65"])[0], 50, 90, 65),
        "floatMvFloor": _clamp((q.get("floatMvFloor") or ["30"])[0], 1, 5000, 30),
        "floatMvGt": _clamp((q.get("floatMvGt") or ["100"])[0], 1, 5000, 100),
        "priceGt": _clamp((q.get("priceGt") or ["30"])[0], 1, 5000, 30),
        "bidAmtFloor": _clamp((q.get("bidAmtFloor") or ["3000"])[0], 0, 100000, 3000),
    }
