# -*- coding: utf-8 -*-
"""9:25 定格快照源 — 竞价字段的权威来源

契约地位(见 contract.FIELD_AUTHORITY):
  bid_change / bid_amt 的**唯一权威**是 9:25 定格快照。竞价窗口外的实时源没有可信
  竞价数据(东财盘后 f615="-"), 所以盘中/收盘/非交易日一律以快照为准 → 名单幂等。
"""
from typing import Dict

from ... import auction_snapshot
from ..contract import QuoteRow
from .base import BaseSource, FetchContext, SourceResult


class SnapshotSource(BaseSource):
    """从 snapshot_bid 表读最近交易日 9:25 定格全市场快照。

    特点:
      * 纯 DB 读取, 不依赖外部行情 → 不受东财/腾讯故障影响(9/8 断连 30 分钟仍可用)
      * 天然幂等: 同一日期同一定格表 → 同一名单(主人核心诉求)
      * 无实时价 → price=None(不填 0! 填 0 会让 priceGt 门槛静默失效)
    """
    label = "snapshot"

    def fetch(self, ctx: FetchContext) -> SourceResult:
        data = auction_snapshot.load_snapshot_full(ctx.date or None) or {}
        rows: Dict[str, QuoteRow] = {}
        want = set(ctx.codes) if ctx.codes else None
        for code, v in data.items():
            if want is not None and code not in want:
                continue
            row = QuoteRow.from_snapshot(v, degraded=ctx.degraded)
            if not row.code:
                row.code = code
            rows[row.code] = row
        if not rows:
            return SourceResult(
                error="快照源无数据(当日及最近交易日均无 9:25 定格快照)",
                degraded=True, requested=len(want or []))
        return SourceResult(rows=rows, degraded=ctx.degraded,
                            requested=len(want or []) if want else len(rows))
