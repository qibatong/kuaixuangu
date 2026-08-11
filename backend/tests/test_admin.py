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


def test_settings_set_idempotent():
    """settings 覆盖写入(INSERT OR REPLACE, 兼容老 SQLite)"""
    assert settings.set("scoring", {"w_bid": 0.5}) is True
    assert settings.get("scoring")["w_bid"] == 0.5
    assert settings.set("scoring", {"w_bid": 0.4}) is True
    assert settings.get("scoring")["w_bid"] == 0.4


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


# ---------- 打分明细(factors) ----------
def test_get_scoring_returns_factors(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/admin/scoring", headers=hdrs(token))
    d = r.json()
    fac = d["scoring"]["factors"]
    assert set(fac.keys()) == {"bid", "activity", "warn", "market", "yesterday"}
    assert fac["bid"]["buckets"][0] == ["3", "5.5", 1.0]
    assert "default" in fac["bid"]


def test_factor_buckets_default_consistency():
    """默认分档打分与原硬编码逻辑一致(边界值抽样)"""
    cfg = scorer.get_scoring_cfg()
    # 竞价涨幅: 3~5.5=1.0, >5.5或2~3=0.88, 1.5~2=0.65, 0~1.5=0.4, 其余=0.1
    assert scorer.get_factor_score(cfg, "bid", 3.0) == 1.0
    assert scorer.get_factor_score(cfg, "bid", 5.49) == 1.0
    assert scorer.get_factor_score(cfg, "bid", 5.5) == 0.88
    assert scorer.get_factor_score(cfg, "bid", 2.5) == 0.88
    assert scorer.get_factor_score(cfg, "bid", 1.8) == 0.65
    assert scorer.get_factor_score(cfg, "bid", 0.5) == 0.4
    assert scorer.get_factor_score(cfg, "bid", 0) == 0.1
    # 换手率: >=0.8=1.0, 0.4~0.8=0.88, 0.2~0.4=0.72, 0.08~0.2=0.5, 0~0.08=0.3
    assert scorer.get_factor_score(cfg, "activity", 0.8) == 1.0
    assert scorer.get_factor_score(cfg, "activity", 0.5) == 0.88
    assert scorer.get_factor_score(cfg, "activity", 0.3) == 0.72
    assert scorer.get_factor_score(cfg, "activity", 0.1) == 0.5
    assert scorer.get_factor_score(cfg, "activity", 0.05) == 0.3
    # 异动: 5=1.0, 4=0.85, 3=0.6, 其余=0.18
    assert scorer.get_factor_score(cfg, "warn", 5) == 1.0
    assert scorer.get_factor_score(cfg, "warn", 4) == 0.85
    assert scorer.get_factor_score(cfg, "warn", 3) == 0.6
    assert scorer.get_factor_score(cfg, "warn", 2) == 0.18
    # 市值: <30=1.0, 30~60=0.88, 60~120=0.68, 120~250=0.45, >=250=0.22
    assert scorer.get_factor_score(cfg, "market", 29.9) == 1.0
    assert scorer.get_factor_score(cfg, "market", 30) == 0.88
    assert scorer.get_factor_score(cfg, "market", 100) == 0.68
    assert scorer.get_factor_score(cfg, "market", 200) == 0.45
    assert scorer.get_factor_score(cfg, "market", 250) == 0.22
    # 昨日涨幅: 3~9.5=0.9, 1~3=0.65, 0~1=0.4, -3~0=0.25, 其余=0.15
    assert scorer.get_factor_score(cfg, "yesterday", 3) == 0.9
    assert scorer.get_factor_score(cfg, "yesterday", 9.49) == 0.9
    assert scorer.get_factor_score(cfg, "yesterday", 9.5) == 0.65
    assert scorer.get_factor_score(cfg, "yesterday", 2) == 0.65
    assert scorer.get_factor_score(cfg, "yesterday", 0.5) == 0.4
    assert scorer.get_factor_score(cfg, "yesterday", -1) == 0.25
    assert scorer.get_factor_score(cfg, "yesterday", -5) == 0.15


def test_put_custom_factor_bucket_affects_score(client, first_user):
    """自定义竞价分档(全部区间都给满分)后, 概率应上升"""
    token, _, _ = first_user
    cfg = dict(DEFAULT)
    cfg["factors"] = {
        "bid": {"buckets": [["0", "99", 1.0]], "default": 0.1},
        "activity": DEFAULT["factors"]["activity"],
        "warn": DEFAULT["factors"]["warn"],
        "market": DEFAULT["factors"]["market"],
        "yesterday": DEFAULT["factors"]["yesterday"],
    }
    r = client.put("/api/admin/scoring", json={"scoring": cfg}, headers=hdrs(token))
    assert r.status_code == 200
    # 构造一个竞价涨幅 1.0(原得 0.4, 现应得 1.0)的标的
    raw = {"f2": 18.50, "f3": 3.20, "f4": 3.10, "f5": 150000.0, "f6": 2800.0,
           "f8": 5.50, "f10": 1.80, "f12": "600001", "f14": "测试甲",
           "f17": 18.90, "f18": 17.90, "f20": 5.0e10, "f21": 4.0e9,
           "f100": "软件服务", "f102": "广东", "f103": "AI概念",
           "f615": 3.50, "f616": 5.0e7, "f617": 300.0, "f618": 400.0, "f630": 3}
    raw["f615"] = 1.0   # 竞价涨幅 1%(默认档 0.4, 自定义全区间 1.0)
    p_new = scorer.compute_score(raw)["probability"]
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()
    p_default = scorer.compute_score(raw)["probability"]
    assert p_new > p_default


def test_put_invalid_factor_bucket(client, first_user):
    token, _, _ = first_user
    # 下限>=上限
    bad = dict(DEFAULT)
    bad["factors"] = {k: v for k, v in DEFAULT["factors"].items()}
    bad["factors"]["bid"] = {"buckets": [["5", "3", 1.0]], "default": 0.1}
    r = client.put("/api/admin/scoring", json={"scoring": bad}, headers=hdrs(token))
    assert r.status_code == 400 and "下限需小于上限" in r.json().get("msg", "")
    # 得分>1
    bad = dict(DEFAULT)
    bad["factors"] = {k: dict(v) for k, v in DEFAULT["factors"].items()}
    bad["factors"]["warn"] = {"buckets": [["3", "4", 1.5]], "default": 0.1}
    r = client.put("/api/admin/scoring", json={"scoring": bad}, headers=hdrs(token))
    assert r.status_code == 400 and "0~1" in r.json().get("msg", "")
    # 未知因子
    bad = dict(DEFAULT)
    bad["factors"] = {"hack": {"buckets": [["0", "1", 1.0]], "default": 0.1}}
    r = client.put("/api/admin/scoring", json={"scoring": bad}, headers=hdrs(token))
    assert r.status_code == 400 and "未知因子" in r.json().get("msg", "")


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


# ---------- 管理员重置用户密码 ----------
def test_admin_reset_password_ok(client, first_user, second_user):
    """管理员给普通用户重置密码后, 新密码可登录, 旧密码失效"""
    token, _, _ = first_user
    _, uname2 = second_user
    r = client.post("/api/admin/users/reset-password",
                    json={"username": uname2, "password": "qwer1234"},
                    headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and d["username"] == uname2
    # 新密码登录成功
    r2 = client.post("/api/login", json={"username": uname2, "password": "qwer1234"})
    assert r2.status_code == 200 and r2.json().get("ok")
    # 旧密码登录失败
    r3 = client.post("/api/login", json={"username": uname2, "password": "test123456"})
    assert r3.status_code == 401


def test_admin_reset_password_short(client, first_user, second_user):
    """密码少于6位被拒绝"""
    token, _, _ = first_user
    _, uname2 = second_user
    r = client.post("/api/admin/users/reset-password",
                    json={"username": uname2, "password": "123"},
                    headers=hdrs(token))
    assert r.status_code == 400


def test_admin_reset_password_self_forbidden(client, first_user):
    """不能重置自己的密码(防误操作锁死管理员)"""
    token, uname, _ = first_user
    r = client.post("/api/admin/users/reset-password",
                    json={"username": uname, "password": "qwer1234"},
                    headers=hdrs(token))
    assert r.status_code == 400


def test_admin_reset_password_not_found(client, first_user):
    token, _, _ = first_user
    r = client.post("/api/admin/users/reset-password",
                    json={"username": "no_such_user_xyz", "password": "qwer1234"},
                    headers=hdrs(token))
    assert r.status_code == 404


def test_admin_reset_password_requires_admin(client, second_user):
    """非管理员调用返回403"""
    token, _ = second_user
    r = client.post("/api/admin/users/reset-password",
                    json={"username": "anyone", "password": "qwer1234"},
                    headers=hdrs(token))
    assert r.status_code == 403
