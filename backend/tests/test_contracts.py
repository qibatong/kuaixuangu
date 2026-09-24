# -*- coding: utf-8 -*-
"""字段契约注册表自检 —— 契约写错必须在这里红（方案 WP1a）。

本文件的存在意义：让「字段从哪来 / 什么口径 / 何时可用」的登记表自身可被 CI 校验，
而不是靠人记住。契约表写错时，这里第一时间报错，而不是等到盘中发现某列恒 0。
"""
from __future__ import annotations

import re

import pytest

from app.services import contracts


def test_registry_passes_validation():
    """CI 第一道闸：契约表本身必须合法。"""
    assert contracts.validate() == []


def test_ready_after_is_hhmmss():
    """就绪时刻格式统一，避免字符串比较排序时踩坑。"""
    for c in contracts.all_fields().values():
        assert re.match(r"^\d{2}:\d{2}:\d{2}$", c.ready_after), c.name


@pytest.mark.parametrize("name,source_field,column", [
    ("auc_main_net", "auction_main_net_amount", "auc_main_net"),
    ("seal_amount", "fa_0925", "bid_buy_amt"),
    ("free_mv", "free_float_mv", "free_mv"),
])
def test_key_contracts_pinned(name, source_field, column):
    """把体检结论固化成断言 —— 谁改坏这条，说明动了 P1/P5 的前提。"""
    c = contracts.field(name)
    assert c.source_field == source_field
    assert c.column == column


def test_auc_main_net_marks_degraded():
    """P1 的事实必须在契约里留痕，否则下次有人会以为它一直好好的。"""
    c = contracts.field("auc_main_net")
    assert c.status == "degraded"
    assert c.ready_after == "09:25:35"
    assert c.probe is not None and c.probe.kind == "non_zero_ratio"


def test_unknown_field_raises_with_hint():
    with pytest.raises(KeyError) as ei:
        contracts.field("no_such_field")
    assert "contracts/fields.py" in str(ei.value)


# snapshot_bid 里「非上游字段」的列白名单（本地算出来的 / 元信息 / 历史遗留）。
# ★ 与方案初稿的差异：初稿白名单含 id / chg_to_yesterday / yday_amount / yday_chg
#   （该表**并无**这些列），且漏了 ts / fd_to_yesterday / pre_fd_break_* 。
#   实测 `PRAGMA table_info(snapshot_bid)`（2026-09-24，见 db/database.py:354-441）后订正。
_SNAPSHOT_BID_LOCAL_COLUMNS = frozenset({
    "date", "time_point", "code",                       # 主键/时点
    "ts",                                               # 采集时刻(本地)
    "name", "board",                                    # 展示/概念标签列
    "bid_change", "bid_amt",                            # 竞价涨幅 / 竞价额(本地算)
    "warn_type",                                        # 东财 f630 异动等级(定格时冻结落库)
    "fd_to_yesterday",                                  # 封昨比(猫爪 screening 派生, 非直接字段)
    "pre_fd_break_amount", "pre_fd_break_times",        # 历史遗留列: 猫爪无此字段, 曾误加;
                                                        # SQLite 3.7.17 无 DROP COLUMN ⇒ 保留,
                                                        # 值恒默认 0, 无任何代码读写
})


def test_snapshot_bid_no_orphan_column():
    """反向检查：snapshot_bid 的采集列必须都能在契约里找到，或在白名单里写明来由。

    这条断言的现实价值：将来往定格表新增一列（像 v4.11.40 加 auc_vol_ratio 那样），
    若忘了登记契约，本测试立即红 —— 避免"列落了库但没人知道它从哪来"。
    """
    from app.db import database
    conn = database.get_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(snapshot_bid)").fetchall()]
    finally:
        conn.close()
    registered = {c.column for c in contracts.all_fields().values() if c.column}
    orphans = set(cols) - registered - _SNAPSHOT_BID_LOCAL_COLUMNS
    assert orphans == set(), (
        f"以下 snapshot_bid 列既未登记契约、也不在白名单：{sorted(orphans)} —— "
        f"新增上游字段请写入 contracts/fields.py；本地列请补进 _SNAPSHOT_BID_LOCAL_COLUMNS 并写明来由")


def test_probe_before_ready_is_not_ready():
    """时间门：用一个空库探测，必须判 NOT-READY（而非异常）。"""
    r = contracts.probe("auc_main_net", "1990-01-01", "9_25")
    assert r.passed is False and r.n_total == 0


def test_health_snapshot_shape():
    """体检快照必须可 JSON 化、且覆盖全部登记字段（/api/health 直接返回它）。"""
    import json
    snap = contracts.health_snapshot("1990-01-01", "9_25")
    json.dumps(snap)                                   # 不可序列化会抛 TypeError
    assert snap["date"] == "1990-01-01"
    assert {f["name"] for f in snap["fields"]} == set(contracts.all_fields())
