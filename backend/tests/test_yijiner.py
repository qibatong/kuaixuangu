# -*- coding: utf-8 -*-
"""竞价一进二(/api/yijiner)用例 —— 2026-09-28 新增。

覆盖:
  · 门禁: 未登录 401 / 免费试用 403(vip_required) / 付费会员 200
  · 首板口径: 剔除非首板(lb!=1)、剔除一字板(fb<=9:25:00)、剔除创业板/科创板
  · 评分: 四因子求和 + 红线封顶 45 + 可信度映射(逐字对齐网页版)
  · 已知缺陷的**有意行为**锁定: f4 被当昨收 ⇒ strength 的 price_stability 恒为 12
  · 过滤: 竞价涨幅 / 流通市值 / 股价 / 次新(f26)
  · 排序降序; 涨停池为空 → ok:false 而非抛错

取数一律打桩(fetcher.fetch_zt_pool / fetcher.fetch_raw_by_codes), 不打外网。
"""
import time

import pytest

from app.services import fetcher, yijiner

# ---------------- 打桩数据构造 ----------------
# 一条"能通过全部过滤且拿满分档"的基准行:
#   竞价涨幅 5.0 ∈ [3,8) / 流通 50 亿 ∈ [10,230] / 股价 20 ∈ [2,100]
#   f4=1.0 > 0 与 f5=1000 > 0  → 不被 is_suspended 判停牌
#   f5=1000, f10=2.0, f2=20 → bidTurn=(1000×20)/(2×100)=100 → 量比档 1.5(满档)
#   f26=20200101 → 上市已久, 不被次新剔除
def _row(code, name="测试股", price=20.0, real_change=5.0, f4=1.0, f5=1000.0,
         f10=2.0, open_=19.5, mv_yuan=5_000_000_000, industry="行业A",
         concept="概念A", bid_change=5.0, f26=20200101):
    return {
        "f12": code, "f14": name, "f2": price, "f3": real_change, "f4": f4,
        "f5": f5, "f10": f10, "f17": open_, "f21": mv_yuan, "f100": industry,
        "f103": concept, "f615": bid_change, "f26": f26,
    }


def _zt(lb=1, fb=100000, zbc=0, fund=1e8, zdp=10.0):
    """涨停池条目: fb=首封时间(HHMMSS, 100000=10:00:00 > 9:25:00 ⇒ 非一字板)。"""
    return {"lb": lb, "fb": fb, "zbc": zbc, "fund": fund, "zdp": zdp}


def _stub(monkeypatch, pool, rows, calls=None):
    def _fake_pool(date=None):
        if calls is not None:
            calls["pool_dates"].append(date)
        return pool

    def _fake_rows(codes, extra_fields=None):
        if calls is not None:
            calls["codes"] = list(codes)
            calls["extra_fields"] = extra_fields
        return rows

    monkeypatch.setattr(fetcher, "fetch_zt_pool", _fake_pool)
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _fake_rows)


def _auth(u):
    """带鉴权头。注意 conftest 的 first_user/second_user 是 **tuple** 而非 dict:
    first_user → (token, username, invite_code); second_user → (token, username)。"""
    tok = u[0] if isinstance(u, (tuple, list)) else u["token"]
    return {"Authorization": "Bearer " + tok}


# ---------------- 1. 门禁 ----------------
def test_requires_login(client):
    """未登录 → 401。"""
    r = client.get("/api/yijiner")
    assert r.status_code == 401


def test_free_user_forbidden(client, second_user):
    """免费试用(member_level=0) → 403 vip_required(与竞价异动同强度: 连数据都拿不到)。

    响应结构与既有 VIP 门禁一致(见 tests/test_kpl.py:1013):
        {"detail": {"ok": False, "code": "vip_required", "msg": "..."}}
    """
    r = client.get("/api/yijiner", headers=_auth(second_user))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "vip_required"
    assert "竞价一进二" in r.json()["detail"]["msg"]   # 文案须是功能名, 而非"竞价异动"


def test_paid_member_allowed(client, first_user, monkeypatch):
    """付费会员(member_level=1) → 200。"""
    _stub(monkeypatch, {"600001": _zt()}, [_row("600001")])
    r = client.get("/api/yijiner", headers=_auth(first_user))
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------- 2. 首板口径 ----------------
def test_only_first_board_lb1(client, first_user, monkeypatch):
    """连板数 != 1 的票不得进入名单(二板以上不是"一进二"的起点)。"""
    pool = {"600001": _zt(lb=1), "600002": _zt(lb=2), "600003": _zt(lb=3)}
    rows = [_row("600001"), _row("600002"), _row("600003")]
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert [x["code"] for x in d["list"]] == ["600001"]
    assert d["stats"]["firstBoard"] == 1


def test_one_word_board_excluded(client, first_user, monkeypatch):
    """首封 <= 9:25:00 视为一字板 → 剔除(网页版口径 fbt <= 92500)。"""
    pool = {"600001": _zt(fb=100000), "600002": _zt(fb=92500), "600003": _zt(fb=92459)}
    rows = [_row("600001"), _row("600002"), _row("600003")]
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert [x["code"] for x in d["list"]] == ["600001"]
    assert d["stats"]["oneWordDropped"] == 2


def test_main_board_only(client, first_user, monkeypatch):
    """创业板 300/301、科创板 688、北交所 8/4 一律剔除, 只留 60/00。"""
    codes = ["600001", "000002", "300003", "301004", "688005", "830006"]
    pool = {c: _zt() for c in codes}
    rows = [_row(c) for c in codes]
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert sorted(x["code"] for x in d["list"]) == ["000002", "600001"]


# ---------------- 3. 评分(对齐网页版) ----------------
def test_score_full_marks(client, first_user, monkeypatch):
    """基准行: 竞价涨幅 5.0 → 价格分 24; 量比满档 → 30; 强度 20; 板块 15 ⇒ 89 / 可信 88。

    强度分 20 = price_stability 12 + momentum 8 —— 其中 12 是 f4 被当昨收造成的
    "恒满分"(见 services/yijiner.py 头注释), 本用例同时锁定该**有意保留**的行为。
    """
    _stub(monkeypatch, {"600001": _zt()}, [_row("600001")])
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    it = d["list"][0]
    assert it["probability"] == 89
    assert it["confidence"] == 88
    assert it["redFlag"] is False


def test_strength_score_always_full_stability():
    """🔴 有意行为锁定: f4(涨跌额) 被当昨收 ⇒ distance_to_limit 恒 0 ⇒ price_stability 恒 12。

    量比为最低档 0.2 时 momentum=1.066… ⇒ strength = 12 + 1.066 = 13.066…
    若日后有人"顺手修正"为 f18, 本用例会红 —— 那是有意的提醒(需同步前端口径说明)。
    """
    assert yijiner.strength_score(price=10.0, f4=0.97, code="000678", ratio=0.2) == pytest.approx(12 + (0.2 / 1.5) * 8)
    assert yijiner.strength_score(price=10.0, f4=0.97, code="000678", ratio=1.5) == 20


def test_redline_caps_at_45(client, first_user, monkeypatch):
    """量比 < 0.3 触发红线 → 总分封顶 45(网页版 checkRedLine + min(total,45))。

    f5=1 / f10=100 / f2=20 ⇒ bidTurn=0.002 ⇒ 量比档 0.2(<0.3) ⇒ 红线。
    """
    _stub(monkeypatch, {"600001": _zt()}, [_row("600001", f5=1.0, f10=100.0)])
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    it = d["list"][0]
    assert it["redFlag"] is True
    assert it["probability"] == 45


def test_sorted_desc(client, first_user, monkeypatch):
    """按综合评分降序(同分按 code 升序, 保证稳定)。"""
    pool = {"600001": _zt(), "600002": _zt(), "600003": _zt()}
    rows = [_row("600001", bid_change=3.0),   # 价格分 18 → 总分低
            _row("600002", bid_change=7.0),   # 价格分 28 → 总分高
            _row("600003", bid_change=5.0)]   # 价格分 24 → 居中
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    probs = [x["probability"] for x in d["list"]]
    assert probs == sorted(probs, reverse=True)
    assert [x["code"] for x in d["list"]] == ["600002", "600003", "600001"]


# ---------------- 4. 过滤 ----------------
def test_filter_bid_range(client, first_user, monkeypatch):
    """竞价涨幅须落在 [3, 8) 半开区间。"""
    pool = {c: _zt() for c in ("600001", "600002", "600003", "600004")}
    rows = [_row("600001", bid_change=3.0),    # 含下界 → 保留
            _row("600002", bid_change=7.99),   # 未到上界 → 保留
            _row("600003", bid_change=2.99),   # 低于下界 → 剔除
            _row("600004", bid_change=8.0)]    # 含上界 → 剔除
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert sorted(x["code"] for x in d["list"]) == ["600001", "600002"]
    assert d["stats"]["dropped"].get("bid_range") == 2


def test_filter_mv_and_price(client, first_user, monkeypatch):
    """流通市值 [10,230] 亿(闭区间)、股价 [2,100] 元(闭区间)。"""
    pool = {c: _zt() for c in ("600001", "600002", "600003", "600004")}
    rows = [
        _row("600001", mv_yuan=10 * 10**8, price=2.0),      # 双下界 → 保留
        _row("600002", mv_yuan=230 * 10**8, price=100.0),   # 双上界 → 保留
        _row("600003", mv_yuan=9 * 10**8),                  # 市值过小 → 剔除
        _row("600004", price=101.0),                        # 股价过高 → 剔除
    ]
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert sorted(x["code"] for x in d["list"]) == ["600001", "600002"]
    dropped = d["stats"]["dropped"]
    assert dropped.get("mv_range") == 1 and dropped.get("price_range") == 1


def test_filter_new_stock(client, first_user, monkeypatch):
    """上市 < 60 天次新剔除(f26 需经 extra_fields 追加请求)。"""
    pool = {"600001": _zt(), "600002": _zt()}
    today = time.strftime("%Y%m%d")
    rows = [_row("600001", f26=20200101), _row("600002", f26=int(today))]  # 今天上市
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert [x["code"] for x in d["list"]] == ["600001"]
    assert d["stats"]["dropped"].get("new_stock") == 1


def test_extra_fields_asks_for_f26(client, first_user, monkeypatch):
    """回归防线: 取候选行情时必须追加 f26(否则次新过滤静默失效)。"""
    calls = {"pool_dates": [], "codes": None, "extra_fields": None}
    _stub(monkeypatch, {"600001": _zt()}, [_row("600001")], calls)
    client.get("/api/yijiner", headers=_auth(first_user))
    assert calls["extra_fields"] == "f26"
    assert calls["codes"] == ["600001"]


def test_st_excluded(client, first_user, monkeypatch):
    """ST 股剔除(stSuspend 默认开)。"""
    pool = {"600001": _zt(), "600002": _zt()}
    rows = [_row("600001"), _row("600002", name="ST测试")]
    _stub(monkeypatch, pool, rows)
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert [x["code"] for x in d["list"]] == ["600001"]


# ---------------- 5. 边界 / 降级 ----------------
def test_empty_pool_returns_error_not_raise(client, first_user, monkeypatch):
    """涨停池全空(长假/数据源异常) → ok:false + 空 list, 不抛 500。"""
    _stub(monkeypatch, {}, [])
    r = client.get("/api/yijiner", headers=_auth(first_user))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["list"] == [] and d["count"] == 0


def test_quote_fetch_failure_returns_error(client, first_user, monkeypatch):
    """行情点查抛错 → ok:false, 不抛 500。"""
    def _boom(codes, extra_fields=None):
        raise RuntimeError("东财 ulist 点查失败")

    monkeypatch.setattr(fetcher, "fetch_zt_pool", lambda date=None: {"600001": _zt()})
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _boom)
    r = client.get("/api/yijiner", headers=_auth(first_user))
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_payload_shape(client, first_user, monkeypatch):
    """输出字段完整性(前端渲染依赖这些 key)。"""
    _stub(monkeypatch, {"600001": _zt(lb=1, fb=100000, zbc=2, fund=12345678)}, [_row("600001")])
    d = client.get("/api/yijiner", headers=_auth(first_user)).json()
    assert d["strategy"] == "yijiner"
    assert d["dataDate"].isdigit() and len(d["dataDate"]) == 8
    it = d["list"][0]
    for k in ("code", "name", "probability", "confidence", "redFlag", "bidChange",
              "realChange", "entityChange", "circulationMV", "price", "industry",
              "concept", "limitBoards", "firstSealTime", "breakCount", "sealFund"):
        assert k in it, "缺字段 " + k
    assert it["limitBoards"] == 1 and it["breakCount"] == 2 and it["firstSealTime"] == 100000
    assert d["filters"]["bidMin"] == 3.0 and d["filters"]["bidMax"] == 8.0
