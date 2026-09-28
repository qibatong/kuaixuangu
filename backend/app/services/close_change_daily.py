# -*- coding: utf-8 -*-
"""全市场收盘涨跌幅落库: 把 close_change_history 每个交易日写满
================================================================
根因(2026-09-28 生产实测):
  close_change_history 原本是**惰性填充** —— 只有"被请求到"的股票才会在
  kpl.fill_close_change_from_kline 的库兜底 / 「收盘自愈」分支里写库。实测覆盖:
      2026-09-28  460 行 | 2026-09-24  449 行 | 2026-09-19   45 行   (全市场 5000+)
  ⇒ 未被请求到的股票, 每次回看/实时请求都要现场多源拉日K, 而**猫爪 `a=daily`
    持续 429 限流(每次退避 1s)**。实测代价:
      · 「龙虎榜」实时路径: 每请求 +1.0s(冷态 fill_close_change_from_kline 980ms)
      · 历史回看日 9/24:    冷态 +4.0s
  这两个症状(实时慢 / 回看日残余慢)是**同一个根因**。

做法: 交易日收盘后用**东财批量行情**一次拉全市场涨跌幅写库, 每日自检补齐。
  口径与 kpl.fill_close_change_from_kline 的「收盘自愈」分支**完全同源**:
  同为 fetch_spot_quote_map, 收盘后其 realChange(东财 f3) 即当日收盘涨幅;
  并复用**同一把门禁** kpl._close_chg_persist_allowed(交易日 + 已过 15:00)
  与**同一个写入口** kpl._close_chg_db_put ⇒ 零新语义、零新数据源。
  幂等(INSERT OR REPLACE) ⇒ 重跑无副作用。

自愈设计(2026-08-27 起本项目对定时任务的既定要求):
  不做"一次性窗口", 而是**每 5 分钟自检当日库内行数**, 不足则补 —— 因此进程重启、
  错过时点、上游抖动都能自愈; 每日尝试次数封顶(_MAX_TRIES), 避免上游长期不可用时空转。

边界(如实记录, 不夸大收益):
  * 覆盖 market_fs(["hs","cyb","kcb"])(沪深+创业板+科创板), 与既有「收盘自愈」分支
    同口径 —— **不含北交所**; 北交所个股仍走原有多源日K兜底, 行为不变。
  * **只解决"当天起"的覆盖**: 历史日无法用行情快照回填(行情接口只给"当下"),
    历史日仍走原有惰性补齐 —— 单日回看几次后自然焐热。
  * 行情明显不完整(< MIN_CODES)时**不写库并重试**, 避免把上游抖动的残缺结果固化成当日数据
    (写库是 INSERT OR REPLACE 只增不删, 故残缺也不会污染已有行, 但会留下"以为已完成"的假象)。
"""
import threading
import time

from ..core import logger
from ..core import trade_calendar as tc
from . import fetcher, kpl, scorer

log = logger.get_logger(__name__)

RUN_AT = 15 * 60 + 10     # 北京时间 15:10(收盘 15:00 后; 门禁要求已过 15:00)
_CHECK_EVERY = 300        # 自检间隔(秒)
MIN_CODES = 3000          # 一次拿到的股票数下限(全市场 5000+); 低于此视为行情不完整, 不写库
DONE_ROWS = 4000          # 库内当日行数达此值即视为当日已完成(不再自检)
_MAX_TRIES = 12           # 每日尝试上限(上游长期不可用时放弃当日, 不空转)
_BACKOFF0 = 180           # 首次失败后的重试间隔(秒)
_BACKOFF_MAX = 900        # 退避上限(15 分钟)

_fired = ""               # 已确认完成的日期(北京时间 YYYY-MM-DD)
_tries_date = ""          # _tries 所属日期
_tries = 0
_running = False
_next_try = 0.0
_backoff = 0


def _bj():
    """北京时间 struct_time + 当日分钟数(项目惯例: 显式 +8h, 见 kpl._bj_today 说明)"""
    g = time.gmtime(time.time() + 8 * 3600)
    return g, g.tm_hour * 60 + g.tm_min


def _bj_date(g=None):
    g = g or time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _day_rows(date):
    """库内 date 已有多少行(命中主键 (date,code), 开销可忽略); 异常返回 -1"""
    from ..db import database
    conn = None
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT COUNT(*) FROM close_change_history WHERE date=?",
                           (date,)).fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        log.warning("收盘涨跌幅落库: 统计库内行数失败 date=%s err=%s", date, e)
        return -1
    finally:
        if conn:
            conn.close()


def save_day(date=None):
    """把 date(默认今天)的**全市场**收盘涨跌幅写满 close_change_history。幂等; 返回落库条数。

    返回 0 表示本次未写库, 原因见日志: 门禁未放行(非交易日/未过15:00) / 行情不完整 / 数据源异常。
    """
    date = date or _bj_date()
    # ★ 只允许当日: 行情快照(东财批量)只反映"当下", 拿它写历史日会把今天的涨幅写进历史 ——
    #   而 _close_chg_persist_allowed 对 date<今天 是**放行**的(它的设计场景是逐只日K回填)。
    #   这两者的差异正是本模块唯一的污染风险点, 故在此显式挡住。
    if date != _bj_date():
        log.warning("收盘涨跌幅落库拒绝: 只支持当日(行情快照只反映当前会话) date=%s today=%s",
                    date, _bj_date())
        return 0
    if not kpl._close_chg_persist_allowed(date):
        log.info("收盘涨跌幅落库跳过(门禁未放行: 非交易日 或 今日未过15:00) date=%s", date)
        return 0
    try:
        spot = fetcher.fetch_spot_quote_map(scorer.market_fs(["hs", "cyb", "kcb"]))
    except Exception as e:
        log.warning("收盘涨跌幅落库: 全市场行情拉取异常 date=%s err=%s", date, e)
        return 0
    if not spot:
        log.warning("收盘涨跌幅落库: 全市场行情为空 date=%s", date)
        return 0
    pairs = {}
    for code, q in (spot or {}).items():
        if not code or not isinstance(q, dict):
            continue
        rc = q.get("realChange")
        if rc is None:
            rc = q.get("change")
        if rc is None:
            continue
        try:
            pairs[str(code)] = float(rc)
        except (TypeError, ValueError):
            continue
    if len(pairs) < MIN_CODES:
        log.warning("收盘涨跌幅落库: 行情仅 %d 只(<%d, 疑似上游抖动) date=%s, 本次不写库(稍后重试)",
                    len(pairs), MIN_CODES, date)
        return 0
    kpl._close_chg_db_put(date, pairs)
    log.info("收盘涨跌幅落库完成 date=%s 写入 %d 只 (库内当日 %d 行)",
             date, len(pairs), _day_rows(date))
    return len(pairs)


def _daily_task(date):
    global _fired, _running, _backoff, _next_try
    try:
        if save_day(date) > 0:
            _fired = date
            _backoff = 0
            return
        _backoff = min(_BACKOFF_MAX, _backoff * 2 if _backoff else _BACKOFF0)
    except Exception as e:
        _backoff = min(_BACKOFF_MAX, _backoff * 2 if _backoff else _BACKOFF0)
        log.warning("收盘涨跌幅落库异常 date=%s err=%s", date, e)
    finally:
        _next_try = time.time() + _backoff
        _running = False


def _needed(g, date):
    """当日是否仍需落库: 交易日 + 已过 RUN_AT + 库内行数不足"""
    if not tc.is_trade_day_of(g):
        return False
    if _bj()[1] < RUN_AT:
        return False
    if not kpl._close_chg_persist_allowed(date):
        return False
    return _day_rows(date) < DONE_ROWS


def _scheduler_loop():
    global _running, _tries, _tries_date
    log.info("收盘涨跌幅落库已启动: 交易日 %02d:%02d 起自检(每%d秒), 全市场写 close_change_history",
             RUN_AT // 60, RUN_AT % 60, _CHECK_EVERY)
    while True:
        try:
            g, _hm = _bj()
            date = _bj_date(g)
            if date != _tries_date:                # 跨日重置尝试计数
                _tries_date, _tries = date, 0
            if (_fired != date and not _running and _tries < _MAX_TRIES
                    and time.time() >= _next_try and _needed(g, date)):
                _tries += 1
                _running = True
                threading.Thread(target=_daily_task, args=(date,), daemon=True,
                                 name="close-change-daily").start()
        except Exception as e:
            log.warning("收盘涨跌幅调度异常 err=%s", e)
        time.sleep(_CHECK_EVERY)


def start_scheduler():
    """由 app.worker 拉起(与 stock_temper/concept_refresh 同模式)"""
    threading.Thread(target=_scheduler_loop, daemon=True, name="close-change-sched").start()
