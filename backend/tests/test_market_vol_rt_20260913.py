# -*- coding: utf-8 -*-
"""两市量能改取开盘啦实时接口(a=MarketSCLN) —— 2026-09-13

背景(主人反馈"相比昨日永远比的昨日总成交, 不是同一时点"): 首页「两市资金 /
较昨日同时」原依赖 worker 自存快照 market_brief_intraday_*, 有两个硬伤 ——
  ① 09:30 存在 amount=0 脏点 → 早盘匹配到它后增量虚高 7.7 倍
     (9/11 10:00 显示 +6631 亿, 正确应为 9/10 10:00 的 5750 亿 → +857 亿)
  ② 东财分页失败即**静默少算**(9/8 少 1491 亿 = -7.6%), 基准侧与今日侧都可能失真。
实时接口一次请求即给「今日此刻 + 昨日同一时点」, 两侧同源同口径。

开关: settings market_vol_rt(默认 0=关, 1=开); 取不到时**完全回退**, 不改行为。
"""
import pytest

from app.services import kpl, fetcher
from app.services import settings as st_svc


# 9/11 收盘实测形状的桩数据(单位: 万元)
FAKE_SCLN = {"info": {
    "last": "197189848",        # 今日此刻   → 19718.98 亿
    "s_zrcs": "164714782",      # 昨日全天   → 16471.48 亿
    "s_zrtj": "57500000",       # 昨日同一时点 → 5750.00 亿
    "s3_zrtj": "182102971",     # 前 3 日同期 → 18210.30 亿
    "ycln": "19718亿",
    "yclnstr": "19718亿(19.72%,增量3247亿)",
    "csbl": 19.72, "color": "1", "time": 1789313011, "trends": [],
}}


@pytest.fixture(autouse=True)
def _reset_vol_rt():
    """用例结束把开关复位成默认(0), 避免污染后续用例(本项目 session 顺序依赖敏感)"""
    yield
    try:
        st_svc.set("market_vol_rt", 0)
    except Exception:
        pass


# ---------- ① parse_market_volume_rt: 解析与容错 ----------
def test_parse_market_volume_rt_ok():
    """万元 → 亿元换算 + 四个量能字段 + 预测量能串"""
    v = kpl.parse_market_volume_rt(FAKE_SCLN)
    assert v is not None
    assert v["amount"] == 19718.98
    assert v["prev_same_time"] == 5750.00
    assert v["prev_full"] == 16471.48
    assert v["prev3_same_time"] == 18210.30
    assert v["forecast"] == "19718亿"
    assert v["forecast_str"] == "19718亿(19.72%,增量3247亿)"
    assert v["ts"] == 1789313011


@pytest.mark.parametrize("bad", [
    None,                        # 网络失败(_call 返回 None)
    {},                          # 无 info
    {"info": []},                # info 是列表 —— 日级历史接口(MarketSCLNKLine)的形状
    {"info": {"last": "0"}},     # 非交易时段 0 值脏点
    {"info": {"last": "abc"}},   # 脏字符串
    {"info": None},              # info 显式 None
])
def test_parse_market_volume_rt_bad(bad):
    """任何异常形状都返回 None —— 0 值绝不当实测值(会被前端渲染成"无量", 比不显示更糟)"""
    assert kpl.parse_market_volume_rt(bad) is None


# ---------- ② build_market_brief_payload 接入 ----------
def _stub_base(monkeypatch, vol_rt):
    """桩掉两个外网子函数 + 设置开关, 只留量能分支被测"""
    st_svc.set("market_vol_rt", vol_rt)
    monkeypatch.setattr(kpl, "fetch_market_breadth",
                        lambda: {"rise": 1, "fall": 1, "ts": 1, "day": "x", "yesterday": None})
    # 自算值: 9/11 收盘 19716.63 亿; 自存快照基准是 0 值脏点(早盘真实场景)
    monkeypatch.setattr(fetcher, "fetch_market_brief",
                        lambda *a, **k: {"stockCount": 5558, "amount": 19716.63, "date": "2026-09-11"})
    monkeypatch.setattr(fetcher, "get_same_time_yesterday",
                        lambda *a, **k: {"amount": 0.0, "stockCount": 5558, "ts": 1, "date": "2026-09-10"})


def test_build_vol_rt_on(monkeypatch):
    """开关开 + 取到值 → amount 与 last_same_time 双双被实时接口覆盖"""
    _stub_base(monkeypatch, 1)
    monkeypatch.setattr(kpl, "fetch_kpl_market_scln", lambda **k: FAKE_SCLN)
    d = kpl.build_market_brief_payload()
    assert d["market"]["amount"] == 19718.98
    assert d["market"]["volSrc"] == "kpl"
    assert d["market"]["volForecast"] == "19718亿(19.72%,增量3247亿)"
    # 关键: 基准从自存快照的 0 值脏点 → 昨日同一时点 5750 亿
    assert d["last_same_time"]["amount"] == 5750.00
    assert d["last_same_time"]["src"] == "kpl"
    # stockCount 仍沿用自算(实时接口不含只数)
    assert d["last_same_time"]["stockCount"] == 5558


def test_build_vol_rt_fallback(monkeypatch):
    """接口失败(返回 None) → 完全回退自算值, 行为与改动前一致"""
    _stub_base(monkeypatch, 1)
    monkeypatch.setattr(kpl, "fetch_kpl_market_scln", lambda **k: None)
    d = kpl.build_market_brief_payload()
    assert d["market"]["amount"] == 19716.63      # 自算值不变
    assert "volSrc" not in d["market"]
    assert d["last_same_time"]["amount"] == 0.0   # 原基准不变


def test_build_vol_rt_raise_fallback(monkeypatch):
    """接口抛异常(非 None 失败) → 同样回退, 不让异常冒到接口层"""
    _stub_base(monkeypatch, 1)
    def _boom(**k):
        raise RuntimeError("模拟网络异常")
    monkeypatch.setattr(kpl, "fetch_kpl_market_scln", _boom)
    d = kpl.build_market_brief_payload()
    assert d["market"]["amount"] == 19716.63
    assert "volSrc" not in d["market"]


def test_build_vol_rt_off(monkeypatch):
    """开关关(默认) → 根本不调实时接口, 一切照旧"""
    _stub_base(monkeypatch, 0)
    def _boom(**k):
        raise AssertionError("开关关闭时不应调用实时接口")
    monkeypatch.setattr(kpl, "fetch_kpl_market_scln", _boom)
    d = kpl.build_market_brief_payload()
    assert d["market"]["amount"] == 19716.63
    assert "volSrc" not in d["market"]
    assert d["last_same_time"]["amount"] == 0.0
