# -*- coding: utf-8 -*-
"""昨日涨幅一致性回归(2026-09-08 修「top3 有时90分有时93分」)

现象: 同筛选条件两次请求, 名单相同但 top 票概率在 90↔93 之间跳。
根因: yesterday 因子权重 6%, 缺失走 default 0.15 / 命中走 0.4·0.65·0.9 → 单票
      概率差 1.5~4.5 分; 而命中率随"缓存回填进度 + 日K源熔断"在 0%~100% 之间跳。

本文件锁定三条修复:
  ① 日K 行**不把换手率当涨跌幅** — 同花顺 parts[7] 是换手率(实测 600127:
     真涨幅 8.35%, parts[7]=30.118); 东财走官方 f58 涨跌幅列
  ② 成交额对成功但涨跌幅缺失 → 允许**补齐重试**(原实现只按 pair 判定 → 预热走了
     无涨幅的源后, chg 整天都是 None)
  ③ 批级一致性: 本批命中率 < 阈值 → **整批返回空**, 杜绝半有半无

2026-09-10 二审: 昨比链 = 东财日K(主) → 腾讯 qfqday(同语义备源, 真实成交额万元)。
  会"编造字段"的源(全市场竞价 f615/f616 用现价假造)必须删; 同语义真实数据保留 ——
  东财 push2his 为接口级时段性风控(命中率约 5%), 单源会让收盘落库永久空转。
  故 ① 中"腾讯 qfqday 自算涨跌幅"用例保留(_fetch_yesterday_amount_tencent 仍在线)。
"""
import json
import time
import urllib.request
from datetime import date, timedelta

import pytest

from app.core import config
from app.services import fetcher

# conftest 的 session 级 mock_data_source 把 fetch_yesterday_changes 整体替换为
# 假实现(恒返回 1.5); 本文件的批级一致性用例测的正是真实实现 → 还原(同
# test_yesterday_cache 对 fetch_yesterday_amounts 的处理)
_ORIG_FETCH_YDAY_CHG = fetcher.fetch_yesterday_changes


@pytest.fixture(autouse=True)
def _use_real_fetch_yday_chg(monkeypatch):
    """还原真实 fetch_yesterday_changes(该函数仍被 api/stocks.py 与 picker/pipeline 使用)"""
    monkeypatch.setattr(fetcher, "fetch_yesterday_changes", _ORIG_FETCH_YDAY_CHG)
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache.clear()
    yield
    with fetcher._yesterday_lock:
        fetcher._yesterday_cache.clear()


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


# ---------------- ① 腾讯: qfqday 自算(同语义备源) ----------------
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
    # after_close=False 显式指定: 本用例测"自算涨跌幅", 与收盘语义无关。若用默认值
    # (按真实时钟判定) 则盘后跑会落进"收盘后 T 必须=今天"的校验, 而 payload 是历史
    # 日期 ⇒ 必然返回 (None,None), 用例与真实时钟耦合(2026-09-26 加固)。
    pair, chg = fetcher._fetch_yesterday_amount_tencent("600127", after_close=False)
    assert pair is not None and abs(pair[0] - 434781.26) < 1
    assert abs(chg - 9.31) < 0.05, "应自算 (14.79-13.53)/13.53=9.31%%, 实际 %s" % chg


def test_yday_fallback_tencent_used_when_eastmoney_down(monkeypatch):
    """东财日K失败 → 自动切腾讯(同语义真实成交额); 腾讯也熔断 → 置空不抛异常"""
    from urllib.error import URLError

    # 东财 KLINE_HOSTS 全部失败
    monkeypatch.setattr(config, "KLINE_HOSTS", ["https://em1"])
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=5, context=None: (_ for _ in ()).throw(URLError("down")))
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_tencent",
                        lambda code, after_close=None: ([434781.26, 183383.84], 9.31))
    fetcher._HEALTH["eastmoney_kline"]["down_since"] = 0
    fetcher._HEALTH["eastmoney_kline"]["fails_in_row"] = 0
    fetcher._HEALTH["tencent_kline"]["down_since"] = 0
    fetcher._HEALTH["tencent_kline"]["fails_in_row"] = 0

    pair, chg = fetcher._fetch_yesterday_amount_one("600127")
    assert pair == [434781.26, 183383.84], "东财失败应切腾讯同语义备源, 不能直接置空"
    assert abs(chg - 9.31) < 0.05

    # 腾讯也在熔断冷却中 → 直接放弃(不抛异常)
    fetcher._HEALTH["eastmoney_kline"]["down_since"] = time.time()
    fetcher._HEALTH["eastmoney_kline"]["cooldown"] = 600
    fetcher._HEALTH["tencent_kline"]["down_since"] = time.time()
    fetcher._HEALTH["tencent_kline"]["cooldown"] = 600
    try:
        pair2, chg2 = fetcher._fetch_yesterday_amount_one("600127")
        assert pair2 is None and chg2 is None
    finally:
        for k in ("eastmoney_kline", "tencent_kline"):
            fetcher._HEALTH[k]["down_since"] = 0
            fetcher._HEALTH[k]["fails_in_row"] = 0


# ---------------- ① 东财: 官方涨跌幅列优先 ----------------
# (2026-09-11 删除 test_ths_close_derived_change_not_turnover:
#  该用例锁的是 _kline_amount_pair(close_idx=4) 的**同花顺列序**(parts[7]=换手率)。
#  同花顺昨比源已摘链, 其唯一使用方 _fetch_yesterday_amount_ths 亦已于同日整体删除
#  → 该参数组合在生产中已无调用者, 属「已删兜底代码对应的用例」。)


def test_eastmoney_uses_official_change_column():
    """东财行 日期,开,收,高,低,量,额,涨跌幅(f58) → 直接用官方涨跌幅列"""
    rows = [
        "2020-01-01,10.00,10.00,10.10,9.90,1000,10000000.0,1.20",
        "2020-01-02,10.00,10.50,10.60,10.00,1200,12000000.0,5.00",
    ]
    pair, chg = fetcher._kline_amount_pair(rows, after_close=False)
    assert pair is not None and abs(pair[0] - 1200.0) < 1
    assert chg == 5.00, "东财 f58 官方涨跌幅列应被优先采用"


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


# ---------------- ④ 收盘后语义: 今天已定格, 不得再跳过(2026-09-08) ----------------
def test_after_close_today_is_counted(monkeypatch):
    """收盘后(≥15:05)今天的 K 线已定格 → T 必须取今天, 否则"昨日涨幅"滞后一整天

    此前无条件跳过今天 → 收盘后到午夜前, 用户看到的"昨日涨幅"实际是前天的。
    ★ v4.11.55: 校验目标改 `_yday_expected_tdate()` 后, 本用例需显式声明"期望 T 日 = 今天"
    (否则真实时钟落在周末/节假日时, 期望 T 日会是上一交易日, 用例与日历耦合)。
    """
    today = fetcher._bj_date_str()
    monkeypatch.setattr(fetcher, "_yday_expected_tdate",
                        lambda now=None: today.replace("-", ""))
    rows = [
        "%s,10.0,10.2,10.3,9.9,1000,10200000,3.55" % _d(-1),
        "%s,10.2,10.8,10.9,10.1,2000,21600000,5.88" % today,   # 今天(已收盘)
    ]
    pair, chg = fetcher._kline_amount_pair(rows, after_close=True)
    assert pair is not None and abs(pair[0] - 2160.0) < 1, "收盘后 T 应为今天"
    assert chg == 5.88, "收盘后涨跌幅应取今天的 5.88, 实际 %s" % chg


def test_intraday_today_still_skipped():
    """盘中(after_close=False)仍必须跳过今天 —— 未收盘 K 线不得参与"""
    today = fetcher._bj_date_str()
    rows = [
        "%s,10.0,10.2,10.3,9.9,1000,10200000,3.55" % _d(-1),
        "%s,10.2,10.8,10.9,10.1,2000,21600000,5.88" % today,
    ]
    pair, chg = fetcher._kline_amount_pair(rows, after_close=False)
    assert pair is not None and abs(pair[0] - 1020.0) < 1, "盘中 T 应为上一交易日"
    assert chg == 3.55
