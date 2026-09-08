# -*- coding: utf-8 -*-
"""模式层单测 (重构 P0) — 消灭散落时间硬编码后, 时段分派必须一次定义处处正确"""
from datetime import datetime, timedelta, timezone

from app.services.picker import mode as pm

BJ = timezone(timedelta(hours=8))


def ts(y, m, d, hh, mm):
    """构造北京时间对应时间戳(服务器时区无关)"""
    return datetime(y, m, d, hh, mm, tzinfo=BJ).timestamp()


# 2026-09-08 = 周二, 09-12 = 周六, 09-13 = 周日
def test_preopen_before_915():
    assert pm.resolve_mode(ts(2026, 9, 8, 8, 0)).mode is pm.PickMode.PREOPEN
    assert pm.resolve_mode(ts(2026, 9, 8, 0, 30)).mode is pm.PickMode.PREOPEN


def test_auction_window_915_to_925():
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 15)).mode is pm.PickMode.AUCTION
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 20)).mode is pm.PickMode.AUCTION
    # 边界: 9:14:59 仍属盘前
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 14)).mode is pm.PickMode.PREOPEN


def test_locked_925_to_930():
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 25)).mode is pm.PickMode.LOCKED
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 27)).mode is pm.PickMode.LOCKED
    # 边界: 9:24 仍属竞价窗口
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 24)).mode is pm.PickMode.AUCTION


def test_intraday_930_to_1500():
    assert pm.resolve_mode(ts(2026, 9, 8, 9, 30)).mode is pm.PickMode.INTRADAY
    assert pm.resolve_mode(ts(2026, 9, 8, 14, 59)).mode is pm.PickMode.INTRADAY


def test_closed_after_1500():
    assert pm.resolve_mode(ts(2026, 9, 8, 15, 0)).mode is pm.PickMode.CLOSED
    assert pm.resolve_mode(ts(2026, 9, 8, 23, 59)).mode is pm.PickMode.CLOSED


def test_weekend_always_closed():
    """周末任何时点都是闭市回放(老逻辑同样跳过周末采集)"""
    assert pm.resolve_mode(ts(2026, 9, 12, 10, 0)).mode is pm.PickMode.CLOSED   # 周六
    assert pm.resolve_mode(ts(2026, 9, 13, 9, 20)).mode is pm.PickMode.CLOSED   # 周日


def test_holiday_hook_reserved():
    """节假日日历预留: 传入即生效(当前老代码无此能力, 属已知缺口补全)"""
    hol = {"2026-10-01"}
    assert pm.resolve_mode(ts(2026, 10, 1, 10, 0)).mode is pm.PickMode.INTRADAY
    assert pm.resolve_mode(ts(2026, 10, 1, 10, 0), holidays=hol).mode is pm.PickMode.CLOSED


def test_policy_semantics():
    """模式语义: 幂等 / 可锁 / 竞价窗口 — 决定取数与落库行为"""
    assert pm.POLICIES[pm.PickMode.PREOPEN].deterministic is True
    assert pm.POLICIES[pm.PickMode.LOCKED].deterministic is True
    assert pm.POLICIES[pm.PickMode.LOCKED].allow_lock is True      # 仅锁定期可落库
    assert pm.POLICIES[pm.PickMode.PREOPEN].allow_lock is False
    # 只有竞价窗口允许读实时 f615/f616
    assert pm.POLICIES[pm.PickMode.AUCTION].auction_window is True
    assert pm.POLICIES[pm.PickMode.INTRADAY].auction_window is False
    assert pm.POLICIES[pm.PickMode.LOCKED].auction_window is False


def test_only_auction_allows_list_to_change():
    """2026-09-08 主人拍板: 竞价结束后名单即定型。
    盘中/收盘/盘前一律幂等; 只有竞价窗口(数据在变)允许名单变化。"""
    for m in (pm.PickMode.PREOPEN, pm.PickMode.LOCKED,
              pm.PickMode.INTRADAY, pm.PickMode.CLOSED):
        assert pm.POLICIES[m].deterministic is True, \
            "%s 必须幂等(同条件必同名单)" % m.value
    assert pm.POLICIES[pm.PickMode.AUCTION].deterministic is False


def test_no_relock_after_930():
    """9:30 后禁止重新选股(原始版本 reLockData 同规则: '9:30后禁止重新选股')"""
    assert pm.POLICIES[pm.PickMode.INTRADAY].allow_relock is False
    assert pm.POLICIES[pm.PickMode.CLOSED].allow_relock is False
    # 9:30 前允许改条件重选
    assert pm.POLICIES[pm.PickMode.PREOPEN].allow_relock is True
    assert pm.POLICIES[pm.PickMode.LOCKED].allow_relock is True


def test_realtime_patch_only_display_fields():
    """盘中可补展示字段, 但不得重算名单(幂等) — 与 source_priority 定格优先配套。
    原始版本 updateRealTimeOnly() 即: 只更新已入选票 realChange/entityChange。
    """
    intraday = pm.POLICIES[pm.PickMode.INTRADAY]
    assert intraday.realtime_patch is True
    assert intraday.source_priority[0] == "snapshot", "名单必须优先认定格快照"
    # 竞价窗口: 定格前无快照可用, 以实时为准
    # 竞价窗口无当日定格快照 → 名单只能来自实时全市场(点查源此时无候选 codes)
    assert pm.POLICIES[pm.PickMode.AUCTION].source_priority[0] == "eastmoney_market"
    # 盘前: 实时源返回最近交易日收盘定格, 全天恒定, 补它可填现价/实体/异动列
    # 且不影响名单幂等(名单仍由 snapshot 决定)
    # 2026-09-09 主人反馈 9/9 0:37 盘前看昨收, 现涨/实体/异动/抢筹/奖牌区全空
    preopen = pm.POLICIES[pm.PickMode.PREOPEN]
    assert preopen.realtime_patch is True, "盘前必须允许补丁源补展示字段"
    assert preopen.source_priority[0] == "snapshot", "名单必须优先认定格快照"
    assert preopen.deterministic is True
    # 收盘后无需刷新展示字段
    # 收盘后补丁源返回的是**收盘定格值**(不再变化) → 补它不破坏幂等,
    # 且必须补, 否则现价/现涨列全空(P3 测试机实测)
    assert pm.POLICIES[pm.PickMode.CLOSED].realtime_patch is True


def test_every_mode_has_fail_message():
    """铁律2: 每个模式都必须有失败明示文案, 不允许静默降级"""
    for m, p in pm.POLICIES.items():
        assert p.fail_message, "模式 %s 缺少失败提示文案" % m.value
        assert p.source_priority, "模式 %s 缺少数据源优先级" % m.value


def test_trading_day_and_date():
    assert pm.is_trading_day(ts(2026, 9, 8, 10, 0)) is True     # 周二
    assert pm.is_trading_day(ts(2026, 9, 12, 10, 0)) is False   # 周六
    assert pm.bj_date(ts(2026, 9, 8, 23, 30)) == "2026-09-08"


def test_accepts_datetime_and_timestamp_equally():
    """2026-09-08 测试机部署实锤: 原 resolve_mode 只吃 float, 传 datetime 会在
    `now + 8*3600` 抛 TypeError。业务代码 datetime.now() 直传是高频写法 →
    两种入参必须完全等价(含跨时区: naive/UTC+8/UTC 三种 datetime 同解)。"""
    cases = [
        (2026, 9, 8, 8, 0, pm.PickMode.PREOPEN),
        (2026, 9, 8, 9, 20, pm.PickMode.AUCTION),
        (2026, 9, 8, 9, 27, pm.PickMode.LOCKED),
        (2026, 9, 8, 10, 0, pm.PickMode.INTRADAY),
        (2026, 9, 8, 16, 0, pm.PickMode.CLOSED),
        (2026, 9, 12, 10, 0, pm.PickMode.CLOSED),   # 周六
    ]
    for y, m, d, hh, mm, expect in cases:
        stamp = ts(y, m, d, hh, mm)
        assert pm.resolve_mode(stamp).mode is expect, "时间戳入参 %s 解析错误" % (expect,)

        # 带时区 datetime(UTC+8)
        dt_bj = datetime(y, m, d, hh, mm, tzinfo=BJ)
        assert pm.resolve_mode(dt_bj).mode is expect, "datetime(UTC+8) 入参 %s 解析错误" % (expect,)

        # naive datetime(无时区, 按北京时间理解)
        dt_naive = datetime(y, m, d, hh, mm)
        assert pm.resolve_mode(dt_naive).mode is expect, "naive datetime 入参 %s 解析错误" % (expect,)

        # UTC 时区 datetime(同一时刻)
        dt_utc = dt_bj.astimezone(timezone.utc)
        assert pm.resolve_mode(dt_utc).mode is expect, "datetime(UTC) 入参 %s 解析错误" % (expect,)

        # 辅助函数同样兼容
        assert pm.is_trading_day(dt_bj) == pm.is_trading_day(stamp)
        assert pm.bj_date(dt_bj) == pm.bj_date(stamp) == "%04d-%02d-%02d" % (y, m, d)


def test_none_now_does_not_raise():
    """now=None 走当前时间, 不得抛异常(线上主路径)"""
    p = pm.resolve_mode()
    assert p.mode in set(pm.PickMode)
    assert isinstance(pm.bj_date(), str) and len(pm.bj_date()) == 10
