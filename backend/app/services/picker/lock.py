# -*- coding: utf-8 -*-
"""
锁仓接入层 (重构 P4)
=================================================================================
给「系统批次 system_batch」与「9:26 自动应用 auto_apply」两条锁仓链路提供**同一个**
统一入口, 让它们与首页选股共用 pipeline(模式层 → 名单源 → 粗筛 → 评分 → 精筛)。

为什么需要单独一层(而不是直接调 pipeline.run):
  1. **filters 形态不统一**: system_batch 传的是 validate_filters 输出(标量 dict,
     markets=["hs","cyb","kcb"]), auto_apply 传的是管理员后台原始 dict, 且 markets 是
     **["SH","SZ","BJ"] 大写形态**。picker/filter.in_markets 只认小写 hs/cyb/kcb —
     直接透传会让所有票被市场过滤掉 → **名单全空**(这是 P4 最容易踩的坑)。
  2. **锁仓对模式有要求**: 竞价进行中(AUCTION)名单还在变, 锁下来的不是定格值;
     盘前(PREOPEN)当日 9:25 快照还没生成。这两种模式必须拒绝并**明示原因**,
     不能静默产出一份假名单。
  3. **降级必须可诊断**: 锁仓在后台线程跑, 出错没人看得到 → 全部收进 LockResult
     并由调用方记日志(含 mode/源/剔除计数/耗时)。

铁律(与 P0 模式层一致): 名单只认 9:25 定格, 盘中不重选。锁仓即"把定格名单固化"。
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ...core import logger
from ..filter_defaults import FILTER_DEFAULTS as _FILTER_DEFAULTS
from . import mode as pm
from . import pipeline

log = logger.get_logger(__name__)


# ---------------------------------------------------------------- 参数归一化
# 大写/别名形态 → picker 内部口径。BJ(北交所)一律排除(老口径也不选北交所)。
_MARKET_ALIAS = {
    "SH": "hs", "SZ": "hs", "HS": "hs", "MAIN": "hs",
    "CYB": "cyb", "KCB": "kcb", "STAR": "kcb",
    "BJ": "", "BJS": "", "BSE": "",
}
_VALID_MARKETS = ("hs", "cyb", "kcb")

# 缺失键的兜底(仅用于"调用方没给"的极端情况, 见 to_picker_filters)。
# ★ v4.11.46: 本体已归口到 services/filter_defaults.FILTER_DEFAULTS(模块顶部 import),
#   此处不再保留副本。历史教训 —— 本仓"同一个筛选参数各抄一份"催生过真实事故:
#   system_batch 那份副本漏了 scoreFloor, 合并时静默丢弃线上的 60, 让系统批次
#   悄悄吃到本文件的硬编码兜底 50(首页 60) ⇒ 同一时刻 64 只 vs 27 只。


def norm_markets(markets: Any) -> List[str]:
    """市场范围归一化 → ["hs","cyb","kcb"] 子集。

    兼容三种入参: None/空 → 全市场; 字符串 "hs,cyb"; 列表 ["SH","SZ","BJ"]。
    大写别名与北交所都会被正确折算/剔除。
    """
    if not markets:
        return list(_VALID_MARKETS)
    if isinstance(markets, str):
        markets = [m for m in markets.replace(" ", "").split(",") if m]
    out: List[str] = []
    for m in markets:
        k = str(m or "").strip()
        if not k:
            continue
        k = _MARKET_ALIAS.get(k.upper(), k.lower())
        if k in _VALID_MARKETS and k not in out:
            out.append(k)
    return out or list(_VALID_MARKETS)


def _num(v, default: float) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _bool(v, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def to_picker_filters(f: Dict, markets: Any = None) -> Dict[str, Any]:
    """把"任意形态"的筛选参数归一成 pipeline/filter 期望的形态。

    兼容: validate_filters 输出(标量) / 管理员后台原始 dict(标量或 [str] 列表形态)。
    缺失键落兜底默认, 绝不抛异常 —— 锁仓是后台任务, 参数问题不该让它崩。
    """
    f = dict(f or {})
    out: Dict[str, Any] = {"markets": norm_markets(
        markets if markets is not None else f.get("markets"))}
    for k, dv in _FILTER_DEFAULTS.items():
        raw = f.get(k)
        if isinstance(raw, (list, tuple)):      # qs() 形态: [str]
            raw = raw[0] if raw else None
        out[k] = _bool(raw, dv) if isinstance(dv, bool) else _num(raw, dv)
    return out


# ---------------------------------------------------------------- 结果
@dataclass
class LockResult:
    """锁仓选股结果(与首页名单同构 + 诊断元信息)"""
    items: List[dict] = field(default_factory=list)
    mode: str = ""
    mode_label: str = ""
    sources: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    degraded: bool = False
    n_universe: int = 0
    n_candidate: int = 0
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.items)

    def summary(self) -> str:
        return ("mode=%s 全市场=%d 候选=%d 入选=%d 源=%s 降级=%s 剔除=%s %dms"
                % (self.mode, self.n_universe, self.n_candidate, len(self.items),
                   ",".join(self.sources) or "-", self.degraded, self.stats or {},
                   self.elapsed_ms))


# 拒绝锁仓的模式 → 原因(竞价未结束/当日快照未生成, 锁下来的不是定格名单)
_REJECT = {
    pm.PickMode.AUCTION: "竞价进行中(9:15-9:25), 名单尚未定格 — 拒绝锁仓",
    pm.PickMode.PREOPEN: "盘前(00:00-9:15), 当日 9:25 竞价快照尚未生成 — 拒绝锁仓",
}


def run_lock(f: Dict, *, markets: Any = None, top: Optional[int] = None,
             now=None, ctx=None, cfg: Optional[dict] = None,
             log_tag: str = "") -> LockResult:
    """跑一次锁仓选股。

    f:       筛选参数(任意形态, 见 to_picker_filters)
    markets: 覆盖 f 里的市场范围(调用方自己的口径优先时传)
    top:     截取前 N 名(system_batch 存 top30; auto_apply 不截)
    now:     时间注入(测试用)
    ctx:     外部事实注入(测试用); None 时自动加载

    永不抛异常: 任何失败都收进 LockResult.errors, 由调用方决定是否落库。
    """
    t0 = time.time()
    tag = "[%s] " % log_tag if log_tag else ""
    try:
        filters = to_picker_filters(f, markets)
        policy = pm.resolve_mode(now)
        res = LockResult(mode=policy.mode.value, mode_label=policy.label)

        reason = _REJECT.get(policy.mode)
        if reason:
            res.errors.append(reason)
            res.elapsed_ms = int((time.time() - t0) * 1000)
            log.warning("%s锁仓被拒绝: %s", tag, reason)
            return res

        if ctx is None:
            ctx = pipeline.load_context(markets=filters["markets"])
        if ctx.markets is None:
            ctx.markets = filters["markets"]

        pr = pipeline.run(filters, ctx=ctx, now=now, cfg=cfg)
        res.sources = list(pr.sources)
        res.errors = list(pr.errors)
        res.stats = dict(pr.stats)
        res.degraded = pr.degraded
        res.n_universe = pr.n_universe
        res.n_candidate = pr.n_candidate
        items = list(pr.items)
        if top and top > 0:
            items = items[:top]
        res.items = items
        res.elapsed_ms = int((time.time() - t0) * 1000)
        log.info("%s锁仓完成 %s", tag, res.summary())
        return res
    except Exception as e:                                       # noqa: BLE001
        # 锁仓在后台线程, 抛异常没人看得见 —— 必须转成结果 + 日志
        log.error("%s锁仓异常 err=%s", tag, e, exc_info=True)
        r = LockResult()
        r.errors.append("锁仓异常: %s" % e)
        r.elapsed_ms = int((time.time() - t0) * 1000)
        return r

