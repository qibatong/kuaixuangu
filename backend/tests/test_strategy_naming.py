# -*- coding: utf-8 -*-
"""选股**策略参数**命名消歧(2026-09-09 主人指示彻底改名)

背景: 项目里有两个都叫 mode 的东西, 排查时极易误读 ——
  1) 选股**策略** strategy: auction=竞价因子表 / spot=盘中实时因子表
     —— HTTP 参数(?strategy=) + 评分配置(get_scoring_cfg)
  2) 选股**时段** PickMode: preopen/auction/locked/intraday/closed(内部, 由
     resolve_mode 判定, 与用户传参无关)
典型误读: 盘中看到接口回显 mode='auction' 以为"午休还在竞价窗口", 其实那是
策略=竞价选股, 与时段无关(实测 14:08 内部时段为 intraday)。

本组用例锁死三件事:
  - 新名 strategy 为准, 返回体以 strategy 为权威字段
  - 旧名 mode 保留为兼容别名(线上缓存前端/书签/脚本仍在传), 不得 400
  - 改名**不得**影响时段语义(PickMode)与评分结果
"""
import os
import sqlite3

import pytest

from app.services import scorer
from app.services.picker import mode as pmode


def hdrs(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture(scope="module", autouse=True)
def _as_admin(first_user):
    """把 first_user 设为管理员(管理端评分配置接口需要 admin)"""
    token, uname, _ = first_user
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("UPDATE users SET is_admin=1 WHERE username=?", (uname,))
    conn.commit()
    conn.close()


# ==================== /api/stocks 参数 ====================
def test_new_strategy_param_accepted(client, first_user):
    """新参数 strategy 被接受(合法值不 400)"""
    token, _, _ = first_user
    for s in ("auction",):
        r = client.get("/api/stocks?action=ping&strategy=%s" % s, headers=hdrs(token))
        assert r.status_code == 200, "strategy=%s 应被接受" % s
        assert r.json().get("ok")


def test_legacy_mode_param_still_accepted(client, first_user):
    """旧参数 mode 仍兼容 —— 线上缓存前端/书签/未同步脚本还在传, 不得 400"""
    token, _, _ = first_user
    for s in ("auction",):
        r = client.get("/api/stocks?action=ping&mode=%s" % s, headers=hdrs(token))
        assert r.status_code == 200, "旧参数 mode=%s 必须继续兼容" % s
        assert r.json().get("ok")


def test_strategy_takes_precedence_over_mode(client, first_user):
    """同时传 strategy 与 mode 时以 strategy 为准"""
    token, _, _ = first_user
    # strategy 合法 / mode 非法 → 应按 strategy 放行(证明 strategy 优先)
    r = client.get("/api/stocks?action=ping&strategy=auction&mode=zzz",
                   headers=hdrs(token))
    assert r.status_code == 200
    assert r.json().get("ok")


def test_invalid_strategy_rejected(client, first_user):
    """非法策略值 400(且报错文案用新名)"""
    token, _, _ = first_user
    r = client.get("/api/stocks?action=ping&strategy=zzz", headers=hdrs(token))
    assert r.status_code == 400
    assert "strategy" in r.json().get("msg", "")


def test_spot_strategy_now_rejected(client, first_user):
    """2026-09-09 盘中实时选股(spot)下线: 前端无入口/后端零调用 → 传 spot 显式 400。

    不静默退化成 auction —— 若旧书签还在传 spot, 宁可报错也不要给用户一份
    他以为"盘中实时"、实际是竞价定格的名单。"""
    token, _, _ = first_user
    for q in ("strategy=spot", "mode=spot"):
        r = client.get("/api/stocks?action=refresh&%s" % q, headers=hdrs(token))
        assert r.status_code == 400, q
        assert not r.json().get("ok")


# ==================== 评分配置 ====================
def test_scoring_cfg_new_keyword():
    """get_scoring_cfg(strategy=) 取到对应因子表"""
    assert abs(scorer.get_scoring_cfg(strategy="auction")["w_bid"] - 0.34) < 1e-9


def test_scoring_cfg_legacy_keyword_alias():
    """旧关键字 mode= 仍可用(兼容别名, 未同步部署的调用点不至于 TypeError)"""
    assert abs(scorer.get_scoring_cfg(mode="auction")["w_bid"] - 0.34) < 1e-9
    # 两种写法结果完全一致
    assert (scorer.get_scoring_cfg(strategy="auction") ==
            scorer.get_scoring_cfg(mode="auction"))


def test_admin_scoring_accepts_both_params(client, first_user):
    """管理端评分配置 GET: strategy 与旧 mode 都返回竞价配置"""
    token, _, _ = first_user
    for q in ("strategy=auction", "mode=auction"):
        r = client.get("/api/admin/scoring?%s" % q, headers=hdrs(token))
        assert r.status_code == 200
        d = r.json()
        assert d.get("strategy") == "auction", q
        assert d.get("mode") == "auction", q       # 兼容字段
        assert abs(d["scoring"]["w_bid"] - 0.34) < 1e-9


def test_admin_scoring_invalid_strategy(client, first_user):
    """管理端非法策略值 400"""
    token, _, _ = first_user
    r = client.get("/api/admin/scoring?strategy=zzz", headers=hdrs(token))
    assert r.status_code == 400


# ==================== 改名不得影响时段语义 ====================
def test_pick_mode_independent_of_strategy(first_user):
    """时段模式只由时间决定, 与策略参数无关(改名不得污染时段语义)"""
    import datetime
    cases = [
        ((8, 0), "preopen"),
        ((9, 20), "auction"),
        ((9, 27), "locked"),
        ((11, 50), "intraday"),      # 午休仍归盘中(曾因此误读过一次)
        ((14, 8), "intraday"),
        ((15, 30), "closed"),
    ]
    for (hh, mm), expect in cases:
        got = pmode.resolve_mode(datetime.datetime(2026, 9, 9, hh, mm)).mode.value
        assert got == expect, "%02d:%02d 应为 %s, 实为 %s" % (hh, mm, expect, got)


def test_pick_mode_enum_unchanged():
    """PickMode 取值与边界常量保持不变(时段语义未被改名波及)"""
    assert {m.value for m in pmode.PickMode} == {
        "preopen", "auction", "locked", "intraday", "closed"}
    assert (pmode.T_PREOPEN_END, pmode.T_AUCTION_END,
            pmode.T_LOCKED_END, pmode.T_INTRADAY_END) == (555, 565, 570, 900)
