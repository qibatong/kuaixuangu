# -*- coding: utf-8 -*-
"""超智研判（一期）聚合：口径纯函数 + 接口冒烟。

对应方案 `docs/超智研判-聚合页开发方案-20261001.md` §四（口径 A 案）与 §七（验收）。
重点钉住三件事：
  ① **不吃 aipick 配额**（接口只用 `get_uid`，没有 `quota_guard`）；
  ② 风险三档 / 标签规则按 A 案；
  ③ 单块失败不整页失败（`meta.notes` 如实降级）。
"""
from app.services import chaozhi


# ---------------- ① 情绪阶段（升温 / 分歧 / 转强） ----------------
def test_phase_zhuanqiang_when_fanbao_rate_high():
    """昨日炸板股今日回封率 ≥40% ⇒ 转强（优先级最高）"""
    s = [
        {"date": "d1", "zt": 30, "zb": 10, "zbRate": 25.0, "maxLb": 4, "fanbaoRate": 0},
        {"date": "d2", "zt": 20, "zb": 20, "zbRate": 50.0, "maxLb": 3, "fanbaoRate": 45.0},
    ]
    chaozhi._assign_phase(s)
    assert s[1]["phase"] == "转强"


def test_phase_shengwen_needs_two_improvements():
    """≥2 项改善（涨停数↑ / 炸板率↓ / 最高连板↑）⇒ 升温；否则分歧"""
    warm = [
        {"date": "d1", "zt": 20, "zb": 10, "zbRate": 33.3, "maxLb": 3, "fanbaoRate": 0},
        {"date": "d2", "zt": 30, "zb": 5, "zbRate": 14.3, "maxLb": 4, "fanbaoRate": 0},
    ]
    chaozhi._assign_phase(warm)
    assert warm[1]["phase"] == "升温"

    flat = [
        {"date": "d1", "zt": 20, "zb": 10, "zbRate": 33.3, "maxLb": 3, "fanbaoRate": 0},
        {"date": "d2", "zt": 20, "zb": 10, "zbRate": 33.3, "maxLb": 3, "fanbaoRate": 0},
    ]
    chaozhi._assign_phase(flat)
    assert flat[1]["phase"] == "分歧"
    assert flat[0]["phase"] == "分歧"


# ---------------- ② 风险三档 + 标签 ----------------
def test_risk_map_red_yellow_others(monkeypatch):
    monkeypatch.setattr(chaozhi.dev_risk, "load_warn_map",
                        lambda d=None: {"600000": {"level": "red"}, "000001": {"level": "yellow"}})
    m = chaozhi._risk_map("2026-10-01")
    assert m["600000"] == "high" and m["000001"] == "mid"


def test_tag_rules():
    assert chaozhi._tag(85, 82, "low") == "关注"      # 双 ≥80 且低风险
    assert chaozhi._tag(85, 50, "low") == "谨慎"      # 有一个 <60
    assert chaozhi._tag(85, 70, "mid") == "观察"      # 风险中
    assert chaozhi._tag(70, 65, "low") == "待定"      # 都在 60~80
    assert chaozhi._tag(95, 95, "high") == "谨慎"     # 高风险一律谨慎
    assert chaozhi._tag(None, 90, "low") == "观察"    # 单模型（另一个缺失）≥80


# ---------------- ③ 资金强度公式 ----------------
def test_capital_score_formula_and_clamp(monkeypatch):
    monkeypatch.setattr(chaozhi.kpl, "fetch_board_rank",
                        lambda *a, **k: {"list": [{"mainNet": 2e10}, {"mainNet": 0}]})
    import app.services.meoz_client as meoz
    monkeypatch.setattr(meoz, "emo_daily", lambda *a, **k: {"am": 1.5e12})
    v, detail = chaozhi.score_capital()
    assert 65 <= v <= 75, (v, detail)          # 中性偏强日落在 69 量级
    assert detail["amount"] == 1.5e12

    # 极端放量净流入 ⇒ 公式自然上限 = 50+40·tanh(∞) = 90（夹取上限 95 只是兜底护栏，正常到不了）
    monkeypatch.setattr(chaozhi.kpl, "fetch_board_rank", lambda *a, **k: {"list": [{"mainNet": 1e13}]})
    v2, _ = chaozhi.score_capital()
    assert 88 <= v2 <= 95, v2

    # 上游不可用 ⇒ None（前端显示 --，而不是 0 被误读成"极弱"）
    monkeypatch.setattr(chaozhi.kpl, "fetch_board_rank",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    v3, d3 = chaozhi.score_capital()
    assert v3 is None and d3.get("reason")


# ---------------- ④ 双模型合并 ----------------
def test_picks_merge_two_models_and_risk(monkeypatch):
    monkeypatch.setattr(chaozhi, "_pick_date", lambda models=("xgb", "lgb"): "2026-10-01")

    def fake_read(date, model):
        if model == "xgb":
            return {"all": [{"code": "600000", "name": "浦发银行", "ai_prob": 0.82, "bid_change": 1.2}]}
        return {"all": [{"code": "600000", "name": "浦发银行", "ai_prob": 0.71, "bid_change": 1.2},
                        {"code": "300001", "name": "特锐德", "ai_prob": 0.90, "bid_change": 3.3}]}

    monkeypatch.setattr(chaozhi, "_read_json", fake_read)
    monkeypatch.setattr(chaozhi, "_risk_map", lambda d: {"600000": "high"})
    picks, meta = chaozhi.load_picks()
    by = {p["code"]: p for p in picks}
    assert by["600000"]["scoreXgb"] == 82 and by["600000"]["scoreLgb"] == 71
    assert by["600000"]["risk"] == "high" and by["600000"]["tag"] == "谨慎"
    # 只有火眼的票也要出现（缺的模型为 None，而不是整只丢掉）
    assert by["300001"]["scoreLgb"] == 90 and by["300001"]["scoreXgb"] is None
    assert meta["models"]["xgb"] is True and meta["models"]["lgb"] is True


def test_picks_missing_model_is_reported_not_zero(monkeypatch):
    """火眼缺失 ⇒ meta.notes 说明，且分数为 None（**不能显示 0**）"""
    monkeypatch.setattr(chaozhi, "_pick_date", lambda models=("xgb", "lgb"): "2026-10-01")
    monkeypatch.setattr(chaozhi, "_read_json",
                        lambda date, model: {"all": [{"code": "600000", "name": "浦发银行",
                                                      "ai_prob": 0.82}]} if model == "xgb" else None)
    monkeypatch.setattr(chaozhi, "_risk_map", lambda d: {})
    picks, meta = chaozhi.load_picks()
    assert picks and picks[0]["scoreLgb"] is None
    assert any("火眼" in n for n in meta["notes"])


# ---------------- ⑤ 接口冒烟 ----------------
def test_overview_endpoint_ok_and_no_quota(client, first_user, monkeypatch):
    """接口 200 + 结构齐全；**不消耗 aipick 配额**（没有 quota_guard，免费用户也能调）"""
    token, _, _ = first_user
    r = client.get("/api/chaozhi/overview", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is True, d
    for k in ("scores", "emotion", "capital", "picks", "meta"):
        assert k in d, k
    assert set(["emotion", "capital", "promote", "support"]) <= set(d["scores"].keys())
    assert isinstance(d["emotion"]["series"], list)
    assert isinstance(d["capital"]["series"], list)
    assert isinstance(d["picks"], list)
    assert isinstance(d["meta"].get("notes"), list)


def test_overview_endpoint_requires_auth(client):
    r = client.get("/api/chaozhi/overview")
    assert r.status_code in (401, 403), r.status_code
