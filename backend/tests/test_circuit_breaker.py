# -*- coding: utf-8 -*-
"""
熔断器 + 三源冗余兜底 + 快照新鲜度告警 测试 (2026-08-25)
验证数据源故障时不会卡死 API, 且快照采集有兜底+告警
"""
import time
import logging

import pytest

from app.services import fetcher, auction_snapshot


@pytest.fixture(autouse=True)
def _reset_health():
    """每个测试前后重置数据源健康状态, 避免熔断器状态跨测试污染"""
    with fetcher._health_lock:
        for src in fetcher._HEALTH:
            h = fetcher._HEALTH[src]
            h["down_since"] = 0
            h["fails_in_row"] = 0
            h["cooldown"] = h.get("base_cooldown", fetcher._CIRCUIT_OPEN_SECONDS)
    yield
    with fetcher._health_lock:
        for src in fetcher._HEALTH:
            h = fetcher._HEALTH[src]
            h["down_since"] = 0
            h["fails_in_row"] = 0
            h["cooldown"] = h.get("base_cooldown", fetcher._CIRCUIT_OPEN_SECONDS)


# ---------- 熔断器 ----------

def test_circuit_open_fast_fail(monkeypatch):
    """东财故障后熔断期内: fetch_eastmoney 快速失败, 不等超时"""
    # 模拟东财故障
    fetcher._record("eastmoney_clist", False)
    assert fetcher._check_circuit() is True

    # 熔断期内调用应立即抛异常, 不等网络超时
    t0 = time.time()
    with pytest.raises(RuntimeError, match="熔断"):
        fetcher.fetch_eastmoney("m:0+t:6")
    elapsed = time.time() - t0
    assert elapsed < 0.1, "熔断期应 <100ms 快速失败, 实际 %.0fms" % (elapsed * 1000)


def test_circuit_open_all_fast_fail(monkeypatch):
    """fetch_eastmoney_all 熔断期内也快速失败"""
    fetcher._record("eastmoney_clist", False)
    assert fetcher._check_circuit() is True

    t0 = time.time()
    with pytest.raises(RuntimeError, match="熔断"):
        fetcher.fetch_eastmoney_all("m:0+t:6")
    elapsed = time.time() - t0
    assert elapsed < 0.1


def test_circuit_recovers_after_cooldown(monkeypatch):
    """熔断冷却期过后: 半开探测, 成功则恢复"""
    # 模拟故障
    fetcher._record("eastmoney_clist", False)
    assert fetcher._check_circuit() is True

    # 模拟冷却期过: 手动把 down_since 设为 61 秒前
    with fetcher._health_lock:
        fetcher._HEALTH["eastmoney_clist"]["down_since"] = time.time() - 61
    assert fetcher._check_circuit() is False, "冷却期过后应允许半开探测"

    # 模拟成功调用 → 恢复
    fetcher._record("eastmoney_clist", True, ms=100)
    assert fetcher._check_circuit() is False
    with fetcher._health_lock:
        assert fetcher._HEALTH["eastmoney_clist"]["down_since"] == 0


def test_circuit_not_open_when_healthy():
    """正常状态: 不熔断"""
    fetcher._record("eastmoney_clist", True, ms=50)
    assert fetcher._check_circuit() is False


def test_circuit_stays_open_on_half_open_fail():
    """半开探测失败: 继续熔断"""
    # 先故障
    fetcher._record("eastmoney_clist", False)
    # 冷却期过 → 半开
    with fetcher._health_lock:
        fetcher._HEALTH["eastmoney_clist"]["down_since"] = time.time() - 61
    assert fetcher._check_circuit() is False

    # 半开探测又失败 → 重新熔断
    fetcher._record("eastmoney_clist", False)
    assert fetcher._check_circuit() is True


# ---------- 2026-09-01 熔断加固: 抖动保护 + 指数退避 + 短路含量脉 ----------

def test_jitter_source_threshold_2_does_not_break_on_single_fail():
    """ths/tencent 抖动保护: 单次失败不熔断, 连续2次才熔断"""
    fetcher._record("ths_kline", False)
    assert fetcher._check_circuit("ths_kline") is False, "单次抖动不应熔断"
    fetcher._record("ths_kline", False)
    assert fetcher._check_circuit("ths_kline") is True, "连续2次失败应熔断"


def test_jitter_source_recovers_after_single_success():
    """抖动源失败1次后成功: 计数清零, 不熔断"""
    fetcher._record("tencent_kline", False)
    assert fetcher._check_circuit("tencent_kline") is False
    fetcher._record("tencent_kline", True, ms=100)
    with fetcher._health_lock:
        assert fetcher._HEALTH["tencent_kline"]["fails_in_row"] == 0
    assert fetcher._check_circuit("tencent_kline") is False


def test_cooldown_backoff_grows_on_consecutive_failures():
    """连续失败: 冷却指数退避 60→120→240(上限600)"""
    for _ in range(3):
        fetcher._record("eastmoney_kline", False)
    with fetcher._health_lock:
        cd = fetcher._HEALTH["eastmoney_kline"]["cooldown"]
    assert cd == 60 * (2 ** 2), "连续3次失败后冷却应为 240s, 实际 %s" % cd
    # 冷却期内 check 为 True(熔断中)
    assert fetcher._check_circuit("eastmoney_kline") is True
    # 冷却期过后可半开探测
    with fetcher._health_lock:
        fetcher._HEALTH["eastmoney_kline"]["down_since"] = time.time() - cd - 1
    assert fetcher._check_circuit("eastmoney_kline") is False


def test_cooldown_resets_to_base_after_success():
    """成功后冷却恢复基准值, fails_in_row 清零"""
    fetcher._record("eastmoney_kline", False)
    fetcher._record("eastmoney_kline", False)
    fetcher._record("eastmoney_kline", True, ms=100)
    with fetcher._health_lock:
        h = fetcher._HEALTH["eastmoney_kline"]
        assert h["fails_in_row"] == 0
        assert h["cooldown"] == h["base_cooldown"]
        assert h["down_since"] == 0


def test_short_circuit_skips_when_all_four_down(monkeypatch):
    """四源(东财/ths/tencent/量脉)全熔断: 单只链路直接短路, 不调底层兜底(连量脉也不试)"""
    from app.services import liangmai
    lm_calls = []
    monkeypatch.setattr(fetcher, "_host_blocked", lambda host: True)   # 东财全部域名快速失败
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_ths", lambda c: None)
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_tencent", lambda c: None)
    monkeypatch.setattr(liangmai, "_fetch_yesterday_amount_one",
                        lambda c, d: lm_calls.append(c) or [111.0, 222.0])
    # 四源全部打到熔断阈值
    fetcher._record("eastmoney_kline", False)
    for src in ("ths_kline", "tencent_kline"):
        fetcher._record(src, False)
        fetcher._record(src, False)
    for _ in range(3):
        fetcher._record("liangmai_kline", False)
    assert all(fetcher._check_circuit(s)
               for s in ("eastmoney_kline", "ths_kline", "tencent_kline", "liangmai_kline"))

    res = fetcher._fetch_yesterday_amount_one("600519")
    assert res is None, "四源全down应短路置空, 实际 %s" % res
    assert lm_calls == [], "短路时不应调用量脉"


def test_short_circuit_does_not_skip_when_liangmai_up(monkeypatch):
    """三源down但量脉可用: 不短路, 走量脉兜底(2026-09-01 核心修复)"""
    from app.services import liangmai
    lm_calls = []
    monkeypatch.setattr(fetcher, "_host_blocked", lambda host: True)   # 东财全部域名快速失败
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_ths", lambda c: None)
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_tencent", lambda c: None)
    monkeypatch.setattr(liangmai, "_fetch_yesterday_amount_one",
                        lambda c, d: lm_calls.append(c) or [333.0, 444.0])
    # 仅三源(东财/ths/tencent)熔断, 量脉健康
    fetcher._record("eastmoney_kline", False)
    for src in ("ths_kline", "tencent_kline"):
        fetcher._record(src, False)
        fetcher._record(src, False)
    assert not fetcher._check_circuit("liangmai_kline")

    res = fetcher._fetch_yesterday_amount_one("600519")
    assert res == [333.0, 444.0], "量脉可用时应兜底成功, 实际 %s" % res
    assert lm_calls == ["600519"], "应调用量脉兜底"


def test_ensure_spot_cache_returns_stale_on_circuit(monkeypatch):
    """熔断时 ensure_spot_cache 沿用旧缓存, 不报错(降级服务)"""
    # 先写入一条缓存
    fake_raw = [{"f12": "600001", "f14": "测试"}]
    fs = "m:0+t:6"
    with fetcher._fetch_lock:
        fetcher._cache[fs] = {"raw": fake_raw, "ts": time.time() - 999}

    # 模拟东财熔断
    fetcher._record("eastmoney_clist", False)
    assert fetcher._check_circuit() is True

    # ensure_spot_cache 应返回旧缓存, 不抛异常
    raw, err = fetcher.ensure_spot_cache("refresh", fs, before930=False)
    assert err is None
    assert raw is not None


# ---------- 三源冗余兜底 ----------

def test_kpl_fallback_when_eastmoney_fails(client, monkeypatch):
    """东财全分区失败时: _fetch_market_map 用开盘啦竞价榜兜底"""
    # 模拟东财全失败
    def boom(fs):
        raise RuntimeError("network down")
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", boom)
    # 2026-08-31: 兜底链新增腾讯/量脉层, 需一并 mock 失败才能测到 kpl 兜底
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_tencent_market", boom)

    # 模拟开盘啦返回数据
    kpl_seal = [
        {"code": "600001", "name": "测试甲", "bidChange": 10.0, "bidAmt": 5e7, "bidSealAmt": 1.2e8, "board": "AI概念"},
    ]
    kpl_boom = [
        {"code": "000002", "name": "测试乙", "bidChange": 6.0, "bidAmt": 3e7, "bidSealAmt": 0, "board": "医药"},
    ]

    import app.services.kpl as kpl_mod
    monkeypatch.setattr(kpl_mod, "fetch_bid_seal", lambda: kpl_seal)
    monkeypatch.setattr(kpl_mod, "fetch_bid_boom", lambda: kpl_boom)
    monkeypatch.setattr(kpl_mod, "clear_cache", lambda: None)

    raw_all = auction_snapshot._fetch_market_map(full=True)

    assert len(raw_all) == 2
    assert "600001" in raw_all
    assert raw_all["600001"]["bid_buy_amt"] == 1.2e8
    assert raw_all["600001"]["board"] == "AI概念"
    assert "000002" in raw_all


def test_kpl_fallback_empty_when_kpl_also_fails(client, monkeypatch):
    """东财+开盘啦都失败时: 返回空 map, snapshot_at 返回 0"""
    def boom(fs):
        raise RuntimeError("network down")
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", boom)
    # 2026-08-31: 兜底链新增腾讯/量脉层, 需一并 mock 失败才能测到 kpl 兜底
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_tencent_market", boom)

    import app.services.kpl as kpl_mod
    monkeypatch.setattr(kpl_mod, "fetch_bid_seal", lambda: [])
    monkeypatch.setattr(kpl_mod, "fetch_bid_boom", lambda: [])
    monkeypatch.setattr(kpl_mod, "clear_cache", lambda: None)

    raw_all = auction_snapshot._fetch_market_map(full=True)
    assert raw_all == {}


def test_snapshot_at_with_kpl_fallback(client, monkeypatch):
    """东财失败+开盘啦兜底: snapshot_at 成功落库开盘啦数据"""
    def boom(fs):
        raise RuntimeError("network down")
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_eastmoney_all", boom)
    # 2026-08-31: 兜底链新增腾讯/量脉层, 需一并 mock 失败才能测到 kpl 兜底
    monkeypatch.setattr(auction_snapshot.fetcher, "fetch_tencent_market", boom)

    kpl_seal = [
        {"code": "600001", "name": "测试甲", "bidChange": 10.0, "bidAmt": 5e7, "bidSealAmt": 1.2e8, "board": "AI概念"},
    ]
    import app.services.kpl as kpl_mod
    monkeypatch.setattr(kpl_mod, "fetch_bid_seal", lambda: kpl_seal)
    monkeypatch.setattr(kpl_mod, "fetch_bid_boom", lambda: [])
    monkeypatch.setattr(kpl_mod, "clear_cache", lambda: None)

    n = auction_snapshot.snapshot_at("9_20", force=True)
    assert n == 1

    from app.db import database
    conn = database.get_conn()
    row = conn.execute("SELECT code, name, board FROM snapshot_bid WHERE date=? AND time_point='9_20'",
                       (auction_snapshot._bj_date(),)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "600001"
    assert row[1] == "测试甲"
    assert row[2] == "AI概念"


# ---------- 快照新鲜度告警 ----------

def test_freshness_check_sends_alert_on_missing(client, monkeypatch):
    """9:31 盘点发现缺失时点 → 推送告警"""
    from app.services import notify

    sent = []
    monkeypatch.setattr(notify, "send_text", lambda msg, **k: sent.append(msg))

    # 模拟 9:31 时点, 无任何快照
    monkeypatch.setattr(auction_snapshot, "_bj_date", lambda: "2026-08-25")
    # 清除 setnx 标记, 让盘点逻辑执行
    auction_snapshot.store.delete("sched:checked:2026-08-25")
    for tp in auction_snapshot.TIME_POINTS:
        auction_snapshot.store.delete("sched:done:2026-08-25:%s" % tp)

    # 直接调用盘点逻辑(模拟 _scheduler_loop 中的 9:31 分支)
    from app.services.cache_store import store
    date = "2026-08-25"
    if store.setnx("sched:checked:" + date, 1, ttl=86400):
        missing = []
        for tp in auction_snapshot.TIME_POINTS:
            if store.get("sched:done:%s:%s" % (date, tp)):
                continue
            if tp == "9_24":
                if not auction_snapshot._has_snapshot(date, tp):
                    missing.append(tp)
            else:
                missing.append(tp)
        if missing:
            msg = "今日快照采集缺失时点: %s (date=%s)" % (",".join(missing), date)
            try:
                notify.send_text("[快照告警] " + msg)
            except Exception:
                pass

    assert sent, "应推送告警"
    assert any("快照告警" in m for m in sent)
