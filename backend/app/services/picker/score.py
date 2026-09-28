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
from typing import Any, Dict, List, Optional, Tuple

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
    strength: **竞价强度**(0~1, 由 services/bid_strength 合成, pipeline 注入;
        2026-09-23 v7 起为**两层** = 量比档 0.75 + AI 档 0.25, 净额档已移除)。
        提供时**替代** warn(f630 异动等级) 因子 —— f630 只有东财点查才给真实值,
        腾讯/快照行恒填 0, 东财一断就全员 default(实测 2026-09-08 批次#1585 全部
        39 只 warn=0, 天花板从 99.4 崩到 85.5)。None = 未启用, 退回 warn(对拍用)。
    """
    if cfg is None:
        from .. import scorer                       # 延迟导入: 避免模块循环
        cfg = scorer.get_scoring_cfg()

    # ---- 因子原始值(None = 我不知道) ----
    bid_change = row.bid_change
    bid_turnover = row.bid_turnover                # 派生: 竞价量×价/自由流通市值
    warn_type = row.warn_type
    # 市值因子口径(2026-09-20 主人指令): **自由流通市值**优先, 缺失回退流通市值。
    #   config.buckets 已按自由流通口径折算(见 scorer.DEFAULT_SCORING 注释)。
    #   ★ 走 row.mv_yi 统一取值 —— 与 filter 的 floatMvFloor/Gt 门槛**同口径**,
    #     避免"评分用 A 值、门槛用 B 值"的名单漂移。
    #   0 与缺失同义(市值 0 的公司不存在, 且 0 会落进首桶拿**满分** 1.0 ——
    #   老链路正是这样把"市值未知"翻译成"超小盘最优", 权重 11%)。
    circ_mv = row.mv_yi
    if not circ_mv:
        circ_mv = None
    yday = row.yesterday_change

    # ---- 分档打分: 缺失 → default(绝不落 0 值桶) ----
    bid_score = (factor_default(cfg, "bid") if bid_change is None
                 else factor_score(cfg, "bid", bid_change))
    activity_score = (factor_default(cfg, "activity") if bid_turnover is None
                      else factor_score(cfg, "activity", bid_turnover))
    # 老链路 bid_vol_ratio 恒 0.0(量比加成是死代码, 从未触发): 此处保留同行为,
    # 不加成。留字段位是为了对拍可见 —— 哪天接了真实竞量比再启用。
    if strength is not None:
        # 竞价强度(2026-09-23 v7 起为**两层合成**= 量比0.75 + AI0.25, 对东财免疫) ——
        # 已由 bid_strength.score_one 处理缺失, 返回 None(各层全缺)时这里才走 default
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


# ---------------------------------------------------------------- 粗筛排队键
COARSE_RANK_MISSING = float("inf")
"""无竞价涨幅时的排队位: 排在**所有有效涨幅之后**(见 coarse_rank_key)。

为什么缺失要排最后、不当 0: 0 = "平开"是**有效**涨幅, 混进 0 那一档等于把
"没有竞价数据"冒充成"平开票"(契约铁律1: 缺失 = None, 永不填 0)。
本分支只在 ctx.require_bid_change=False(竞价早期数据未全的兼容开关)时才可达;
默认 require_bid_change=True ⇒ 无竞价涨幅的票在**门槛阶段**就被剔除, 进不到排序。
"""


def coarse_rank_key(bid_change: Optional[float], code: str) -> Tuple[float, str]:
    """**粗筛排队键**(升序用): (定格竞价涨幅的降序位, code)。

    ★ 2026-09-26 主人指令: 排队键由「**定格三因子粗排分**降序」(score.coarse_rank_score,
    2026-09-23 v4.11.37 上线)改为「**定格竞价涨幅降序**」—— 即直接用"当日涨幅榜"
    这把市场公认的尺子, 不再自造复合分。

      口径依据: 定格时点(9:25 撮合**之后**) C=O ⇒ **当日涨幅 ≡ 开盘涨幅 ≡ 竞价涨幅**,
      三者同值; 而竞价涨幅的权威来源就是定格快照的 `bid_change`
      (见 contract.FIELD_AUTHORITY["bid_change"])。
      名额上限不变(filter.COARSE_MAX / stocks._SNAP_CANDIDATE_MAX = **200**),
      **全部门槛一律不动**(板块 / ST / 昨涨停 / 竞涨上下限 / 自由流通市值 / 竞价额照旧)。

    同涨幅按 code 升序 —— 与 score_rows 的并列规则一致, 使结果与输入顺序**无关**
    (2026-09-23 改键时立的纪律, 本次沿用: 更早的「竞价额降序」在同额时依赖输入顺序、
    不可复现)。

    ⚠️ 旧键 `coarse_rank_score`(0~100 复合分, 含 bid/activity/market 三因子)已**随本次
       改动整体移除** —— 它只在"排队取前 N"里用过、**不参与任何门槛判定**, 故删除后
       不改变任何一只票的入选资格, 只改**排队顺序**。若将来要恢复复合分, 请连带恢复
       filter.py / stocks.py 两处调用点与对应用例。
    """
    if bid_change is None:
        return (COARSE_RANK_MISSING, code)
    return (-float(bid_change), code)


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
    industry: Optional[str] = None        # 板块覆盖后(开盘啦)的行业/概念
    concept: Optional[str] = None

    @property
    def code(self) -> str:
        return self.row.code

    @property
    def name(self) -> str:
        return self.row.name

    def to_dict(self) -> Dict[str, Any]:
        """前端字段(与老链路输出对齐, 含 province 等老字段位)

        ★ 2026-09-28: `bidTurnover` / `bidVolRatio` 改用 getattr 兜底 None ——
        spot 引擎的 SpotScoreResult **没有这两个字段**(竞换手/竞量比对 spot 不适用),
        而 spot 已接进 pipeline 复用本 to_dict(); 硬取会 AttributeError
        (v4.11.80 探针实测:`'SpotScoreResult' object has no attribute 'bid_turnover'`)。
        auction 的 ScoreResult **恒有**这两个属性, 故 getattr 对老路径**行为逐字不变**。
        """
        r = self.row
        return {
            "code": r.code,
            "name": r.name,
            "probability": self.score.probability,
            "confidence": self.score.confidence,
            "bidChange": r.bid_change,
            "realChange": r.real_change,
            "entityChange": r.entity_change,
            "bidTurnover": getattr(self.score, "bid_turnover", None),
            "bidVolRatio": getattr(self.score, "bid_vol_ratio", None),
            "speed": r.turnover,                       # 老链路 speed = f8 换手率
            "warnType": r.warn_type,
            # 市值展示口径(2026-09-20): 走统一 mv_yi(自由流通优先) —— 与门槛/评分同口径
            "circulationMV": None if not r.mv else round(r.mv_yi or 0.0, 4),
            "freeCirculationMV": None if not r.free_mv else round(r.free_mv / 1e8, 4),
            "industry": self.industry or r.industry or "-",
            "concept": self.concept or r.concept or "-",
            "province": "-",                           # 老链路 f102, 前端未强依赖
            "bidAmt": None if r.bid_amt is None else round(r.bid_amt / 1e4, 2),   # 万元
            "bidRatio": self.bid_ratio,
            "accel": self.accel,
            "price": r.price,
            "volRatio": r.vol_ratio,
            "turnover": r.turnover,
            "degraded": r.degraded,
            "source": r.source,
        }
