# -*- coding: utf-8 -*-
"""17% 异动因子「改回东财 f630」的通路回归 (2026-09-18 v4.11.30)

为什么需要这个文件(整条链的每一环都要钉住):
------------------------------------------------------------------------------
主人要求把评分 17% 权重的「异动」改回东财 f630。**光翻 `use_bid_strength=0`
开关不够** —— 实测发现:

  * 东财**点查**(`.../api/qt/ulist.np/get`, 即选股补丁源 `eastmoney_realtime`)
    长期失败(`RemoteDisconnected`) → 补丁源永远拿不到 f630, 退腾讯点查(腾讯无 f630)。
    ⚠️ 2026-09-19 订正: 失败根因**不是接口被封, 而是域名**——原代码写死
    `push2.eastmoney.com`(整站 RST), 换成 `push2dycalc.eastmoney.com` 同一 path
    `rc=0` 且**带 f630**。已修(v4.11.33, `_ULIST_HOSTS` 顺序重试),
    见 `test_ulist_domain_0919.py`。
  * 定格链路(9:25 后选股)的名单行来自 `snapshot_bid`, 而 `QuoteRow.from_snapshot`
    原先**不设 warn_type** → 全市场 warn_type=None → 落 default 0.18 →
    17% × 0.82 = 13.9 分凭空蒸发(9/8 批次#1585、9/17 批次 18/18 都是这个形态,
    9/17 更直接导致 `候选7 入选0 剔除 score_floor` 一整天零名单)。
  * 而**全市场 clist(`push2dycalc`)是通的, 且 `config.FIELDS` 本就含 f630**
    (2026-09-18 实测: hs 板块 3487 行中 1268 行非 0, 占 36.4%, 取值域 0~14,
    3/4/5 档齐全) → 答案不是"接受退化", 而是**在 9_25 采集那一秒把 f630 落库**。

于是本版把链路打通为:
    采集(`_fetch_market_map` 收 f630) → 落库(`snapshot_bid.warn_type`)
    → 读取(`load_snapshot_full`) → 契约(`QuoteRow.from_snapshot.warn_type`)
    → 评分(`parts["warn"]`, 档位 5→1.0 / 4→0.85 / 3→0.6 / 其余→default 0.18)

本文件按这个顺序逐环断言, 并额外守住两条**反向防线**:
  ① `config.FIELDS` 必须继续含 f630 —— 少了它整条链静默失效(没人会报错);
  ② 老库缺 `warn_type` 列时必须**降级而不是让名单整体失踪**(load_snapshot_full
     原先一处 DB 异常就 `return {}` → 全市场名单直接空, 比"异动缺值"严重得多)。
"""
import sqlite3
import time

import pytest

from app.core import config
from app.db import database
from app.services import auction_snapshot, scorer
from app.services.picker.contract import QuoteRow
from app.services.picker.score import compute_score


def _bj_today():
    return auction_snapshot._bj_date()


def _bj_yesterday():
    """北京时间的昨天(用于造一条"最近交易日"快照, 落在 load_snapshot_full 的 15 日窗口内)"""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - 86400))


def _real_load(date):
    """取**未被 conftest 桩掉**的真实 load_snapshot_full。

    conftest.mock_data_source 是 session 级 autouse, 为了"测试库里没有定格数据"把
    `load_snapshot_full` 桩成了 MOCK_RAW 造的快照 —— 而本文件要验证的正是**真实读库
    那一环**(snapshot_bid.warn_type → 返回字典), 打到桩上等于没测。conftest 已把
    真实实现挂在 `_real_load_snapshot_full` 上。
    """
    real = getattr(auction_snapshot, "_real_load_snapshot_full", None)
    assert real is not None, "conftest 未暴露真实实现入口(_real_load_snapshot_full)"
    return real(date)


# ================================================================ 反向防线 ①
def test_config_fields_still_request_f630():
    """整个设计依赖东财全市场请求里带 f630 —— 谁把它从 FIELDS 删掉, 这里立刻红"""
    assert "f630" in config.FIELDS, (
        "config.FIELDS 少了 f630 → 全市场 clist 不再返回异动等级 → 17% 因子静默退化")


# ================================================================ ① 采集层
def _em_row(code, f630, chg=3.0):
    return {
        "f12": code, "f14": "测试" + code[-2:], "f615": chg, "f616": 5.0e7,
        "f617": 300.0, "f618": 400.0, "f630": f630,
        "f5": 1000.0, "f10": 800.0, "f21": 4.0e9, "f117": 3.6e9,
        "f100": "软件服务", "f103": "AI概念",
    }


@pytest.fixture
def _patch_market(monkeypatch):
    """桩掉全市场行情与 TickPlus(不碰网络), 由用例指定返回行"""
    def _apply(rows):
        monkeypatch.setattr(auction_snapshot.fetcher, "_fetch_market_all_with_fallback",
                            lambda fs, *a, **k: rows)
        monkeypatch.setattr(auction_snapshot.fetcher, "_fetch_market_with_fallback",
                            lambda fs, *a, **k: rows)
        monkeypatch.setattr(auction_snapshot.tickplus, "snapshot_map", lambda *a, **k: {})
    return _apply


def test_market_map_carries_f630(monkeypatch, _patch_market):
    """全市场行 → raw_all 必须带上 warn_type(f630), 供落库"""
    _patch_market([_em_row("600001", 4), _em_row("000002", 0), _em_row("300003", 5)])
    raw = auction_snapshot._fetch_market_map(full=True)
    assert raw["600001"]["warn_type"] == 4
    assert raw["000002"]["warn_type"] == 0        # 0 = 无异动, 是**真值**不是缺失
    assert raw["300003"]["warn_type"] == 5


def test_market_map_non_numeric_f630_falls_to_zero(monkeypatch, _patch_market):
    """f630 为 '-'/None/脏值时不许炸, 统一落 0(与东财缺省同义)"""
    rows = [_em_row("600001", "-"), _em_row("000002", None), _em_row("300003", "x")]
    _patch_market(rows)
    raw = auction_snapshot._fetch_market_map(full=True)
    assert [raw[c]["warn_type"] for c in ("600001", "000002", "300003")] == [0, 0, 0]


def test_kpl_and_tickplus_fallback_rows_default_warn_zero(monkeypatch):
    """兜底源(开盘啦 / TickPlus)无 f630 → warn_type=0, 保证落库列非空且不臆造值"""
    def boom(fs, *a, **k):
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(auction_snapshot.fetcher, "_fetch_market_all_with_fallback", boom)
    monkeypatch.setattr(auction_snapshot.fetcher, "_fetch_market_with_fallback", boom)
    monkeypatch.setattr(auction_snapshot.tickplus, "snapshot_map", lambda *a, **k: {})

    import app.services.kpl as kpl_mod
    monkeypatch.setattr(kpl_mod, "clear_cache", lambda: None)
    monkeypatch.setattr(kpl_mod, "fetch_bid_seal", lambda: [
        {"code": "600001", "name": "测试甲", "bidChange": 10.0, "bidAmt": 5e7,
         "bidSealAmt": 1.2e8, "board": "AI概念"}])
    monkeypatch.setattr(kpl_mod, "fetch_bid_boom", lambda: [])

    raw = auction_snapshot._fetch_market_map(full=True)
    assert raw["600001"]["warn_type"] == 0


# ================================================================ ② 落库层
def test_snapshot_at_persists_warn_type(monkeypatch, _patch_market):
    """snapshot_at 必须把 warn_type 写进 snapshot_bid.warn_type 列"""
    _patch_market([_em_row("600001", 4), _em_row("000002", 0)])
    monkeypatch.setattr(auction_snapshot.mv_cache, "fill",
                        lambda raw, date=None: {"need": 0, "from_cache": 0,
                                                "from_tencent": 0, "miss": 0, "saved": 0})
    import app.services.kpl as kpl_mod
    monkeypatch.setattr(kpl_mod, "clear_cache", lambda: None)
    monkeypatch.setattr(kpl_mod, "fetch_bid_seal", lambda: [])
    monkeypatch.setattr(kpl_mod, "fetch_bid_boom", lambda: [])

    tp = "9_20"
    date = _bj_today()
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid WHERE date=? AND time_point=?", (date, tp))
    conn.commit()
    conn.close()
    try:
        n = auction_snapshot.snapshot_at(tp, force=True)
        assert n == 2
        conn = database.get_conn()
        got = dict(conn.execute(
            "SELECT code, warn_type FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, tp)).fetchall())
        conn.close()
        assert got == {"600001": 4, "000002": 0}, \
            "采集侧没把 f630 落进 snapshot_bid.warn_type"
    finally:
        conn = database.get_conn()
        conn.execute("DELETE FROM snapshot_bid WHERE date=? AND time_point=?", (date, tp))
        conn.commit()
        conn.close()


# ================================================================ ③ 契约层
def test_from_snapshot_reads_warn_type():
    """快照行 → QuoteRow 必须带上 warn_type(=17% 因子在定格链路的唯一来源)"""
    row = QuoteRow.from_snapshot({"code": "600001", "name": "测试甲", "bid_change": 3.5,
                                  "bid_amt": 8888.0, "float_mv": 4.0e9,
                                  "warn_type": 4})
    assert row.warn_type == 4


def test_from_snapshot_without_warn_type_is_none():
    """老库/老行没有该键 → None(评分走 default), 不冒充成 0 值桶"""
    row = QuoteRow.from_snapshot({"code": "600001", "name": "测试甲", "bid_change": 3.5,
                                  "bid_amt": 8888.0, "float_mv": 4.0e9})
    assert row.warn_type is None


def test_from_snapshot_warn_type_zero_kept_as_zero():
    """f630=0 是**实测真值**(无异动), 不能被当成缺失丢掉"""
    row = QuoteRow.from_snapshot({"code": "600001", "bid_change": 3.5, "warn_type": 0})
    assert row.warn_type == 0


# ================================================================ ④ 读取层
def test_load_snapshot_full_exposes_warn_type():
    date = _bj_yesterday()
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid WHERE date=?", (date,))
    conn.execute(
        "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, "
        "bid_amt, name, bid_buy_amt, float_mv, free_mv, board, warn_type, ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (date, "9_25", "600001", 3.5, 8888.0, "测试甲", 0, 4.0e9, 3.6e9, "AI概念", 4,
         int(time.time())))
    conn.commit()
    conn.close()
    try:
        out = _real_load(date)
        assert out["600001"]["warn_type"] == 4
    finally:
        conn = database.get_conn()
        conn.execute("DELETE FROM snapshot_bid WHERE date=?", (date,))
        conn.commit()
        conn.close()


def test_load_snapshot_full_degrades_on_old_schema(monkeypatch, tmp_path):
    """🔴 反向防线 ②: 老库(缺 warn_type 列)必须**降级**而不是让名单整体失踪。

    原实现一处 DB 异常 → `return {}` → 全市场名单直接空; 那比"异动缺值"严重得多。
    """
    db = tmp_path / "old_schema.db"
    date = _bj_yesterday()
    c0 = sqlite3.connect(str(db))
    c0.execute("CREATE TABLE snapshot_bid (date TEXT, time_point TEXT, code TEXT, "
               "bid_change REAL, bid_amt REAL, ts INT, name TEXT, bid_buy_amt REAL, "
               "float_mv REAL, free_mv REAL, board TEXT, "
               "PRIMARY KEY (date, time_point, code))")
    c0.execute("INSERT INTO snapshot_bid VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (date, "9_25", "600001", 3.5, 8888.0, 1, "测试甲", 0, 4.0e9, 3.6e9, ""))
    c0.commit()
    c0.close()

    monkeypatch.setattr(database, "get_conn", lambda: sqlite3.connect(str(db)))
    out = _real_load(date)
    assert "600001" in out, "缺列导致整个快照读成空 → 名单会整体消失"
    assert out["600001"]["bid_change"] == 3.5
    assert out["600001"]["warn_type"] is None, "缺列应降级为未知(评分走 default)"


# ================================================================ ⑤ 评分层
def _row(warn, **kw):
    base = dict(code="600001", name="测试甲", bid_change=3.5, bid_amt=5.0e7,
                float_mv=4.0e9, prev_close=10.0, yesterday_change=1.0,
                warn_type=warn)
    base.update(kw)
    return QuoteRow(**base)


@pytest.mark.parametrize("f630,expect", [(5, 1.0), (4, 0.85), (3, 0.6),
                                         (2, 0.18), (1, 0.18), (0, 0.18)])
def test_switch_off_scores_by_f630_bucket(f630, expect):
    """开关置 0(未注入竞价强度) → 17% 因子必须按 f630 档位打分, 不再全员 default"""
    cfg = scorer.get_scoring_cfg()
    sr = compute_score(_row(f630), cfg, None)
    assert sr.parts["warn"]["score"] == pytest.approx(expect)
    assert sr.parts["warn"]["value"] == f630


def test_f630_high_gear_actually_lifts_probability():
    """量化影响: 4 档 vs 0 档 = (0.85-0.18)×17% ≈ +11.4 分 —— 证明 f630 通路真的带区分度

    (9/17 事故正是"全员 default" ⇒ 这个差值恒为 0 ⇒ 天花板被压 14 分)
    """
    cfg = scorer.get_scoring_cfg()
    p0 = compute_score(_row(0), cfg, None).probability
    p4 = compute_score(_row(4), cfg, None).probability
    p5 = compute_score(_row(5), cfg, None).probability
    assert 5 < p0 < 95 and 5 < p4 < 95, "样本票不该被概率上下限夹住, 否则差值失真"
    assert p5 > p4 > p0
    # 概率按 js_round 取整 → 差值有 ±1 的取整误差; 故容差放到 1.0
    # (2026-09-20 市值因子改自由流通口径后, 样本票绝对分位移导致取整边界跳动,
    #  严格的 abs=0.5 会因 ±1 取整而误报; 语义仍是"4 档比 0 档高约 11.4 分")
    assert (p4 - p0) == pytest.approx(0.17 * (0.85 - 0.18) * 100, abs=1.0)


def test_injected_strength_still_wins_over_f630():
    """反向防线: 开关打开(注入竞价强度)时, 强度仍**优先**于 f630 —— 回滚路径必须可用"""
    cfg = scorer.get_scoring_cfg()
    sr = compute_score(_row(4), cfg, 0.6)
    assert sr.parts["warn"]["score"] == pytest.approx(0.6)
    assert sr.parts["warn"]["value"] == "竞价强度"
