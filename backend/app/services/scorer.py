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
# 五项因子权重和应=1.0; 改动经管理端 PUT /api/admin/scoring 保存后即时生效
DEFAULT_SCORING = {
    "w_bid": 0.34,        # 竞价分权重
    "w_activity": 0.32,   # 活跃度(竞价换手/量比)权重
    "w_warn": 0.17,       # 异动(封单/抢筹)权重
    "w_market": 0.11,     # 流通市值权重
    "w_yesterday": 0.06,  # 昨日涨幅权重
    "conf_warn_high": 10,  # 置信度: 强异动加成
    "conf_turnover": 8,    # 置信度: 高换手加成
    "conf_bid": 7,         # 置信度: 竞价温和区间加成
}
_scoring_cfg = None


def get_scoring_cfg(force=False):
    """读取评分权重(内存缓存; 管理端更新后调 reload 生效)"""
    global _scoring_cfg
    if _scoring_cfg is None or force:
        cfg = settings.get("scoring")
        if isinstance(cfg, dict):
            merged = dict(DEFAULT_SCORING)
            for k, v in cfg.items():
                if k in DEFAULT_SCORING:
                    try:
                        merged[k] = float(v)
                    except (TypeError, ValueError):
                        pass
            _scoring_cfg = merged
        else:
            _scoring_cfg = dict(DEFAULT_SCORING)
    return _scoring_cfg


def reload_scoring_cfg():
    """管理端更新权重后强制刷新内存缓存, 返回新配置"""
    return get_scoring_cfg(force=True)


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
    return get_warn_type(s) >= 5


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

    # 竞价分
    bid_score = 0.15
    if 3.0 <= bid_change <= 5.5:
        bid_score = 1.0
    elif bid_change >= 2:
        bid_score = 0.88
    elif bid_change >= 1.5:
        bid_score = 0.65
    elif bid_change > 0:
        bid_score = 0.4
    else:
        bid_score = 0.1

    # 活跃度分
    activity_score = 0.2
    if bid_turnover >= 0.8:
        activity_score = 1.0
    elif bid_turnover >= 0.4:
        activity_score = 0.88
    elif bid_turnover >= 0.2:
        activity_score = 0.72
    elif bid_turnover >= 0.08:
        activity_score = 0.5
    elif bid_turnover > 0:
        activity_score = 0.3
    else:
        activity_score = 0.1
    if bid_vol_ratio >= 0.3:
        activity_score = min(1.0, activity_score + 0.1)

    # 异动分
    warn_score = 1.0 if warn_type == 5 else (0.85 if warn_type == 4 else (0.6 if warn_type == 3 else 0.18))

    # 市值分
    if circ_mv < 30:
        market_score = 1.0
    elif circ_mv < 60:
        market_score = 0.88
    elif circ_mv < 120:
        market_score = 0.68
    elif circ_mv < 250:
        market_score = 0.45
    else:
        market_score = 0.22

    # 昨日涨幅分
    if 3 <= yesterday_approx < 9.5:
        yesterday_score = 0.9
    elif yesterday_approx >= 1:
        yesterday_score = 0.65
    elif yesterday_approx >= 0:
        yesterday_score = 0.4
    elif yesterday_approx > -3:
        yesterday_score = 0.25
    else:
        yesterday_score = 0.15

    cfg = get_scoring_cfg()
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


def process_all_stocks(raw, f, yesterday_map=None):
    """yesterday_map: code -> 昨日成交额(万元), 用于计算竞价成交额占比"""
    yesterday_map = yesterday_map or {}
    scored = []
    for s in raw:
        sc = compute_score(s)
        bid_amt = get_bid_amt(s)   # 万元
        y_amt = yesterday_map.get(s.get("f12"))
        bid_ratio = round(bid_amt / y_amt * 100, 2) if y_amt else None   # 竞价/昨日成交额占比(%)
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
            "bidRatio": bid_ratio,      # 竞价成交额/昨日成交额 (%)
            "price": parse_float(s.get("f2")),
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
