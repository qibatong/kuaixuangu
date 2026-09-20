# -*- coding: utf-8 -*-
"""管理端接口测试: 权限 / 用户列表统计 / 评分权重读写与生效"""
import os
import sqlite3

import pytest

from app.services import scorer, settings

DEFAULT = dict(scorer.DEFAULT_SCORING)
VALID = {"w_bid": 0.30, "w_activity": 0.35, "w_warn": 0.15, "w_market": 0.12, "w_yesterday": 0.08,
         "conf_warn_high": 10, "conf_turnover": 8, "conf_bid": 7}


def _prob(raw):
    """用**当前生效的**评分配置给一行东财行情打分(走唯一评分实现 picker/score.py)。

    2026-09-11 老链路退役前此处直调 scorer.compute_score; 现改走契约层 → 评分层,
    与线上同一条代码路径, 保证"管理员改配置 → 概率变化"这条断言测的是真实实现。
    """
    from app.services.picker.contract import QuoteRow
    from app.services.picker.score import compute_score
    row = QuoteRow.from_eastmoney(raw, auction_window=True)
    return compute_score(row, scorer.get_scoring_cfg()).probability


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
    # 2026-09-08: 新增 bid_strength(替代失活的 f630 异动等级) → 断言改"包含"语义,
    # 避免以后每加一个因子就要改一次这里的硬编码集合(原断言 == 5 个因子已失败一次)。
    assert {"bid", "activity", "warn", "market", "yesterday"} <= set(fac.keys())
    assert "bid_strength" in fac          # 竞价强度(三层合成, 对东财免疫)
    # 2026-09-09: 负涨幅低分桶(中石科技竞涨-8.01%吃 default 0.1 事故) → 首桶为负桶
    assert fac["bid"]["buckets"][0][0] == "-99"          # 负桶在前: [-99,0.001)→0.05
    assert ["3", "5.5", 1.0] in fac["bid"]["buckets"]    # 正区间桶仍完整
    assert "default" in fac["bid"]


def test_factor_buckets_default_consistency():
    """默认分档打分表(边界值抽样)

    2026-09-11: 打分函数唯一实现是 picker/score_factors.factor_score(老链路
    scorer.get_factor_score 已退役), 断言值随实现迁移, 语义与边界完全不变。
    """
    from app.services.picker.score_factors import factor_score as fs
    cfg = scorer.get_scoring_cfg()
    # 竞价涨幅: 3~5.5=1.0, >5.5或2~3=0.88, 1.5~2=0.65, 0.001~1.5=0.4,
    #           负/平开(<0.001)=0.05(2026-09-09 低分桶), 数据缺失=default 0.1
    assert fs(cfg, "bid", 3.0) == 1.0
    assert fs(cfg, "bid", 5.49) == 1.0
    assert fs(cfg, "bid", 5.5) == 0.88
    assert fs(cfg, "bid", 2.5) == 0.88
    assert fs(cfg, "bid", 1.8) == 0.65
    assert fs(cfg, "bid", 0.5) == 0.4
    assert fs(cfg, "bid", 0) == 0.05          # 平开落负桶
    assert fs(cfg, "bid", -8.01) == 0.05      # 大跌惩罚分
    # 换手率: >=0.8=1.0, 0.4~0.8=0.88, 0.2~0.4=0.72, 0.08~0.2=0.5, 0~0.08=0.3
    assert fs(cfg, "activity", 0.8) == 1.0
    assert fs(cfg, "activity", 0.5) == 0.88
    assert fs(cfg, "activity", 0.3) == 0.72
    assert fs(cfg, "activity", 0.1) == 0.5
    assert fs(cfg, "activity", 0.05) == 0.3
    # 异动: 5=1.0, 4=0.85, 3=0.6, 其余=0.18
    assert fs(cfg, "warn", 5) == 1.0
    assert fs(cfg, "warn", 4) == 0.85
    assert fs(cfg, "warn", 3) == 0.6
    assert fs(cfg, "warn", 2) == 0.18
    # 市值(2026-09-20 改自由流通口径): <15=1.0, 15~30=0.88, 30~60=0.68, 60~125=0.45, >=125=0.22
    assert fs(cfg, "market", 14.9) == 1.0
    assert fs(cfg, "market", 15) == 0.88
    assert fs(cfg, "market", 50) == 0.68
    assert fs(cfg, "market", 100) == 0.45
    assert fs(cfg, "market", 125) == 0.22
    # 昨日涨幅: 3~9.5=0.9, 1~3=0.65, 0~1=0.4, -3~0=0.25, 其余=0.15
    assert fs(cfg, "yesterday", 3) == 0.9
    assert fs(cfg, "yesterday", 9.49) == 0.9
    assert fs(cfg, "yesterday", 9.5) == 0.65
    assert fs(cfg, "yesterday", 2) == 0.65
    assert fs(cfg, "yesterday", 0.5) == 0.4
    assert fs(cfg, "yesterday", -1) == 0.25
    assert fs(cfg, "yesterday", -5) == 0.15


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
    p_new = _prob(raw)
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()
    p_default = _prob(raw)
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
    p_big_default = _prob(big)
    p_small_default = _prob(small)
    assert p_small_default > p_big_default   # 默认: 小市值分更高

    # 把市值权重调高到 0.5(其他按比例缩到 0.5 合计), 差距应拉大
    high_mv = {"w_bid": 0.15, "w_activity": 0.15, "w_warn": 0.10, "w_market": 0.50, "w_yesterday": 0.10,
               "conf_warn_high": 10, "conf_turnover": 8, "conf_bid": 7}
    r = client.put("/api/admin/scoring", json={"scoring": high_mv}, headers=hdrs(token))
    assert r.status_code == 200
    p_big_high = _prob(big)
    p_small_high = _prob(small)
    assert (p_small_high - p_big_high) > (p_small_default - p_big_default) * 0.9


# ---------- 管理员重置用户密码 ----------
def test_admin_reset_password_ok(client, first_user, create_user_token):
    """管理员给普通用户重置密码后, 新密码可登录, 旧密码失效。"""
    token, _, _ = first_user
    u = create_user_token()
    target = u["username"]
    r = client.post("/api/admin/users/reset-password",
                    json={"username": target, "password": "qwer1234"},
                    headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") and d["username"] == target
    # 新密码登录成功
    r2 = client.post("/api/login", json={"username": target, "password": "qwer1234"})
    assert r2.status_code == 200 and r2.json().get("ok")
    # 旧密码登录失败
    r3 = client.post("/api/login", json={"username": target, "password": u["password"]})
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


def test_admin_scoring_invalid_mode(client, first_user):
    """非法 mode 400"""
    token, _, _ = first_user
    r = client.get("/api/admin/scoring?mode=xxx", headers=hdrs(token))
    assert r.status_code == 400
    r2 = client.put("/api/admin/scoring?mode=xxx", json={"scoring": VALID}, headers=hdrs(token))
    assert r2.status_code == 400


def test_admin_scoring_spot_offline(client, first_user):
    """2026-09-09 盘中评分配置随 spot 一并下线: 传 spot 返回 400(不再有独立因子表)"""
    token, _, _ = first_user
    r = client.get("/api/admin/scoring?strategy=spot", headers=hdrs(token))
    assert r.status_code == 400
    r2 = client.put("/api/admin/scoring?mode=spot", json={"scoring": VALID}, headers=hdrs(token))
    assert r2.status_code == 400


# ---------- 全局默认筛选参数(defaults) ----------
def _restore_default_filters():
    """测试后恢复 default_filters, 避免污染其他用例"""
    settings.set("default_filters", {})


def test_admin_defaults_put_ok(client, first_user):
    """普通保存: 写入 settings, 不带 force 时 forceCleared=0"""
    token, _, _ = first_user
    try:
        r = client.put("/api/admin/defaults",
                       json={"defaults": {"bidAmtFloor": 2000}},
                       headers=hdrs(token))
        assert r.status_code == 200
        d = r.json()
        assert d.get("ok") and d.get("forceCleared") == 0
        assert d["defaults"]["bidAmtFloor"] == 2000
        assert settings.get("default_filters")["bidAmtFloor"] == 2000
    finally:
        _restore_default_filters()


def test_admin_defaults_put_invalid(client, first_user):
    """非法字段/类型/缺参 400"""
    token, _, _ = first_user
    r = client.put("/api/admin/defaults", json={"defaults": {"hack": 1}}, headers=hdrs(token))
    assert r.status_code == 400 and "未知字段" in r.json().get("msg", "")
    r = client.put("/api/admin/defaults", json={"defaults": {"bidAmtFloor": "x"}}, headers=hdrs(token))
    assert r.status_code == 400 and "需为数字" in r.json().get("msg", "")
    r = client.put("/api/admin/defaults", json={"defaults": {"limitUp": "yes"}}, headers=hdrs(token))
    assert r.status_code == 400 and "需为布尔值" in r.json().get("msg", "")
    r = client.put("/api/admin/defaults", json={}, headers=hdrs(token))
    assert r.status_code == 400


def test_admin_defaults_force_clears_user_filters(client, first_user, second_user):
    """force=true: 清除所有用户筛选偏好(仅筛选字段), 保留主题(bg/font)"""
    token, _, _ = first_user
    token2, _ = second_user
    # 用户先保存: 筛选字段 + 主题字段
    r = client.post("/api/prefs",
                    json={"settings": {"bidGt": 9, "stSuspend": False, "bg": "light", "font": "lg"}},
                    headers=hdrs(token2))
    assert r.status_code == 200
    try:
        r = client.put("/api/admin/defaults",
                       json={"defaults": {"bidAmtFloor": 2000}, "force": True},
                       headers=hdrs(token))
        assert r.status_code == 200
        d = r.json()
        assert d.get("ok") and d.get("forceCleared", 0) >= 1
        assert d["defaults"]["bidAmtFloor"] == 2000
        # 用户偏好: 筛选字段被清除, 主题保留
        r2 = client.get("/api/prefs", headers=hdrs(token2))
        s = r2.json().get("settings") or {}
        assert "bidGt" not in s and "stSuspend" not in s
        assert s.get("bg") == "light" and s.get("font") == "lg"
    finally:
        _restore_default_filters()


def test_admin_defaults_no_force_keeps_user_filters(client, first_user, second_user):
    """不带 force: 用户已存筛选偏好不受影响"""
    token, _, _ = first_user
    token2, _ = second_user
    r = client.post("/api/prefs",
                    json={"settings": {"bidGt": 9, "bg": "dark"}},
                    headers=hdrs(token2))
    assert r.status_code == 200
    try:
        r = client.put("/api/admin/defaults",
                       json={"defaults": {"bidAmtFloor": 2000}},
                       headers=hdrs(token))
        assert r.status_code == 200
        assert r.json().get("forceCleared") == 0
        r2 = client.get("/api/prefs", headers=hdrs(token2))
        s = r2.json().get("settings") or {}
        assert s.get("bidGt") == 9 and s.get("bg") == "dark"
    finally:
        _restore_default_filters()


# ---------- 管理员代编辑用户资料 ----------
def test_admin_profile_edit_ok(client, first_user, create_user_token):
    """管理员编辑用户资料(微信名/备注/手机号) -> 成功, 列表可见"""
    token, _, _ = first_user
    u = create_user_token()
    uname = u["username"]
    uid = None
    r2 = client.get("/api/admin/users", headers=hdrs(token))
    for row in r2.json().get("rows", []):
        if row["username"] == uname:
            uid = row["id"]
            break
    assert uid is not None
    # 只改微信名+备注(不动手机/邮箱)
    r3 = client.post("/api/admin/users/profile",
                     json={"uid": uid, "wx_name": "北棠", "remark": "8月微信用户"},
                     headers=hdrs(token))
    assert r3.status_code == 200, r3.text
    d = r3.json()
    assert d.get("ok") and d["wx_name"] == "北棠" and d["remark"] == "8月微信用户"
    # 列表已返回 wx_name/remark 字段
    r4 = client.get("/api/admin/users", headers=hdrs(token))
    row2 = next(x for x in r4.json()["rows"] if x["id"] == uid)
    assert row2.get("wx_name") == "北棠" and row2.get("remark") == "8月微信用户"


def test_admin_profile_invalid_phone(client, first_user, second_user):
    """资料手机号格式错 -> 400"""
    token, _, _ = first_user
    _, uname2 = second_user
    r = client.get("/api/admin/users?keyword=" + uname2, headers=hdrs(token))
    uid2 = r.json()["rows"][0]["id"]
    r = client.post("/api/admin/users/profile",
                    json={"uid": uid2, "phone": "123"}, headers=hdrs(token))
    assert r.status_code == 400


def test_admin_profile_duplicate_email(client, first_user, second_user, create_user_token):
    """资料邮箱与其他用户重复 -> 400"""
    token, _, _ = first_user
    # 建两个用户, 用第二个用户的邮箱去改第一个 -> 应拒绝
    ua = create_user_token()
    ub = create_user_token()
    u1 = ua["username"]
    u2 = ub["username"]
    e2 = ub["email"]
    rows = client.get("/api/admin/users", headers=hdrs(token)).json()["rows"]
    id1 = next(x["id"] for x in rows if x["username"] == u1)
    # 把 u1 邮箱改成 u2 的邮箱 -> 唯一性冲突
    r = client.post("/api/admin/users/profile",
                    json={"uid": id1, "email": e2}, headers=hdrs(token))
    assert r.status_code == 400
    assert "邮箱" in r.json().get("msg", "")


def test_admin_profile_no_fields(client, first_user, second_user):
    """没有要更新的字段 -> 400"""
    token, _, _ = first_user
    _, uname2 = second_user
    r = client.get("/api/admin/users?keyword=" + uname2, headers=hdrs(token))
    uid2 = r.json()["rows"][0]["id"]
    r = client.post("/api/admin/users/profile", json={"uid": uid2}, headers=hdrs(token))
    assert r.status_code == 400


# ---------- 管理员代创建/删除账号 + pay_remark ----------
def test_admin_create_user_ok(client, first_user):
    """管理员创建账号(带会员等级/到期/微信名/付款备注) -> 成功, 可登录"""
    import uuid
    token, _, _ = first_user
    uname = "mk_" + uuid.uuid4().hex[:8]
    phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8)
    email = uuid.uuid4().hex[:8] + "@test.local"
    r = client.post("/api/admin/users/create",
                    json={"username": uname, "password": "Test123456",
                          "phone": phone, "email": email,
                          "member_level": 1, "expire_at": "2026-12-31",
                          "wx_name": "测试微信", "pay_remark": "8月微信月付"},
                    headers=hdrs(token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") and d["username"] == uname and d["member_level"] == 1
    assert d["wx_name"] == "测试微信" and d["pay_remark"] == "8月微信月付"
    assert d["expire_at"] > 0  # 设置了具体日期
    # 新账号可用初始密码登录
    r2 = client.post("/api/login", json={"username": uname, "password": "Test123456"})
    assert r2.status_code == 200


def test_admin_create_duplicate(client, first_user, second_user):
    """用户名/手机号/邮箱冲突时拒绝"""
    token, _, _ = first_user
    _, uname2 = second_user
    phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8) if False else "13800000000"
    # 用 second_user 的用户名, 期望 409
    r = client.post("/api/admin/users/create",
                    json={"username": uname2, "password": "Test123456",
                          "phone": phone, "email": "x@x.local"},
                    headers=hdrs(token))
    assert r.status_code == 409


def test_admin_create_invalid_phone(client, first_user):
    """手机号格式错 -> 400"""
    import uuid
    token, _, _ = first_user
    r = client.post("/api/admin/users/create",
                    json={"username": "mk_" + uuid.uuid4().hex[:8],
                          "password": "Test123456",
                          "phone": "123", "email": uuid.uuid4().hex[:8] + "@x.local"},
                    headers=hdrs(token))
    assert r.status_code == 400


def test_admin_profile_pay_remark(client, first_user, create_user_token):
    """资料接口支持 pay_remark 字段 (会员专属付款备注)"""
    token, _, _ = first_user
    u = create_user_token()
    uname = u["username"]
    # 取 uid
    rows = client.get("/api/admin/users", headers=hdrs(token)).json()["rows"]
    uid = next(x["id"] for x in rows if x["username"] == uname)
    # 写 pay_remark
    r2 = client.post("/api/admin/users/profile",
                     json={"uid": uid, "pay_remark": "8-16微信月付300元", "remark": "老用户"},
                     headers=hdrs(token))
    assert r2.status_code == 200
    d = r2.json()
    assert d["pay_remark"] == "8-16微信月付300元" and d["remark"] == "老用户"
    # 列表返回 pay_remark
    rows2 = client.get("/api/admin/users", headers=hdrs(token)).json()["rows"]
    row2 = next(x for x in rows2 if x["id"] == uid)
    assert row2.get("pay_remark") == "8-16微信月付300元"


def test_admin_delete_user_ok(client, first_user, create_user_token):
    """删除普通用户 -> 成功, 后续登录失败"""
    token, _, _ = first_user
    u = create_user_token()
    uname = u["username"]
    # 取 uid
    rows = client.get("/api/admin/users", headers=hdrs(token)).json()["rows"]
    uid = next(x["id"] for x in rows if x["username"] == uname)
    # 删除
    r2 = client.post("/api/admin/users/delete", json={"uid": uid}, headers=hdrs(token))
    assert r2.status_code == 200, r2.text
    assert r2.json().get("ok")
    # 删除后不能登录
    r3 = client.post("/api/login", json={"username": uname, "password": "Test123456"})
    assert r3.status_code == 401


def test_admin_delete_self_forbidden(client, first_user):
    """不能删除自己"""
    token, _, _ = first_user
    # first_user 的 uid 需要从数据库查
    import sqlite3, os
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    me = next((r for r in conn.execute("SELECT id FROM users WHERE is_admin=1 LIMIT 1")), None)
    conn.close()
    assert me
    r = client.post("/api/admin/users/delete", json={"uid": me[0]}, headers=hdrs(token))
    assert r.status_code == 400


def test_admin_delete_admin_forbidden(client, first_user):
    """不能删除其他管理员(测试环境只有 first_user 一个管理员, 尝试按 username 删自己"""
    token, uname, _ = first_user
    r = client.post("/api/admin/users/delete", json={"username": uname}, headers=hdrs(token))
    # 既触发"删除自己"也触发"管理员", 任何一个 400 都行
    assert r.status_code == 400


# ---------- 会员筛选 tab (服务端过滤) ----------
def test_admin_users_member_tab(client, first_user, create_user_token):
    """管理端列表按 member_tab 服务端过滤: all/member/normal/admin"""
    import uuid
    token, _, _ = first_user
    # 建 3 个不同等级的用户 (member_level 0/1/2)
    for lvl in [0, 1, 2]:
        create_user_token(username="tab_" + uuid.uuid4().hex[:6], member_level=lvl)
    # member tab: 只返回 lvl>0 且非管理员
    r = client.get("/api/admin/users?memberTab=member&pageSize=100",
                   headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    member_rows = [x for x in d["rows"] if x["username"].startswith("tab_")]
    for m in member_rows:
        assert (m["member_level"] or 0) > 0
        assert not m["is_admin"]
    # admin tab: 仅管理员
    r = client.get("/api/admin/users?memberTab=admin&pageSize=100",
                   headers=hdrs(token))
    assert r.status_code == 200
    for row in r.json()["rows"]:
        assert row["is_admin"]
    # normal tab: lvl=0 且非管理员
    r = client.get("/api/admin/users?memberTab=normal&pageSize=100",
                   headers=hdrs(token))
    assert r.status_code == 200
    for row in r.json()["rows"]:
        assert (row["member_level"] or 0) == 0 and not row["is_admin"]
    # total 在不同 tab 应不同 (member + normal + admin 至少 3 个用户; 但 total=member+normal+admin 不一定 = all,
    # 因为 lvl=2/1 也算"member", 且 admin=1 不算 member/normal)
    r_all = client.get("/api/admin/users?memberTab=all&pageSize=1", headers=hdrs(token))
    total_all = r_all.json()["total"]
    r_m = client.get("/api/admin/users?memberTab=member&pageSize=1", headers=hdrs(token))
    total_m = r_m.json()["total"]
    r_n = client.get("/api/admin/users?memberTab=normal&pageSize=1", headers=hdrs(token))
    total_n = r_n.json()["total"]
    r_a = client.get("/api/admin/users?memberTab=admin&pageSize=1", headers=hdrs(token))
    total_a = r_a.json()["total"]
    assert total_m + total_n + total_a == total_all, \
        f"tab 总数不一致: all={total_all} m={total_m} n={total_n} a={total_a}"


# ---------- 管理端搜索扩展: 微信名/备注/付款备注 ----------
def test_admin_search_wx_name_remark(client, first_user, create_user_token):
    """按微信名/备注/付款备注搜索用户"""
    import uuid
    token, _, _ = first_user
    # 创建带微信名+备注的用户
    u = create_user_token(username="search_" + uuid.uuid4().hex[:6])
    uname = u["username"]
    rows = client.get("/api/admin/users?keyword=" + uname, headers=hdrs(token)).json()["rows"]
    uid = next(x["id"] for x in rows if x["username"] == uname)
    # 设置微信名/备注/付款备注
    r = client.post("/api/admin/users/profile",
                    json={"uid": uid, "wx_name": "寻宝探险家", "remark": "微信群老用户",
                          "pay_remark": "月付300元"},
                    headers=hdrs(token))
    assert r.status_code == 200
    # 按微信名搜索
    r = client.get("/api/admin/users?keyword=" + "寻宝", headers=hdrs(token))
    assert r.status_code == 200
    assert any(x["id"] == uid for x in r.json()["rows"]), "按微信名应搜到该用户"
    # 按备注搜索
    r = client.get("/api/admin/users?keyword=" + "老用户", headers=hdrs(token))
    assert any(x["id"] == uid for x in r.json()["rows"]), "按备注应搜到该用户"
    # 按付款备注搜索
    r = client.get("/api/admin/users?keyword=" + "300元", headers=hdrs(token))
    assert any(x["id"] == uid for x in r.json()["rows"]), "按付款备注应搜到该用户"
    # 无关关键词不应命中
    r = client.get("/api/admin/users?keyword=" + "绝不存在xyz", headers=hdrs(token))
    assert not any(x["id"] == uid for x in r.json()["rows"]), "无关关键词不应命中"


# ---------- 付费 / VIP tab 分别过滤 ----------
def test_admin_users_paid_vip_tab(client, first_user, create_user_token):
    """付费 tab 只返回 member_level=1; VIP tab 只返回 member_level=2"""
    import uuid
    token, _, _ = first_user
    # 创建 2 个新用户: paid=level1, vip=level2
    paid_u = create_user_token(username="paid_" + uuid.uuid4().hex[:6], member_level=1)
    vip_u = create_user_token(username="vip_" + uuid.uuid4().hex[:6], member_level=2)
    paid_uname = paid_u["username"]
    vip_uname = vip_u["username"]
    # 取新用户 uid
    rows_all = client.get("/api/admin/users?keyword=paid_&pageSize=20",
                           headers=hdrs(token)).json()["rows"]
    paid_uid = next((r["id"] for r in rows_all if r["username"] == paid_uname), None)
    rows_all = client.get("/api/admin/users?keyword=vip_&pageSize=20",
                           headers=hdrs(token)).json()["rows"]
    vip_uid = next((r["id"] for r in rows_all if r["username"] == vip_uname), None)
    # paid tab: 只返回付费用户
    r = client.get("/api/admin/users?memberTab=paid&pageSize=100",
                   headers=hdrs(token))
    assert r.status_code == 200
    rows = r.json()["rows"]
    # 所有 paid tab 行 member_level==1
    for row in rows:
        assert row["member_level"] == 1
        assert not row["is_admin"]
    # paid_uname 在内, vip_uname 不在内
    paid_in = any(x["id"] == paid_uid for x in rows)
    vip_in = any(x["id"] == vip_uid for x in rows)
    assert paid_in and not vip_in, "paid tab 应只含付费用户"
    # vip tab: 只返回 VIP
    r = client.get("/api/admin/users?memberTab=vip&pageSize=100",
                   headers=hdrs(token))
    rows = r.json()["rows"]
    for row in rows:
        assert row["member_level"] == 2
        assert not row["is_admin"]
    vip_in = any(x["id"] == vip_uid for x in rows)
    paid_in = any(x["id"] == paid_uid for x in rows)
    assert vip_in and not paid_in, "vip tab 应只含 VIP 用户"
    # member tab 总数 = paid + vip
    r_m = client.get("/api/admin/users?memberTab=member&pageSize=1", headers=hdrs(token))
    r_p = client.get("/api/admin/users?memberTab=paid&pageSize=1", headers=hdrs(token))
    r_v = client.get("/api/admin/users?memberTab=vip&pageSize=1", headers=hdrs(token))
    total_m = r_m.json()["total"]
    total_p = r_p.json()["total"]
    total_v = r_v.json()["total"]
    assert total_m == total_p + total_v, f"member({total_m}) 应等于 paid({total_p})+vip({total_v})"
