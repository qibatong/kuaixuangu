# -*- coding: utf-8 -*-
"""封单额「按时点取分时字段」单元测试(2026-09-29 主人拍板)

规则: 9_15→fa_0915 / 9_20→fa_0920 / 9_24→fa_0924 / 9_25→fa_0925
      (猫爪 daily_auc_fd 官方语义: fa_MMDD = 该时刻前最后一笔「匹配价=涨停价」的竞价金额)

覆盖:
  ① 四个时点各自取到**本时刻**的封单, 互不串用
  ② 9:15 采集时**不得**采用 fa_0925(9:25 尚未发生, 旧写法正是因此让该列恒空)
  ③ fd_amount 不再作为封单来源 —— 它与 fa_0925 不是一个口径
     (2026-09-29 只读探针实测新华传媒 18.53亿 vs 77.37亿, 差 4 倍)
  ④ 真封单标记 `_seal_meoz` 落盘前可恢复(不被东财涨幅"非涨停清零"误杀)
  ⑤ 同族兜底: 9_25 主字段为 0 时退到 fa_0925l / fa_0920f
"""
import pytest

from app.services import auction_snapshot as A


# 四个时点的封单值刻意设为**互不相等且量级不同**, 一旦串用会立刻红
SEAL_0915 = 8.09e9
SEAL_0920 = 7.61e9
SEAL_0924 = 6.50e9
SEAL_0925 = 7.74e9


@pytest.fixture
def fake_meoz(monkeypatch):
    """伪造 meoz_client: screening 主源 + daily_auc_fd 分时封单(四时点全给)。"""
    from app.services import meoz_client

    td = A._bj_date().replace("-", "")
    code = "600825"

    scr = {
        code: {"tradedate": td, "symbol": code, "name": "新华传媒",
               "auc_pct_chg": 10.06, "auc_amt": 42364600,
               "circ_mv": 9.0e9, "free_float_mv": 4.0e9,
               # 🔴 口径不同的字段: 必须**不再**被当作封单
               "fd_amount": 1.85e9,
               # screening 侧同名字段(9_15 同源兜底)
               "fa_0915": SEAL_0915,
               "fa_0920f": 1.0e8, "fa_0925l": 7.69e9},
    }
    fd = {
        code: {"tradedate": td, "symbol": code, "name": "新华传媒",
               "fa_0915": SEAL_0915, "fa_0920": SEAL_0920,
               "fa_0924": SEAL_0924, "fa_0925": SEAL_0925,
               "fa_0920f": 1.0e8, "fa_0925l": 7.69e9},
    }
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "screening_map", lambda **k: scr)
    monkeypatch.setattr(meoz_client, "valuation_map", lambda **k: {})
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: fd)
    return code


@pytest.mark.parametrize("tp,expect", [
    ("9_15", SEAL_0915),
    ("9_20", SEAL_0920),
    ("9_24", SEAL_0924),
    ("9_25", SEAL_0925),
])
def test_seal_takes_the_field_of_that_time_point(fake_meoz, tp, expect):
    """① 每个时点取**本时刻**的封单"""
    raw = {}
    A._merge_meoz(raw, tp)
    assert raw[fake_meoz]["bid_buy_amt"] == pytest.approx(expect)


def test_0915_never_falls_back_to_fa_0925(fake_meoz):
    """② 9:15 不得采用 fa_0925 —— 旧写法四个时点共取 fa_0925, 该列因此恒空"""
    raw = {}
    A._merge_meoz(raw, "9_15")
    got = raw[fake_meoz]["bid_buy_amt"]
    assert got == pytest.approx(SEAL_0915)
    assert got != pytest.approx(SEAL_0925)


def test_fd_amount_is_not_used_as_seal(fake_meoz):
    """③ fd_amount 与 fa_09xx 口径不同, 不得再作封单来源(差 4 倍)"""
    raw = {}
    A._merge_meoz(raw, "9_15")
    assert raw[fake_meoz]["bid_buy_amt"] != pytest.approx(1.85e9)


def test_seal_meoz_marker_for_restore(fake_meoz):
    """④ 真封单标记落 `_seal_meoz`, 供 snapshot_at 在"非涨停清零"后恢复"""
    raw = {fake_meoz: {"bid_change": 0, "bid_amt": 0, "name": "新华传媒",
                       "bid_buy_amt": 0}}
    A._merge_meoz(raw, "9_20")
    assert raw[fake_meoz]["_seal_meoz"] == pytest.approx(SEAL_0920)
    assert raw[fake_meoz]["bid_buy_amt"] == pytest.approx(SEAL_0920)


def test_9_25_fallback_within_same_family(monkeypatch, fake_meoz):
    """⑤ 9_25 主字段为 0 → 同族兜底 fa_0925l(不越级去拿 9:15 的值)"""
    from app.services import meoz_client
    td = A._bj_date().replace("-", "")
    code = fake_meoz
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: {
        code: {"tradedate": td, "symbol": code, "name": "新华传媒",
               "fa_0915": SEAL_0915, "fa_0920": SEAL_0920,
               "fa_0925": 0, "fa_0925l": 7.69e9},
    })
    raw = {}
    A._merge_meoz(raw, "9_25")
    assert raw[code]["bid_buy_amt"] == pytest.approx(7.69e9)


def test_cross_day_fd_rows_are_dropped(monkeypatch, fake_meoz):
    """⑥ 串日防护: tradedate ≠ 当日 → 整源丢弃, 不得把昨日的 fa_0915 当今日 9:15 封单"""
    from app.services import meoz_client
    import datetime
    yday = (datetime.date.fromisoformat(A._bj_date()) - datetime.timedelta(days=1))
    yd = yday.strftime("%Y%m%d")
    code = fake_meoz
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: {
        code: {"tradedate": yd, "symbol": code, "name": "新华传媒",
               "fa_0915": SEAL_0915, "fa_0925": SEAL_0925},
    })
    # screening 侧的 fa_0915 同源兜底也要一并屏蔽, 否则它会顶上来掩盖串日判定
    monkeypatch.setattr(meoz_client, "screening_map", lambda **k: {
        code: {"tradedate": A._bj_date().replace("-", ""), "symbol": code,
               "name": "新华传媒", "auc_pct_chg": 10.06, "auc_amt": 42364600,
               "circ_mv": 9.0e9, "free_float_mv": 4.0e9},
    })
    raw = {}
    st = A._merge_meoz(raw, "9_15")
    assert st["seal_n"] == 0, "串日行必须被剔除"
    assert (raw[code]["bid_buy_amt"] or 0) == 0, "串日封单不得落库"


def test_default_time_point_is_9_25(fake_meoz):
    """缺省(兼容既有单测)必须收敛到 9:25 口径, 不能是任意时点"""
    raw = {}
    A._merge_meoz(raw)
    assert raw[fake_meoz]["bid_buy_amt"] == pytest.approx(SEAL_0925)
