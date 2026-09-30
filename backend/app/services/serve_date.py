# -*- coding: utf-8 -*-
"""服务日期唯一判据 —— 主人 2026-09-30「数据日期规矩」
================================================================================
规矩原文(主人):
  · 股市竞价开盘时间为交易日的 **9:15**;
  · **9:00 以前**显示**上一个交易日**的数据;
  · **9:00 起**开始(尝试)用**当天**的数据;
  · 🔴 **如果拿不到当天的数据, 也不能使用上一个交易日的数据** —— 这条要盯死了。

⇒ 本模块把"逻辑交易日从 **09:00** 开始"这件事收成**一个谓词**, 全仓所有"要不要回退到
  上一交易日"的判断都必须问它, **禁止再各写一份 `tc.is_trade_day(...)` 之类的门禁**
  —— 2026-09-30 审计发现 6 处"库里没这天数据就退昨天"的后门, 根因就是各写各的。

分区(以**交易日**为前提; 非交易日另说):
  ┌───────────────┬──────────────────────────────────────────────┐
  │ 00:00 ~ 09:00 │ 属于**上一个交易日**的尾巴 ⇒ 显示上一交易日   │
  │ 09:00 ~ 24:00 │ 属于**当天**               ⇒ 当天没数据就空   │
  └───────────────┴──────────────────────────────────────────────┘
  注意 09:00~09:15 这 15 分钟**已算"当天"但当天必然还没数据** ⇒ 页面就该是空的
  (主人明确选择此口径, 代价是这 15 分钟无数据可看; 换来的是"绝不用昨日冒充今日")。

与既有常量的关系(不要新造):
  · 09:15 竞价开始 —— `picker.mode.T_PICK_BLOCK_FROM` / `api.kpl._is_auction_hours()`
  · 09:26:30 定格  —— `auction_snapshot._BID25_FREEZE_SEC`
  本模块的 09:00 是**独立**的一刀(逻辑交易日起点), 不是上面任何一个。
"""
import datetime
import time

from ..core import logger
from ..core import trade_calendar as tc

log = logger.get_logger(__name__)


def _norm_day(day):
    """→ 'YYYY-MM-DD'; 无法识别 → ''(由调用方按"保守"处理)。

    兼容 str('2026-09-29' / '20260929' / '2026/09/29') / datetime.date / struct_time。
    🔴 为什么要自己归一: `tc.is_trade_day()` 对**无法识别的日期 fail-open 返回 True**
      ("保守放行"), 那是为调度门禁设计的; 但本模块的语义相反 —— 认不出的日期
      **绝不能放行回退**(否则一个脏参数就能让"当天没数据"退成昨天的数)。两者
      语义刚好相反, 故必须先归一、再交给日历。
    """
    if not day:
        return ""
    if isinstance(day, datetime.datetime):
        return day.date().isoformat()
    if isinstance(day, datetime.date):
        return day.isoformat()
    if hasattr(day, "tm_year"):                                # struct_time
        try:
            return time.strftime("%Y-%m-%d", day)
        except Exception:                                      # noqa: BLE001
            return ""
    s = str(day).strip().replace("/", "-").replace(".", "-")
    if len(s) == 8 and s.isdigit():                            # YYYYMMDD
        s = "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return ""
    return s

# 逻辑交易日起点: 交易日 09:00(北京时间的当日秒)
T_PREOPEN_SEC = 9 * 3600

# serve_date 的返回模式(供前端区分"为什么是这一天")
MODE_EXPLICIT = "explicit"   # 用户显式选了日期(历史回看) —— 永不回退
MODE_PREV = "prev"           # 交易日盘前(00:00~09:00) ⇒ 上一交易日
MODE_TODAY = "today"         # 交易日 09:00 起 ⇒ 当天(没数据就是空)
MODE_OFFDAY = "offday"       # 非交易日 ⇒ 由调用方对齐到最近交易日


def _bj(now_ts=None):
    """→ (北京时间 struct_time, 当日秒)"""
    g = time.gmtime((time.time() if now_ts is None else now_ts) + 8 * 3600)
    return g, g.tm_hour * 3600 + g.tm_min * 60 + g.tm_sec


def today(now_ts=None) -> str:
    """北京时间今天(YYYY-MM-DD)。

    ★ 不用 `time.strftime`: 单测普遍 monkeypatch 全局 `time.gmtime` 成**轻量替身**
      (只带 tm_hour/tm_min/tm_wday), 而 `strftime` 要求真 struct_time ⇒ 会 TypeError。
      直接整数拼串既不依赖 strftime, 也在替身缺 tm_year 时**抛得明确**、便于上层兜住。
    """
    g, _ = _bj(now_ts)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def allow_back(day: str, now_ts=None) -> bool:
    """**核心谓词**: 正在取 `day` 这一天时, 若它没数据, 是否允许回退到上一交易日?

    判据(三档, 顺序即优先级):
      ① `day` **不是交易日**(周末/法定休市) ⇒ **允许** —— 那时"最近交易日"才是用户想看的;
      ② `day` 是交易日但**不是"今天"**(= 用户在回看历史某日) ⇒ **禁止** ——
         历史回看必须原样, 静默平移一天是"凑数据"(2026-09-29 龙虎榜事故同型);
      ③ `day` 就是**今天**且是交易日 ⇒ 只有**还没到 09:00** 才允许
         (那时逻辑上仍属上一个交易日的尾巴); 09:00 起一律禁止。

    ⇒ 一句话: **交易日 09:00 起, 当天没数据就返回空/零, 绝不用昨天顶上。**
    day 无法识别时**保守禁止**(宁可显示空, 也不拿昨天冒充今天)。
    """
    d = _norm_day(day)
    if not d:
        log.warning("allow_back 日期无法识别(%r) ⇒ 保守禁止回退", day)
        return False
    try:
        if not tc.is_trade_day(d):
            return True
    except Exception as e:                                     # noqa: BLE001
        log.warning("allow_back 交易日判定失败 day=%s err=%s ⇒ 保守禁止回退", d, str(e)[:120])
        return False
    try:
        if d != today(now_ts):
            return False                                       # ② 历史交易日: 不平移
        _g, sec = _bj(now_ts)
        return sec < T_PREOPEN_SEC
    except Exception as e:                                     # noqa: BLE001
        # 时钟不可用(如单测把 gmtime 换成缺字段的轻量替身) ⇒ 保守禁止回退:
        # 宁可显示空, 也不要因取不到时间而误把"昨天"当今天。
        log.warning("allow_back 时刻判定失败 day=%s err=%s ⇒ 保守禁止回退", d, str(e)[:120])
        return False


def allow_back_now(now_ts=None) -> bool:
    """**现在**允不允许"当天没数据就退上一交易日"?

    = `allow_back(北京今天)`。给"库里没这天数据 → 自动回退最近交易日"这类后门当闸门用:
    交易日 09:00 起一律不容许(否则 09:00~09:15 / 盘中早段会把昨天冒充今天)。
    """
    return allow_back(today(now_ts), now_ts)


def serve_date(requested: str = "", now_ts=None):
    """解析**服务日期** → (date, mode)。

    requested 为空 = 实时模式(由本函数按规矩决定看哪一天);
    requested 非空 = 用户显式回看历史, **原样返回、绝不回退**(他就是要看那一天)。

    实时模式下:
      · 非交易日          → 返回今天, mode=offday(由调用方对齐到最近有数据的交易日)
      · 交易日 & < 09:00  → 上一交易日, mode=prev
      · 交易日 & >= 09:00 → 今天, mode=today(没数据就是空, 不回退)
    """
    req = (requested or "").strip()
    if req:
        return req, MODE_EXPLICIT
    day = today(now_ts)
    try:
        if not tc.is_trade_day(day):
            return day, MODE_OFFDAY
    except Exception as e:                                     # noqa: BLE001
        log.warning("serve_date 交易日判定失败 day=%s err=%s ⇒ 按当天处理", day, str(e)[:120])
        return day, MODE_TODAY
    if allow_back(day, now_ts):
        return (tc.prev_trade_date(day) or day), MODE_PREV
    return day, MODE_TODAY
