# -*- coding: utf-8 -*-
"""竞价强度(bid_strength)三层信号 — 替代已失活的 f630 异动等级"""
import pytest

from app.services import bid_strength as bs


def _cfg():
    return {
        "factors": {"bid_strength": {
            "buckets": [["3", "9999", 1.0], ["2", "3", 0.85], ["1.5", "2", 0.7],
                        ["1.0", "1.5", 0.55], ["0.6", "1.0", 0.4], ["0", "0.6", 0.25]],
            "default": 0.22,
            "qc_bonus": 0.15, "qc_last_bonus": 0.10,
            "accel_up": 0.08, "accel_down": -0.08,
        }},
    }


# ---------------------------------------------------------------- 主分: 竞价量比
def test_vol_ratio_buckets():
    """量比越大分越高; 3 倍以上满分"""
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=5.0), _cfg()) == pytest.approx(1.0)
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=2.5), _cfg()) == pytest.approx(0.85)
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2), _cfg()) == pytest.approx(0.55)
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=0.3), _cfg()) == pytest.approx(0.25)


def test_vol_ratio_missing_uses_default_not_zero():
    """量比缺失 → default 0.22, 不是 0(0 会落 ["0","0.6"] 桶拿 0.25, 差别虽小但语义不同)"""
    st = bs.BidStrength(code="1", accel=0.0)      # 量比 None, 只有加速度
    assert st.bid_vol_ratio is None
    # 加速度 0.0 属中性区间不修正 → 分数就是 default
    assert bs.score_one(st, _cfg()) == pytest.approx(0.22)


# ---------------------------------------------------------------- 抢筹加成
def test_qc_bonus_stacks():
    """抢筹强度榜 +0.15, 最后一秒抢筹 +0.10, 可叠加"""
    base = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2), _cfg())   # 0.55
    with_qc = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2, qc_delta=1.5), _cfg())
    with_both = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2,
                                            qc_delta=1.5, qc_last=True), _cfg())
    assert with_qc == pytest.approx(base + 0.15)
    assert with_both == pytest.approx(base + 0.25)


def test_qc_alone_when_vol_missing():
    """量比缺失但命中抢筹 → default 0.22 + 0.15(不因一层缺失抹掉另一层信号)"""
    st = bs.BidStrength(code="1", qc_delta=2.0)
    assert bs.score_one(st, _cfg()) == pytest.approx(0.37)


# ---------------------------------------------------------------- 加速度修正
def test_accel_up_and_down():
    """9_24→9_25 拉升加分 / 跳水减分; 中性区间不修正"""
    base = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2), _cfg())   # 0.55
    up = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2, accel=2.0), _cfg())
    down = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2, accel=-3.0), _cfg())
    flat = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.2, accel=0.1), _cfg())
    assert up == pytest.approx(base + 0.08)
    assert down == pytest.approx(base - 0.08)
    assert flat == pytest.approx(base)


def test_score_clamped():
    """合成后封顶 1.0 / 保底 0.05"""
    best = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=99, qc_delta=9,
                                       qc_last=True, accel=5), _cfg())
    worst = bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=0.01, accel=-5), _cfg())
    assert best == 1.0
    assert worst == pytest.approx(0.17)      # 0.25 - 0.08


# ---------------------------------------------------------------- 三层全缺
def test_all_missing_returns_none():
    """三层全缺 → None(交给上层 factor default), 绝不返回一个假分数"""
    assert bs.score_one(bs.BidStrength(code="1"), _cfg()) is None
    assert bs.score_one(None, _cfg()) is None


def test_partial_missing_still_scores():
    """只要有一层可用就出分 —— 这正是为了解决 f630 '一层挂掉全员 default'"""
    assert bs.score_one(bs.BidStrength(code="1", accel=1.0), _cfg()) is not None
    assert bs.score_one(bs.BidStrength(code="1", qc_last=True), _cfg()) is not None
    assert bs.score_one(bs.BidStrength(code="1", bid_vol_ratio=1.0), _cfg()) is not None


# ---------------------------------------------------------------- 缺失标记
def test_missing_tagging():
    st = bs.BidStrength(code="1", bid_vol_ratio=1.5)
    bs._tag_missing({"1": st})
    assert set(st.missing) == {"accel", "qc"}
    assert not st.complete


# ---------------------------------------------------------------- 量比去噪
def test_min_yday_amt_constant():
    """昨日竞价额下限 100 万(低于此值量比失真: 昨额1万 → 量比302倍)"""
    assert bs.MIN_YDAY_BID_AMT_WAN == 100.0


# ---------------------------------------------------------------- 真实场景回归
def test_jinjian_case():
    """金健米业 600127 (2026-09-08): 竞价全程撤单, 量能没放大 → 不该拿高分。

    实测: 9:24=7.54% → 9:25=3.99%(加速度 -3.55), 竞价量比 1.00(今11436万/昨11423万),
    未命中抢筹名单。老口径靠"竞价涨幅 3.99% 恰好落 3~5.5% 满分档"登顶(80分),
    新口径下该因子应显著低于满分(0.55 - 0.08 = 0.47)。
    """
    st = bs.BidStrength(code="600127", bid_vol_ratio=1.00, accel=-3.55)
    s = bs.score_one(st, _cfg())
    assert s == pytest.approx(0.47)      # 0.55(量比1.0档) - 0.08(跳水)
    assert s < 0.6                        # 明确不是"强势"


def test_strong_case_ranks_higher():
    """放量 + 抢筹 + 末段拉升 → 接近满分, 排序必须高于金健米业"""
    weak = bs.score_one(bs.BidStrength(code="600127", bid_vol_ratio=1.00, accel=-3.55), _cfg())
    strong = bs.score_one(bs.BidStrength(code="000523", bid_vol_ratio=27.88,
                                         qc_delta=1.2, qc_last=True, accel=1.5), _cfg())
    assert strong == 1.0
    assert strong > weak * 2


# ---------------- 日期回退(2026-09-09) ----------------
def test_fill_snapshot_falls_back_when_passed_date_has_no_snapshot(monkeypatch):
    """传入未来日期(9/9 凌晨 date='2026-09-09', 9_25 快照未生成) → 自动回退到
    MAX(date) 的最近 9:25 快照; 否则 strength 全空 → 异动列变成 0(9/9 0:37 主反馈真因)。
    直接验证 mock 函数被调用 + date 被替换为最近交易日。"""
    calls = []

    def fake_get_conn():
        class _Cur:
            def execute(self_inner, sql, params=()):
                sql_l = sql.strip()
                if "SELECT 1 FROM snapshot_bid" in sql_l:
                    class _R:
                        def fetchone(_): return None
                    return _R()
                if "SELECT MAX(date) FROM snapshot_bid" in sql_l \
                        and "WHERE time_point='9_25'" in sql_l and "date <" not in sql_l:
                    calls.append(("max_no_lt", params))
                    class _R:
                        def fetchone(_): return ("2026-09-08",)
                    return _R()
                if "date < ? AND time_point='9_25'" in sql_l:
                    calls.append(("yday", params))
                    class _R:
                        def fetchone(_): return None
                    return _R()
                # 默认空游标
                class _Empty:
                    def fetchone(_): return None
                    def __iter__(self_inner): return iter([])
                return _Empty()

        class _Conn:
            def cursor(self): return _Cur()
            def close(self): pass
        return _Conn()

    monkeypatch.setattr("app.db.database.get_conn", fake_get_conn)
    from app.services.bid_strength import _fill_snapshot
    out = {}
    _fill_snapshot(out, want={"600127"}, date="2099-01-01")
    # 关键: 触发了 MAX(date) 回退查询
    assert any(c[0] == "max_no_lt" for c in calls), \
        "传入日期无快照时必须回退到 MAX(date) 的最近交易日 — 调用列表: %r" % calls
