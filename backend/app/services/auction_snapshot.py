# -*- coding: utf-8 -*-
"""
竞价多时点快照归档服务: 9:15 / 9:20 / 9:25 全市场快照每日自动采集
================================================================
- 工作日 9:15 / 9:20 / 9:25 三个时点, 后台线程自动抓取全市场行情 → snapshot_bid 表
- 每日积累 → 形成"历史多时点回放库"(短线侠式核心壁垒)
- 9:25 lock 时读取 9:20 快照, 计算涨幅加速度
"""
import concurrent.futures
import json
import threading
import time

from ..core import logger
from ..db import database
from . import fetcher, scorer
from .cache_store import store

log = logger.get_logger(__name__)

# 时点 -> (开始分钟, 结束分钟) 北京时间(每 10 秒轮询, 窗口 1 分钟防漏)
# 9_24 用于"最后一秒抢筹"兜底: 调度器在 9:24:30 后每轮询重采覆盖, 最后一份≈9:24:3x~4x
# 真正秒级用 snapshot_lastsec(9:24:45-9:25:03 每秒高频采样 + 差值回退, 见 _lastsec_loop)
TIME_POINTS = {
    "9_15": (9 * 60 + 15, 9 * 60 + 16),
    "9_20": (9 * 60 + 20, 9 * 60 + 21),
    "9_24": (9 * 60 + 24, 9 * 60 + 25),
    "9_25": (9 * 60 + 25, 9 * 60 + 26),
}
DEFAULT_POINT = "9_20"     # 加速度计算使用的时点

# 最后一秒高频采样窗口: (9:24:45) ~ (9:25:03), 每秒一次(ts 记实际时刻)
LASTSEC_START = 9 * 3600 + 24 * 60 + 45
LASTSEC_END = 9 * 3600 + 25 * 60 + 3

_fetch_lock = threading.Lock()       # 东财拉取串行化(时点快照 vs 秒级采样 双线程防并发限流)
# 调度去重已外置 CacheStore(跨进程): setnx("sched:done:date:tp", ttl=1天) 等
# 旧 _sched_lock/_sched_done/_sched_checked/_lastsec_done 移除(2026-08-16 Phase1)


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _fetch_market_map(full=False):
    """抓取全市场快照, 返回 {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv}}
    过滤异常涨幅(±30% 外, A股涨跌停上限20%/新股44%, 非交易时段字段可能异常)
    full=True : fetch_eastmoney_all 分页全市场(~5500只, 按代码f12排序, 时点快照用)
    full=False: fetch_eastmoney 单页200只×3分区(按涨幅倒序=竞价最强前600, 秒级采样用,
                9:24:45-9:25:03 共18秒窗口, 分页全市场需60s+, 无法每秒完成)
    并发: 沪深创科 3 分区 ThreadPoolExecutor 并发拉取(全市场分页内部仍串行防限流),
          单页模式耗时 3×~1.5s → ~1.5s, 秒级采样 8 秒窗口可采 5-8 个点
    """
    with _fetch_lock:
        raw_all = {}

        def _grab(m):
            try:
                if full:
                    return m, fetcher.fetch_eastmoney_all(scorer.market_fs([m]))
                return m, fetcher.fetch_eastmoney(scorer.market_fs([m]))
            except Exception as e:
                log.warning("快照拉取失败 market=%s full=%s err=%s", m, full, e)
                return m, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            for m, raw in ex.map(_grab, ("hs", "cyb", "kcb")):
                if not raw:
                    continue
                for s in raw:
                    code = s.get("f12")
                    if not code:
                        continue
                    bc = scorer.get_bid_change(s)
                    if bc < -30 or bc > 30:    # 明显异常数据(非交易时段字段污染)
                        continue
                    raw_all[code] = {
                        "bid_change": bc,
                        "bid_amt": scorer.get_bid_amt(s),
                        "name": str(s.get("f14") or ""),          # 名称
                        # 竞价封单额(元) = f10 买一量(手) × f5 买一价 × 100(股/手)
                        # 涨停时买一委托即封单; 非涨停时=买一委托金额(竞价强弱参考)
                        "bid_buy_amt": scorer.parse_float(s.get("f10")) * scorer.parse_float(s.get("f5")) * 100,
                        "float_mv": scorer.parse_float(s.get("f21")),             # 自由流通市值(元) - f21 流通市值在短线语境≈自由流通
                        "board": str(s.get("f103") or s.get("f100") or ""),       # 概念(f103优先, 行业f100兜底)
                    }
        return raw_all


def snapshot_at(time_point, force=False):
    """抓取并归档某时点全市场快照, 返回入库数量; 失败返回 0
    时点快照用全市场分页(fetch_eastmoney_all ~5500只), 非单页600只
    封单额 bid_buy_amt 优先用开盘啦涨停委买额(真实封单, 非涨停股为0),
    其次东财 f10×f5 计算; 三者皆无则为 0
    force=True: 跳过非交易日检查(测试用, 允许任意日期落库)"""
    if time_point not in TIME_POINTS:
        return 0
    date = _bj_date()
    # 防御(2026-08-16): 非交易日绝不写入! 之前手动调用/调试曾把周日数据写入库,
    # 用户看到 8-16 周日金螳螂封单 39.78亿 等垃圾数据(来自东财非交易时段错误行情)
    # 调度循环 _scheduler_loop 已加 g.tm_wday<5 判断; 此处再加防御防外部调用
    if not force:
        g = time.gmtime(time.time() + 8 * 3600)
        if g.tm_wday >= 5:
            log.warning("[快照采集] 拒绝非交易日写入 tp=%s date=%s(周%d) 防御性跳过", time_point, date, g.tm_wday)
            return 0
    t0 = time.time()
    log.info("[快照采集] 开始 time=%s date=%s", time_point, date)
    raw_all = _fetch_market_map(full=True)
    if not raw_all:
        log.warning("[快照采集] 拉取为空 time=%s date=%s 耗时%.0fms (东财全市场接口无返回, 该时点数据缺失!)",
                    time_point, date, (time.time() - t0) * 1000)
        return 0
    # 叠加开盘啦涨停委买额(真实封单) + 概念: MorningBiddingList Type=4 返回该时点
    # 涨停股封单额与概念(开盘啦概念优先, 东财 f103/f100 兜底)
    n_seal = 0
    n_board = 0
    kpl_cnt = 0
    try:
        from . import kpl
        kpl.clear_cache()          # 保证拿到当前时点的新鲜数据(非前一时点缓存)
        kpl_seal = kpl.fetch_bid_seal() or []
        kpl_map = {s["code"]: s for s in kpl_seal}
        kpl_cnt = len(kpl_seal)
        for code, v in raw_all.items():
            # 该时点是否涨停(与 _is_zt 一致): 决定封单额是否有效
            bc = v.get("bid_change") or 0
            if code[:2] in ("30", "68"):
                is_zt = bc >= 19.9
            elif code[:1] in ("8", "4"):
                is_zt = bc >= 29.9
            else:
                is_zt = bc >= 9.9
            # 核心修复(2026-08-16): 非涨停时点封单强制 0!
            # _fetch_market_map 给所有股票都算了东财 f10×f5×100(买一委托金额),
            # 若开板股不在 KPL 榜会保留该非零值 → 前端仍显示"封单"(用户反馈的同类问题)
            if not is_zt:
                v["bid_buy_amt"] = 0
            s = kpl_map.get(code)
            if s:
                seal = s.get("bidSealAmt") or 0
                if seal and is_zt:
                    v["bid_buy_amt"] = seal
                    n_seal += 1
                b = s.get("board") or ""
                if b:
                    v["board"] = b     # 开盘啦概念覆盖东财
                    n_board += 1
        if n_seal or n_board:
            log.info("[快照采集] time=%s 开盘啦叠加 封单%d只 概念%d只 (开盘啦返回%d只)",
                     time_point, n_seal, n_board, kpl_cnt)
        else:
            log.info("[快照采集] time=%s 开盘啦叠加 0 只 (开盘啦返回%d只, 可能非交易时段或接口异常)",
                     time_point, kpl_cnt)
    except Exception as e:
        log.warning("[快照采集] time=%s 开盘啦封单叠加失败 err=%s", time_point, e, exc_info=True)
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, board, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(date, time_point, code, v["bid_change"], v["bid_amt"], v.get("name", ""),
              v.get("bid_buy_amt", 0), v.get("float_mv", 0), v.get("board", ""), int(time.time()))
             for code, v in raw_all.items()])
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("[快照采集] 落库失败 time=%s err=%s", time_point, e, exc_info=True)
        return 0
    log.info("[快照采集] 完成 date=%s time=%s 数量%d 耗时%.0fms (封单覆盖%d只)",
             date, time_point, len(raw_all), (time.time() - t0) * 1000, n_seal)
    # 数据质量自检(2026-08-16): 采集后立即校验"封单是否只出现在涨停时点"等规则,
    # 异常推飞书告警, 不等用户发现(用户连续 3 次发现同类问题, 改为系统自动把关)
    try:
        check_seal_quality(date, time_point, force=True)
    except Exception as e:
        log.warning("封单质量自检异常 err=%s", e)
    return len(raw_all)


def check_seal_quality(date, time_point, force=False):
    """采集后数据质量自检: 校验 snapshot_bid 该时点封单数据是否合理
    规则:
    1. 非涨停股 bid_buy_amt>0 的数量(应为 0, 否则是"开板股挂封单"问题)
    2. 涨停股中 bid_buy_amt=0 的数量(应较少, KPL 未覆盖时会缺)
    3. 封单额/流通市值比 > 0.5 的异常股(封单超过流通市值一半, 大概率数据错位)
    异常时推飞书告警(限频: 同一 date+tp 只推一次)
    force=True: 跳过限频(手动/测试调用也执行)"""
    # 限频: 同一日期+时点只告警一次, 避免 9:31 盘点也触发重复推送
    dedup_key = "seal_quality_warn:%s:%s" % (date, time_point)
    if not force and store.get(dedup_key):
        return None
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_buy_amt, float_mv FROM snapshot_bid "
            "WHERE date=? AND time_point=?", (date, time_point)).fetchall()
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    n_nonzt_seal = 0
    nonzt_samples = []
    n_zt_no_seal = 0
    abnormal_ratio = []
    n_zt = 0
    for code, bc, buy, mv in rows:
        if bc is None:
            continue
        if code[:2] in ("30", "68"):
            is_zt = bc >= 19.9
        elif code[:1] in ("8", "4"):
            is_zt = bc >= 29.9
        else:
            is_zt = bc >= 9.9
        buy = buy or 0
        if is_zt:
            n_zt += 1
            if buy <= 0:
                n_zt_no_seal += 1
            elif mv and buy > mv * 0.5:
                abnormal_ratio.append((code, round(buy / mv, 2)))
        else:
            if buy > 0:
                n_nonzt_seal += 1
                if len(nonzt_samples) < 5:
                    nonzt_samples.append("%s(%.2f%%)封单%.2f亿" % (
                        code, bc, buy / 1e8))
    # 判定: 非涨停挂封单是明确 bug, 必须告警; 其余为提示
    problems = []
    if n_nonzt_seal > 0:
        problems.append("非涨停股挂封单 %d 只! 示例: %s" % (n_nonzt_seal, ", ".join(nonzt_samples)))
    if n_zt > 0 and n_zt_no_seal > n_zt * 0.3:
        problems.append("涨停股缺封单 %d/%d 只(KPL 未覆盖?)" % (n_zt_no_seal, n_zt))
    if abnormal_ratio:
        problems.append("封单/流通比异常 >50%%: %s" % ", ".join("%s:%s" % (c, r) for c, r in abnormal_ratio[:5]))
    if not problems:
        log.info("[数据质量] date=%s tp=%s 自检通过(涨停%d只 缺封单%d 非涨停挂封单%d)",
                 date, time_point, n_zt, n_zt_no_seal, n_nonzt_seal)
        return {"ok": True, "n_zt": n_zt, "n_zt_no_seal": n_zt_no_seal,
                "n_nonzt_seal": n_nonzt_seal}
    # 有问题: 告警
    msg = "竞价封单数据异常 [%s %s]\n%s" % (date, time_point, "\n".join(problems))
    log.warning("[数据质量] %s", msg)
    if not force:
        store.set(dedup_key, 1, 3600)
    try:
        from . import notify
        notify.send_text(msg, title="⚠️ 数据质量告警")
    except Exception as e:
        log.warning("数据质量告警推送失败 err=%s", e)
    return {"ok": False, "problems": problems, "n_zt": n_zt,
            "n_zt_no_seal": n_zt_no_seal, "n_nonzt_seal": n_nonzt_seal}


def snapshot_lastsec_at(ts_sec):
    """最后一秒高频采样: 抓取当前全市场快照存入 snapshot_lastsec(ts=实际时刻秒)
    返回入库数量; 失败返回 0(该秒跳过, 序列仍可用)
    秒级采样用单页600只(8秒窗口限制, 分页全市场需60s+), 覆盖竞价最强前600"""
    date = _bj_date()
    # 防御(2026-08-16): 非交易日拒绝写入
    g = time.gmtime(time.time() + 8 * 3600)
    if g.tm_wday >= 5:
        return 0
    raw_all = _fetch_market_map(full=False)
    if not raw_all:
        log.warning("最后一秒采样为空 ts=%d (东财接口无返回, 该秒跳过)", ts_sec)
        return 0
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_lastsec (date, code, bid_change, bid_amt, ts) "
            "VALUES (?,?,?,?,?)",
            [(date, code, v["bid_change"], v["bid_amt"], ts_sec)
             for code, v in raw_all.items()])
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("最后一秒采样落库失败 ts=%d err=%s", ts_sec, e)
        return 0
    log.info("最后一秒采样已存 date=%s ts=%d 数量%d", date, ts_sec, len(raw_all))
    return len(raw_all)


def _lastsec_loop():
    """最后一秒高频采样线程: 9:24:45-9:25:03 窗口内每秒尝试一次(接口耗时>1s时自动降频),
    ts 记录实际采集时刻 → 序列用于"差值回退"计算最后一秒抢筹"""
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            ts_total = g.tm_hour * 3600 + g.tm_min * 60 + g.tm_sec
            if g.tm_wday < 5 and LASTSEC_START <= ts_total <= LASTSEC_END:
                # 秒级去重: 同一秒只采一次(接口耗时>1s时自然降频, 不会并发堆积)
                if store.setnx("sched:lastsec:%s:%d" % (date, ts_total), 1, ttl=3600):
                    snapshot_lastsec_at(ts_total)
            time.sleep(1)
        except Exception as e:
            log.error("最后一秒采样调度异常 err=%s", e)
            time.sleep(1)


def load_snapshot(date=None, time_point=DEFAULT_POINT):
    """读取某日某时点快照, 返回 {code: {bid_change, bid_amt}}; 无数据返回 {}"""
    date = date or _bj_date()
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, time_point)).fetchall()
        conn.close()
    except Exception:
        return {}
    return {r[0]: {"bid_change": r[1], "bid_amt": r[2]} for r in rows}


def query_snapshot(date, time_point, limit=50):
    """历史回放: 某日某时点全市场快照(按竞价涨幅降序, 带名称)"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt, name, ts FROM snapshot_bid "
            "WHERE date=? AND time_point=? ORDER BY bid_change DESC LIMIT ?",
            (date, time_point, min(limit, 500))).fetchall()
        conn.close()
    except Exception:
        return []
    return [{"code": r[0], "bid_change": r[1], "bid_amt": r[2], "name": r[3] or ""} for r in rows]


# 个股三时点对比展示的时点(9:15/9:20/9:25)
STOCK_POINTS = ["9_15", "9_20", "9_25"]


def query_stock_snapshot(date, code):
    """个股三时点封单对比: 某日该股 9:15/9:20/9:25 各时点快照(bid_change/bid_amt/委买额/市值)
    返回 {name, points: {9_15: {...}, ...}}, 缺时点为 None"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT time_point, code, bid_change, bid_amt, bid_buy_amt, float_mv, name FROM snapshot_bid "
            "WHERE date=? AND code=?", (date, code)).fetchall()
        conn.close()
    except Exception:
        return {"name": "", "points": {}}
    name = ""
    points = {}
    for tp, c, bc, amt, buy, mv, nm in rows:
        if tp in STOCK_POINTS:
            if not name and nm:
                name = nm
            points[tp] = {
                "code": c,
                "bid_change": bc,
                "bid_amt": amt,
                "bid_buy_amt": buy,
                "float_mv": mv,
            }
    return {"name": name, "points": points}


def _is_zt(code, bid_change):
    """竞价涨停判断(按板块涨停幅度, 涨幅达到阈值视为涨停):
    创业/科创(30/68) 20%, 北交所(8/4) 30%, 主板 10%"""
    if bid_change is None:
        return False
    if code[:2] in ("30", "68"):
        return bid_change >= 19.9
    if code[:1] in ("8", "4"):
        return bid_change >= 29.9
    return bid_change >= 9.9


# 榜单分层: 1=9:25 涨停(封死) / 2=9:20 涨停(9:25 回落) / 3=仅 9:15 涨停(9:20/9:25 回落)
LAYER_TAGS = {
    1: "9:25封死",
    2: "9:20封板回落",
    3: "9:15封板回落",
    4: "9:25强势异动(≥5%)",   # 弱市降级: 三层涨停榜为空时显示 9_25 涨幅≥5% 的票
    5: "9:25异动(≥3%)",        # 极弱市降级: 涨幅≥3% 的票
}


def query_3points_board(date, limit=100):
    """三时点封单榜: 全市场按三层原则排序(短线侠式)
    1. 9:25 涨停 → 按 9:25 封单额排序(无封单降级竞价额)
    2. 9:25 未涨停但 9:20 涨停 → 按 9:20 封单额排序
    3. 9:25/9:20 均未涨停但 9:15 涨停 → 按 9:15 封单额排序
    弱市降级(2026-08-17 主人反馈):
    4. 三层涨停榜为空时, 降级展示 9:25 涨幅≥5% 的强势异动票(用竞价额bid_amt近似)
    5. 若仍为空, 降级展示 9:25 涨幅≥3% 的异动票
    返回 [{code, name, layer, tag, sort_amt, points:{9_15..}, degraded:bool}]"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, board FROM snapshot_bid "
            "WHERE date=? AND time_point IN ('9_15','9_20','9_25')", (date,)).fetchall()
        conn.close()
    except Exception:
        return []
    agg = {}
    for tp, code, bc, amt, name, buy, mv, board in rows:
        d = agg.setdefault(code, {"code": code, "name": name or "", "board": board or "", "points": {}})
        if name:
            d["name"] = name
        if board:
            d["board"] = board
        d["points"][tp] = {"bid_change": bc, "bid_amt": amt, "bid_buy_amt": buy, "float_mv": mv}
    out = []
    for code, d in agg.items():
        p15, p20, p25 = d["points"].get("9_15"), d["points"].get("9_20"), d["points"].get("9_25")
        layer = None
        sort_amt = 0.0
        degraded = False
        if p25 and _is_zt(code, p25["bid_change"]):
            layer = 1
            sort_amt = (p25["bid_buy_amt"] or 0) or (p25["bid_amt"] or 0)
        elif p20 and _is_zt(code, p20["bid_change"]):
            layer = 2
            sort_amt = (p20["bid_buy_amt"] or 0) or (p20["bid_amt"] or 0)
        elif p15 and _is_zt(code, p15["bid_change"]):
            layer = 3
            sort_amt = (p15["bid_buy_amt"] or 0) or (p15["bid_amt"] or 0)
        elif p25 and p25["bid_change"] is not None and p25["bid_change"] >= 5:
            # 弱市降级: 三层涨停榜为空时, 展示 9:25 涨幅≥5% 的强势异动(用竞价额)
            layer = 4
            sort_amt = p25["bid_amt"] or 0
            degraded = True
        if layer is None:
            continue
        out.append({"code": code, "name": d["name"], "layer": layer,
                    "tag": LAYER_TAGS[layer], "sort_amt": round(sort_amt, 2),
                    "degraded": degraded,
                    "board": d["board"], "points": d["points"]})
    # 二次降级: 若仍为空(layer 仅 4 都找不到 → 行情极弱), 放宽到 9:25 涨幅≥3%
    if not out:
        for code, d in agg.items():
            p15, p20, p25 = d["points"].get("9_15"), d["points"].get("9_20"), d["points"].get("9_25")
            if p25 and p25["bid_change"] is not None and p25["bid_change"] >= 3:
                out.append({
                    "code": code, "name": d["name"], "layer": 5,
                    "tag": LAYER_TAGS[5], "sort_amt": round(p25["bid_amt"] or 0, 2),
                    "degraded": True,
                    "board": d["board"], "points": d["points"]
                })
    # 分层优先(小→大), 层内按封单额降序
    out.sort(key=lambda x: (x["layer"], -x["sort_amt"]))
    return out[: min(limit, 300)]


def _has_snapshot(date, time_point):
    """某日某时点是否已有快照数据(9_24 重采型时点用, 不依赖 _sched_done 标记)"""
    try:
        conn = database.get_conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, time_point)).fetchone()[0]
        conn.close()
        return n > 0
    except Exception:
        return False


def _scheduler_loop():
    """后台调度: 工作日按时点窗口抓取一次, 每 10 秒轮询; 9:31 后盘点当日采集情况"""
    # 去重标记走 CacheStore: qc/weekend 各自 setnx 1 天
    global _last_intraday_ts   # 分时快照时间戳(模块级), 否则函数内赋值会被视为局部变量 → UnboundLocalError
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            hm = g.tm_hour * 60 + g.tm_min
            for tp, (start, end) in TIME_POINTS.items():
                key = "sched:done:%s:%s" % (date, tp)
                if store.get(key):
                    continue
                if g.tm_wday >= 5:
                    # 周末/节假日: 只在 9:20 记录一次, 避免每分钟刷日志
                    if store.setnx("sched:weekend:" + date, 1, ttl=86400):
                        log.info("[快照采集] 非交易日(周%d) date=%s 跳过采集", g.tm_wday, date)
                    continue
                if not (start <= hm <= end):
                    continue
                if tp == "9_24":
                    # 最后一秒专用时点: 9:24:00-9:24:40 窗口内每轮询重采覆盖,
                    # 最后一份快照 ≈ 9:24:3x~4x(真正"最后一秒", 而非整分钟差)
                    # 9:24:40 后停止重采: 把 _fetch_lock 让给 lastsec(9:24:45-9:25:03 每秒采样),
                    # 避免全市场分页(4-6s)阻塞秒级采样导致抢筹右表缺失
                    # 注意: 判断必须 < 9*60+25(窗口内), 写 9*60+30(9:30) 会导致永不采集!
                    if hm < 9 * 60 + 24 or (hm == 9 * 60 + 24 and g.tm_sec < 40):
                        snapshot_at(tp)
                elif tp == "9_25" and g.tm_sec < 10:
                    # 2026-08-18 主人要求: 9:25 竞价撮合后数据定格, 晚几秒采保证一致 —
                    # 9:25:00-10 是撮合瞬间, 接口返回中间态(如中石科技 20% vs 定格后 19.53%),
                    # 各机器轮询时刻不同导致快照不一致; 延迟到 9:25:10 后采, 拿最终竞价值
                    # 注: 9_25 窗口仍为 9:25:00-9:26:00, 9:25:10 后轮询触发(10s 间隔保证命中)
                    continue
                else:
                    if store.setnx(key, 1, ttl=86400):
                        log.info("[快照采集] 触发时点窗口 tp=%s hm=%d:%02d date=%s", tp, hm // 60, hm % 60, date)
                        if snapshot_at(tp):
                            log.info("[快照采集] 时点完成并入完成集 tp=%s date=%s", tp, date)
                            # 2026-08-18 主人要求: 9_25 竞价快照落库后立即触发 AI 采集+预测
                            # (不等 9:27 轮询窗口, 数据到手就预测, 9:30 前出结果)
                            if tp == "9_25":
                                try:
                                    from . import aipick_scheduler
                                    aipick_scheduler.trigger_after_bid_snapshot()
                                except Exception as e:
                                    log.error("aipick 采集/预测触发失败 err=%s", e)
                        else:
                            # 失败回滚 setnx 标记: 窗口内下一轮轮询(10s)重试, 东财/KPL 瞬时故障自愈
                            # 窗口结束后(hm>end)不再触发, 9:31 盘点告警兜底
                            store.delete(key)
                            log.warning("[快照采集] 时点失败(返回0) tp=%s date=%s 窗口内将重试", tp, date)
            # 9:24:30-9:25:00 抢筹结果快照: 触发 fetch_bid_qiangcang 落库
            # (2026-08-17 修复: 9:26 触发太晚, 开盘啦 Type4 竞价净额 9:25 撮合后清零 → list20=0 落库失败,
            #  提前到最后一秒重采窗口, Type4 数据最接近定格且 bidNetAmt 有效)
            if (g.tm_wday < 5 and 9 * 60 + 24 <= hm <= 9 * 60 + 30
                    and store.setnx("sched:qc:" + date, 1, ttl=86400)):
                try:
                    from . import kpl
                    kpl.clear_cache()
                    d = kpl.fetch_bid_qiangcang()
                    n = len((d or {}).get("list20", []))
                    log.info("竞价抢筹结果快照已存 date=%s list20=%d只(9:24-9:25窗口, Type4有效)",
                             date, n)
                    # 竞价类 tab 落库(2026-08-16 修复): seal/boom 是竞价实时接口,
                    # 15:30 收盘后返回空 → 必须此时落库, 否则竞价委买/爆量/净额 tab 无历史
                    kpl.save_auction_history(date, phase="bid")
                except Exception as e:
                    # 失败回滚: 窗口 9:24-9:30 内下一轮轮询重试(避免 KPL 瞬时故障导致抢筹 tab 当日无数据)
                    store.delete("sched:qc:" + date)
                    log.warning("竞价抢筹结果快照失败(窗口内将重试) err=%s", e)
            # 9:26-9:30 自动应用选股(2026-08-16 用户反馈): 用户打开应用但没点"应用"按钮,
            # 当天历史为空; 9:25 快照齐后给所有活跃用户跑一次自动应用(标记 auto_applied=True).
            # 后台守护线程执行(全市场评分一次+按用户过滤), 不阻塞本调度循环.
            if (g.tm_wday < 5 and 9 * 60 + 26 <= hm <= 9 * 60 + 30
                    and store.setnx("sched:auto_apply:" + date, 1, ttl=86400)):
                try:
                    from . import auto_apply
                    auto_apply.trigger_auto_apply()
                except Exception as e:
                    log.warning("9:26 自动应用 调度失败(不影响抢筹落库) err=%s", e)
            # 15:30-15:35 板块轮动日终快照: 抓当日板块强度 Top10 落库(多数据源), 形成轮动数据基础
            if g.tm_wday < 5 and 15 * 60 + 30 <= hm <= 15 * 60 + 35:
                try:
                    # 两市概况收盘快照(2026-08-16): 供次日"两市总量/较上一日"对比
                    # 收盘后 f6=全天成交额, stockCount=全市场股票数
                    if store.setnx("sched:done:market_brief_" + date, 1, ttl=86400):
                        try:
                            from . import fetcher
                            brief = fetcher.fetch_market_brief(max_age=0)  # 强制刷新
                            if brief:
                                from ..db import database
                                conn = database.get_conn()
                                conn.execute(
                                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
                                    ("market_brief_last",
                                     json.dumps({"date": brief["date"],
                                                 "stockCount": brief["stockCount"],
                                                 "amount": brief["amount"]}),
                                     int(time.time())))
                                conn.commit()
                                conn.close()
                                log.info("两市概况收盘快照已存 date=%s 股票数=%d 成交额=%.0f亿",
                                         brief["date"], brief["stockCount"], brief["amount"])
                            else:
                                store.delete("sched:done:market_brief_" + date)
                        except Exception as e:
                            store.delete("sched:done:market_brief_" + date)
                            log.warning("两市概况收盘快照失败(窗口内重试) err=%s", e)
                    from . import sector_rotation
                    for src in ("kpl", "em", "ths"):
                        if store.setnx("sched:done:sector_%s_%s" % (src, date), 1, ttl=86400):
                            n = sector_rotation.record_today_top(source=src)
                            if not n:
                                # 2026-08-17 修复: 抓取失败删 key 让收盘窗口内重试
                                # (此前失败不删 key -> 当日永久缺失, 如 kpl doc42 当天数据 15:30 未就绪返回 1020)
                                store.delete("sched:done:sector_%s_%s" % (src, date))
                    # 人气热榜历史快照(三源): 供人气榜回看历史
                    from . import hot_rank
                    for src in ("kpl", "em", "ths"):
                        if store.setnx("sched:done:hot_%s_%s" % (src, date), 1, ttl=86400):
                            hot_rank.save_hot_rank_history(date, source=src)
                    # 龙虎榜当日快照: 供龙虎榜回看历史(接口支持 Time 参数, 但落库保证数据在)
                    if store.setnx("sched:done:lhb_" + date, 1, ttl=86400):
                        from . import kpl
                        lst = kpl.fetch_lhb(date)
                        if lst:
                            from ..db import database
                            conn = database.get_conn()
                            conn.execute(
                                "INSERT OR REPLACE INTO lhb_history (date, list, ts) VALUES (?,?,?)",
                                (date, json.dumps(lst, ensure_ascii=False), int(time.time())))
                            conn.commit()
                            conn.close()
                    # 连板梯队当日快照: 供连板天梯回看历史(接口不支持历史日期, 必须落库)
                    if store.setnx("sched:done:ladder_" + date, 1, ttl=86400):
                        from . import kpl as _kpl
                        n = _kpl.save_ladder_history(date)
                        if n:
                            log.info("连板梯队快照已存 date=%s 共%d只", date, n)
                        else:
                            store.delete("sched:done:ladder_" + date)   # 失败回滚, 15:30-15:35 窗口内重试
                    # 竞价异动日终快照(非竞价 tab): 供竞价异动页按日期回看历史
                    # (昨日涨停/昨断板/炸板 接口支持历史日期, 15:30 后抓取仍有效)
                    # 竞价类 tab(seal/boom/qiangcang)已在 9:26 窗口落库, 不在此重复
                    if store.setnx("sched:done:auction_" + date, 1, ttl=86400):
                        from . import kpl as _kpl2
                        n2 = _kpl2.save_auction_history(date, phase="close")
                        if n2:
                            log.info("竞价异动日终快照已存 date=%s 共%d个tab", date, n2)
                        else:
                            store.delete("sched:done:auction_" + date)   # 失败回滚, 15:30-15:35 窗口内重试
                except Exception as e:
                    log.warning("板块轮动日终快照失败 err=%s", e, exc_info=True)
            # 9:31-9:35 盘点当日采集: 缺失时点告警(排查关键, 数据过了点无法补)
            if g.tm_wday < 5 and 9 * 60 + 31 <= hm <= 9 * 60 + 35 and store.setnx("sched:checked:" + date, 1, ttl=86400):
                missing = []
                for tp in TIME_POINTS:
                    if store.get("sched:done:%s:%s" % (date, tp)):
                        continue
                    if tp == "9_24":
                        # 9_24 是重采型时点(不标记 setnx), 用数据存在性判断
                        if not _has_snapshot(date, tp):
                            missing.append(tp)
                    else:
                        missing.append(tp)
                if missing:
                    log.warning("今日快照采集缺失时点: %s (date=%s), 相关功能(加速度/回放)会缺数据",
                                ",".join(missing), date)
                else:
                    log.info("今日快照采集完整: %s (date=%s)", ",".join(TIME_POINTS), date)
            # 两市分时快照滚动存(2026-08-16): 交易时段每 5 分钟调用一次 fetch_market_brief,
            # 写入 settings market_brief_intraday_{date}, 供次日做"两市较昨日同时刻"对比
            # 累计成交额全天单调递增, 5min 粒度足够"同时刻对比"精度
            if g.tm_wday < 5 and 9 * 60 + 30 <= hm <= 15 * 60:
                if time.time() - _last_intraday_ts >= 300:
                    try:
                        from . import fetcher
                        snap = fetcher.record_intraday_snapshot(date=date)
                        _last_intraday_ts = time.time()
                        if snap:
                            log.info("分时快照已存 date=%s amount=%.0f亿 stockCount=%d",
                                     date, snap["amount"], snap["stockCount"])
                    except Exception as e:
                        log.warning("分时快照失败(下一轮重试) err=%s", e)
        except Exception as e:
            log.error("快照调度异常 err=%s", e)
        time.sleep(10)


_last_intraday_ts = 0.0   # 上次分时快照时间戳(模块级, worker 启动时为 0 立即跑一次)


def start_scheduler():
    """main.py startup 调用: 启动后台抓取线程(单 worker 下唯一实例)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=_lastsec_loop, daemon=True)
    t2.start()
    log.info("竞价多时点快照调度已启动(9:15/9:20/9:24/9:25/9:26抢筹结果快照/9:24:45-9:25:03最后一秒高频采样)")
