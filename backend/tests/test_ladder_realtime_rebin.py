# -*- coding: utf-8 -*-
"""连板梯队实时路径 rebin 测试(2026-09-07 主人反馈"龙版传媒是 6 连板不是 5 连板")

背景: 开盘啦 DailyLimitPerformance 的 PidType 只分到 **五板+(pid=5)**, 6 板以上全部
      塞进第 5 档 → 前端显示"五板+"/5 板。历史回看(date 指定)早已用 rebin_ladder
      (东财涨停池真实 limitUpDays)拆成 1~8 档, **实时路径漏了这一步**。
      生产实测: 龙版传媒 605577 东财 limitUpDays=6, 开盘啦给 pid=5, rebin 后落第 6 档。
"""


def test_realtime_ladder_rebins_high_boards(client, create_user_token, monkeypatch):
    """实时(date 空)也应 rebin: 6 板票落第 6 档, 不再归到"五板+\""""
    from app.services import kpl

    u = create_user_token()
    client.headers["Authorization"] = "Bearer " + u["token"]
    # 开盘啦: 龙版传媒放在五板+档(pid=5), 美格智能首板(pid=1)
    monkeypatch.setattr(kpl, "fetch_ladder_all", lambda: {
        1: [{"code": "002881", "name": "美格智能"}],
        5: [{"code": "605577", "name": "龙版传媒"}],
    })
    # 东财涨停池真实连板数
    monkeypatch.setattr(kpl, "real_limit_days", lambda date: {
        "605577": 6, "002881": 1})
    # 涨幅 merge 走行情源, 测试里桩空避免外网
    from app.services import fetcher
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})

    r = client.get("/api/kpl/ladder")
    assert r.status_code == 200
    d = (r.json() or {}).get("ladder") or {}
    codes_in = lambda pid: [x.get("code") for x in (d.get(str(pid)) or d.get(pid) or [])]
    assert "605577" in codes_in(6), f"6 板应落第 6 档, 实际各档: { {k: codes_in(k) for k in d} }"
    assert "605577" not in codes_in(5), "不应再留在第 5 档(五板+)"
    assert "002881" in codes_in(1), "首板不受影响"


def test_realtime_ladder_falls_back_when_real_missing(client, create_user_token, monkeypatch):
    """东财涨停池不可用(real_limit_days 空) → 原样返回 5 档结构, 不丢股"""
    from app.services import kpl
    from app.services import fetcher

    u = create_user_token()
    client.headers["Authorization"] = "Bearer " + u["token"]
    monkeypatch.setattr(kpl, "fetch_ladder_all", lambda: {
        5: [{"code": "605577", "name": "龙版传媒"}]})
    monkeypatch.setattr(kpl, "real_limit_days", lambda date: {})
    monkeypatch.setattr(fetcher, "fetch_spot_quote_map", lambda fs: {})

    r = client.get("/api/kpl/ladder")
    d = (r.json() or {}).get("ladder") or {}
    all_codes = [x.get("code") for pid in d for x in (d[pid] or [])]
    assert "605577" in all_codes, "东财缺失时仍应保留该股(以开盘啦档位为准)"
