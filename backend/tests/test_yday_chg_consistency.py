# -*- coding: utf-8 -*-
"""昨日涨幅一致性回归(2026-09-08 修「top3 有时90分有时93分」)

现象: 同筛选条件两次请求, 名单相同但 top 票概率在 90↔93 之间跳。
根因: yesterday 因子权重 6%, 缺失走 default 0.15 / 命中走 0.4·0.65·0.9 → 单票
      概率差 1.5~4.5 分; 而命中率随"缓存回填进度 + 日K源熔断"在 0%~100% 之间跳。

本文件锁定三条修复:
  ① 兜底源(同花顺/腾讯)**自算**涨跌幅 — 同花顺 parts[7] 是换手率不是涨幅(实测
     600127: 真涨幅 8.35%, parts[7]=30.118)
  ② 成交额对成功但涨跌幅缺失 → 允许**补齐重试**(原实现只按 pair 判定 → 预热走了
     无涨幅的兜底源后, chg 整天都是 None)
  ③ 批级一致性: 本批命中率 < 阈值 → **整批返回空**, 杜绝半有半无
"""
import json
import time
from datetime import date, timedelta

from app.core import config
from app.services import fetcher


def _d(offset):
    """相对今天的日期(YYYYMMDD), offset<0 表示过去"""
    return (date.today() + timedelta(days=offset)).strftime("%Y%m%d")


def _setup_cache(entries):
    today = fetcher._bj_date_str()
    fetcher._yesterday_cache.clear()
    for code, ent in entries.items():
        fetcher._yesterday_cache[code] = ent
    return today


def teardown_function():
    fetcher._yesterday_cache.clear()


# ---------------- ① 同花顺: parts[7] 是换手率, 必须自算 ----------------
def test_ths_close_derived_change_not_turnover():
    """真实同花顺行: 日期,开,高,低,收,量,额,**换手率** → 涨跌幅必须自算(收盘价环比)

    实测 600127 2026-09-08: 收 14.66 / 昨收 13.53 → 真涨幅 8.35%;
    parts[7]=30.118 是换手率 —— 沿用东财列序会把 30.118 喂进 yesterday 因子,
    落进 "9.5~99 → 0.65" 档, 与真实 8.35%(3~9.5 → 0.9 档)不符。
    """
    rows = [
        "%s,10.00,10.10,9.90,10.00,1000,10000000.0,1.5,," % _d(-3),
        "%s,10.10,11.20,10.05,11.00,1200,12000000.0,2.1,," % _d(-2),
        "%s,11.00,11.60,10.90,11.50,900,9000000.0,30.118,," % _d(-1),
    ]
    pair, chg = fetcher._kline_amount_pair(rows, close_idx=4, chg_idx=None)
    assert pair is not None and abs(pair[0] - 900.0) < 1          # 900 万 → 万元
    assert abs(chg - 4.55) < 0.01, "应自算 (11.5-11.0)/11.0=4.55%%, 实际 %s" % chg
    assert abs(chg - 30.118) > 1, "不得把换手率当涨跌幅"


def test_eastmoney_uses_official_change_column():
    """东财行 日期,开,收,高,低,量,额,涨跌幅(f58) → 直接用官方涨跌幅列"""
    rows = [
        "2020-01-01,10.00,10.00,10.10,9.90,1000,10000000.0,1.20",
        "2020-01-02,10.00,10.50,10.60,10.00,1200,12000000.0,5.00",
    ]
    pair, chg = fetcher._kline_amount_pair(rows)
    assert pair is not None and abs(pair[0] - 1200.0) < 1
    assert chg == 5.00, "东财 f58 官方涨跌幅列应被优先采用"


# ---------------- ① 腾讯: qfqday 自算 ----------------
def test_tencent_qfqday_derives_change(monkeypatch):
    """腾讯 qfqday 行 [日期,开,收,高,低,量,{},换手率,额万元,''] → 收盘价环比自算

    实测 600127: 2026-09-07 收 13.53 → 2026-09-08 收 14.79 = +9.31%;
    row[7] 是换手率(46.62), 不是涨跌。
    """
    d2, d1 = _d(-2), _d(-1)
    payload = {"data": {"sh600127": {"qfqday": [
        [d2, "13.23", "13.53", "13.53", "12.80", "1379519.00", {}, "21.50", "183383.84", ""],
        [d1, "14.07", "14.79", "14.88", "14.01", "2992169.00", {}, "46.62", "434781.26", ""],
    ]}}}

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
                        lambda *a, **k: _Resp(json.dumps(payload).encode()))
    pair, chg = fetcher._fetch_yesterday_amount_tencent("600127")
    assert pair is not None and abs(pair[0] - 434781.26) < 1
    assert abs(chg - 9.31) < 0.05, "应自算 (14.79-13.53)/13.53=9.31%%, 实际 %s" % chg


# ---------------- ② 涨跌幅缺失 → 允许补齐重试 ----------------
def test_chg_missing_triggers_refetch(monkeypatch):
    """pair 成功但 chg=None 且过补齐窗口 → 必须进 need(否则预热带走的缺失整天不补)"""
    today = fetcher._bj_date_str()
    old = time.time() - (config.YDAY_CHG_RETRY_TTL + 10)
    _setup_cache({"600127": [today, [1000.0, 900.0], old, None]})
    assert fetcher._collect_yday_need(["600127"], today, time.time()) == ["600127"]


def test_chg_present_not_refetched(monkeypatch):
    """chg 已有值 → 不得重复打扰数据源"""
    today = fetcher._bj_date_str()
    old = time.time() - (config.YDAY_CHG_RETRY_TTL + 10)
    _setup_cache({"600127": [today, [1000.0, 900.0], old, 8.35]})
    assert fetcher._collect_yday_need(["600127"], today, time.time()) == []


def test_chg_missing_within_window_not_refetched():
    """补齐窗口内(未过 TTL) → 不重拉, 防高频打扰数据源"""
    today = fetcher._bj_date_str()
    recent = time.time() - 10
    _setup_cache({"600127": [today, [1000.0, 900.0], recent, None]})
    assert fetcher._collect_yday_need(["600127"], today, time.time()) == []


# ---------------- ③ 批级一致性 ----------------
def test_batch_below_threshold_returns_empty():
    """命中率 2/5=40% < 60% → 整批返回空(全走 default), 杜绝半有半无"""
    today = fetcher._bj_date_str()
    _setup_cache({
        "c1": [today, [1.0, 1.0], 0.0, 5.0],
        "c2": [today, [1.0, 1.0], 0.0, 6.0],
        "c3": [today, [1.0, 1.0], 0.0, None],
        "c4": [today, [1.0, 1.0], 0.0, None],
        "c5": [today, [1.0, 1.0], 0.0, None],
    })
    out = fetcher.fetch_yesterday_changes(["c1", "c2", "c3", "c4", "c5"])
    assert out == {}, "覆盖率不足时整批按缺失处理, 实际 %s" % out


def test_batch_above_threshold_returns_all():
    """命中率 4/5=80% ≥ 60% → 正常返回命中的 4 只"""
    today = fetcher._bj_date_str()
    _setup_cache({
        "c1": [today, [1.0, 1.0], 0.0, 5.0],
        "c2": [today, [1.0, 1.0], 0.0, 6.0],
        "c3": [today, [1.0, 1.0], 0.0, 1.0],
        "c4": [today, [1.0, 1.0], 0.0, 2.0],
        "c5": [today, [1.0, 1.0], 0.0, None],
    })
    out = fetcher.fetch_yesterday_changes(["c1", "c2", "c3", "c4", "c5"])
    assert set(out) == {"c1", "c2", "c3", "c4"}


def test_batch_consistency_can_be_disabled():
    today = fetcher._bj_date_str()
    _setup_cache({
        "c1": [today, [1.0, 1.0], 0.0, 5.0],
        "c2": [today, [1.0, 1.0], 0.0, None],
    })
    out = fetcher.fetch_yesterday_changes(["c1", "c2"], min_coverage=0)
    assert out == {"c1": 5.0}
