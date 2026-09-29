# -*- coding: utf-8 -*-
"""P0① 竞价窗口让路 + spot 预热跨进程共享一次出网（2026-09-29）。

背景（按主人给的优先级：竞价异动 ＞ 竞价选股 ＞ 实时动态选股 ＞ 一进二）:
  猫爪/东财配额是全链路共享的，而 09:15~09:28 最挤。原 spot 全市场预热窗口 **09:26** 起，
  正好压在 9_25 定格采集（09:26:30~38）与净额/量比补采（≈09:26:45 / 09:27:20）上；
  且该预热"每个 web worker 各拉一份全市场（28 页）"（生产 2 worker ⇒ 上游调用翻倍）。

本文件锁三件事:
  ① 预热窗口起点 = **09:28**（让开定格/补采），且 09:30 前至少还有一轮预热（40s 周期）；
  ② 谁真出网就把**裁剪后的 raw** 发布到 kv，另一个进程**零上游**复用重建 map；
  ③ 共享条目过期后必须重新出网（不能拿 45s 前的行情硬撑）。
另附一条: 概念刷新只许改 board 列, **不得改写 auction_daily_history.ts**。
"""
import calendar
import json
import sqlite3
import time

import pytest

from app.services import fetcher
from app.services import concept_refresh as cr


class _Kv:
    """假 kv(共享 raw 用到的 get/set 语义)。"""

    def __init__(self):
        self.kv = {}
        self.set_calls = []

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def set(self, key, value, ttl=0):
        self.set_calls.append((key, ttl))
        self.kv[key] = value
        return True


def _epoch(cst):
    """北京时间字符串 → epoch(UTC)。"""
    return calendar.timegm(time.strptime(cst + ':00', '%Y-%m-%d %H:%M:%S')) - 8 * 3600


def _row(code, price=10.0):
    return {"f12": code, "f14": "测试", "f2": price, "f3": 1.0, "f8": 2.0,
            "f10": 3.0, "f17": 9.0, "f99": "无关字段(不该进共享)"}


# ---------------- ②b 出网令牌: 消除"两 worker 同瞬间各拉一份"的竞态 ----------------

class _KvOneShot(_Kv):
    """前 after-1 次 get 返回 None(模拟对端还没发布), 之后返回共享条目。"""

    def __init__(self, after=3, price=7.0):
        super().__init__()
        self.calls = 0
        self.after = after
        self.price = price

    def get(self, key, default=None):
        self.calls += 1
        if self.calls >= self.after and str(key).startswith("spot:raw:"):
            return {"raw": [_row("600001", price=self.price)], "ts": time.time()}
        return None

    def setnx(self, key, value=1, ttl=0):
        return False        # 令牌已被对端抢走


def test_peer_waits_and_reuses_instead_of_double_fetch(monkeypatch):
    """令牌被对端抢走 ⇒ 预热线程稍微等一会儿读共享, 自己**不出网**。

    (生产实测: 两个 worker 同时重启时相位对齐, 无令牌会各拉一份全市场 28 页。)"""
    store = _KvOneShot(after=3)
    monkeypatch.setattr(fetcher, "store", store)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback",
                        lambda fs: pytest.fail("令牌被抢时预热线程不该出网"))
    raw, reused = fetcher._spot_fetch_or_share("hs", wait_for_peer=True)
    assert reused is True
    assert fetcher._build_quote_map(raw)["600001"]["price"] == 7.0


def test_request_path_never_waits_for_peer(monkeypatch):
    """请求路径持 `_quote_map_lock` ⇒ 令牌被抢也**不等待**, 直接出网(不阻塞同进程其它请求)。"""
    monkeypatch.setattr(fetcher, "store", _Kv())
    monkeypatch.setattr(fetcher, "_spot_lease_try", lambda fs: False)
    calls = {"n": 0}

    def _f(fs):
        calls["n"] += 1
        return [_row("600001")]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", _f)
    raw, reused = fetcher._spot_fetch_or_share("hs")        # 默认 wait_for_peer=False
    assert reused is False and calls["n"] == 1


def test_peer_fail_open_when_holder_dies(monkeypatch):
    """对端抢了令牌却始终不发布(进程崩了) ⇒ 等满后自己拉, 绝不空转。"""
    class _KvNoShare(_Kv):
        def setnx(self, key, value=1, ttl=0):
            return False

    monkeypatch.setattr(fetcher, "store", _KvNoShare())
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def _f(fs):
        calls["n"] += 1
        return [_row("600001", price=3.0)]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", _f)
    raw, reused = fetcher._spot_fetch_or_share("hs", wait_for_peer=True)
    assert reused is False and calls["n"] == 1
    assert fetcher._build_quote_map(raw)["600001"]["price"] == 3.0


# ---------------- ① 窗口让路 ----------------

def test_prewarm_window_starts_at_0928():
    """09:27 不预热(定格/补采还在跑), 09:28 起预热。"""
    assert fetcher.spot_prewarm_active(_epoch('2026-09-29 09:26')) is False
    assert fetcher.spot_prewarm_active(_epoch('2026-09-29 09:27')) is False
    assert fetcher.spot_prewarm_active(_epoch('2026-09-29 09:28')) is True
    assert fetcher.spot_prewarm_active(_epoch('2026-09-29 10:00')) is True
    assert fetcher.spot_prewarm_active(_epoch('2026-09-29 15:05')) is True
    assert fetcher.spot_prewarm_active(_epoch('2026-09-29 15:06')) is False


def test_prewarm_window_weekend_inactive():
    """周六不预热(2026-09-26 是周六)。"""
    assert fetcher.spot_prewarm_active(_epoch('2026-09-26 10:00')) is False


def test_0928_still_leaves_rounds_before_0930():
    """09:28 起 + 40s 周期 ⇒ 09:30 前至少还有 2 轮预热(首屏仍命中缓存)。"""
    start = _epoch('2026-09-29 09:28')
    rounds = [start + i * fetcher._SPOT_PREWARM_PERIOD for i in range(3)]
    assert sum(1 for r in rounds if r < _epoch('2026-09-29 09:30')) >= 2


# ---------------- ② 跨进程共享 ----------------

def test_second_process_reuses_shared_raw(monkeypatch):
    """第一个进程出网并发布; 第二个进程(同 kv)零上游复用, 且 map 内容一致。"""
    store = _Kv()
    monkeypatch.setattr(fetcher, "store", store)
    calls = {"n": 0}

    def _fetch(fs):
        calls["n"] += 1
        return [_row("600001")]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", _fetch)

    raw1, reused1 = fetcher._spot_fetch_or_share("hs")
    raw2, reused2 = fetcher._spot_fetch_or_share("hs")
    assert (reused1, reused2) == (False, True), "第二个进程必须走复用"
    assert calls["n"] == 1, "上游只允许出网一次(kv 共享)"
    assert fetcher._build_quote_map(raw2)["600001"]["price"] == 10.0
    assert store.set_calls and store.set_calls[0][0] == "spot:raw:hs"
    assert store.set_calls[0][1] == fetcher._SPOT_SHARE_TTL


def test_share_keeps_only_needed_fields(monkeypatch):
    """共享条只裁剪保留重建 map 所需字段（全量 raw 每 40s 进 kv 会写几 MB）。"""
    store = _Kv()
    monkeypatch.setattr(fetcher, "store", store)
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", lambda fs: [_row("600001")])
    fetcher._spot_fetch_or_share("hs")
    shared = store.kv["spot:raw:hs"]["raw"][0]
    assert set(shared.keys()) <= set(fetcher._SPOT_SHARE_FIELDS)
    assert "f99" not in shared
    # 裁剪后仍能重建出正确 map(price/volRatio/turnover/realChange 都在)
    m = fetcher._build_quote_map([shared])["600001"]
    assert (m["price"], m["volRatio"], m["turnover"], m["realChange"]) == (10.0, 3.0, 2.0, 1.0)


def test_stale_share_must_refetch(monkeypatch):
    """共享条目过期(>45s) ⇒ 必须重新出网, 不能拿旧行情硬撑。"""
    store = _Kv()
    store.kv["spot:raw:hs"] = {"raw": [_row("600001", price=1.0)],
                               "ts": time.time() - (fetcher._SPOT_SHARE_TTL + 5)}
    monkeypatch.setattr(fetcher, "store", store)
    calls = {"n": 0}

    def _fetch(fs):
        calls["n"] += 1
        return [_row("600001", price=2.0)]

    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", _fetch)
    raw, reused = fetcher._spot_fetch_or_share("hs")
    assert reused is False and calls["n"] == 1
    assert fetcher._build_quote_map(raw)["600001"]["price"] == 2.0


def test_share_failure_does_not_break_fetch(monkeypatch):
    """kv 写失败(异常)不得影响本次取数 —— 共享只是优化, 不是依赖。"""
    class _Boom:
        def get(self, key, default=None):
            raise RuntimeError("kv down")

        def set(self, key, value, ttl=0):
            raise RuntimeError("kv down")

    monkeypatch.setattr(fetcher, "store", _Boom())
    monkeypatch.setattr(fetcher, "_fetch_market_all_with_fallback", lambda fs: [_row("600001")])
    raw, reused = fetcher._spot_fetch_or_share("hs")
    assert reused is False and fetcher._build_quote_map(raw)["600001"]["price"] == 10.0


# ---------------- 附: 概念刷新不得改写采集元信息 ----------------

def test_concept_refresh_keeps_snapshot_ts(monkeypatch, tmp_path):
    """board 刷新只改 list, **不得改 ts**(否则"原始采集时刻"被推后, 排障被误导)。"""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE auction_daily_history "
                 "(date TEXT, tab TEXT, list TEXT, ts INTEGER, PRIMARY KEY(date, tab))")
    conn.execute("CREATE TABLE qc_snapshot (date TEXT, code TEXT, board TEXT)")
    # 该函数先改竞价异动 tab、再改 qc_snapshot、最后读 lhb_history; 任一段抛异常都会让
    # 整个 try 跳到 except(commit 被跳过) ⇒ 测试库必须把三张表都建出来。
    conn.execute("CREATE TABLE lhb_history (date TEXT, list TEXT, ts INTEGER)")
    ts0 = 1700000000
    conn.execute("INSERT INTO auction_daily_history VALUES (?,?,?,?)",
                 ("2026-09-29", "seal", json.dumps([{"code": "600001", "board": ""}]), ts0))
    conn.commit()
    conn.close()

    monkeypatch.setattr(cr.config, "DB_FILE", str(db))
    n = cr._update_lists_with_board("2026-09-29", {"600001": "AI、算力"})
    # 注: n 含 qc_snapshot 段按 conn.total_changes 累加的既有行为 ⇒ 只断言"有回写"
    assert n >= 1

    conn = sqlite3.connect(str(db))
    lst, ts = conn.execute("SELECT list, ts FROM auction_daily_history "
                           "WHERE date='2026-09-29' AND tab='seal'").fetchone()
    conn.close()
    assert json.loads(lst)[0]["board"] == "AI、算力", "board 必须被刷新"
    assert ts == ts0, "ts 必须保持原始采集时刻(不许被 board 刷新推后)"
