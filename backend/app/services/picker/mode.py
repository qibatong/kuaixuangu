# -*- coding: utf-8 -*-
"""
选股模式层 (重构 P0)
=================================================================================
老链路把 4 种时段行为挤在 api_stocks 单函数里, 靠 9 处 before930 / hm<9*60+30 之类的
时间硬编码分派, 每来一个新场景就加一个 if, 彼此干扰(近30天13commit中9次在选股的直接
原因)。本层把"现在是什么模式、该模式怎么取数、失败怎么办"收敛到**一个函数**。

模式划分(比老逻辑多识别出 LOCKED — 9:25 竞价结束到 9:30 开盘是语义独立的锁定窗口):
  PREOPEN  00:00-9:15   盘前定格  名单幂等, 用上交易日 9:25 定格(可改条件重选)
  AUCTION  9:15-9:25    竞价窗口  竞价数据在变 → **唯一允许名单变化的模式**
  LOCKED   9:25-9:30    锁定期    9:25 定格已定型, 幂等且可锁定落库
  INTRADAY 9:30-15:00   盘中      名单**固定**(9:25 定格), 仅刷新已入选票的展示字段
  CLOSED   15:00后/非交易日        最近交易日定格, 幂等

幂等总原则(2026-09-08 主人拍板, 与最初单文件版 shunshi_fixed.html 一致:
"9:30前可重新选股 · 9:30后仅更新实时涨幅"):
  **竞价结束(9:25)后, 每种筛选条件下的名单即定型** — 盘中/收盘/非交易日一律不变;
  改条件后出的是另一份名单, 但**条件改回来必须得到完全相同的名单**。
  盘中实时源只允许补 realChange/entityChange 等展示字段: 不重排·不重筛·不增票。
  (老代码 9:30 后仍按实时行情重算 → 同条件两次结果不同, 是"名单波动/大跌票混入"
   类事故反复出现的主因之一)

交易日判定: 目前仅周末(与老逻辑一致)。节假日日历为已知缺口 — 老代码同样没有,
这里预留 is_trading_day 的 holidays 参数, 后续接入交易日历不需改调用方。
"""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class PickMode(str, Enum):
    """选股运行模式"""
    PREOPEN = "preopen"      # 盘前定格(未开盘)
    AUCTION = "auction"      # 竞价窗口(9:15-9:25)
    LOCKED = "locked"        # 锁定期(9:25-9:30, 定格已定型)
    INTRADAY = "intraday"    # 盘中实时(9:30-15:00)
    CLOSED = "closed"        # 闭市/非交易日(回放最近交易日定格)


# 时段边界(北京时间 分钟数)
T_PREOPEN_END = 9 * 60 + 15     # 9:15 竞价开始
T_AUCTION_END = 9 * 60 + 25     # 9:25 竞价结束(定格点)
T_LOCKED_END = 9 * 60 + 30      # 9:30 开盘
T_INTRADAY_END = 15 * 60        # 15:00 收盘


@dataclass(frozen=True)
class ModePolicy:
    """某模式下的完整行为声明(新增模式只需在此加一条)"""
    mode: PickMode
    label: str                       # 中文名(日志/前端提示用)
    source_priority: Tuple[str, ...] # 数据源尝试顺序(sources/ 适配层标签)
    deterministic: bool              # 名单是否必须幂等(同条件必同结果)
    allow_lock: bool                 # 是否允许锁定落库
    auction_window: bool             # 是否可取实时竞价字段 f615/f616
    allow_relock: bool               # 是否允许**重新选股**(重算名单); 9:30 后一律 False
    realtime_patch: bool             # 是否允许刷新展示字段(realChange/entityChange)。
                                     # 只补**已入选票**的展示值: 不重排/不重筛/不增票
    fail_message: str                # 取数全失败时给用户的明示文案(铁律2: 降级必须可见)


POLICIES = {
    # 2026-09-08 主人拍板(原始单文件版本 shunshi_fixed.html 的设计即如此:
    # "9:30前可重新选股 · 9:30后仅更新实时涨幅"):
    # **竞价结束(9:25)后名单就定型** — 盘中/收盘/非交易日一律幂等, 同筛选条件必得同
    # 名单(改条件再改回来也必须完全一致)。盘中只补已入选票的展示字段(realChange/
    # entityChange), 不重排·不重筛·不增票。仅 AUCTION 竞价进行中名单会变(数据在变)。
    PickMode.PREOPEN: ModePolicy(
        mode=PickMode.PREOPEN,
        label="盘前定格",
        source_priority=("snapshot", "tencent_point"),
        deterministic=True,
        allow_lock=False,
        auction_window=False,
        allow_relock=True,           # 盘前用上个交易日定格, 允许改条件重选
        realtime_patch=False,        # 未开盘, 无实时字段可补
        fail_message="盘前未开盘, 且上个交易日竞价数据不可用 — 不提供名单",
    ),
    PickMode.AUCTION: ModePolicy(
        mode=PickMode.AUCTION,
        label="竞价窗口",
        source_priority=("eastmoney_realtime", "tencent_point"),
        deterministic=False,         # 竞价数据实时在变 — 唯一允许名单变化的模式
        allow_lock=False,
        auction_window=True,
        allow_relock=True,
        realtime_patch=True,
        fail_message="竞价行情源不可用 — 不提供名单(竞价数据不可伪造)",
    ),
    PickMode.LOCKED: ModePolicy(
        mode=PickMode.LOCKED,
        label="锁定期",
        source_priority=("snapshot",),
        deterministic=True,
        allow_lock=True,
        auction_window=False,
        allow_relock=True,           # 9:25-9:30 仍可锁定/重选(定格已定型)
        realtime_patch=False,
        fail_message="9:25 竞价定格数据不可用 — 无法锁定",
    ),
    PickMode.INTRADAY: ModePolicy(
        mode=PickMode.INTRADAY,
        label="盘中",
        # 名单只认 9:25 定格(幂等); 实时源**仅**用于补展示字段, 不得参与评分/排序/过滤
        source_priority=("snapshot", "eastmoney_realtime"),
        deterministic=True,
        allow_lock=False,
        auction_window=False,
        allow_relock=False,          # 9:30 后禁止重选(原始版本 reLockData 同规则)
        realtime_patch=True,         # 只更新已入选票的实时涨幅/实体涨幅
        fail_message="9:25 竞价定格数据不可用 — 不提供名单(盘中不重算名单)",
    ),
    PickMode.CLOSED: ModePolicy(
        mode=PickMode.CLOSED,
        label="闭市回放",
        source_priority=("snapshot",),
        deterministic=True,
        allow_lock=False,
        auction_window=False,
        allow_relock=False,
        realtime_patch=False,        # 已收盘, 实时字段=收盘值, 无需刷新
        fail_message="最近交易日竞价数据不可用 — 不提供名单",
    ),
}


def bj_hm(now: Optional[float] = None) -> Tuple[int, int]:
    """当前北京时间 (hour*60+min, 星期几 0=周一)。服务器时区无关(UTC+8)。"""
    t = time.gmtime((now if now is not None else time.time()) + 8 * 3600)
    return t.tm_hour * 60 + t.tm_min, t.tm_wday


def is_trading_day(now: Optional[float] = None, holidays: Optional[set] = None) -> bool:
    """是否交易日。当前口径: 非周末即交易日(与老逻辑一致)。
    holidays: 预留法定节假日集合({'2026-10-01', ...}), 传入后生效 — 后续接入
    真实交易日历不需改调用方。"""
    t = time.gmtime((now if now is not None else time.time()) + 8 * 3600)
    if t.tm_wday >= 5:
        return False
    if holidays:
        day = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
        if day in holidays:
            return False
    return True


def bj_date(now: Optional[float] = None) -> str:
    """当前北京时间日期 YYYY-MM-DD"""
    t = time.gmtime((now if now is not None else time.time()) + 8 * 3600)
    return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def resolve_mode(now: Optional[float] = None,
                 holidays: Optional[set] = None) -> ModePolicy:
    """解析当前应选股模式(唯一入口 — 业务代码禁止再写时间判断)。

    now: 可选时间戳(测试注入), 默认当前时间。
    holidays: 预留节假日集合。
    """
    if not is_trading_day(now, holidays):
        return POLICIES[PickMode.CLOSED]
    hm, _ = bj_hm(now)
    if hm < T_PREOPEN_END:
        return POLICIES[PickMode.PREOPEN]
    if hm < T_AUCTION_END:
        return POLICIES[PickMode.AUCTION]
    if hm < T_LOCKED_END:
        return POLICIES[PickMode.LOCKED]
    if hm < T_INTRADAY_END:
        return POLICIES[PickMode.INTRADAY]
    return POLICIES[PickMode.CLOSED]
