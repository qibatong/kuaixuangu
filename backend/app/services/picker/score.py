# -*- coding: utf-8 -*-
"""
选股评分层 (重构 P2)
=================================================================================
与老 scorer.compute_score 的**差异只在"缺失值语义"**:

  老链路把行情当 dict 读, 任何字段缺失都被 parse_float 静默转成 0.0, 于是:
    * 流通市值缺失 → 0 亿 → 落进 market 分档 ["0","30"] → 拿 **满分 1.0**(权重 11%)
      即"我不知道它多大"被翻译成"它是超小盘, 最优质"
    * 竞价涨幅缺失 → 退回 f3(当日涨幅) → 竞价 34% 权重按现价打分(9/7 大跌票混入根因)
    * 竞价换手缺失 → 0 → 落 default 0.1(这条反而歪打正着)
  本层一律: **缺失 = None = 走该因子的 default 分**, 绝不冒充成 0。

  分档表/权重/置信度加成全部复用 scorer 的配置(同一份 settings "scoring"),
  保证调参行为一致、对拍可比。

计分口径契约:
  在**字段完备**的输入上, 本层输出与已退役老链路 scorer 逐票一致(分与名单);
  在字段缺失的输入上，允许且**应当**出现差异(差异即修正: 缺失不再冒充 0 值桶)。
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contract import QuoteRow
from .score_factors import factor_score, factor_default, js_round


@dataclass
class ScoreResult:
    """单票评分结果(内部单位: 分=0~100 整数, 与老链路同)"""
    probability: int = 0                    # 上涨概率(展示值 5~95)
    confidence: int = 0                     # 置信度(展示值 55~90)
    bid_turnover: Optional[float] = None    # 竞价换手率 %(None=未知, 不是 0)
    bid_vol_ratio: Optional[float] = None   # 竞量比(老链路恒 0.0, 保留字段位)
    # 各因子取值与得分(内部诊断/对拍用, 不下发前端 — 2026-08-31 主人要求评分构成保密)
    parts: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def compute_score(row: QuoteRow, cfg: Optional[dict] = None,
                  strength: Optional[float] = None) -> ScoreResult:
    """契约版竞价评分: 输入 QuoteRow, 输出与老链路同构的评分结果。

    因子取值全部来自契约层(唯一权威来源), 缺失(None)一律走该因子 default 分。
    cfg: 评分配置(缺省自动取 scorer.get_scoring_cfg()), 便于测试注入。
    strength: **竞价强度**(0~1, 由 services/bid_strength 三层合成, pipeline 注入)。
        提供时**替代** warn(f630 异动等级) 因子 —— f630 只有东财点查才给真实值,
        腾讯/快照行恒填 0, 东财一断就全员 default(实测 2026-09-08 批次#1585 全部
        39 只 warn=0, 天花板从 99.4 崩到 85.5)。None = 未启用, 退回 warn(对拍用)。
    """
    if cfg is None:
        from .. import scorer                       # 延迟导入: 避免模块循环
        cfg = scorer.get_scoring_cfg()

    # ---- 因子原始值(None = 我不知道) ----
    bid_change = row.bid_change
    bid_turnover = row.bid_turnover                # 派生: 竞价量×价/流通市值
    warn_type = row.warn_type
    # 流通市值: 0 与缺失同义(市值 0 的公司不存在, 且 0 会落进 ["0","30"] 桶拿**满分**
    # 1.0 —— 老链路正是这样把"市值未知"翻译成"超小盘最优", 权重 11%)
    circ_mv = (row.float_mv / 1e8) if row.float_mv else None
    yday = row.yesterday_change

    # ---- 分档打分: 缺失 → default(绝不落 0 值桶) ----
    bid_score = (factor_default(cfg, "bid") if bid_change is None
                 else factor_score(cfg, "bid", bid_change))
    activity_score = (factor_default(cfg, "activity") if bid_turnover is None
                      else factor_score(cfg, "activity", bid_turnover))
    # 老链路 bid_vol_ratio 恒 0.0(量比加成是死代码, 从未触发): 此处保留同行为,
    # 不加成。留字段位是为了对拍可见 —— 哪天接了真实竞量比再启用。
    if strength is not None:
        # 竞价强度(三层合成, 对东财免疫) —— 已由 bid_strength.score_one 处理缺失,
        # 返回 None(三层全缺)时这里才走 default
        warn_score = strength
        warn_label: Any = "竞价强度"
    else:
        warn_label = warn_type
        warn_score = (factor_default(cfg, "warn") if warn_type is None
                      else factor_score(cfg, "warn", warn_type))
    market_score = (factor_default(cfg, "market") if circ_mv is None
                    else factor_score(cfg, "market", circ_mv))
    yday_score = (factor_default(cfg, "yesterday") if yday is None
                  else factor_score(cfg, "yesterday", yday))

    base = (bid_score * cfg["w_bid"] + activity_score * cfg["w_activity"]
            + warn_score * cfg["w_warn"] + market_score * cfg["w_market"]
            + yday_score * cfg["w_yesterday"])
    prob = max(5.0, min(95.0, base * 100))

    # ---- 置信度: 三项加成, 缺失(None)一律不加(老链路缺失=0 → 同样不加, 行为一致) ----
    conf = 65.0
    if strength is not None:
        # 竞价强度 ≥0.85(≈命中抢筹且放量) 才加成, 与老链路 warn>=4 同档位
        if strength >= 0.85:
            conf += cfg["conf_warn_high"]
    elif warn_type is not None and warn_type >= 4:
        conf += cfg["conf_warn_high"]
    if bid_turnover is not None and bid_turnover >= 0.4:
        conf += cfg["conf_turnover"]
    if bid_change is not None and 2 <= bid_change <= 6.5:
        conf += cfg["conf_bid"]
    conf = min(90.0, max(55.0, conf))

    def _r2(v):
        return None if v is None else round(v, 2)

    parts = {
        "bid": {"value": _r2(bid_change), "score": bid_score, "weight": cfg["w_bid"]},
        "activity": {"value": _r2(bid_turnover), "score": activity_score,
                     "weight": cfg["w_activity"]},
        "warn": {"value": warn_label, "score": warn_score, "weight": cfg["w_warn"]},
        "market": {"value": _r2(circ_mv), "score": market_score, "weight": cfg["w_market"]},
        "yesterday": {"value": _r2(yday), "score": yday_score, "weight": cfg["w_yesterday"]},
    }
    return ScoreResult(
        probability=js_round(prob),
        confidence=js_round(conf),
        bid_turnover=bid_turnover,
        bid_vol_ratio=0.0,
        parts=parts,
    )


# ---------------------------------------------------------------- 批量评分
def score_rows(rows: List[QuoteRow], cfg: Optional[dict] = None,
               strengths: Optional[Dict[str, float]] = None) -> List["ScoredRow"]:
    """批量评分 + 按 probability 降序排序(与已退役老链路同序)。

    strengths: {code: 竞价强度 0~1}, 由 services/bid_strength 提供; 传了就用
    竞价强度替代 warn 因子(见 compute_score 注释)。None = 退回 f630(对拍用)。

    返回 ScoredRow(QuoteRow + 评分 + 展示字段), 供过滤层与结果输出使用。
    """
    out: List[ScoredRow] = []
    for r in rows:
        sr = compute_score(r, cfg, (strengths or {}).get(r.code))
        out.append(ScoredRow(row=r, score=sr))
    out.sort(key=lambda x: (-x.score.probability, x.row.code))
    return out


@dataclass
class ScoredRow:
    """评分后的行: 契约行 + 评分 + 竞价派生展示字段。

    to_dict() 输出**与老链路 item 同构**(前端字段零改动), 缺失字段透出 None
    (前端应显示 '-' 而非 0.00 — 9/8 现涨幅列清一色 0.00% 就是填 0 造成的)。
    """
    row: QuoteRow
    score: ScoreResult
    # ---- 竞价派生(由 pipeline 注入; 老链路曾就地计算) ----
    bid_ratio: Optional[float] = None     # 竞价额/最近已收盘交易日全天额 (%)
    accel: Optional[float] = None         # 9:25-9:20 竞价涨幅加速度(%)
    qiangchou: int = 0                    # 抢筹标记 0/1
    industry: Optional[str] = None        # 板块覆盖后(开盘啦)的行业/概念
    concept: Optional[str] = None

    @property
    def code(self) -> str:
        return self.row.code

    @property
    def name(self) -> str:
        return self.row.name

    def to_dict(self) -> Dict[str, Any]:
        """前端字段(与老链路输出对齐, 含 province 等老字段位)"""
        r = self.row
        return {
            "code": r.code,
            "name": r.name,
            "probability": self.score.probability,
            "confidence": self.score.confidence,
            "bidChange": r.bid_change,
            "realChange": r.real_change,
            "entityChange": r.entity_change,
            "bidTurnover": self.score.bid_turnover,
            "bidVolRatio": self.score.bid_vol_ratio,
            "speed": r.turnover,                       # 老链路 speed = f8 换手率
            "warnType": r.warn_type,
            "circulationMV": None if r.float_mv is None else round(r.float_mv / 1e8, 4),
            "industry": self.industry or r.industry or "-",
            "concept": self.concept or r.concept or "-",
            "province": "-",                           # 老链路 f102, 前端未强依赖
            "bidAmt": None if r.bid_amt is None else round(r.bid_amt / 1e4, 2),   # 万元
            "bidRatio": self.bid_ratio,
            "accel": self.accel,
            "price": r.price,
            "qiangchou": self.qiangchou,
            "volRatio": r.vol_ratio,
            "turnover": r.turnover,
            "degraded": r.degraded,
            "source": r.source,
        }
