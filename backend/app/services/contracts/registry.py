# -*- coding: utf-8 -*-
"""字段契约注册表：加载 + 校验 + 只读查询。"""
from __future__ import annotations

import re
from functools import lru_cache

from .fields import FIELDS
from .schema import FieldContract

_HHMMSS = re.compile(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_VALID_STATUS = {"ok", "degraded", "dead", "planned"}
_VALID_PROBE = {"non_zero_ratio", "non_null_ratio", "row_count"}


class ContractError(ValueError):
    """字段契约不合法。"""


def _problems() -> list[str]:
    out: list[str] = []
    seen: dict[str, str] = {}
    cols: dict[str, str] = {}
    for c in FIELDS:
        if c.name in seen:
            out.append(f"{c.name}: 字段名重复")
            continue
        seen[c.name] = c.name
        if not _HHMMSS.match(c.ready_after):
            out.append(f"{c.name}: ready_after 必须是 HH:MM:SS，实为 {c.ready_after!r}")
        if c.status not in _VALID_STATUS:
            out.append(f"{c.name}: status 非法 {c.status!r}")
        if c.probe is not None and c.probe.kind not in _VALID_PROBE:
            out.append(f"{c.name}: ready_probe.kind 非法 {c.probe.kind!r}")
        if c.column and not _IDENT.match(c.column):
            out.append(f"{c.name}: column {c.column!r} 不是合法 SQL 标识符")
        if c.column:
            if c.column in cols:
                out.append(f"落库列 {c.column!r} 被 {cols[c.column]} 与 {c.name} 重复占用")
            cols[c.column] = c.name
        if c.status != "planned" and not c.consumers:
            out.append(f"{c.name}: consumers 为空 —— 未登记消费方，改字段时无法评估影响面")
        if c.status == "degraded" and not c.confusion:
            out.append(f"{c.name}: status=degraded 必须写明 confusion（为什么退化）")
    return out


@lru_cache(maxsize=1)
def _index() -> dict[str, FieldContract]:
    ps = _problems()
    if ps:
        raise ContractError("字段契约校验失败：\n  - " + "\n  - ".join(ps))
    return {c.name: c for c in FIELDS}


def all_fields() -> dict[str, FieldContract]:
    return dict(_index())


def field(name: str) -> FieldContract:
    try:
        return _index()[name]
    except KeyError:
        raise KeyError(
            f"字段 {name} 未登记 —— 新增字段请先写入 contracts/fields.py"
        ) from None


def by_column(column: str) -> FieldContract | None:
    for c in _index().values():
        if c.column == column:
            return c
    return None


def by_source(prefix: str) -> list[FieldContract]:
    return [c for c in _index().values() if c.source.startswith(prefix)]


def late_fields(now_hhmmss: str, source_prefix: str = "") -> list[FieldContract]:
    items = [c for c in _index().values()
             if (not source_prefix or c.source.startswith(source_prefix))
             and not c.self_computable]
    return [c for c in items if c.ready_after > now_hhmmss]


def earliest_ready(source_prefix: str = "") -> str:
    items = [c for c in _index().values()
             if not source_prefix or c.source.startswith(source_prefix)]
    return min((c.ready_after for c in items), default="00:00:00")


def latest_ready(source_prefix: str = "") -> str:
    items = [c for c in _index().values()
             if not source_prefix or c.source.startswith(source_prefix)]
    return max((c.ready_after for c in items), default="00:00:00")


def validate() -> list[str]:
    return _problems()
