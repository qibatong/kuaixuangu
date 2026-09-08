# -*- coding: utf-8 -*-
"""
选股数据源适配层 (重构 P1)
=================================================================================
老链路的取数是"散在主流程里的 if-else 降级链": 东财全市场 → 东财点查 → 腾讯点查 →
快照行直出, 每一层只保证"不崩", 不保证**字段完备**, 且降级对调用方完全不可见 →
9/8 事故(降级行 price=0 让 priceGt 静默失效 → 名单虚胖一倍; f3=0 → 现涨幅列全 0)。

本层契约:
  * 每个数据源是一个 adapter, 统一输出 Dict[code, QuoteRow](契约实体, 缺失=None)
  * **适配层永不抛异常**: 任何失败都体现为 SourceResult.error(铁律2: 降级必须可见)
  * 字段语义由 contract.FIELD_AUTHORITY 唯一裁定, adapter 只做"取数 + 映射",
    不得自行发明 fallback(腾讯源尤其: 无竞价字段就必须 None, 不许拿现价涨幅冒充)
  * 标签与 mode.ModePolicy.source_priority 对齐(REGISTRY), pipeline 按标签取 adapter

目录:
  base.py       FetchContext / SourceResult / BaseSource(安全执行包装)
  snapshot.py   9:25 定格快照 — 竞价字段的**权威来源**
  eastmoney.py  东财实时(点查/全市场)
  tencent.py    腾讯点查 / 腾讯全市场兜底
"""
from .base import BaseSource, FetchContext, SourceResult, REGISTRY, get_source  # noqa: F401
