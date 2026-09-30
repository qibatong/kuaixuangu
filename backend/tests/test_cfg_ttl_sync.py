# -*- coding: utf-8 -*-
"""配置缓存的多 worker 一致性(2026-09-30)
==========================================
背景(主人问"后台改参数会同步到后端吗? 需要设置同步后自动重启吗"时查实):
线上/测试机都是 `uvicorn --workers 2`, 而评分配置缓存在**进程内存**
(scorer._scoring_cfg / score_spot._spot_cfg), 会员配置则写进运行时 config 模块属性,
admin 保存后的 reload_*() **只能清处理该请求的那个进程** ⇒ 另一半 worker 一直用
旧配置, 且运行时没有任何地方会 force 刷新 ⇒ **只有重启才恢复**。
测试机实测: 保存 w_bid 0.28→0.45 后, 30 次请求 19 次旧值 / 11 次新值。

修复: 进程内缓存改为"每 settings.CFG_TTL 秒读一次配置原文当指纹, 变了才重建"
(settings.raw), 会员配置由 main.py 全局中间件做同款比对。

本文件钉住的不变量(每条都对应一个"改回旧写法就会挂"的点):
  1. 管理端保存后**不调 reload**, TTL 到期即自动跟上(核心修复);
  2. TTL **未到期**时一次库都不读 ⇒ 节流有效
     (一条选股请求逐票调 get_scoring_cfg 可达 200 次, 每次都读 SQLite 才是浪费);
  3. 读库失败时**沿用现有缓存**, 绝不回落到 DEFAULT_SCORING
     —— 否则一次 SQLite 抖动就把线上权重打回代码默认值;
  4. 冷启动 + 读库失败也必须返回 dict(调用方直接 .get() 会崩);
  5. 会员配置同款按需重载 ⇒ 额度/注册开关不再"要重启才生效"。
"""
import os
import sqlite3
import time

import pytest

from app.api import admin
from app.core import config
from app.services import scorer, settings
from app.services.picker import score_spot

DEFAULT = dict(scorer.DEFAULT_SCORING)
DEFAULT_SPOT = dict(score_spot.DEFAULT_SCORING_SPOT)

# 会被会员配置改写的运行时属性(用例结束后必须逐项还原)
_MEMBER_ATTRS = ("NEW_USER_DAYS", "NEW_USER_MEMBER_LEVEL", "INVITE_REWARD_DAYS",
                 "QUOTA_PICKER_DAILY", "QUOTA_AIPICK_DAILY", "QUOTA_AUCTION_DAILY",
                 "QUOTA_CHECKIN_BONUS", "REG_OPEN", "REG_IP_DAY_LIMIT",
                 "INVITE_SAME_IP_LIMIT")


def _raw_db_set(key, value_json):
    """直接改库(绕过 settings.set), 用于还原"原本没有这行"的情况"""
    conn = sqlite3.connect(os.environ["BID_DB_PATH"])
    if value_json is None:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO settings (key,value,updated_at) VALUES (?,?,?)",
                     (key, value_json, int(time.time())))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _ttl(monkeypatch):
    """固定 TTL=3s, 让判定不受环境变量 KX_CFG_TTL 影响(取值是运行时读的)"""
    monkeypatch.setattr(settings, "CFG_TTL", 3.0)


@pytest.fixture(autouse=True)
def _restore_cfg():
    """用例后把**库**与**运行时**都还原 —— 否则会污染配额/注册类用例"""
    old_member_raw = settings.raw("member_conf")
    old_attrs = {a: getattr(config, a, None) for a in _MEMBER_ATTRS}
    yield
    settings.set("scoring", DEFAULT)
    settings.set("scoring_spot", DEFAULT_SPOT)
    scorer.reload_scoring_cfg()
    score_spot.reload_spot_cfg()
    _raw_db_set("member_conf", old_member_raw if old_member_raw else None)
    for a, v in old_attrs.items():
        if v is not None:
            setattr(config, a, v)
    admin._member_conf_fp = None
    admin._member_conf_chk = 0.0


def _ttl_expired(mod, chk_attr):
    """模拟"这个 worker 的 TTL 到期了": 把上次比对时间推回过去,
    但**保留它的缓存与旧指纹** —— 这正是"另一个 worker"的真实状态"""
    setattr(mod, chk_attr, 0.0)


def _set_scoring(**kw):
    """写库但不调 reload(模拟"管理员在**另一个** worker 上保存")"""
    settings.set("scoring", dict(DEFAULT, **kw))


# ---------------------------------------------------------------- 竞价评分配置


def test_scoring_autosync_without_reload():
    """1. 不调 reload, TTL 到期后自动跟上 —— 本次修复的核心"""
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()
    assert abs(scorer.get_scoring_cfg()["w_bid"] - DEFAULT["w_bid"]) < 1e-9

    _set_scoring(w_bid=0.42)                      # 管理员保存(此进程没调 reload)
    _ttl_expired(scorer, "_scoring_chk")          # 另一个 worker 的 TTL 到期
    assert abs(scorer.get_scoring_cfg()["w_bid"] - 0.42) < 1e-9


def test_scoring_throttles_db_reads(monkeypatch):
    """2. TTL 内一次库都不读(缓存必须还在, 否则逐票评分会退化成 200 次查库)"""
    real_raw = settings.raw
    calls = []

    def counted(key):
        calls.append(key)
        return real_raw(key)

    monkeypatch.setattr(settings, "raw", counted)
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()
    calls.clear()
    for _ in range(50):
        scorer.get_scoring_cfg()
    assert calls == [], "TTL 内不应读库, 实际读了 %d 次" % len(calls)


def test_scoring_no_reread_within_ttl():
    """2b. TTL 未到期时即使库里已改, 也仍用缓存(节流语义, 不是 bug)"""
    settings.set("scoring", DEFAULT)
    scorer.reload_scoring_cfg()
    scorer._scoring_chk = time.time()
    _set_scoring(w_bid=0.42)
    assert abs(scorer.get_scoring_cfg()["w_bid"] - DEFAULT["w_bid"]) < 1e-9


def test_scoring_keeps_cache_when_db_read_fails(monkeypatch):
    """3. 读库失败 ⇒ 沿用现有缓存, 绝不回落到 DEFAULT_SCORING"""
    _set_scoring(w_bid=0.42)
    scorer.reload_scoring_cfg()
    assert abs(scorer.get_scoring_cfg()["w_bid"] - 0.42) < 1e-9

    monkeypatch.setattr(settings, "raw", lambda key: None)       # 指纹读失败
    monkeypatch.setattr(settings, "get", lambda key, d=None: d)  # 值也读不到
    _ttl_expired(scorer, "_scoring_chk")
    assert abs(scorer.get_scoring_cfg()["w_bid"] - 0.42) < 1e-9


def test_scoring_cold_start_read_failure_returns_dict(monkeypatch):
    """4. 冷启动 + 读库失败: 返回默认 dict, 不能返回 None"""
    monkeypatch.setattr(settings, "raw", lambda key: None)
    monkeypatch.setattr(settings, "get", lambda key, d=None: d)
    scorer._scoring_cfg = None
    scorer._scoring_fp = None
    scorer._scoring_chk = 0.0
    cfg = scorer.get_scoring_cfg(force=True)
    assert isinstance(cfg, dict) and "w_bid" in cfg


# ---------------------------------------------------------------- 盘中评分配置


def test_spot_autosync_without_reload():
    """5. 盘中策略同款: 不调 reload 也能自动跟上"""
    settings.set("scoring_spot", DEFAULT_SPOT)
    score_spot.reload_spot_cfg()
    base = score_spot.get_spot_cfg()["w_chg"]
    assert abs(base - 0.77) > 1e-9, "默认值不应等于测试用的新值, 否则本用例无意义"

    settings.set("scoring_spot", dict(DEFAULT_SPOT, w_chg=0.77))
    _ttl_expired(score_spot, "_spot_chk")
    assert abs(score_spot.get_spot_cfg()["w_chg"] - 0.77) < 1e-9


# ---------------------------------------------------------------- 会员配置


def test_member_conf_autosync():
    """6. 会员配置: 另一 worker 的额度也能自动跟上(改前要等重启)"""
    settings.set("member_conf", {"quota_picker_daily": 7})
    admin._member_conf_chk = 0.0        # TTL 到期
    admin._member_conf_fp = None        # 另一个 worker 还没见过这版
    assert admin.ensure_member_conf_fresh() is True
    assert config.QUOTA_PICKER_DAILY == 7


def test_member_conf_throttled_and_noop():
    """7. TTL 内不重载; 指纹没变也不重载(不能每个请求都 apply 一遍)"""
    admin._member_conf_fp = None
    admin._member_conf_chk = 0.0
    assert admin.ensure_member_conf_fresh() is True     # 第一次对齐
    assert admin.ensure_member_conf_fresh() is False, "指纹未变不应重复 apply"
    admin._member_conf_chk = time.time()                # TTL 未到期
    assert admin.ensure_member_conf_fresh() is False


def test_member_conf_keeps_runtime_on_db_failure(monkeypatch):
    """8. 指纹读失败时不 apply ⇒ 不能把后台配好的额度打回环境变量默认值"""
    settings.set("member_conf", {"quota_picker_daily": 7})
    admin._member_conf_fp = None
    admin._member_conf_chk = 0.0
    assert admin.ensure_member_conf_fresh() is True
    assert config.QUOTA_PICKER_DAILY == 7

    monkeypatch.setattr(settings, "raw", lambda key: None)
    admin._member_conf_chk = 0.0
    admin._member_conf_fp = None
    assert admin.ensure_member_conf_fresh() is False
    assert config.QUOTA_PICKER_DAILY == 7, "读库失败不得改动运行时额度"
