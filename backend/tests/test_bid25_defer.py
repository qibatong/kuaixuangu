# -*- coding: utf-8 -*-
"""9:25 定格推迟到「拿到猫爪数据再定格」+ 定格枪固定 09:26:30 + 竞价量比接入。

背景(三条都是实测, 不是推测):
  ① 猫爪竞价字段(daily_auc 的 auc_vol_ratio / fundflow_kp 的竞价净额)**09:25:35 起才产出、
     09:26:16 才出满**, 而旧定格枪打在 09:25:20~49 ⇒ auc_vol_ratio 恒 0
     (2026-09-18~24 全库 4 时点复现), 异动因子的量比层于是静默回退旧口径
     「今 9:25 额 ÷ 昨 9:25 额」(覆盖率仅 28%: 只有昨日竞价额 ≥100 万的票算得出)。
  ② 更隐蔽的**串日**: 不传 date(或 date_offset=0)时, 目标日该分钟尚未产出, 上游会返回
     "最近可用"那份 —— 2026-09-24 09:15 取 trademin=0925 拿到的是 **09-23** 的 9:25(5567 行)。
     若把"有 5567 行"当成就绪判据, 早盘每一枪都会把昨日量比写成今日定格。
  ③ 2026-09-24 主人**二次拍板**: 定格那一枪**固定在 09:26:30**(`_BID25_FREEZE_SEC`),
     9:25:00~9:26:29 全程静默不采 —— 该段猫爪竞价字段仍在产出, 采了必是空车,
     还白烧一轮全市场拉取(8~15s)。09:26:30 起首采。
     🔴 2026-09-29 主人**再收紧**: 重采截止 / 净额·量比补采上限 / 抢筹轮采上界
      **一律不得晚于 09:26:30** ⇒ 定格那一枪即终值(无回滚重采), 定格后不再改写竞价数据
      (与下游 9:26 系统批次 / AI 预测的消费口径一致)。

本文件覆盖:
  A. 时间常量: 窗口 / 定格首采时刻 / 重采截止(契约推导)与不变式;
     以及定格首采纯函数 `_bid25_before_freeze` 的秒级边界;
  B. 就绪判定 meoz_bid_ready: 串日必须判未就绪、当日非零达标才判就绪;
  C. 补采 refill_bid_vol_ratio: 只写真非零、绝不覆盖、串日不回填、幂等;
  D. 源码级守卫: 定格首采门槛必须走 `_bid25_before_freeze` —— 历史的"分钟限定"写法
     (`hm == 9*60+25 and sec < _BID25_MIN_SEC`)在 _BID25_MIN_SEC 涨到 90 后会让
     9:26:00 就开采, 恰在定格时刻之前 30 秒; 且单位口径必须由纯函数单点把守;
     重采必须走 _bid25_retry_open(单位口径单一入口, 禁止内联减 9*3600 回流);
     落库必须是幂等 upsert(重采真的会触发, 写重行会被下游全盘放大);
  E. 关联链路: aipick 采集/预测的**定格就绪门** —— 落库时刻(09:26:3x~09:26:4x)与
     aipick 窗口起点(09:26:00)重叠, 不设门约五成概率抢跑, collector 会静默回退自拉。
"""
import inspect
import re
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


def test_bid25_freeze_point_is_092630():
    """★ 定格首采时刻 = 09:26:30(2026-09-24 二次拍板; 2026-09-29 收紧为"这一枪即终值")。

    四条不变式(任一条破了都是"改了但没生效"或"空车采集"):
      ① `_BID25_MIN_SEC` 必须 >= 90 —— 旧值 45(= 09:25:45)会在猫爪出满(09:26:16)之前开采;
      ② `_BID25_FREEZE_SEC` 由 `_BID25_MIN_SEC` **唯一推导**, 不得在别处再写一份字面量;
      ③ **定格时刻 == 重采截止**(2026-09-29 新口径): 不再留重采空间 ⇒ 单枪定格、定格后不改数据;
         若哪天又要放开重采, 这条会红, 提醒同步更新 `_BID25_RETRY_UNTIL` 与下游口径;
      ④ 单位: 与 `_BID25_RETRY_UNTIL` 同为**当日绝对秒**(含 9*3600)。
    """
    assert A._BID25_MIN_SEC >= 90
    assert A._BID25_FREEZE_SEC == 9 * 3600 + 25 * 60 + A._BID25_MIN_SEC
    assert A._BID25_FREEZE_SEC == 9 * 3600 + 26 * 60 + 30      # 09:26:30 = 33990
    assert A._BID25_FREEZE_SEC == A._BID25_RETRY_UNTIL         # ★ 单枪定格(无重采空间)
    assert A._BID25_FREEZE_SEC > 9 * 3600, \
        "定格时刻漏了 9*3600(口径退回'9 点后秒数'), 与 hm*60+sec 比较会恒 True"


def test_bid25_before_freeze_boundaries():
    """`_bid25_before_freeze` 秒级边界: 09:25:00~09:26:29 静默, 09:26:30 起可采。

    ★ 全程用**当日绝对秒**(hm*60+sec)这一把尺子, 与实现同口径 —— 若一侧额外加减
      9*3600, 断言会因量级差而恒真/恒假, 等于没测(本文件初版就踩过这个坑)。
    """
    assert A._bid25_before_freeze(9 * 60 + 25, 0) is True       # 9:25:00 静默段起
    assert A._bid25_before_freeze(9 * 60 + 25, 45) is True      # 旧首采点(09:25:45): 已废, 仍静默
    assert A._bid25_before_freeze(9 * 60 + 25, 59) is True      # 9:25 整分钟静默
    assert A._bid25_before_freeze(9 * 60 + 26, 0) is True       # ★ 9:26:00 仍静默(旧写法会在此开采)
    assert A._bid25_before_freeze(9 * 60 + 26, 16) is True      # 猫爪出满(09:26:16)仍静默
    assert A._bid25_before_freeze(9 * 60 + 26, 29) is True      # 定格前一秒
    assert A._bid25_before_freeze(9 * 60 + 26, 30) is False     # ★ 定格首采时刻(33990)
    assert A._bid25_before_freeze(9 * 60 + 26, 31) is False
    assert A._bid25_before_freeze(9 * 60 + 27, 59) is False     # 窗口末端


def test_bid25_retry_until_derived_from_contract():
    """重采截止由 auc_vol_ratio 契约 ready_after 推导, 且必须落在窗口内。

    🔴 全程用**当日绝对秒**(hm*60+sec)这一把尺子, 与 _bid25_retry_open 同口径 ——
       若一侧额外减 9*3600, 断言会因为**量级差 9*3600 而恒真**, 等于没测(本文件初版
       就踩了这个坑, 是它放过了生产代码里的单位 bug)。
    """
    c = contracts.field("auc_vol_ratio")
    h, m, s = (int(x) for x in c.ready_after.split(":"))
    ready_sec = h * 3600 + m * 60 + s                              # 09:25:35 = 33935
    assert A._BID25_RETRY_UNTIL == ready_sec + 55                  # 09:26:30 = 33990(2026-09-29 收紧)
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
    """_bid25_retry_open 边界(2026-09-29 收紧后): 9:25:00~9:26:29 可重采, **9:26:30 起接受当前值**。

    ★ 本函数只判「是否还在重采时间窗内」, **不管现在到没到定格首采时刻**
      (那是 `_bid25_before_freeze` 的职责) —— 所以 9:25 整分钟也返回 True。
    ★ 收紧后 09:26:30 = 定格时刻 = 截止 ⇒ 实质上"定格即终值"(无重采轮次)。
    """
    assert A._bid25_retry_open(9 * 60 + 25, 0) is True      # 定格首采前: 也须在"可重采"侧
    assert A._bid25_retry_open(9 * 60 + 25, 45) is True     # 旧首采点(33945)
    assert A._bid25_retry_open(9 * 60 + 26, 16) is True     # 猫爪产出上限附近(33976) ★ 等猫爪的意义
    assert A._bid25_retry_open(9 * 60 + 26, 29) is True     # 定格前一秒(33989)
    assert A._bid25_retry_open(9 * 60 + 26, 30) is False    # ★ 截止当秒 = 定格时刻(33990)
    assert A._bid25_retry_open(9 * 60 + 27, 29) is False    # 旧截止(34049): 已不再接受重采
    assert A._bid25_retry_open(9 * 60 + 27, 59) is False    # 窗口末端(34079)


def test_all_bid_collection_windows_capped_at_092630():
    """★★ 2026-09-29 主人口径: 三处"竞价轮采/补采"**一律不得晚于 09:26:30(= 定格时刻)**。

    被约束的三处(与生产对话逐条对应):
      ① 竞价四时点快照(→ snapshot_bid) 的定稿/重采截止;
      ② 竞价净额·量比补采的硬上限;
      ③ 竞价抢筹结果快照的轮采(含失败重试)上界。
    把它们放在同一条断言里, 是为了"以后放开任何一处都会立刻红" —— 而不是各自散落。
    """
    cap = 9 * 3600 + 26 * 60 + 30                    # 09:26:30
    assert A._BID25_FREEZE_SEC == cap
    assert A._BID25_RETRY_UNTIL <= cap
    assert A.NETFILL_END_SEC <= cap
    assert A._BID_QC_UNTIL_SEC <= cap
    # 收窄不能误伤成"空窗": 起点必须仍早于上限, 且不早于上游就绪
    assert A.NETFILL_START_SEC < A.NETFILL_END_SEC
    assert A.NETFILL_START_SEC >= 9 * 3600 + 25 * 60 + 35        # ≥ 猫爪产出起点 09:25:35


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
    # 🔴 2026-09-29: 先进入"下一轮"再验重复调用 —— 补采新增了**每轮一取**的跨进程令牌
    #   (`netfill:turn:vr:<date>`, TTL 31s < 轮询 35s): 本轮内第二次会被"另一 worker 正在取"
    #   挡掉而返回 (0,0,1)。生产里两个 worker 共享一轮正是设计目标; 令牌按 TTL 自行过期,
    #   清掉 == 时间推进到下一轮, 于是仍能验"已有非零值不被覆盖"。
    from app.services.cache_store import store as _cs
    _cs.clear_prefix("netfill:turn:")
    assert A.refill_bid_vol_ratio(D2, PT) == (1, 0, 1)
    assert _read_vr(D2, "600001") == 7.7


def test_refill_vol_ratio_noop_when_disabled_or_no_rows(monkeypatch):
    """猫爪未启用 / 无定格行 → 返回全 0, 不抛异常"""
    monkeypatch.setattr(meoz_client, "enabled", lambda: False)
    assert A.refill_bid_vol_ratio("1999-01-01", PT) == (0, 0, 0)
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    assert A.refill_bid_vol_ratio("1999-01-01", PT) == (0, 0, 0)


# ------------------------------------------------------------------ D 源码级守卫
def test_scheduler_bid25_gate_is_freeze_scoped():
    """定格首采门槛必须走 `_bid25_before_freeze`(定格时刻口径), 不得回流"分钟限定"写法。

    🔴 历史写法 `hm == 9*60+25 and g.tm_sec < _BID25_MIN_SEC` 在 `_BID25_MIN_SEC` 涨到
      90(> 59)后**语义已错**: 9:25 整分钟仍全跳过 ✓, 但 **9:26:00 立刻进采集分支** ✗ ——
      恰在定格时刻(09:26:30)之前 30 秒打一枪必然扑空的采集, 正是本次要消除的行为。
      整点分钟判定根本无法表达「9:26:30」这个跨分钟时刻。
    ★ 断言只看**代码**(先剥掉 `#` 注释再判) —— 否则为了讲清历史而引用旧写法的注释
      会把断言弄红, 逼着后人删掉最有价值的解释。
    """
    src = inspect.getsource(A._scheduler_loop)
    code = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
    assert "_bid25_before_freeze" in code, "定格首采门槛未走统一口径函数"
    assert "_BID25_MIN_SEC" not in code, \
        "_BID25_MIN_SEC 已涨到 90(>59), 裸用会漏掉定格前的静默段(9:26:00 就开采)"
    assert "hm == 9 * 60 + 25" not in code, "已废弃的'分钟限定'写法回流"


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
