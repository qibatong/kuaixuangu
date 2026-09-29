# -*- coding: utf-8 -*-
"""东财源: 实时点查 / 全市场

竞价字段语义(关键):
  仅 **竞价窗口内**(policy.auction_window)才允许读实时 f615/f616; 窗口外一律以
  9:25 定格 map 为准, 拿不到就是 None。老链路在窗口外退化取 f3 → "竞涨=现涨",
  大跌票因满足"竞价涨幅≤7%"上限而混入名单(9/7 事故), 本源用参数约束根除。
"""
from typing import Dict, List, Optional

from ... import fetcher
from ..contract import QuoteRow
from .base import BaseSource, FetchContext, SourceResult


def _rows_from_diff(raw: Optional[List[dict]], ctx: FetchContext,
                    degraded: bool) -> Dict[str, QuoteRow]:
    """东财 diff 行列表 → QuoteRow 映射(点查与全市场共用)。

    定格 map 优先于实时字段: day_bid_change/day_bid_amt_wan 有值即用之(权威),
    无值时才按 auction_window 与否决定是否取实时 f615/f616。
    """
    rows: Dict[str, QuoteRow] = {}
    for s in raw or []:
        code = str(s.get("f12") or "")
        if not code:
            continue
        row = QuoteRow.from_eastmoney(
            s,
            auction_window=bool(getattr(ctx.policy, "auction_window", False)),
            day_bid_change=ctx.day_bid_change.get(code),
            day_bid_amt_wan=ctx.day_bid_amt_wan.get(code),
            day_bid_vol=ctx.day_bid_vol.get(code),
            yesterday_chg=ctx.yesterday_chg.get(code),
            degraded=degraded,
        )
        rows[code] = row
    return rows


class EastmoneyRealtimeSource(BaseSource):
    """东财按 code 点查(fetch_raw_by_codes): 给候选集补实时行情。

    必须传 codes —— 点查源不接受"无候选集"调用(那会退化成全市场, 而全市场正是
    名单波动根因: 双 worker 缓存不一致 + 28 页拉取)。
    """
    label = "eastmoney_realtime"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        codes = [c for c in (ctx.codes or []) if c]
        if not codes:
            return SourceResult(error="东财点查源需要候选代码集(不接受无 codes 调用)")
        raw = fetcher.fetch_raw_by_codes(codes)
        rows = _rows_from_diff(raw, ctx, degraded=ctx.degraded)
        if not rows:
            return SourceResult(error="东财点查源返回空(接口故障或全部代码无行情)",
                                degraded=True, requested=len(codes))
        return SourceResult(rows=rows, degraded=ctx.degraded, requested=len(codes))


class EastmoneyMarketSource(BaseSource):
    """东财全市场(ensure_cache): 仅竞价窗口等"必须实时全市场"的模式使用。

    ⚠️ 盘中/收盘**禁止**使用(名单会随行情漂移, 违反幂等总原则)。
    """
    label = "eastmoney_market"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        from ... import scorer
        fs = scorer.market_fs(ctx.markets or list(scorer.ALL_MARKETS))
        raw, err = fetcher.ensure_cache("filter", fs, True)
        if err or not raw:
            return SourceResult(error="东财全市场源失败: %s" % (err or "返回空"),
                                degraded=True, requested=len(ctx.codes or []))
        rows = _rows_from_diff(raw, ctx, degraded=ctx.degraded)
        return SourceResult(rows=rows, degraded=ctx.degraded,
                            requested=len(ctx.codes or []))
