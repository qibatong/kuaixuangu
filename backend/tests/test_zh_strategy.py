# -*- coding: utf-8 -*-
"""ZH 竞价选股策略(services/picker/zh.py)单测。

口径来源: 优化清单「结论 13」的只读回放 —— 今日(2026-09-29)入选 6 只, 其中 301190 善水科技
**恰好 5.00%** 入选 ⇒ 区间必须**含端点**; 判涨停用「四舍五入涨停价式」(板块自适应)。

🔴 占昨量口径(2026-09-29 15:1x 订正): 回放脚本(`zh_replay2.sh:120`)用的是
   **量/量** = (竞价额/竞价价/100) ÷ 昨量(手); 我首版误写成"额/额" ⇒ 高开票系统性偏大 5~13%
   ⇒ 把 000678/603949 顶出 10% 上限。现默认 `VOL_MODE="vol"`, 并保留 `zhVolMode="amt"` 可选。
"""
import json as _json
import sqlite3

import pytest

from app.services import filter_defaults as fd
from app.services.picker import zh

AMT = {"zhVolMode": "amt"}          # 额/额模式(可选口径)


def _dd(dates, closes, amounts=None, volumes=None, opens=None):
    """构造 stock_kline.day_data 形态(列式数组)"""
    return {"time": list(dates), "open": list(opens or closes), "close": list(closes),
            "high": list(closes), "low": list(closes),
            "volume": list(volumes or [100000] * len(closes)),
            "amount": list(amounts or [1e8] * len(closes))}


def _gene_kl():
    """昨量 100000 手 / 昨额 1 亿元(隐含均价 10 元); 历史含一次涨停(10 → 11.00); 末根 09-28"""
    return _dd(["2026-09-01", "2026-09-02", "2026-09-28"], [10.0, 11.0, 10.5])


def _row(code, name, chg, amt):
    return {"code": code, "name": name, "bid_change": chg, "bid_amt": amt}


# ---------------- 板块标签 ----------------
@pytest.mark.parametrize("code,board", [
    ("600825", "主板"), ("000678", "主板"), ("002074", "主板"),
    ("300876", "创业"), ("301190", "创业"),
    ("688981", "科创"), ("689009", "科创"),
    ("920267", "北交"), ("830799", "北交"), ("430047", "北交"),
])
def test_board_of(code, board):
    assert zh.board_of(code) == board


# ---------------- 涨停基因: 判据 + 板块自适应 ----------------
def test_zt_gene_round_limit_price_true():
    """000012 昨收 3.64 → 收 4.00(+9.89%) 是真涨停(旧阈值式 ≥9.9% 会**漏判**)"""
    kl = zh.ordered_klines(_dd(["2026-09-25", "2026-09-28"], [3.64, 4.00]))
    assert zh.zt_gene("000012", kl) == (1, True)


def test_zt_gene_low_price_false_positive_rejected():
    """000004 昨收 0.26 → 收 0.28(+7.69%) 不是涨停(1 分钱容差式会**误判**)"""
    kl = zh.ordered_klines(_dd(["2026-09-25", "2026-09-28"], [0.26, 0.28]))
    assert zh.zt_gene("000004", kl) == (0, False)


@pytest.mark.parametrize("code,pct,hit", [
    ("600825", 10.0, True), ("600825", 9.9, False),      # 主板 10%
    ("300876", 20.0, True), ("300876", 9.9, False),      # 创业 20%
    ("920267", 30.0, True), ("920267", 20.0, False),     # 北交所 30%(20% 不算)
    ("430047", 30.0, True), ("430047", 9.9, False),      # 老三板/北交老段 30%
])
def test_zt_gene_board_adaptive(code, pct, hit):
    pc = 10.0
    kl = zh.ordered_klines(_dd(["2026-09-25", "2026-09-28"],
                               [pc, round(pc * (1 + pct / 100.0), 2)]))
    assert zh.zt_gene(code, kl)[1] is hit, (code, pct)


def test_zt_gene_window_and_min():
    closes = [10.0, 11.0, 10.5, 10.5, 10.5, 10.5]        # 第 2 根是涨停
    kl = zh.ordered_klines(_dd(["2026-09-%02d" % (10 + i) for i in range(6)], closes))
    assert zh.zt_gene("600000", kl, days=120, min_cnt=1) == (1, True)
    assert zh.zt_gene("600000", kl, days=2, min_cnt=1) == (0, False)     # 窗口收窄 ⇒ 探不到
    assert zh.zt_gene("600000", kl, days=120, min_cnt=2) == (1, False)   # 次数不够


def test_zt_gene_insufficient_bars():
    kl = zh.ordered_klines(_dd(["2026-09-28"], [10.0]))
    assert zh.zt_gene("600000", kl) == (0, False)


# ---------------- 昨额/昨量: 单位与"跳过今日" ----------------
def test_yesterday_amount_units_and_today_skip():
    """amount 单位是元 ⇒ /1e4 转万元(与 snapshot_bid.bid_amt 同单位, 防 1e4 级错误)"""
    kl = zh.ordered_klines(_dd(["2026-09-28", "2026-09-29"], [10, 10], [1e9, 5e8]))
    assert zh.yesterday_amount(kl, today="2026-09-29") == 1e5      # 1e9 元 = 100000 万元
    assert zh.yesterday_amount(kl) == 5e4                          # 不给 today ⇒ 取末根


def test_yesterday_volume_units_and_today_skip():
    """volume 单位是手(直接用), 不得再 ×100"""
    kl = zh.ordered_klines(_dd(["2026-09-28", "2026-09-29"], [10, 10], None, [123456, 999]))
    assert zh.yday_volume(kl, today="2026-09-29") == 123456
    assert zh.yday_volume(kl) == 999


def test_yesterday_amount_missing():
    kl = zh.ordered_klines(_dd(["2026-09-29"], [10], [0]))
    assert zh.yesterday_amount(kl, today="2026-09-29") is None      # 无"已收盘"根
    assert zh.yesterday_amount([], today="2026-09-29") is None
    assert zh.yday_volume([], today="2026-09-29") is None


def test_bid_volume_lots_formula():
    """竞价量(手) = 竞价额(元) ÷ [昨收×(1+高开)] ÷ 100 —— 与回放同式"""
    # 600 万元 / 10.5 元 / 100 = 5714.2857 手
    assert round(zh.bid_volume_lots(600.0, 5.0, 10.0), 4) == 5714.2857
    assert zh.bid_volume_lots(None, 5.0, 10.0) is None
    assert zh.bid_volume_lots(600.0, 5.0, 0) is None


# ---------------- 占昨量两种口径(默认量/量) ----------------
def test_vol_mode_default_is_volume_ratio():
    """默认口径 = 量/量(与回放同式); 额/额仍是 6.00% 作对照

    注意末根收盘 = **10.5**(不是 10) ⇒ 竞价价 = 10.5×1.05 = 11.025, 手数用函数现算避免手算错。
    """
    km = {"600111": _gene_kl()}
    row = _row("600111", "甲", 5.0, 600.0)
    picks, _ = zh.select([row], lambda c: km.get(c), today="2026-09-29")
    want = round(zh.bid_volume_lots(600.0, 5.0, 10.5) / 100000 * 100, 2)
    assert len(picks) == 1 and picks[0]["volPct"] == want == 5.44
    assert picks[0]["volMode"] == "vol" and picks[0]["ydayVol"] == 100000
    picks2, _ = zh.select([row], lambda c: km.get(c), f=AMT, today="2026-09-29")
    assert picks2[0]["volPct"] == 6.0 and picks2[0]["volMode"] == "amt"


def test_vol_mode_smaller_than_amt_mode_when_gap_up():
    """🔴 回归钉子(口径事故根因): 高开票竞价价 > 昨均价 ⇒ 量/量 **必然小于** 额/额。

    首版误用额/额 ⇒ 000678(9.57%→10.62%)/603949(9.08%→10.17%) 被顶出 10% 上限而漏选。
    """
    km = {"600111": _gene_kl()}                       # 隐含昨均价 = 1e8/(100000×100) = 10 元
    row = _row("600111", "甲", 5.0, 620.0)            # 竞价价 = 10.5 > 10
    v, _ = zh.select([row], lambda c: km.get(c), today="2026-09-29")
    a, _ = zh.select([row], lambda c: km.get(c), f=AMT, today="2026-09-29")
    assert v and a and v[0]["volPct"] < a[0]["volPct"]


# ---------------- 选股: 条件 + 排序 + 统计 ----------------
def test_select_basic_and_sort_desc():
    """量/量口径(末根收盘 10.5 ⇒ 竞价价 11.025) ⇒ 按占昨量降序"""
    km = {c: _gene_kl() for c in ("600111", "600112", "600113", "600114")}
    rows = [
        _row("600111", "甲", 5.0, 600.0),
        _row("600112", "乙", 5.0, 900.0),
        _row("600113", "丙", 2.9, 900.0),      # 高开不足
        _row("600114", "ST丁", 5.0, 900.0),    # ST
    ]
    picks, st = zh.select(rows, lambda c: km.get(c), today="2026-09-29")
    w900 = round(zh.bid_volume_lots(900.0, 5.0, 10.5) / 100000 * 100, 2)
    assert [p["code"] for p in picks] == ["600112", "600111"]
    assert picks[0]["volPct"] == w900 == 8.16 and picks[0]["ztGene"] == 1
    assert picks[0]["market"] == "主板" and picks[0]["ydayAmt"] == 1e4
    assert st["drop_chg"] == 1 and st["drop_name"] == 1 and st["kept"] == 2


def test_select_bounds_are_inclusive():
    """回放里 301190 恰好 5.00% 入选 ⇒ [5,10] 必须含端点(边界外才剔), 两种口径都测"""
    km = {"600111": _gene_kl()}
    # 量/量: 昨量 100000 手, 竞价量 5000 手 = 5.00%; 10000 手 = 10.00%
    keep = [_row("600111", "甲", 0.0, 525.0), _row("600111", "甲", 0.0, 1050.0)]
    picks, _ = zh.select(keep, lambda c: km.get(c), f={"zhBidGt": 0}, today="2026-09-29")
    assert [p["volPct"] for p in picks] == [10.0, 5.0]
    drop = [_row("600111", "甲", 0.0, 524.0), _row("600111", "甲", 0.0, 1051.0)]
    picks2, st2 = zh.select(drop, lambda c: km.get(c), f={"zhBidGt": 0}, today="2026-09-29")
    assert picks2 == [] and st2["drop_vol"] == 2
    # 额/额: 500 万/1000 万 对 1 亿昨额 ⇒ 5% / 10%
    keep_a = [_row("600111", "甲", 5.0, 500.0), _row("600111", "甲", 5.0, 1000.0)]
    pa, _ = zh.select(keep_a, lambda c: km.get(c), f=AMT, today="2026-09-29")
    assert [p["volPct"] for p in pa] == [10.0, 5.0]


def test_select_range_configurable():
    """可配: 放宽到 [3,15] 后 11% 的票应入选(结论 13: [5,10] 6 只 / [3,15] 11 只)"""
    km = {"600111": _gene_kl()}
    row = _row("600111", "甲", 5.0, 1100.0)                       # 额/额 11%
    assert zh.select([row], lambda c: km.get(c), f=AMT, today="2026-09-29")[0] == []
    picks, _ = zh.select([row], lambda c: km.get(c),
                         f={"zhVolPctFloor": 3, "zhVolPctGt": 15, "zhVolMode": "amt"},
                         today="2026-09-29")
    assert [p["code"] for p in picks] == ["600111"]

    # 高开门槛同样可配
    low = [_row("600111", "甲", 2.0, 600.0)]
    assert zh.select(low, lambda c: km.get(c), today="2026-09-29")[0] == []
    picks2, _ = zh.select(low, lambda c: km.get(c), f={"zhBidGt": 1}, today="2026-09-29")
    assert len(picks2) == 1


def test_select_missing_kline_and_gene_gate():
    rows = [_row("600111", "甲", 5.0, 600.0), _row("600112", "乙", 5.0, 600.0),
            _row("600113", "丙", 5.0, 600.0)]
    km = {
        "600111": _gene_kl(),
        "600112": _dd(["2026-09-28"], [10.0]),                    # 仅 1 根 ⇒ 无法判
        "600113": _dd(["2026-09-01", "2026-09-28"], [10.0, 10.1]),  # 无涨停 ⇒ 基因不达标
    }
    picks, st = zh.select(rows, lambda c: km.get(c), today="2026-09-29")
    assert [p["code"] for p in picks] == ["600111"]
    assert st["drop_nokline"] == 1 and st["drop_gene"] == 1


def test_select_dirty_values_do_not_crash():
    """脏值(None/空串/非数字码)不抛, 按"无法证明达标 ⇒ 剔除"处理。

    🔴 回归钉子: 首版用 `.zfill(6)` 且不校验形状 ⇒ 空码会变成 "000000" 混进名单(本用例抓到)。
    """
    rows = [{"code": "600111", "name": None, "bid_change": None, "bid_amt": ""},
            {"code": "", "name": "空码", "bid_change": 5.0, "bid_amt": 600.0},
            {"code": "abc", "name": "非数字", "bid_change": 5.0, "bid_amt": 600.0}]
    picks, st = zh.select(rows, lambda c: _gene_kl(), today="2026-09-29")
    assert picks == [] and st["kept"] == 0
    assert st["drop_name"] == 2      # 空码 + 非数字码
    assert st["drop_chg"] == 1       # 首行 bid_change 为 None(不是"码"的问题)


def test_select_pads_short_numeric_code():
    """上游可能给 int 型代码(如 678) ⇒ 必须补零成 000678, 否则带前导零的票整批漏选"""
    rows = [{"code": 678, "name": "襄阳轴承", "bid_change": 5.0, "bid_amt": 600.0}]
    picks, st = zh.select(rows, lambda c: _gene_kl(), today="2026-09-29")
    assert [p["code"] for p in picks] == ["000678"]
    assert picks[0]["market"] == "主板"


# ---------------- 参数真相源 ----------------
def test_cfg_keys_live_in_filter_defaults():
    """ZH 数值参数必须落在 filter_defaults.FILTER_DEFAULTS(管理员可调 + 单一真相源)"""
    for k in zh.DEFAULTS:
        assert k in fd.FILTER_DEFAULTS, "ZH 参数 %s 不在真相源里" % k
    assert zh.VOL_MODE == "vol", "默认口径必须是量/量(与回放基线一致)"


def test_cfg_dirty_value_falls_back():
    cfg = zh._cfg({"zhBidGt": "", "zhVolPctGt": "abc", "zhVolPctFloor": 3})
    assert cfg["zhVolPctFloor"] == 3.0
    assert cfg["zhBidGt"] == zh.DEFAULTS["zhBidGt"]          # 脏值 → 兜底
    assert cfg["zhVolPctGt"] == zh.DEFAULTS["zhVolPctGt"]


# ---------------- run(): 只读跑通(in-memory 库) ----------------
def test_run_readonly_with_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshot_bid(date TEXT, time_point TEXT, code TEXT, "
                 "bid_change REAL, bid_amt REAL, float_mv REAL, free_mv REAL, name TEXT)")
    conn.execute("CREATE TABLE stock_kline(code TEXT, day_data TEXT, ts INTEGER)")
    conn.execute("INSERT INTO stock_kline VALUES(?,?,0)",
                 ("600111", _json.dumps(_gene_kl())))
    conn.execute("INSERT INTO snapshot_bid VALUES(?,?,?,?,?,?,?,?)",
                 ("2026-09-29", "9_25", "600111", 5.0, 600.0, 1e10, 1e10, "甲"))
    conn.commit()
    picks, st, meta = zh.run(date="2026-09-29", conn=conn)
    assert [p["code"] for p in picks] == ["600111"]
    assert meta["snapshot_rows"] == 1 and meta["time_point"] == "9_25" and st["kept"] == 1
    assert meta["cfg"]["zhVolPctFloor"] == 5.0
