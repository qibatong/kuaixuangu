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
    """标签 = 综合分 + 模型一致性 + 风险（2026-10-02 升级；见方案 §10.2）"""
    assert chaozhi._tag(85, 82, 80, 0.10, "low") == "关注"    # 综合≥80 且分歧小 且低风险
    assert chaozhi._tag(85, 82, 80, 0.40, "low") == "观察"    # 分歧偏大 ⇒ 降为观察
    assert chaozhi._tag(85, 90, 50, 0.60, "low") == "谨慎"    # 分歧 ≥0.5 ⇒ 谨慎
    assert chaozhi._tag(85, 50, 50, 0.10, "low") == "谨慎"    # 任一模型 <60
    assert chaozhi._tag(85, 70, 70, 0.10, "mid") == "观察"    # 风险中
    assert chaozhi._tag(60, 65, 62, 0.10, "low") == "待定"    # 综合 55~70
    assert chaozhi._tag(40, 45, 42, 0.10, "low") == "谨慎"    # 综合 <55
    assert chaozhi._tag(95, 95, 95, 0.00, "high") == "谨慎"   # 高风险一律谨慎
    # 🔴 只有单模型有分（divergence=None）⇒ **不能**判"关注"（证据不足）⇒ 最高只到观察
    assert chaozhi._tag(95, 90, None, None, "low") == "观察"


def test_fuse_rank_equal_weight_and_divergence():
    """综合分 = 当日百分位加权（等权）；分歧度 = |rank差|；并列取平均名次"""
    rows = [
        {"code": "A", "scoreXgb": 90, "scoreLgb": 90},   # 两模型都是最高 ⇒ 综合 100、分歧 0
        {"code": "B", "scoreXgb": 80, "scoreLgb": 50},   # 金睛第2/火眼最低 ⇒ 分歧大
        {"code": "C", "scoreXgb": 70, "scoreLgb": 70},   # 两模型都最低 ⇒ 综合 0、分歧 0
    ]
    chaozhi.fuse(rows)
    by = {r["code"]: r for r in rows}
    assert by["A"]["scoreFused"] == 100 and by["A"]["divergence"] == 0
    # 三只票 ⇒ 名次百分位 = 0 / 0.5 / 1.0；B 的 rankXgb=0.5、rankLgb=0 ⇒ 综合 25、分歧 0.5
    assert by["C"]["scoreFused"] == 25 and by["C"]["divergence"] == 0.5
    assert by["B"]["scoreFused"] == 25                      # (0.5 + 0.0)/2 ⇒ 25
    assert by["B"]["divergence"] == 0.5                     # |0.5 - 0.0|
    assert by["A"]["rankXgb"] == 1.0 and by["C"]["rankXgb"] == 0.0


def test_picks_fused_spread_within_candidate_pool(monkeypatch):
    """🔴 综合分的百分位在**候选池内**算 ⇒ 60 只展示项应铺开（而不是全挤在 99~100）

    实测踩到: 在全市场 ~5500 只里算百分位，前 60 名全是 99~100 分，综合分失去区分度。
    """
    monkeypatch.setattr(chaozhi, "_pick_date", lambda *a, **k: "2026-10-01")
    monkeypatch.setattr(chaozhi, "_risk_map", lambda d: {})
    rows = [{"code": "%06d" % i, "name": "股%d" % i, "ai_prob": (600 - i) / 1000.0}
            for i in range(600)]                      # 600 只候选(模拟全市场大池)
    monkeypatch.setattr(chaozhi, "_read_json",
                        lambda date, model: {"all": rows} if model == "xgb" else None)
    picks, _ = chaozhi.load_picks(top=60)
    fused = [p["scoreFused"] for p in picks]
    # ⚠️ 最高不一定是 100：**并列名次取平均名次**会把顶端拉低（本题假数据有大量同分）
    assert max(fused) >= 90 and min(fused) < 60, (max(fused), min(fused))
    assert fused == sorted(fused, reverse=True)


def test_picks_prefer_top_over_all_and_mark_change_kind(monkeypatch):
    """🔴 2026-10-02 主人反馈「数据读不对」的根因回归：

    ① 同时存在 top（过滤后候选）与 all（过滤前全量）时**必须只用 top** ——
       否则竞价涨幅 9.9~10.9% 的票（**竞价就涨停、根本买不进**）会混进名单，
       且本页 60 只 / 金睛火眼 30 只 ⇒ 名单对不上（机器实测 all 里 >7% 的有 15 只）。
    ② 涨幅必须带**口径标记**：原始预测文件里只有 `bid_change`（9:25 竞价涨幅）
       ⇒ changeKind=='bid'，前端据此显示"竞价"，不许裸显示数字（否则被读成"当前涨幅"）。
    ③ `yesterday_chg` 名字骗人（实测 09-28 版数值==竞价涨幅、09-30 版==当日收盘涨幅，
       半夜 backfill 跑出来的是"未来数据"）⇒ 必须忽略，绝不拿来当涨幅。
    """
    monkeypatch.setattr(chaozhi, "_pick_date", lambda *a, **k: "2026-10-02")
    monkeypatch.setattr(chaozhi, "_risk_map", lambda d: {})
    top = [{"code": "600000", "name": "浦发银行", "ai_prob": 0.92, "bid_change": 1.5}]
    al = top + [{"code": "300001", "name": "特锐德", "ai_prob": 0.99, "bid_change": 10.04}]

    # ① 只用 top
    monkeypatch.setattr(chaozhi, "_read_json",
                        lambda date, model: {"top": top, "all": al} if model == "xgb" else None)
    picks, _ = chaozhi.load_picks(top=60)
    assert [p["code"] for p in picks] == ["600000"], [p["code"] for p in picks]
    assert picks[0]["change"] == 1.5
    # ② 口径标记 = 竞价
    assert picks[0]["changeKind"] == "bid"

    # ② 接口层若注入实时涨幅 ⇒ 值与该用优先，且标记跟着变
    monkeypatch.setattr(chaozhi, "_read_json",
                        lambda date, model: {"top": [dict(top[0], realTime=3.3)]} if model == "xgb" else None)
    picks2, _ = chaozhi.load_picks(top=60)
    assert picks2[0]["change"] == 3.3 and picks2[0]["changeKind"] == "realtime"

    # ③ yesterday_chg 必须被忽略（它既不是昨涨幅、也不该当涨幅）
    monkeypatch.setattr(chaozhi, "_read_json",
                        lambda date, model: {"top": [dict(top[0], yesterday_chg=9.99)]} if model == "xgb" else None)
    picks3, _ = chaozhi.load_picks(top=60)
    assert picks3[0]["change"] == 1.5, picks3[0]["change"]


def test_fuse_single_model_degrades_to_that_model():
    """某模型整日缺失 ⇒ 综合分 = 另一个模型的当日百分位（按可用权重归一），divergence=None"""
    rows = [{"code": "A", "scoreXgb": 90, "scoreLgb": None},
            {"code": "B", "scoreXgb": 60, "scoreLgb": None}]
    chaozhi.fuse(rows)
    assert rows[0]["scoreFused"] == 100 and rows[1]["scoreFused"] == 0
    assert rows[0]["rankLgb"] is None and rows[0]["divergence"] is None


def test_fuse_ties_and_tiny_pool():
    """并列取平均名次；样本只有 1 只时给 0.5（避免除以 0）"""
    rows = [{"code": "A", "scoreXgb": 80, "scoreLgb": 80},
            {"code": "B", "scoreXgb": 80, "scoreLgb": 60}]
    chaozhi.fuse(rows)
    assert rows[0]["rankXgb"] == rows[1]["rankXgb"] == 0.5   # 并列 ⇒ 平均名次 0.5
    one = [{"code": "X", "scoreXgb": 77, "scoreLgb": 77}]
    chaozhi.fuse(one)
    assert one[0]["scoreFused"] == 50


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
    monkeypatch.setattr(chaozhi, "_pick_date", lambda *a, **k: "2026-10-01")

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
    monkeypatch.setattr(chaozhi, "_pick_date", lambda *a, **k: "2026-10-01")
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
