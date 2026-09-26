# -*- coding: utf-8 -*-
"""「昨日成交额数据冻结」回归 —— 期望 T 日与三源「收盘必须确认今日」纪律
=========================================================================
(2026-09-26 生产事故, 修复见 v4.11.53)

**事故现场**: 生产 `yday_amount` 自 2026-09-10 首次落库后**再没更新过** ——
09-14~09-24 共 9 个交易日, 每天 09:25 读到的都是同一批值(与 09-10 收盘涨幅逐位
相同率 99.6%、与 09-24 交集 418 只逐位相同率 0%), 而 tdate 标签每天照常前进。
已排除"同批重算"假象(各日行 ts 各不相同)。该因子权重 `settings.scoring.w_yesterday
= 0.05`, 于是失准 9 天。

**机制是三方闭环, 缺一不可**:
  ① 读侧 `_yday_hydrate_from_db` 无条件接受"5 天内"的库行, 并把它写成
     `[today, pair, now, chg]` —— 等于向 `_collect_yday_need` 宣称"今天已经拉到了";
  ② 于是 need 恒为空 → **永不重拉**;
  ③ 收盘落库 `_persist_to_db` 把同一批值**原样回写**、只把 tdate 推进到今天
     (拉取路径 `is_today()` 在 `after_close` 时无条件"不跳过今天", 从而把"昨天那根"
     当作 T 日返回)。

**修复 = 四条判据**(逐条对应本文件的用例):
  A. `_yday_expected_tdate()` 定义"此刻应有的 T 日", 读库逐行比对(TestA);
  B. 三源(东财 / 腾讯 / 猫爪)收盘后**必须确认拿到今天那根K线**, 否则返回 (None,None)
     —— 从根上拒绝"拿昨天那根冒充今天"(TestB);
  C. `_persist_to_db` 落库前再校验 tdate(第三道防线, 见 test_yday_amount_db);
  D. 收盘落库 0 行 → `_prewarm_once` 返回 False, 收盘窗口内重试(见 test_yday_amount_db)。
"""
import calendar
import json

import pytest

from app.services import fetcher


def _ts(y, m, d, hh, mm):
    """北京时间 (y,m,d,hh,mm) → epoch 秒(供 `_yday_expected_tdate(now=...)` 注入)。"""
    return calendar.timegm((y, m, d, hh, mm, 0, 0, 0, 0)) - 8 * 3600


@pytest.fixture(autouse=True)
def _reset_tencent_health():
    """腾讯用例会走 _record 更新数据源健康统计 → 前后复位, 防污染跨文件的熔断判定"""
    h = fetcher._HEALTH["tencent_kline"]
    snap = dict(h)
    yield
    h.clear()
    h.update(snap)


# ======================= TestA: 期望 T 日 =======================
def test_expected_tdate_across_trading_day():
    """交易日: 盘前/盘中 → 上一交易日; 收盘后(>=15:05) → 今天"""
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 24, 9, 5)) == "20260923"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 24, 14, 0)) == "20260923"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 24, 15, 4)) == "20260923", "15:05 前仍是盘中口径"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 24, 15, 10)) == "20260924"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 24, 23, 59)) == "20260924"


def test_expected_tdate_across_midautumn_and_weekend():
    """中秋(09-25 五) / 周末(09-26~27) → 一律指 09-24; 节后周一收盘后才指自己"""
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 25, 10, 0)) == "20260924", "法定休市日"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 25, 15, 10)) == "20260924", "休市日盘后不得指自己"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 26, 12, 0)) == "20260924", "周六"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 27, 12, 0)) == "20260924", "周日"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 28, 9, 5)) == "20260924", "节后周一盘前"
    assert fetcher._yday_expected_tdate(_ts(2026, 9, 28, 15, 10)) == "20260928", "节后周一盘后"


def test_expected_tdate_across_national_day():
    """国庆 10-01~10-07 全休市 → 10-08 盘前的"上一交易日"是节前最后一天 09-30"""
    for dd in (1, 2, 5, 6, 7):
        assert fetcher._yday_expected_tdate(_ts(2026, 10, dd, 10, 0)) == "20260930", \
            "10-0%d 是法定休市日" % dd
    assert fetcher._yday_expected_tdate(_ts(2026, 10, 8, 9, 5)) == "20260930"
    assert fetcher._yday_expected_tdate(_ts(2026, 10, 8, 15, 10)) == "20261008"


def test_holiday_never_becomes_expected_tdate():
    """★ 核心性质: **任何非交易日都不得成为期望 T 日**

    这正是生产脏行(`tdate=20260925`)永久失效的判据 —— 09-25 是中秋法定休市日,
    永远不可能等于任何时刻的期望 T 日, 因此那 5556 行无论哪天读都命中不了,
    等价于不存在(修复后无需人工 DELETE 也能自愈; 清理只是让库干净)。
    """
    for (y, m, d) in [(2026, 9, 25), (2026, 9, 26), (2026, 9, 27),
                      (2026, 10, 1), (2026, 10, 2), (2026, 10, 5), (2026, 10, 6), (2026, 10, 7)]:
        want = "%04d%02d%02d" % (y, m, d)
        for (hh, mm) in [(9, 5), (12, 0), (15, 10), (20, 0)]:
            assert fetcher._yday_expected_tdate(_ts(y, m, d, hh, mm)) != want, \
                "%04d-%02d-%02d %02d:%02d 非交易日, 不得被当作 T 日" % (y, m, d, hh, mm)


# ======================= TestB: 三源「收盘必须确认今日」 =======================
_EM_ROWS_2D = [
    "2026-09-23,10.0,10.2,10.3,9.9,1000,10200000,3.55",
    "2026-09-24,10.2,10.8,10.9,10.1,2000,21600000,5.88",
]
_EM_ROW_TODAY = "2026-09-25,10.8,11.0,11.1,10.7,500,5500000,1.85"


def test_eastmoney_close_requires_today(monkeypatch):
    """东财: 收盘后源只更新到昨天 → 必须 (None,None), 不得把 09-24 那根当 09-25 返回"""
    monkeypatch.setattr(fetcher, "_bj_date_str", lambda: "2026-09-25")
    assert fetcher._kline_amount_pair(_EM_ROWS_2D, after_close=True) == (None, None)
    pair, chg = fetcher._kline_amount_pair(_EM_ROWS_2D + [_EM_ROW_TODAY], after_close=True)
    assert pair == [550.0, 2160.0], "拿到今天那根后才正常返回(万元)"
    assert chg == 1.85


def test_eastmoney_intraday_unaffected(monkeypatch):
    """盘中(未收盘)不受此校验影响: 无条件下 rows[-1] 就是"最近已收盘交易日" """
    monkeypatch.setattr(fetcher, "_bj_date_str", lambda: "2026-09-25")
    pair, chg = fetcher._kline_amount_pair(_EM_ROWS_2D, after_close=False)
    assert pair == [2160.0, 1020.0]
    assert chg == 5.88


def test_meoz_close_requires_today():
    """猫爪(主源): 同上纪律 —— 这条是"换源后事故复发"的唯一入口, 必须钉死"""
    def _row(d, amt, pct, close):
        return {"tradedate": d, "amount": amt, "pct_chg": pct, "close": close}
    rows = [_row("20260923", 1.02e7, 3.55, 10.2), _row("20260924", 2.16e7, 5.88, 10.8)]
    assert fetcher._yday_pair_from_daily(rows, today="2026-09-25", after_close=True) == (None, None)
    pair, chg = fetcher._yday_pair_from_daily(
        rows + [_row("20260925", 5.5e6, 1.85, 11.0)], today="2026-09-25", after_close=True)
    assert pair == [550.0, 2160.0]
    assert chg == 1.85


def test_meoz_intraday_unaffected():
    """盘中: 跳过今天(未收盘) → T = 最近已收盘交易日 09-24"""
    def _row(d, amt, pct, close):
        return {"tradedate": d, "amount": amt, "pct_chg": pct, "close": close}
    rows = [_row("20260923", 1.02e7, 3.55, 10.2),
            _row("20260924", 2.16e7, 5.88, 10.8),
            _row("20260925", 5.5e6, 1.85, 11.0)]      # 今天(未收盘)应被跳过
    pair, chg = fetcher._yday_pair_from_daily(rows, today="2026-09-25", after_close=False)
    assert pair == [2160.0, 1020.0]
    assert chg == 5.88


def test_tencent_close_requires_today(monkeypatch):
    """腾讯(第三源): 同一条纪律 —— "源不同、纪律必须相同", 否则换源即复发"""
    monkeypatch.setattr(fetcher, "_bj_date_str", lambda: "2026-09-25")

    def _mk(dates):
        return {"data": {"sh600127": {"qfqday": [
            [d, "10.0", "10.5", "10.6", "9.9", "1000.0", {}, "1.0", "100000.0", ""]
            for d in dates]}}}

    class _Resp:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetcher, "_http_get",
                        lambda *a, **k: _Resp(json.dumps(_mk(["2026-09-23", "2026-09-24"])).encode()))
    assert fetcher._fetch_yesterday_amount_tencent("600127", after_close=True) == (None, None)

    monkeypatch.setattr(fetcher, "_http_get",
                        lambda *a, **k: _Resp(json.dumps(_mk(
                            ["2026-09-23", "2026-09-24", "2026-09-25"])).encode()))
    pair, _chg = fetcher._fetch_yesterday_amount_tencent("600127", after_close=True)
    # 腾讯 row[8] 口径 = **万元**(与东财 row[6] 的"元"不同, 这是历史踩坑点), 故原样返回
    assert pair == [100000.0, 100000.0]


# ======================= 交易日历: prev_trade_date(本次新增) =======================
def test_prev_trade_date_basics():
    """跨周末/长假取"上一个交易日"—— 这是期望 T 日的底座, 不能用"当天-1天"粗算"""
    from app.core import trade_calendar as tc
    assert tc.prev_trade_date("2026-09-24") == "2026-09-23"      # 普通前推
    assert tc.prev_trade_date("2026-09-25") == "2026-09-24"      # 中秋(休市日)
    assert tc.prev_trade_date("2026-09-28") == "2026-09-24"      # 节后周一 → 跨三天
    assert tc.prev_trade_date("2026-10-08") == "2026-09-30"      # 国庆后 → 节前最后交易日
    assert tc.prev_trade_date("2026-09-24", include_today=True) == "2026-09-24"
    assert tc.prev_trade_date("2026-09-26", include_today=True) == "2026-09-24", "周六本身非交易日"


def test_prev_trade_date_guards():
    """无法识别 / 回溯不足 → 返回 None(弃权), **不猜日期**"""
    from app.core import trade_calendar as tc
    assert tc.prev_trade_date("garbage") is None
    assert tc.prev_trade_date(None) is not None, "None = 今天, 正常返回"
    assert tc.prev_trade_date("2026-09-24", max_back=1) == "2026-09-23"
    assert tc.prev_trade_date("2026-09-28", max_back=1) is None, "只回溯 1 天跨不过周末 ⇒ 弃权"
