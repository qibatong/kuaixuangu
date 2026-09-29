# -*- coding: utf-8 -*-
"""涨停判据唯一真相源: 板块幅度(含 920 段北交所) + 四舍五入涨停价式。

背景(2026-09-29, 生产数据回放对拍: 近 120 日 × 4002 只 × 逐日):
  · 原内联判据 `code[:2] in ("30","68")` / `code[:1] in ("8","4")` 会把 **920 段北交所
    漏进主板 10%** ⇒ 北交所 30% 涨停被当非涨停(封单清零、分层判错)。
  · 主人原式(1 分钱容差)多判 28 次(低价股假阳性); 旧阈值式(≥上限−0.1pp)漏判 198 次(≈2.1%);
    四舍五入涨停价式最准(实例见下)。
"""
import pytest

from app.services import auction_snapshot as asnap


# ---------- 板块涨停幅度 ----------
@pytest.mark.parametrize("code,pct", [
    ("600825", 0.10), ("000678", 0.10), ("002074", 0.10),      # 沪深主板 10%
    ("300876", 0.20), ("301190", 0.20), ("688981", 0.20), ("689009", 0.20),  # 创业/科创 20%
    ("920001", 0.30), ("920819", 0.30),                        # ★ 北交所新号段(本次修复)
    ("830799", 0.30), ("871981", 0.30), ("430047", 0.30), ("889999", 0.30),
])
def test_zt_limit_pct(code, pct):
    assert asnap.zt_limit_pct(code) == pct


def test_bj_920_no_longer_treated_as_main_board():
    """🔴 回归: 920 段必须按北交所 30%, 不得落进主板 10%。"""
    assert asnap.zt_limit_pct("920001") == 0.30
    assert asnap._is_zt("920001", 9.9) is False      # 主板阈值不能误判成涨停
    assert asnap._is_zt("920001", 29.9) is True


# ---------- 四舍五入涨停价式 ----------
def test_round_limit_price_catches_true_limit_up():
    """000012: 昨收 3.64 → 收 4.00 = +9.89%, 但 4.00 就是涨停价 ⇒ 是真涨停。
    旧阈值式(≥9.9%)会**漏判**, 本式必须判出。"""
    assert asnap.zt_price("000012", 3.64) == 4.00
    assert asnap.is_zt_by_price("000012", 4.00, 3.64) is True
    assert asnap.is_zt_by_change("000012", 9.89, 3.64) is True
    assert asnap.is_zt_by_change("000012", 9.89) is False     # 无 pre_close ⇒ 退回旧阈值式(已知漏判)


def test_round_limit_price_rejects_low_price_false_positive():
    """000004: 昨收 0.26 → 收 0.28 = +7.69%, 不是涨停。
    主人原式的 1 分钱容差会**误判**为涨停(0.01 元 ≈ 4%), 本式必须否掉。"""
    assert asnap.zt_price("000004", 0.26) == 0.29
    assert asnap.is_zt_by_price("000004", 0.28, 0.26) is False
    assert asnap.is_zt_by_change("000004", 7.69, 0.26) is False


def test_price_judge_is_none_without_pre_close():
    """缺 pre_close 一律返回 None(不猜), 由调用方降级。"""
    assert asnap.is_zt_by_price("600825", 10.06, None) is None
    assert asnap.is_zt_by_price("600825", 10.06, 0) is None


def test_zt_price_half_cent_rounds_down():
    """🔴 主人定标实例(2026-09-29): **武汉蓝电 920779 前收 21.85 → 收 28.40 是涨停**。

    21.85 × 1.3 = **28.405 恰在半分位** ⇒ 交易所涨停价 = **28.40**(不是 28.41);
    原"纯四舍五入"实现给 28.41 ⇒ 把真涨停判成非涨停(漏判)。
    """
    assert asnap.zt_price("920779", 21.85) == 28.40
    assert asnap.is_zt_by_price("920779", 28.40, 21.85) is True
    # 非半数仍走四舍五入 —— 不许把低价股的进位一起改坏
    assert asnap.zt_price("000004", 0.26) == 0.29        # 0.286 → 0.29
    assert asnap.is_zt_by_price("000004", 0.28, 0.26) is False
    assert asnap.zt_price("000012", 3.64) == 4.00        # 4.004 → 4.00
    assert asnap.zt_price("600825", 10.0) == 11.00       # 11.000 整
    assert asnap.zt_price("300876", 10.0) == 12.00       # 创业板 20%
    # 与 scorer 侧同口径(唯一真相源, 防止两套实现漂移)
    from app.services import scorer
    assert asnap.zt_price("920779", 21.85) == scorer.zt_price("920779", 21.85) == 28.40
    for c, pc in (("920779", 21.85), ("000004", 0.26), ("000012", 3.64), ("600825", 10.0),
                  ("300876", 10.0), ("688981", 33.33), ("830799", 7.77)):
        assert asnap.zt_price(c, pc) == scorer.zt_price(c, pc), (c, pc)


def test_zt_price_st_5pct_needs_name():
    """ST 的 5% 必须把名称传进来(不传按 10% 算)。

    🔴 2026-09-29 首版把 `is_yizi` 改成走 `zt_price` 时漏了 name ⇒ 直接把
      `test_phase1.py::test_is_yizi_st_5pct` 打红(既有 ST 保护被打破), 此用例为钉子。
    """
    assert asnap.zt_price("600001", 10.0, "ST某某") == 10.50
    assert asnap.zt_price("600001", 10.0) == 11.00                    # 不传 ⇒ 10%
    assert asnap.is_zt_by_price("600001", 10.50, 10.0, name="ST某某") is True
    assert asnap.is_zt_by_price("600001", 10.50, 10.0) is False


# ---------- 兼容入口 ----------
def test_compat_entry_keeps_old_threshold_when_no_pre_close():
    assert asnap._is_zt("600825", 10.06) is True
    assert asnap._is_zt("600825", 9.5) is False
    assert asnap._is_zt("300876", 19.9) is True
    assert asnap._is_zt("600825", None) is False


def test_three_call_sites_share_one_truth():
    """三处调用点(叠加段/质量报表/分层)必须都走同一个判据 —— 源码级断言, 防止再被复制成内联。"""
    import inspect
    src = inspect.getsource(asnap)
    # 内联的旧写法必须彻底消失
    assert 'bc >= 19.9' not in src
    assert 'bc >= 29.9' not in src
    assert 'bc >= 9.9' not in src
    assert src.count("is_zt_by_change(") >= 3          # 定义 1 次 + 调用 ≥2 次
