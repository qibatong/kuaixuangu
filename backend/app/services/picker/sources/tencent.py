# -*- coding: utf-8 -*-
"""腾讯源: 按 code 点查 / 全市场兜底

竞价字段(铁律):
  腾讯**没有**竞价专属字段。fetcher._tencent_diff 为兼容老链路会把现价涨幅塞进
  f615、把累计成交额塞进 f616 —— 那是给老 scorer 用的近似值, **不是竞价数据**。
  本适配层必须显式清空 bid_*(除非定格 map 有权威值), 否则会重现"拿现价涨幅
  冒充竞价涨幅"的事故(9/7 竞涨=现涨 → 大跌票混入)。
"""
from typing import Dict, List, Optional

from ... import fetcher
from ..contract import QuoteRow
from .base import BaseSource, FetchContext, SourceResult


def _rows_from_tencent(raw: Optional[List[dict]], ctx: FetchContext) -> Dict[str, QuoteRow]:
    """腾讯 diff 行(东财同构) → QuoteRow, 并**强制**竞价字段只认定格值。

    auction_window 恒传 False: 腾讯 f615 本就是现价涨幅冒充的, 任何窗口都不该取。
    """
    rows: Dict[str, QuoteRow] = {}
    for s in raw or []:
        code = str(s.get("f12") or "")
        if not code:
            continue
        row = QuoteRow.from_eastmoney(
            s,
            auction_window=False,                     # 腾讯无真实竞价字段, 永不取 f615
            yesterday_chg=ctx.yesterday_chg.get(code),
            degraded=True,                            # 走腾讯 = 东财已失败, 必然是降级
        )
        # 竞价字段: 只有定格 map 是权威, 没有就 None(绝不拿现价涨幅顶替)
        row.bid_change = ctx.day_bid_change.get(code)
        amt_wan = ctx.day_bid_amt_wan.get(code)
        row.bid_amt = None if amt_wan is None else amt_wan * 1e4   # 万元 → 元
        row.bid_vol = None
        row.source = "tencent"
        row.degraded = True
        rows[code] = row
    return rows


class TencentPointSource(BaseSource):
    """腾讯按 code 点查: 东财点查失败时的**首选兜底**(9/8 方案 A+ 实证有效)。

    作用: 名单仍由快照池固定(幂等), 腾讯只补实时展示字段(现价/涨幅/今开/换手),
    避免"快照行直出"导致的现价 0 + 过滤失效 → 名单虚胖。
    """
    label = "tencent_point"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        codes = [c for c in (ctx.codes or []) if c]
        if not codes:
            return SourceResult(error="腾讯点查源需要候选代码集(不接受无 codes 调用)")
        raw = fetcher.fetch_tencent_by_codes(codes)
        rows = _rows_from_tencent(raw, ctx)
        if not rows:
            return SourceResult(error="腾讯点查源返回空", degraded=True,
                                requested=len(codes))
        return SourceResult(rows=rows, degraded=True, requested=len(codes))


class TencentMarketSource(BaseSource):
    """腾讯全市场兜底: 东财全市场熔断且必须拿全市场时使用(竞价窗口内)。"""
    label = "tencent_market"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        from ... import scorer
        fs = scorer.market_fs(ctx.markets or ["hs", "cyb", "kcb"])
        raw = fetcher.fetch_tencent_market(fs)
        rows = _rows_from_tencent(raw, ctx)
        if not rows:
            return SourceResult(error="腾讯全市场源返回空", degraded=True,
                                requested=len(ctx.codes or []))
        return SourceResult(rows=rows, degraded=True,
                            requested=len(ctx.codes or []))
