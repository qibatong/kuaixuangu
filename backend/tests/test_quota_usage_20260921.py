# -*- coding: utf-8 -*-
"""配额用量统计(看板卡片) —— 2026-09-21

背景: 管理端看板「今日配额使用」卡片旧版直接 `v-for` 遍历后端返回的 dict,
把内部键名原样漏到界面上(用户看到 `checkin_today 0` / `limits picker:3 …`),
且标题写「Top」却没有任何排行。本次把这三点都修正:
  1. `quota_stats()` 返回**固定字段**, `usage_top()` 提供真实排行;
  2. 用量来源 = CacheStore 计数 key `quota:{feature}:{uid}:{date}`;
  3. **必须排除**同前缀的 `quota:bonus:`(额度, 不是用量) 与 `quota:dedup:`(10s 去重标记)。
本文件就是给第 3 条上锁 —— 混入 bonus 会让用量虚高, 是对外数字正确性问题。
"""
from app.core import config
from app.services import quota as quota_svc
from app.services.cache_store import store

DATE = quota_svc._bj_date()


def _set(key, val):
    """按生产写入形态落 key: store.incr 落 `str(n)`; 这里用 store.set 落 int(json → `2`)。
    ★ 不要传 str: store.set 会 json.dumps("2") 得到带引号的 `"2"`, 与生产形态不符。"""
    store.set(key, int(val), ttl=86400 + 3600)


# ==================== 1. 严格只认"用量"key ====================
def test_usage_top_excludes_bonus_and_dedup_keys(client, create_user_token):
    """`quota:bonus:`(签到额度) 与 `quota:dedup:`(去重标记) 绝不能被算作用量"""
    u = create_user_token(member_level=0)
    uid = u["uid"]
    quota_svc.reset_user(uid, "picker", bonus=True)

    _set("quota:picker:%d:%s" % (uid, DATE), 2)        # 真实用量: 2
    _set("quota:bonus:picker:%d:%s" % (uid, DATE), 3)  # 签到加成: 是额度, 不是用量
    _set("quota:dedup:%d:picker" % uid, 1)             # 去重标记: 无日期段, 非次数

    recs = [r for r in quota_svc.usage_top(date=DATE)["rows"] if r["uid"] == uid]
    assert len(recs) == 1, "同前缀的 bonus/dedup key 被误算成用量: %s" % recs
    assert recs[0]["used"] == 2, "用量应=2(不含 bonus 的 3): %s" % recs[0]
    assert recs[0]["feature"] == "picker"


def test_usage_top_tolerates_json_string_value(client, create_user_token):
    """kv_cache 的 val 历史上既可能是 `2` 也可能是 `"2"`(store.set 走 json.dumps)。
    两种都要认; 认不出的脏值按 0 处理, 不能让一张榜因为一个脏值整体报错。"""
    u = create_user_token(member_level=0)
    uid = u["uid"]
    store.set("quota:picker:%d:%s" % (uid, DATE), "7", ttl=86400 + 3600)   # 带引号形态
    store.set("quota:aipick:%d:%s" % (uid, DATE), "oops", ttl=86400 + 3600)  # 脏值
    recs = {r["feature"]: r["used"] for r in quota_svc.usage_top(date=DATE)["rows"]
            if r["uid"] == uid}
    assert recs.get("picker") == 7, "带引号的数值应被解析为 7: %s" % recs
    assert "aipick" not in recs, "脏值应按 0 处理(不占榜): %s" % recs


def test_usage_top_ignores_other_dates(client, create_user_token):
    """只统计指定日期, 历史日期的 key 不得进入今日榜"""
    u = create_user_token(member_level=0)
    uid = u["uid"]
    _set("quota:picker:%d:2000-01-01" % uid, 99)
    assert [r for r in quota_svc.usage_top(date=DATE)["rows"] if r["uid"] == uid] == []


def test_usage_top_ignores_zero_and_unknown_feature(client, create_user_token):
    """0 次不占榜; 非注册 feature 的前缀不认"""
    u = create_user_token(member_level=0)
    uid = u["uid"]
    _set("quota:picker:%d:%s" % (uid, DATE), 0)
    _set("quota:unknown:%d:%s" % (uid, DATE), 5)
    assert [r for r in quota_svc.usage_top(date=DATE)["rows"] if r["uid"] == uid] == []


# ==================== 2. 排序 / 截断 / 汇总 ====================
def test_usage_top_sorted_desc_and_limited(client, create_user_token):
    """降序 + limit 生效 + feature 过滤。注意 kv_cache 是全测试共享的同一张临时库,
    所以断言只锚定"我这三个 uid 的相对次序"与"全局严格降序", 不断言全局总条数。"""
    u1 = create_user_token(member_level=0)
    u2 = create_user_token(member_level=0)
    u3 = create_user_token(member_level=0)
    mine = (u1["uid"], u2["uid"], u3["uid"])
    for uid in mine:
        quota_svc.reset_user(uid, "picker", bonus=True)
        quota_svc.reset_user(uid, "aipick", bonus=True)
    _set("quota:picker:%d:%s" % (u1["uid"], DATE), 1)
    _set("quota:picker:%d:%s" % (u2["uid"], DATE), 9)
    _set("quota:aipick:%d:%s" % (u3["uid"], DATE), 5)

    got = quota_svc.usage_top(date=DATE, limit=50)
    order = [r["used"] for r in got["rows"]]
    assert order == sorted(order, reverse=True), "未按用量降序: %s" % order
    assert [r["uid"] for r in got["rows"] if r["uid"] in mine] == \
        [u2["uid"], u3["uid"], u1["uid"]], "三人相对次序应为 9>5>1"
    assert len(quota_svc.usage_top(date=DATE, limit=2)["rows"]) == 2, "limit 未生效"
    assert got["users"] >= 3 and got["by_feature"]["picker"] >= 10
    only_ai = quota_svc.usage_top(date=DATE, feature="aipick", limit=50)
    assert {r["feature"] for r in only_ai["rows"]} == {"aipick"}, "feature 过滤失效"
    assert [r["used"] for r in only_ai["rows"] if r["uid"] == u3["uid"]] == [5]


# ==================== 3. quota_stats 契约(锁死字段名) ====================
def test_quota_stats_returns_fixed_chinese_friendly_keys(client, create_user_token):
    """字段集合必须固定 —— 前端按字段名渲染, 不再盲遍历(盲遍历才会漏英文键)"""
    st = quota_svc.quota_stats()
    assert set(st) == {
        "date", "limits", "checkin_bonus_per_day",
        "checkin_today", "bonus_granted_today",
        "usage_total", "usage_users", "usage_by_feature", "usage_top",
    }, "字段集合变了, 前端看板卡片需同步改: %s" % sorted(st)
    assert st["date"] == DATE
    assert st["limits"] == {"picker": quota_svc.base_limit("picker"),
                            "aipick": quota_svc.base_limit("aipick"),
                            "auction": quota_svc.base_limit("auction")}
    assert st["checkin_bonus_per_day"] == quota_svc.base_limit("auction") * 0 + 3
    assert isinstance(st["usage_top"], list)


def test_quota_stats_top_carries_username(client, create_user_token):
    """Top 条目必须带 username(否则管理端只能看 uid, 无法定位到人)。
    写一个足够大的用量以稳稳排进 Top10 —— kv_cache 是全测试共享的临时库。"""
    u = create_user_token(member_level=0)
    quota_svc.reset_user(u["uid"], "picker", bonus=True)
    _set("quota:picker:%d:%s" % (u["uid"], DATE), 9999)
    st = quota_svc.quota_stats()
    row = [r for r in st["usage_top"] if r["uid"] == u["uid"]]
    assert row, "该用户应有用量记录"
    assert row[0]["username"] == u["username"]
    assert st["usage_total"] >= 9999 and st["usage_users"] >= 1


def test_quota_stats_survives_store_failure(monkeypatch):
    """kv_cache 读失败时必须退回空榜而不是抛异常 —— 看板不能因为统计挂掉整页"""
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(quota_svc.database, "get_conn", _boom)
    got = quota_svc.usage_top(date=DATE)
    assert got["rows"] == [] and got["total"] == 0 and got["users"] == 0
