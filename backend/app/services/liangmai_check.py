# -*- coding: utf-8 -*-
"""
量脉数据源独立校验任务 (feature/liangmai, 2026-08-31)
==================================================================
用量脉(独立第三方数据源)给快选数据"把关", 防止开盘啦/东财单点故障或数据错乱不被发现。

三层校验:
  1. 涨停池双源交叉(强): 快选本地 snapshot_bid 9_25 涨停集合 vs 量脉 stockpool_limit_up
     - 集合差异(量脉有我们漏 / 我们有量脉没) > 阈值 → 告警
  2. 量脉抢筹榜数据健康自检(弱): 非空/数量范围/涨幅区间/金额为正 → 异常告警
     (抢筹口径不同: 快选=秒级涨幅跳变, 量脉=委托金额排序, 不做榜单直接对比)
  3. 量脉全市场行情健康自检(弱): 非空/数量>4000/字段有效 → 异常告警
     (该接口当前可能 502, 此校验正好监测其服务端稳定性)

触发: 交易日 9:40(早盘竞价落定) + 15:10(尾盘后) 各一次; 告警走 notify.send_text(飞书/Server酱/企微)
"""
import threading
import time

from ..core import logger
from ..db import database
from . import liangmai, notify

log = logger.get_logger(__name__)

# 触发时间(分钟): 9:40 早盘校验 / 15:10 尾盘校验
CHECK_TIMES = (9 * 60 + 40, 15 * 60 + 10)
WINDOW = 30          # 触发窗口 ±30 分钟(防错过)
DIFF_THRESHOLD = 10  # 涨停池集合差异超过该只数 → 告警
RATIO_THRESHOLD = 0.15  # 涨停池差异比例超过 15% → 告警

_fired = {}          # date -> set(已触发的 CHECK_TIMES 值)


def _bj():
    t = time.gmtime(time.time() + 8 * 3600)
    date = time.strftime("%Y-%m-%d", t)
    return t, date, t.tm_hour * 60 + t.tm_min


def _local_limit_up_codes(date):
    """快选本地涨停集合: snapshot_bid 9_25 时点涨停(涨停阈值按板块) → set(code)"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change FROM snapshot_bid WHERE date=? AND time_point='9_25'",
            (date,)).fetchall()
        conn.close()
        codes = set()
        for r in rows:
            code = r["code"]
            bc = r["bid_change"] or 0
            if code[:2] in ("30", "68"):
                ok = bc >= 19.9
            elif code[:1] in ("8", "4"):
                ok = bc >= 29.9
            else:
                ok = bc >= 9.9
            if ok:
                codes.add(code)
        return codes
    except Exception as e:
        log.warning("本地涨停池读取失败 date=%s err=%s", date, e)
        return None


def check_limit_up(date):
    """涨停池双源交叉校验: 返回 (差异只数, 快选集合, 量脉集合); 差异超阈值时告警"""
    try:
        lm = liangmai.fetch_limit_up(date)
    except Exception as e:
        log.warning("量脉涨停池拉取失败(跳过校验) date=%s err=%s", date, str(e)[:100])
        return None
    lm_codes = {str(s.get("dm")) for s in lm if s.get("dm")}
    local = _local_limit_up_codes(date)
    if not lm_codes or local is None:
        return None
    miss = lm_codes - local          # 量脉有、我们漏了
    extra = local - lm_codes         # 我们有、量脉没有
    diff = len(miss) + len(extra)
    ratio = diff / max(len(local), 1)
    log.info("涨停池校验 date=%s 快选%d只 量脉%d只 差异%d只(%.1f%%) 漏%d 多%d",
             date, len(local), len(lm_codes), diff, ratio * 100, len(miss), len(extra))
    if diff > DIFF_THRESHOLD and ratio > RATIO_THRESHOLD:
        m = ("涨停池双源差异过大: 快选%d只 vs 量脉%d只, 差异%d只\n"
             "量脉有我们漏(%d): %s\n我们有量脉没(%d): %s\n请人工核对数据源!")
        extra_names = []
        if extra:
            try:
                conn = database.get_conn()
                rows = conn.execute(
                    "SELECT code, name FROM snapshot_bid WHERE date=? AND time_point='9_25' AND code IN (%s)"
                    % ",".join("?" * len(extra)), (date,) + tuple(sorted(extra))).fetchall()
                conn.close()
                extra_names = [r["name"] + r["code"] for r in rows[:10]]
            except Exception:
                pass
        notify.send_text(
            m % (len(local), len(lm_codes), diff, len(miss),
                 ",".join(sorted(miss))[:200] or "-",
                 len(extra), ",".join(extra_names)[:200] or "-"),
            title="⚠️ 涨停池数据源校验告警")
    return diff


def check_grab_health(date):
    """量脉抢筹榜数据健康自检(弱): 非空/数量/涨幅区间/金额 → 异常告警"""
    try:
        rows = liangmai.fetch_grab_amount(date, "0", "1")
    except Exception as e:
        log.warning("量脉抢筹拉取失败(跳过) date=%s err=%s", date, str(e)[:100])
        return None
    if not rows:
        notify.send_text("量脉早盘抢筹榜为空 date=%s (数据源异常?)" % date,
                         title="⚠️ 量脉抢筹数据自检告警")
        return 0
    bad = [s for s in rows if not (s.get("code") and (s.get("qcwtje") or 0) > 0)]
    bad_amt = [(s.get("name") or s.get("code")) for s in rows if (s.get("qczf") or 0) > 25]
    log.info("抢筹自检 date=%s 返回%d只 异常字段%d条", date, len(rows), len(bad))
    if len(rows) < 5:
        notify.send_text("量脉早盘抢筹榜数量过少(%d只) date=%s" % (len(rows), date),
                         title="⚠️ 量脉抢筹数据自检告警")
    elif bad_amt:
        notify.send_text("量脉抢筹涨幅异常>25%%: %s date=%s" % (",".join(bad_amt[:5]), date),
                         title="⚠️ 量脉抢筹数据自检告警")
    return len(rows)


def check_market_health():
    """量脉全市场行情健康自检: 非空/数量>4000 → 异常告警
    该接口当前可能 502, 此校验兼做服务端稳定性监测"""
    try:
        rows = liangmai.fetch_market_all()
    except Exception as e:
        log.warning("量脉全市场拉取失败(服务端故障?) err=%s", str(e)[:100])
        notify.send_text("量脉全市场行情接口异常: %s" % str(e)[:120],
                         title="⚠️ 量脉全市场行情自检告警")
        return None
    if not rows or len(rows) < 4000:
        notify.send_text("量脉全市场行情数量异常(%d只) (应>4000)" % len(rows or []),
                         title="⚠️ 量脉全市场行情自检告警")
        return len(rows or [])
    log.info("量脉全市场行情自检 OK: %d只", len(rows))
    return len(rows)


def _run_checks(date, hm):
    log.info("量脉数据源校验开始 date=%s hm=%d", date, hm)
    threading.Thread(target=check_limit_up, args=(date,), daemon=True,
                     name="lm-check-limit").start()
    threading.Thread(target=check_grab_health, args=(date,), daemon=True,
                     name="lm-check-grab").start()
    threading.Thread(target=check_market_health, daemon=True,
                     name="lm-check-market").start()


def _scheduler_loop():
    log.info("量脉数据源校验调度已启动(交易日 9:40 / 15:10)")
    while True:
        try:
            g, date, hm = _bj()
            if g.tm_wday < 5:
                fired = _fired.setdefault(date, set())
                for target in CHECK_TIMES:
                    if abs(hm - target) <= WINDOW and target not in fired:
                        _run_checks(date, hm)
                        fired.add(target)
                if fired and hm < min(CHECK_TIMES) - WINDOW - 5:
                    _fired.pop(date, None)
        except Exception as e:
            log.warning("量脉校验调度异常 err=%s", e)
        time.sleep(30)


def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="liangmai-check-sched")
    t.start()
    log.info("量脉数据源校验 调度线程已启动(交易日 9:40 / 15:10)")
