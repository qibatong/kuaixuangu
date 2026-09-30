# -*- coding: utf-8 -*-
"""「权重组」因子(竞价强度 bid_strength) 的保存保护(2026-09-30)
==================================================================
线上实测到的**静默**故障: 后台保存一次评分权重后, 「竞价昨比」权重 w_zb 从 0.4 变 0。
三条链环环相扣:
  · 前端 AdminView.vue 保存时 `payload.factors = {}` 后只从**界面表单**重建五因子;
  · 后端 settings.set("scoring", new) 是**整表替换** ⇒ 库里就真没了;
  · 旧 _validate_scoring 要求"每个 factor 必须有 buckets", 而 bid_strength 是
    权重组(键为 w_vol_ratio/w_zb/w_ai/w_ff)没有 buckets ⇒ 前端即使想带上也被 400 拒掉。

防复发断言:
  1. 校验放行"含 w_* 的权重组"(可无 buckets), 但子权重仍须 0~1、合计≈1;
  2. 普通因子**仍然**必须带 buckets(不能因为放行权重组而把这条也放开);
  3. 保存时 payload 没带权重组 ⇒ 用库里现值补齐; 带了 ⇒ 以 payload 为准;
  4. 端到端: PUT 五因子权重(不含 bid_strength)后, 库里与**生效配置**里的 w_zb 仍是 0.4。
"""
import os
import sqlite3

import pytest

from app.api import admin
from app.services import scorer, settings

BID_STRENGTH = {"w_vol_ratio": 0.6, "w_zb": 0.4, "w_ai": 0.0, "w_ff": 0.0}
VALID = {"w_bid": 0.30, "w_activity": 0.35, "w_warn": 0.15, "w_market": 0.12, "w_yesterday": 0.08,
         "conf_warn_high": 10, "conf_turnover": 8, "conf_bid": 7}
DEFAULT = dict(scorer.DEFAULT_SCORING)


@pytest.fixture(scope="session", autouse=True)
def _mark_admin(first_user):
    """本文件单独运行时也要保证 first_user 是管理员(幂等)"""
    _token, uname, _ = first_user
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("UPDATE users SET is_admin=1 WHERE username=?", (uname,))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _restore_scoring():
    yield
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def _with_strength(strength=None):
    """库中配置: 默认因子表 + 指定的竞价强度子权重"""
    return dict(DEFAULT, factors=dict(DEFAULT["factors"],
                                      bid_strength=dict(strength or BID_STRENGTH)))


# ---------------- 1/2. 校验: 放行权重组, 但不放松普通因子 ----------------

def test_validate_accepts_weight_group_without_buckets():
    cfg = dict(VALID, factors={"bid_strength": dict(BID_STRENGTH)})
    assert admin._validate_scoring(cfg, "auction") == ""


def test_validate_accepts_weight_group_with_buckets_too():
    """带分档表的完整 bid_strength(界面上拿到的是合并后配置)也必须能保存"""
    merged = dict(scorer.DEFAULT_SCORING["factors"]["bid_strength"])
    merged.update(BID_STRENGTH)
    cfg = dict(VALID, factors={"bid_strength": merged})
    assert admin._validate_scoring(cfg, "auction") == ""


def test_validate_still_rejects_normal_factor_without_buckets():
    cfg = dict(VALID, factors={"bid": {"label": "竞价涨幅", "default": 0.1}})
    assert "缺少有效的分档表" in admin._validate_scoring(cfg, "auction")


def test_validate_rejects_out_of_range_sub_weight():
    bad = dict(VALID, factors={"bid_strength": dict(BID_STRENGTH, w_zb=1.5)})
    assert "需在 0~1 之间" in admin._validate_scoring(bad, "auction")


def test_validate_rejects_sub_weight_sum_far_from_one():
    bad = dict(VALID, factors={"bid_strength": {"w_vol_ratio": 0.6, "w_zb": 0.6}})
    assert "子权重之和" in admin._validate_scoring(bad, "auction")


# ---------------- 3. 补齐: payload 没带就用库值, 带了以 payload 为准 ----------------

def test_preserve_keeps_stored_weight_group():
    settings.set("scoring", _with_strength())
    new = {"factors": {"bid": {"buckets": [["0", "1", 1.0]]}}}
    out = admin._preserve_factor_groups(new, "auction")
    assert abs(out["factors"]["bid_strength"]["w_zb"] - 0.4) < 1e-9
    assert out["factors"]["bid"] == {"buckets": [["0", "1", 1.0]]}


def test_preserve_does_not_override_payload():
    settings.set("scoring", _with_strength())
    new = {"factors": {"bid_strength": {"w_vol_ratio": 1.0, "w_zb": 0.0, "w_ai": 0.0, "w_ff": 0.0}}}
    out = admin._preserve_factor_groups(new, "auction")
    assert out["factors"]["bid_strength"]["w_zb"] == 0.0


def test_preserve_noop_when_store_has_none():
    """库里连 factors 都没有 ⇒ 保持原样(不凭空造出一个 factors 键)"""
    settings.set("scoring", {"w_bid": 0.3, "w_activity": 0.3, "w_warn": 0.2,
                             "w_market": 0.15, "w_yesterday": 0.05})
    new = {"w_bid": 0.2}
    assert admin._preserve_factor_groups(new, "auction") == new


# ---------------- 4. 端到端: 后台保存五因子权重后, 昨比权重仍在 ----------------

def test_put_weights_keeps_bid_strength(client, first_user):
    token, _, _ = first_user
    settings.set("scoring", _with_strength())
    scorer.reload_scoring_cfg()
    assert abs(scorer.get_scoring_cfg()["factors"]["bid_strength"]["w_zb"] - 0.4) < 1e-9

    # 模拟前端: 只回传五因子权重(不含 bid_strength) —— 这正是抹掉昨比的真实路径
    r = client.put("/api/admin/scoring", json={"scoring": dict(VALID)}, headers=hdrs(token))
    assert r.status_code == 200, r.text
    assert r.json().get("ok"), r.text

    stored = settings.get("scoring")["factors"]["bid_strength"]
    assert abs(stored["w_zb"] - 0.4) < 1e-9, "保存五因子权重后 bid_strength 不应丢失"
    eff = scorer.get_scoring_cfg()["factors"]["bid_strength"]
    assert abs(eff["w_zb"] - 0.4) < 1e-9, "生效配置里的昨比权重也不应回落到默认 0"
    assert abs(scorer.get_scoring_cfg()["w_bid"] - 0.30) < 1e-9, "五因子权重本身要正常生效"


def test_put_can_enable_zb_from_ui_payload(client, first_user):
    """界面现在能改昨比: 带上 bid_strength 保存 ⇒ 生效值跟着变"""
    token, _, _ = first_user
    settings.set("scoring", dict(DEFAULT))
    scorer.reload_scoring_cfg()
    assert abs(scorer.get_scoring_cfg()["factors"]["bid_strength"]["w_zb"]) < 1e-9

    payload = dict(VALID, factors={"bid_strength": dict(BID_STRENGTH)})
    r = client.put("/api/admin/scoring", json={"scoring": payload}, headers=hdrs(token))
    assert r.status_code == 200 and r.json().get("ok"), r.text
    assert abs(scorer.get_scoring_cfg()["factors"]["bid_strength"]["w_zb"] - 0.4) < 1e-9
