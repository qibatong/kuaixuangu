# -*- coding: utf-8 -*-
"""KPL 回看预热窗口（2026-09-28 收敛为 08:30~20:00 + 竞价时段跳过）

为什么专门盯这个谓词：它是**唯一**决定「收盘后/夜里是否还在打上游」的开关。
2026-09-28 生产实测：22:58 巡检时发现 20:58/21:18/21:38/21:58/22:18/22:38/22:58
每 20 分钟仍跑一轮（`KPL回看预热完成 5/5 天`）—— 而回看数据是**历史不可变**的，
夜里无人访问 ⇒ 每轮预热出的"热"在下一个用户到来前必然过期，纯属重复打上游。
收敛成服务窗口后，必须把边界钉死，否则"夜里又偷偷开始刷"不会有人发现。
"""
import calendar

from app.services import kpl


def _ts_cst(y, mo, d, hh, mm):
    """按**北京时间**构造 ts（谓词内部 +8h 还原，故这里整体减 8h）"""
    return calendar.timegm((y, mo, d, hh, mm, 0, 0, 0, 0)) - 8 * 3600


def test_window_boundaries():
    """窗口 08:30~20:00（两端含）：窗内可预热，窗外一律不打上游"""
    # 窗口内
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 8, 30)) is True     # 左端点(含)
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 10, 0)) is True
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 15, 0)) is True     # 收盘后仍在窗内
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 20, 0)) is True     # 右端点(含)
    # 窗口外 ⇒ 夜间不再打上游
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 8, 29)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 20, 1)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 22, 58)) is False   # ← 实测踩到的时刻
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 23, 59)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 29, 0, 0)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 29, 3, 0)) is False


def test_auction_skip_kept_inside_window():
    """窗口内的竞价时段仍跳过（上游此时最紧张）—— 收敛窗口不得顺手把它丢了"""
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 9, 5)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 9, 20)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 9, 40)) is False
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 9, 4)) is True
    assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, 9, 41)) is True


def test_window_is_beijing_time_not_server_local():
    """按**北京时间**判定（服务器 TZ 无关）：直接给 UTC 时间戳也得同一结论"""
    # 2026-09-28 15:00 UTC = 23:00 北京 ⇒ 夜 => False
    assert kpl.kpl_replay_prewarm_active(calendar.timegm((2026, 9, 28, 15, 0, 0, 0, 0, 0))) is False
    # 2026-09-28 00:30 UTC = 08:30 北京 ⇒ 窗口左端点 => True
    assert kpl.kpl_replay_prewarm_active(calendar.timegm((2026, 9, 28, 0, 30, 0, 0, 0, 0))) is True


def test_weight_of_the_change_is_bounded():
    """把"收益/代价"钉成断言，防止有人日后随手放大窗口：
    夜间(20:00~08:30) 12.5 小时必须全 False ⇒ 每天最多约 11.5 小时内按 20min 一轮。"""
    for hh in list(range(0, 9)) + list(range(21, 24)):
        assert kpl.kpl_replay_prewarm_active(_ts_cst(2026, 9, 28, hh, 15)) is False, hh


# ============================================================================
# 2026-10-01 竞价链路 P2-7（清单 1.5）: 预热范围**可配**
#   动机: 主人想回看"更早的历史日", 而原先固定 5 天 ⇒ 更早的日期首次访问冷取数 4.8~7.4s。
#   风险面: 每天 = 4 个全市场猫爪接口 + 1.2s, 无上限会吃配额/拉长每轮 ⇒ 必须夹取。
# ============================================================================
from app.services import settings as _settings   # noqa: E402

_DAYS_KEY = kpl.KPL_REPLAY_DAYS_KEY


def _set_days(v):
    _settings.set(_DAYS_KEY, v)


def test_replay_days_default_is_five():
    """默认 5(与旧行为完全一致 —— 不配就是原样, 避免"改了配置才发现出网翻倍")"""
    _set_days(kpl._KPL_REPLAY_DAYS)
    assert kpl.kpl_replay_days() == 5
    assert kpl._KPL_REPLAY_DAYS == 5


def test_replay_days_configurable():
    """可放大: 主人要回看更早的历史日时, 改 settings 即可(无需重启)"""
    try:
        _set_days(10)
        assert kpl.kpl_replay_days() == 10
        _set_days(20)
        assert kpl.kpl_replay_days() == 20
    finally:
        _set_days(kpl._KPL_REPLAY_DAYS)


def test_replay_days_is_clamped_on_both_ends():
    """夹取(照本仓既有范式 max(1, min(MAX, n))): 0/负数→1, 超大→20, 非数字→默认 5

    🔴 这条是**安全阀**: 配錯的值绝不能把上游出网量放大到失控。
    """
    cases = ((0, 1), (-3, 1), (1, 1), (999, kpl._KPL_REPLAY_DAYS_MAX),
             (21, kpl._KPL_REPLAY_DAYS_MAX), ("abc", 5), (None, 5), (3.9, 3))
    try:
        for bad, want in cases:
            _set_days(bad)
            assert kpl.kpl_replay_days() == want, "输入 %r 应为 %d, 实际 %d" % (
                bad, want, kpl.kpl_replay_days())
    finally:
        _set_days(kpl._KPL_REPLAY_DAYS)


def test_replay_dates_uses_settings_and_is_descending_unique():
    """`_kpl_replay_dates()` 缺省走 settings; 返回**降序且不重复**的真实交易日"""
    try:
        _set_days(3)
        ds = kpl._kpl_replay_dates()
        assert len(ds) == 3, ds
        assert ds == sorted(ds, reverse=True), ds
        assert len(set(ds)) == 3, ds
        # 显式传参仍优先(供测试/调试), 不读 settings
        assert len(kpl._kpl_replay_dates(2)) == 2
    finally:
        _set_days(kpl._KPL_REPLAY_DAYS)


def test_cost_bound_of_max_days():
    """把"收益/代价"钉成断言: 即使配到上限, 出网量也远低于猫爪日配额(80000)

    每轮 ≈ 天数 × 4 次调用(猫爪 auc_kp/daily_auc_detail/daily_auc/screening),
    每天 24h/20min = 72 轮。
    """
    rounds_per_day = 24 * 3600 // kpl._KPL_REPLAY_PERIOD
    assert kpl._KPL_REPLAY_DAYS_MAX * 4 * rounds_per_day < 80000
