# -*- coding: utf-8 -*-
"""竞价异动历史快照: 落库/回看/API date 参数"""
import sys
sys.path.insert(0, "backend")

from app.services import kpl
from app.services.kpl import save_auction_history, query_auction_history


def _hdrs(token):
    return {"Authorization": "Bearer " + token}


# ---------- 落库 / 回看 ----------
def test_save_and_query_auction_history(client, monkeypatch):
    """save_auction_history 落库, query_auction_history 回看"""
    fake = [{"code": "600487", "name": "亨通光电", "change": 10.01}]
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda *a, **k: {"list20": fake})
    monkeypatch.setattr(kpl, "fetch_yest_zt", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_yest_broken", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_broken_zt", lambda *a, **k: fake)

    nb = save_auction_history("2026-08-12", phase="bid")
    nc = save_auction_history("2026-08-12", phase="close")
    assert nb >= 3   # seal/boom/qiangcang
    assert nc >= 3   # yest_zt/yest_broken/broken_*
    # 回看各 tab
    assert query_auction_history("2026-08-12", "seal")[0]["code"] == "600487"
    assert query_auction_history("2026-08-12", "boom")[0]["name"] == "亨通光电"
    assert query_auction_history("2026-08-12", "yest_zt")[0]["code"] == "600487"
    # 无数据日期返回空
    assert query_auction_history("2020-01-01", "seal") == []


# ---------- API date 参数 ----------
def _mock_vip(client):
    """竞价异动接口 v4.1 起要求 VIP/付费会员, 测试 mock 权限"""
    from app.api import deps
    client.app.dependency_overrides[deps.require_vip_or_paid] = lambda: 1


def test_api_bid_seal_date(client, first_user, monkeypatch):
    """bid-seal?date= 读历史表"""
    _mock_vip(client)
    token, _, _ = first_user
    fake = [{"code": "600487", "name": "亨通光电", "change": 10.01}]
    monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_bid_boom", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_bid_qiangcang", lambda *a, **k: {"list20": []})
    save_auction_history("2026-08-12", phase="bid")
    r = client.get("/api/kpl/bid-seal?date=2026-08-12", headers=_hdrs(token))
    d = r.json()
    assert d.get("ok") and d["list"][0]["code"] == "600487"
    assert d.get("date") == "2026-08-12"
    # 周六自动对齐(需要 snapshot_bid 有数据, 用无数据日期验证不崩)
    r2 = client.get("/api/kpl/bid-seal?date=2026-08-15", headers=_hdrs(token))
    assert r2.status_code == 200


def test_api_yest_zt_date(client, first_user, monkeypatch):
    """yest-zt?date= 读历史表"""
    _mock_vip(client)
    token, _, _ = first_user
    fake = [{"code": "600519", "name": "贵州茅台", "change": 5.0}]
    monkeypatch.setattr(kpl, "fetch_yest_zt", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_yest_broken", lambda: fake)
    monkeypatch.setattr(kpl, "fetch_broken_zt", lambda *a, **k: [])
    save_auction_history("2026-08-13", phase="close")
    r = client.get("/api/kpl/yest-zt?date=2026-08-13", headers=_hdrs(token))
    d = r.json()
    assert d.get("ok") and d["list"][0]["code"] == "600519"
