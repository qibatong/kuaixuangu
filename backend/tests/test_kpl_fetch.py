# -*- coding: utf-8 -*-
"""kpl 服务层具名 fetch 函数: mock _call 测 loader 解析/字段映射/降级"""
import pytest

from app.services import kpl


@pytest.fixture(autouse=True)
def _clear_kpl_cache():
    """fetch_* 用模块级 _cached 缓存, 不同测试 mock 不同 _call, 必须先清缓存避免相互污染"""
    kpl.clear_cache()
    yield
    kpl.clear_cache()


def _row(*vals):
    return list(vals)


# ---------- 连板梯队 ----------
def test_fetch_ladder_parses(monkeypatch):
    """fetch_ladder: mock _call 返回开盘啦 info, 校验字段映射与楼层标签"""
    # info 真实结构: [ [row1, row2, ...] ] 双层嵌套
    row = [_row("600001", "测A", "1", "首板涨停", 1690000000, "机器人", 1.2e8, 2.0e8,
                3.0e7, 1.0e7, 0.5e7, 5.0e8, "概念X", 8.0e9, 25.5, "", "", "", "",
                "", "801000", 3, "1", "5.2", "6.1")]
    info = [[row[0]]]
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"info": info, "tip": ""})
    rows = kpl.fetch_ladder(1)
    assert rows and rows[0]["code"] == "600001"
    assert rows[0]["ladder"] == 1 and rows[0]["ladderLabel"] == "首板"
    assert rows[0]["seal"] == 1.2e8
    assert rows[0]["concept"] == "概念X"
    assert rows[0]["turnover"] == 25.5


def test_fetch_ladder_bad_info(monkeypatch):
    """info 非 list → 空列表"""
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"info": "bad"})
    assert kpl.fetch_ladder(1) == []


def test_fetch_ladder_row_too_short(monkeypatch):
    """行长度不足 → 跳过"""
    monkeypatch.setattr(kpl, "_call",
                        lambda host, params: {"info": [(1, 2, 3)]})
    assert kpl.fetch_ladder(1) == []


def test_fetch_ladder_all(monkeypatch):
    """fetch_ladder_all 聚合 5 档"""
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"info": []})
    d = kpl.fetch_ladder_all()
    assert set(d.keys()) == {1, 2, 3, 4, 5}
    for v in d.values():
        assert isinstance(v, list)


# ---------- 连板梯队 rebin(五板+ 拆 6/7/8+ 档) ----------
def _ladder_5tier():
    def mk(code, name):
        return {"code": code, "name": name}
    return {
        1: [mk("600001", "首板A"), mk("600002", "首板B")],
        2: [mk("600003", "二板A")],
        3: [],
        4: [mk("600004", "四板A")],
        5: [mk("600005", "六板A"), mk("600006", "七板A"), mk("600007", "八板A")],
    }


def test_rebin_ladder_splits_height(monkeypatch):
    """五板+ 按东财真实连板拆为 6/7/8 档, 超过 8 板归八板+"""
    monkeypatch.setattr(kpl, "real_limit_days",
                        lambda date: {"600005": 6, "600006": 7, "600007": 12, "600002": 2})
    out = kpl.rebin_ladder(_ladder_5tier(), "2026-09-01")
    assert set(out.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}
    assert [it["code"] for it in out[5]] == []
    assert [it["code"] for it in out[6]] == ["600005"]
    assert [it["code"] for it in out[7]] == ["600006"]
    assert [it["code"] for it in out[8]] == ["600007"]        # 12 板 → 八板+
    assert [it["code"] for it in out[2]] == ["600002", "600003"]  # 首板 600002 真实 2 板 → 升档
    assert [it["code"] for it in out[1]] == ["600001"]


def test_rebin_ladder_fallback_when_empty(monkeypatch):
    """东财数据源失败(空 dict) → 原样返回 5 档结构, 前端兼容"""
    monkeypatch.setattr(kpl, "real_limit_days", lambda date: {})
    d = _ladder_5tier()
    out = kpl.rebin_ladder(d, "2026-09-01")
    assert out is d
    assert set(out.keys()) == {1, 2, 3, 4, 5}


def test_rebin_ladder_keeps_missing_codes(monkeypatch):
    """东财缺失的股票保持 pid 档位(不丢股)"""
    monkeypatch.setattr(kpl, "real_limit_days", lambda date: {"600005": 6})
    out = kpl.rebin_ladder(_ladder_5tier(), "2026-09-01")
    assert [it["code"] for it in out[6]] == ["600005"]
    assert [it["code"] for it in out[5]] == ["600006", "600007"]  # 无东财数据 → 留在五板


# ---------- 板块强度 / 成分 ----------
def test_fetch_board_rank_parses(monkeypatch):
    lst = [_row("801001", "芯片", 100.5, 3.2, 1.5, 5.0e9, 2.0e8, 1.0e8, 1.0e8,
                2.1, 1.5e10, 0, 0, 3.0e10, 5.0e7, 30.5, 28.0)]
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": lst})
    rows = kpl.fetch_board_rank()
    assert rows and rows[0]["name"] == "芯片"
    assert rows[0]["strength"] == 100.5 and rows[0]["change"] == 3.2
    assert rows[0]["totalMv"] == 3.0e10


def test_fetch_board_rank_by_date(monkeypatch):
    lst = [_row("801001", "芯片", 90.0, 2.0, 0.5, 1.0e9, 1.0e8, 0.5e8, 0.5e8,
                1.0, 2.0e10, 0, 0, 4.0e10, 1.0e7, 40.0, 38.0)]
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": lst})
    rows = kpl.fetch_board_rank_by_date("2026-08-20")
    assert rows and rows[0]["name"] == "芯片"


def test_fetch_board_rank_none(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda host, params: None)
    assert kpl.fetch_board_rank() is None


def test_fetch_board_stocks_parses(monkeypatch):
    # index: 0=code 1=name 4=concept 5=price 6=change 7=amount 10=floatMv 11=mainNet
    #        21=volRatio 23=limitTag 24=ladder 25=turnover 38=totalMv
    row = [""] * 39
    row[0] = "600001"; row[1] = "测A"; row[4] = "概念"; row[5] = 18.5
    row[6] = 9.9; row[7] = 2.0e8; row[10] = 5.0e9; row[11] = 3.0e7
    row[21] = 2.3; row[23] = "首板"; row[24] = "龙一"; row[25] = 26.9; row[38] = 8.0e9
    lst = [row]
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": lst})
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-20")
    rows = kpl.fetch_board_stocks("801001")
    assert rows and rows[0]["code"] == "600001"
    assert rows[0]["change"] == 9.9 and rows[0]["limitTag"] == "首板"
    assert rows[0]["ladder"] == "龙一" and rows[0]["turnover"] == 26.9
    assert rows[0]["totalMv"] == 8.0e9


def test_fetch_board_stocks_empty(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": "bad"})
    assert kpl.fetch_board_stocks("801001") == []


# ---------- 尾盘抢筹 ----------
def test_fetch_wpqc_parses(monkeypatch):
    lst = [_row("600001", "测A", "资金", "尾盘", "概念", 3.5, 2.0e8, 9.0e8,
                1.2e8, 0.8e8, 0.4e8, 1.0e8, 0.5e8, 2, "二连", 4.5, 33.5)]
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"List": lst})
    rows = kpl.fetch_wpqc()
    assert rows and rows[0]["code"] == "600001"
    assert rows[0]["qcNet"] == 0.4e8
    assert rows[0]["limitBoards"] == 2
    assert rows[0]["qcChange"] == 4.5 and rows[0]["qcStrength"] == 33.5


def test_fetch_wpqc_none(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda host, params: None)
    assert kpl.fetch_wpqc() is None


# ---------- 人气热榜 ----------
def test_fetch_hot_rank_parses(monkeypatch):
    lst = [_row("600001", "测A", 5.5, 0, 1, 0, 0)]
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"List": lst})
    rows = kpl.fetch_hot_rank()
    assert rows and rows[0]["code"] == "600001"
    assert rows[0]["change"] == 5.5 and rows[0]["rank"] == 1


def test_fetch_hot_rank_short_row_skipped(monkeypatch):
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"List": [(1, 2)]})
    assert kpl.fetch_hot_rank() == []


# ---------- 情绪 ----------
def test_fetch_sentiment_full(monkeypatch):
    import app.services.kpl as km
    d = {"info": [{"ztjs": "88", "strong": "67", "lbgd": "5", "df_num": "3", "Day": "2026-08-20"}],
         "tip": "情绪平稳"}
    monkeypatch.setattr(km, "_call", lambda host, params: d)
    monkeypatch.setattr(km, "fetch_zt_dt_line", lambda *a, **k: [{"limit_down_count": 12}])
    out = km.fetch_sentiment()
    assert out is not None
    assert out["ztCount"] == 88 and out["dtCount"] == 12
    assert out["strong"] == 67 and out["lbgd"] == 5


def test_fetch_sentiment_no_dtline(monkeypatch):
    import app.services.kpl as km
    d = {"info": [{"ztjs": "88", "strong": "67", "lbgd": "5", "df_num": "3", "Day": "2026-08-20"}]}
    monkeypatch.setattr(km, "_call", lambda host, params: d)
    monkeypatch.setattr(km, "fetch_zt_dt_line", lambda *a, **k: [])
    out = km.fetch_sentiment()
    assert out["dtCount"] == 0


def test_fetch_sentiment_empty_info(monkeypatch):
    import app.services.kpl as km
    monkeypatch.setattr(km, "_call", lambda host, params: {"info": []})
    assert km.fetch_sentiment() is None


# ---------- 涨停原因 ----------
def test_fetch_zt_reason_parses(monkeypatch):
    monkeypatch.setattr(kpl, "_call",
                        lambda host, params: {"List": [{"Date": "2026-08-20",
                                                        "Reason": "AI", "SCLT": "龙一",
                                                        "Boom_ZS": "1"}]})
    rows = kpl.fetch_zt_reason("600001")
    assert rows and rows[0]["reason"] == "AI" and rows[0]["sclt"] == "龙一"


def test_fetch_zt_reason_fallback_reason(monkeypatch):
    monkeypatch.setattr(kpl, "_call",
                        lambda host, params: {"List": [{"Date": "2026-08-20",
                                                        "GNSM": "概念甲"}]})
    rows = kpl.fetch_zt_reason("600001")
    assert rows and rows[0]["reason"] == "概念甲"

# ---------- 板块成分股缓存 (v4.11.79) ----------
# 背景: 前端给「板块题材」右栏加了 60s 轮询 ⇒ 成分股接口此前**无缓存**,
#   每个客户端每次轮询都真打上游(开盘啦付费 8 万/日配额)。本轮加 30s TTL。
def _bs_row(code="600001", name="测A"):
    row = [""] * 39
    row[0] = code; row[1] = name; row[4] = "概念"; row[5] = 18.5
    row[6] = 9.9; row[7] = 2.0e8; row[10] = 5.0e9; row[11] = 3.0e7
    row[21] = 2.3; row[23] = "首板"; row[24] = "龙一"; row[25] = 26.9; row[38] = 8.0e9
    return row


def test_board_stocks_cache_hit_avoids_upstream(monkeypatch):
    """🔴 命中缓存时**不得**再调上游 —— 这是省 8 万/日配额的核心。
    用计数 _call 次数的 fake 证实: 第二次调用时 _call 次数不增加。"""
    calls = {"n": 0}

    def fake_call(host, params):
        calls["n"] += 1
        return {"list": [_bs_row()]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-20")

    kpl.clear_cache()
    r1 = kpl.fetch_board_stocks("801001")
    assert r1 and r1[0]["code"] == "600001"
    assert calls["n"] == 1, "首次应真调上游"

    r2 = kpl.fetch_board_stocks("801001")
    assert r2 and r2[0]["code"] == "600001"
    assert calls["n"] == 1, "🔴 二次应命中缓存, 不得再调上游(否则轮询会线性吃配额)"


def test_board_stocks_cache_key_isolated_by_st(monkeypatch):
    """🔴 缓存 key 必须含 st —— 同板块 st=30(展示) 与 st=500(全量) 是**不同结果集**,
    不含 st 会让 500 的结果污染 30 的展示(或反之)。"""
    calls = {"st": []}

    def fake_call(host, params):
        calls["st"].append(params.get("st"))
        return {"list": [_bs_row(code="600" + str(params.get("st")).zfill(3))]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-20")

    kpl.clear_cache()
    a = kpl.fetch_board_stocks("801001", st=30)
    b = kpl.fetch_board_stocks("801001", st=500)
    assert calls["st"] == ["30", "500"], "🔴 不同 st 必须各自 miss 并调上游"
    assert a[0]["code"] != b[0]["code"], "🔴 不同 st 的结果不得互相串"


def test_board_stocks_cache_key_isolated_by_plate(monkeypatch):
    """不同板块不得互相命中缓存(按 plate_id 隔离)。"""
    calls = {"n": 0}

    def fake_call(host, params):
        calls["n"] += 1
        return {"list": [_bs_row(code=params.get("PlateID"))]}

    monkeypatch.setattr(kpl, "_call", fake_call)
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-20")

    kpl.clear_cache()
    a = kpl.fetch_board_stocks("801001")
    b = kpl.fetch_board_stocks("801002")
    assert calls["n"] == 2, "🔴 不同板块必须各自 miss"
    assert a[0]["code"] == "801001" and b[0]["code"] == "801002"


def test_board_stocks_hist_uses_long_ttl(monkeypatch):
    """🔴 历史日走长 TTL(config.KPL_BOARD_STOCKS_HIST_TTL), 实时走短 TTL。
    用「记录 store.set 时收到的 ttl」证实 —— 直接核语义, 不比时间。"""
    seen = []
    real_set = kpl.store.set

    def spy_set(key, value, ttl=0):
        seen.append((key, ttl))
        return real_set(key, value, ttl)

    monkeypatch.setattr(kpl.store, "set", spy_set)
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": [_bs_row()]})

    kpl.clear_cache()
    kpl.fetch_board_stocks("801001")                      # 实时
    live_ttl = seen[-1][1]
    kpl.clear_cache()
    kpl.fetch_board_stocks("801001", date="2026-08-20")   # 历史
    hist_ttl = seen[-1][1]

    assert live_ttl == kpl.config.KPL_BOARD_STOCKS_TTL, f"实时 TTL 应为短 TTL, 实得 {live_ttl}"
    assert hist_ttl == kpl.config.KPL_BOARD_STOCKS_HIST_TTL, f"历史 TTL 应为长 TTL, 实得 {hist_ttl}"
    assert hist_ttl > live_ttl, "历史 TTL 必须比实时长(历史数据不变化)"


def test_board_stocks_is_hist_decided_before_live_branch(monkeypatch):
    """🔴 关键回归: is_hist 必须在**进 live 分支前**从入参 date 判定。
    live 分支会把局部 date 改写成"上一交易日"(回退用) —— 若用改写后的 date 判 is_hist,
    实时请求会被误当历史、缓存半小时(数据看起来"卡住不更新", 正是本轮要修的症状)。
    证法: 实时请求「实时接口返回空 → 触发回退」这条路径, 仍须用**短 TTL**。"""
    seen = []
    real_set = kpl.store.set
    monkeypatch.setattr(kpl.store, "set",
                        lambda key, value, ttl=0: (seen.append((key, ttl)), real_set(key, value, ttl))[1])

    # 实时接口返回空(模拟被拒) → 触发回退到 _prev_trade_day() 的历史分支
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": []})
    monkeypatch.setattr(kpl, "_prev_trade_day", lambda: "2026-08-20")

    kpl.clear_cache()
    kpl.fetch_board_stocks("801001")
    # 回退路径下 list 为空 ⇒ loader 返回 [] ⇒ 不写缓存(见 cached_singleflight 语义)
    # 故这里改用「回退返回有效数据」的版本再跑一次
    monkeypatch.setattr(kpl, "_call",
                        lambda host, params: {"list": [_bs_row()]} if params.get("apiv") == "w41"
                        else {"list": []})
    kpl.clear_cache()
    kpl.fetch_board_stocks("801001")
    assert seen, "回退路径应当写了缓存"
    ttl = seen[-1][1]
    assert ttl == kpl.config.KPL_BOARD_STOCKS_TTL, (
        f"🔴 实时请求即使走了回退分支, 也必须是**短 TTL**; 实得 {ttl} "
        f"(说明 is_hist 是在 date 被改写后才判定的 —— 会把实时当历史缓存半小时)")


def test_board_stocks_cache_key_has_hist_date(monkeypatch):
    """历史请求的缓存 key 必须含日期 —— 否则回看 8/20 与回看 8/21 会串成同一份。"""
    keys = []
    real_set = kpl.store.set
    monkeypatch.setattr(kpl.store, "set",
                        lambda key, value, ttl=0: (keys.append(key), real_set(key, value, ttl))[1])
    monkeypatch.setattr(kpl, "_call", lambda host, params: {"list": [_bs_row()]})

    kpl.clear_cache()
    kpl.fetch_board_stocks("801001", date="2026-08-20")
    k1 = keys[-1]
    kpl.clear_cache()
    kpl.fetch_board_stocks("801001", date="2026-08-21")
    k2 = keys[-1]
    assert k1 != k2, "🔴 不同历史日的缓存 key 必须不同"
    assert "20260820" in k1 and "20260821" in k2
