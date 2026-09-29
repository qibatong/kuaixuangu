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
    """腾讯 diff 行(东财同构) → QuoteRow, 竞价字段按**时段**决定是否可信。

    腾讯没有竞价专属字段: f615 = 现价涨幅、f616 = 累计成交额(老链路兼容映射)。
      窗口**外**(盘前/盘中/收盘): 现价涨幅 ≠ 竞价涨幅、累计额 ≠ 竞价额 → 取之即
        事故(9/7 大跌票混入) → 一律清空, 只认 9:25 定格。
      竞价窗口**内**(9:15-9:25): 尚未撮合, 现价就是竞价虚拟价、累计额就是竞价额
        → 此时 f615/f616 **语义正确**, 必须取(否则竞价窗口内 bid_change 恒 None,
        被 require_bid_change 全剔 → 名单恒空, 2026-09-09 生产实证)。
    """
    win = bool(getattr(ctx.policy, "auction_window", False))
    rows: Dict[str, QuoteRow] = {}
    for s in raw or []:
        code = str(s.get("f12") or "")
        if not code:
            continue
        row = QuoteRow.from_eastmoney(
            s,
            auction_window=win,                       # 源能力: 仅竞价窗口内可读 f615/f616
            period_auction=win,                       # 时段事实: 供停牌判定(竞价期 vol 恒 0)
            yesterday_chg=ctx.yesterday_chg.get(code),
            degraded=True,                            # 走腾讯 = 东财已失败, 必然是降级
        )
        # 竞价字段: 9:25 定格 map 是权威; 无定格时按**时段**决定是否保留行内实时值
        #   - 竞价窗口内: 行内 f615/f616 语义正确(见上) → 保留
        #   - 窗口外:     现价涨幅/累计额 ≠ 竞价数据 → 清空(绝不拿现价涨幅顶替)
        if ctx.day_bid_change.get(code) is not None:
            row.bid_change = ctx.day_bid_change[code]
        elif not win:
            row.bid_change = None
        amt_wan = ctx.day_bid_amt_wan.get(code)
        if amt_wan is not None:
            row.bid_amt = amt_wan * 1e4               # 万元 → 元
        elif not win:
            row.bid_amt = None
        bv = ctx.day_bid_vol.get(code)                # 腾讯无竞价量字段, 只有定格值可用
        if bv is not None:
            row.bid_vol = bv
        elif not win:
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
        fs = scorer.market_fs(ctx.markets or list(scorer.ALL_MARKETS))
        raw = fetcher.fetch_tencent_market(fs)
        rows = _rows_from_tencent(raw, ctx)
        if not rows:
            return SourceResult(error="腾讯全市场源返回空", degraded=True,
                                requested=len(ctx.codes or []))
        return SourceResult(rows=rows, degraded=True,
                            requested=len(ctx.codes or []))
