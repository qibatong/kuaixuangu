# -*- coding: utf-8 -*-
"""
盘中实时选股(spot)评分层
=================================================================================
2026-09-28 重建。原实现(2026-09-09 提交 4c56083 随 spot 功能整体下线)位于
老 scorer.compute_score_spot / process_spot_stocks / apply_spot_filters,
本模块将其**按 picker 契约层重写**(与 picker/score.py 同架构), 而非照抄老代码:

  1. 输入=QuoteRow(契约唯一权威), 不再是东财 raw dict(f3/f10/f8/f21 散读)。
     收益: 换源(猫爪/腾讯/快照)自动同口径, 不再绑死东财字段号。
  2. 缺失一律 None → 走该因子 default 分(契约铁律1), 不冒充 0。
     老实现 parse_float 把缺失转 0, 会让"市值未知"落进 market 首桶拿满分 1.0。
  3. 权重表(DEFAULT_SCORING_SPOT)与原实现**逐值一致**, 保证评分口径不变。
     原表见 4c56083^:backend/app/services/scorer.py:81-129。

计分口径: 六因子加权 = 实时涨幅 0.28 / 量比 0.26 / 换手率 0.18 /
封单强度 0.14 / 自由流通市值 0.08 / 昨日涨幅 0.06; 置信度三项加成。
与竞价的区别: 竞价看**9:25 定格**, 盘中看**实时**(real_change/vol_ratio/turnover)。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .contract import QuoteRow
from .score_factors import factor_score, factor_default, js_round


# ---------- 盘中实时选股权重表(2026-09-28 从 4c56083^ 原样恢复) ----------
# 结构: 权重 w_* + 置信度加成 conf_* + 因子分档表 factors
DEFAULT_SCORING_SPOT: Dict[str, Any] = {
    "w_chg": 0.28,        # 实时涨幅权重(健康涨幅区间优先, 过高=追高风险)
    "w_vol_ratio": 0.26,  # 量比权重(放量确认)
    "w_turnover": 0.18,   # 换手率权重(活跃度)
    "w_seal": 0.14,       # 封单强度权重(涨停股封成比; 非涨停=0分档)
    "w_market": 0.08,     # 流通市值权重
    "w_yesterday": 0.06,  # 昨日涨幅权重
    "conf_seal_high": 12,  # 置信度: 强封单(封成比>=2%)加成
    "conf_vol_ratio": 8,   # 置信度: 显著放量(量比>=2)加成
    "conf_chg": 6,         # 置信度: 健康涨幅区间加成
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


def get_spot_cfg() -> Dict[str, Any]:
    """盘中评分配置。

    ⚠️ 当前**只返回内置默认表** —— 管理端 settings 表 "scoring_spot" 的自定义
    覆盖尚未接回(原实现有, 随 4c56083 删除)。若要恢复管理端可调, 需同步
    改 admin.py 与 settings 读写, 属独立工作项。此处保持"默认表即可跑"的最小面。
    """
    return dict(DEFAULT_SCORING_SPOT)


@dataclass
class SpotScoreResult:
    """单票盘中评分结果(与 ScoreResult 同构, 便于前端字段复用)"""
    probability: int = 0
    confidence: int = 0
    seal_ratio: float = 0.0        # 封成比 %(封单额/自由流通市值)
    seal_fund: float = 0.0         # 封单额(亿)
    limit_boards: int = 0          # 连板数(涨停池)
    break_count: int = 0           # 开板次数(涨停池)
    parts: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def compute_score_spot(row: QuoteRow, zt_info: Optional[Dict[str, Any]] = None,
                       cfg: Optional[dict] = None) -> SpotScoreResult:
    """盘中实时评分: 输入契约行, 输出与竞价同构的评分结果。

    因子取值全部来自契约层(唯一权威来源), 缺失(None)一律走该因子 default 分。

    row:     QuoteRow —— real_change/vol_ratio/turnover/mv_yi/yesterday_change
    zt_info: 涨停池单股信息 {fund(封单额,亿), lb(连板), zbc(开板次数)} 或 None
             (非涨停/无数据)。封成比 = 封单额 / 自由流通市值 ×100。
    cfg:     评分配置(缺省取 get_spot_cfg()), 便于测试注入。
    """
    cfg = cfg or get_spot_cfg()
    zt_info = zt_info or {}

    # ---- 因子原始值(None = 我不知道, 绝不填 0) ----
    real_chg = row.real_change          # 实时涨幅 %
    vol_ratio = row.vol_ratio           # 量比
    turnover = row.turnover             # 换手率 %
    circ_mv = row.mv_yi                 # 自由流通市值(亿), 缺失→None
    if not circ_mv:
        circ_mv = None
    yday = row.yesterday_change

    # ---- 封单强度(封成比): 仅涨停股有效, 非涨停/无数据 = 0.0 ----
    #     注: 这里 0.0 是**语义上的 0**(非涨停股确实没有封单), 不是"缺失冒充 0",
    #     故不进 default 分支 —— 与老实现一致。
    try:
        fund = float((zt_info or {}).get("fund") or 0)
    except (TypeError, ValueError):
        fund = 0.0
    seal_ratio = (round(fund / circ_mv * 100, 2)
                  if (fund > 0 and circ_mv and circ_mv > 0) else 0.0)

    # ---- 分档打分: 缺失 → default(绝不落 0 值桶) ----
    chg_score = (factor_default(cfg, "chg") if real_chg is None
                 else factor_score(cfg, "chg", real_chg))
    vol_score = (factor_default(cfg, "vol_ratio") if vol_ratio is None
                 else factor_score(cfg, "vol_ratio", vol_ratio))
    turn_score = (factor_default(cfg, "turnover") if turnover is None
                  else factor_score(cfg, "turnover", turnover))
    seal_score = factor_score(cfg, "seal", seal_ratio)
    market_score = (factor_default(cfg, "market") if circ_mv is None
                    else factor_score(cfg, "market", circ_mv))
    yday_score = (factor_default(cfg, "yesterday") if yday is None
                  else factor_score(cfg, "yesterday", yday))

    base = (chg_score * cfg["w_chg"] + vol_score * cfg["w_vol_ratio"]
            + turn_score * cfg["w_turnover"] + seal_score * cfg["w_seal"]
            + market_score * cfg["w_market"] + yday_score * cfg["w_yesterday"])
    prob = max(5.0, min(95.0, base * 100))

    # ---- 置信度: 三项加成, 缺失(None)一律不加 ----
    conf = 65.0
    if seal_ratio >= 2:
        conf += cfg["conf_seal_high"]
    if vol_ratio is not None and vol_ratio >= 2:
        conf += cfg["conf_vol_ratio"]
    if real_chg is not None and 1.5 <= real_chg <= 6:
        conf += cfg["conf_chg"]
    conf = min(90.0, max(55.0, conf))

    def _r2(v):
        return None if v is None else round(v, 2)

    parts = {
        "chg": {"value": _r2(real_chg), "score": chg_score, "weight": cfg["w_chg"]},
        "vol_ratio": {"value": _r2(vol_ratio), "score": vol_score,
                      "weight": cfg["w_vol_ratio"]},
        "turnover": {"value": _r2(turnover), "score": turn_score,
                     "weight": cfg["w_turnover"]},
        "seal": {"value": _r2(seal_ratio), "score": seal_score, "weight": cfg["w_seal"]},
        "market": {"value": _r2(circ_mv), "score": market_score,
                   "weight": cfg["w_market"]},
        "yesterday": {"value": _r2(yday), "score": yday_score,
                      "weight": cfg["w_yesterday"]},
    }
    return SpotScoreResult(
        probability=js_round(prob),
        confidence=js_round(conf),
        seal_ratio=seal_ratio,
        seal_fund=fund,
        limit_boards=int((zt_info or {}).get("lb") or 0),
        break_count=int((zt_info or {}).get("zbc") or 0),
        parts=parts,
    )
