# -*- coding: utf-8 -*-
"""
选股数据契约层 (重构 P0)
=================================================================================
老链路字段语义混乱实录(本层要消灭的东西):
  - get_bid_change(): f615 缺失/异常 → **隐式退回 f3** → 盘后 f615 = "-" 时"竞价涨幅"
    变成"当日涨幅", 过滤按现价判 → 大跌票混入(9/7 事故)
  - compute_score(): "昨日涨幅"因子实际取 **f3(当日涨幅)** 代理, 语义错误但已成既定权重
  - 降级构造行 price 填 0 → priceGt 门槛永不触发 → 名单虚胖一倍(9/8 事故)
  - 降级构造行 f3 填 0 → 前端"现涨幅"列清一色 0.00%

本层契约:
  * 每个字段在 FIELD_AUTHORITY 里声明**唯一权威来源**, 任何改动必须改这里并补注释
  * 缺失 = None, 永不填 0(填 0 = 静默撒谎: 0 会被过滤/展示当成有效值)
  * 单位统一: 金额=元, 涨幅=百分点(%), 市值=元 — 展示层(to_dict)才转万元/亿
  * 退化取值只允许出现在 DEGRADE_RULES 中, 且必须写明触发条件与理由

待主人确认的可疑口径(先保持与老逻辑一致以保证双跑对拍, 但显式标注):
  * yesterday_change: 老逻辑用 f3(当日涨幅)当"昨日涨幅"代理, 语义错误。
    若要改为真实昨日涨幅, 需新数据源 + 重新校准权重(不在本次重构范围)。
"""
import math
from dataclasses import dataclass, fields as dc_fields
from typing import Any, Dict, List, Optional, Tuple

# ============================ 字段权威来源表 ============================
# 唯一真源: 任何"这个字段从哪来"的问题都以此表为准, 禁止在别处隐式 fallback。
FIELD_AUTHORITY: Dict[str, str] = {
    "code": "股票代码: 东财 f12 / 快照 code / 腾讯 v_xx#### 解析",
    "name": "股票名称: 东财 f14 / 快照 name / 腾讯 f[1]",

    # ---- 竞价字段: 权威 = 9:25 定格快照; 仅竞价窗口内可用实时 f615/f616 ----
    "bid_change": "竞价涨幅%: ① 9:25 定格快照 bid_change ② 竞价窗口内 f615。"
                  "**禁止**在非竞价窗口退回 f3(老逻辑隐式 fallback = 竞涨变现涨, 大跌票混入根因)",
    "bid_amt": "竞价额(元): ① 9:25 定格快照 bid_amt ② 竞价窗口内 f616。"
               "禁止用 f6(累计成交额)冒充 — 盘中 f6 是全天累计, 会算出 1000%+ 荒谬昨比",
    "bid_vol": "竞价量(股): ① 定格快照 bid_vol ② 竞价窗口内 f617。"
               "**窗口外恒 None** — 老链路窗口外用 f5(当日累计成交量)算'竞价换手', "
               "语义错误(盘中 f5 是全天累计, 会算出虚高换手), 契约层不允许该退化",

    # ---- 实时字段: 权威 = 实时行情源 ----
    "price": "现价(元): 实时源 f2 / 腾讯 f[3]; 定格模式取昨收(prev_close)",
    "prev_close": "昨收(元): 实时源 f18 / 腾讯 f[4]",
    "open": "今开(元): 实时源 f17 / 腾讯 f[5]; 缺失时实体涨幅为 None(老逻辑填0导致实体列全0%)",
    "real_change": "现涨幅%: 实时源 f3; 盘前未开盘时数据源本身无值 → None(不是 0)",
    "vol": "成交量(股): 实时源 f5(手)×100 / 腾讯 f[36](手)×100",
    "amount": "成交额(元): 实时源 f6 / 腾讯 f[37](万)×1e4",
    "turnover": "换手率%: 实时源 f8 / 腾讯 f[38]",
    "vol_ratio": "量比: 实时源 f10",
    "warn_type": "异动等级: 实时源 f630(实测取值 0/1/2)",

    # ---- 半静态 ----
    "float_mv": "流通市值(元): 实时源 f21 / 快照 float_mv, **缺失时回退 free_mv**"
                "(老逻辑只取 float_mv → 快照行市值为 0 被 floatMvFloor 误杀)",
    "industry": "行业: 东财 f100 / 开盘啦覆盖",
    "concept": "概念: 东财 f103 / 开盘啦覆盖",

    # ---- 可疑口径(保持老行为, 待确认) ----
    "yesterday_change": "昨日涨幅%: 真实值 = T日(最近已收盘交易日)涨跌幅, 来自东财日K f58"
                        "(fetcher.fetch_yesterday_changes, 与成交额**同一次请求**返回)。"
                        "2026-09-08 已修正: 老逻辑拿**当日 f3** 冒充昨日涨幅(语义错配)已废弃",

    # ---- 元信息 ----
    "source": "本行数据来源标签(eastmoney/tencent/snapshot), 用于降级可见性",
    "degraded": "本行是否来自降级路径(True 时必须前端明示, 见铁律2)",
}

# ============================ 退化规则 ============================
# 唯一允许的退化取值白名单。任何不在此表中的 fallback 都是 bug。
DEGRADE_RULES: Tuple[str, ...] = (
    "float_mv: 实时源 f21 缺失/为 0 → 回退快照 free_mv"
    "  (理由: 快照表历史行存在 float_mv=0 但 free_mv 有值的脏数据)",
    "price: 定格模式下无实时价 → 取 prev_close"
    "  (理由: 定格名单本就是 9:25 状态, 昨收是该时点的真实价格基准)",
    "is_suspended: prev_close/vol 缺失导致停牌**未知**时, 若 bid_amt>0 → 判非停牌"
    "  (理由: 9:25 有竞价成交额本身就是'有成交'的强证据; 老逻辑 f4/f5 缺失被"
    "  parse_float 转成 0 → 判为停牌并剔除, 是 2026-09-01 降级行整批被误杀的根因)",
)


def _f(v: Any) -> Optional[float]:
    """契约安全的 float 转换: 非法/空/'-'/NaN 一律 None(绝不返回 0)"""
    if v is None or v == "" or v == "-":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


@dataclass
class QuoteRow:
    """一行选股行情的契约实体。

    铁律1: 所有值字段默认 None(而非 0)。None = "我不知道", 0 = "我知道它是 0",
    这两者在过滤门槛与前端展示中语义完全不同 —— 9/8 名单虚胖一倍就是因为
    降级行把 price 填 0, 让 priceGt 门槛静默失效。
    """
    # 标识
    code: str = ""
    name: str = ""

    # 竞价字段(权威=9:25 定格)
    bid_change: Optional[float] = None      # 竞价涨幅 %
    bid_amt: Optional[float] = None         # 竞价额(元)
    bid_vol: Optional[float] = None         # 竞价量(股)

    # 实时字段
    price: Optional[float] = None           # 现价(元)
    prev_close: Optional[float] = None      # 昨收(元)
    open: Optional[float] = None            # 今开(元)
    real_change: Optional[float] = None     # 现涨幅 %
    vol: Optional[float] = None             # 成交量(股)
    amount: Optional[float] = None          # 成交额(元)
    turnover: Optional[float] = None        # 换手率 %
    vol_ratio: Optional[float] = None       # 量比
    warn_type: Optional[int] = None         # 异动等级

    # 半静态
    float_mv: Optional[float] = None        # 流通市值(元)
    industry: Optional[str] = None
    concept: Optional[str] = None

    # 可疑口径(保持老行为)
    yesterday_change: Optional[float] = None

    # 元信息
    source: str = ""
    degraded: bool = False
    auction_window: bool = False
    """**时段**标记: 本行是否采集于竞价窗口(9:15-9:25)。

    与 from_eastmoney 的同名参数**语义不同**: 参数表示"该源是否允许读实时竞价字段
    f615/f616"(源能力), 本字段表示"当前是不是竞价时段"(时段事实), 用于停牌判定:
    竞价期内尚未撮合成交 → 成交量恒 0, 拿 vol==0 判停牌会把**全市场误杀**
    (2026-09-09 生产实证: 竞价窗口 25 次调用入选恒为 0, 剔除原因全是 suspend)。
    """

    # ---------- 派生量(property 计算, 不存储, 避免冗余不一致) ----------
    @property
    def is_suspended(self) -> Optional[bool]:
        """停牌判断: 昨收<=0 或 成交量==0 → 停牌。
        老逻辑 is_suspended(f4<=0 或 f5==0): 降级行无 f4/f5 时会被**误判为停牌**
        (2026-09-01 事故)。本契约下 prev_close/vol 皆 None → 返回 None(未知),
        由调用方按模式策略决定"未知是否剔除", 不得默认剔除或默认保留。

        竞价窗口例外(2026-09-09 修复): 集合竞价期尚未撮合, 成交量**恒为 0**,
        vol==0 此时是"还没开盘"而非"停牌" → 该时段只看昨收判定。真停牌股无竞价额,
        仍会被 bidAmtFloor 门槛剔除(见 filter.apply_filters 第 7 步), 不放水。
        """
        if self.prev_close is None or self.vol is None:
            return None
        if self.auction_window:
            return self.prev_close <= 0
        return self.prev_close <= 0 or self.vol == 0

    @property
    def entity_change(self) -> Optional[float]:
        """实体涨幅%: 今开 → 现价。缺今开/现价 → None(老逻辑填 0 → 实体列全 0%)"""
        o, p = self.open, self.price
        if not o or not p:
            return None
        return (p - o) / o * 100

    @property
    def auction_price(self) -> Optional[float]:
        """**9:25 定格竞价价** = 昨收 ×(1 + 竞价涨幅/100)。

        存在的理由(2026-09-08 P3 实测): 快照表 snapshot_bid **没有 price 列**,
        而 priceGt 门槛必须有个"定格价"才能判 —— 用实时价判, 盘中价格一漂
        名单就变(票涨过 30 元被剔、跌回来又出现), 幂等直接不成立; 用昨收×
        竞价涨幅推算出的竞价价, **全天恒定**, 既让门槛生效又不漂移。
        缺昨收或竞价涨幅 → None(此时门槛不生效, 由调用方决定保留还是剔除)。
        """
        if not self.prev_close or self.bid_change is None:
            return None
        p = self.prev_close * (1.0 + self.bid_change / 100.0)
        return p if math.isfinite(p) and p > 0 else None

    @property
    def bid_turnover(self) -> Optional[float]:
        """竞价换手率%(派生): **竞价额 ÷ 流通市值 × 100**

        口径选择(2026-09-08 关键): 竞价额(元)÷流通市值(元)×100 与
        "竞价量×价÷流通市值"数学等价(竞价额 = 竞价量×价), 但**不依赖 bid_vol** ——
        快照表 snapshot_bid 只存 bid_change/bid_amt, **没有竞价量字段**, 若用竞价量
        口径则盘中/盘后全市场 bid_turnover 恒为 None → activity 因子(权重 32%)
        全部走 default 0.1 → 评分体系塌陷。
        老链路用 f5(当日成交量)×价÷市值: 竞价窗口内 f5 恰为竞价量故碰巧正确,
        窗口外 f5 是全天累计 → 虚高(语义错误, 契约层不允许该退化)。
        bid_amt 缺失时退回 bid_vol×price 兜底(竞价窗口内实时行有 f617/f2)。
        """
        mv = self.float_mv
        if not mv or mv <= 0:
            return None
        t = None
        if self.bid_amt:
            t = self.bid_amt / mv * 100
        elif self.bid_vol and self.price:
            t = self.bid_vol * self.price / mv * 100
        return t if (t is not None and math.isfinite(t)) else None

    # ---------- 契约查询 ----------
    def missing(self, *names: str) -> List[str]:
        """返回这些字段中缺失(None/空)的字段名"""
        out = []
        for n in names:
            v = getattr(self, n, None)
            if v is None or v == "":
                out.append(n)
        return out

    def require(self, *names: str) -> bool:
        """关键字段是否齐全(用于"字段不全则该条件不生效"或"剔除此票"的策略判断)"""
        return not self.missing(*names)

    def missing_fields(self) -> List[str]:
        """所有缺失字段(排除元信息)"""
        skip = {"source", "degraded"}
        return [f.name for f in dc_fields(self)
                if f.name not in skip and (getattr(self, f.name) is None
                                           or getattr(self, f.name) == "")]

    # ---------- 展示层输出(单位转换在此, 内部一律元) ----------
    def to_dict(self) -> Dict[str, Any]:
        """前端字段映射。None 原样透出(前端应显示 '-' 而非 0.00)。
        单位转换: bidAmt→万元, circulationMV→亿, amount→亿, 与前端现有约定一致。"""
        return {
            "code": self.code,
            "name": self.name,
            "price": self.price,
            "realChange": self.real_change,
            "entityChange": self.entity_change,
            "bidChange": self.bid_change,
            "bidAmt": None if self.bid_amt is None else round(self.bid_amt / 1e4, 2),
            "bidTurnover": self.bid_turnover,
            "turnover": self.turnover,
            "volRatio": self.vol_ratio,
            "amount": None if self.amount is None else round(self.amount / 1e8, 4),
            "circulationMV": None if self.float_mv is None else round(self.float_mv / 1e8, 2),
            "industry": self.industry,
            "concept": self.concept,
            "degraded": self.degraded,
            "source": self.source,
        }

    # ================= 构造入口(字段映射 = 契约的一部分) =================
    @classmethod
    def from_eastmoney(cls, s: Dict[str, Any], *, auction_window: bool = False,
                       day_bid_change: Optional[float] = None,
                       day_bid_amt_wan: Optional[float] = None,
                       yesterday_chg: Optional[float] = None,
                       day_bid_vol: Optional[float] = None,
                       degraded: bool = False,
                       period_auction: Optional[bool] = None) -> "QuoteRow":
        """东财 push2 diff 行 → QuoteRow。

        auction_window: 该源**是否允许读取实时竞价字段** f615/f616(源能力)。
            **仅竞价窗口内**才允许取; 窗口外 f615 为 "-" / f616 退回历史值,
            取之即事故(9/7 竞涨=现涨)。腾讯等无竞价字段的源恒传 False。
        period_auction: **时段**事实(是否 9:15-9:25), 写进 QuoteRow.auction_window
            供停牌判定使用; None 时回退取 auction_window 的值(东财源两者同义)。
        day_bid_change / day_bid_amt_wan / day_bid_vol: 9:25 定格值(额为万元), 由
            调用方传入; 提供时**优先**于实时字段(定格是竞价字段的权威来源,
            见 FIELD_AUTHORITY)。窗口外竞价量只能来自定格 —— 没有就是 None。
        """
        vol_hand = _f(s.get("f5"))          # 成交量(手)
        row = cls(
            code=str(s.get("f12") or s.get("code") or ""),
            name=str(s.get("f14") or s.get("name") or ""),
            price=_f(s.get("f2")),
            prev_close=_f(s.get("f18")) or _f(s.get("f4")),   # f18=昨收, f4 同义兜底
            open=_f(s.get("f17")),
            real_change=_f(s.get("f3")),
            vol=None if vol_hand is None else vol_hand * 100,
            amount=_f(s.get("f6")),
            turnover=_f(s.get("f8")),
            vol_ratio=_f(s.get("f10")),
            warn_type=(lambda v: None if v is None else int(v))(_f(s.get("f630"))),
            float_mv=_f(s.get("f21")),
            industry=s.get("f100") or None,
            concept=s.get("f103") or None,
            # 2026-09-08 修正: 昨日涨幅取真实值(日K f58), 不再用当日 f3 冒充
            yesterday_change=yesterday_chg,
            source="eastmoney",
            degraded=degraded,
            auction_window=(auction_window if period_auction is None
                            else period_auction),
        )
        # 竞价字段: 定格值优先 → 窗口内实时 → None(绝不退化 f3)
        if day_bid_change is not None:
            row.bid_change = day_bid_change
        elif auction_window:
            row.bid_change = _f(s.get("f615"))
        if day_bid_amt_wan is not None:
            row.bid_amt = day_bid_amt_wan * 1e4      # 万元 → 元
        elif auction_window:
            row.bid_amt = _f(s.get("f616"))
        row.bid_vol = (day_bid_vol if day_bid_vol is not None
                       else (_f(s.get("f617")) if auction_window else None))
        return row

    @classmethod
    def from_snapshot(cls, v: Dict[str, Any], *, degraded: bool = False) -> "QuoteRow":
        """9:25 定格快照行 → QuoteRow。

        快照是竞价字段的**权威来源**: bid_change/bid_amt 直接取; 无实时价 → price
        取昨收(见 DEGRADE_RULES)。
        float_mv 缺失回退 free_mv(快照表历史脏数据: float_mv=0 但 free_mv 有值)。

        warn_type(2026-09-18 v4.11.30): 快照表现在也落**东财 f630 异动等级**。
        这是 17% 异动因子在定格链路的唯一来源 —— 东财点查(push2 ulist)长期被封,
        补丁源拿不到 f630(详见 database.init_db 的 snapshot_bid.warn_type 注释)。
        老库/老行无此键 → None → 评分走 default(与改动前行为一致)。
        """
        prev = _f(v.get("pre_close")) or _f(v.get("f18"))
        fmv = _f(v.get("float_mv")) or _f(v.get("free_mv"))
        amt_wan = _f(v.get("bid_amt"))          # 快照 bid_amt 单位=万元
        return cls(
            code=str(v.get("code") or ""),
            name=str(v.get("name") or ""),
            bid_change=_f(v.get("bid_change")),
            bid_amt=None if amt_wan is None else amt_wan * 1e4,
            bid_vol=_f(v.get("bid_vol")),
            price=_f(v.get("price")) or prev,   # 定格无实时价 → 昨收(DEGRADE_RULES)
            prev_close=prev,
            open=_f(v.get("open")),
            real_change=_f(v.get("change")) or _f(v.get("real_change")),
            warn_type=(lambda x: None if x is None else int(x))(_f(v.get("warn_type"))),
            float_mv=fmv,
            industry=v.get("industry") or None,
            concept=v.get("concept") or None,
            yesterday_change=_f(v.get("change")) or _f(v.get("real_change")),
            source="snapshot",
            degraded=degraded,
        )

    @classmethod
    def from_tencent(cls, f: List[str], *, degraded: bool = False,
                     yesterday_chg: Optional[float] = None) -> "QuoteRow":
        """腾讯 qt.gtimg.cn 单行(~分隔 88 字段) → QuoteRow。
        索引与 fetcher._tencent_diff 一致: f[1]名称 f[2]代码 f[3]现价 f[4]昨收
        f[5]今开 f[31]涨跌% f[36]成交量(手) f[37]成交额(万) f[38]换手 f[43]流通市值(亿)。
        腾讯无竞价专属字段 → bid_* 一律 None(不拿现价涨幅冒充竞价涨幅)。"""
        def g(i):
            try:
                return f[i]
            except IndexError:
                return None

        vol_hand = _f(g(36))
        mv_yi = _f(g(43))
        return cls(
            code=str(g(2) or ""),
            name=str(g(1) or ""),
            price=_f(g(3)),
            prev_close=_f(g(4)),
            open=_f(g(5)),
            real_change=_f(g(31)),
            vol=None if vol_hand is None else vol_hand * 100,
            amount=(lambda a: None if a is None else a * 1e4)(_f(g(37))),
            turnover=_f(g(38)),
            float_mv=None if mv_yi is None else mv_yi * 1e8,
            yesterday_change=yesterday_chg,   # 腾讯无真实昨日涨幅 → 默认 None
            source="tencent",
            degraded=degraded,
        )
