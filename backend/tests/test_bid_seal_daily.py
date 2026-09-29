# -*- coding: utf-8 -*-
"""连续 N 日竞价封单(bid_seal_daily)单元测试

覆盖:
  ① 展示集合 = **9:15 / 9:20 / 9:25 任一时点涨停**(2026-09-30 主人明确四步规则)
     —— 含盘中炸板票(9:15/9:20 封过板、9:25 回落、涨幅为负)
  ② 「一字」与「封单总额」只统计 **9:25 涨停**的行(Σ fa_0925l), 不被放宽的展示集带偏
  ③ 各时点涨停判据 = 该时点封单字段非零(官方语义「匹配价=涨停价」), 非涨停股无封单
  ④ 三层排序: ①9:25 涨停 ②9:20 涨停回落 ③仅 9:15 涨停
  ⑤ 连板数来自 limit_pool.limit_times(与模板 12/12 对拍的口径) + 串日防护
  ⑥ last_trade_days 跳过周末与法定休市(2026-09-25 中秋)
  ⑦ 环比 = 与更早一个交易日比; 最早一天为 None
  ⑧ 上游失败/无数据 → 空列降级, 不抛异常
"""
import pytest

from app.services import bid_seal_daily as B


def _mk(td, **kw):
    row = {"tradedate": td, "symbol": kw.pop("code"), "name": kw.pop("name", "测试股"),
           "theme_names_kpl": kw.pop("board", "概念A,概念B")}
    row.update(kw)
    return row


# ---- 夹具用到的数值(刻意互不相等, 串用时必红) ----
S0915 = 8.09e9
S0920 = 7.61e9
S0925L = 7.69e9
BROKEN_0915 = 1.1e8      # 炸板票: 9:15/9:20 封过板
BROKEN_0920 = 4.7e7


@pytest.fixture
def fake_fd(monkeypatch):
    """按日期返回不同夹具; 记录被请求的日期, 供断言用。"""
    from app.services import meoz_client
    calls = []

    def _map(date=None, **k):
        calls.append(str(date))
        if date == "20260929":
            return {
                # 沪主板 10.06% → 9:25 涨停, 三时点都有封单 ⇒ 层1 + 计入一字
                "600825": _mk(date, code="600825", name="新华传媒",
                              auc_pct_chg=10.06, fa_0915=S0915,
                              fa_0920=S0920, fa_0925=7.74e9, fa_0925l=S0925L),
                # 创业板 10.0% → **不是**涨停(20% 才涨停) 且**任何时点都无封单** ⇒ 不进榜
                "300001": _mk(date, code="300001", name="创业板假涨停", auc_pct_chg=10.0),
                # 炸板票: 9:15/9:20 封过板, 9:25 回落到 -0.51% ⇒ 必须**展示**(层2),
                #   但不计入一字/封单总额(它 9:25 没涨停)
                "300002": _mk(date, code="300002", name="跨境通",
                              auc_pct_chg=-0.51, fa_0915=BROKEN_0915, fa_0920=BROKEN_0920),
                # 仅 9:15 涨停 → 层3
                "600003": _mk(date, code="600003", name="仅915涨停",
                              auc_pct_chg=4.25, fa_0915=2.2e8),
                # 三个时点都非涨停、也无封单 → 不进榜
                "600004": _mk(date, code="600004", name="平平无奇", auc_pct_chg=3.1),
            }
        if date == "20260928":
            return {
                "600825": _mk(date, code="600825", name="新华传媒",
                              auc_pct_chg=10.0386, fa_0915=8.56e9,
                              fa_0920=9.87e9, fa_0925=9.99e9, fa_0925l=9.98e9),
            }
        return {}

    monkeypatch.setattr(meoz_client, "auc_fd_map", _map)

    # 连板数: 源 = limit_pool.limit_times(2026-09-29 与模板 12/12 逐位对拍确认)
    def _lp(date=None, **k):
        if date == "20260929":
            return {"600825": {"tradedate": date, "symbol": "600825",
                               "type": "u", "limit_times": 6}}
        return {}

    monkeypatch.setattr(meoz_client, "limit_pool_map", _lp)
    return calls


def _codes(d):
    return [r["code"] for r in d["rows"]]


def test_any_of_three_time_points_limit_up_is_shown(fake_fd):
    """① 四步规则: 9:15 / 9:20 / 9:25 任一时点涨停都展示"""
    d = B.day_seal("2026-09-29")
    got = set(_codes(d))
    assert "600825" in got, "9:25 涨停(三时点均封) 应展示"
    assert "300002" in got, "9:15/9:20 封过板、9:25 炸板(-0.51%) 必须展示"
    assert "600003" in got, "仅 9:15 涨停 应展示"
    assert "300001" not in got, "创业板 10.0% 非涨停且无封单, 不得进榜"
    assert "600004" not in got, "三时点都非涨停且无封单, 不得进榜"


def test_broken_board_row_is_layer2_and_not_counted_in_yizi(fake_fd):
    """② 炸板票进展示(层2), 但**不计入**一字/封单总额"""
    d = B.day_seal("2026-09-29")
    broken = [r for r in d["rows"] if r["code"] == "300002"][0]
    assert broken["layer"] == 2, "9:20 涨停回落 → 层2"
    assert broken["v9_15"] == pytest.approx(BROKEN_0915)
    assert broken["v9_20"] == pytest.approx(BROKEN_0920)
    assert broken["v9_25"] == 0, "9:25 已炸板, 无末笔封单"
    assert broken["chg"] == -0.51
    # 统计只算 9:25 涨停那只
    assert d["yizi"] == 1
    assert d["sealTotal"] == pytest.approx(S0925L)


def test_layer3_only_0915_limit_up(fake_fd):
    """仅 9:15 涨停 → 层3; 一字/总额仍不含它"""
    d = B.day_seal("2026-09-29")
    only15 = [r for r in d["rows"] if r["code"] == "600003"][0]
    assert only15["layer"] == 3
    assert only15["v9_20"] == 0 and only15["v9_25"] == 0


def test_three_layer_sort_order(fake_fd):
    """④ 三层排序: 层1(9:25) → 层2(9:20) → 层3(仅9:15)"""
    d = B.day_seal("2026-09-29")
    assert _codes(d) == ["600825", "300002", "600003"]


def test_yizi_and_total_match_verified_caliber(fake_fd):
    """② 一字 = 9:25 涨停只数; 总额 = Σ fa_0925l(模板四日逐位对拍的口径)"""
    d = B.day_seal("2026-09-29")
    assert d["date"] == "2026-09-29"
    assert d["yizi"] == 1
    assert d["sealTotal"] == pytest.approx(S0925L)
    r0 = d["rows"][0]
    assert r0["v9_15"] == pytest.approx(S0915)
    assert r0["v9_20"] == pytest.approx(S0920)
    assert r0["v9_25"] == pytest.approx(S0925L), "9:25 列取末笔, 与总额同字段"
    assert r0["chg"] == 10.06
    assert r0["layer"] == 1


def test_gem_board_10pct_without_seal_is_not_shown(fake_fd):
    """③ 分板块判涨停: 创业板 10.0% 不是涨停(且无封单) —— 用「涨幅≥9.9」会误收"""
    d = B.day_seal("2026-09-29")
    assert "300001" not in _codes(d)


def test_limit_times_attached_from_limit_pool(fake_fd):
    """⑤ 连板数来自 limit_pool.limit_times(与模板 12/12 对拍确认的口径)"""
    d = B.day_seal("2026-09-29")
    assert d["rows"][0]["code"] == "600825"
    assert d["rows"][0]["limitTimes"] == 6
    # 池里没有该票 → 0(前端不显示该标签), 而不是猜测/沿用别日值
    assert all(r["limitTimes"] == 0 for r in d["rows"] if r["code"] != "600825")


def test_limit_times_cross_day_is_dropped(monkeypatch, fake_fd):
    """⑤ 连板守串日纪律: tradedate ≠ 目标日 → 不显示(宁可少标签, 不挂错数)"""
    from app.services import meoz_client
    monkeypatch.setattr(meoz_client, "limit_pool_map", lambda date=None, **k: {
        "600825": {"tradedate": "20260926", "symbol": "600825",
                   "type": "u", "limit_times": 5},
    })
    d = B.day_seal("2026-09-29")
    assert d["rows"][0]["code"] == "600825"
    assert d["rows"][0]["limitTimes"] == 0


def test_cross_day_fd_rows_are_dropped(monkeypatch, fake_fd):
    """串日防护: tradedate ≠ 当日 → 整源丢弃"""
    from app.services import meoz_client
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda date=None, **k: {
        "600825": {"tradedate": "20260926", "symbol": "600825", "name": "新华传媒",
                   "auc_pct_chg": 10.06, "fa_0915": S0915, "fa_0925l": S0925L},
    })
    d = B.day_seal("2026-09-29")
    assert d["yizi"] == 0 and d["sealTotal"] == 0, "串日行不得被当成当日"
    assert d["rows"] == []


def test_last_trade_days_skips_weekend_and_holiday():
    """⑥ 2026-09-25 中秋(周五, 休市) + 09-26/27 周末 → 09-27 回溯应落到 09-24"""
    ds = B.last_trade_days(3, "2026-09-27")
    assert ds[0] == "2026-09-24", "周末请求必须回退到最近交易日"
    assert ds == ["2026-09-24", "2026-09-23", "2026-09-22"]
    # 交易日请求原样返回, 逐日回溯跳过 09-25
    assert B.last_trade_days(3, "2026-09-29") == ["2026-09-29", "2026-09-28", "2026-09-24"]


def test_build_trend_is_vs_next_older_day(fake_fd):
    """⑦ 环比: days[0] 与 days[1](更早一日)比; 最早一天为 None"""
    out = B.build(days=2, end="2026-09-29")
    days = out["days"]
    assert [d["date"] for d in days] == ["2026-09-29", "2026-09-28"]
    assert days[0]["diff"] == pytest.approx(S0925L - 9.98e9)
    assert days[0]["prevDate"] == "2026-09-28"
    assert days[0]["diffPct"] == pytest.approx(
        (S0925L - 9.98e9) / 9.98e9 * 100, abs=0.05)
    assert days[1]["diff"] is None and days[1]["prevDate"] is None


def test_upstream_failure_degrades_to_empty(monkeypatch):
    """⑧ 上游抛异常 → 空列降级, 不抛"""
    from app.services import meoz_client

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(meoz_client, "auc_fd_map", _boom)
    monkeypatch.setattr(meoz_client, "limit_pool_map", _boom)
    d = B.day_seal("2026-09-29")
    assert d["yizi"] == 0 and d["sealTotal"] == 0 and d["rows"] == []


def test_days_clamped_to_max(monkeypatch, fake_fd):
    """请求天数被夹到 MAX_DAYS 内(防一次拉爆上游配额)"""
    assert len(B.last_trade_days(999, "2026-09-29")) == B.MAX_DAYS
