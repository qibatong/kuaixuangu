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
# ---------- 盘中实时选股评分权重(管理员可调, 存 settings 表 "scoring_spot") ----------
# 结构与竞价一致, 因子针对盘中实时数据: 实时涨幅/量比/换手/封单强度/市值/昨日涨幅
DEFAULT_SCORING_SPOT = {
    "w_chg": 0.28,        # 实时涨幅权重(健康涨幅区间优先, 过高=追高风险)
    "w_vol_ratio": 0.26,  # 量比权重(放量确认)
    "w_turnover": 0.18,   # 换手率权重(活跃度)
    "w_seal": 0.14,       # 封单强度权重(涨停股封成比; 非涨停=0分档)
    "w_market": 0.08,     # 流通市值权重
    "w_yesterday": 0.06,  # 昨日涨幅权重
    "conf_seal_high": 12, # 置信度: 强封单(封成比>=2%)加成
    "conf_vol_ratio": 8,  # 置信度: 显著放量(量比>=2)加成
    "conf_chg": 6,        # 置信度: 健康涨幅区间加成
    "factors": {
        "chg": {
            "label": "实时涨幅", "unit": "%",
            "buckets": [["3", "6", 1.0], ["1.5", "3", 0.85], ["6", "9.5", 0.85],
                        ["0", "1.5", 0.5], ["9.5", "99", 0.35], ["-99", "0", 0.15]],
            "default": 0.1,
        },
        "vol_ratio": {
            "label": "量比", "unit": "倍",
            "buckets": [["2", "99", 1.0], ["1.5", "2", 0.85], ["1", "1.5", 0.6],
                        ["0.5", "1", 0.35]],
            "default": 0.15,
        },
        "turnover": {
            "label": "换手率", "unit": "%",
            "buckets": [["3", "15", 1.0], ["1.5", "3", 0.8], ["15", "25", 0.7],
                        ["0.5", "1.5", 0.45], ["25", "99", 0.35]],
            "default": 0.15,
        },
        "seal": {
            "label": "封单强度(封成比)", "unit": "%",
            "buckets": [["2", "99", 1.0], ["1", "2", 0.85], ["0.5", "1", 0.65],
                        ["0.1", "0.5", 0.4]],
            "default": 0.12,
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
_scoring_spot_cfg = None


def get_scoring_cfg(force=False, mode="auction"):
    """读取评分配置(权重+打分明细; 内存缓存; 管理端更新后调 reload 生效)
    mode: "auction"=竞价 / "spot"=盘中实时, 各自独立配置"""
    global _scoring_cfg, _scoring_spot_cfg
    if mode == "spot":
        if _scoring_spot_cfg is None or force:
            cfg = settings.get("scoring_spot")
            if isinstance(cfg, dict):
                merged = dict(DEFAULT_SCORING_SPOT)
                num_keys = ("w_chg", "w_vol_ratio", "w_turnover", "w_seal",
                            "w_market", "w_yesterday",
                            "conf_seal_high", "conf_vol_ratio", "conf_chg")
                for k, v in cfg.items():
                    if k in num_keys:
                        try:
                            merged[k] = float(v)
                        except (TypeError, ValueError):
                            pass
                if isinstance(cfg.get("factors"), dict):
                    fac = dict(DEFAULT_SCORING_SPOT["factors"])
                    for fk, fv in cfg["factors"].items():
                        if fk in fac and isinstance(fv, dict):
                            fac[fk] = dict(fac[fk], **fv)
                    merged["factors"] = fac
                _scoring_spot_cfg = merged
            else:
                _scoring_spot_cfg = dict(DEFAULT_SCORING_SPOT)
        return _scoring_spot_cfg
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
    get_scoring_cfg(force=True)
    get_scoring_cfg(force=True, mode="spot")
    return get_scoring_cfg()


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


def get_bid_amt(s, auction_ok=True):
    """竞价成交额(万元): f616 固定竞价额优先。
    仅竞价窗口内缺失时退回 f6(此时 f6≈竞价额);
    非窗口(盘中/收盘) f6=累计成交额, 不可作竞价额 → 缺失直接返回 0。
    (修复: 盘中 f616 缺失时误用 f6 会把累计成交额当竞价额, 竞价/昨比可算出 1000%+ 荒谬值)"""
    amt = parse_float(s.get("f616"))
    if not amt > 0 and auction_ok:
        amt = parse_float(s.get("f6"))
    return 0.0 if (not math.isfinite(amt) or amt <= 0) else amt / 10000


def get_warn_type(s):
    return int(parse_float(s.get("f630")))


def is_first_board(s):
    """昨日涨停/连板判断。
    2026-09-07 改造(腾讯兜底期勾「昨涨停」筛空问题): 优先用 push2ex **昨涨停池名单**
    (fetcher.get_yesterday_zt_codes, 与数据源无关 —— 腾讯/量脉兜底行无 f103 概念字段,
    原实现只认 f103 导致兜底期恒 False 全滤空); 名单不可用(网络失败/非交易窗口返回 None)
    降级 f103 概念标签(东财行)。行内无 code 时同样降级。
    (历史: 原实现用 f630>=5, 但实测 f630 取值只有 0/1/2, 该条件永不成立, 过滤从未生效)"""
    code = s.get("f12") or s.get("code")
    if code:
        from . import fetcher as _f
        st = _f.get_yesterday_zt_codes()
        if st is not None:
            return code in st
    concept = s.get("f103") or ""
    return ("昨日涨停" in concept) or ("昨日连板" in concept)


def _in_markets(code, markets):
    """市场范围过滤(2026-09-07 修复: 腾讯兜底无视 fs 按全市场拉取 → 后端若只依赖
    raw 范围做市场过滤会整体失效, 主/创/科勾选不起作用)。此处按代码前缀在**评分层**
    兜底, 任何数据源(东财/腾讯/量脉)都生效:
      hs=沪主板60x + 深主板00x | cyb=300/301 | kcb=688/689
    北交所(4/8/9开头)不在 UI 选项, 与东财 market_fs 口径一致(不返回)"""
    if not markets:
        return True    # markets 未提供(旧调用方/测试直接构造) → 不限制市场
    code = str(code or "")
    if code.startswith(("300", "301")):
        return "cyb" in markets
    if code.startswith(("688", "689")):
        return "kcb" in markets
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003", "301")):
        return "hs" in markets
    return False    # 北交所等不在 UI 选项 → 一律排除


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

    # 评分构成(2026-08-17): 五因子分项明细, 前端展示"评分原因"增强可信度
    def _r2(v):
        return None if v is None else round(v, 2)

    factors = {
        "bid": {"label": "竞价涨幅", "value": _r2(bid_change), "score": round(bid_score * 100), "weight": cfg["w_bid"]},
        "activity": {"label": "竞价换手", "value": _r2(bid_turnover), "score": round(activity_score * 100), "weight": cfg["w_activity"]},
        "warn": {"label": "异动等级", "value": _r2(warn_type), "score": round(warn_score * 100), "weight": cfg["w_warn"]},
        "market": {"label": "流通市值", "value": _r2(circ_mv), "score": round(market_score * 100), "weight": cfg["w_market"]},
        "yesterday": {"label": "昨日涨幅", "value": _r2(yesterday_approx), "score": round(yesterday_score * 100), "weight": cfg["w_yesterday"]},
    }

    return {
        "probability": js_round(prob),
        "confidence": js_round(conf),
        "bidTurnover": bid_turnover,
        "bidVolRatio": bid_vol_ratio,
        # 2026-08-31 主人要求: 评分构成(五因子分项+权重)属内部逻辑, 不对用户暴露, factors 不再返回
        # "factors": factors,
    }


# ---------- 盘中实时评分 ----------
def compute_score_spot(s, zt_info=None):
    """盘中实时评分: 因子=实时涨幅/量比/换手率/封单强度/市值/昨日涨幅。
    zt_info: 涨停池单股信息 {fund, lb, zbc, ...} 或 None(非涨停/无数据)。
    封单强度 = 封单额(亿) / 流通市值(亿) ×100 (封成比%), 非涨停股按 0 计。
    """
    real_chg = parse_float(s.get("f3"))          # 实时涨幅 %
    vol_ratio = parse_float(s.get("f10"))        # 量比
    turnover = parse_float(s.get("f8"))          # 换手率 %
    circ_mv = parse_float(s.get("f21")) / 1e8    # 流通市值(亿)
    yesterday_approx = real_chg                  # 与竞价口径一致: f3 代理
    fund = (zt_info or {}).get("fund") or 0      # 封单额(亿)
    seal_ratio = round(fund / circ_mv * 100, 2) if (fund > 0 and circ_mv > 0) else 0.0

    cfg = get_scoring_cfg(mode="spot")
    chg_score = get_factor_score(cfg, "chg", real_chg)
    vol_score = get_factor_score(cfg, "vol_ratio", vol_ratio)
    turn_score = get_factor_score(cfg, "turnover", turnover)
    seal_score = get_factor_score(cfg, "seal", seal_ratio)
    market_score = get_factor_score(cfg, "market", circ_mv)
    yesterday_score = get_factor_score(cfg, "yesterday", yesterday_approx)

    base = (chg_score * cfg["w_chg"] + vol_score * cfg["w_vol_ratio"]
            + turn_score * cfg["w_turnover"] + seal_score * cfg["w_seal"]
            + market_score * cfg["w_market"] + yesterday_score * cfg["w_yesterday"])
    prob = max(5.0, min(95.0, base * 100))

    conf = 65.0
    if seal_ratio >= 2:
        conf += cfg["conf_seal_high"]
    if vol_ratio >= 2:
        conf += cfg["conf_vol_ratio"]
    if 1.5 <= real_chg <= 6:
        conf += cfg["conf_chg"]
    conf = min(90.0, max(55.0, conf))

    # 评分构成(2026-08-17): 盘中六因子分项明细, 与竞价 factors 同结构
    def _r2(v):
        return None if v is None else round(v, 2)

    factors = {
        "chg": {"label": "实时涨幅", "value": _r2(real_chg), "score": round(chg_score * 100), "weight": cfg["w_chg"]},
        "vol_ratio": {"label": "量比", "value": _r2(vol_ratio), "score": round(vol_score * 100), "weight": cfg["w_vol_ratio"]},
        "turnover": {"label": "换手率", "value": _r2(turnover), "score": round(turn_score * 100), "weight": cfg["w_turnover"]},
        "seal": {"label": "封单强度", "value": _r2(seal_ratio), "score": round(seal_score * 100), "weight": cfg["w_seal"]},
        "market": {"label": "流通市值", "value": _r2(circ_mv), "score": round(market_score * 100), "weight": cfg["w_market"]},
        "yesterday": {"label": "昨日涨幅", "value": _r2(yesterday_approx), "score": round(yesterday_score * 100), "weight": cfg["w_yesterday"]},
    }

    return {
        "probability": js_round(prob),
        "confidence": js_round(conf),
        "sealRatio": seal_ratio,
        "sealFund": fund,
        "limitBoards": int((zt_info or {}).get("lb") or 0),
        "breakCount": int((zt_info or {}).get("zbc") or 0),
        # 2026-08-31 主人要求: 评分构成(五因子分项+权重)属内部逻辑, 不对用户暴露, factors 不再返回
        # "factors": factors,
    }


def process_spot_stocks(raw, f, zt_map=None):
    """盘中实时选股主流程: 评分 + 过滤 + 排序。
    zt_map: code -> 涨停池信息 {fund, lb, zbc, zdp}(fetcher.fetch_zt_pool 结果)"""
    zt_map = zt_map or {}
    scored = []
    for s in raw:
        zt = zt_map.get(s.get("f12"))
        sc = compute_score_spot(s, zt)
        scored.append({
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "probability": sc["probability"],
            "confidence": sc["confidence"],
            "realChange": parse_float(s.get("f3")),
            "volRatio": parse_float(s.get("f10")),
            "turnover": parse_float(s.get("f8")),
            "sealRatio": sc["sealRatio"],
            "sealFund": sc["sealFund"],
            "limitBoards": sc["limitBoards"],
            "breakCount": sc["breakCount"],
            # 2026-08-31 主人要求: 评分构成属内部逻辑, 不对用户暴露
            # "factors": sc["factors"],
            "speed": parse_float(s.get("f8")),
            "circulationMV": parse_float(s.get("f21")) / 1e8,
            "price": parse_float(s.get("f2")),
            "amount": parse_float(s.get("f6")) / 1e8,   # 成交额(亿)
            "industry": s.get("f100") or "-",
            "concept": s.get("f103") or "-",
            "bidChange": get_bid_change(s),
            "bidAmt": get_bid_amt(s),   # 竞价金额(万元), 盘中保留展示(9:25定格)
            "_raw": s,
        })
    scored.sort(key=lambda x: x["probability"], reverse=True)
    result = apply_spot_filters(scored, f)
    for it in result:
        it.pop("_raw", None)
    return result


# ---------- 盘中过滤 ----------
def apply_spot_filters(items, f):
    """盘中实时过滤: ST/停牌、昨日涨停、实时涨幅区间、量比下限、换手率区间、
    市值区间、价格上限、涨停封板剔除(可选)。"""
    result = []
    for it in items:
        name = it["name"]
        prob, conf = it["probability"], it["confidence"]
        mv = it["circulationMV"]
        price = it["price"]
        real_chg = it["realChange"]
        vol_ratio = it["volRatio"]
        turnover = it["turnover"]

        # 2026-09-07 主人确认语义: limitUp **勾选=把昨日涨停/连板股也包含进结果**,
        # 不勾=剔除这类票(注意: 不是"只看昨涨停" —— 勾选后结果仍含正常筛选的票,
        # 只是多出昨日涨停的票)。markets 下沉评分层: 腾讯兜底无视 fs 按全市场拉 raw,
        # 原实现靠 raw 范围过滤市场 → 兜底期 主/创/科 勾选整体失效。
        _mk_code = it.get("code") or (it.get("_raw") or {}).get("f12") or ""
        if _mk_code and not _in_markets(_mk_code, f.get("markets") or []):
            continue
        if not f["limitUp"] and is_first_board(it["_raw"]):
            continue
        if not f["stSuspend"]:
            if is_st(name):
                continue
            if is_suspended(it["_raw"]):
                continue
        if f["spotExcludeZT"] and it["limitBoards"] > 0:   # 剔除已涨停封板(买不进)
            continue
        if real_chg < f["chgFloor"] or real_chg > f["chgGt"]:
            continue
        if f["volRatioFloor"] > 0 and vol_ratio < f["volRatioFloor"]:
            continue
        if f["turnoverFloor"] > 0 and turnover < f["turnoverFloor"]:
            continue
        if f["turnoverGt"] > 0 and turnover > f["turnoverGt"]:
            continue
        if prob < f["probLt"] and conf < f["confLt"]:
            continue
        if mv < f["floatMvFloor"]:
            continue
        if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
            continue
        if f["priceGt"] > 0 and price > f["priceGt"]:
            continue
        result.append(it)
    return result


# ---------- 竞价过滤 ----------
def apply_filters(items, f):
    result = []
    for it in items:
        name = it["name"]
        bid_chg = it["bidChange"]
        prob, conf = it["probability"], it["confidence"]
        mv = it["circulationMV"]
        price = it["price"]
        bid_amt = it["bidAmt"]

        # 2026-08-25 语义反转(正逻辑): 同 apply_spot_filters, 见注释
        # 2026-09-07 主人确认: limitUp 勾选=包含昨涨停/连板股, 不勾=剔除(非"只看")
        _mk_code = it.get("code") or (it.get("_raw") or {}).get("f12") or ""
        if _mk_code and not _in_markets(_mk_code, f.get("markets") or []):
            continue
        if not f["limitUp"] and is_first_board(it["_raw"]):
            continue
        if not f["stSuspend"]:
            if is_st(name):
                continue
            if is_suspended(it["_raw"]):
                continue
        if bid_chg > f["bidGt"]:
            continue
        if prob < f["probLt"] and conf < f["confLt"]:
            continue
        if mv < f["floatMvFloor"]:
            continue
        if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
            continue
        if bid_amt < f["bidAmtFloor"]:
            continue
        if f["priceGt"] > 0 and price > f["priceGt"]:
            continue
        result.append(it)
    return result


def is_qiangchou(bid_change, bid_ratio):
    """竞价抢筹信号: 竞价涨幅>=2% 且 竞价成交额/昨日成交额占比>=20%
    (资金在竞价阶段显著抢筹; 二期加入 9:20 加速度后再增强)"""
    if bid_ratio is None or bid_ratio < 20:
        return False
    return bid_change >= 2


def score_all_stocks(raw, yesterday_map=None, snapshot_map=None, qiangchou_codes=None,
                     day_bid_amt=None):
    """全市场评分 + 排序(不按用户过滤); 返回 scored 列表(含 _raw)
    2026-08-16 拆分: 9:26 自动应用按用户复用同一份评分, 只各自过滤,
    避免 150+ 用户各跑一次全市场评分(性能 150 倍差距)。
    2026-09-01 抢筹口径: 新增 qiangchou_codes(右视图竞价异动"竞价抢筹"代码集合) —
    命中集合才打抢筹标(与右视图 9:20→9:25 涨幅/最后一秒段口径一致);
    集合为空或未传时回退旧公式(竞价涨幅>=2% 且 竞/昨>=20%)兜底。
    2026-09-03 竞额定格: day_bid_amt = 当日 9:25 定格竞价额 map {code: 万元}
    (auction_snapshot.load_day_bid_amt), 窗口外(盘中/收盘)bidAmt/bidRatio 以其为准 — 
    腾讯兜底期行情 f616 被近似为实时累计成交额, 直接读会把「竞额」显示成实时成交额。"""
    yesterday_map = yesterday_map or {}
    snapshot_map = snapshot_map or {}
    day_bid_amt = day_bid_amt or {}
    scored = []
    # 竞价/昨比: 分子=今日竞价额(f616, 9:25定格), 分母=最近已收盘交易日(T)全天额。
    # pair 由 _kline_amount_pair 保证 [最近已收盘T日, T-1日], 任何时间(窗口/盘中/收盘)都可算,
    # 分母恒为最近已收盘交易日, 避免"今日累计额/地量日/前天"错位导致失真。
    auction_ok = in_auction_window()
    for s in raw:
        sc = compute_score(s)
        code = s.get("f12")
        # 竞价额(万元): 2026-09-03 修复「竞额列=实时成交额」— 东财封禁期行情走腾讯兜底,
        # f616 被近似为累计实时成交额(fetcher.py), 盘中(窗口外)直接读会把竞额显示成实时成交额。
        # → 窗口内(9:15-9:31)行情 f616 新鲜(东财定格/腾讯仍在竞价累计阶段)直接用;
        #   窗口外(盘中/收盘) f616 已失真, 以当日 9:25 定格快照为准(9:30 前无连续竞价,
        #   9:25:xx 采集 bid_amt=当日竞价定格额, 全天恒定可信); 快照缺该 code 才回退 f616。
        if auction_ok:
            bid_amt = get_bid_amt(s, True)
        else:
            snap_amt = day_bid_amt.get(code)
            bid_amt = snap_amt if (snap_amt or 0) > 0 else get_bid_amt(s, False)   # 万元
        pair = yesterday_map.get(code)   # [最近已收盘T日, T-1日] 万元
        bid_ratio = None
        if pair:
            y_amt = pair[0]
            bid_ratio = round(bid_amt / y_amt * 100, 2) if y_amt else None
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
            # 2026-08-31 主人要求: 评分构成(五因子分项+权重)属内部逻辑, 不对用户暴露
            # "factors": sc["factors"],
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
            # 2026-09-01 抢筹口径改版: 命中右视图"竞价抢筹"代码集合才打标;
            # 集合为空/未传(数据源故障或非竞价场景)回退旧公式(涨幅>=2% 且 竞/昨>=20%)
            "qiangchou": (1 if s.get("f12") in qiangchou_codes else 0)
                        if qiangchou_codes else (1 if is_qiangchou(get_bid_change(s), bid_ratio) else 0),
            # 实时维度字段(盘中模式同竞价模式都用, 前端展示; 不参与竞价评分/过滤)
            "volRatio": parse_float(s.get("f10")),
            "turnover": parse_float(s.get("f8")),
            "_raw": s,
        })
    scored.sort(key=lambda x: x["probability"], reverse=True)
    return scored


def process_all_stocks(raw, f, yesterday_map=None, snapshot_map=None, qiangchou_codes=None,
                       day_bid_amt=None):
    """全市场竞价评分(与竞价锁定共用同一套): 评分 + 过滤 + 排序。
    兼容入口(2026-09-01 可测性重构后内部复用 score_all_stocks + apply_filters);
    与 score_all_stocks + apply_filters 拆分等价, 保留兼容入口: 评分 + 过滤 + 清理 _raw。
    day_bid_amt: 当日 9:25 定格竞价额 map {code: 万元}(auction_snapshot.load_day_bid_amt),
    非 None 时 bidAmt/bidRatio 优先用它 — 修复腾讯兜底期 f616=实时成交额导致盘中
    「竞额」列显示成实时成交额(2026-09-03 主人反馈)。
    """
    scored = score_all_stocks(raw, yesterday_map, snapshot_map, qiangchou_codes, day_bid_amt)
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
        "floatMvFloor": _clamp((q.get("floatMvFloor") or ["30"])[0], 0, 5000, 30),
        "floatMvGt": _clamp((q.get("floatMvGt") or ["100"])[0], 0, 5000, 100),
        "priceGt": _clamp((q.get("priceGt") or ["30"])[0], 0, 5000, 30),
        "bidAmtFloor": _clamp((q.get("bidAmtFloor") or ["3000"])[0], 0, 100000, 3000),
        # ---- 盘中实时(mode=spot)参数 ----
        "chgFloor": _clamp((q.get("chgFloor") or ["0"])[0], -20, 30, 0),       # 实时涨幅下限
        "chgGt": _clamp((q.get("chgGt") or ["9.5"])[0], -20, 30, 9.5),         # 实时涨幅上限
        "volRatioFloor": _clamp((q.get("volRatioFloor") or ["1"])[0], 0, 20, 1),  # 量比下限
        "turnoverFloor": _clamp((q.get("turnoverFloor") or ["1"])[0], 0, 100, 1), # 换手率下限
        "turnoverGt": _clamp((q.get("turnoverGt") or ["0"])[0], 0, 100, 0),    # 换手率上限(0=不限)
        "spotExcludeZT": _truthy((q.get("spotExcludeZT") or ["0"])[0]),        # 剔除已涨停
    }
