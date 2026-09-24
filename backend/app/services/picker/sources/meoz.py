# -*- coding: utf-8 -*-
"""猫爪源: 按 code 点查 / 全市场(screening)

设计要点(2026-09-24 换源 WP0「去东财换猫爪」第一步, 纯新增, 零存量行为变化):

  1. **一个接口两条用法**: 猫爪 `screening` 不传 symbols = 全市场(实测 5553~5651 只),
     传 symbols = 点查(同源同字段)。故点查源与全市场源共用 meoz_client.screening_map,
     共用 `_AUC_SNAP_TTL` 缓存 —— 不新增接口、不新增 TTL 常量(避免再造一个"第二个 30")。
  2. **竞价的"何时可读实时值"沿用东财同一闸门**(policy.auction_window), 见
     `QuoteRow.from_meoz` docstring: 猫爪 auc_* 语义上全天有效, 但放宽是 换源 WP2 的独立
     决策, 本次刻意保持一致, 免得"换源"静默改变名单语义。
  3. **缺口即 None**(warn_type / industry) —— 与铁律1 一致: 缺失不得填 0,
     否则评分的"异动兜底档"与前端"行业"列会把"我不知道"显示成"已知为 0/空"。
  4. 单位换算(vol/auc_vol 手→股)全在 `QuoteRow.from_meoz` 里做, 本层只做"取数 + 组装",
     与 eastmoney/tencent 适配层的分工一致(字段语义归契约层唯一裁定)。

★ 2026-09-24 已接入(换源 WP2): 4 个非竞价模式的**补丁源**、AUCTION 的**名单源**。
   readme 上面第 2 点(竞价语义保持与东财一致)随之生效 —— 换源不改竞价字段语义。
"""
from typing import Dict, Optional

from ... import meoz_client
from ..contract import QuoteRow
from .base import BaseSource, FetchContext, SourceResult

# 全市场源的最小行数闸门(2026-09-24 换源 WP2 新增)。
# 为什么需要: `_fetch_list` 只看 `SourceResult.ok`(= error 为空且 rows 非空), 而全市场源
# 的 `requested` 是 0 → coverage 恒 1.0 ⇒ **半残数据会被静默当成有效名单源接管**。
# A 股全市场长期 5000+ 只(实测猫爪 5572 / 东财 5561), 闸门取 1000 只: 只要上游返回
# 一个数量级偏小的结果(接口降级/分页截断/缓存半写), 就判为不可用并交给下一级源,
# 而不是"换个源把名单缩水"。阈值取这么低是为了**只拦真正的异常**, 不干预正常波动。
_MEOZ_MARKET_MIN_ROWS = 1000


def _rows_from_meoz(smap: Optional[Dict[str, dict]], ctx: FetchContext,
                    degraded: bool = False) -> Dict[str, QuoteRow]:
    """猫爪 screening map({symbol: {字段: 值}}) → Dict[code, QuoteRow]。

    点查源与全市场源共用本映射, 差异**只在 fetch() 里传不传 symbols** —— 避免两处
    各写一遍映射(那是"同一口径两处维护"的老毛病, 换字段时必漏改一处)。
    """
    win = bool(getattr(ctx.policy, "auction_window", False))
    rows: Dict[str, QuoteRow] = {}
    for code, m in (smap or {}).items():
        c = str(code or "")
        if not c:
            continue
        rows[c] = QuoteRow.from_meoz(
            m or {},
            auction_window=win,
            day_bid_change=ctx.day_bid_change.get(c),
            day_bid_amt_wan=ctx.day_bid_amt_wan.get(c),
            day_bid_vol=ctx.day_bid_vol.get(c),
            yesterday_chg=ctx.yesterday_chg.get(c),
            degraded=degraded,
        )
    return rows


class MeozRealtimeSource(BaseSource):
    """猫爪按 code 点查(screening + symbols): 给候选集补实时行情。

    必须传 codes —— 点查源不接受"无候选集"调用(那会退化成全市场, 而全市场正是
    名单波动根因: 双 worker 缓存不一致 + 28 页拉取)。与东财/腾讯点查源同约束。
    """
    label = "meoz_realtime"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        codes = [c for c in (ctx.codes or []) if c]
        if not codes:
            return SourceResult(error="猫爪点查源需要候选代码集(不接受无 codes 调用)")
        if not meoz_client.enabled():
            return SourceResult(error="猫爪源未启用(settings.use_meoz=0 或缺 apikey)",
                                degraded=True, requested=len(codes))
        smap = meoz_client.screening_map(symbols=codes)
        rows = _rows_from_meoz(smap, ctx, degraded=ctx.degraded)
        # 只认请求集: 上游若多回代码一律丢弃(与东财点查源同纪律, 防止名单被"意外扩集")
        rows = {c: rows[c] for c in codes if c in rows}
        if not rows:
            return SourceResult(error="猫爪点查源返回空(接口故障或全部代码无行情)",
                                degraded=True, requested=len(codes))
        return SourceResult(rows=rows, degraded=ctx.degraded, requested=len(codes))


class MeozMarketSource(BaseSource):
    """猫爪全市场(screening 不传 symbols): 仅竞价窗口等"必须实时全市场"的模式使用。

    ⚠️ 与东财全市场源同约束: 盘中/收盘**禁止**使用(名单会随行情漂移, 违反幂等总原则)。
    ⚠️ **忽略 ctx.markets**: 猫爪 screening 无市场范围参数 —— 返回值永远是 A 股全市场
       (含北交所 344 只)。北交所由下游 `picker/filter.py` 按代码前缀排除
       (2026-09-21 主人拍板: 系统不需要北交所), 故此处不做二次过滤, 避免两处判定不一致。
    """
    label = "meoz_market"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        if not meoz_client.enabled():
            return SourceResult(error="猫爪源未启用(settings.use_meoz=0 或缺 apikey)",
                                degraded=True, requested=len(ctx.codes or []))
        smap = meoz_client.screening_map()
        if not smap:
            # 先分「空」与「半残」: 两者都是失败, 但原因不同 —— 排查时一眼能分清
            # "接口故障没返回" 与 "返回了但被截断/缓存半写"(后者才是本闸门要拦的形态)。
            return SourceResult(error="猫爪全市场源返回空(接口故障)",
                                degraded=True, requested=len(ctx.codes or []))
        if len(smap) < _MEOZ_MARKET_MIN_ROWS:
            # 半残结果不得接管名单: 明确报错 → _fetch_list 自动尝试下一级源
            return SourceResult(
                error="猫爪全市场源行数异常(%d < %d) — 视为不可用, 交由下一级源"
                      % (len(smap), _MEOZ_MARKET_MIN_ROWS),
                degraded=True, requested=len(ctx.codes or []))
        rows = _rows_from_meoz(smap, ctx, degraded=ctx.degraded)
        if not rows:
            return SourceResult(error="猫爪全市场源映射后为空(全部代码缺失)",
                                degraded=True, requested=len(ctx.codes or []))
        return SourceResult(rows=rows, degraded=ctx.degraded,
                            requested=len(ctx.codes or []))
