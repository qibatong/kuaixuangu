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
            # 2026-09-09 负涨幅低分桶: 低开/大跌(含平开, <0.001%)显式给 0.05, 不再与
            # "数据缺失"同吃 default 0.1(中石科技 9/8 竞涨-8.01% → 0.1 事故:
            # 34% 权重只扣 3.4 分, 负竞涨照样能靠其他因子凑分入选)
            # 桶边界: [-99, 0.001) 与正桶下限 0.001 无缝衔接(左闭右开)
            "buckets": [["-99", "0.001", 0.05], ["3", "5.5", 1.0], ["5.5", "99", 0.88],
                        ["2", "3", 0.88], ["1.5", "2", 0.65], ["0.001", "1.5", 0.4]],
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
        # 2026-09-08 新增: **竞价强度** —— 替代已失活的 f630 异动等级(权重同为 w_warn)。
        # f630 只有东财点查给真实值, 腾讯/快照行恒填 0 → 东财一断全员 default 0.18,
        # 17%×0.82=13.9 分凭空蒸发(实测 2026-09-08 批次#1585 全部 39 只 warn=0)。
        # 三层合成(详见 services/bid_strength.py):
        #   主分 = 竞价量比(今日9:25竞价额 ÷ 昨日9:25竞价额, 快照表自算, 昨额<100万判不可用)
        #   + 抢筹加成(开盘啦 qcDelta 榜 +0.15 / 最后一秒抢筹 +0.10)
        #   + 加速度修正(9_24→9_25 拉升 +0.08 / 跳水 -0.08)
        # 每层独立降级: 任一层缺失只走该层 default, 不再出现"一个字段挂掉全员 default"。
        "bid_strength": {
            "label": "竞价强度", "unit": "合成",
            "buckets": [["3", "9999", 1.0], ["2", "3", 0.85], ["1.5", "2", 0.7],
                        ["1.0", "1.5", 0.55], ["0.6", "1.0", 0.4], ["0", "0.6", 0.25]],
            "default": 0.22,
            "qc_bonus": 0.15,        # 命中开盘啦抢筹强度榜(list20 有 qcDelta)
            "qc_last_bonus": 0.10,   # 命中最后一秒抢筹(listLast)
            "accel_up": 0.08,        # 9_24→9_25 拉升 > 0.5 个百分点
            "accel_down": -0.08,     # 跳水 < -0.5 个百分点
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


def get_scoring_cfg(force=False, strategy="auction", mode=None):
    """读取评分配置(权重+打分明细; 内存缓存; 管理端更新后调 reload 生效)

    strategy: 选股策略。2026-09-09 盘中实时选股(spot)功能已下线(前端无入口,
    后端整链零调用), 仅保留 "auction"=竞价因子表。

    2026-09-09 命名消歧(主人指示): 原参数名 mode 与选股**时段模式** PickMode
    (preopen/auction/locked/intraday/closed) 撞名, 排查时极易误读(曾把接口回显的
    策略 mode='auction' 当成"午休仍在竞价窗口"的 bug)。此处统一改称 strategy,
    语义=选股策略(用哪套因子表); mode 关键字保留为**兼容别名**——线上若仍有
    旧调用点(未同步部署的脚本/老代码)传 mode= 不至于 TypeError, 下版本移除。
    """
    if mode is not None:
        strategy = mode
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
    get_scoring_cfg(force=True)
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


def _factor_default(cfg, key, dflt=0.1):
    """取某因子的 default 分(用于该因子**数据缺失**时的中性处理)。
    契约铁律1: 缺失就是缺失, 不得填 0 冒充 —— 0 会落进 ["0","1"] 桶拿 0.4 分,
    等于凭空给一只"昨日涨幅未知"的票打了个"昨日微涨"的分。"""
    f = (cfg.get("factors") or {}).get(key) or {}
    try:
        return float(f.get("default", dflt))
    except (TypeError, ValueError):
        return dflt


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


def _bj_hm():
    """当前北京时间 hour*60+min(可 mock, 供竞价额来源判断与测试用)"""
    t = time.gmtime(time.time() + 8 * 3600)
    return t.tm_hour * 60 + t.tm_min


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
def compute_score(s, yesterday_chg=None, strength=None):
    """竞价评分。yesterday_chg: **真实昨日涨幅%**(T日已收盘涨跌幅, 见
    fetcher.fetch_yesterday_changes); 不传/为 None 表示该票昨日涨幅未知 → 该因子走
    default 分(不再用当日 f3 冒充, 详见下方注记)。

    strength: **竞价强度**(0~1, services/bid_strength 三层合成) — 2026-09-08 新增,
    传了就**替代** warn(f630 异动等级)因子。f630 只有东财点查才给真实值, 腾讯/快照
    行恒填 0 → 东财一断全员 default 0.18, 17%×0.82=13.9 分凭空蒸发(实测 2026-09-08
    批次#1585 全部 39 只 warn=0, 天花板 99.4→85.5)。None = 不启用(默认, 行为不变)。
    """
    bid_change = get_bid_change(s)
    bid_turnover = get_bid_turnover(s)
    bid_vol_ratio = 0.0
    warn_type = get_warn_type(s)
    circ_mv = parse_float(s.get("f21")) / 1e8          # 流通市值(亿)
    # 2026-09-08 语义修正(主人拍板): 原实现 yesterday_approx = f3, 即拿**当日涨幅**当
    # "昨日涨幅"。但 factors.yesterday 的分档(3~9.5% 给 0.9 分)语义是"昨日强势股延续",
    # 喂当日涨幅属语义错配 —— 竞价时点当日涨幅甚至尚未形成。
    # 改为真实昨日涨幅(T 日已收盘涨跌幅, 东财日K f58, 与成交额同一次请求返回)。
    # 缺失时(None)走 _factor_default, 不再 fallback 任何代理值。
    yesterday_approx = yesterday_chg

    # 竞价分(按配置分档表)
    cfg = get_scoring_cfg()
    bid_score = get_factor_score(cfg, "bid", bid_change)

    # 活跃度分(竞价换手 + 量比加成)
    activity_score = get_factor_score(cfg, "activity", bid_turnover)
    if bid_vol_ratio >= 0.3:
        activity_score = min(1.0, activity_score + 0.1)

    # 异动分: 2026-09-08 起可用**竞价强度**替代(f630 会失活, 见 compute_score 注释)。
    # 不传 strength 时行为与改造前**完全一致**, 保证对拍基线不变。
    warn_score = strength if strength is not None else get_factor_score(cfg, "warn", warn_type)

    # 市值分
    market_score = get_factor_score(cfg, "market", circ_mv)

    # 昨日涨幅分
    yesterday_score = (_factor_default(cfg, "yesterday") if yesterday_approx is None
                       else get_factor_score(cfg, "yesterday", yesterday_approx))

    base = (bid_score * cfg["w_bid"] + activity_score * cfg["w_activity"]
            + warn_score * cfg["w_warn"] + market_score * cfg["w_market"]
            + yesterday_score * cfg["w_yesterday"])
    prob = max(5.0, min(95.0, base * 100))

    conf = 65.0
    if strength is not None:
        if strength >= 0.85:            # 与老口径 warn>=4 同档位
            conf += cfg["conf_warn_high"]
    elif warn_type >= 4:
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
        # 2026-09-09 下限 bidLt(与 picker/filter 同口径): 默认 0=竞价翻绿即剔。
        # 中石科技 9/8 竞涨 -8.01% 仍以 58 分混入名单事故 — 上限只管"过高", 负竞涨
        # 一直畅通无阻; 竞价异动选的是走强票, 低开(哪怕放量)不是异动是出货。
        if bid_chg is not None and bid_chg < f.get("bidLt", 0):
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


_QC_LABEL = {"amt": "竞额", "chg": "涨幅", "last": "末秒"}
_QC_UNIT = {"amt": "%", "chg": "个百分点", "last": "个百分点"}


def _qc_fields(code, qiangchou_codes=None, qiangchou_detail=None,
               bid_change=None, bid_ratio=None):
    """抢筹输出字段(2026-09-09 主人需求: 左视图只看到 🔥 分不清类型、看不到幅度)

    三级回退, 口径由细到粗:
      1) qiangchou_detail 命中 → 带类型(amt/chg/last) + 各自幅度 + 中文摘要 qcText
      2) 仅有 qiangchou_codes(旧调用/数据源只给集合) → 只打标, 无类型无幅度
      3) 两者皆无(数据源故障) → 旧公式 is_qiangchou 兜底, 并置 qcFallback=1
    """
    d = (qiangchou_detail or {}).get(str(code or ""))
    if d and d.get("types"):
        types = [t for t in d["types"] if t in _QC_LABEL]
        parts = ["%s抢筹 %s%s" % (_QC_LABEL[t], d.get(t), _QC_UNIT[t])
                 for t in types if d.get(t) is not None]
        return {"qiangchou": 1, "qcType": "+".join(types),
                "qcAmt": d.get("amt"), "qcChg": d.get("chg"), "qcLast": d.get("last"),
                "qcText": "；".join(parts) or "命中竞价抢筹", "qcFallback": 0}
    if qiangchou_codes:
        hit = 1 if code in qiangchou_codes else 0
        return {"qiangchou": hit, "qcType": "qc" if hit else "",
                "qcAmt": None, "qcChg": None, "qcLast": None,
                "qcText": "命中竞价抢筹" if hit else "", "qcFallback": 0}
    hit = 1 if is_qiangchou(bid_change, bid_ratio) else 0
    return {"qiangchou": hit, "qcType": "formula" if hit else "",
            "qcAmt": None, "qcChg": None, "qcLast": None,
            "qcText": "公式兜底(竞涨≥2% 且 竞/昨≥20%)" if hit else "", "qcFallback": 1 if hit else 0}


def score_all_stocks(raw, yesterday_map=None, snapshot_map=None, qiangchou_codes=None,
                     day_bid_amt=None, day_bid_change=None, yesterday_chg_map=None,
                     strengths=None, qiangchou_detail=None):
    """全市场评分 + 排序(不按用户过滤); 返回 scored 列表(含 _raw)
    2026-08-16 拆分: 9:26 自动应用按用户复用同一份评分, 只各自过滤,
    避免 150+ 用户各跑一次全市场评分(性能 150 倍差距)。
    2026-09-01 抢筹口径: 新增 qiangchou_codes(右视图竞价异动"竞价抢筹"代码集合) —
    命中集合才打抢筹标(与右视图 9:20→9:25 涨幅/最后一秒段口径一致);
    集合为空或未传时回退旧公式(竞价涨幅>=2% 且 竞/昨>=20%)兜底。
    2026-09-03 竞额定格: day_bid_amt = 当日 9:25 定格竞价额 map {code: 万元}
    (auction_snapshot.load_day_bid_amt), 窗口外(盘中/收盘)bidAmt/bidRatio 以其为准 — 
    腾讯兜底期行情 f616 被近似为实时累计成交额, 直接读会把「竞额」显示成实时成交额。
    2026-09-08 竞涨定格: day_bid_change = 当日 9:25 定格竞价涨幅 map {code: %}
    (auction_snapshot.load_day_bid_change) — 东财行情 f615 收盘后返回 "-" → get_bid_change
    退 f3(现价)导致 竞涨=现涨 + 涨幅过滤按现价(生产事故)。窗口外把行 f615 覆写为定格值,
    评分/抢筹/过滤/展示全链路同源; 缺失定格值的 code 保留行情原值(不误杀新股/北交)。"""
    yesterday_map = yesterday_map or {}
    snapshot_map = snapshot_map or {}
    day_bid_amt = day_bid_amt or {}
    day_bid_change = day_bid_change or {}
    yesterday_chg_map = yesterday_chg_map or {}   # 真实昨日涨幅 {code: %}(2026-09-08)
    strengths = strengths or {}   # 竞价强度 {code: 0~1}(2026-09-08, 空=不启用)
    scored = []
    # 竞价/昨比: 分子=今日竞价额(f616, 9:25定格), 分母=最近已收盘交易日(T)全天额。
    # pair 由 _kline_amount_pair 保证 [最近已收盘T日, T-1日], 任何时间(窗口/盘中/收盘)都可算,
    # 分母恒为最近已收盘交易日, 避免"今日累计额/地量日/前天"错位导致失真。
    auction_ok = in_auction_window()
    # 2026-09-08 竞涨定格(生产事故): 东财行情 f615 收盘后返回 "-" → get_bid_change 退
    # f3(现价/收盘涨幅) → 盘后 竞涨=现涨 + 「涨幅≤bidGt」过滤/竞价34%评分按现价判 → 筛出
    # 当日大跌票(22:06 fd27ebe 上生产后首页可见)。与 bidAmt 同判据: 窗口内(9:15-9:30)
    # 行情 f615 新鲜直接用; 窗口外以当日 9:25 定格快照 bid_change **覆写行 f615**, 使评分/
    # 抢筹/accel/输出全链路同源且名单恒定可复现; 快照缺该 code 保留行情原值(不误杀)。
    use_spot_bid = auction_ok and _bj_hm() < 9 * 60 + 30
    for s in raw:
        code = s.get("f12")
        if not use_spot_bid and code in day_bid_change:
            s = dict(s)
            s["f615"] = day_bid_change[code]
        # 真实昨日涨幅(缺失 → None → 该因子走 default 分, 不用当日 f3 冒充)
        sc = compute_score(s, yesterday_chg_map.get(code), strengths.get(code))
        # 竞价额(万元): 2026-09-03 修复「竞额列=实时成交额」— 东财封禁期行情走腾讯兜底,
        # f616 被近似为累计实时成交额(fetcher.py), 盘中(窗口外)直接读会把竞额显示成实时成交额。
        # → 窗口内(9:15-9:31)行情 f616 新鲜(东财定格/腾讯仍在竞价累计阶段)直接用;
        #   窗口外(盘中/收盘) f616 已失真, 以当日 9:25 定格快照为准(9:30 前无连续竞价,
        #   9:25:xx 采集 bid_amt=当日竞价定格额, 全天恒定可信); 快照缺该 code 才回退 f616。
        # 9:30 已开盘: 腾讯兜底行 f616=实时成交额 → 竞价窗口内也只在 **9:30 前**
        # 用行情 f616(in_auction_window 到 9:31, 留出这 1 分钟边界)
        if use_spot_bid:
            bid_amt = get_bid_amt(s, True)
        else:
            # 2026-09-07 修复(主人反馈"竞价额 3000→2500 万, 选出 56 只, 是不是有问题"):
            # 窗口外(盘中/收盘)**只认 9:25 定格竞价额**, 缺失即 0, **不再回退 f616** —
            # 腾讯兜底行 f616 被近似为**全天累计成交额**(fetcher 腾讯映射),
            # 收盘后动辄数亿 → 竞价额门槛形同虚设。实测同一参数因行情源(东财/腾讯)
            # 抖动返回 19 / 56 / 122 只(批次 #8226/#8227/#8225), 结果完全不可信。
            # 竞价额缺失(快照无该 code)应表现为 0 → 不满足"≥门槛"被过滤, 而非用
            # 成交额冒充。竞价窗口内仍用行情 f616(新鲜可信)。
            bid_amt = float(day_bid_amt.get(code) or 0)   # 万元
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
            **_qc_fields(s.get("f12"), qiangchou_codes, qiangchou_detail,
                        get_bid_change(s), bid_ratio),
            # 实时维度字段(盘中模式同竞价模式都用, 前端展示; 不参与竞价评分/过滤)
            "volRatio": parse_float(s.get("f10")),
            "turnover": parse_float(s.get("f8")),
            "_raw": s,
        })
    scored.sort(key=lambda x: x["probability"], reverse=True)
    return scored


def process_all_stocks(raw, f, yesterday_map=None, snapshot_map=None, qiangchou_codes=None,
                       day_bid_amt=None, day_bid_change=None, yesterday_chg_map=None,
                       strengths=None, qiangchou_detail=None):
    """全市场竞价评分(与竞价锁定共用同一套): 评分 + 过滤 + 排序。
    兼容入口(2026-09-01 可测性重构后内部复用 score_all_stocks + apply_filters);
    与 score_all_stocks + apply_filters 拆分等价, 保留兼容入口: 评分 + 过滤 + 清理 _raw。
    day_bid_amt: 当日 9:25 定格竞价额 map {code: 万元}(auction_snapshot.load_day_bid_amt),
    非 None 时 bidAmt/bidRatio 优先用它 — 修复腾讯兜底期 f616=实时成交额导致盘中
    「竞额」列显示成实时成交额(2026-09-03 主人反馈)。
    day_bid_change: 当日 9:25 定格竞价涨幅 map {code: %}(auction_snapshot.load_day_bid_change,
    2026-09-08 事故修复) — 东财 f615 收盘后为 "-", 窗口外以它覆写 bidChange, 否则退
    f3 造成 竞涨=现涨 + 「涨幅≤bidGt」过滤按现价(细节见 score_all_stocks)。
    """
    scored = score_all_stocks(raw, yesterday_map, snapshot_map, qiangchou_codes,
                              day_bid_amt, day_bid_change, yesterday_chg_map, strengths,
                              qiangchou_detail)
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
        "bidLt": _clamp((q.get("bidLt") or ["0"])[0], -20, 20, 0),   # 竞价涨幅下限(2026-09-09: 默认0=低开剔除; 可配负值放宽)
        "probLt": _clamp((q.get("probLt") or ["65"])[0], 5, 95, 65),
        "confLt": _clamp((q.get("confLt") or ["65"])[0], 50, 90, 65),
        # 评分下限(2026-09-10 主人拍板, 全站默认 80): 单票评分低于此分**直接不入选**。
        # 与 probLt 的区别 —— probLt 是"概率<probLt **且** 信心<confLt"的**双低**剔除,
        # 高信心可以救低概率票; scoreFloor 是**单阈值硬门槛**, 不管信心多高一律砍。
        "scoreFloor": _clamp((q.get("scoreFloor") or ["80"])[0], 0, 100, 80),
        "floatMvFloor": _clamp((q.get("floatMvFloor") or ["30"])[0], 0, 5000, 30),
        "floatMvGt": _clamp((q.get("floatMvGt") or ["100"])[0], 0, 5000, 100),
        "priceGt": _clamp((q.get("priceGt") or ["30"])[0], 0, 5000, 30),
        "bidAmtFloor": _clamp((q.get("bidAmtFloor") or ["3000"])[0], 0, 100000, 3000),
        # ---- 盘中实时参数【2026-09-09 spot 已下线; 暂留: users.py 用户偏好白名单
        #      与 validate_filters 输出契约引用, 删除收益 < 契约变更风险】----
        "chgFloor": _clamp((q.get("chgFloor") or ["0"])[0], -20, 30, 0),       # 实时涨幅下限
        "chgGt": _clamp((q.get("chgGt") or ["9.5"])[0], -20, 30, 9.5),         # 实时涨幅上限
        "volRatioFloor": _clamp((q.get("volRatioFloor") or ["1"])[0], 0, 20, 1),  # 量比下限
        "turnoverFloor": _clamp((q.get("turnoverFloor") or ["1"])[0], 0, 100, 1), # 换手率下限
        "turnoverGt": _clamp((q.get("turnoverGt") or ["0"])[0], 0, 100, 0),    # 换手率上限(0=不限)
        "spotExcludeZT": _truthy((q.get("spotExcludeZT") or ["0"])[0]),        # 剔除已涨停
    }
