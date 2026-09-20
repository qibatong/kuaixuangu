# -*- coding: utf-8 -*-
"""2026-09-08 快照候选池适用时段扩展(主人核心诉求: 同条件名单波动 + 加载慢):
9:15 前(凌晨/盘前)的 lock/filter 从「ensure_cache 实时拉全市场」切到「9:25 定格
快照候选池(load_snapshot_full 自动回退最近交易日)」。

背景: 9/8 07:26-07:29 生产实测同 filters 连续 lock 名单 30→13 波动(交集仅 13/30) —
凌晨当日无 9_25 快照也无实时竞价, 实时全市场只拿到昨日收盘缓存, 且双 uvicorn worker
缓存不一致(raw 5548↔5556)+昨日额命中爬坡 → 边界票进出(大跌票「有概率」混入);
28 页拉取也让加载高达 5-15s。快照池路径: 候选固定 → 名单幂等稳定; 点查仅几十只 → <3s。
9:15-9:30 竞价窗口内仍走实时(当日动态竞价只能走实时源)。
"""
import pytest

from app.services import auction_snapshot, fetcher, scorer

# 与 test_stocks.py 同款: conftest 的 MOCK_RAW(全通过默认筛选的行情行)
from conftest import MOCK_RAW


def _snap_rows():
    """构造 load_snapshot_full 返回值 {code: {快照行}} — 600001 为合格候选
    (free_mv 单位元, bid_amt 单位万元, 均同 snapshot_bid 表语义)"""
    return {"600001": {"name": "测试甲", "bid_change": 3.5, "bid_amt": 5000.0,
                       "free_mv": 500.0 * 1e8}}


def _url(action):
    # ⚠️ scoreFloor=0: 本组测试的**验证对象是「路径选择」**(凌晨走快照池点查, 不走实时
    #   全市场), 不是评分高低。而 2026-09-10 上线的全站默认 scoreFloor=80(见 commit
    #   9d290e4) 会把「只有 9:25 定格字段」的合成候选(评分 25~56 分)整批砍掉 →
    #   名单恒空 → `"600001" in codes` 断言必败(2026-09-20 排查确认: 属**测试陈旧**,
    #   非生产缺陷 —— 该 commit 未同步更新本文件)。故这里显式关闭评分下限,
    #   让路径选择断言回到被测行为本身。点查失败降级快照行直出的用例(另有 score_floor
    #   _exempt 豁免机制)不受本改动影响。
    return ("/api/stocks?action=%s&strategy=auction&markets=hs,cyb,kcb&bidGt=7&probLt=65"
            "&confLt=65&floatMvGt=1000&priceGt=300&bidAmtFloor=3000&scoreFloor=0" % action)


def _mock_8am(monkeypatch):
    """凌晨 8:00(9:15 前, 当日无快照/无竞价) — 覆盖 session 默认 9:25"""
    monkeypatch.setattr(scorer, "bj_now", lambda: (8, 0, True))
    monkeypatch.setattr(scorer, "_bj_hm", lambda: 8 * 60)
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)


def _mock_snap_pool(monkeypatch):
    """桩掉快照候选池路径的全部数据源, 记录路径选择(ensure vs 点查)"""
    calls = {"ensure": 0, "point": 0}
    monkeypatch.setattr(auction_snapshot, "load_snapshot_full", lambda *a, **kw: _snap_rows())
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_amt",
                        lambda: {"600001": 5000.0})     # 万元, ≥bidAmtFloor
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change",
                        lambda: {"600001": 3.5})

    def fake_ensure(action, fs, before930):
        # 与 conftest mock_data_source / fetcher.ensure_cache 同款业务拒绝
        if action == "lock" and not before930:
            return None, "9:30 后禁止重新选股"
        calls["ensure"] += 1
        return [], None
    monkeypatch.setattr(fetcher, "ensure_cache", fake_ensure)

    def fake_point(codes):
        calls["point"] += 1
        return [dict(MOCK_RAW[0])]                       # 600001 行情行(东财 ulist 同构)
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", fake_point)
    return calls


def _get(client, token, action):
    return client.get(_url(action), headers={"Authorization": "Bearer " + token})


def test_8am_filter_uses_snap_pool(client, create_user_token, monkeypatch):
    """核心: 凌晨 8:00 action=filter → 走快照候选池(点查), 不拉实时全市场"""
    _mock_8am(monkeypatch)
    calls = _mock_snap_pool(monkeypatch)
    u = create_user_token()
    r = _get(client, u["token"], "filter")
    d = r.json()
    assert r.status_code == 200 and d.get("ok"), d
    codes = {s["code"] for s in d["list"]}
    assert "600001" in codes, "快照池点查的合格候选应入选"
    assert calls["point"] >= 1, "凌晨 filter 应走 fetch_raw_by_codes 点查"
    assert calls["ensure"] == 0, "凌晨 filter 不应走 ensure_cache 实时全市场(波动+慢的根因)"


def test_8am_lock_uses_snap_pool(client, create_user_token, monkeypatch):
    """9:30 前 lock(研究锁定, 主人 07:26-07:29 的真实操作)同样走快照池 → 名单幂等"""
    from app.services import notify, stats
    _mock_8am(monkeypatch)
    calls = _mock_snap_pool(monkeypatch)
    monkeypatch.setattr(notify, "push_result_async", lambda result, f: None)
    monkeypatch.setattr(stats, "record_daily_yizi", lambda raw: {"yizi_count": 0})
    u = create_user_token()
    r = _get(client, u["token"], "lock")
    d = r.json()
    assert r.status_code == 200 and d.get("ok"), d
    codes = {s["code"] for s in d["list"]}
    assert "600001" in codes
    assert calls["point"] >= 1, "凌晨 lock 应走快照池点查"
    assert calls["ensure"] == 0, "凌晨 lock 不应走 ensure_cache 实时全市场"


def test_after930_lock_still_rejected(client, create_user_token, monkeypatch):
    """9:30 后 lock 仍被拒(403) — 快照池不得绕过 ensure_cache 的业务拒绝"""
    from app.services import auction_snapshot as _as
    monkeypatch.setattr(scorer, "bj_now", lambda: (15, 0, False))
    monkeypatch.setattr(scorer, "_bj_hm", lambda: 15 * 60)
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)
    calls = _mock_snap_pool(monkeypatch)     # 若 lock 误走快照池, point 会被触发
    u = create_user_token()
    r = _get(client, u["token"], "lock")
    assert r.status_code == 403, "9:30 后 lock 应被 ensure_cache 拒绝(403)"
    assert calls["point"] == 0, "9:30 后 lock 不得走快照池绕过业务拒绝"


# ========== 2026-09-08 方案 A: 点查失败降级快照行直出(不再降级实时全市场) ==========
def _snap_rows_broad():
    """多候选快照: 600001 合格 / 600003 竞额不足(粗筛剔除) / 600004 竞价>bidGt(粗筛剔除)"""
    return {
        "600001": {"name": "测试甲", "bid_change": 3.5, "bid_amt": 5000.0,
                   "free_mv": 500.0 * 1e8},
        "600003": {"name": "测试丙", "bid_change": 2.0, "bid_amt": 100.0,
                   "free_mv": 500.0 * 1e8},
        "600004": {"name": "测试丁", "bid_change": 9.0, "bid_amt": 9000.0,
                   "free_mv": 500.0 * 1e8},
    }


def _mock_8am_point_fail(monkeypatch):
    """8:00 + 快照池数据源 + fetch_raw_by_codes 抛网络异常(东财 ulist 断连)"""
    _mock_8am(monkeypatch)
    calls = {"ensure": 0, "point": 0}

    def fake_ensure(action, fs, before930):
        if action == "lock" and not before930:
            return None, "9:30 后禁止重新选股"
        calls["ensure"] += 1
        return [], None
    monkeypatch.setattr(fetcher, "ensure_cache", fake_ensure)
    monkeypatch.setattr(auction_snapshot, "load_snapshot_full", lambda *a, **kw: _snap_rows_broad())
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_amt",
                        lambda: {"600001": 5000.0, "600004": 9000.0})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change",
                        lambda: {"600001": 3.5, "600004": 9.0})

    def fake_point(codes):
        calls["point"] += 1
        raise RuntimeError("Remote end closed connection without response")
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", fake_point)
    # 腾讯点查同样失败 —— 否则补丁源会成功拿到实时行情, 走不到"快照行直出"兜底
    def fake_tx(codes):
        raise RuntimeError("腾讯数据源熔断中")
    monkeypatch.setattr(fetcher, "fetch_tencent_by_codes", fake_tx)
    return calls


def test_point_fail_filter_falls_back_to_snapshot_rows(client, create_user_token, monkeypatch):
    """方案 A 核心: 点查失败 → 9:25 快照行直出名单, 绝不降级实时全市场(波动根因)"""
    calls = _mock_8am_point_fail(monkeypatch)
    u = create_user_token()
    r = _get(client, u["token"], "filter")
    d = r.json()
    assert r.status_code == 200 and d.get("ok"), d
    codes = {s["code"] for s in d["list"]}
    assert "600001" in codes, "点查失败降级后合格快照候选应直出入选"
    assert "600003" not in codes, "竞额<门槛的粗筛剔除票不得出现(直出集合=粗筛候选)"
    assert "600004" not in codes, "竞价涨幅>bidGt 的粗筛剔除票不得出现"
    assert calls["point"] >= 1, "点查应被触发(随后失败)"
    assert calls["ensure"] == 0, "点查失败不得降级 ensure_cache 实时全市场"
    # 降级行用定格值(非退化): 直出行 bidChange=9:25 定格 3.5, bidAmt=5000(定格)
    hit = [s for s in d["list"] if s["code"] == "600001"][0]
    assert abs(hit["bidChange"] - 3.5) < 1e-6, "直出名单 bidChange 应为快照定格 3.5"
    assert abs(hit["bidAmt"] - 5000.0) < 1e-6, "直出名单 bidAmt 应为快照定格 5000(万元)"
    assert hit["realChange"] == 0.0 or hit["realChange"] is None, "降级行无实时现涨(不退化显示)"


def test_point_fail_lock_falls_back_to_snapshot_rows(client, create_user_token, monkeypatch):
    """9:30 前 lock 同场景: 点查失败 → 快照行直出(主人 07:26-07:29 真实操作路径)"""
    from app.services import notify, stats
    calls = _mock_8am_point_fail(monkeypatch)
    monkeypatch.setattr(notify, "push_result_async", lambda result, f: None)
    monkeypatch.setattr(stats, "record_daily_yizi", lambda raw: {"yizi_count": 0})
    u = create_user_token()
    r = _get(client, u["token"], "lock")
    d = r.json()
    assert r.status_code == 200 and d.get("ok"), d
    codes = {s["code"] for s in d["list"]}
    assert "600001" in codes
    assert calls["ensure"] == 0, "lock 点查失败也不得降级 ensure_cache 实时全市场"
