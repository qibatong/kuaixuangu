# -*- coding: utf-8 -*-
"""9:25 定格推迟到「拿到猫爪数据再定格」+ 竞价量比接入(2026-09-24 主人拍板)。

背景(两条都是实测, 不是推测):
  ① 猫爪竞价字段(daily_auc 的 auc_vol_ratio / fundflow_kp 的竞价净额)**09:25:35 起才产出**,
     而旧定格枪打在 09:25:20~49 ⇒ auc_vol_ratio 恒 0(2026-09-18~24 全库 4 时点复现),
     异动因子的量比层于是静默回退旧口径「今 9:25 额 ÷ 昨 9:25 额」。
  ② 更隐蔽的**串日**: 不传 date(或 date_offset=0)时, 目标日该分钟尚未产出, 上游会返回
     "最近可用"那份 —— 2026-09-24 09:15 取 trademin=0925 拿到的是 **09-23** 的 9:25(5567 行)。
     若把"有 5567 行"当成就绪判据, 早盘每一枪都会把昨日量比写成今日定格。

本文件覆盖:
  A. 时间常量: 窗口/下限/重采截止(契约推导)与不变式;
  B. 就绪判定 meoz_bid_ready: 串日必须判未就绪、当日非零达标才判就绪;
  C. 补采 refill_bid_vol_ratio: 只写真非零、绝不覆盖、串日不回填、幂等;
  D. 源码级守卫: 定格首采门槛限定在 9:25 那一分钟(否则 9:26 重采窗口被切碎);
     重采必须走 _bid25_retry_open(单位口径单一入口, 禁止内联减 9*3600 回流);
     落库必须是幂等 upsert(重采真的会触发, 写重行会被下游全盘放大);
  E. 关联链路: aipick 采集/预测的**定格就绪门** —— 落库时刻(09:25:5x~09:26:1x)与
     aipick 窗口起点(09:26:00)重叠, 不设门约五成概率抢跑, collector 会静默回退自拉。
"""
import inspect
import time

from app.db import database
from app.services import auction_snapshot as A
from app.services import contracts, meoz_client

# 冷门日期: 避免与其他用例写入的 (date, time_point) 行互相干扰
D1 = "2026-11-16"
D2 = "2026-11-17"
PT = "9_25"


def _seed(date, rows):
    """rows = [(code, auc_vol_ratio), ...]"""
    conn = database.get_conn()
    try:
        for code, vr in rows:
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_bid "
                "(date, time_point, code, bid_change, bid_amt, ts, auc_vol_ratio) "
                "VALUES (?,?,?,?,?,?,?)",
                (date, PT, code, 1.0, 100.0, int(time.time()), vr))
        conn.commit()
    finally:
        conn.close()


def _read_vr(date, code):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT auc_vol_ratio FROM snapshot_bid "
            "WHERE date=? AND time_point=? AND code=?", (date, PT, code)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


# ------------------------------------------------------------------ A 时间常量
def test_bid25_window_covers_meoz_publish_lag():
    """窗口末端必须给足"等猫爪"的余量 —— 猫爪实测 09:26:16 才出满。"""
    _, end = A.TIME_POINTS["9_25"]
    assert end >= 9 * 60 + 27, "9_25 窗口末端应 >= 9:27(留出猫爪就绪重采轮次)"


def test_bid25_min_sec_raised_above_meoz_floor():
    """首采下限 >= 45s: 09:25:20 首采必然扑空(猫爪 09:25:35 才产出), 白烧一轮全市场拉取。"""
    assert A._BID25_MIN_SEC >= 45


def test_bid25_retry_until_derived_from_contract():
    """重采截止由 auc_vol_ratio 契约 ready_after 推导, 且必须落在窗口内。

    🔴 全程用**当日绝对秒**(hm*60+sec)这一把尺子, 与 _bid25_retry_open 同口径 ——
       若一侧额外减 9*3600, 断言会因为**量级差 9*3600 而恒真**, 等于没测(本文件初版
       就踩了这个坑, 是它放过了生产代码里的单位 bug)。
    """
    c = contracts.field("auc_vol_ratio")
    h, m, s = (int(x) for x in c.ready_after.split(":"))
    ready_sec = h * 3600 + m * 60 + s                              # 09:25:35 = 33935
    assert A._BID25_RETRY_UNTIL == ready_sec + 115                 # 09:27:30 = 34050
    _, end = A.TIME_POINTS["9_25"]
    win_end_sec = end * 60 + 59                                    # 9:27:59 = 34079
    # 不变式①: 截止必须 <= 窗口末端, 否则重采分支根本不会被触发(静默失效)
    assert A._BID25_RETRY_UNTIL <= win_end_sec
    # 不变式②: 且必须晚于上游就绪(否则"等了个寂寞")
    assert A._BID25_RETRY_UNTIL > ready_sec


def test_bid25_retry_until_is_absolute_second():
    """★ 单位铁律: 截止值必须是**当日绝对秒**(含 9*3600), 不是"9 点后秒数"。

    2026-09-19 的内联实现把右侧减了 9*3600, 与左侧(当日绝对秒)口径相反 →
    条件恒 False, 定格重采静默失效近一周。这条断言把量级钉死, 同类错误改不动。
    """
    assert A._BID25_RETRY_UNTIL > 9 * 3600, \
        "截止值漏了 9*3600(口径退回'9 点后秒数'), 与 hm*60+sec 比较会恒 False"


def test_bid25_retry_open_boundaries():
    """_bid25_retry_open 边界: 9:25:45~9:27:29 可重采, 9:27:30 起接受当前值。"""
    assert A._bid25_retry_open(9 * 60 + 25, 0) is True      # 首采下限前: 也须在"可重采"侧
    assert A._bid25_retry_open(9 * 60 + 25, 45) is True     # 首采下限(33945)
    assert A._bid25_retry_open(9 * 60 + 26, 16) is True     # 猫爪产出上限附近(33976) ★ 等猫爪的意义
    assert A._bid25_retry_open(9 * 60 + 27, 29) is True     # 截止前一秒(34049)
    assert A._bid25_retry_open(9 * 60 + 27, 30) is False    # 截止当秒(34050)
    assert A._bid25_retry_open(9 * 60 + 27, 59) is False    # 窗口末端(34079)


def test_vr_ready_threshold_matches_contract():
    """就绪阈值与契约 probe.min 必须一致 —— 防两处漂移(注释不会报错, 断言会)。"""
    c = contracts.field("auc_vol_ratio")
    assert c.probe is not None
    assert abs(A._VR_READY_MIN - c.probe.min) < 1e-9
    assert A._VR_READY_MIN_N == int(round(c.probe.min * A._MARKET_SIZE_MEDIAN))


# ------------------------------------------------------------------ B 就绪判定
def _patch_meoz(monkeypatch, payload, enabled=True):
    monkeypatch.setattr(meoz_client, "enabled", lambda: enabled)
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **kw: payload)


def test_meoz_bid_ready_false_on_empty(monkeypatch):
    """上游返回 0 行(未产出/抖动) → 未就绪"""
    _patch_meoz(monkeypatch, {})
    assert A.meoz_bid_ready("2026-09-24") is False


def test_meoz_bid_ready_false_on_stale_tradedate(monkeypatch):
    """★ 串日防护: 整批数据的 tradedate 是**别的日子** → 必须判未就绪。

    这是本改动的核心安全条款: 2026-09-24 09:15 实测 daily_auc 返回的是 09-23 的 9:25
    (5567 行、量比几乎全非零)。只看"非零够多"就会把昨日值当今日定格。
    """
    stale = {"%06d" % (600000 + i): {"tradedate": "20260923", "auc_vol_ratio": 9.9}
             for i in range(0, 6000)}
    _patch_meoz(monkeypatch, stale)
    assert A.meoz_bid_ready("2026-09-24") is False


def test_meoz_bid_ready_false_below_threshold(monkeypatch):
    """当日数据但非零率不达标(仅零星有值) → 未就绪"""
    payload = {"%06d" % (600000 + i): {"tradedate": "20260924", "auc_vol_ratio": 0}
               for i in range(0, 6000)}
    for k in list(payload)[:100]:                        # 100/6000 远低于 90%
        payload[k]["auc_vol_ratio"] = 5.5
    _patch_meoz(monkeypatch, payload)
    assert A.meoz_bid_ready("2026-09-24") is False


def test_meoz_bid_ready_true_when_today_and_nonzero(monkeypatch):
    """当日数据 + 非零达标 → 就绪"""
    payload = {"%06d" % (600000 + i): {"tradedate": "20260924", "auc_vol_ratio": 5.5}
               for i in range(0, 6000)}
    _patch_meoz(monkeypatch, payload)
    assert A.meoz_bid_ready("2026-09-24") is True


def test_meoz_bid_ready_passes_when_meoz_disabled(monkeypatch):
    """猫爪未启用: 不存在"等猫爪", 不得把定格永久卡死。"""
    monkeypatch.setattr(meoz_client, "enabled", lambda: False)
    assert A.meoz_bid_ready("2026-09-24") is True


# ------------------------------------------------------------------ C 补采
def test_refill_vol_ratio_writes_only_nonzero(monkeypatch):
    """上游非零 → 回填; 真 0 / 无值 / 串日 → 一律保持 0 不写。"""
    _seed(D1, [("600001", 0.0), ("600002", 0.0), ("600003", 0.0), ("600004", 0.0)])
    _patch_meoz(monkeypatch, {
        "600001": {"tradedate": "20261116", "auc_vol_ratio": 3.3},
        "600002": {"tradedate": "20261116", "auc_vol_ratio": 0},
        "600003": {"tradedate": "20261116", "auc_vol_ratio": None},
        "600004": {"tradedate": "20261115", "auc_vol_ratio": 9.9},   # 串日
    })
    nz, n_upd, n_all = A.refill_bid_vol_ratio(D1, PT)
    assert (nz, n_upd, n_all) == (1, 1, 4)
    assert _read_vr(D1, "600001") == 3.3
    assert _read_vr(D1, "600002") == 0.0
    assert _read_vr(D1, "600003") == 0.0
    assert _read_vr(D1, "600004") == 0.0          # 串日残值绝不回填


def test_refill_vol_ratio_idempotent(monkeypatch):
    """已有非零值不被覆盖(只补缺纪律) → 重复调用零副作用。"""
    _seed(D2, [("600001", 7.7)])
    _patch_meoz(monkeypatch, {"600001": {"tradedate": "20261117", "auc_vol_ratio": 1.1}})
    assert A.refill_bid_vol_ratio(D2, PT) == (1, 0, 1)
    assert _read_vr(D2, "600001") == 7.7
    assert A.refill_bid_vol_ratio(D2, PT) == (1, 0, 1)
    assert _read_vr(D2, "600001") == 7.7


def test_refill_vol_ratio_noop_when_disabled_or_no_rows(monkeypatch):
    """猫爪未启用 / 无定格行 → 返回全 0, 不抛异常"""
    monkeypatch.setattr(meoz_client, "enabled", lambda: False)
    assert A.refill_bid_vol_ratio("1999-01-01", PT) == (0, 0, 0)
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    assert A.refill_bid_vol_ratio("1999-01-01", PT) == (0, 0, 0)


# ------------------------------------------------------------------ D 源码级守卫
def test_scheduler_bid25_gate_is_minute_scoped():
    """定格首采门槛必须限定在 9:25 那一分钟。

    窗口延到 9:27 后, 若仍是裸 `g.tm_sec < _BID25_MIN_SEC`, 则 9:26:00~9:26:44 与
    9:27:00~9:27:44 会被一并跳过 —— 恰好把推迟定格换来的重采窗口切碎。
    """
    src = inspect.getsource(A._scheduler_loop)
    assert "hm == 9 * 60 + 25" in src, "9:25 首采门槛缺少分钟限定(会切碎 9:26/9:27 重采窗口)"
    assert "_BID25_MIN_SEC" in src


def test_scheduler_retry_fuse_includes_meoz_readiness():
    """回滚重采必须有「猫爪未就绪」这一条 —— 这是「拿到猫爪数据再定格」的落点。"""
    src = inspect.getsource(A._scheduler_loop)
    assert "meoz_bid_ready" in src, "9:25 重采保险丝缺少猫爪就绪判定"


def test_snapshot_write_is_idempotent_upsert():
    """★ 重采现在**真的会触发**(见上), 故定格落库必须是幂等 upsert。

    两个前提缺一不可: ① SQL 是 `INSERT OR REPLACE`; ② `snapshot_bid` 的 PK 含
    `(date, time_point, code)`。任一被改, 一次定格会被写 2~4 份 → `has_today_snapshot`、
    候选池、质量盘点全部跟着多算。修复前重采是死条件, 这个前提从未被真正用到过。
    """
    src = inspect.getsource(A.snapshot_at)
    assert "INSERT OR REPLACE INTO snapshot_bid" in src, "落库不是 upsert, 重采会写重复行"
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='snapshot_bid'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "snapshot_bid 表不存在"
    assert "PRIMARY KEY (date, time_point, code)" in str(row[0]), \
        "snapshot_bid 主键不含 (date,time_point,code), INSERT OR REPLACE 不会去重"


def test_scheduler_uses_retry_open_helper():
    """调度器必须走 _bid25_retry_open —— 禁止内联「减 9*3600」的历史写法回流。

    该写法使条件恒 False(2026-09-19~24 重采静默失效), 属"改了但没生效"类事故,
    必须由断言拦住, 不能靠注释和记忆。
    """
    src = inspect.getsource(A._scheduler_loop)
    assert "_bid25_retry_open" in src, "9:25 重采未走统一口径函数"
    assert "_retry_sec_today" not in src, "已废弃的 _retry_sec_today 回流(单位口径会再次弄反)"
    assert "_BID25_RETRY_UNTIL - 9 * 3600" not in src, "内联减 9*3600 → 条件恒 False"


# ------------------------------------------- E 关联链路: aipick 采集/预测的就绪门
def _patch_has_snapshot(monkeypatch, value):
    monkeypatch.setattr(A, "has_today_snapshot", lambda *a, **k: value)


def test_aipick_waits_for_bid25_snapshot(monkeypatch):
    """★ 定格推迟的直接副作用: aipick 采集/预测必须等当日 9_25 落库。

    落库时刻(09:25:5x~09:26:1x)与 `aipick_collect` 窗口起点(09:26:00, 20s 轮询)重叠 ⇒
    不设门约五成概率抢跑: `collector.fetch_from_kuaixuan` 读到 0 行 → 静默回退"猫爪自拉",
    且当天唯一那次 setnx 已烧掉 ⇒ 全天 AI 输入集与设计不符。
    """
    from app.services import aipick_scheduler as S

    _patch_has_snapshot(monkeypatch, False)
    assert S._aipick_ready("aipick_collect", 9 * 60 + 26) is False      # 未落库 → 等
    assert S._aipick_ready("aipick_predict", 9 * 60 + 27) is False
    # 不依赖当日定格的三个任务不受影响(窗口照常)
    assert S._aipick_ready("aipick_label", 9 * 60 + 26) is True
    assert S._aipick_ready("aipick_backfill", 9 * 60 + 26) is True
    assert S._aipick_ready("aipick_train", 9 * 60 + 26) is True

    _patch_has_snapshot(monkeypatch, True)
    assert S._aipick_ready("aipick_collect", 9 * 60 + 26) is True       # 落库即放行


def test_aipick_ready_hard_fallback(monkeypatch):
    """硬兜底: 到 09:29 即使仍无定格也放行 —— 不能让 AI 侧连带"当天彻底不跑"。

    定格整点缺失是独立故障(已有 _check_system_batch 补跑 + 飞书告警链路)。
    """
    from app.services import aipick_scheduler as S

    _patch_has_snapshot(monkeypatch, False)
    assert S._aipick_ready("aipick_collect", S._AIPICK_READY_FALLBACK_HM - 1) is False
    assert S._aipick_ready("aipick_collect", S._AIPICK_READY_FALLBACK_HM) is True
    assert S._aipick_ready("aipick_predict", S._AIPICK_READY_FALLBACK_HM) is True


def test_aipick_ready_probe_failure_is_conservative(monkeypatch):
    """探测异常 → 视为未就绪(保守), 但到硬兜底必须能解除 —— 否则探测一挂当天全停。"""
    from app.services import aipick_scheduler as S

    def _boom(*a, **k):
        raise RuntimeError("db 挂了")

    monkeypatch.setattr(A, "has_today_snapshot", _boom)
    assert S._aipick_ready("aipick_collect", 9 * 60 + 28) is False
    assert S._aipick_ready("aipick_collect", S._AIPICK_READY_FALLBACK_HM) is True


def test_system_batch_check_due_waits_for_snapshot(monkeypatch):
    """同源竞态 ②: `_check_system_batch` **每天只跑一次**(setnx), 抢在落库前会误报飞书
    「今日系统自动选股批次未生成」, 还会拿空快照去补跑批次 ⇒ 9:26 那一分钟必须先等落库。

    但 9:27 起**必须无条件执行** —— "整整一分钟都没有定格"正是本检查要告警的场景,
    不能因为加了守卫而永不检查(否则真故障反而静默)。
    """
    from app.services import aipick_scheduler as S

    _patch_has_snapshot(monkeypatch, False)
    assert S._system_batch_check_due(9 * 60 + 25) is False     # 窗口外(太早)
    assert S._system_batch_check_due(9 * 60 + 26) is False     # 窗口内但定格未落库 → 等
    assert S._system_batch_check_due(9 * 60 + 27) is True      # 强制检查(告警场景)
    assert S._system_batch_check_due(9 * 60 + 28) is True
    assert S._system_batch_check_due(9 * 60 + 29) is False     # 窗口外(太晚)

    _patch_has_snapshot(monkeypatch, True)
    assert S._system_batch_check_due(9 * 60 + 26) is True      # 落库即可检查


def test_bid25_landed_is_conservative_on_error(monkeypatch):
    """探测异常 → 按"未落库"处理(保守), 不让 db 抖动把两处守卫全变成放行。"""
    from app.services import aipick_scheduler as S

    def _boom(*a, **k):
        raise RuntimeError("db 挂了")

    monkeypatch.setattr(A, "has_today_snapshot", _boom)
    assert S._bid25_landed() is False
    assert S._aipick_ready("aipick_collect", 9 * 60 + 26) is False


def test_aipick_guard_wired_into_scheduler_loop():
    """守卫必须**真的接在**调度循环里(打桩打错地方 = 假绿)。"""
    from app.services import aipick_scheduler as S

    src = inspect.getsource(S._scheduler_loop)
    assert "_aipick_ready(name, hm)" in src
    assert "_system_batch_check_due(hm)" in src, "system_batch 检查未走统一判据(守卫未接线)"
    assert "continue" in src, "未就绪时须 continue(不置 _done_flags, 下轮重试)"
