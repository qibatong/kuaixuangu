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
        # 2026-09-19 主人拍板: 去掉抢筹层(开盘啦退役)。
        # 2026-09-20 主人拍板(v5): **删加速度修正 + 低开 gate, 换竞价主力净额层**。
        # 2026-09-23 主人拍板(v7): **去掉净额档, 其 0.30 权重并入量比档** ——
        #   实测依据(2026-09-23 定格 5561 只, 探针 _kx_probe_warn_layers.py):
        #   竞价主力净额**连续 12 个交易日全市场非零 0 只**(采集链路 9:25 定格早于
        #   猫爪 fundflow_kp 生成) ⇒ 该层恒走 ff_default 0.35 = **常数 0.105**,
        #   对排序零贡献、只稀释量比层。故 w_ff 置 0(等价移除), 0.30 全给量比:
        #   合成 = 0.75×量比分档 + 0.25×AI档(子权重自动归一; 量比权重 45%→75%)。
        #   量比   = 今日9:25竞价额 ÷ 昨日9:25竞价额(快照自算, 昨额<100万判不可用)
        #   ⚠️ 副作用: 盘中动态加分层(stocks._apply_intraday_ff_bonus)与净额档同源
        #   (score_one_live_ff 取 max(竞价档, 盘中档)) ⇒ w_ff=0 后其 bonus 恒为 0,
        #   该功能一并失效(实测 2026-09-23 影响 12 只 / Top5 边界换 1 只)。
        "bid_strength": {
            "label": "竞价强度", "unit": "合成",
            # 层① 量比分档(与 v2 一致, 不动)
            "buckets": [["3", "9999", 1.0], ["2", "3", 0.85], ["1.5", "2", 0.7],
                        ["1.0", "1.5", 0.55], ["0.6", "1.0", 0.4], ["0", "0.6", 0.25]],
            "default": 0.22,
            # 层① 层③ 子权重(归一后生效) —— v7 去掉净额档(w_ff=0), 权重并入量比
            "w_vol_ratio": 0.75,
            "w_ff": 0.0,
            "w_ai": 0.25,
            # 层② 净额占自由流通市值% 分档 —— 按 2026-09-18 全市场分布定草案:
            #   P90=0.006 / P95=0.019 / P99=0.115 / max=1.64; 有值内净流入≈净流出各半。
            #   跑几天有数据后再校准(老规矩)。
            "ff_buckets": [["0.30", "9999", 1.0], ["0.10", "0.30", 0.85],
                           ["0.03", "0.10", 0.7], ["0.005", "0.03", 0.55],
                           ["0.0001", "0.005", 0.45],
                           ["-0.005", "0", 0.30], ["-0.03", "-0.005", 0.20],
                           ["-9999", "-0.03", 0.10]],
            "ff_default": 0.35,      # 无大单信号(0/缺失) → 中性, 不惩罚不奖励
            # 层③ AI 预测(aipick XGBoost, 2026-09-20 主人拍板) —— 标准=全市场榜
            #   Top30 ∩ p≥0.80 三档。实测 12 交易日分布: p≥0.8 日均 25~40 只(极端 63),
            #   p≥0.5 有 50~90 只 —— 门槛定 0.5 会大面积加分, 无区分度。
            #   低于所有档位下限 → 不进榜 → 走 ai_default 中性(不当惩罚)。
            "ai_buckets": [["0.90", "1.01", 1.0], ["0.85", "0.90", 0.85],
                           ["0.80", "0.85", 0.70]],
            "ai_topn": 30,           # 全市场按概率取前 30(极端日封顶保险)
            "ai_default": 0.35,      # 不在 AI 榜(常态) → 中性
        },
        "market": {
            "label": "自由流通市值", "unit": "亿",
            # ★ 2026-09-20: 市值口径由「流通市值」改「自由流通市值」(主人指令)。
            #   自由流通市值 ≈ 流通市值的 0.28~0.57 倍(中位约 0.5) → 分档阈值
            #   同步按 ≈0.5 折算, 保持"越小分越高"的相对档位不变:
            #   旧(流通): [0,30]1.0 [30,60]0.88 [60,120]0.68 [120,250]0.45
            #   新(自由): [0,15]1.0 [15,30]0.88 [30,60]0.68  [60,125]0.45
            #   若行只有流通市值(无自由流通), score.py 会用自由流通优先/流通兜底,
            #   兜底时阈值略偏保守(流通大于自由流通 → 落入更高档 → 分略低), 无害。
            "buckets": [["0", "15", 1.0], ["15", "30", 0.88], ["30", "60", 0.68],
                        ["60", "125", 0.45]],
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


# ---------- 工具 ----------
def parse_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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


def get_bid_amt(s, auction_ok=True):
    """竞价成交额(万元): f616 固定竞价额优先。
    仅竞价窗口内缺失时退回 f6(此时 f6≈竞价额);
    非窗口(盘中/收盘) f6=累计成交额, 不可作竞价额 → 缺失直接返回 0。
    (修复: 盘中 f616 缺失时误用 f6 会把累计成交额当竞价额, 竞价/昨比可算出 1000%+ 荒谬值)"""
    amt = parse_float(s.get("f616"))
    if not amt > 0 and auction_ok:
        amt = parse_float(s.get("f6"))
    return 0.0 if (not math.isfinite(amt) or amt <= 0) else amt / 10000


def _in_markets(code, markets):
    """市场范围过滤(2026-09-07 修复: 腾讯兜底无视 fs 按全市场拉取 → 后端若只依赖
    raw 范围做市场过滤会整体失效, 主/创/科勾选不起作用)。此处按代码前缀在**评分层**
    兜底, 任何数据源(东财/腾讯)都生效:
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


def is_bse(code):
    """北交所判定(2026-09-21 主人拍板「系统不需要北交所数据」): 4/8/920 开头 = 北交所。
    4=老三板/北交所老段(43), 8=北交所(83/87/88), 920=北交所新段(2024 起切换)。
    供采集层(auction_snapshot)与概念层(concept_refresh)全链路过滤复用。"""
    return str(code or "").startswith(("4", "8", "920"))


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
        "probLt": _clamp((q.get("probLt") or ["50"])[0], 5, 95, 50),
        "confLt": _clamp((q.get("confLt") or ["50"])[0], 50, 90, 50),
        # 评分下限(2026-09-10 主人拍板全站默认 80; 2026-09-20 主人拍板降到 50):
        # 单票评分低于此分**直接不入选**。与 probLt 的区别 —— probLt 是
        # "概率<probLt **且** 信心<confLt"的**双低**剔除, 高信心可以救低概率票;
        # scoreFloor 是**单阈值硬门槛**, 不管信心多高一律砍。
        # 2026-09-20 起 clamp 下限也卡到 50 —— 50 分是系统铁底, 0=关闭门槛已取消。
        "scoreFloor": _clamp((q.get("scoreFloor") or ["50"])[0], 50, 100, 50),
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
