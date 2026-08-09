# -*- coding: utf-8 -*-
"""管理端接口测试: 权限 / 用户列表统计 / 评分权重读写与生效"""
import os
import sqlite3

import pytest

from app.services import scorer, settings

DEFAULT = dict(scorer.DEFAULT_SCORING)
VALID = {"w_bid": 0.30, "w_activity": 0.35, "w_warn": 0.15, "w_market": 0.12, "w_yesterday": 0.08,
         "conf_warn_high": 10, "conf_turnover": 8, "conf_bid": 7}


@pytest.fixture(scope="session", autouse=True)
def _admin_setup(first_user):
    """显式把 first_user 设为管理员并标记 admin_initialized(测试库确定性,
    避免 ensure_admin 按 id 最小用户误选其他测试注册的账号)"""
    token, uname, _ = first_user
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    conn.execute("UPDATE users SET is_admin=1 WHERE username=?", (uname,))
    conn.execute("INSERT OR REPLACE INTO settings (key,value,updated_at) "
                 "VALUES ('admin_initialized','true',0)")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _restore_scoring():
    """每个测试后恢复默认权重, 避免污染其他用例/文件"""
    yield
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()


def hdrs(token):
    return {"Authorization": "Bearer " + token}


# ---------- 权限 ----------
def test_admin_requires_auth(client):
    r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_non_admin_forbidden(client, second_user):
    token, _ = second_user
    r = client.get("/api/admin/users", headers=hdrs(token))
    assert r.status_code == 403
    r = client.get("/api/admin/scoring", headers=hdrs(token))
    assert r.status_code == 403
    r = client.put("/api/admin/scoring", json={"scoring": VALID}, headers=hdrs(token))
    assert r.status_code == 403


# ---------- 用户列表 ----------
def test_admin_users_ok(client, first_user, second_user):
    token, _, _ = first_user
    r = client.get("/api/admin/users", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    # 统计字段
    s = d["stats"]
    assert s["total"] >= 2 and s["today"] >= 0
    assert "active" in s and "invited" in s
    # 列表字段
    assert d["total"] >= 2
    assert len(d["rows"]) >= 1
    row = d["rows"][0]
    for k in ("id", "username", "created_at", "is_admin", "invited_count", "batch_count"):
        assert k in row


def test_admin_users_keyword(client, first_user, second_user):
    token, _, _ = first_user
    _, uname2 = second_user
    r = client.get("/api/admin/users?keyword=" + uname2, headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 1
    assert d["rows"][0]["username"] == uname2


def test_admin_users_pagination(client, first_user, second_user):
    token, _, _ = first_user
    r = client.get("/api/admin/users?page=1&pageSize=1", headers=hdrs(token))
    d = r.json()
    assert d["total"] >= 2 and len(d["rows"]) == 1


# ---------- 评分权重 ----------
def test_admin_scoring_get_default(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/admin/scoring", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    sc = d["scoring"]
    assert abs(sc["w_bid"] - 0.34) < 1e-9
    assert abs(sc["w_activity"] - 0.32) < 1e-9
    assert len(d["w_keys"]) == 5 and len(d["conf_keys"]) == 3


def test_admin_scoring_put_ok(client, first_user):
    token, _, _ = first_user
    r = client.put("/api/admin/scoring", json={"scoring": VALID}, headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and "已保存" in d.get("msg", "")
    # 内存缓存已刷新
    assert abs(scorer.get_scoring_cfg()["w_bid"] - 0.30) < 1e-9


def test_admin_scoring_put_invalid_sum(client, first_user):
    token, _, _ = first_user
    bad = dict(VALID)
    bad["w_bid"] = 0.99  # 和 >> 1
    r = client.put("/api/admin/scoring", json={"scoring": bad}, headers=hdrs(token))
    assert r.status_code == 400
    assert "权重之和" in r.json().get("msg", "")


def test_admin_scoring_put_invalid_value(client, first_user):
    token, _, _ = first_user
    bad = dict(VALID)
    bad["w_warn"] = 5.0  # 超出 0~1
    r = client.put("/api/admin/scoring", json={"scoring": bad}, headers=hdrs(token))
    assert r.status_code == 400
    assert "0~1" in r.json().get("msg", "")


def test_admin_scoring_put_missing(client, first_user):
    token, _, _ = first_user
    r = client.put("/api/admin/scoring", json={}, headers=hdrs(token))
    assert r.status_code == 400


# ---------- 权重生效到评分 ----------
def test_scoring_cfg_affects_compute(client, first_user):
    """调高市值权重后, 小市值股票相对概率应上升"""
    token, _, _ = first_user
    # 大盘股(市值大, market_score 低) vs 小盘股(市值小, market_score 高)
    big = {"f2": 20.0, "f3": 4.0, "f4": 3.0, "f5": 100000.0, "f6": 2000.0,
           "f8": 3.0, "f10": 1.2, "f12": "600000", "f14": "大盘股",
           "f17": 20.5, "f18": 19.5, "f20": 2.0e11, "f21": 1.5e11,
           "f100": "银行", "f102": "北京", "f103": "权重",
           "f615": 4.2, "f616": 2.0e8, "f617": 1000.0, "f618": 1200.0, "f630": 3}
    small = dict(big)
    small.update({"f12": "300001", "f14": "小盘股", "f20": 2.0e10, "f21": 3.0e9})
    r = client.put("/api/admin/scoring", json={"scoring": VALID}, headers=hdrs(token))
    assert r.status_code == 200
    p_big_default = scorer.compute_score(big)["probability"]
    p_small_default = scorer.compute_score(small)["probability"]
    assert p_small_default > p_big_default   # 默认: 小市值分更高

    # 把市值权重调高到 0.5(其他按比例缩到 0.5 合计), 差距应拉大
    high_mv = {"w_bid": 0.15, "w_activity": 0.15, "w_warn": 0.10, "w_market": 0.50, "w_yesterday": 0.10,
               "conf_warn_high": 10, "conf_turnover": 8, "conf_bid": 7}
    r = client.put("/api/admin/scoring", json={"scoring": high_mv}, headers=hdrs(token))
    assert r.status_code == 200
    p_big_high = scorer.compute_score(big)["probability"]
    p_small_high = scorer.compute_score(small)["probability"]
    assert (p_small_high - p_big_high) > (p_small_default - p_big_default) * 0.9
