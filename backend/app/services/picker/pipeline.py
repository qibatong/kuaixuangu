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

灰度: 本模块**不改任何老代码**。api 层旁路调用 run() 并与老结果比对(见 parity.compare)。
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


@dataclass
class PickContext:
    """一次选股所需的**外部事实**(全部可注入 → 单测无需网络/DB)"""
    date: str = ""
    markets: Optional[List[str]] = None
    zt_codes: Optional[Set[str]] = None          # 昨涨停/连板(None=名单不可用)
    qiangchou_codes: Optional[Set[str]] = None   # 竞价抢筹(None=回退公式)
    day_bid_change: Dict[str, float] = field(default_factory=dict)   # 9:25 定格涨幅 %
    day_bid_amt_wan: Dict[str, float] = field(default_factory=dict)  # 9:25 定格额(万元)
    day_bid_vol: Dict[str, float] = field(default_factory=dict)      # 9:25 定格量(股)
    yesterday_chg: Dict[str, float] = field(default_factory=dict)    # 真实昨日涨幅 %
    yesterday_map: Dict[str, list] = field(default_factory=dict)     # 成交额对(竞/昨比)
    snapshot_map: Dict[str, dict] = field(default_factory=dict)      # 9:20 快照(加速度)
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
    """名单源(source_priority[0])"""
    label = policy.source_priority[0]
    src = get_source(label)
    if src is None:
        return SourceResult(label=label, error="未知数据源标签: %s" % label,
                            degraded=True)
    return src.run(FetchContext(
        policy=policy, date=ctx.date, markets=filters.get("markets"),
        day_bid_change=ctx.day_bid_change, day_bid_amt_wan=ctx.day_bid_amt_wan,
        day_bid_vol=ctx.day_bid_vol, yesterday_chg=ctx.yesterday_chg))


def _fetch_patch(ctx: PickContext, policy: pm.ModePolicy,
                 codes: Sequence[str], filters: Dict) -> Optional[SourceResult]:
    """补丁源(source_priority[1:]): 逐个尝试, 第一个成功的即可。失败不影响名单。"""
    for label in policy.source_priority[1:]:
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
    """跑一次完整选股(新链路)。老链路零改动, 本函数可旁路调用做灰度对拍。

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
        log.warning("选股新链路 名单源失败 mode=%s err=%s — %s",
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

    # 3) 评分 + 精筛(只针对候选) → **名单在此定型**
    #    关键顺序: 补丁源在过滤**之后**才应用。若先补丁再过滤, 实时价会参与
    #    priceGt 判定 → 盘中价格一漂名单就变(老链路正是如此, 幂等不成立)。
    cand_rows = [rows[c] for c in codes if c in rows]
    srows = score_rows(cand_rows, cfg)
    outcome = pfilter.apply_filters(srows, filters, fctx)
    res.stats = dict(outcome.stats)

    # 4) 展示字段补丁: 只对**已入选票**拉, 且不再重新过滤(名单已定型)
    #    补丁失败 → 现价列显示 '-', 名单不变(老链路点查失败会整批降级 → 名单虚胖)
    pr = _fetch_patch(ctx, policy, [it.code for it in outcome.kept], filters)
    if pr is not None:
        rows = _merge_rows(rows, pr.rows)
        res.sources.append(pr.label)
        if pr.degraded:
            res.degraded = True
        # merge 返回的是**新行对象** → kept 按 code 重新指向(评分结果复用)
        outcome.kept = [ScoredRow(row=rows[it.code], score=it.score)
                        for it in outcome.kept if it.code in rows]
    else:
        res.errors.append("补丁源不可用(仅影响实时展示字段, 名单仍有效)")

    # 5) 输出(老链路 item 同构 + 定格派生字段)
    ymap = ctx.yesterday_map or {}
    snap = ctx.snapshot_map or {}
    qc = ctx.qiangchou_codes
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
        d.update({
            "bidAmt": None if bid_amt_wan is None else round(bid_amt_wan, 2),
            "bidRatio": bid_ratio,
            "accel": accel,
            "qiangchou": (1 if (qc is not None and r.code in qc) else 0),
            "province": "-",
            "speed": r.turnover,
            "warnType": r.warn_type,
            "degraded": res.degraded or bool(r.degraded),
            "source": r.source,
        })
        res.items.append(d)
        res.rows[r.code] = r
    res.items.sort(key=lambda x: (-x.get("probability", 0), x.get("code", "")))
    res.elapsed_ms = int((time.time() - t0) * 1000)
    log.info("选股新链路 mode=%s 全市场=%d 候选=%d 入选=%d 源=%s 降级=%s 剔除=%s 耗时%dms",
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
        qc = kpl.get_qiangchou_codes()
        ctx.qiangchou_codes = set(qc) if qc else None
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
