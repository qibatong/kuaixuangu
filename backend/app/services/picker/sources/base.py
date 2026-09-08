# -*- coding: utf-8 -*-
"""数据源适配层: 上下文 / 结果 / 基类"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..contract import QuoteRow
from ..mode import ModePolicy


@dataclass
class FetchContext:
    """一次取数的上下文: 模式策略 + 权威定格 map + 目标代码集。

    day_bid_change / day_bid_amt_wan / yesterday_chg 是**外部权威数据**:
    定格值优先于任何实时字段(见 contract.FIELD_AUTHORITY), adapter 只负责注入,
    不负责判断该不该用 —— 语义判断集中在契约层, 避免重演老链路的散落 fallback。
    """
    policy: ModePolicy
    date: str = ""                                     # YYYY-MM-DD(快照源按此取)
    codes: Optional[List[str]] = None                  # 候选代码集(点查源必填)
    markets: Optional[List[str]] = None                # 市场范围(全市场源用)
    day_bid_change: Dict[str, float] = field(default_factory=dict)   # 9:25 定格竞价涨幅 %
    day_bid_amt_wan: Dict[str, float] = field(default_factory=dict)  # 9:25 定格竞价额(万元)
    day_bid_vol: Dict[str, float] = field(default_factory=dict)      # 9:25 定格竞价量(股)
    yesterday_chg: Dict[str, float] = field(default_factory=dict)    # 真实昨日涨幅 %
    prev_error: Optional[str] = None                   # 前序源失败原因(降级可见性)

    @property
    def degraded(self) -> bool:
        """本次取数是否处于降级状态(前序源已失败)"""
        return self.prev_error is not None


@dataclass
class SourceResult:
    """单个数据源的取数结果。

    铁律2(降级必须可见): 失败/**部分失败**都必须显式体现, 调用方据此决定是否
    继续降级、是否给前端打 degraded 标记 —— 绝不允许"静默返回空/半残数据"。
    """
    label: str = ""
    rows: Dict[str, QuoteRow] = field(default_factory=dict)
    error: Optional[str] = None      # None=成功; 非空=失败原因(明示, 不吞异常)
    elapsed_ms: int = 0
    degraded: bool = False           # 本次结果是否来自降级路径
    requested: int = 0               # 请求的代码数(用于覆盖率统计)

    @property
    def ok(self) -> bool:
        """是否可作为有效数据源使用"""
        return self.error is None and bool(self.rows)

    @property
    def coverage(self) -> float:
        """覆盖率: 返回数 / 请求数(0.0~1.0); requested 未知时按 1.0 处理"""
        if not self.requested:
            return 1.0 if self.rows else 0.0
        return min(1.0, len(self.rows) / float(self.requested))

    def missing_codes(self, codes: Optional[List[str]] = None) -> List[str]:
        """请求了但没拿到的代码(部分失败检测, 供 pipeline 决定是否补数)"""
        if not codes:
            return []
        return [c for c in codes if c not in self.rows]


class BaseSource:
    """数据源基类: 子类实现 fetch(), 通过 run() 安全调用。

    run() 吞掉所有异常并转成 SourceResult.error —— 目的是让**降级成为显式数据**
    而不是控制流: pipeline 只看 SourceResult, 不需要 try/except 嵌套(老链路正是
    层层 try/except 导致语义丢失)。
    """
    label: str = ""

    def fetch(self, ctx: FetchContext) -> SourceResult:
        raise NotImplementedError

    def run(self, ctx: FetchContext) -> SourceResult:
        """安全执行 + 计时 + 标签回填"""
        t0 = time.time()
        try:
            r = self.fetch(ctx)
        except Exception as e:                      # noqa: BLE001 - 适配层显式吞异常
            return SourceResult(
                label=self.label, error="%s: %s" % (type(e).__name__, e),
                elapsed_ms=int((time.time() - t0) * 1000),
                degraded=True, requested=len(ctx.codes or []))
        if not r.label:
            r.label = self.label
        r.elapsed_ms = int((time.time() - t0) * 1000)
        return r


# ---- 标签 → adapter 注册表: 与 mode.ModePolicy.source_priority 严格对齐 ----
# 新增数据源必须在此登记, 否则 pipeline 按优先级取源时会取不到(有测试兜底)。
REGISTRY: Dict[str, str] = {}


def get_source(label: str) -> Optional[BaseSource]:
    """按标签取 adapter 实例; 未知标签返回 None(不抛, 由 pipeline 决定降级)"""
    from . import eastmoney, snapshot, tencent          # 延迟导入: 避免模块循环
    table = {
        "snapshot": snapshot.SnapshotSource,
        "eastmoney_realtime": eastmoney.EastmoneyRealtimeSource,
        "eastmoney_market": eastmoney.EastmoneyMarketSource,
        "tencent_point": tencent.TencentPointSource,
        "tencent_market": tencent.TencentMarketSource,
    }
    REGISTRY.update({k: v.__module__ + "." + v.__name__ for k, v in table.items()})
    cls = table.get(label)
    return cls() if cls else None
