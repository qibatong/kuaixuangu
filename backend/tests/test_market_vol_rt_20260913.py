# -*- coding: utf-8 -*-
"""两市量能改取开盘啦实时接口(a=MarketSCLN) —— 2026-09-13 新增 / 2026-09-14 语义纠正

背景(主人反馈"相比昨日永远比的昨日总成交, 不是同一时点"): 首页「两市资金 /
较昨日同时」原依赖 worker 自存快照 market_brief_intraday_*, 有两个硬伤 ——
  ① 09:30 存在 amount=0 脏点 → 早盘匹配到它后增量虚高 7.7 倍
     (9/11 10:00 显示 +6631 亿, 正确应为 9/10 10:00 的 5750 亿 → +857 亿)
  ② 东财分页失败即**静默少算**(9/8 少 1491 亿 = -7.6%), 基准侧与今日侧都可能失真。
实时接口一次请求即给「今日此刻 + 昨日同一时点」, 两侧同源同口径。

🔴 2026-09-14 纠正: 9/13 初版把字段**判反**了 ——
  `s_zrtj` 是昨日**全天**(不是同期), `s_zrcs` 才是昨日**同一时点**。
  根因: 9/13 在**周日**验证, 收盘后"同期"退化为"全天", 两字段完全相等 → 看不出破绽。
  本文件 fixture 已改用 **9/14 11:05 盘中真实返回**(两字段可区分), 并加语义方向断言防回归。

开关: settings market_vol_rt(默认 0=关, 1=开); 取不到时**完全回退**, 不改行为。
"""
import pytest

from app.services import kpl, fetcher
from app.services import settings as st_svc


# 2026-09-14 11:05 生产实测形状的桩数据(单位: 万元; 各值取自真实接口返回)
#   last=10214.41亿(今日此刻) / s_zrcs=11660.49亿(昨日同时点) / s_zrtj=19718.98亿(昨日全天)
FAKE_SCLN = {"info": {
    "last": "102144082",        # 今日此刻         → 10214.41 亿 (== trends 末[1])
    "s_zrcs": "116604857",      # ★昨日**同一时点** → 11660.49 亿 (== trends 末[2])
    "s_zrtj": "197189848",      # 昨日**全天**      → 19718.98 亿 (= 9/11 全天实测 19716.63)
    "s3_zrtj": "182488431",     # 前3日**全天均值**  → 18248.84 亿 (≠ 同期)
    "ycln": "16957亿",
    "yclnstr": "16957亿(-14%,缩量2761亿)",
    "csbl": -14, "color": "2", "time": 1789355099,
    "trends": [
        ["09:30", "1645452", "1794187", "1670202", "-8.83", "17978亿(-8.83%,缩量1740亿)", "2", "2"],
        ["11:05", "102144082", "116604857", "109923253", "-14", "16957亿(-14%,缩量2761亿)", "2", "2"],
    ],
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
    assert v["amount"] == 10214.41
    # 🔴 防回归(9/14 纠正): 同期取 s_zrcs, 全天取 s_zrtj —— 9/13 曾经反着取
    assert v["prev_same_time"] == 11660.49    # s_zrcs = 昨日**同一时点**
    assert v["prev_full"] == 19718.98         # s_zrtj = 昨日**全天**
    assert v["prev3_same_time"] == 10992.33   # trends 末行[3] = 前3日**同期**
    assert v["forecast"] == "16957亿"
    assert v["forecast_str"] == "16957亿(-14%,缩量2761亿)"
    assert v["ts"] == 1789355099


def test_parse_vol_rt_same_time_lt_full():
    """语义方向铁律: 盘中「同一时点累计」必须 < 「昨日全天」(9/13 判反正是违反了这一条)"""
    v = kpl.parse_market_volume_rt(FAKE_SCLN)
    assert v["prev_same_time"] < v["prev_full"]


def test_parse_vol_rt_swapped_guard():
    """自检: 若字段语义再度漂移(同期 > 全天) → 丢弃同期值并告警, 不把荒谬基准喂给前端"""
    bad = {"info": {"last": "1000000", "s_zrcs": "50000000", "s_zrtj": "30000000"}}
    v = kpl.parse_market_volume_rt(bad)
    assert v is not None
    assert v["prev_same_time"] is None     # 异常同期值被丢弃
    assert v["prev_full"] == 3000.00       # 全天值照常给出


def test_parse_vol_rt_no_trends():
    """trends 缺失/空 → prev3_same_time 为 None(而非 0), 其余字段不受影响"""
    d = {"info": {"last": "1000000", "s_zrcs": "600000", "s_zrtj": "900000", "trends": []}}
    v = kpl.parse_market_volume_rt(d)
    assert v["prev3_same_time"] is None
    assert v["amount"] == 100.00 and v["prev_same_time"] == 60.00 and v["prev_full"] == 90.00


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
    assert d["market"]["amount"] == 10214.41
    assert d["market"]["volSrc"] == "kpl"
    assert d["market"]["volForecast"] == "16957亿(-14%,缩量2761亿)"
    # 关键: 基准从自存快照的 0 值脏点 → 昨日**同一时点** 11660.49 亿
    # (9/13 判反时这里是 19718.98 = 昨日全天 → 前端显示"缩量 9505 亿", 虚高 6.6 倍)
    assert d["last_same_time"]["amount"] == 11660.49
    assert d["last_same_time"]["src"] == "kpl"
    # stockCount 仍沿用自算(实时接口不含只数)
    assert d["last_same_time"]["stockCount"] == 5558
    # 端到端: 前端 diffAmt = 今日 − 昨日同时点 → 小幅缩量(而非 -9505 亿的荒谬值)
    assert d["market"]["amount"] - d["last_same_time"]["amount"] == pytest.approx(-1446.08, abs=0.01)


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
