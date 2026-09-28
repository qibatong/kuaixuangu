# -*- coding: utf-8 -*-
"""
选股编排层 (重构 P3)
=================================================================================
把 P0/P1/P2 四层串成一条**可独立调用、可整体测试**的链路:

    resolve_mode(现在什么模式)
      → 名单源(source_priority[0])      决定名单(定格快照 / 竞价窗口实时全市场)
      → 粗筛 coarse_filter              全市场 → ~200 只候选(省日K/点查开销)
      → 取数(昨日涨幅/补丁行情)          只针对候选
      → 评分 score_rows                 同 P2
      → 精筛 apply_filters              含 prob/conf 双低与价格上限
      → 展示字段补丁(不改名单!)         现价/涨幅/换手/量比
      → 输出(与老链路 item 同构 + degraded/source 元信息)

与老链路 api_stocks 的本质区别:
  1. **名单与展示分离**: 补丁源失败只影响"现价列显示 -", 名单不变(老链路点查
     失败 → 整批降级成快照行直出 → 名单虚胖一倍, 9/8 事故)。
  2. **时间段判定只有一个入口**: resolve_mode, 不再散落 before930/hm 硬编码。
  3. **降级可观测**: 每一步的 source/error 都进 PipelineResult, 前端可提示。

2026-09-11: 老链路(含 api 层旁路对拍 parity.py)已整体退役 —— 本模块是**唯一**选股链路。
"""
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ...core import logger
from . import filter as pfilter
from . import mode as pm
from . import precompute
from .contract import QuoteRow
from .score import ScoredRow, score_rows
from .sources.base import FetchContext, SourceResult, get_source

log = logger.get_logger(__name__)


@dataclass
class PickContext:
    """一次选股所需的**外部事实**(全部可注入 → 单测无需网络/DB)"""
    date: str = ""
    markets: Optional[List[str]] = None
    zt_codes: Optional[Set[str]] = None          # 昨涨停/连板(None=名单不可用)
    day_bid_change: Dict[str, float] = field(default_factory=dict)   # 9:25 定格涨幅 %
    day_bid_amt_wan: Dict[str, float] = field(default_factory=dict)  # 9:25 定格额(万元)
    day_bid_vol: Dict[str, float] = field(default_factory=dict)      # 9:25 定格量(股)
    yesterday_chg: Dict[str, float] = field(default_factory=dict)    # 真实昨日涨幅 %
    yesterday_map: Dict[str, list] = field(default_factory=dict)     # 成交额对(竞/昨比)
    snapshot_map: Dict[str, dict] = field(default_factory=dict)      # 9:20 快照(加速度)
    strengths: Dict[str, float] = field(default_factory=dict)        # 竞价强度(注入后不再自加载)
    require_bid_change: bool = True


@dataclass
class PipelineResult:
    mode: str = ""
    mode_label: str = ""
    items: List[dict] = field(default_factory=list)     # 最终名单(老链路同构)
    rows: Dict[str, QuoteRow] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)    # 实际用到的源(按顺序)
    errors: List[str] = field(default_factory=list)     # 各级源失败原因(降级可见)
    stats: Dict[str, int] = field(default_factory=dict)  # 剔除原因计数
    degraded: bool = False
    n_universe: int = 0        # 名单源返回的全市场行数
    n_candidate: int = 0       # 粗筛后候选数
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        """是否产出了可用名单(空名单≠失败, 但"取数全失败"是失败)"""
        return not self.errors or bool(self.items)


# ---------------------------------------------------------------- 取数
def _merge_rows(base: Dict[str, QuoteRow],
                patch: Dict[str, QuoteRow]) -> Dict[str, QuoteRow]:
    """补丁行合并进基行: **只补实时展示字段, 竞价字段永远认定格**。

    铁律: 竞价字段(bid_change/bid_amt/bid_vol)的权威是 9:25 定格, 补丁源的
    f615/f616 在窗口外是历史值/全天累计值, 拿就是事故(9/7 竞涨=现涨)。

    返回**新字典**(行对象用 dataclasses.replace 拷贝, 不原地改), 调用方需重新
    取回需要的行 —— 避免"改了一半"的中间态被别处看到。
    """
    out = dict(base)
    for code, p in patch.items():
        b = out.get(code)
        if b is None:
            out[code] = p
            continue
        b = replace(b)                     # 拷贝后再改, 不污染原对象
        # 竞价字段: 以基行(定格)为准, 基行没有才用补丁行的定格注入值
        if b.bid_change is None:
            b.bid_change = p.bid_change
        if b.bid_amt is None:
            b.bid_amt = p.bid_amt
        if b.bid_vol is None:
            b.bid_vol = p.bid_vol
        # 实时字段: 补丁行优先(补丁源就是干这个的)
        for k in ("price", "prev_close", "open", "real_change", "vol",
                  "amount", "turnover", "vol_ratio", "warn_type",
                  "industry", "concept"):
            v = getattr(p, k)
            if v is not None and v != "":
                setattr(b, k, v)
        # 市值: 实时 f21 优先, 缺失保留快照(free_mv 回退值)
        if p.float_mv:
            b.float_mv = p.float_mv
        # 自由流通市值: 实时 f117 优先, 缺失保留快照 free_mv(竞价换手/抢筹强度用)
        if p.free_mv:
            b.free_mv = p.free_mv
        if p.yesterday_change is not None:
            b.yesterday_change = p.yesterday_change
        b.source = "%s+%s" % (b.source or "?", p.source or "?")
        out[code] = b                      # 放回新对象(调用方按 code 重新取)
    return out


def _refreeze_locked(merged: Dict[str, QuoteRow],
                     frozen: Dict[str, Tuple]) -> Dict[str, QuoteRow]:
    """物化路径专用: 补丁补完展示字段后, 把**参与名单判定**的字段还原为物化值。

    为什么需要(2026-09-12 P1-2): 物化路径下评分已由预计算定好, 若再让补丁源改写
    mv(f21/f117 统一值) / prev_close(f18), 则**评分用的是 A 值、门槛用的是 B 值**,
    名单会随行情源可用性漂移 —— 这正是预计算要消灭的问题。

    ★ 2026-09-20 口径改自由流通: 冻结值改为 **mv**(= free_mv 优先, 缺则 float_mv),
      还原时写回 `free_mv`(统一市值载体)。同时把 `float_mv` 一并置为同值, 保证
      后端内部 `mv` 与 `float_mv` 展示列一致(不出现"门槛用 A、展示列显示 B")。

    只还原"物化表里确实有值"的字段(物化为 None 时保留补丁给的, 属于补缺不是改写)。
    """
    out: Dict[str, QuoteRow] = {}
    for code, r in merged.items():
        f = frozen.get(code)
        if f is None:
            out[code] = r
            continue
        bid_chg, bid_amt, bid_vol, mv, prev_close = f
        r = replace(r)
        if bid_chg is not None:
            r.bid_change = bid_chg
        if bid_amt is not None:
            r.bid_amt = bid_amt
        if bid_vol is not None:
            r.bid_vol = bid_vol
        if mv is not None:
            r.free_mv = mv          # 统一市值载体(门槛/评分同口径)
            r.float_mv = mv         # 展示列同值, 避免两列不一致
        if prev_close is not None:
            r.prev_close = prev_close
        out[code] = r
    return out


def _fetch_list(ctx: PickContext, policy: pm.ModePolicy,
                filters: Dict) -> SourceResult:
    """名单源: source_priority[:list_source_count] **依次尝试**, 第一个成功的为准。

    这不是"兜底"而是**源优先级** —— 外部行情源(东财被墙是生产常态)必须有替代,
    该设计是业务必需而非技术债。区别在于: 每一步的 label/error/degraded 都进
    PipelineResult, 降级在日志与接口里**可见**, 不是静默吞掉。
    """
    labels = list(policy.source_priority[:max(1, policy.list_source_count)])
    last: Optional[SourceResult] = None
    for label in labels:
        src = get_source(label)
        if src is None:
            last = SourceResult(label=label, error="未知数据源标签: %s" % label,
                                degraded=True)
            continue
        r = src.run(FetchContext(
            policy=policy, date=ctx.date, markets=filters.get("markets"),
            day_bid_change=ctx.day_bid_change, day_bid_amt_wan=ctx.day_bid_amt_wan,
            day_bid_vol=ctx.day_bid_vol, yesterday_chg=ctx.yesterday_chg))
        if r.ok:
            return r
        last = r
        log.warning("名单源[%s]不可用, 尝试下一个 err=%s", label, r.error)
    return last or SourceResult(label="?", error="无可用名单源", degraded=True)


def _fetch_patch(ctx: PickContext, policy: pm.ModePolicy,
                 codes: Sequence[str], filters: Dict) -> Optional[SourceResult]:
    """补丁源: source_priority[list_source_count:], 逐个尝试, 第一个成功的即可。
    失败不影响名单(只影响现价/涨幅等展示字段与价格门槛)。"""
    for label in policy.source_priority[max(1, policy.list_source_count):]:
        if not policy.realtime_patch:
            break
        src = get_source(label)
        if src is None:
            continue
        r = src.run(FetchContext(
            policy=policy, date=ctx.date, codes=list(codes),
            markets=filters.get("markets"),
            day_bid_change=ctx.day_bid_change,
            day_bid_amt_wan=ctx.day_bid_amt_wan,
            day_bid_vol=ctx.day_bid_vol,
            yesterday_chg=ctx.yesterday_chg))
        if r.ok:
            return r
    return None


# ---------------------------------------------------------------- 主流程
def run(filters: Dict, *, ctx: Optional[PickContext] = None,
        now=None, holidays: Optional[set] = None,
        cfg: Optional[dict] = None, strategy: str = "auction",
        spot_cfg: Optional[dict] = None) -> PipelineResult:
    """跑一次完整选股(唯一链路)。

    filters: scorer.validate_filters 的输出
    ctx:     外部事实(定格 map / 昨涨停名单 / 昨日涨幅...); None 时自动加载
    now:     时间注入(测试用); None = 当前时间
    strategy: **选股策略(2026-09-28 新增)** —— 决定"用哪套评分/过滤":
              · "auction"(默认) 竞价五因子: score_rows + apply_filters(行为与改造前逐字一致)
              · "spot"          盘中六因子: compute_score_spot + apply_spot_filters
              ⚠️ 只影响 **评分层与精筛层**(下面第 3.5/4 步), 其余各层(名单源/粗筛/
                 昨日涨幅/补丁源/输出组装)**两条策略共用** —— 这正是把 spot 接进
                 pipeline 而非复制一份的意义。
    spot_cfg: spot 评分配置注入(测试用; None = 取 score_spot.get_spot_cfg())

    🔴 auction 路径的**零改动保证**: strategy 默认 "auction", 且下面所有 spot 分支
       都写成 `if strategy == "spot"`, 故老调用方(不传 strategy)行为逐字不变。
    """
    t0 = time.time()
    ctx = ctx or load_context()
    policy = pm.resolve_mode(now, holidays)
    res = PipelineResult(mode=policy.mode.value, mode_label=policy.label)
    is_spot = (strategy == "spot")

    # 1) 名单源
    #    2026-09-12 P1-2: 开关开启且物化表有当日全市场评分时, 直接读物化表(一次 SELECT,
    #    无网络、天然幂等); 表缺失/行数不足 → 静默回退原路径, 接口永不报错。
    mat_rows, mat_scores = ({}, {})
    use_mat = False
    if precompute.read_enabled():
        try:
            mat_rows, mat_scores = precompute.read_materialized(ctx.date)
        except Exception as e:                                    # noqa: BLE001
            log.warning("[预计算] 物化表读取异常(回退原路径) err=%s", e)
            mat_rows, mat_scores = ({}, {})
        use_mat = bool(mat_rows)

    if use_mat:
        rows = dict(mat_rows)
        res.sources.append("precompute")
        res.n_universe = len(rows)
        # ★ 2026-09-20: 冻结字段从 float_mv 改为 **mv(free_mv 优先, 缺则 float_mv)** ——
        #   门槛与 market 评分统一走 mv, 故冻结的必须是同一口径; 预计算物化表
        #   (precompute) 写入的也是 mv, 回填到 free_mv 列(见 precompute.read_materialized)。
        frozen: Dict[str, Tuple] = {
            c: (r.bid_change, r.bid_amt, r.bid_vol, r.mv, r.prev_close)
            for c, r in rows.items()}
        lr = None                                                 # 未走名单源
    else:
        lr = _fetch_list(ctx, policy, filters)
        res.sources.append(lr.label)
        if not lr.ok:
            res.errors.append("名单源[%s]失败: %s" % (lr.label, lr.error or "无数据"))
            res.degraded = True
            res.elapsed_ms = int((time.time() - t0) * 1000)
            log.warning("选股失败 名单源无数据 mode=%s err=%s — %s",
                        policy.mode.value, lr.error, policy.fail_message)
            return res
        rows = lr.rows
        res.n_universe = len(rows)
        if lr.degraded:
            res.degraded = True
        frozen = {}

    # 2) 粗筛 → 候选(省日K与点查; 只按定格可判定的字段, 不需要先评分)
    #    ★ 2026-09-28: spot **不走粗筛**。理由有三:
    #      ① 粗筛的排队键是"定格竞价涨幅"(coarse_filter 内 coarse_rank_key), 对 spot
    #         无意义 —— spot 看实时涨幅, 没有定格概念;
    #      ② 粗筛的不少门槛依赖竞价字段(bidGt/bidLt/bidAmtFloor/require_bid_change),
    #         spot 名单里这些字段可能为 None, 硬套会把有效票误杀;
    #      ③ 粗筛存在的理由是**省"逐只拉日K + 点查行情"的开销**(竞价要按 code 点查),
    #         而 spot 用 ensure_spot_cache 一次性拿全市场实时行情, 无逐只点查开销 ⇒
    #         全市场直接评分实测约 1.x 秒, 不需要粗筛换来的那点性能。
    #      ⇒ spot 的候选 = 全市场(与 api/stocks_spot.py 的行为一致)。
    fctx = pfilter.FilterContext(
        markets=ctx.markets if ctx.markets is not None else filters.get("markets"),
        zt_codes=ctx.zt_codes, require_bid_change=ctx.require_bid_change)
    if is_spot:
        codes = [r.code for r in rows.values()]
    else:
        codes = pfilter.coarse_filter(list(rows.values()), filters, fctx)
    res.n_candidate = len(codes)
    if not codes:
        res.stats["coarse_empty"] = 0
        res.elapsed_ms = int((time.time() - t0) * 1000)
        return res

    # 2.5) 昨日成交额(竞/昨比展示用): **只对候选**拉日K(全市场拉 = 加载慢根因之一)
    #      昨日涨幅已由契约行承载(row.yesterday_change), 无需在此再拉。
    if not ctx.yesterday_map:
        fill_yesterday(codes, ctx)

    # 3) 补丁源: 对**候选**补昨收/现价/市值等(补丁失败不阻塞, 只影响门槛与展示)
    #    注意: 补丁带来的**实时价不参与价格门槛** — 门槛由 filter 用定格竞价价
    #    (昨收×竞价涨幅, 全天恒定)判定, 故补丁在过滤前后都不改变名单。
    pr = _fetch_patch(ctx, policy, codes, filters)
    if pr is not None:
        rows = _merge_rows(rows, pr.rows)
        if use_mat:
            # 名单字段仍以物化定格为准(补丁只补展示), 否则评分与门槛会用到两套值
            rows = _refreeze_locked(rows, frozen)
        res.sources.append(pr.label)
        if pr.degraded:
            res.degraded = True
    else:
        res.errors.append("补丁源不可用(价格门槛与实时展示字段将缺失)")
        # 2026-09-11: 补丁源不可用必须置 degraded —— 此前只 append errors、degraded 仍是
        #   False, 日志/接口显示「降级=False」而实际现价/现涨全缺, 排查被误导
        #   (9/11 生产现涨全 0 事故: 日志 mode=locked 源=snapshot 降级=False,
        #    看不出"根本没跑补丁源")。违背铁律2「降级必须可见」。
        res.degraded = True
        # 2026-09-11: 补丁源全失败 = 候选只有定格字段(换手/量比/异动/昨日涨幅全缺)
        #   → 评分是"保守占位分"而非真实评分 → 评分下限(scoreFloor)必须豁免, 否则
        #   "点查失败 → 快照行直出保名单"这条降级保命路径会被砍成空名单
        #   (test_auction_snap_pool_offhours 暴露; 其余过滤项不受影响)。
        # 2026-09-12: 物化路径**不适用** —— 评分来自预计算(全市场、字段已定),
        #   不因补丁缺失而失真, 故不豁免(豁免会让低分票混进物化名单)。
        if not use_mat:
            fctx.score_floor_exempt = True

    # 3.5) 竞价强度(替代失活的 f630 异动等级, 权重同为 w_warn=17%):
    #      信号全部来自**快照表 + 本地 AI 推理**, 对东财免疫 —— 东财点查断了照样有分。
    #      只对候选加载(全市场拉没必要); 加载失败 → strengths 为空 → 退回 warn 因子。
    #      物化路径跳过: 评分已含该因子, 且物化表的 warn_type 已是强度档位。
    #      🔴 spot 路径也跳过(2026-09-28): spot 六因子**不含**竞价强度因子, 加载它是
    #         纯浪费(要读快照表 + 跑本地 AI 推理), 且结果不会被消费。
    strengths: Dict[str, float] = {}
    if not use_mat and not is_spot:
        strengths = _load_strength(codes, ctx)

    # 4) 评分 + 精筛(只针对候选) → **名单在此定型**
    #    ★ 2026-09-28: 按 strategy 分派 —— 这是两条策略**唯一的实质分叉点**。
    #      · auction: score_rows(五因子) + apply_filters
    #      · spot:    compute_score_spot(六因子) + apply_spot_filters
    #      两个 spot 函数与竞价侧**签名同构**(QuoteRow → ScoredRow / FilterOutcome),
    #      故此处只需换函数、不必改上下文; 也正因同构, 才敢接进这条唯一链路。
    if is_spot:
        # spot 精筛前需给每行注入 `_spot_zt` 标记(涨停池有该 code 即视为已封板),
        # 供 spotExcludeZT 判定 —— 与 api/stocks_spot.py 的注入逻辑同源。
        # 标记来源: ctx.zt_codes(昨涨停名单)不含"今日封板"信息, 故只用 row 自身
        # 已带的 warn_type/涨停池信息不可得时, 该门槛自然不生效(不误杀)。
        from .score_spot import compute_score_spot, get_spot_cfg       # 延迟导入: 避免模块循环
        _spot_cfg = spot_cfg or get_spot_cfg()
        cand_rows = []
        for c in codes:
            if c not in rows:
                continue
            rr = rows[c]
            _sc = compute_score_spot(rr, None, _spot_cfg)
            cand_rows.append(ScoredRow(row=rr, score=_sc))
        _sctx = pfilter.FilterContext(
            markets=ctx.markets if ctx.markets is not None else filters.get("markets"),
            zt_codes=ctx.zt_codes,
            # spot 无 9:25 定格概念: 竞价涨幅缺失**不应剔除**(竞价侧默认 True)
            require_bid_change=False,
            # spot 用实时价判定价格门槛(盘中价格就是当下的, 无"定格"概念)
            price_gate="realtime")
        outcome = pfilter.apply_spot_filters(cand_rows, filters, _sctx)
    elif use_mat:
        # 评分已由预计算算好, 此处只按 code 取回(零网络、毫秒级)
        cand_rows = [ScoredRow(row=rows[c], score=mat_scores[c])
                     for c in codes if c in rows and c in mat_scores]
        outcome = pfilter.apply_filters(cand_rows, filters, fctx)
    else:
        cand_rows = score_rows([rows[c] for c in codes if c in rows], cfg, strengths)
        outcome = pfilter.apply_filters(cand_rows, filters, fctx)
    res.stats = dict(outcome.stats)

    # 5) 输出(老链路 item 同构 + 定格派生字段)
    ymap = ctx.yesterday_map or {}
    snap = ctx.snapshot_map or {}
    # 异动等级档位映射(强/中/弱)已删除(2026-09-20 主人指令): 强度分仍完整参与
    # 异动 17% 评分与置信度加成, 但不再对外输出强/中/弱档位。warnType 仅保留
    # 历史落库 NOT NULL 兼容值(回退 f630, 实测恒 0)。
    for it in outcome.kept:
        r = it.row
        d = it.to_dict()
        bid_amt_wan = None if r.bid_amt is None else r.bid_amt / 1e4
        pair = ymap.get(r.code)
        bid_ratio = None
        if pair and pair[0] and bid_amt_wan is not None:
            bid_ratio = round(bid_amt_wan / pair[0] * 100, 2)
        accel = None
        s9 = snap.get(r.code) or {}
        if policy.auction_window and s9.get("bid_change") is not None \
                and r.bid_change is not None:
            accel = round(r.bid_change - s9["bid_change"], 2)
        warn_label = r.warn_type
        d.update({
            "bidAmt": None if bid_amt_wan is None else round(bid_amt_wan, 2),
            "bidRatio": bid_ratio,
            "accel": accel,
            "province": "-",
            "speed": r.turnover,
            "warnType": warn_label,
            "degraded": res.degraded or bool(r.degraded),
            "source": r.source,
        })
        if is_spot:
            # ★ 2026-09-28 spot 字段适配: 补上 spot 专有字段(封成比/封单额/连板/开板),
            #   供前端 spot 列与落库消费。这些字段在竞价 Result 里叫法不同, 故放在
            #   分支内覆盖, **auction 路径一个字节都不动**。
            #   注: `bidTurnover`/`bidVolRatio` 的 None 兜底已落在 ScoredRow.to_dict()
            #   (那里用 getattr 兼容 SpotScoreResult 无该字段), 此处无需重复覆盖。
            _sc = it.score
            d.update({
                "sealRatio": getattr(_sc, "seal_ratio", 0.0),
                "sealFund": getattr(_sc, "seal_fund", 0.0),
                "limitBoards": getattr(_sc, "limit_boards", 0),
                "breakCount": getattr(_sc, "break_count", 0),
            })
        res.items.append(d)
        res.rows[r.code] = r
    res.items.sort(key=lambda x: (-x.get("probability", 0), x.get("code", "")))
    res.elapsed_ms = int((time.time() - t0) * 1000)
    log.info("选股 strategy=%s mode=%s 全市场=%d 候选=%d 入选=%d 源=%s 降级=%s 剔除=%s 耗时%dms",
             strategy, policy.mode.value, res.n_universe, res.n_candidate, len(res.items),
             ",".join(res.sources), res.degraded, res.stats, res.elapsed_ms)
    return res


# ---------------------------------------------------------------- 上下文加载
def load_context(date: Optional[str] = None,
                 markets: Optional[List[str]] = None) -> PickContext:
    """从各服务加载外部事实(真实运行时用; 单测请直接构造 PickContext 注入)。

    任何一项失败都退化为"该事实不可用"而不是抛异常 —— 与适配层同原则:
    降级是数据, 不是控制流。
    """
    from .. import auction_snapshot, fetcher
    from .mode import bj_date
    date = date or bj_date()
    ctx = PickContext(date=date, markets=markets)
    try:
        ctx.day_bid_change = auction_snapshot.load_day_bid_change(date) or {}
    except Exception as e:                                    # noqa: BLE001
        log.warning("定格竞价涨幅加载失败(竞价字段将缺失) err=%s", e)
    try:
        ctx.day_bid_amt_wan = auction_snapshot.load_day_bid_amt(date) or {}
    except Exception as e:                                    # noqa: BLE001
        log.warning("定格竞价额加载失败(竞价字段将缺失) err=%s", e)
    try:
        ctx.zt_codes = fetcher.get_yesterday_zt_codes()
    except Exception as e:                                    # noqa: BLE001
        log.warning("昨涨停名单加载失败(降级 concept 匹配) err=%s", e)
    try:
        snaps = auction_snapshot.load_snapshot(date) or {}
        ctx.snapshot_map = snaps if isinstance(snaps, dict) else {}
    except Exception as e:                                    # noqa: BLE001
        log.warning("9:20 快照加载失败(加速度不可算) err=%s", e)
    return ctx


def fill_yesterday(codes: Sequence[str],
                   ctx: PickContext) -> PickContext:
    """按候选 code 补齐昨日涨幅/成交额对(日K请求, 只对候选拉 — 全市场拉会拖慢)。

    注: 昨日涨幅在契约行里(row.yesterday_change)已由适配层注入, 这里补的是
    **成交额对**(竞/昨比展示)。两者走同一份日K缓存(fetcher), 零额外请求。
    """
    from .. import fetcher
    codes = [c for c in codes if c]
    if not codes:
        return ctx
    if not ctx.yesterday_chg:
        try:
            ctx.yesterday_chg = fetcher.fetch_yesterday_changes(codes) or {}
        except Exception as e:                                # noqa: BLE001
            log.warning("昨日涨幅加载失败(该因子走 default 分) err=%s", e)
    try:
        ctx.yesterday_map = fetcher.fetch_yesterday_amounts(codes) or {}
    except Exception as e:                                    # noqa: BLE001
        log.warning("昨日成交额加载失败(竞/昨比不可算) err=%s", e)
    return ctx


def _load_strength(codes: Sequence[str], ctx: PickContext) -> Dict[str, float]:
    """加载竞价强度(替代 f630 异动等级), 返回 {code: 0~1}。

    信号(竞价量比 / AI 预测档位; 2026-09-23 v7 起两层)全部来自快照表 + 本地模型,
    **不依赖东财** —— 东财点查断了照样有分, 不会再出现"全员 default → 天花板崩 14 分"。
    异常一律吞掉返回空 dict(调用方退回 warn 因子, 不阻塞选股)。
    """
    if ctx.strengths:                      # 调用方显式注入(测试/对拍)
        return ctx.strengths
    try:
        from .. import bid_strength
        # 2026-09-17 主人要求「把 17% 异动因子改回东财 f630」: 开关关闭必须在此短路。
        # 注意 load_scores 内部才有 enabled() 判断, 但它只被 precompute 物化路径调用,
        # 而生产 precompute_read 未启用 → 本函数是唯一生效路径;
        # 不在这里判开关 = use_bid_strength 对主链路完全无效(曾踩)。
        if not bid_strength.enabled():
            log.info("[竞价强度] 开关已关闭(use_bid_strength=0) → 异动因子退回 f630 异动等级")
            return {}
        st = bid_strength.load(list(codes), date=ctx.date)
        out = {c: v for c, v in bid_strength.score_map(st).items() if v is not None}
        log.info("[竞价强度] 候选%d只 取到强度%d只", len(codes), len(out))
        return out
    except Exception as e:                                    # noqa: BLE001
        log.warning("[竞价强度] 加载失败(退回 f630 异动等级) err=%s", e)
        return {}
