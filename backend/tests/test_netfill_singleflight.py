# -*- coding: utf-8 -*-
"""竞价补采「每轮一取」令牌 + 就绪判定微缓存(2026-09-29 P0-c1/c2)测试。

背景(生产 `--workers 2`):
  * **补采**: 两个 worker 各有自己的 `_last_netfill_ts` 与调度循环 ⇒ 同一轮各取一遍
    **同一份竞价定格终值**(净额 3 片 + 量比 1 次全市场, 回填幂等 ⇒ 谁写都一样, 重复纯浪费);
  * **就绪判定**: `meoz_bid_ready` 里的 `fresh=True` 全市场 `daily_auc`(无缓存)每 10s 一轮、
    两进程各一次 ⇒ 一个定格窗口能打出 ~12 次全市场探测, 正压在猫爪最紧张的时段。

本文件锁住 4 件事:
  ① 抢不到"每轮一取"令牌 ⇒ 一轮上游都不打(交给另一 worker);
  ② 令牌 TTL **短于**轮询间隔(抢到令牌的进程异常时, 另一进程最多被跳过一轮);
  ③ 令牌**不改变** WP2b 的 fresh 语义(仍然直打上游、仍不读不写主链缓存);
  ④ 就绪判定只缓存"判定结果"且 TTL < 10s 轮询间隔, 异常路径不缓存。
"""
import pytest

from app.services import auction_snapshot as asnap, meoz_client


class _KvStore:
    """带真实 kv 语义的假 store(跨进程令牌 + 判定微缓存都要跑真逻辑)。"""

    def __init__(self, ini=None):
        self.kv = dict(ini or {})
        self.setnx_calls = []
        self.set_calls = []

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def set(self, key, value, ttl=0):
        self.set_calls.append((key, value, ttl))
        self.kv[key] = value
        return True

    def setnx(self, key, value=1, ttl=0):
        self.setnx_calls.append((key, ttl))
        if key in self.kv:
            return False
        self.kv[key] = value
        return True


class _Rows:
    def __init__(self, items):
        self._items = items

    def fetchall(self):
        return [(c,) for c in self._items]

    def rowcount(self):
        return len(self._items)


class _Conn:
    def __init__(self, items):
        self._items = items

    def execute(self, *a, **k):
        return _Rows(self._items)

    def executemany(self, *a, **k):
        return _Rows(self._items)

    def commit(self):
        pass

    def close(self):
        pass


def _patch_db(monkeypatch, codes):
    monkeypatch.setattr(asnap.database, "get_conn", lambda: _Conn(codes))


def _patch_enabled(monkeypatch):
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)


# ---------------- ① 令牌语义 ----------------

def test_turn_ttl_shorter_than_poll_interval():
    """令牌 TTL 必须**短于**轮询间隔 —— 抢到令牌的进程若异常, 另一进程最坏只被跳过一轮。"""
    assert asnap._netfill_turn_ttl() < asnap.netfill_interval()
    assert asnap._netfill_turn_ttl() >= 5


def test_turn_keys_are_independent_and_do_not_collide():
    """净额/量比各自独立(否则互相阻塞), 且新键不与既有关卡/缓存撞名 ——
    不能挤占板块异动(`sem:meoz`)、开盘啦(`sem:kpl`)、昨比(`sem:yday`)的槽位。"""
    k_net = asnap._netfill_turn_key("net", "2026-09-29")
    k_vr = asnap._netfill_turn_key("vr", "2026-09-29")
    assert k_net != k_vr, "净额与量比必须各自独立"
    for k in (k_net, k_vr):
        assert k.startswith("netfill:turn:")
        assert "sem:" not in k
        assert not k.startswith(("meoz:", "kpl:", "meoz"))


def test_refill_main_net_skips_when_other_worker_took_turn(monkeypatch):
    """有活可干但令牌被抢 ⇒ 一轮上游都不打, 也不写库(另一 worker 正在做同一件事)。"""
    _patch_db(monkeypatch, ["600001", "600002"])
    monkeypatch.setattr(asnap, "store", _KvStore({"netfill:turn:net:2026-09-29": 1}))
    _patch_enabled(monkeypatch)
    monkeypatch.setattr(meoz_client, "fundflow_map",
                        lambda *a, **k: pytest.fail("令牌被抢时不得打上游"))
    assert asnap.refill_bid_main_net("2026-09-29") == (0, 0, 2)


def test_refill_vol_ratio_skips_when_other_worker_took_turn(monkeypatch):
    """量比同理(独立键, 不被净额令牌连带阻塞)。"""
    _patch_db(monkeypatch, ["600001"])
    monkeypatch.setattr(asnap, "store", _KvStore({"netfill:turn:vr:2026-09-29": 1}))
    _patch_enabled(monkeypatch)
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: pytest.fail("令牌被抢时不得打上游"))
    assert asnap.refill_bid_vol_ratio("2026-09-29") == (0, 0, 1)


def test_net_turn_does_not_block_vol_ratio(monkeypatch):
    """净额令牌被占**不影响**量比: 两者数据源不同, 不该互相拖累。"""
    _patch_db(monkeypatch, ["600001"])
    store = _KvStore({"netfill:turn:net:2026-09-29": 1})     # 净额已被别人抢走
    monkeypatch.setattr(asnap, "store", store)
    _patch_enabled(monkeypatch)
    seen = {}

    def _am(*a, **k):
        seen["called"] = True
        return {}

    monkeypatch.setattr(meoz_client, "daily_auc_amt", _am)
    asnap.refill_bid_vol_ratio("2026-09-29")
    assert seen.get("called") is True, "净额令牌不该把量比一起挡掉"


def test_refill_keeps_fresh_semantics(monkeypatch):
    """令牌**不改变** WP2b 语义: 抢到令牌时依旧 fresh 直打上游(不读不写主链缓存)。"""
    _patch_db(monkeypatch, ["600001"])
    store = _KvStore()
    monkeypatch.setattr(asnap, "store", store)
    _patch_enabled(monkeypatch)
    seen = {}

    def _ff(codes, date_offset=None, date=None, fresh=False):
        seen["fresh"] = fresh
        return {}

    monkeypatch.setattr(meoz_client, "fundflow_map", _ff)
    asnap.refill_bid_main_net("2026-09-29")

    assert seen["fresh"] is True, "补采必须保持 fresh 直打上游"
    assert store.setnx_calls == [("netfill:turn:net:2026-09-29", asnap._netfill_turn_ttl())]
    assert not [k for k, _ in store.set_calls if k.startswith("meoz:")], "不得写主链缓存键"


# ---------------- ④ 就绪判定微缓存 ----------------

def test_bidready_ttl_below_snapshot_poll_interval():
    """判定结果缓存必须**短于**快照轮询间隔(10s), 否则「每轮都看得见上游真值」被破坏。"""
    assert asnap._BIDREADY_TTL < 10


def test_bidready_verdict_shared_within_one_round(monkeypatch):
    """同一轮(8s 内)两进程共享一次探测: 第二次调用不得再打全市场 fresh daily_auc。"""
    monkeypatch.setattr(asnap, "store", _KvStore())
    _patch_enabled(monkeypatch)
    calls = {"n": 0}

    def _am(*a, **k):
        calls["n"] += 1
        # 就绪判据是「非零量比只数 ≥ _VR_READY_MIN_N(≈ 全市场 90%)」⇒ 必须给足行数,
        # 否则判定恒为「未就绪」, 测不到缓存那一层。
        n = asnap._VR_READY_MIN_N + 10
        return {"%06d" % i: {"tradedate": "20260929", "auc_vol_ratio": 1.2}
                for i in range(n)}

    monkeypatch.setattr(meoz_client, "daily_auc_amt", _am)

    assert asnap.meoz_bid_ready("2026-09-29") is True
    assert asnap.meoz_bid_ready("2026-09-29") is True
    assert calls["n"] == 1, "同一轮内不得重复打全市场 fresh daily_auc"


def test_bidready_not_ready_verdict_is_also_cached(monkeypatch):
    """「未就绪」同样缓存(两 worker 会同时反复问同一个问题) —— 但仍标 None 值的行按未就绪处理。"""
    store = _KvStore()
    monkeypatch.setattr(asnap, "store", store)
    _patch_enabled(monkeypatch)
    calls = {"n": 0}

    def _am(*a, **k):
        calls["n"] += 1
        return {"600001": {"tradedate": "20260928", "auc_vol_ratio": 1.2}}   # 串日残值

    monkeypatch.setattr(meoz_client, "daily_auc_amt", _am)

    assert asnap.meoz_bid_ready("2026-09-29") is False
    assert asnap.meoz_bid_ready("2026-09-29") is False
    assert calls["n"] == 1
    assert store.kv["bidready:20260929"] == 0


def test_bidready_exception_is_not_cached(monkeypatch):
    """探测抛异常 ⇒ 视为未就绪且**不缓存**(否则一次抖动就把就绪判定冻住 8 秒)。"""
    store = _KvStore()
    monkeypatch.setattr(asnap, "store", store)
    _patch_enabled(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("429")

    monkeypatch.setattr(meoz_client, "daily_auc_amt", _boom)

    assert asnap.meoz_bid_ready("2026-09-29") is False
    assert store.set_calls == [], "异常路径不得写判定缓存"


def test_bidready_disabled_meoz_passes_without_probe(monkeypatch):
    """猫爪未启用 ⇒ 直接放行(True), 且不打上游、不写缓存 —— 原语义不能被兜底改掉。"""
    store = _KvStore()
    monkeypatch.setattr(asnap, "store", store)
    monkeypatch.setattr(meoz_client, "enabled", lambda: False)
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: pytest.fail("猫爪未启用时不得探测"))
    assert asnap.meoz_bid_ready("2026-09-29") is True
    assert store.set_calls == []
