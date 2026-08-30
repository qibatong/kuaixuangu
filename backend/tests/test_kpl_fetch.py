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