# -*- coding: utf-8 -*-
"""v4.11.80 第三步: spot 接入「锁定链路」 —— 名单源 / 策略标记 / 指纹排除
======================================================================
本文件只测**第三步新增的三件事**, 与 test_pipeline_spot_strategy.py(第一步的评分层分叉)
互补、不重叠:

  A. **spot 名单源必须是实时全市场**, 不是 9:25 定格快照
     (`_fetch_spot_universe`, 走 `fetcher.ensure_spot_cache`)。
     真实坑: 第一步只把评分层换成 spot, 名单源仍是 `_fetch_list` → source_priority[0]
     恒为 "snapshot" → 真跑返回 sources=['snapshot','meoz_realtime'], n_universe 恰好
     == load_snapshot_full 行数。后果: ① 名单域被 9:25 定格冻结(9:25 无快照的票进不来)
     ② 5561 只逐只点查是重操作。本组断言把它钉死。

  B. **`save_batch` 的 `_strategy` 标记**: 新批次必须带, 且**旧批次(无该键)读侧兼容为 auction**。

  C. **`_canon_filter_fingerprint` 必须排除 `_strategy`**:
     否则新批次带键、旧批次无键 → 所有幂等/直读/去重**全部失配**(灾难性静默退化)。

跑法(必须上测试机, 本机无 pytest):
  cd /opt/kuaixuan/backend && PYTHONPATH=/opt/kuaixuan/backend \
    /opt/bid-venv/bin/python -m pytest -q tests/test_spot_lock_step3.py
"""
import json

import pytest

from app.services import history
from app.services.picker import pipeline
from app.services.picker.contract import QuoteRow
from app.services.picker.sources import base as sb

SPOT_F = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "chgGt": 0, "chgFloor": 0, "priceGt": 0, "probLt": 0, "confLt": 0,
    "floatMvFloor": 0, "floatMvGt": 0, "volRatioFloor": 0, "turnoverFloor": 0,
    "turnoverGt": 0, "bidAmtFloor": 0, "bidGt": 0, "bidLt": 0, "scoreFloor": 0,
}


# ======================================================================
# A. spot 名单源 == 实时全市场(不是 9:25 定格快照)
# ======================================================================
def _em_row(code, *, name="某股", price=10.0, prev=9.8, real=2.0, turnover=5.0,
            vol_ratio=2.5, mv=50e8):
    """构造一条**东财原始行**(f12=code 等字段), 供 QuoteRow.from_eastmoney 解析。"""
    return {"f12": code, "f14": name, "f2": price, "f18": prev,
            "f3": real, "f8": turnover, "f10": vol_ratio, "f21": mv}


def test_spot_universe_uses_realtime_cache_not_snapshot(monkeypatch):
    """🔴 核心: spot 名单源必须调 `ensure_spot_cache`, **不得**调 `ensure_cache`(定格缓存)。

    变异测试: 把 `_fetch_spot_universe` 改回 `_fetch_list` → 本断言红。
    """
    calls = {"spot": 0, "snap": 0}

    def _fake_spot(action, fs, before930):
        calls["spot"] += 1
        return [_em_row("600000"), _em_row("600001")], None

    def _fake_snap(action, fs, before930):          # 若被调用 = 走错源
        calls["snap"] += 1
        return [], "不该走到这里"

    from app.services import fetcher
    monkeypatch.setattr(fetcher, "ensure_spot_cache", _fake_spot)
    monkeypatch.setattr(fetcher, "ensure_cache", _fake_snap)
    # 竞价字段(仅展示)读定格: 让它返回空, 不影响评分
    from app.services import auction_snapshot
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change", lambda: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_amt", lambda: {})

    lr = pipeline._fetch_spot_universe(dict(SPOT_F))
    assert lr.label == "spot_market", "标签必须为 spot_market, 实际=%s" % lr.label
    assert calls["spot"] == 1, "必须调 ensure_spot_cache(实时全市场)"
    assert calls["snap"] == 0, "🔴 不得调 ensure_cache(那是 9:25 定格缓存)"
    assert not lr.degraded, "正常路径不得 degraded"
    assert set(lr.rows.keys()) == {"600000", "600001"}


def test_spot_universe_failure_is_degraded_not_raise(monkeypatch):
    """取数失败必须**降级返回**, 不能抛 —— 抛会让 pipeline 整条挂掉(用户看到 500)。"""
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "ensure_spot_cache",
                        lambda a, f, b: (None, "模拟实时行情故障"))
    lr = pipeline._fetch_spot_universe(dict(SPOT_F))
    assert lr.label == "spot_market"
    assert lr.degraded is True
    assert lr.rows == {} and "故障" in (lr.error or "")


def test_spot_universe_exception_is_degraded(monkeypatch):
    """底层抛异常也必须兜住(不能让它穿透到请求处理层)。"""
    from app.services import fetcher

    def _boom(a, f, b):
        raise RuntimeError("模拟底层炸了")

    monkeypatch.setattr(fetcher, "ensure_spot_cache", _boom)
    lr = pipeline._fetch_spot_universe(dict(SPOT_F))
    assert lr.degraded is True and lr.label == "spot_market"


def test_spot_universe_empty_is_degraded(monkeypatch):
    """实时返回空列表 → 降级(不能当成"今天没有符合条件的票")。"""
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "ensure_spot_cache", lambda a, f, b: ([], None))
    lr = pipeline._fetch_spot_universe(dict(SPOT_F))
    assert lr.degraded is True


def test_spot_universe_bid_fields_read_are_display_only(monkeypatch):
    """竞价字段(竞涨/竞额)必须读当日 9:25 定格**仅供展示**, 失败不阻塞。

    关键: bid_change 要落到行上(前端竞涨列), 但它**不参与** spot 六因子评分。
    """
    from app.services import fetcher, auction_snapshot
    monkeypatch.setattr(fetcher, "ensure_spot_cache",
                        lambda a, f, b: ([_em_row("600000")], None))
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change", lambda: {"600000": 3.3})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_amt", lambda: {"600000": 8800.0})
    lr = pipeline._fetch_spot_universe(dict(SPOT_F))
    row = lr.rows["600000"]
    assert abs((row.bid_change or 0) - 3.3) < 1e-6, "竞涨(展示)应来自定格"


def test_spot_universe_bid_read_failure_does_not_block(monkeypatch):
    """定格读取抛异常 → 竞涨/竞额为 None, 但名单**照常产出**(绝不阻塞)。"""
    from app.services import fetcher, auction_snapshot
    monkeypatch.setattr(fetcher, "ensure_spot_cache",
                        lambda a, f, b: ([_em_row("600000")], None))

    def _boom():
        raise RuntimeError("定格表读取炸了")

    monkeypatch.setattr(auction_snapshot, "load_day_bid_change", _boom)
    lr = pipeline._fetch_spot_universe(dict(SPOT_F))
    assert not lr.degraded, "定格读取失败不得让整个名单源降级"
    assert "600000" in lr.rows


def test_pipeline_spot_source_label_is_spot_market(monkeypatch):
    """端到端: pipeline.run(strategy='spot') 的 sources 必须含 spot_market 且**不含 snapshot**。

    这是第一步遗留缺陷的**总体回归**(真机探针复现过 sources=['snapshot','meoz_realtime'])。
    """
    from app.services import fetcher, auction_snapshot

    class _FakeSource(sb.BaseSource):
        def __init__(self, rows, label):
            self.label, self._rows = label, rows

        def fetch(self, ctx):
            return sb.SourceResult(rows=dict(self._rows), requested=len(self._rows))

    monkeypatch.setattr(fetcher, "ensure_spot_cache",
                        lambda a, f, b: ([_em_row("600000"), _em_row("600001")], None))
    monkeypatch.setattr(fetcher, "fetch_yesterday_amounts", lambda codes: {})
    monkeypatch.setattr(fetcher, "fetch_yesterday_changes", lambda codes: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change", lambda: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_amt", lambda: {})
    # snapshot 源**故意放一个能用的假的** —— 若代码误走它, sources 里就会出现 snapshot
    monkeypatch.setattr(pipeline, "get_source",
                        lambda label: _FakeSource({"999999": QuoteRow(code="999999", name="定格票")},
                                                  label) if label == "snapshot" else None)

    ctx = pipeline.PickContext(date="2026-09-28", markets=["hs", "cyb", "kcb"], zt_codes=set())
    import datetime
    res = pipeline.run(dict(SPOT_F), ctx=ctx, now=datetime.datetime(2026, 9, 28, 14, 0),
                       strategy="spot")
    assert "spot_market" in res.sources, "sources 缺 spot_market: %s" % (res.sources,)
    assert "snapshot" not in res.sources, (
        "🔴 spot 名单源不得用 snapshot(定格) —— 实际 sources=%s" % (res.sources,))


# ======================================================================
# B. save_batch 的 _strategy 标记
# ======================================================================
def _item(code="600000", name="测试股", prob=88):
    return {"code": code, "name": name, "probability": prob, "confidence": 1,
            "bidChange": 3.0, "realChange": 2.0, "entityChange": 1.0,
            "bidTurnover": 20.0, "warnType": 0, "circulationMV": 50.0,
            "industry": "x", "concept": "y", "bidAmt": 1e8, "bidRatio": 1.1}


@pytest.fixture
def _clean_batches():
    """建/清批次表, 隔离其它用例(同一 sqlite 文件, 测试并发跑会串)。"""
    from app.db import database
    database.init_db()
    conn = database.get_conn()
    try:
        conn.execute("DELETE FROM batch_stocks")
        conn.execute("DELETE FROM batches")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = database.get_conn()
    try:
        conn.execute("DELETE FROM batch_stocks")
        conn.execute("DELETE FROM batches")
        conn.commit()
    finally:
        conn.close()


def test_save_batch_writes_strategy_marker(_clean_batches):
    """spot 批次 → filters_json 必须含 `_strategy='spot'`。"""
    f = {"markets": ["hs"], "bidGt": 0}
    bid = history.save_batch(1, "lock", [_item()], f, strategy="spot")
    assert bid is not None
    conn = history._conn()
    try:
        row = conn.execute("SELECT filters FROM batches WHERE id=?", (bid,)).fetchone()
    finally:
        conn.close()
    fl = json.loads(row["filters"])
    assert fl.get("_strategy") == "spot", "filters 缺 _strategy 标记: %s" % row["filters"]
    assert "markets" not in fl, "markets 仍应被排除(既有口径不变)"


def test_save_batch_auction_marker(_clean_batches):
    """auction 批次 → `_strategy='auction'`。"""
    bid = history.save_batch(1, "lock", [_item()], {"markets": ["hs"]}, strategy="auction")
    conn = history._conn()
    try:
        row = conn.execute("SELECT filters FROM batches WHERE id=?", (bid,)).fetchone()
    finally:
        conn.close()
    assert json.loads(row["filters"]).get("_strategy") == "auction"


def test_save_batch_no_strategy_keeps_old_shape(_clean_batches):
    """不传 strategy(旧调用方) → **不写**该键 —— 保持与历史批次同形。"""
    bid = history.save_batch(1, "lock", [_item()], {"markets": ["hs"]})
    conn = history._conn()
    try:
        row = conn.execute("SELECT filters FROM batches WHERE id=?", (bid,)).fetchone()
    finally:
        conn.close()
    assert "_strategy" not in json.loads(row["filters"])


def test_old_batch_without_marker_is_readable(_clean_batches):
    """🔴 旧批次(无 `_strategy`)必须仍能正常读回(兼容性: 前端会把它判为 auction)。

    本用例只保证**读侧不炸**; 前端把它判成 auction 的规则在 spot.spec.js 静态锁。
    """
    bid = history.save_batch(1, "lock", [_item()], {"markets": ["hs"], "bidGt": 3, "scoreFloor": 50})
    conn = history._conn()
    try:
        row = conn.execute("SELECT filters FROM batches WHERE id=?", (bid,)).fetchone()
    finally:
        conn.close()
    fl = json.loads(row["filters"] or "{}")
    assert "_strategy" not in fl, "不传 strategy 时不得凭空写该键"
    assert "markets" not in fl, "markets 仍应被排除(既有口径不变)"
    # 其它筛选键必须原样保留(旧批次对新代码完全兼容)
    assert fl.get("bidGt") == 3
    assert fl.get("scoreFloor") == 50


# ======================================================================
# C. 指纹必须排除 _strategy(否则新老批次全部失配)
# ======================================================================
def test_fingerprint_excludes_strategy_marker():
    """🔴 核心: 带/不带 `_strategy` 的**同一套筛选参数**指纹必须相等。

    否则: 新批次带键、旧批次无键 → 幂等/直读/去重全部失配
    → 当日 lock 不再幂等, refresh 不再直读, 退化成每次全市场重算(静默重灾)。
    """
    base = {"bidGt": 3, "scoreFloor": 50, "markets": ["hs"]}
    a = dict(base)
    b = dict(base, _strategy="spot")
    c = dict(base, _strategy="auction")
    fa, fb, fc = (history._canon_filter_fingerprint(x) for x in (a, b, c))
    assert fa == fb == fc, "指纹必须忽略 _strategy: %s / %s / %s" % (fa, fb, fc)


def test_fingerprint_still_excludes_markets():
    """既有行为回归: markets 仍被排除(不能被本次改动带偏)。"""
    f1 = history._canon_filter_fingerprint({"bidGt": 3, "markets": ["hs"]})
    f2 = history._canon_filter_fingerprint({"bidGt": 3, "markets": ["hs", "cyb"]})
    assert f1 == f2


def test_fingerprint_still_detects_real_filter_change():
    """🔴 反向守卫: 排除 _strategy 后, **真实筛选条件变化**仍必须产生不同指纹。

    只测"相等"会让"把整个 f 都排除"这种错法通过 —— 必须双向。
    """
    f1 = history._canon_filter_fingerprint({"bidGt": 3, "scoreFloor": 50})
    f2 = history._canon_filter_fingerprint({"bidGt": 5, "scoreFloor": 50})
    f3 = history._canon_filter_fingerprint({"bidGt": 3, "scoreFloor": 60})
    assert f1 != f2 and f1 != f3 and f2 != f3


def test_fingerprint_key_order_insensitive():
    """键顺序不同 → 指纹相同(sort_keys 保证; 兼容前后端 dict 插入顺序差异)。"""
    f1 = history._canon_filter_fingerprint({"bidGt": 3, "scoreFloor": 50, "_strategy": "spot"})
    f2 = history._canon_filter_fingerprint({"_strategy": "spot", "scoreFloor": 50, "bidGt": 3})
    assert f1 == f2


def test_strategy_marker_does_not_break_idempotency(_clean_batches):
    """端到端: 同一套参数 + 不同 strategy 标记, `find_today_lock_matching` 仍能命中。

    这直接证明"幂等不被 _strategy 破坏" —— 是 C 组指纹结论的**行为级**验证。
    """
    import time
    f = {"markets": ["hs"], "bidGt": 0, "scoreFloor": 0}
    # 先以 auction 落一个当日 lock 批次
    bid = history.save_batch(1, "lock", [_item()], dict(f), strategy="auction")
    assert bid is not None
    # ⚠️ 幂等门禁是 `batch_time >= 09:25:00`(当日定型口径)。save_batch 用**当前时刻**落库,
    #   若本用例在 09:25 前跑会天然落空 → 显式注入一个 9:25 之后的 now_ts, 让用例与钟点无关。
    now = time.time()
    hit = history.find_today_lock_matching(1, dict(f), now_ts=now)
    assert hit is not None, "🔴 _strategy 不得破坏当日 lock 幂等匹配"
    assert hit["id"] == bid
    # 反向守卫: 换成**真实不同**的参数 → 必须**不**命中(证明匹配不是在乱放行)
    miss = history.find_today_lock_matching(1, {"markets": ["hs"], "bidGt": 9.9}, now_ts=now)
    assert miss is None, "不同筛选参数不得命中(否则幂等判据形同虚设)"
