# -*- coding: utf-8 -*-
"""
选股模式层 (重构 P0)
=================================================================================
老链路把 4 种时段行为挤在 api_stocks 单函数里, 靠 9 处 before930 / hm<9*60+30 之类的
时间硬编码分派, 每来一个新场景就加一个 if, 彼此干扰(近30天13commit中9次在选股的直接
原因)。本层把"现在是什么模式、该模式怎么取数、失败怎么办"收敛到**一个函数**。

模式划分(比老逻辑多识别出 LOCKED — 9:25 竞价结束到 9:30 开盘是语义独立的锁定窗口):
  PREOPEN  00:00-9:15   盘前定格  名单幂等, 用上交易日 9:25 定格(可改条件重选;
                                 API 透出定格来源日期, 前端顶栏标注"上一交易日")
  AUCTION  9:15-9:25    竞价窗口  竞价数据在变 → 名单会变。**v4.11.29 起默认被闸门拦住**
                                 (只认当日 9:25 定格, 竞价过程不出名单), 仅
                                 pick_window_guard=0 时才会真正走到该模式
  LOCKED   9:25-9:30    锁定期    9:25 定格已定型, 幂等且可锁定落库
  INTRADAY 9:30-15:00   盘中      名单**固定**(9:25 定格), 仅刷新已入选票的展示字段
  CLOSED   15:00后/非交易日        最近交易日定格, 幂等(前端同样标注来源日期)

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
from datetime import datetime, timedelta, timezone
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

# ---- 选股闸门 v4(2026-09-18 主人拍板: 只认当日 9:25 定格, 竞价过程一律不出名单) ----
# 演进(务必连着读, 否则会重犯):
#   v4.11.22 首次引入, 口径「交易日 9:00-9:26 整段禁选」—— 但没有区分「竞价过程」
#     与「盘前昨日定格」, 且当时闸门还会连前端自动加载一起掐死 → 9/17 早盘该时段
#     0 请求事故 → v4.11.26 整体回退并摘除闸门本体。
#   v4.11.27 重做, 只挡两段: ① [09:00,09:15) 盘前; ② [09:25:00,09:25:50] 落库前。
#     **09:15-9:25 竞价进行中放行** —— 当时理由是"竞价数据在变但那正是用户要看的实时竞价"。
#   v4.11.29 主人拍板推翻该理由(2026-09-18):
#     「选股本来就是竞价结束后才选, 竞价过程数据都在变化, 选的股也没意义」
#     ⇒ **竞价进行中不再提供名单**。实证动机: 9/18 早盘 09:15/09:22 两批(lock/filter)
#       因当日 9_25 未落库, 竞涨幅/竞价额**整批回退昨日 9_25**(黑猫 3.35=昨日值,
#       今日实为 1.00), 而竞涨幅占评分权重 34% ⇒ 名单与评分双双失真, 且页面下午仍在
#       回显该批次, 用户看到"刷出来是昨天的数据"。
#     ⇒ v4 只挡**一段**: [09:15:00, 9:25 定格落库] —— 竞价开始到当日定格可用为止。
#       盘前(00:00-9:15)与"落库前 9:25:00-9:25:50" 两段合并进同一段(9:15 起就已禁)。
#
# 为什么盘前不再拦(v4.11.27 的 ① 被移除):
#   盘前用上交易日定格是**设计内功能**(复盘/预演), 主人选择"保留但强制标注"(2026-09-18)
#   ⇒ 盘前放行, 由 API 透出定格来源日期 + 前端顶部常驻标注条明示"基于上一交易日定格",
#     消除"误当成当日名单"的风险。此时不拦反而更符合"变更可见"原则。
#
# 非交易日**不拦**(周末/节假日回放最近交易日定格是既有功能, 用户明确知情, 同样标注来源)。
# 秒级粒度: 9:14:59 放行(盘前) / 9:15:00 拦; 9:25:50 拦 / 9:25:51 起由**快照维**裁决。
# 2026-09-19 主人拍板: 拦截段末端由 09:25:35 顺延至 09:25:50 —— 换猫爪源后
#   竞价定格数据落库更晚(猫爪 9:25 定格需等交易所撮合完成后才可拉取), 原 09:25:35
#   放行会取到未完成的 9:25 快照 → 名单错位。顺延 15 秒留足落库缓冲。
# 逃生开关: settings.pick_window_guard=0 时时间维整体失效(前后端同口径, 见前端
#   _loadPickGateEnabled) —— 此时 AUCTION 模式会按实时全市场名单出结果(应急用)。
T_PICK_BLOCK_FROM = 9 * 3600 + 15 * 60         # 09:15:00 (含) — 竞价开始即禁(名单不可信)
T_PICK_BLOCK_TO = 9 * 3600 + 25 * 60 + 50      # 09:25:50 (含) — 换猫爪源后定格落库更晚(原 09:25:35)
# 时间维放行起点(= 拦截段结束的下一秒); ≥ 此点还须过**快照维**(见 api/stocks 双闸门)
T_PICK_OPEN = T_PICK_BLOCK_TO + 1              # 09:25:51


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
    list_source_count: int = 1
    """前 N 个 source_priority 为**名单源**(依次尝试, 第一个成功的为准);
    其余为**补丁源**(只补展示字段, 不改名单)。>1 = 该时段需多源容灾。

    竞价窗口设 2(东财 → 腾讯全市场): 此前只有 source_priority[0] 是名单源,
    tencent_market 排第二却只被当补丁源用 → 名单源实际单点; 生产机东财被墙时
    全靠 ensure_cache 内部隐式切腾讯, 降级在日志里不可见(降级=False)。"""

    @property
    def list_sources(self) -> Tuple[str, ...]:
        return self.source_priority[:max(1, self.list_source_count)]

    @property
    def patch_sources(self) -> Tuple[str, ...]:
        return self.source_priority[max(1, self.list_source_count):]


POLICIES = {
    # 2026-09-08 主人拍板(原始单文件版本 shunshi_fixed.html 的设计即如此:
    # "9:30前可重新选股 · 9:30后仅更新实时涨幅"):
    # **竞价结束(9:25)后名单就定型** — 盘中/收盘/非交易日一律幂等, 同筛选条件必得同
    # 名单(改条件再改回来也必须完全一致)。盘中只补已入选票的展示字段(realChange/
    # entityChange), 不重排·不重筛·不增票。仅 AUCTION 竞价进行中名单会变(数据在变)。
    PickMode.PREOPEN: ModePolicy(
        mode=PickMode.PREOPEN,
        label="盘前定格",
        source_priority=("snapshot", "eastmoney_realtime", "tencent_point"),
        deterministic=True,
        allow_lock=False,
        auction_window=False,
        allow_relock=True,           # 盘前用上个交易日定格, 允许改条件重选
        realtime_patch=True,         # 盘前调用实时源(东财/腾讯)返回的是**最近交易日收盘定格**,
                                     # 全天恒定不变 → 补它不破坏名单幂等, 也不影响确定性;
                                     # 不补则现价/现涨/实体/异动列全空(P5 上线后主人反馈 9/9 0:37)
        fail_message="盘前未开盘, 且上个交易日竞价数据不可用 — 不提供名单",
    ),
    PickMode.AUCTION: ModePolicy(
        mode=PickMode.AUCTION,
        label="竞价窗口",
        # 2026-09-08 P3: 竞价窗口**没有当日定格快照**(9:25 才定格), 名单只能来自
        # 实时全市场(点查源需要候选 codes, 此时无候选可用 → 取不到源)。
        # 这是唯一允许"名单随行情变化"的模式(deterministic=False)。
        # 2026-09-09 修正: 竞价窗口**两个都是名单源**(东财挂了直接切腾讯全市场),
        # 此前 list_source_count 默认 1 → tencent_market 只被当补丁源用, 名单源实际
        # 单点; 生产机东财被墙(今日东财全市场失败 2357 次)时全靠 ensure_cache 内部
        # 隐式切腾讯, 降级在日志里不可见(降级=False), 排查只能靠猜。
        source_priority=("eastmoney_market", "tencent_market"),
        list_source_count=2,
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
        # 2026-09-11 事故修复(生产「选股现涨幅又为 0」): 原为 ("snapshot",) +
        # realtime_patch=False → **一个补丁源都没有** → 定格快照行无实时价 →
        # real_change 恒 None → 落库被 history._safe_num 兜成 0(列 NOT NULL) →
        # 9:26 系统批次 / auto_apply 自动锁仓(恰在本窗口跑)全员名单现涨 0.00%;
        # 且 9:25-9:30 前端走 lock 当日幂等直读, 原样回吐该 0。
        # 9:25 竞价已成交, 实时点查有现价 → 与 PREOPEN/INTRADAY/CLOSED 对齐补齐补丁源。
        # **不破坏幂等**: list_source_count 仍为 1 → 名单只由 snapshot 定;
        # 补丁只补展示字段(price/现涨/换手), 且价格门槛按定格竞价价判定, 不参与名单。
        source_priority=("snapshot", "eastmoney_realtime", "tencent_point"),
        deterministic=True,
        allow_lock=True,
        auction_window=False,        # 竞价字段仍一律取 9:25 定格(补丁不得注入 f615/f616)
        allow_relock=True,           # 9:25-9:30 仍可锁定/重选(定格已定型)
        realtime_patch=True,
        fail_message="9:25 竞价定格数据不可用 — 无法锁定",
    ),
    PickMode.INTRADAY: ModePolicy(
        mode=PickMode.INTRADAY,
        label="盘中",
        # 名单只认 9:25 定格(幂等); 实时源**仅**用于补展示字段, 不得参与评分/排序/过滤
        # 名单=定格快照(幂等); 后面两级只做**展示字段补丁**(现价/涨幅/换手),
        # 不改变名单 — 补丁失败不影响名单(老链路点查失败会整批降级, 名单跟着变)
        source_priority=("snapshot", "eastmoney_realtime", "tencent_point"),
        deterministic=True,
        allow_lock=False,
        auction_window=False,
        allow_relock=False,          # 2026-09-20 主人拍板: 9:30-10:00 重新选股已放开
                                     # (api/stocks 快照池条件 + 前端 isBefore1000),
                                     # 本字段无消费方, 保留声明位; 10:00 后仍禁止重选
                                     # (拒绝点: fetcher.ensure_cache「9:30后禁止重新选股」)
        realtime_patch=True,         # 只更新已入选票的实时涨幅/实体涨幅
        fail_message="9:25 竞价定格数据不可用 — 不提供名单(盘中不重算名单)",
    ),
    PickMode.CLOSED: ModePolicy(
        mode=PickMode.CLOSED,
        label="闭市回放",
        # 名单=定格快照; 后两级补**收盘价/收盘涨幅**(2026-09-08 P3 修正: 原设
        # realtime_patch=False 导致收盘后现价/现涨全为 None, 前端列全空 —
        # 收盘后实时源返回的就是收盘定格值, **不再变化**, 补它不破坏幂等)
        source_priority=("snapshot", "eastmoney_realtime", "tencent_point"),
        deterministic=True,
        allow_lock=False,
        auction_window=False,
        allow_relock=False,
        realtime_patch=True,
        fail_message="最近交易日竞价数据不可用 — 不提供名单",
    ),
}


_BJ_TZ = timezone(timedelta(hours=8))


def _ts(now) -> float:
    """归一化时间为 POSIX 时间戳(秒)。

    2026-09-08 修复(测试机部署探针实锤): resolve_mode/is_trading_day 等原只接受
    时间戳 float, 调用方误传 datetime.datetime 会在 `now + 8*3600` 处抛
    TypeError(unsupported operand type(s) for +: 'datetime.datetime' and 'int'),
    且报错指向内部实现、难以定位。业务代码从 datetime.now() 直接传是高频写法,
    故在此统一兼容 datetime / 时间戳 / None 三种入参。
    """
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        # naive(无时区) 按北京时间理解 — 业务代码 datetime.now() 跑在 UTC+8 服务器,
        # 语义是"本地时间", 按 UTC 解释会整体偏移 8 小时(实测 08:00 被解成 16:00 闭市)
        if now.tzinfo is None:
            return now.replace(tzinfo=_BJ_TZ).timestamp()
        return now.timestamp()          # aware datetime: 由 tzinfo 正确折算到 epoch
    return float(now)


def bj_hm(now=None) -> Tuple[int, int]:
    """当前北京时间 (hour*60+min, 星期几 0=周一)。服务器时区无关(UTC+8)。
    now 可为 datetime / 时间戳 / None(当前时间)。"""
    t = time.gmtime(_ts(now) + 8 * 3600)
    return t.tm_hour * 60 + t.tm_min, t.tm_wday


def bj_secs(now=None) -> Tuple[int, int]:
    """当前北京时间**当日秒偏移** (hour*3600+min*60+sec, 星期几 0=周一)。

    与 bj_hm 同源, 但**到秒** —— 闸门第二段的边界在 09:25:50 / 09:25:51 之间,
    分钟粒度无法表达(9:25:50 与 9:25:59 会判成同一分钟)。
    now 可为 datetime / 时间戳 / None。
    """
    t = time.gmtime(_ts(now) + 8 * 3600)
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec, t.tm_wday


def is_trading_day(now=None, holidays: Optional[set] = None) -> bool:
    """是否交易日。当前口径: 非周末即交易日(与老逻辑一致)。
    holidays: 预留法定节假日集合({'2026-10-01', ...}), 传入后生效 — 后续接入
    真实交易日历不需改调用方。now 可为 datetime / 时间戳 / None。"""
    t = time.gmtime(_ts(now) + 8 * 3600)
    if t.tm_wday >= 5:
        return False
    if holidays:
        day = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
        if day in holidays:
            return False
    return True


def bj_date(now=None) -> str:
    """当前北京时间日期 YYYY-MM-DD (now 可为 datetime / 时间戳 / None)"""
    t = time.gmtime(_ts(now) + 8 * 3600)
    return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def resolve_mode(now=None,
                 holidays: Optional[set] = None) -> ModePolicy:
    """解析当前应选股模式(唯一入口 — 业务代码禁止再写时间判断)。

    now: datetime 或时间戳(测试注入), 默认当前时间。两种入参等价。
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


# 闸门文案(前后端同口径 —— 前端 frontend/src/utils/time.js 的常量必须与此逐字一致,
# 否则用户在不同触发路径下会看到两套说法; 有后端单测 test_pick_block_msg_shared_with_frontend
# 与前端单测 time.test.js 双向对拍)
PICK_BLOCK_MSG_TIME = "竞价进行中 · 9:25 定格后开放"
PICK_BLOCK_MSG_SNAP = "9:25 竞价定格尚未落库 · 稍后自动恢复"


def is_pick_open(now=None, holidays: Optional[set] = None) -> bool:
    """当前是否处于**允许选股**时段(闸门的时间维)。

    规则(2026-09-18 v4): 交易日只挡一段 ——
      - [09:15:00, 09:25:50]  竞价进行中 / 当日 9_25 定格尚未落库。
        **竞价过程数据每 10 秒在变, 用它排出来的名单不成立**(主人 2026-09-18 拍板);
        且此段取数会回退到**上一交易日** 9_25 定格(竞涨幅/竞价额整批错位, 而竞涨幅
        占评分权重 34%), 见 9/18 早盘 09:15/09:22 两批实证。

    - **00:00-09:14:59 盘前放行** —— PREOPEN 用上交易日定格是设计内功能(复盘/预演),
      由 API 透出定格来源日期 + 前端顶栏标注明示, 不再靠"禁选"来防误认;
    - ≥09:25:51 时间维放行, 但调用方还须叠加**快照维**(当日 9_25 已落库)才真正放行
      —— 见 api/stocks.py 的 _pick_blocked_reason 与 auction_snapshot.has_today_snapshot;
    - 非交易日(周末/节假日) **不拦** —— 回放最近交易日定格是既有功能;
    - settings.pick_window_guard=0 → 时间维整体失效(前端另有同口径开关联动)。

    秒级粒度: 9:14:59 放行, 9:15:00 拦; 9:25:50 拦, 9:25:51 放行。
    now 可为 datetime / 时间戳 / None。
    """
    if not is_trading_day(now, holidays):
        return True
    s, _ = bj_secs(now)
    if T_PICK_BLOCK_FROM <= s <= T_PICK_BLOCK_TO:
        return False
    return True


def pick_resume_at(now=None) -> str:
    """当前拦截段的**放行时刻**字符串(供 API 回给前端做提示), 未拦截返回 ""。

    单一拦截段 → T_PICK_OPEN 时刻字符串(现 09:25:51)。与 is_pick_open 同源同口径。
    ★ 从常量派生(不再硬编码): 闸门末端顺延时此文案自动跟随, 避免两处漂移。
    """
    if not is_trading_day(now):
        return ""
    s, _ = bj_secs(now)
    if T_PICK_BLOCK_FROM <= s <= T_PICK_BLOCK_TO:
        return "%02d:%02d:%02d" % (T_PICK_OPEN // 3600,
                                   (T_PICK_OPEN % 3600) // 60,
                                   T_PICK_OPEN % 60)
    return ""
