# -*- coding: utf-8 -*-
"""
选股过滤层 (重构 P2)
=================================================================================
与老 scorer.apply_filters 的**过滤项与顺序逐条一致**, 三处语义修正:

  1. 停牌判定: 老 is_suspended 读 f4/f5, 缺失时被 parse_float 转成 0 → 0<=0 →
     **判为停牌并剔除**(2026-09-01 事故: 降级行被误杀)。本层用契约 is_suspended
     返回 None(未知) → 不剔除; 且"9:25 有竞价额"本身就是非停牌的强证据, 故
     bid_amt>0 时直接判非停牌(见 DEGRADE_RULES 新增条目)。

  2. 昨涨停: 契约行没有"昨日涨停"这类东财概念标签的权威来源(腾讯行无 f103),
     老链路因此恒 False。本层以**外部昨涨停代码集合**为权威(ctx.zt_codes),
     集合不可用时才降级 concept 文本匹配(与老 is_first_board 同)。

  3. 竞价涨幅缺失: 老链路 f615 缺失 → 退 f3 → 0/当日涨幅 → 基本都通过 bidGt 上限。
     本层 bid_change=None 表示"没有竞价数据", 竞价选股没有竞价数据的票**不应入选**
     (它根本不该出现在竞价名单里) → 默认剔除, 统计在 stats["no_bid_change"]。
     可用 ctx.require_bid_change=False 关掉(竞价窗口早期数据未全时的兼容开关)。

过滤口径契约: 字段完备 + 同 zt_codes 口径下, 本层与已退役老链路的 apply_filters
逐票同结果; 差异只允许出现在"字段缺失"分支, 且每条剔除原因都必须能在 stats 里查到。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .score import ScoredRow, coarse_rank_key


# 市场范围判定(与 scorer._in_markets 同口径, 独立实现以免 picker→scorer 循环依赖)
def in_markets(code: str, markets: Optional[List[str]]) -> bool:
    """hs=沪主板60x + 深主板00x | cyb=300/301 | kcb=688/689 | **bj=北交所 4/8/920**。
    markets 为空/None → 不限制(老口径: 旧调用方不传 markets)

    🔴 2026-09-29 主人拍板「北交所纳入」: 新增 bj 分支(此前 4/8/920 一律 False);
      `900xxx`/`200xxx` 不被 4/8/920 命中 ⇒ 仍排除。
    """
    if not markets:
        return True
    c = str(code or "")
    if c.startswith(("300", "301")):
        return "cyb" in markets
    if c.startswith(("688", "689")):
        return "kcb" in markets
    if c.startswith(("4", "8", "920")):
        return "bj" in markets
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "hs" in markets
    return False


def is_st(name: Optional[str]) -> bool:
    """ST 股判定(与老 scorer.is_st 同)"""
    return bool(name) and ("ST" in name)


def is_first_board(row_code: str, concept: Optional[str],
                   zt_codes: Optional[Set[str]]) -> bool:
    """昨日涨停/连板判定。

    zt_codes: push2ex 昨涨停池名单(**权威**, 与数据源无关 — 腾讯行无 f103,
    只认 concept 会恒 False 全滤空, 2026-09-07 事故)。None = 名单不可用 → 降级 concept。
    """
    if zt_codes is not None:
        return row_code in zt_codes
    c = concept or ""
    return ("昨日涨停" in c) or ("昨日连板" in c)


@dataclass
class FilterContext:
    """过滤上下文: 评分/契约之外的**外部事实**(昨涨停名单、市场范围、策略开关)"""
    markets: Optional[List[str]] = None
    zt_codes: Optional[Set[str]] = None     # None = 名单不可用, 降级 concept 匹配
    require_bid_change: bool = True         # 竞价涨幅缺失是否剔除(见模块注释 3)
    drop_unknown_suspend: bool = False      # 停牌"未知"是否剔除(默认 False: 不误杀)
    price_gate: str = "auction"
    """价格门槛用哪个价判定:
      "auction"  定格竞价价 = 昨收×(1+竞价涨幅)**全天恒定** → 名单不漂移(默认)
      "realtime" 实时价(已退役老链路的行为) — 盘中价格一漂名单就变, 仅复现历史用
      缺失(None) → 门槛不生效, 保留该票(不误杀)
    """
    score_floor_exempt: bool = False
    """**降级豁免**评分下限(scoreFloor): 补丁源(东财点查/腾讯点查)全不可用时,
    候选行只剩 9:25 定格字段 —— 换手/量比/异动/昨日涨幅全缺 → 评分算出来的不是
    "这只票差", 而是"没有数据可算", 属**保守占位分**。此时 scoreFloor 硬门槛会把
    "快照行直出保名单"整批砍成空名单(2026-09-11 修: 方案A 降级保命路径实效)。
    豁免只作用于评分门槛, 其余过滤项(板块/ST/竞涨/市值/竞额)一律不变。"""

    @property
    def zt_available(self) -> bool:
        return self.zt_codes is not None


@dataclass
class FilterOutcome:
    """过滤结果: 保留行 + 逐原因剔除统计(可观测 = 可排查; 老链路只返回结果,
    排查"为什么这只票没了"只能人肉复算)"""
    kept: List[ScoredRow] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    def bump(self, reason: str) -> None:
        self.stats[reason] = self.stats.get(reason, 0) + 1

    @property
    def rejected(self) -> int:
        return sum(self.stats.values())


def apply_filters(rows: List[ScoredRow], f: Dict,
                  ctx: Optional[FilterContext] = None) -> FilterOutcome:
    """契约版竞价过滤。f = scorer.validate_filters 输出的筛选参数 dict。

    剔除顺序与老链路一致(先便宜后昂贵), 便于对拍逐项定位差异。
    """
    ctx = ctx or FilterContext()
    out = FilterOutcome()
    markets = ctx.markets if ctx.markets is not None else f.get("markets")

    for it in rows:
        r, sc = it.row, it.score
        code, name = r.code, r.name

        # 1) 市场范围(代码前缀判定 — 腾讯兜底无视 fs 全市场拉, 只能在此兜底)
        if not in_markets(code, markets):
            out.bump("market")
            continue

        # 2) 昨涨停/连板(limitUp 语义: 勾选=**包含**这类票, 不勾=剔除)
        if not f.get("limitUp", True) and is_first_board(code, r.concept, ctx.zt_codes):
            out.bump("first_board")
            continue

        # 3) ST / 停牌
        if not f.get("stSuspend", True):
            if is_st(name):
                out.bump("st")
                continue
            susp = r.is_suspended
            if susp is None and r.bid_amt and r.bid_amt > 0:
                susp = False          # 9:25 有竞价额 → 必非停牌(DEGRADE_RULES)
            if susp is None:
                if ctx.drop_unknown_suspend:
                    out.bump("suspend_unknown")
                    continue
            elif susp:
                out.bump("suspend")
                continue

        # 4) 竞价涨幅区间: 上限 bidGt(过高=追高风险) + 下限 bidLt(2026-09-09 新增:
        #    低开/大跌剔除 — 中石科技 9/8 竞涨 -8.01% 仍以 prob 58 混入名单事故;
        #    默认 0=竞价翻绿即剔; 负数可配置(极端低吸策略可放宽)。None → require_bid_change)
        bid_chg = r.bid_change
        if bid_chg is None:
            if ctx.require_bid_change:
                out.bump("no_bid_change")
                continue
        elif bid_chg < f.get("bidLt", 0):
            out.bump("bid_lt")
            continue
        elif bid_chg > f["bidGt"]:
            out.bump("bid_gt")
            continue

        # 5) 概率/置信度**双低**才剔除(老语义: 二者同时不满足)
        if sc.probability < f["probLt"] and sc.confidence < f["confLt"]:
            out.bump("prob_conf")
            continue

        # 5.1) 评分下限(2026-09-10 主人拍板, 全站默认 80): 单阈值硬门槛 ——
        #   与 5) 的双低剔除独立: 双低允许"高信心救低概率", scoreFloor 不看信心,
        #   评分不够就是不够(低分票展示出来只会干扰决策)。0 = 关闭该门槛。
        #   注意: 只能放精筛(评分后) — 粗筛在评分前跑, 拿不到 probability。
        #   2026-09-11: 补丁源全失败时评分失真(见 FilterContext.score_floor_exempt)
        #   → 本次豁免该门槛, 否则"快照行直出保名单"会被砍成空名单。
        if (not ctx.score_floor_exempt and f.get("scoreFloor", 0) > 0
                and sc.probability < f["scoreFloor"]):
            out.bump("score_floor")
            continue

        # 6) 市值区间(缺失 → 无法证明达标 → 剔除, 与老口径 0<floor 同结果)
        #    ★ 2026-09-20 口径改**自由流通市值**(row.mv = free_mv 优先, 缺则回退 float_mv):
        #      主人拍板「所有的流通市值改为自由流通市值」→ 门槛与 market 评分同口径,
        #      不再出现"评分用 free_mv、门槛用 float_mv"的双口径名单漂移。
        mv = r.mv_yi
        if mv is None or mv < f["floatMvFloor"]:
            out.bump("mv_floor")
            continue
        if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
            out.bump("mv_gt")
            continue

        # 7) 竞价额下限(万元口径; 缺失 → 剔除, 与老口径 0<floor 同结果)
        bid_amt_wan = None if r.bid_amt is None else r.bid_amt / 1e4
        if bid_amt_wan is None or bid_amt_wan < f["bidAmtFloor"]:
            out.bump("bid_amt")
            continue

        # 8) 价格上限(缺失 → 无法证明超限 → 保留, 与老口径 0>priceGt=False 同结果)
        #    判定价默认取**定格竞价价**(全天恒定), 不用实时价 — 否则盘中价格一漂
        #    名单就变(老链路用实时价 → 名单漂移, 已退役)。
        gate_price = (r.auction_price if ctx.price_gate == "auction" else r.price)
        if f["priceGt"] > 0 and gate_price is not None and gate_price > f["priceGt"]:
            out.bump("price_gt")
            continue

        out.kept.append(it)
    return out


COARSE_MAX = 200
"""候选上限: 与老链路 _SNAP_CANDIDATE_MAX 同值。粗筛后要按 code 拉日K(昨日涨幅)
与点查行情, 候选过多会拖慢。

⚠️ 2026-09-23 实测订正: 「120 只足以覆盖」只在**参数较严**时成立。
   按当天实际参数回溯 20 个交易日, 平均只通过 23.4 只、仅 1 天超过 120;
   但按当前这套松参数(竞价额≥500 万)回溯, 平均 143.6 只、**13/20 天触顶**。
   即触顶与否几乎完全由参数宽严决定 —— 触顶日**由排队键决定谁被砍掉**, 故它是本常量
   唯一要紧的搭档: 2026-09-23 改「定格三因子粗排分」, 2026-09-26 改「定格竞价涨幅」
   (见 coarse_filter 的排队键说明)。

★ 2026-09-23 主人指令: 名额 120 → **200**(与 stocks._SNAP_CANDIDATE_MAX 同值同改)。
   依据就是上面那段实测: 松参数下平均 143.6 只/日、20 日里 13 日触顶 ⇒ 120 是
   真实瓶颈, 触顶日候选**没机会进评分**。上调到 200 后, 绝大多数的交易日候选
   **不再被截断**, 截断只在极端放量日发生。
   ⚠️ 代价: 候选变多 ⇒ 粗筛后「按 code 拉日K + 点查行情」的请求数同比例上升,
   这是本次扩容的唯一成本(已由 200 这个量级吸收, 未改任何取数逻辑)。
   ⚠️ 两条链路必须同值: 本常量与 stocks._SNAP_CANDIDATE_MAX 任一单独改, 都会让
   「实时链路名单」与「盘后快照链路名单」在触顶日不一致(2026-09-18 双口径漂移事故)。"""


def coarse_filter(rows: Sequence[Any], f: Dict,
                  ctx: Optional[FilterContext] = None,
                  limit: int = COARSE_MAX) -> List[str]:
    """**评分前**的粗筛: 只用"定格数据即可判定"的门槛, 返回候选 code(按定格涨幅降序)。

    rows: QuoteRow 或 ScoredRow 均可(粗筛不需要评分结果, 故可跳过全市场评分)。

    存在的理由(性能): 昨日涨幅要按 code 拉日K、展示字段要按 code 点查行情,
    全市场 5500 只都做 = 加载慢的老根因。先按快照字段粗筛到 ~200 只再取数,
    与老链路 _snapshot_candidate_codes 同思路(老代码在 api 层手写, 分散且不可测)。

    粗筛**不含**需要评分的门槛(prob/conf 双低)与需要实时价的门槛(priceGt) —
    那些留给 apply_filters; 因此粗筛只会"漏不掉"任何最终该入选的票。
    ⚠️ 上面这句只在**名额够用**时成立: 一旦触顶, 被截掉的票连评分机会都没有。
       所以"触顶时谁被砍掉"完全由排队键决定 —— 见下。

    ── 排队键(2026-09-26 主人指令) ──────────────────────────────────────
    由「**定格三因子粗排分降序**」(score.coarse_rank_score, 2026-09-23 上线)改为
    「**定格竞价涨幅(bid_change)降序**」—— 即直接用"当日涨幅榜"这把市场公认的尺子。
    口径依据: 定格时点(9:25 撮合之后) C=O ⇒ **当日涨幅 ≡ 开盘涨幅 ≡ 竞价涨幅**, 三者同值。
    **名额上限(limit/COARSE_MAX)仍为 200**(2026-09-23 由 120 上调, 见常量注释)。
    🔴 **本函数全部门槛一律未动** —— 排队键只决定"触顶时谁被砍掉", 不改变任何一只票的
       入选资格(能进排序的票本就已经在门槛之内); 触顶与否仍由参数宽严决定。
    同涨幅按 code 升序, 与 score.score_rows 的并列规则一致 → 结果与输入顺序无关
    (更早的"竞价额降序"在同额时依赖输入顺序, 不可复现)。
    """
    ctx = ctx or FilterContext()
    markets = ctx.markets if ctx.markets is not None else f.get("markets")
    cand = []
    for it in rows:
        r = getattr(it, "row", it)          # ScoredRow → 取 row; QuoteRow 直接用
        if not in_markets(r.code, markets):
            continue
        if not f.get("limitUp", True) and is_first_board(r.code, r.concept, ctx.zt_codes):
            continue
        if not f.get("stSuspend", True):
            if is_st(r.name):
                continue
            susp = r.is_suspended
            if susp is None and r.bid_amt and r.bid_amt > 0:
                susp = False
            if susp:                       # 未知(None)不剔除, 同 apply_filters
                continue
        bid_chg = r.bid_change
        if bid_chg is None:
            if ctx.require_bid_change:
                continue
        elif bid_chg < f.get("bidLt", 0):
            continue                                # 低开/大跌剔除(同 apply_filters)
        elif bid_chg > f["bidGt"]:
            continue
        # ★ 2026-09-20: 取值改 row.mv_yi(自由流通优先), 与精筛/评分同口径。
        #   ⚠️ mv_yi 是忠实的(nv=0 → 0.0), 但本处语义要求 **0 也算"未知"** ——
        #     0 市值不存在, 且粗筛跑在补丁源之前(见下), 此处把 0 当"不达标"剔除
        #     等于让行情源可用性决定名单。故用 `mv_yi or None` 归零。
        mv = r.mv_yi or None
        # 2026-09-18(v4.11.28): 市值**未知**(None) → 不在此剔除, 留给 apply_filters。
        #   理由: 粗筛在补丁源(东财点查/腾讯点查)之前跑, 此时用不到真市值; 而补丁之后
        #   的 apply_filters 能拿到 f21/f44 真值。在这里把"暂时没数据"当成"不达标"剔除,
        #   等于让**行情源可用性决定名单** —— 9/17 东财全分区失败时, 5335 只快照行
        #   市值未知, 其中 1335 只因腾讯补值上限被截断, 整批被 floatMvFloor 误杀。
        #   精筛仍保持"缺失 → 无法证明达标 → 剔除"(与老口径一致), 故最终语义不变,
        #   只是把判定推迟到**有真值的那一刻**。
        # ★ 2026-09-20: 取值改 row.mv_yi(自由流通优先), 与精筛/评分同口径。
        if mv is not None:
            if mv < f["floatMvFloor"]:
                continue
            if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
                continue
        bid_amt_wan = None if r.bid_amt is None else r.bid_amt / 1e4
        if bid_amt_wan is None or bid_amt_wan < f["bidAmtFloor"]:
            continue
        cand.append((r.code, r.bid_change))
    cand.sort(key=lambda x: coarse_rank_key(x[1], x[0]))
    return [c for c, _ in cand[:limit]]


def apply_spot_filters(rows: List[ScoredRow], f: Dict,
                       ctx: Optional[FilterContext] = None) -> FilterOutcome:
    """盘中实时(spot)过滤。2026-09-28 重建。

    ⚠️ 本函数是 spot 策略的**专用**过滤层, 与竞价 apply_filters **分离** ——
    两者门槛语义不同(spot 看**实时**涨幅/量比/换手; 竞价看**定格**竞价涨幅),
    混用会静默改变名单。

    与竞价 apply_filters 的差异(逐条):
      1. **多消费 6 个盘中参数**(2026-09-09 spot 下线后它们零消费点,
         即 v4.11.x 期间"前端能勾、选了不生效"的静默失效):
         chgGt(实时涨幅上限) / chgFloor(下限) / volRatioFloor(量比下限) /
         turnoverFloor(换手下限) / turnoverGt(换手上限) / spotExcludeZT(剔除已涨停)。
      2. 涨幅判定用 **real_change(实时)** 而非 bid_change(定格竞价)。
      3. 竞价额门槛(bidAmtFloor)**不参与** —— 盘中距 9:25 已远, 竞价额不是选股依据
         (老实现同样不含此门槛)。

    剔除顺序照老 apply_spot_filters(先便宜后昂贵), 便于对拍。
    zt_codes 与涨停池: 老实现靠 it["limitBoards"] 判"是否已涨停"(涨停池 lb 字段);
    本层从 row.warn_type 取不到该信息, 故 **spotExcludeZT 依赖调用方在过滤前
    为行注入 `_spot_zt` 标记**(见 pipeline_spot); 未注入时该门槛不生效(不误杀)。
    """
    ctx = ctx or FilterContext()
    out = FilterOutcome()
    markets = ctx.markets if ctx.markets is not None else f.get("markets")

    for it in rows:
        r, sc = it.row, it.score
        code, name = r.code, r.name

        # 1) 市场范围
        if not in_markets(code, markets):
            out.bump("market")
            continue

        # 2) 昨涨停/连板(limitUp 语义同竞价: 勾选=包含, 不勾=剔除)
        if not f.get("limitUp", True) and is_first_board(code, r.concept, ctx.zt_codes):
            out.bump("first_board")
            continue

        # 3) ST / 停牌(盘中: 有成交量即非停牌)
        if not f.get("stSuspend", True):
            if is_st(name):
                out.bump("st")
                continue
            susp = r.is_suspended
            if susp is None and r.bid_amt and r.bid_amt > 0:
                susp = False
            if susp is None:
                if ctx.drop_unknown_suspend:
                    out.bump("suspend_unknown")
                    continue
            elif susp:
                out.bump("suspend")
                continue

        # 4) spotExcludeZT: 剔除已涨停封板(买不进)。标记由调用方注入。
        if f.get("spotExcludeZT") and getattr(r, "_spot_zt", False):
            out.bump("spot_zt")
            continue

        # 5) 实时涨幅区间 [chgFloor, chgGt]
        real_chg = r.real_change
        if real_chg is None:
            out.bump("no_real_change")
            continue
        if real_chg < f.get("chgFloor", 0):
            out.bump("chg_floor")
            continue
        if f.get("chgGt", 0) > 0 and real_chg > f["chgGt"]:
            out.bump("chg_gt")
            continue

        # 6) 量比下限
        vr = r.vol_ratio
        vr_floor = f.get("volRatioFloor", 0) or 0
        if vr_floor > 0 and (vr is None or vr < vr_floor):
            out.bump("vol_ratio")
            continue

        # 7) 换手率区间 [turnoverFloor, turnoverGt]
        to = r.turnover
        to_floor = f.get("turnoverFloor", 0) or 0
        to_gt = f.get("turnoverGt", 0) or 0
        if to_floor > 0 and (to is None or to < to_floor):
            out.bump("turnover_floor")
            continue
        if to_gt > 0 and to is not None and to > to_gt:
            out.bump("turnover_gt")
            continue

        # 8) 概率/置信度双低
        if sc.probability < f["probLt"] and sc.confidence < f["confLt"]:
            out.bump("prob_conf")
            continue

        # 9) 评分下限
        if (not ctx.score_floor_exempt and f.get("scoreFloor", 0) > 0
                and sc.probability < f["scoreFloor"]):
            out.bump("score_floor")
            continue

        # 10) 市值区间(同竞价: mv_yi 自由流通优先)
        mv = r.mv_yi
        if mv is None or mv < f["floatMvFloor"]:
            out.bump("mv_floor")
            continue
        if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
            out.bump("mv_gt")
            continue

        # 11) 价格上限(spot 用**实时价** —— 盘中价格就是当下的, 不存在"定格"概念)
        if f["priceGt"] > 0 and r.price is not None and r.price > f["priceGt"]:
            out.bump("price_gt")
            continue

        out.kept.append(it)
    return out


def kept_codes(outcome: FilterOutcome) -> List[str]:
    return [it.code for it in outcome.kept]


def summarize(outcome: FilterOutcome, total: int) -> Tuple[int, Dict[str, int]]:
    """(保留数, 剔除原因统计) — 日志/对拍报告用"""
    return len(outcome.kept), dict(outcome.stats)
