# -*- coding: utf-8 -*-
"""两市概况收盘快照的**交易日守卫**(写前 + 读侧) —— 2026-09-26 (v4.11.57) 新增

背景
----
`settings.market_brief_last` 存的是「上一交易日**收盘全天**」两市成交额, 被前端当作
"较昨日全天"的对比基准。但 15:30 收盘快照落库后它会变成**今日**收盘 ⇒ 与今日自比恒 0
(前端显示"放量 0 亿")。2026-09-07 因此加了一条: 此时改用 `market_brief_prev`。

🔴 原判据是「`last.date` == **字面今天**」—— 只在"写入侧恰好只于交易日 15:30 落库"
这一前提下才成立, 属**靠巧合正确**:

  ① 它用系统时钟的"今天", 与"今天是不是交易日"完全无关;
  ② 写入侧此前**没有"日期必须是今日"的写前守卫**, 而行情源在收盘定格尚未生成时会返回
     **上一交易日的复制行**。2026-09-25(中秋 · 星期五)正是这类残值被写成 settings 键
     (`market_brief_*` 2 个 + `kv_cache` 42 个, 见 v4.11.53 复盘)。这种**非交易日**日期
     与"今天"永不相等 ⇒ 脏值会被长期当作"上一交易日全天"喂给前端, 且**不会自愈**。

修法两处成对(只做一边等于没做 —— 与 v4.11.53「读侧比对 + 写侧确认」同一教训)
------------------------------------------------------------------------
写侧 `auction_snapshot._brief_date_ok()`: brief.date 必须 == 今日, 否则拒写 + 窗口内重试;
读侧 `kpl._mb_baseline_is_today()`: 显式表达「①今日是交易日 ∧ ②已过 15:30 ∧ ③last 确为今日」。

本文件钉死两侧判据。**时间全部注入**(`now_ts` 参数 / `_ts()`) ⇒ 任何一天跑都稳定,
不会像"用真实今天造数据"的用例那样周末必红。
"""
import calendar
import logging
import time

import pytest

from app.services import auction_snapshot, fetcher, kpl
from app.services import settings as st_svc

# ---- 2026 年真实日历锚点(与 app/core/trade_calendar.HOLIDAYS_2026 一致) ----
D0923 = "2026-09-23"   # 星期三 · 交易日
D0924 = "2026-09-24"   # 星期四 · 交易日
D0925 = "2026-09-25"   # 星期五 · ★ 中秋节 · **法定休市**(裸 weekday 判定会误当交易日)
D0926 = "2026-09-26"   # 星期六


def _ts(bj: str) -> float:
    """北京时间 "YYYY-MM-DD HH:MM" → Unix 时间戳(生产同一换算: UTC+8)。"""
    return calendar.timegm(time.strptime(bj, "%Y-%m-%d %H:%M")) - 8 * 3600


@pytest.fixture(autouse=True)
def _reset_warned():
    """告警去重是模块级状态 ⇒ 用例间必须复位, 否则顺序依赖(本项目 session 顺序敏感)"""
    kpl._warned_mb_date = None
    yield
    kpl._warned_mb_date = None


# ============================================================================
# ① 读侧判据 kpl._mb_baseline_is_today(last, now_ts)
# ============================================================================
def test_切换_交易日收盘快照已落库():
    """交易日 15:30 后且 last.date 就是今日 ⇒ last 已被今日收盘覆盖, 该改用 prev"""
    assert kpl._mb_baseline_is_today({"date": D0924}, _ts("2026-09-24 15:31")) is True


def test_切换_边界_恰好15点30分():
    """写入窗口起点 15:30 含在内(与 _MB_CLOSE_SEC 同值)"""
    assert kpl._mb_baseline_is_today({"date": D0924}, _ts("2026-09-24 15:30")) is True


def test_不切换_边界_15点29分():
    """条件②: 快照时刻之前不切 —— 此时 last 即便标着今日也不可信"""
    assert kpl._mb_baseline_is_today({"date": D0924}, _ts("2026-09-24 15:29")) is False


def test_不切换_周六():
    """条件①: 非交易日不存在"今日收盘"(裸"字面今天"判据在这里不会命中, 但语义从未显式)"""
    assert kpl._mb_baseline_is_today({"date": D0926}, _ts("2026-09-26 15:31")) is False


def test_不切换_法定休市日():
    """🔴 2026-09-25 中秋(星期五): 必须走**节假日日历**, 裸 weekday 会当成交易日"""
    assert kpl._mb_baseline_is_today({"date": D0925}, _ts("2026-09-25 15:31")) is False


def test_不切换_last仍是上一交易日():
    """条件③: last.date 是上一交易日 ⇒ 不得误当今日收盘(否则"较昨日全天"跳过一天)"""
    assert kpl._mb_baseline_is_today({"date": D0923}, _ts("2026-09-24 15:31")) is False


@pytest.mark.parametrize("last", [None, {}, {"date": None}, {"date": ""}, {"amount": 1.0}])
def test_不切换_last缺失或无日期(last):
    assert kpl._mb_baseline_is_today(last, _ts("2026-09-24 15:31")) is False


def test_脏日期绝不抛异常():
    """库里的日期串可能是任何形态(历史脏值)。绝不能让异常冒到调用方 —— 那会被
    `except Exception: last = None` 吞掉, 前端"放量"整块消失(比显示旧值更糟)。"""
    assert kpl._mb_baseline_is_today({"date": "2026-99-99"}, _ts("2026-09-24 15:31")) is False
    assert kpl._mb_baseline_is_today({"date": "不是日期"}, _ts("2026-09-24 15:31")) is False
    kpl._mb_warn_if_stale({"date": "2026-99-99"})          # 不抛即通过


def test_不可判定日期保守放行():
    """`is_trade_day` 对无法识别的日期会抛 ValueError → 包装层必须吞掉并放行"""
    assert kpl._mb_is_trade_day(D0924) is True
    assert kpl._mb_is_trade_day(D0925) is False
    assert kpl._mb_is_trade_day("2026-99-99") is True      # 不可判定 ⇒ 不阻断既有行为


# ---------------------------------------------------------------- 脏值告警(可见降级)
def test_告警_日期非交易日(caplog):
    with caplog.at_level(logging.WARNING, logger=kpl.__name__):
        kpl._mb_warn_if_stale({"date": D0925})
    assert [r for r in caplog.records if "非交易日" in str(r.getMessage())]


def test_告警_同日只报一次(caplog):
    """每日一次 —— 否则每个 TTL 到期重算都刷一行, 日志被埋"""
    with caplog.at_level(logging.WARNING, logger=kpl.__name__):
        kpl._mb_warn_if_stale({"date": D0925})
        kpl._mb_warn_if_stale({"date": D0925})
        kpl._mb_warn_if_stale({"date": D0925})
    assert len([r for r in caplog.records if "非交易日" in str(r.getMessage())]) == 1


def test_不告警_正常交易日(caplog):
    with caplog.at_level(logging.WARNING, logger=kpl.__name__):
        kpl._mb_warn_if_stale({"date": D0924})
        kpl._mb_warn_if_stale(None)
    assert not [r for r in caplog.records if "非交易日" in str(r.getMessage())]


# ============================================================================
# ② 写侧守卫 auction_snapshot._brief_date_ok(brief, date)
# ============================================================================
def test_写前守卫_日期是今日才放行():
    assert auction_snapshot._brief_date_ok(
        {"date": D0924, "stockCount": 5561, "amount": 16533.57}, D0924) is True


def test_写前守卫_拒绝上一交易日复制行():
    """🔴 核心用例: 源在"收盘定格尚未生成"时返回昨日行 → 绝不落库。
    否则 last 被写成非今日日期, 读侧"是否等于今天"永不成立 ⇒ 基准长期错位且不自愈。"""
    assert auction_snapshot._brief_date_ok({"date": D0923, "amount": 15800.0}, D0924) is False


@pytest.mark.parametrize("brief", [None, {}, {"date": None}, {"date": ""}, {"amount": 1.0}])
def test_写前守卫_缺日期即拒(brief):
    assert auction_snapshot._brief_date_ok(brief, D0924) is False


def test_写侧两层守卫各管一段():
    """两层缺一不可:
      ① `_is_trade_day` 挡住"今天根本不是交易日"(2026-09-25 中秋事故的根因);
      ② `_brief_date_ok` 挡住"今天是交易日、但源给的是上一交易日的行"。
    补上 ① 之后 ② 仍然必要 —— 交易日收盘定格未生成时源照样能给昨日行。"""
    assert auction_snapshot._is_trade_day(time.gmtime(_ts("2026-09-25 15:31") + 8 * 3600)) is False
    assert auction_snapshot._is_trade_day(time.gmtime(_ts("2026-09-24 15:31") + 8 * 3600)) is True
    assert auction_snapshot._brief_date_ok({"date": D0923}, D0924) is False
    assert auction_snapshot._brief_date_ok({"date": D0924}, D0924) is True


# ============================================================================
# ③ 读侧接线: build_market_brief_payload 是否真的按判据换用 prev
#    (判据本身已由 ① 节钉死; 这里只验"接线有没有接上")
# ============================================================================
LAST = {"date": D0924, "stockCount": 5561, "amount": 16533.57}
PREV = {"date": D0923, "stockCount": 5560, "amount": 15800.12}


@pytest.fixture
def _brief_settings():
    """落一份 last/prev 进 settings, 用例结束还原。

    settings 服务只有 get/set、**没有 delete**, 故只能回写原值; 原本无键时回写
    JSON `null` —— 在读取侧(settings.get 返回 None / 直读 SQL 后 json.loads("null"))
    与"无键"完全等价。"""
    old = {k: st_svc.get(k) for k in
           ("market_brief_last", "market_brief_prev", "market_vol_rt")}
    st_svc.set("market_vol_rt", 0)          # 关掉实时量能分支 ⇒ 零网络
    st_svc.set("market_brief_last", LAST)
    st_svc.set("market_brief_prev", PREV)
    yield
    for k, v in old.items():
        st_svc.set(k, v)


def _stub_net(monkeypatch):
    """把 payload 里的三个外网子函数全部桩掉, 只留 last 基准分支被测"""
    monkeypatch.setattr(kpl, "fetch_market_breadth",
                        lambda: {"rise": 1, "fall": 1, "ts": 1, "day": "x", "yesterday": None})
    monkeypatch.setattr(fetcher, "fetch_market_brief",
                        lambda *a, **k: dict(LAST))
    monkeypatch.setattr(fetcher, "get_same_time_yesterday", lambda *a, **k: None)


def test_接线_收盘后改用prev(monkeypatch, _brief_settings):
    _stub_net(monkeypatch)
    monkeypatch.setattr(kpl, "_mb_baseline_is_today", lambda last, now_ts=None: True)
    d = kpl.build_market_brief_payload()
    assert d["last"] == PREV, "判据成立时 last 必须换成 prev(上一交易日全天)"


def test_接线_非收盘时刻保持last(monkeypatch, _brief_settings):
    _stub_net(monkeypatch)
    monkeypatch.setattr(kpl, "_mb_baseline_is_today", lambda last, now_ts=None: False)
    d = kpl.build_market_brief_payload()
    assert d["last"] == LAST, "判据不成立时必须原样保留 last"
