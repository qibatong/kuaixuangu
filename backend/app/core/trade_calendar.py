# -*- coding: utf-8 -*-
"""A 股交易日历（法定休市表）—— 项目内**单一事实来源**。

================================= 为什么需要它 =================================
2026-09-25（中秋节 · 星期五）复盘「休市日幽灵报告」时发现：仓库里有约二十处调度门禁
**只判周一~周五**（`g.tm_wday < 5`），没有节假日日历。后果：

  · 快选 `snapshot_bid` 当天被写入了**上一交易日的复制行**（`bid_change`/`bid_amt` 逐位相同，
    而猫爪补的 `price`/`bid_turnover` 全为 0）；
  · AI 竞价的 `aipick_scheduler._is_trade_day()` 因此照常跑链，模型拿到 `price=0`
    这种越界输入，涨停概率被顶到 0.94~0.96，页面**默认（最新）** 报告显示 30 只假名单。

本模块提供**日历侧**正门；`scripts/aipick/predict_daily.py` 里的
`price>0` 占比护栏是**数据侧**兜底。两者互补、缺一不可：
  · 日历侧：假日不采集/不预测/不写库 —— 省一次全市场拉取，且不给下游制造垃圾；
  · 数据侧：哪怕日历漏配、或上游源降级返回残缺快照，也**绝不**产出对外报告。

============================== 数据来源与口径 ==============================
上海证券交易所《关于上海证券交易所 2026 年部分节假日休市安排的通知》
（**上证公告〔2025〕45号**，2025-12-22）；
并经《关于 2026 年中秋节、国庆节休市安排的公告》（上证公告〔2026〕22号，2026-09-17）复核。

★ 只收录**落在周一~周五的法定休市日**（2026 年共 19 天）。
  周六/周日不逐一登记 —— 由 weekday 判定覆盖即可：**A 股从不在周末开市**，
  即便"调休"把周六变成工作日也不开市。公告中"另外 2 月 28 日(星期六)为周末休市"
  这类表述即为此意，故无须登记。

======================== 覆盖区间与失效策略（fail-open） ========================
上交所**每年 12 月**才发布下一年度的休市公告，因此 2027 年表此刻尚不存在
—— 这不是遗漏，是还没公告。硬猜 2027 的日期一旦猜错，会**把真实交易日拦掉**，
后果比"没日历"严重得多。故本模块采用 **fail-open**：

    COVERED_FROM <= date <= COVERED_TO  →  查表判定
    区间之外                            →  `is_holiday()` 返回 False
                                          （即"周一~周五就是交易日"，退化为改造前行为）

跨过 `COVERED_TO` 后会打**每日一次**的 ERROR 日志（`warn_if_uncovered()`），
提醒维护者补下一年的表；**绝不静默过期**。

补 2027 表的位置：见下方 `HOLIDAYS_2027` 占位注释。
"""

from __future__ import annotations

import time
from datetime import date as _date, timedelta as _timedelta
from typing import Optional, Union

__all__ = [
    "HOLIDAYS",
    "COVERED_FROM",
    "COVERED_TO",
    "is_covered",
    "is_holiday",
    "is_trade_day",
    "is_trade_day_now",
    "is_trade_day_of",
    "prev_trade_date",
    "latest_trade_in",
    "bj_date",
    "warn_if_uncovered",
]

# --------------------------------------------------------------------------- #
# 法定休市日表（只登记"落在周一~周五"的那些天）
# --------------------------------------------------------------------------- #
HOLIDAYS_2026 = frozenset([
    # 元旦：1月1日(四)~1月3日(六)     → 1月3日为周六，由 weekday 覆盖
    "2026-01-01",   # 周四
    "2026-01-02",   # 周五
    # 春节：2月15日(日)~2月23日(一)
    "2026-02-16",   # 周一
    "2026-02-17",   # 周二
    "2026-02-18",   # 周三
    "2026-02-19",   # 周四
    "2026-02-20",   # 周五
    "2026-02-23",   # 周一
    # 清明：4月4日(六)~4月6日(一)
    "2026-04-06",   # 周一
    # 劳动节：5月1日(五)~5月5日(二)
    "2026-05-01",   # 周五
    "2026-05-04",   # 周一
    "2026-05-05",   # 周二
    # 端午：6月19日(五)~6月21日(日)
    "2026-06-19",   # 周五
    # 中秋：9月25日(五)~9月27日(日)   ← 本轮事故当日
    "2026-09-25",   # 周五
    # 国庆：10月1日(四)~10月7日(三)
    "2026-10-01",   # 周四
    "2026-10-02",   # 周五
    "2026-10-05",   # 周一
    "2026-10-06",   # 周二
    "2026-10-07",   # 周三
])

# 👇 2027 年表：上交所公告（通常在 2026 年 12 月发布）后，把工作日休市日补在这里，
#    并把 COVERED_TO 推到 2027-12-31。届时 `warn_if_uncovered()` 的日志会提醒。
# HOLIDAYS_2027 = frozenset([
#     ...
# ])

HOLIDAYS = HOLIDAYS_2026

# 表的有效区间（闭区间）。区间外一律 fail-open（视为非假日）。
COVERED_FROM = "2026-01-01"
COVERED_TO = "2026-12-31"

# 过界告警去重（每日一次，避免 20s 轮询刷爆日志）
_warned_for: Optional[str] = None


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def bj_date(now_ts: Optional[float] = None) -> str:
    """当前北京时间日期 `YYYY-MM-DD`（服务器时区无关，固定 UTC+8）。

    与各调度模块里的 `_bj_date()` 同口径；此处独立实现以免反向依赖 services。
    """
    g = time.gmtime((time.time() if now_ts is None else float(now_ts)) + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _norm(d: Union[str, _date, time.struct_time, None]) -> Optional[str]:
    """把各种"日期表示"归一为 `YYYY-MM-DD`；无法识别返回 None。

    支持：`date` / `struct_time`(视为已 +8h 的北京时间) / `"YYYY-MM-DD"` / `"YYYYMMDD"`。
    """
    if d is None:
        return None
    if isinstance(d, _date):
        return d.isoformat()
    if isinstance(d, time.struct_time):
        return "%04d-%02d-%02d" % (d.tm_year, d.tm_mon, d.tm_mday)
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def is_covered(d: Union[str, _date, time.struct_time]) -> bool:
    """该日期是否落在节假日表的**有效覆盖区间**内（区间外一律 fail-open）。"""
    s = _norm(d)
    if not s:
        return False
    return COVERED_FROM <= s <= COVERED_TO


def is_holiday(d: Union[str, _date, time.struct_time]) -> bool:
    """是否**法定休市日**（仅看节假日表，**不含**周六周日）。

    ★ 区间外返回 False（fail-open）：宁可当交易日放行，也不因表过时而误拦真实交易日。
      无法识别的日期同样返回 False，并交由调用方自身的 weekday 判定兜底。
    """
    s = _norm(d)
    if not s:
        return False
    if not (COVERED_FROM <= s <= COVERED_TO):
        warn_if_uncovered(s)
        return False
    return s in HOLIDAYS


def is_trade_day(d: Union[str, _date, time.struct_time]) -> bool:
    """是否**交易日** = 周一~周五 且 非法定休市日。

    这是绝大多数调度门禁应当使用的判据（替代裸的 `g.tm_wday < 5`）。
    """
    s = _norm(d)
    if not s:
        # 无法识别日期：保守放行（等同改造前行为），由调用方自行兜底
        return True
    if _date.fromisoformat(s).weekday() >= 5:      # 0=周一 … 5=周六 6=周日
        return False
    return not is_holiday(s)


def is_trade_day_of(g: time.struct_time) -> bool:
    """便捷版：直接吃已经 `+8*3600` 处理过的 `struct_time`（各调度循环里的 `g`）。

    ★ 传进来的 `g` 必须是**北京时间**（即调用方已做 `time.gmtime(ts + 8 * 3600)`）。

    ★ 兼容性：先看 `tm_wday`（周末一律 False），再查节假日表。
      这样对**只带 `tm_wday` 的轻量替身对象**（如单测里 `SimpleNamespace(tm_wday=5)`）
      仍能得到正确的周末判定，不会因为缺 `tm_year` 而 fail-open 成"是交易日"。
    """
    wday = getattr(g, "tm_wday", None)
    if isinstance(wday, int) and wday >= 5:
        return False
    return is_trade_day(g)


def is_trade_day_now(now_ts: Optional[float] = None) -> bool:
    """当前北京时间是否交易日。"""
    return is_trade_day(bj_date(now_ts))


def prev_trade_date(d: Union[str, _date, time.struct_time, None] = None, *,
                    include_today: bool = False,
                    max_back: int = 30) -> Optional[str]:
    """`d`(默认今天)之前**最近一个交易日**, 返回 `YYYY-MM-DD`; 找不到返回 None。

    include_today=True 时, 若 `d` 本身是交易日则返回 `d`。

    用途(2026-09-26 新增): 给「昨日成交额(yday_amount)」这类**按 T 日语义取值**的
    缓存/落库判据提供"上一个交易日"。此前各模块靠 `now - 1 天` 粗算, 遇到周末/长假
    就指错日期 —— 而那正是 09-14~09-24「数据冻结但标签每天前进」事故的温床。

    fail-open: 超出 `max_back` 天(异常输入/日历表缺口)返回 None, 由调用方自行兜底 ——
    本模块不猜日期, 猜错会**误拦真实交易日**, 后果比弃权严重。
    """
    base = _norm(d) if d is not None else bj_date()
    if not base:
        return None
    try:
        cur = _date.fromisoformat(base)
    except ValueError:
        return None
    if not include_today:
        cur -= _timedelta(days=1)
    for _ in range(max(1, int(max_back))):
        if is_trade_day(cur):
            return cur.isoformat()
        cur -= _timedelta(days=1)
    return None


def latest_trade_in(dates, day: Union[str, _date, time.struct_time, None] = None) -> Optional[str]:
    """从候选日期序列里挑出 `<= day` 的**最近一个交易日**; 无合规候选返回 None。

    ★ 用途(2026-09-27 v4.11.66): 各处「取表内 MAX(date) 当最近交易日」的**读侧**解析
      此前没有任何交易日历过滤, 于是 2026-09-25(中秋 · 周五 · 法定休市)因当天傍晚前
      还没装日历门禁而照常采集落下的**幽灵快照**被当成了"最近交易日" ⇒ 整站竞价数据
      (竞价封单/委买/爆量/净额/抢筹 + 两市概况 + 选股定格) 被顶成休市日的静态值:
      09-25 四个时点的 bid_change/bid_amt **各自都等于 09-24 的 9_25 定格值**,
      两市概况三时点总成交额恒等(14,723,625,413)。这就是主人 09-27 反馈
      「竞价异动板块的数据是不是有问题」的根因。

    与 `prev_trade_date` 的分工: 后者是**纯日历推算**(不关心库里有没有数据), 本函数是
    **在已存在的候选里挑** —— 用于"表里 MAX(date) 落在休市日"这一类场景, 只跳过非交易日,
    绝不越过候选集去猜一个库里根本没有的日期。

    fail-open: 候选为空 / 全不合规 → 返回 None, 由调用方**保留原值**(宁可显示原有数据,
    也不主动留空 —— 留空会把"回退"变成"无数据", 后果更重)。
    """
    if not dates:
        return None
    limit = _norm(day) if day is not None else None
    for d in dates:
        s = _norm(d)
        if not s or (limit and s > limit):
            continue
        if is_trade_day(s):
            return s
    return None


def warn_if_uncovered(d: Union[str, _date, time.struct_time, None] = None) -> None:
    """日期跨过 `COVERED_TO` 时，**每日一次** 打 ERROR 提醒补表（绝不静默过期）。

    刻意不在模块导入期报错 —— 表过期只是"能力降级"，不应让服务起不来。
    """
    global _warned_for
    s = _norm(d) if d is not None else bj_date()
    if not s:
        return
    if s <= COVERED_TO:
        return
    if _warned_for == s:
        return
    _warned_for = s
    try:
        from . import logger
        logger.get_logger(__name__).error(
            "[交易日历] 节假日表已过期! 日期 %s 超出覆盖区间(至 %s) → 已退化为"
            "「周一~周五即交易日」, 法定假日将被误判为交易日。"
            "请补 HOLIDAYS_2027 并上推 COVERED_TO (见 core/trade_calendar.py)",
            s, COVERED_TO)
    except Exception:                                  # noqa: BLE001
        # 日志不可用也不能连带业务失败
        pass
