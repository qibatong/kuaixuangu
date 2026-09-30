# -*- coding: utf-8 -*-
"""竞价主力净额补采(2026-09-24): 09:26:10 起轮询回填 snapshot_bid.auc_main_net。

背景: 9:25 定格采集那枪落库于 09:25:22~49, 而上游 auction_main_net_amount
      09:25:35~09:26:16 才生成 ⇒ 定格行该列恒 0(全库 8 日 × 4 时点复现)。
本模块覆盖三类判据:
  ① 触发窗口/周末/间隔/达标 四个边界(纯函数 _netfill_due);
  ② 回填语义: 上游非零才写, 真 0/无值不写;
  ③ 幂等: 已有非零值一律不覆盖(WHERE auc_main_net=0), 不触发 aipick/system_batch。
"""
import time

from app.db import database
from app.services import auction_snapshot as asnap
from app.services import meoz_client

# 用冷门日期, 避免与其他用例写入的 (date, time_point) 行互相干扰
# (refill 会取该 date+point 的**全部**行, 断言总数必须只含本用例种的数据)
D1 = "2026-09-28"
D2 = "2026-09-29"
PT = "9_25"


def _seed(date, rows):
    """写入定格行: rows = [(code, auc_main_net), ...]"""
    conn = database.get_conn()
    try:
        for code, net in rows:
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_bid "
                "(date, time_point, code, bid_change, bid_amt, ts, auc_main_net) "
                "VALUES (?,?,?,?,?,?,?)",
                (date, PT, code, 1.0, 100.0, int(time.time()), net))
        conn.commit()
    finally:
        conn.close()


def _read(date, code):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT auc_main_net FROM snapshot_bid "
            "WHERE date=? AND time_point=? AND code=?", (date, PT, code)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _count(date):
    conn = database.get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, PT)).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------ ① 触发边界
def test_netfill_due_window_boundaries():
    """09:26:10 触发(含端点 09:26:10 与 09:26:30); 09:26:09 / 09:26:31 均不触发
    (2026-09-29 主人收紧: 上限由 09:29:50 收到 09:26:30 = 定格时刻)"""
    s, e = asnap.NETFILL_START_SEC, asnap.NETFILL_END_SEC
    now = 1e9
    assert asnap._netfill_due(s - 1, 0, 0.0, now, False) is False      # 09:26:09
    assert asnap._netfill_due(s, 0, 0.0, now, False) is True           # 09:26:10
    assert asnap._netfill_due(e, 0, 0.0, now, False) is True           # 09:29:50
    assert asnap._netfill_due(e + 1, 0, 0.0, now, False) is False      # 09:29:51


def test_netfill_due_weekend_done_interval():
    """周末不采; 已达标不采; 间隔必须 >= netfill_interval()(> 上游 30 秒缓存 TTL)"""
    s, i = asnap.NETFILL_START_SEC, asnap.netfill_interval()
    now = 1e9
    assert asnap._netfill_due(s, 6, 0.0, now, False) is False          # 周六
    assert asnap._netfill_due(s, 5, 0.0, now, False) is False          # 周日
    assert asnap._netfill_due(s, 0, 0.0, now, True) is False           # 已达标(置位)
    assert asnap._netfill_due(s, 0, now - i + 1, now, False) is False  # 间隔未到
    assert asnap._netfill_due(s, 0, now - i, now, False) is True       # 间隔刚好
    # done 传 None(store.get 未命中)视为未完成
    assert asnap._netfill_due(s, 0, 0.0, now, None) is True


def test_netfill_interval_exceeds_upstream_ttl():
    """间隔必须严格大于猫爪缓存 TTL —— 否则每轮都命中上一轮自己写的缓存。

    2026-09-24 (WP2a): NETFILL_INTERVAL 常量已退役, 改由 netfill_interval() 按 TTL
    推导; 本断言仍是有效不变量, 推导关系本身的守卫另见 tests/test_netfill_interval.py。
    """
    assert asnap.netfill_interval() > meoz_client.cache_ttl("fundflow_kp")


# ------------------------------------------------------------------ ② 回填语义
def test_refill_writes_only_nonzero(monkeypatch):
    """上游非零 → 回填; 真 0 / "-" 无值 → 保持 0 不写"""
    _seed(D1, [("600001", 0.0), ("600002", 0.0), ("600003", 0.0)])
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map", lambda codes, **kw: {
        "600001": {"auction_main_net_amount": 1.2e7},
        "600002": {"auction_main_net_amount": 0},
        "600003": {"auction_main_net_amount": "-"},
    })
    nz, n_upd, n_all = asnap.refill_bid_main_net(D1, PT)
    assert (nz, n_upd, n_all) == (1, 1, 3)
    assert _read(D1, "600001") == 1.2e7
    assert _read(D1, "600002") == 0.0
    assert _read(D1, "600003") == 0.0


def test_refill_is_idempotent(monkeypatch):
    """已有非零值不被覆盖 → 重复调用零副作用(只补缺纪律)"""
    _seed(D2, [("600001", 5.0e6)])
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map",
                        lambda codes, **kw: {"600001": {"auction_main_net_amount": 9.9e7}})
    nz, n_upd, n_all = asnap.refill_bid_main_net(D2, PT)
    assert (nz, n_upd, n_all) == (1, 0, 1)          # 上游有值, 但库里已有 → 不回填
    assert _read(D2, "600001") == 5.0e6
    # 再跑一次, 结果不变
    # 🔴 2026-09-29: 必须显式进入"下一轮" —— 补采新增了**每轮一取**的跨进程令牌
    #   (`netfill:turn:net:<date>`, TTL 31s < 轮询 35s), 本轮内第二次调用会被
    #   "另一 worker 正在取"挡掉而返回 (0,0,n) —— 那是生产期望行为(重复取同一份幂等终值
    #   纯属浪费上游), 但与"重复调用零副作用"这条断言的形态不同。令牌按 TTL 自行过期,
    #   所以这里清掉 == 时间推进到下一轮。
    from app.services.cache_store import store as _cs
    _cs.clear_prefix("netfill:turn:")
    assert asnap.refill_bid_main_net(D2, PT) == (1, 0, 1)
    assert _read(D2, "600001") == 5.0e6


def test_refill_noop_when_no_rows_or_disabled(monkeypatch):
    """定格行缺失 / 猫爪未启用 → 返回全 0, 不抛异常"""
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map", lambda codes, **kw: {})
    assert asnap.refill_bid_main_net("1999-01-01", PT) == (0, 0, 0)
    monkeypatch.setattr(meoz_client, "enabled", lambda: False)
    assert asnap.refill_bid_main_net("1999-01-01", PT) == (0, 0, 0)


def test_refill_handles_upstream_empty(monkeypatch):
    """上游返回空 map(分片全失败) → 如实返回 0 回填, 不动库"""
    _seed("2026-09-30", [("600001", 0.0), ("600002", 0.0)])
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map", lambda codes, **kw: {})
    nz, n_upd, n_all = asnap.refill_bid_main_net("2026-09-30", PT)
    assert (nz, n_upd, n_all) == (0, 0, 2)
    assert _read("2026-09-30", "600001") == 0.0


# ------------------------------------------------------------------ ③ 与主链隔离
def test_refill_does_not_touch_other_points_or_dates(monkeypatch):
    """只动 (date, 9_25) 的行 —— 其他时点/日期纹丝不动"""
    date = "2026-10-09"
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_bid "
            "(date, time_point, code, bid_change, bid_amt, ts, auc_main_net) "
            "VALUES (?,?,?,?,?,?,?)", (date, "9_20", "600001", 1.0, 100.0,
                                       int(time.time()), 0.0))
        conn.commit()
    finally:
        conn.close()
    _seed(date, [("600001", 0.0)])
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map",
                        lambda codes, **kw: {"600001": {"auction_main_net_amount": 3.3e6}})
    nz, n_upd, n_all = asnap.refill_bid_main_net(date, PT)
    assert (nz, n_upd, n_all) == (1, 1, 1)          # 总数只算 9_25 那一行
    assert _read(date, "600001") == 3.3e6
    conn = database.get_conn()
    try:
        other = conn.execute(
            "SELECT auc_main_net FROM snapshot_bid WHERE date=? AND time_point='9_20'",
            (date,)).fetchone()[0]
    finally:
        conn.close()
    assert other == 0.0                             # 9_20 未被污染
    assert _count(date) == 1                        # 9_25 只有本用例种的那一行


# ---------------------------------------------------- ⑤ 防串日(2026-09-30 修复)
# 🔴 修复前的缺陷: 取数用 `date_offset=0`, 而猫爪 `fundflow_kp` 的该参数语义 =
#   "**最近一个有数据的交易日**" —— 当日数据未产出时会**回退到上一交易日**
#   (2026-09-30 实测: 盘前 offset=0 返回 20260929/净额非零 1292 只; 而
#    显式 `tradedate=20260930` 返回 0 行 + 业务错误 code=1002, 不返回假值)。
#   ⇒ 首轮补采(09:26:10)若撞上猫爪今日数据尚未产出, 会把**昨日净额**写进今日定格行;
#     且 `WHERE auc_main_net=0` 使后续轮次不会纠正, 非零 1292 只又已 ≥ 达标线(1000)
#     ⇒ 直接"达标即停", 静默把昨天冒充今天。量比那条 09-29 已加固, 净额当时漏改。
def test_refill_skips_rows_carrying_another_tradedate(monkeypatch):
    """上游回的是**别的交易日**的行 → 一律不回填(核心保护)。"""
    D = "2026-08-11"
    _seed(D, [("600001", 0.0), ("600002", 0.0)])
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map", lambda codes, **kw: {
        "600001": {"auction_main_net_amount": 9.9e7, "tradedate": "20260810"},   # 昨日 → 跳过
        "600002": {"auction_main_net_amount": 8.8e7, "tradedate": "20260811"},   # 当日 → 回填
    })
    nz, n_upd, n_all = asnap.refill_bid_main_net(D, PT)
    assert (nz, n_upd, n_all) == (1, 1, 2)          # 只认当日那一行
    assert _read(D, "600001") == 0.0                # 🔴 昨日值绝不进库
    assert _read(D, "600002") == 8.8e7


def test_refill_accepts_rows_without_tradedate(monkeypatch):
    """上游未回 `tradedate` 时不误杀 —— 守卫仅在"有值且与目标日不同"时跳过。

    与 `refill_bid_vol_ratio` 的守卫同款语义; 也保证既有 mock(不带该字段)的用例不受影响。
    """
    D = "2026-08-12"
    _seed(D, [("600001", 0.0)])
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map",
                        lambda codes, **kw: {"600001": {"auction_main_net_amount": 1.1e7}})
    assert asnap.refill_bid_main_net(D, PT) == (1, 1, 1)
    assert _read(D, "600001") == 1.1e7


def test_refill_passes_explicit_tradedate_not_offset(monkeypatch):
    """取数必须**显式传 date**(→ tradedate=YYYYMMDD), 不得再回退成 date_offset=0。"""
    D = "2026-08-13"
    _seed(D, [("600001", 0.0)])
    seen = {}

    def _ff(codes, **kw):
        seen.update(kw)
        return {"600001": {"auction_main_net_amount": 1.0, "tradedate": "20260813"}}

    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "fundflow_map", _ff)
    asnap.refill_bid_main_net(D, PT)
    assert seen.get("date") == "20260813", "必须显式传目标交易日"
    assert "date_offset" not in seen, "不得再用 date_offset(会串到上一交易日)"
