# -*- coding: utf-8 -*-
"""系统口径 ≡ 首页口径 的可执行证明 (v4.11.46, 2026-09-24 主人拍板)

事故(2026-09-24 测试机实测)
-----------------------------------------------------------------------------
本仓曾有 **四份**彼此独立的筛选默认值, 其中 system_batch 那份漏了 scoreFloor:

    settings.default_filters          scoreFloor = 60   (管理员设定)
    admin.get_default_filters()       scoreFloor = 60   (首页左视图)
    auto_apply._get_system_filter()   scoreFloor = 60   (9:26 自动应用)
    system_batch._system_filter()     无此键 → 经 lock 归一后 **50**(硬编码兜底)

同一时刻同一份 9:25 快照: **系统批次 64 只 vs 首页左视图 27 只**,
而 system_batch 的 docstring 声称"与首页左视图完全一致"。

🔴 根因不是数值抄错, 而是**合并白名单**: `for k, v in cfg.items(): if k in merged`
   只接受"本地副本已有的键" → settings 里的 scoreFloor 根本不进白名单, 静默丢弃。

🔴 为什么原有测试没抓住: test_system_batch_check 里那条
   `test_system_filter_keys_match_validate_filters` 把 raw 转成 qs 再喂
   validate_filters, 而 validate_filters **对缺键有默认值(50)** ⇒ 输出上完全
   看不出"这个键到底存不存在"; 而 `test_system_filter_merges_admin_defaults`
   只 monkeypatch 了副本里**已有**的键(floatMvGt/bidAmtFloor) ——
   正好绕过了 `if k in merged` 这个丢弃点。**测了机制, 没测缺失项。**

本文件要钉死的四件事
-----------------------------------------------------------------------------
1. 四份归一份: 两条锁仓链路 + lock 兜底 + admin 全部引用同一对象;
2. **随动性**: settings 里 scoreFloor 一改, 系统口径必须跟着改(而不是固定兜底 50);
3. **新增键自动跟随**: 将来 admin 加新参数, 只改真相源即可, 不必再改各调用点
   —— 这条专门防"再抄一份副本"的回潮;
4. **真跑对拍**: 首页口径与系统口径喂进同一个过滤器, 被消费的每个键逐值相等,
   且名单逐票相同(含"55 分的票必须被 60 门槛剔除"这条复现 64 vs 27 的钉子)。
"""
import pytest

from app.api import admin
from app.services import auto_apply, filter_defaults, scorer, system_batch
from app.services.picker import filter as pfilter
from app.services.picker import lock as plock
from app.services.picker.contract import QuoteRow
from app.services.picker.score import ScoreResult, ScoredRow

# filter.apply_filters 真正消费的键(见 picker/filter.py 的 f.get / f[...]);
# 口径一致只需在这些键上成立 —— 其余(盘中 chg*/volRatioFloor/turnover*/spotExcludeZT)
# 是已下线 spot 链路的遗留字段, filter 不读。
_CONSUMED = (
    "markets", "limitUp", "stSuspend", "bidGt", "probLt", "confLt", "scoreFloor",
    "floatMvFloor", "floatMvGt", "bidAmtFloor", "priceGt",
)

# 测试机线上那一组(2026-09-24 实测), 用来复现事故现场
_LIVE = dict(limitUp=True, stSuspend=False, bidGt=10, probLt=60, confLt=65,
             floatMvFloor=20, floatMvGt=500, priceGt=200, bidAmtFloor=1000,
             scoreFloor=60)


def _patch_defaults(monkeypatch, **over):
    """把 settings 表的 default_filters 换成受控 dict —— 测试不依赖测试库里的残留值。

    ⚠️ 必须 patch **filter_defaults.settings**(真相源所在模块) ——
       各调用点已不再自己读 settings, patch 原调用点会静默失效(测试仍绿但没测到东西)。
    """
    cfg = dict(filter_defaults.FILTER_DEFAULTS)
    cfg.update(over)
    monkeypatch.setattr(filter_defaults.settings, "get",
                        lambda key, default=None: cfg if key == "default_filters" else default)
    return cfg


def _mk(code, prob, conf=70, bid_chg=5.0, mv=100e8, price=20.0, bid_amt=5e7):
    """构造一行过滤输入。默认值刻意全部**远离**各门槛, 让 scoreFloor 成为唯一变量。"""
    r = QuoteRow(code=code, name="票" + code, bid_change=bid_chg, bid_amt=bid_amt,
                 float_mv=mv, price=price, prev_close=price / (1 + bid_chg / 100.0))
    return ScoredRow(row=r, score=ScoreResult(probability=prob, confidence=conf))


# ---------------------------------------------------------------- 1. 归口
def test_all_call_sites_share_one_truth_source(monkeypatch):
    """四条链路指向同一份默认值 —— 不允许再有任何独立副本"""
    _patch_defaults(monkeypatch, **_LIVE)

    sysf = system_batch._system_filter()
    autof = auto_apply._get_system_filter()
    adm = admin.get_default_filters()

    assert sysf == autof, "系统批次与 9:26 自动应用的筛选条件必须逐键相等"
    assert {k: v for k, v in sysf.items() if k != "markets"} == adm, \
        "系统口径去掉 markets 后必须与管理员全局默认完全一致"
    # lock 的兜底也必须就是真相源本体(而不是又一份长得像的字典)
    assert plock._FILTER_DEFAULTS is filter_defaults.FILTER_DEFAULTS
    # admin 保留的同名别名同样指向本体(防止有人"顺手"在 admin 里重建一份)
    assert admin.DEFAULT_FILTERS_DEFAULT is filter_defaults.FILTER_DEFAULTS


def test_no_duplicate_defaults_left_in_callers():
    """源码守卫: 各调用点不得再出现筛选默认值的字面量字典

    用"存在型"断言而非"不存在型"很难写全, 故直接查最典型的特征键:
    副本一定同时含 floatMvGt 与 scoreFloor 两个键的字面量。
    """
    import inspect
    for mod in (system_batch, auto_apply):
        src = inspect.getsource(mod)
        assert '"floatMvGt":' not in src, \
            "%s 里又出现了筛选默认值字面量 —— 请改为引用 filter_defaults" % mod.__name__


# ---------------------------------------------------------------- 2. 随动性
def test_score_floor_follows_settings(monkeypatch):
    """★ 回归钉子: settings 的 scoreFloor 必须进系统口径(修复前被白名单丢弃)"""
    _patch_defaults(monkeypatch, scoreFloor=60)

    f = system_batch._system_filter()
    assert "scoreFloor" in f, "系统口径缺 scoreFloor → 只能吃 lock 的兜底, 不受管理员控制"
    assert f["scoreFloor"] == 60

    # 经 lock 归一后仍是 60。修复前这里是 50.0(lock._FILTER_DEFAULTS 硬编码兜底)
    assert plock.to_picker_filters(f)["scoreFloor"] == 60.0

    # 管理员改一次, 两条链路一起变
    _patch_defaults(monkeypatch, scoreFloor=75)
    assert system_batch._system_filter()["scoreFloor"] == 75
    assert auto_apply._get_system_filter()["scoreFloor"] == 75


# ---------------------------------------------------------------- 3. 新增键自动跟随
def test_new_default_key_flows_through_without_touching_callers(monkeypatch):
    """将来 admin 加新筛选参数 → 只改真相源, 两条锁仓链路自动带上

    这条专门防"再抄一份副本"回潮: 若哪个调用点又自带键集白名单, 该键就传不下去。
    """
    monkeypatch.setitem(filter_defaults.FILTER_DEFAULTS, "bidLt", 0.0)
    _patch_defaults(monkeypatch, bidLt=1.5)

    assert system_batch._system_filter()["bidLt"] == 1.5
    assert auto_apply._get_system_filter()["bidLt"] == 1.5


# ---------------------------------------------------------------- 4. 真跑对拍
def test_system_scope_equals_frontpage_scope_key_by_key(monkeypatch):
    """首页 validate_filters 口径 vs 系统口径: 被消费的每个键逐值相等"""
    cfg = _patch_defaults(monkeypatch, **_LIVE)

    # 首页左视图: 前端把全局默认塞进 qs(值一律是 list[str])再走 validate_filters
    q = {k: [str(v)] for k, v in cfg.items()}
    q["markets"] = ["hs,cyb,kcb,bj"]
    front = scorer.validate_filters(q)

    sysf = plock.to_picker_filters(system_batch._system_filter())

    for k in _CONSUMED:
        assert front[k] == sysf[k], "口径不一致: %s (首页=%r 系统=%r)" % (k, front[k], sysf[k])
    # bidLt 系统口径不带该键, 但 filter 用 f.get("bidLt", 0) 取值 → 必须等价
    assert sysf.get("bidLt", 0) == front["bidLt"]


def test_score_floor_actually_applies_to_system_scope(monkeypatch):
    """★ 复现 64 vs 27: 55 分的票必须与首页一样被 60 门槛剔除

    修复前系统口径实际生效 50 → 55 分的票被放进来(名单虚胖) —— 这正是
    2026-09-24 测试机上"系统批次 64 只 vs 首页 27 只"的机制。
    """
    cfg = _patch_defaults(monkeypatch, **_LIVE)

    q = {k: [str(v)] for k, v in cfg.items()}
    q["markets"] = ["hs,cyb,kcb,bj"]
    front_f = scorer.validate_filters(q)
    sys_f = plock.to_picker_filters(system_batch._system_filter())

    rows = [_mk("600001", 55), _mk("600002", 65)]
    ctx_front = pfilter.FilterContext()
    ctx_sys = pfilter.FilterContext()

    kept_front = [i.code for i in pfilter.apply_filters(rows, front_f, ctx_front).kept]
    kept_sys = [i.code for i in pfilter.apply_filters(rows, sys_f, ctx_sys).kept]

    assert kept_sys == kept_front, "系统口径名单必须与首页口径逐票相同"
    assert kept_sys == ["600002"], "55 分的票必须被 scoreFloor=60 剔除"

    # 回归钉子: 把门槛退回修复前的实际值 50, 55 分的票就会混进来(旧行为)。
    # (apply_filters 保持输入顺序, 故用集合比较, 不依赖票序)
    legacy = dict(sys_f)
    legacy["scoreFloor"] = 50.0
    legacy_kept = {i.code for i in pfilter.apply_filters(rows, legacy, pfilter.FilterContext()).kept}
    assert legacy_kept == {"600001", "600002"}, "旧口径(50)确实会多放一只 —— 这就是 64 vs 27"


def test_markets_stay_lowercase_in_system_scope(monkeypatch):
    """系统市场范围必须是小写 hs/cyb/kcb/bj(大写会让沪深创科北交全部返回 False → 名单恒空)

    2026-09-29 主人拍板「北交所纳入」⇒ 系统口径与前端默认同加 bj(parity 必须一致)。
    """
    _patch_defaults(monkeypatch, **_LIVE)
    assert system_batch._system_filter()["markets"] == ["hs", "cyb", "kcb", "bj"]
    assert auto_apply._get_system_filter()["markets"] == ["hs", "cyb", "kcb", "bj"]
