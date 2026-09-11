# -*- coding: utf-8 -*-
"""P0 止血三项回归(2026-09-11)

锁定三条性质, 对应当日生产事故复盘出的三个坑:

  P0-1 9_25 定格采集时刻下限 + 「未发布」保险丝
        ① 采集时刻下限 ≥ 20 秒(消除 10s 轮询相位抖动踩到撮合中间态)
        ② _same_as_prev_rate: 与 9_24 逐票同额 → 判定"定格值未发布"(正常日实测 0.0%)
        ③ 样本不足(<100)不判断, 避免异常采集误触发重采

  P0-2 9:31 盘点质量阈值(行数 + 有额率)
        ④ _snapshot_quality 返回真实行数/有额率 —— 旧自检只查"时点标记"，
           9/11 熔断日 132 行也报"采集完整", 故障静默
        ⑤ 有额率告警**只对 9_15/9_25 生效**: 9_20/9_24 长期仅 0.7%~2.3%
           (东财限流→腾讯兜底, 竞价期无额字段), 一并告警会造成正常日天天误报

  P0-3 落库 None 与 0 分离
        ⑥ 未知(None/NaN/非数值) → 兜底值 + 记入 miss(不伪装成实测 0)
        ⑦ **真实 0 值不得被打标**(换手 0% / 异动 0 级是真实业务值) ← 最易误伤的一条
        ⑧ 落库→读回往返: 开关关闭时返回兜底 0(现状不变); 开启时返回 null

注意: 用 P0T 前缀假代码 + 2099 假日期, 用例后自行清理 —— 测试库 session 共享,
写真实数据会污染其他用例。
"""
import pytest

from app.db import database
from app.services import auction_snapshot as AS
from app.services import history

_DATE = "2099-01-01"          # 假日期, 不会与真实交易日冲突
_PREFIX = "P0T"               # 假代码前缀


def _code(i):
    return "%s%03d" % (_PREFIX, i)


_CREATED_BATCH_IDS = []       # save_batch 用**当前日期**落库, 只能按 id 精确清理


@pytest.fixture(autouse=True)
def _cleanup():
    """每个用例前后清空本文件写入的假数据"""
    database.init_db()
    _purge()
    yield
    _purge()


def _purge():
    conn = database.get_conn()
    try:
        conn.execute("DELETE FROM snapshot_bid WHERE date=?", (_DATE,))
        conn.execute("DELETE FROM batch_stocks WHERE code LIKE ?", (_PREFIX + "%",))
        for bid in _CREATED_BATCH_IDS:
            conn.execute("DELETE FROM batches WHERE id=?", (bid,))
        conn.commit()
    finally:
        conn.close()
    _CREATED_BATCH_IDS.clear()


def _seed(tp, amts):
    """写一批 snapshot_bid: amts = {code: bid_amt}"""
    conn = database.get_conn()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_bid "
            "(date, time_point, code, bid_change, bid_amt, ts) VALUES (?,?,?,?,?,0)",
            [(_DATE, tp, c, 1.0, a) for c, a in amts.items()])
        conn.commit()
    finally:
        conn.close()


# ---------------- P0-1 ----------------

def test_p01_bid25_min_sec_at_least_20():
    """采集时刻下限 ≥ 20 秒: 10s 轮询相位下, 原 10 秒下限会踩到 9:25:12(熔断日实测)"""
    assert AS._BID25_MIN_SEC >= 20


def test_p01_same_prev_rate_all_equal():
    """9_25 与 9_24 逐票竞价额完全相等 → 1.0(判定定格值未发布, 触发重采)"""
    amts = {_code(i): 100.0 + i for i in range(150)}
    _seed("9_24", amts)
    _seed("9_25", dict(amts))
    assert AS._same_as_prev_rate(_DATE, "9_25", "9_24") == 1.0


def test_p01_same_prev_rate_all_diff():
    """正常日: 9_25 撮合后竞价额普遍变化 → 相等率 0.0(生产全库 7 天实测恒为 0.0%)"""
    _seed("9_24", {_code(i): 100.0 + i for i in range(150)})
    _seed("9_25", {_code(i): 900.0 + i for i in range(150)})
    assert AS._same_as_prev_rate(_DATE, "9_25", "9_24") == 0.0


def test_p01_same_prev_rate_small_sample_no_judge():
    """样本 <100 不判断: 异常采集(如熔断日仅 132 行兜底)不得据此误触发重采"""
    amts = {_code(i): 100.0 + i for i in range(50)}
    _seed("9_24", amts)
    _seed("9_25", dict(amts))
    assert AS._same_as_prev_rate(_DATE, "9_25", "9_24") == 0.0


def test_p01_same_prev_rate_ignores_zero_amt():
    """只统计有额(>0)的票: 否则大量 0==0 会把比率虚高到 1, 正常日也会误判未发布"""
    amts = {_code(i): 0 for i in range(200)}          # 全部无额
    _seed("9_24", amts)
    _seed("9_25", dict(amts))
    assert AS._same_as_prev_rate(_DATE, "9_25", "9_24") == 0.0


# ---------------- P0-2 ----------------

def test_p02_snapshot_quality_counts():
    """质量统计: 行数 + 有额占比(旧自检只查时点标记, 132 行也报"完整")"""
    _seed("9_25", {_code(i): (100.0 if i < 80 else 0) for i in range(100)})
    n, rate = AS._snapshot_quality(_DATE, "9_25")
    assert n == 100
    assert abs(rate - 0.8) < 1e-9


def test_p02_snapshot_quality_empty():
    """无数据 → (0, 0.0), 不得抛异常(9:31 盘点每天都要跑)"""
    n, rate = AS._snapshot_quality(_DATE, "9_20")
    assert (n, rate) == (0, 0.0)


def test_p02_amt_rate_only_for_915_and_925():
    """有额率告警只对 9_15/9_25: 9_20/9_24 长期结构性缺额(0.7%~2.3%), 告警即天天误报"""
    assert set(AS._SNAP_AMT_RATE_POINTS) == {"9_15", "9_25"}
    assert "9_20" not in AS._SNAP_AMT_RATE_POINTS
    assert "9_24" not in AS._SNAP_AMT_RATE_POINTS


def test_p02_min_rows_below_normal_scale():
    """行数下限须显著低于正常量(5500+)且显著高于熔断残值(132)"""
    assert 1000 <= AS._SNAP_MIN_ROWS <= 3500


# ---------------- P0-3 ----------------

def test_p03_none_is_marked():
    """未知(None) → 兜底值 + 记入 miss, 不伪装成实测值"""
    miss = []
    assert history._num_or_mark({"warnType": None}, "warnType", miss) == 0
    assert miss == ["warnType"]


def test_p03_zero_is_real_value():
    """真实 0 值不得被打标 —— 换手 0% / 异动 0 级 / 涨幅 0% 都是有业务含义的实测值"""
    miss = []
    assert history._num_or_mark({"bidTurnover": 0}, "bidTurnover", miss) == 0
    assert history._num_or_mark({"bidTurnover": 0.0}, "bidTurnover", miss) == 0.0
    assert miss == [], "真实 0 被误判为缺失, 读侧会错误显示成「—」"


def test_p03_nan_and_str_are_marked():
    """NaN / 字符串 / 布尔 同样视为未知并打标"""
    for bad in (float("nan"), float("inf"), "3.5", True, object()):
        miss = []
        history._num_or_mark({"x": bad}, "x", miss)
        assert miss == ["x"], repr(bad)


def test_p03_roundtrip_restore_off_keeps_zero(monkeypatch):
    """开关关闭(默认): 行为与改动前完全一致 —— 未知读回仍是 0(前端不受影响)"""
    monkeypatch.setattr(history, "_null_restore_enabled", lambda: False)
    bid = _save_one({"probability": 80, "bidChange": None, "warnType": None})
    row = history.get_batch_stocks_mapped(bid)[0]
    assert row["bidChange"] == 0.0
    assert row["warnType"] == 0
    assert row["probability"] == 80          # 有值的字段不受影响


def test_p03_roundtrip_restore_on_returns_null(monkeypatch):
    """开关开启: 未知读回 null(前端显示「—」); 真实 0 保持 0; 有值字段不变"""
    monkeypatch.setattr(history, "_null_restore_enabled", lambda: True)
    bid = _save_one({"probability": 80, "bidChange": None,
                     "bidTurnover": 0, "warnType": None})
    row = history.get_batch_stocks_mapped(bid)[0]
    assert row["bidChange"] is None          # 未知 → null
    assert row["warnType"] is None
    assert row["bidTurnover"] == 0           # 真实 0 → 保持 0
    assert row["probability"] == 80


def _save_one(item):
    """走真实 save_batch 落一条明细, 返回 batch_id(由 fixture 清理)"""
    base = {"code": _PREFIX + "900", "name": "P0测试", "probability": 0,
            "confidence": 0, "bidChange": 0, "realChange": 0, "entityChange": 0,
            "bidTurnover": 0, "warnType": 0, "circulationMV": 0, "bidAmt": 0}
    base.update(item)
    bid = history.save_batch(0, "test", [base], {"markets": ["SH"]})
    assert bid, "save_batch 失败(空名单不落库?)"
    _CREATED_BATCH_IDS.append(bid)
    return bid
