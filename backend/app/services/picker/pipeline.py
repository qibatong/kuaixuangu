# -*- coding: utf-8 -*-
"""
选股编排层 (重构 P3)
=================================================================================
把 P0/P1/P2 四层串成一条**可独立调用、可整体测试**的链路:

    resolve_mode(现在什么模式)
      → 名单源(source_priority[0])      决定名单(定格快照 / 竞价窗口实时全市场)
      → 粗筛 coarse_filter              全市场 → ~120 只候选(省日K/点查开销)
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
from typing import Any, Dict, List, Optional, Sequence, Set

from ...core import logger
from . import filter as pfilter
from . import mode as pm
from .contract import QuoteRow
from .score import ScoredRow, score_rows
from .sources.base import FetchContext, SourceResult, get_source

log = logger.get_logger(__name__)


_QC_LABEL = {"amt": "竞额", "chg": "涨幅", "last": "末秒"}
_QC_UNIT = {"amt": "%", "chg": "个百分点", "last": "个百分点"}


def _qc_of(code: str, ctx: "PickContext") -> dict:
    """抢筹输出(2026-09-09): 明细优先(带类型 amt/chg/last + 各自幅度 + 中文摘要),
    无明细才退回纯代码集合(只打标)。口径与老链路抢筹打标一致。"""
    d = (ctx.qiangchou_detail or {}).get(code)
    if d and d.get("types"):
        types = [t for t in d["types"] if t in _QC_LABEL]
        parts = ["%s抢筹 %s%s" % (_QC_LABEL[t], d.get(t), _QC_UNIT[t])
                 for t in types if d.get(t) is not None]
        return {"qiangchou": 1, "qcType": "+".join(types),
                "qcAmt": d.get("amt"), "qcChg": d.get("chg"), "qcLast": d.get("last"),
                "qcText": "；".join(parts) or "命中竞价抢筹", "qcFallback": 0}
    hit = 1 if (ctx.qiangchou_codes and code in ctx.qiangchou_codes) else 0
    return {"qiangchou": hit, "qcType": "qc" if hit else "",
            "qcAmt": None, "qcChg": None, "qcLast": None,
            "qcText": "命中竞价抢筹" if hit else "", "qcFallback": 0}


@dataclass
class PickContext:
    """一次选股所需的**外部事实**(全部可注入 → 单测无需网络/DB)"""
    date: str = ""
    markets: Optional[List[str]] = None
    zt_codes: Optional[Set[str]] = None          # 昨涨停/连板(None=名单不可用)
    qiangchou_codes: Optional[Set[str]] = None   # 竞价抢筹(None=回退公式)
    qiangchou_detail: Optional[Dict[str, dict]] = None  # 抢筹明细{code:{types,amt,chg,last}}
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
        if p.yesterday_change is not None:
            b.yesterday_change = p.yesterday_change
        b.source = "%s+%s" % (b.source or "?", p.source or "?")
        out[code] = b                      # 放回新对象(调用方按 code 重新取)
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
        cfg: Optional[dict] = None) -> PipelineResult:
    """跑一次完整选股(唯一链路)。

    filters: scorer.validate_filters 的输出
    ctx:     外部事实(定格 map / 昨涨停名单 / 昨日涨幅...); None 时自动加载
    now:     时间注入(测试用); None = 当前时间
    """
    t0 = time.time()
    ctx = ctx or load_context()
    policy = pm.resolve_mode(now, holidays)
    res = PipelineResult(mode=policy.mode.value, mode_label=policy.label)

    # 1) 名单源
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

    # 2) 粗筛 → 候选(省日K与点查; 只按定格可判定的字段, 不需要先评分)
    fctx = pfilter.FilterContext(
        markets=ctx.markets if ctx.markets is not None else filters.get("markets"),
        zt_codes=ctx.zt_codes, require_bid_change=ctx.require_bid_change)
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
        res.sources.append(pr.label)
        if pr.degraded:
            res.degraded = True
    else:
        res.errors.append("补丁源不可用(价格门槛与实时展示字段将缺失)")
        # 2026-09-11: 补丁源全失败 = 候选只有定格字段(换手/量比/异动/昨日涨幅全缺)
        #   → 评分是"保守占位分"而非真实评分 → 评分下限(scoreFloor)必须豁免, 否则
        #   "点查失败 → 快照行直出保名单"这条降级保命路径会被砍成空名单
        #   (test_auction_snap_pool_offhours 暴露; 其余过滤项不受影响)。
        fctx.score_floor_exempt = True

    # 3.5) 竞价强度(替代失活的 f630 异动等级, 权重同为 w_warn=17%):
    #      三层信号全部来自**快照表 + 开盘啦**, 对东财免疫 —— 东财点查断了照样有分。
    #      只对候选加载(全市场拉没必要); 加载失败 → strengths 为空 → 退回 warn 因子。
    strengths = _load_strength(codes, ctx)

    # 4) 评分 + 精筛(只针对候选) → **名单在此定型**
    cand_rows = [rows[c] for c in codes if c in rows]
    srows = score_rows(cand_rows, cfg, strengths)
    outcome = pfilter.apply_filters(srows, filters, fctx)
    res.stats = dict(outcome.stats)

    # 5) 输出(老链路 item 同构 + 定格派生字段)
    ymap = ctx.yesterday_map or {}
    snap = ctx.snapshot_map or {}
    qc = ctx.qiangchou_codes
    # 异动等级档位: 用**本次评分实际用到的 strength**(局部变量 strengths),
    # 不是 ctx.strengths —— 后者在 load_context 里为空, 填充逻辑在 _load_strength。
    strengths_score = strengths or {}
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
        # 异动等级: 优先用竞价强度档位(对东财免疫), 无 strength 才退回 f630 warnType
        #   强 ≥0.85 / 中 ≥0.65 / 弱 ≥0.40 / 极弱 <0.40 → 0
        # 前端 warnLabel(5=强, 4=⚡中, 3=↑弱, 其他=-) —— 直接套用原档位映射,
        # 业务视觉一致、零前端改动(2026-09-09 主反馈 9/9 0:37 异动列全空)
        st_score = strengths_score.get(r.code)
        if st_score is not None:
            warn_label = 5 if st_score >= 0.85 else 4 if st_score >= 0.65 else 3 if st_score >= 0.40 else 0
        else:
            warn_label = r.warn_type
        d.update({
            "bidAmt": None if bid_amt_wan is None else round(bid_amt_wan, 2),
            "bidRatio": bid_ratio,
            "accel": accel,
            **_qc_of(r.code, ctx),
            "province": "-",
            "speed": r.turnover,
            "warnType": warn_label,
            "degraded": res.degraded or bool(r.degraded),
            "source": r.source,
        })
        res.items.append(d)
        res.rows[r.code] = r
    res.items.sort(key=lambda x: (-x.get("probability", 0), x.get("code", "")))
    res.elapsed_ms = int((time.time() - t0) * 1000)
    log.info("选股 mode=%s 全市场=%d 候选=%d 入选=%d 源=%s 降级=%s 剔除=%s 耗时%dms",
             policy.mode.value, res.n_universe, res.n_candidate, len(res.items),
             ",".join(res.sources), res.degraded, res.stats, res.elapsed_ms)
    return res


# ---------------------------------------------------------------- 上下文加载
def load_context(date: Optional[str] = None,
                 markets: Optional[List[str]] = None) -> PickContext:
    """从各服务加载外部事实(真实运行时用; 单测请直接构造 PickContext 注入)。

    任何一项失败都退化为"该事实不可用"而不是抛异常 —— 与适配层同原则:
    降级是数据, 不是控制流。
    """
    from .. import auction_snapshot, fetcher, kpl
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
    try:
        # 用明细(含类型+幅度)而非纯代码集: 左视图要能区分竞额/涨幅/末秒抢筹(2026-09-09)
        det = kpl.get_qiangchou_detail()
        ctx.qiangchou_detail = det or None
        ctx.qiangchou_codes = set(det.keys()) if det else None
    except Exception as e:                                    # noqa: BLE001
        log.warning("抢筹名单加载失败(回退公式) err=%s", e)
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

    三层信号(抢筹名单 / 竞价量比 / 加速度)全部来自快照表 + 开盘啦, **不依赖东财**
    —— 东财点查断了照样有分, 不会再出现"全员 default → 天花板崩 14 分"。
    异常一律吞掉返回空 dict(调用方退回 warn 因子, 不阻塞选股)。
    """
    if ctx.strengths:                      # 调用方显式注入(测试/对拍)
        return ctx.strengths
    try:
        from .. import bid_strength
        st = bid_strength.load(list(codes), date=ctx.date)
        out = {c: v for c, v in bid_strength.score_map(st).items() if v is not None}
        log.info("[竞价强度] 候选%d只 取到强度%d只", len(codes), len(out))
        return out
    except Exception as e:                                    # noqa: BLE001
        log.warning("[竞价强度] 加载失败(退回 f630 异动等级) err=%s", e)
        return {}
