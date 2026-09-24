# -*- coding: utf-8 -*-
"""字段就绪探测：把"上游这一刻到底产出到没有"变成可查询的**事实**。

只读 —— 不做任何写操作，可随时在盘中调用。
"""
from __future__ import annotations

from dataclasses import dataclass

from ...core import logger
from ...db import database
from .registry import all_fields, field as get_contract

log = logger.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """一次就绪探测的结果。"""
    field: str
    kind: str
    n_hit: int
    n_total: int
    ratio: float
    threshold: float
    passed: bool

    def __str__(self) -> str:
        return (f"{self.field}: {self.n_hit}/{self.n_total} "
                f"({self.ratio:.1%} >= {self.threshold:.1%}) "
                f"{'OK' if self.passed else 'NOT-READY'}")


def probe(field_name: str, date: str, time_point: str = "9_25") -> ProbeResult:
    """按契约探测某字段在某日某时点的就绪度。

    Args:
        field_name: 已登记的字段名。
        date: 交易日 "YYYY-MM-DD"。
        time_point: 时点标识，缺省 9_25。

    Returns:
        ProbeResult。字段无落库列或未配 probe 时，返回 passed=True 的占位结果。

    Raises:
        KeyError: 字段未登记。
    """
    c = get_contract(field_name)
    if not c.column or c.probe is None:
        return ProbeResult(field_name, "none", 0, 0, 0.0, 0.0, True)

    where = "date=? AND time_point=?"
    params = (date, time_point)
    conn = database.get_conn()
    try:
        n_total = conn.execute(
            f"SELECT COUNT(*) FROM snapshot_bid WHERE {where}", params).fetchone()[0]
        if c.probe.kind == "non_zero_ratio":
            cond = f"{c.column} != 0"
        elif c.probe.kind == "non_null_ratio":
            cond = f"{c.column} IS NOT NULL"
        else:
            cond = "1=1"
        n_hit = conn.execute(
            f"SELECT COUNT(*) FROM snapshot_bid WHERE {where} AND {cond}",
            params).fetchone()[0]
    finally:
        conn.close()

    ratio = (n_hit / n_total) if n_total else 0.0
    passed = (n_hit >= c.probe.min) if c.probe.kind == "row_count" else (ratio >= c.probe.min)
    return ProbeResult(field_name, c.probe.kind, n_hit, n_total, ratio, c.probe.min, passed)


def health_snapshot(date: str, time_point: str = "9_25") -> dict:
    """按契约对全部登记字段做一轮就绪体检（只读），返回可 JSON 化的结果。"""
    out = []
    for c in sorted(all_fields().values(), key=lambda x: x.name):
        entry = {"name": c.name, "status": c.status,
                 "ready_after": c.ready_after, "source": c.source, "probe": None}
        if c.column:
            try:
                r = probe(c.name, date, time_point)
                entry["probe"] = {"n_hit": r.n_hit, "n_total": r.n_total,
                                  "ratio": round(r.ratio, 4),
                                  "threshold": r.threshold, "passed": r.passed}
            except Exception as e:                # noqa: BLE001
                entry["probe"] = {"error": str(e)[:120]}
        out.append(entry)
    return {"date": date, "time_point": time_point, "fields": out}
