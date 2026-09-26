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
from ..core import trade_calendar as tc
from ..db import database
from . import fetcher, kpl

log = logger.get_logger(__name__)

# 盘后存档时间窗(北京时间)
# 2026-09-13 P1-a 修正: 15:30 → 18:30。
#   旧值 15:10~15:50 早于上游(xuangubao flash 池)发布时刻 —— 生产实证:
#   8/27 当天 15:25 / 15:37 / 15:47 三次尝试全部「池为空」, 该任务自上线起
#   「涨停/炸板落库」成功日志计数 = 0, 表中 27010 行全靠一次性回补脚本填充。
#   改到 18:30(±20 分 = 18:10~18:50), 并在窗口内按退避重试(见 _daily_task)。
BACKFILL_AT = 18 * 60 + 30
WINDOW = 20
_BACKOFF0 = 300           # 首次失败后的重试间隔(秒)
_BACKOFF_MAX = 1800       # 退避上限(30 分钟), 避免空转打上游

# 次日盘前补救窗(北京时间 09:00-09:05): 补最近 N 个自然日缺失的涨停池/龙虎榜
# 2026-09-13 P1-b 新增: 让故障自愈 —— 晚间窗口若因重启/节假日错过, 次日开盘前兜住,
# 不再累积成长缺口(生产实证: limit 缺 12 天 / lhb 缺 20 天)。
# 选 09:00-09:05 是为了避开 9:15 起的竞价采集主流程。
RESCUE_START = 9 * 60 + 0
RESCUE_END = 9 * 60 + 5
RESCUE_DAYS = 7           # 回溯自然日数(跳过周末, 实际覆盖约 5 个交易日)

# 日K缓存新鲜度: 末根日期 < 期望最近交易日 → 视为陈旧并回源
# 2026-09-13 P1-c 新增: 旧逻辑「命中缓存即永久返回、无任何 TTL」导致日K底座
#   永久冻结在首次写入时刻 —— 生产实证 stock_kline / stock_temper_profile 的 ts
#   全部 = 8/27, 相差 11 个交易日。不修这个, 调时间窗也治不好画像。
_KLINE_CLOSE_HM = 15 * 60 + 5   # 收盘后当日日K才定型

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
def save_day(date=None):
    """把指定日(start默认今日)的涨停池+炸板池落库 limit_history。
    date: YYYY-MM-DD; 幂等(INSERT OR REPLACE)。
    返回落库条数; 涨停/炸板都为空认为是无数据日(可能休市)。
    注: 2026-09-13 删除死参数 force(从未被函数体引用)。"""
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
            n = save_day(day)
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


# ==================== 日K: 读缓存或现拉东财(带新鲜度) ====================
def _norm_day(s):
    """把 2026-09-11 / 2026/09/11 / 20260911 / '2026-09-11 00:00:00' 统一为 YYYY-MM-DD。
    多源兜底(东财/腾讯)日期格式不一致, 统一后方可比较。解析失败返回 ''。"""
    if s is None:
        return ""
    t = str(s).strip().replace("/", "-").replace(".", "-")
    t = t.split(" ")[0].split("T")[0]
    d = t.split("-")
    try:
        if len(d) == 3 and len(d[0]) == 4:
            return "%s-%02d-%02d" % (d[0], int(d[1]), int(d[2]))
        if len(t) == 8 and t.isdigit():
            return "%s-%s-%s" % (t[:4], t[4:6], t[6:8])
    except (ValueError, TypeError):
        return ""
    return ""


def _expected_last_trade_day():
    """期望的最近交易日(北京时间)。忽略节假日 —— 宁可多回源一次也不漏更新。
    未收盘(<15:05)时当日日K尚未定型, 期望值取上一个工作日。"""
    from datetime import date as _d, timedelta
    g, hm = _bj()
    d = _d(g.tm_year, g.tm_mon, g.tm_mday)
    if hm < _KLINE_CLOSE_HM:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _kline_last_day(day_data):
    """取缓存日K最后一根的日期(YYYY-MM-DD); 无 time 字段或解析失败返回 ''。"""
    try:
        kl = json.loads(day_data) if isinstance(day_data, str) else (day_data or {})
        ts = (kl or {}).get("time") or []
        if not ts:
            return ""
        return _norm_day(max(str(x) for x in ts))
    except Exception:
        return ""


def _kline(code, refresh=False):
    """取得个股 day 日K({time,open,close,high,low,...}), 优先读 stock_kline 缓存。

    2026-09-13 P1-c: 缓存新增新鲜度判断 —— 末根日期早于期望最近交易日则回源。
    旧逻辑「命中即永久返回、无任何 TTL」会让日K底座永久冻结在首次写入时刻:
    生产实证 stock_kline 与 stock_temper_profile 的 ts 全部停在 8/27, 相差 11 个
    交易日, 且 rebuild_profiles() 用默认 refresh_kline=False → 画像永不更新。
    """
    if not refresh:
        try:
            conn = database.get_conn()
            row = conn.execute("SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
            conn.close()
            if row and row[0]:
                last = _kline_last_day(row[0])
                want = _expected_last_trade_day()
                # 解析失败(last='')按新鲜处理: 避免异常形态下全量回源打爆上游
                if not last or last >= want:
                    return json.loads(row[0])
                log.info("日K缓存陈旧 code=%s 末根=%s < 期望=%s → 回源", code, last, want)
        except Exception:
            pass
    data = fetcher.fetch_stock_chart_robust(code, "day")   # 多源兜底: 东财→腾讯(见 fetcher.fetch_stock_chart_robust)
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
            # 大阴线/冲高回落 (long upper shadow): 盘中最高涨幅 - 收盘涨幅 差值 >8%
            # 即 (high - close)/prev_close >= 8%, 盘中冲高后大幅回落(长上影大阴线)
            if (_h - _c) / prev_close * 100 >= 8.0:
                big_red += 1
                red_dates.add(d)
            # 日内大回撤(更敏感口径): 盘中高点到收盘回撤 >=5%
            if (_h - _c) / prev_close * 100 >= 5.0:
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

    # ---- 综合股性分(0-100): 封板率30 + 次日溢价30 + 高开率15 + 稳健25 ----
    # 稳健分 = 25 - 大阴线次数*2, 每1次冲高回落(≥8%)的大阴线扣2分, 0为下限。
    # (此前大阴线仅10%权重且≥5次封顶, 对分数几乎无影响, 现已放大并线性生效)
    score = (min(seal_rate, 100) * 0.3
             + min(max(avg_open_prem, 0) * 6, 30)
             + min(gap_up_rate, 100) * 0.15
             + max(0.0, 25 - float(big_red) * 2.0))
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
                      rebuy_rate, rep_factor=repair_rate, big_red=big_red),
    }


def _tags(score, seal_rate, avg_prem, zt_count, max_zt, rebuy_rate, rep_factor, big_red=0):
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
    # 负面标签: 大阴线(冲高回落≥8%)频繁, 负反馈
    if big_red >= 10:
        t.append("冲高回落频繁")
    elif big_red >= 5:
        t.append("波动偏大")
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
    t0 = time.time()
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
    log.info("股性画像全量重建完成 目标=%d 入库=%d 耗时=%.1fs",
             len(codes), done, time.time() - t0)
    return done


# ==================== 调度: 盘后存档 + 次日盘前补救 ====================
_fired = None        # 已成功落库的日期(**成功后才置位**, 见 _daily_task)
_backoff = 0         # 当前退避秒数(失败翻倍, 上限 _BACKOFF_MAX)
_next_try = 0        # 下次允许尝试的时间戳
_running = False     # 任务执行中防重入(rebuild 耗时可能超过 30s 轮询间隔)
_rescued = None      # 当日已执行过盘前补救


def _day_rows(table, day):
    """某表某日已有行数; 查询失败返回 -1(调用方应跳过, 避免把异常误判成"缺失")"""
    if table not in ("limit_history", "lhb_history"):      # 白名单防拼串
        return -1
    try:
        conn = database.get_conn()
        n = conn.execute("SELECT COUNT(*) FROM %s WHERE date=?" % table, (day,)).fetchone()[0]
        conn.close()
        return n
    except Exception as e:
        log.warning("盘前补救计数失败 table=%s day=%s err=%s", table, day, e)
        return -1


def _rescue_missing(days=RESCUE_DAYS):
    """盘前补救(P1-b): 回溯最近 N 个自然日, 补齐缺失交易日的涨停池与龙虎榜。

    意义: 上游发布时刻晚于采集时刻时, 当日窗口必然失败; 旧逻辑既不在窗口内
    重试、也无次日补救 → 缺口持续累积(生产实证 limit 缺 12 个交易日 /
    lhb 缺 20 个交易日)。幂等(INSERT OR REPLACE), 只回溯最近 N 天, 成本可控。
    """
    from datetime import date as _d, timedelta
    today = _d.fromisoformat(_bj_date())
    fixed = []
    for back in range(1, days + 1):
        day = (today - timedelta(days=back)).isoformat()
        if _d.fromisoformat(day).weekday() >= 5:
            continue
        try:
            if _day_rows("limit_history", day) == 0 and save_day(day):
                fixed.append("limit:" + day)
            if _day_rows("lhb_history", day) == 0:
                lst = kpl.fetch_lhb(day) or []
                if lst:
                    conn = database.get_conn()
                    conn.execute(
                        "INSERT OR REPLACE INTO lhb_history (date, list, ts) VALUES (?,?,?)",
                        (day, json.dumps(lst, ensure_ascii=False), int(time.time())))
                    conn.commit()
                    conn.close()
                    fixed.append("lhb:" + day)
        except Exception as e:
            log.warning("盘前补救异常 day=%s err=%s", day, e)
    log.info("股性盘前补救完成 回溯=%d天 补齐=%s", days, (",".join(fixed) or "无(数据齐全)"))
    return fixed


def _daily_task(date):
    """每日盘后: 先落库当日涨停/炸板, 成功后再全量重建画像(保证排行表当日最新)。

    2026-09-13 P1-a 修正: _fired 改为「成功后置位」+ 窗口内失败退避重试。
    旧逻辑「起线程即置位」→ 窗口内只试一次, 上游未发布则当天彻底放弃且次日
    无补救(生产实证: 「涨停/炸板落库」成功日志计数 = 0, 自上线起从未成功)。
    """
    global _fired, _backoff, _next_try, _running
    try:
        n = save_day(date)
        if n > 0:
            _fired = date
            _backoff = 0
            rebuild_profiles()
            return
        _backoff = min(_BACKOFF_MAX, _backoff * 2 if _backoff else _BACKOFF0)
    except Exception as e:
        _backoff = min(_BACKOFF_MAX, _backoff * 2 if _backoff else _BACKOFF0)
        log.warning("股性盘后任务异常 err=%s", e)
    finally:
        _next_try = time.time() + _backoff
        _running = False


def _scheduler_loop():
    global _fired, _rescued, _running
    log.info("股性调度已启动: 盘后存档 %02d:%02d(±%d分) / 盘前补救 %02d:%02d-%02d:%02d",
             BACKFILL_AT // 60, BACKFILL_AT % 60, WINDOW,
             RESCUE_START // 60, RESCUE_START % 60, RESCUE_END // 60, RESCUE_END % 60)
    while True:
        try:
            g, hm = _bj()
            date = _bj_date(g)
            # 次日盘前补救(P1-b): 每天一次, 选 09:00-09:05 避开 9:15 竞价主流程
            # 2026-09-25 复盘: `g.tm_wday < 5` → 交易日历(法定假日不空跑)
            if tc.is_trade_day_of(g) and RESCUE_START <= hm <= RESCUE_END and _rescued != date:
                _rescued = date
                threading.Thread(target=_rescue_missing, daemon=True,
                                 name="stock-temper-rescue").start()
            # 盘后存档(P1-a): 成功后才置 _fired, 失败按退避在窗口内重试
            if (tc.is_trade_day_of(g) and abs(hm - BACKFILL_AT) <= WINDOW
                    and _fired != date and not _running and time.time() >= _next_try):
                _running = True
                threading.Thread(target=_daily_task, args=(date,), daemon=True,
                                 name="stock-temper-daily").start()
            if _fired is not None and date != _fired and hm < BACKFILL_AT - WINDOW - 5:
                _fired = None
        except Exception as e:
            log.warning("股性存档调度异常 err=%s", e)
        time.sleep(30)


def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="stock-temper-sched")
    t.start()
    # 2026-09-13: 原文案写死「交易日 15:30」, 与 BACKFILL_AT 改动后严重矛盾
    # (排查时按 15:30 找窗口会得出错误结论) → 改为动态引用常量。
    log.info("股性调度线程已启动(盘后存档 交易日 %02d:%02d±%d分 / 盘前补救 %02d:%02d)",
             BACKFILL_AT // 60, BACKFILL_AT % 60, WINDOW,
             RESCUE_START // 60, RESCUE_START % 60)