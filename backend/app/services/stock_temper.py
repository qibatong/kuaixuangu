# -*- coding: utf-8 -*-
"""
股性服务: 每日涨停/炸板存档 + 历史回补 + 个股股性画像/评分
============================================================
「股性」用于衡量一只股票的涨停基因、次日溢价、炸板反包、波动特征。

数据来源(三源合一, 均已具备, 无新增外部依赖):
  1. xuangubao flash 涨停/炸板历史池(_flash_pool, 支持 YYYY-MM-DD) —— 每日明细落库
  2. 东财日K(fetch_stock_chart) —— 次日开盘/收盘、大阴线、回撤现算
  3. limit_history 存档表(本模块每日盘后写 + 一次性回补脚本回填)

关键口径(2026-08-27 修正):
  - limit_history.is_limit: 1=最终封住(涨停池 limit_up_pool), 0=最终炸板(炸板池 limit_up_broken)
  - flash 历史接口只给「当日最终态」, 盘中首封时间/封单等细粒度仅从上线起累积
  - 次日溢价/炸板反包/大阴线 用日K现算, 全历史可回补
  - 反包(炸板后重新封住)/修复(大阴线后重新封住)以 limit_history 中该股再次封住为准
"""
import json
import threading
import time

from ..core import logger
from ..db import database
from . import fetcher, kpl

log = logger.get_logger(__name__)

# 盘后存档时间窗(北京时间): 与连板天梯 15:30 对齐
BACKFILL_AT = 15 * 60 + 30
WINDOW = 20

# 反包/修复的时间窗(交易日数): 炸板后 N 日内重新封住=反包; 大阴线后 N 日内重新封住=修复
REBUY_N = 5
REPAIR_N = 10


def _bj():
    g = time.gmtime(time.time() + 8 * 3600)
    return g, g.tm_hour * 60 + g.tm_min


def _bj_date(g=None):
    g = g or time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


# ==================== 落库: 单日涨停/炸板明细 ====================
def save_day(date=None, force=False):
    """把指定日(start默认今日)的涨停池+炸板池落库 limit_history。
    date: YYYY-MM-DD; 幂等(INSERT OR REPLACE)。
    返回落库条数; 涨停/炸板都为空认为是无数据日(可能休市)。"""
    date = date or _bj_date()
    zt = kpl._flash_pool("limit_up_pool", date) or []
    broken = kpl._flash_pool("limit_up_broken", date) or []
    if not zt and not broken:
        log.warning("涨停/炸板池为空 date=%s (可能休市/数据源异常), 跳过", date)
        return 0
    conn = database.get_conn()
    rows = []
    ts = int(time.time())
    for it in zt:
        rows.append((date, it.get("code", ""), it.get("name", ""), 1,
                     int(it.get("limitUpDays", 1) or 1), int(it.get("breakTimes", 0)),
                     float(it.get("change", 0)), it.get("reason", ""), ts))
    for it in broken:
        rows.append((date, it.get("code", ""), it.get("name", ""), 0,
                     int(it.get("limitUpDays", 1) or 1), int(it.get("breakTimes", 0)),
                     float(it.get("change", 0)), it.get("reason", ""), ts))
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO limit_history(date,code,name,is_limit,zt,zbc,change,reason,ts) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        log.info("涨停/炸板落库 date=%s 涨停%d 炸板%d", date, len(zt), len(broken))
        return len(rows)
    finally:
        conn.close()


# ==================== 历史回补: 逐日调 flash 历史池(带失败退避) ====================
def backfill(start_date, end_date):
    """回补 [start_date, end_date] 区间每个自然日的涨停/炸板记录。
    自动跳过无数据的休市日(接口返回空)。返回成功落库的自然日数。
    对上游(xuangubao/东财)做了限流防护: 连续失败自动增大间隔退避, 避免被风控。"""
    from datetime import date as _d, timedelta
    d = _d.fromisoformat(start_date)
    end = _d.fromisoformat(end_date)
    done, fail = 0, 0
    delay = 0.3                      # 基础间隔(秒); 连续失败时指数退避上限 8s
    while d <= end:
        day = d.isoformat()
        try:
            n = save_day(day, force=True)
            if n > 0:
                done += 1
                fail = 0
            time.sleep(delay)
        except Exception as e:
            fail += 1
            delay = min(8.0, delay * 2)     # 退避: 0.3→0.6→…→8s
            log.warning("回补异常 day=%s err=%s (连续失败%d, 间隔%.1fs)",
                        day, e, fail, delay)
            time.sleep(delay)
        d += timedelta(days=1)
    log.info("历史回补完成 %s..%s 有数据日=%d", start_date, end_date, done)
    return done


# ==================== 日K: 读缓存或现拉东财 ====================
def _kline(code, refresh=False):
    """取得个股 day 日K({time,open,close,high,low,...}), 优先读 stock_kline 缓存"""
    if not refresh:
        try:
            conn = database.get_conn()
            row = conn.execute("SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception:
            pass
    data = fetcher.fetch_stock_chart_robust(code, "day")   # 多源兜底: 东财→腾讯→tushare→同花顺→kpl
    if not data or not data.get("time"):
        return None
    try:
        conn = database.get_conn()
        conn.execute("INSERT OR REPLACE INTO stock_kline(code,day_data,ts) VALUES(?,?,?)",
                     (code, json.dumps(data, ensure_ascii=False), int(time.time())))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("日K落库失败 code=%s err=%s", code, e)
    return data


def _ordered_klines(kl):
    """把东财 day 日K标准化为按日期升序的 [(date,open,close,high,low), ...]; 异常安全"""
    if not kl or not kl.get("time"):
        return []
    try:
        o, c, h, lo = kl["open"], kl["close"], kl["high"], kl["low"]
        out = []
        for i, d in enumerate(kl["time"]):
            if not d or lo[i] is None:
                continue
            out.append((str(d), float(o[i]), float(c[i]), float(h[i]), float(lo[i])))
        out.sort(key=lambda x: x[0])
        return out
    except Exception:
        return []


def _next_trade_kline(d, kl_map):
    """返回 >d 且最近的交易日的 K 元组 (o,c,h,l); 无则 None"""
    nxt = None
    for k in kl_map:
        if k > d and (nxt is None or k < nxt):
            nxt = k
    if nxt is None:
        return None
    return kl_map[nxt]


def compute_profile(code, day_window=None, refresh_kline=False):
    """计算单只股票的股性画像。返回 dict(缺省字段为 0/None, 不抛异常)。
    指标覆盖: 封板率/炸板率、连板高度、次日溢价(含按板数衰减)、炸板反包、
    大阴线/日内大回撤与修复能力、打板胜率与盈亏比、综合分与标签。
    day_window: 统计近 N 自然日(默认覆盖回补的一年+)。
    """
    day_window = day_window or 730
    now = _bj_date()
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date,name,is_limit,zt,zbc,change FROM limit_history WHERE code=? "
        "AND date<=? ORDER BY date", (code, now)).fetchall()
    conn.close()
    if not rows:
        return {"code": code, "name": "", "sample_days": 0, "ready": False}

    seals = [r for r in rows if r[2]]          # is_limit=1 封住
    broken = [r for r in rows if not r[2]]     # is_limit=0 炸板
    zt_count, broken_count = len(seals), len(broken)
    touch_count = zt_count + broken_count
    seal_rate = (zt_count / touch_count * 100) if touch_count else 0.0
    max_zt = max((r[3] for r in rows), default=0)
    seals_zt = [r[3] for r in seals]
    avg_zt = (sum(seals_zt) / len(seals_zt)) if seals_zt else 0.0

    # 日K(缓存优先, ?refresh=1 强制现拉) → 用于次日溢价/大阴线/回撤
    klines = _ordered_klines(_kline(code, refresh=refresh_kline))
    kl_map = {(d, o, c, h, lo)[0]: (o, c, h, lo) for (d, o, c, h, lo) in klines}

    # ---- 封住次日溢价 + 打板隔日表现 + 溢价衰减 ----
    open_prems, high_prems, hold_profits = [], [], []
    gap_cnt = 0
    decay = {}                                  # 板数档 -> {n,avg_open,avg_high}
    for r in seals:
        d, zt = r[0], r[3]
        base = kl_map.get(d)                    # 涨停日K, 取收盘=涨停价
        nx = _next_trade_kline(d, kl_map)
        if not base or not nx or base[1] <= 0:
            continue
        bc = base[1]
        no, nc, nh = nx[0], nx[1], nx[2]
        op = (no - bc) / bc * 100
        hp = (nh - bc) / bc * 100
        hp_hold = (nc - bc) / bc * 100
        open_prems.append(op)
        high_prems.append(hp)
        hold_profits.append(hp_hold)
        if no > bc:
            gap_cnt += 1
        tier = str(zt) if zt <= 4 else "4+"
        t = decay.setdefault(tier, {"n": 0, "avg_open": 0.0, "avg_high": 0.0})
        t["n"] += 1
        t["avg_open"] += op
        t["avg_high"] += hp

    n_prem = len(open_prems)
    avg_open_prem = (sum(open_prems) / n_prem) if n_prem else 0.0
    avg_high_prem = (sum(high_prems) / n_prem) if n_prem else 0.0
    gap_up_rate = (gap_cnt / n_prem * 100) if n_prem else 0.0
    avg_hold = (sum(hold_profits) / len(hold_profits)) if hold_profits else 0.0
    for t in decay.values():
        if t["n"]:
            t["avg_open"] = round(t["avg_open"] / t["n"], 2)
            t["avg_high"] = round(t["avg_high"] / t["n"], 2)

    # 打板胜率/盈亏比: 封住次日按收盘卖出
    wins = [p for p in hold_profits if p > 0]
    losses = [p for p in hold_profits if p <= 0]
    win_rate = (len(wins) / len(hold_profits) * 100) if hold_profits else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    pl_ratio = (avg_win / abs(avg_loss)) if avg_loss else (avg_win if avg_win > 0 else 0.0)

    # ---- 炸板反包: 次日高开 + 炸板后 N 交易日内重新封住 ----
    all_dates = sorted({r[0] for r in rows})
    seal_set = {r[0] for r in seals}
    brok_gap, brok_seal_ok = 0, 0
    for rr in broken:
        d = rr[0]
        base = kl_map.get(d)
        nx = _next_trade_kline(d, kl_map)
        if base and nx and nx[0] > base[1]:
            brok_gap += 1
        idx = all_dates.index(d)
        if any(ad in seal_set for ad in all_dates[idx + 1:idx + 1 + REBUY_N]):
            brok_seal_ok += 1
    rebuy_rate = (brok_seal_ok / broken_count * 100) if broken_count else 0.0

    # ---- 大阴线 / 日内大回撤 / 修复能力(全日K扫描) ----
    big_red, deep_dip = 0, 0
    red_dates = set()
    prev_close = None
    kdates = []
    for (d, _o, _c, _h, _lo) in klines:
        if _c is not None and prev_close and prev_close > 0:
            pct = (_c - prev_close) / prev_close * 100
            if pct <= -5.0:            # 实体大阴线: 单日收跌 >=5%
                big_red += 1
                red_dates.add(d)
            if (_h - _c) / prev_close * 100 >= 5.0:    # 日内大回撤: 高点回撤到收盘 >=5%
                deep_dip += 1
        if _c is not None:
            prev_close = _c
        kdates.append(d)
    # 修复: 大阴线后 REPAIR_N 个交易日内再次封住
    red_repaired = 0
    if red_dates:
        for rd in red_dates:
            try:
                i = kdates.index(rd)
            except ValueError:
                continue
            if any(sd in seal_set for sd in kdates[i + 1:i + 1 + REPAIR_N]):
                red_repaired += 1
    red_total = len(red_dates)
    repair_rate = (red_repaired / red_total * 100) if red_total else 0.0

    # ---- 综合股性分(0-100): 封板率40 + 次日溢价30 + 高开率20 + 稳健分10 ----
    score = (min(seal_rate, 100) * 0.4
             + min(max(avg_open_prem, 0) * 6, 30)
             + min(gap_up_rate, 100) * 0.2
             + max(0, 100 - min(big_red * 8, 40)) * 0.1)
    score = round(max(0.0, min(100.0, score)), 1)

    return {
        "code": code, "name": rows[-1][1] or code,
        "sample_days": len(rows), "ready": True,
        "zt_count": zt_count, "broken_count": broken_count, "touch_count": touch_count,
        "seal_rate": round(seal_rate, 1), "broken_rate": round(100 - seal_rate, 1),
        "max_zt": int(max_zt), "avg_zt": round(avg_zt, 2),
        "avg_next_open_prem": round(avg_open_prem, 2),
        "avg_next_high_prem": round(avg_high_prem, 2),
        "gap_up_rate": round(gap_up_rate, 1),
        "premium_decay": decay,
        "rebuy_rate": round(rebuy_rate, 1),
        "broken_gap_count": brok_gap,
        "big_red_count": big_red, "deep_dip_count": deep_dip,
        "repair_rate": round(repair_rate, 1),
        "win_rate": round(win_rate, 1),
        "avg_hold_profit": round(avg_hold, 2),
        "pl_ratio": round(pl_ratio, 2),
        "score": score,
        "tags": _tags(score, seal_rate, avg_open_prem, zt_count, max_zt,
                      rebuy_rate, rep_factor=repair_rate),
    }


def _tags(score, seal_rate, avg_prem, zt_count, max_zt, rebuy_rate, rep_factor):
    t = []
    if zt_count >= 20:
        t.append("涨停基因强")
    elif zt_count >= 8:
        t.append("涨停基因中")
    if seal_rate >= 80:
        t.append("封板率高")
    elif seal_rate < 40 and zt_count >= 5:
        t.append("炸板率高")
    if max_zt >= 5:
        t.append("妖性足(连板高)")
    elif max_zt >= 3:
        t.append("连板能力")
    if avg_prem >= 2:
        t.append("溢价充足")
    elif avg_prem <= -1 and zt_count >= 5:
        t.append("隔日兑现")
    if rebuy_rate >= 50:
        t.append("炸板反包强")
    if rep_factor >= 60:
        t.append("回调修复快")
    if not t:
        t.append("样本积累中")
    return t


# ==================== 排行榜(方案B: 直读画像落库表) ====================
def rank(page=1, size=50, min_zt=0, keyword="", day_window=None):
    """股性排行: 从 stock_temper_profile 直读(len回来即时), 不再逐股实时计算。
    支持 min_zt(最少涨停数) 与 keyword(代码/名称模糊搜索) 过滤; 按 score 降序分页。
    scope = 画像表全量股票数(统计范围); total = 当前筛选后的结果数。"""
    conn = database.get_conn()
    where, args = [], []
    if min_zt:
        where.append("zt_count >= ?")
        args.append(int(min_zt))
    kw = (keyword or "").strip()
    if kw:
        where.append("(code LIKE ? OR name LIKE ?)")
        like = f"%{kw}%"
        args.extend([like, like])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    scope = conn.execute("SELECT COUNT(*) FROM stock_temper_profile").fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) FROM stock_temper_profile{wsql}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT profile FROM stock_temper_profile{wsql} "
        "ORDER BY score DESC, code LIMIT ? OFFSET ?",
        args + [int(size), (int(page) - 1) * int(size)]).fetchall()
    conn.close()
    try:
        out = [json.loads(r[0]) for r in rows]
    except Exception:
        out = []
    return {"total": total, "scope": scope, "page": page, "size": size,
            "list": out}


# ==================== 画像落库 / 重算(方案B) ====================
def _store_profile(p, ts=None):
    """把单只股票画像写入 stock_temper_profile(INSERT OR REPLACE)。"""
    ts = ts or int(time.time())
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_temper_profile(code,name,score,zt_count,profile,ts) "
            "VALUES(?,?,?,?,?,?)",
            (p.get("code", ""), p.get("name") or p.get("code", ""),
             float(p.get("score") or 0), int(p.get("zt_count") or 0),
             json.dumps(p, ensure_ascii=False), ts))
        conn.commit()
    finally:
        conn.close()


def rebuild_profiles(day_window=None):
    """全量重算并落库所有有涨停/炸板记录股票的画像(方案B: 每日盘后/回补后调用)。
    逐股 compute_profile(日K走 stock_kline 缓存), 写入 stock_temper_profile。
    day_window: 统计近 N 自然日(默认覆盖回补的一年+)。返回成功入库数。"""
    conn = database.get_conn()
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM limit_history").fetchall()]
    conn.close()
    ts = int(time.time())
    done = 0
    for code in codes:
        try:
            p = compute_profile(code, day_window=day_window)
        except Exception as e:
            log.warning("画像重建失败 code=%s err=%s", code, e)
            continue
        if not p.get("ready"):
            continue
        _store_profile(p, ts)
        done += 1
    log.info("股性画像全量重建完成 目标=%d 入库=%d", len(codes), done)
    return done


# ==================== 盘后调度 ====================
_fired = None


def _daily_task(date):
    """每日盘后: 先落库当日涨停/炸板, 再全量重建画像(方案B), 保证排行表当日最新。"""
    try:
        n = save_day(date, force=True)
        if n > 0:
            rebuild_profiles()
    except Exception as e:
        log.warning("股性盘后任务异常 err=%s", e)


def _scheduler_loop():
    global _fired
    log.info("股性涨停/炸板盘后存档 调度已启动(交易日 %02d:%02d)", BACKFILL_AT // 60, BACKFILL_AT % 60)
    while True:
        try:
            g, hm = _bj()
            date = _bj_date(g)
            if g.tm_wday < 5 and abs(hm - BACKFILL_AT) <= WINDOW and _fired != date:
                threading.Thread(target=_daily_task, args=(date,), daemon=True,
                                 name="stock-temper-daily").start()
                _fired = date
            if _fired is not None and date != _fired and hm < BACKFILL_AT - WINDOW - 5:
                _fired = None
        except Exception as e:
            log.warning("股性存档调度异常 err=%s", e)
        time.sleep(30)


def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="stock-temper-sched")
    t.start()
    log.info("股性涨停/炸板盘后存档 调度线程已启动(交易日 15:30)")