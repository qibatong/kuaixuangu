# -*- coding: utf-8 -*-
"""字段契约的数据结构。

设计约束：本模块**只依赖标准库** —— 保证契约包可被任意层导入而不产生循环引用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldStatus = Literal["ok", "degraded", "dead", "planned"]
ProbeKind = Literal["non_zero_ratio", "non_null_ratio", "row_count"]


@dataclass(frozen=True, slots=True)
class ReadyProbe:
    """字段就绪探测规则：判定"这一刻上游是否已把该字段产出到可用水平"。"""
    kind: ProbeKind
    min: float
    scope: str = "all_market"
    note: str = ""


@dataclass(frozen=True, slots=True)
class FieldContract:
    """单个采集字段的完整契约。"""
    name: str
    source: str
    source_field: str
    unit: str
    caliber: str
    ready_after: str
    column: str = ""
    consumers: tuple[str, ...] = ()
    confusion: str = ""
    status: FieldStatus = "ok"
    self_computable: bool = False
    probe: ReadyProbe | None = None
