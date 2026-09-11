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
from . import fetcher, mv_cache, scorer, tickplus
from .cache_store import store

log = logger.get_logger(__name__)

# 时点 -> (开始分钟, 结束分钟) 北京时间(每 10 秒轮询, 窗口 1.5 分钟防漏)
# 2026-08-25: 窗口从 1 分钟扩展到 1.5 分钟, 数据源故障时多 3 次重试机会
# 9_24 用于"最后一秒抢筹"兜底: 调度器在 9:24:30 后每轮询重采覆盖, 最后一份≈9:24:3x~4x
# 真正秒级用 snapshot_lastsec(9:24:45-9:25:03 每秒高频采样 + 差值回退, 见 _lastsec_loop)
TIME_POINTS = {
    # 2026-09-07: 9_15 窗口扩到 9:17 —— 9:15:00 竞价刚开始行情源数据未生成, 需延后采集
    # + 数据未就绪时窗口内重采(见 _scheduler_loop)
    "9_15": (9 * 60 + 15, 9 * 60 + 17),
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
                9:24:45-9:25:03 共18秒窗口, 秒级采样窗口内无法全市场分页)
    并发(2026-08-19 起): 全市场 30 页 ThreadPoolExecutor 并发(实测 386ms/次, 2026-08-31),
          单页模式 3 分区并发 3×~1.5s → ~1.5s, 秒级采样 8 秒窗口可采 5-8 个点
    三源冗余(2026-08-25): 东财全部失败时用开盘啦竞价委买/爆量榜兜底,
          至少保存竞价异动关键股票(非全市场, 好过完全缺失)
    """
    _t0 = time.time()
    with _fetch_lock:
        raw_all = {}

        def _grab(m):
            try:
                if full:
                    return m, fetcher._fetch_market_all_with_fallback(scorer.market_fs([m]))
                return m, fetcher._fetch_market_with_fallback(scorer.market_fs([m]))
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
                        "float_mv": scorer.parse_float(s.get("f21")),             # 流通市值(元, 东财 f21)
                        "free_mv": scorer.parse_float(s.get("f117")) or scorer.parse_float(s.get("f21")),  # 实际流通市值(元): 东财 f117 自由流通≈开盘啦"实际流通", f21 兜底
                        "board": str(s.get("f103") or s.get("f100") or ""),       # 概念(f103优先, 行业f100兜底)
                    }
        # 三源冗余兜底: 东财全失败时用开盘啦竞价榜填充关键股票
        if not raw_all:
            log.warning("[快照采集] 东财全分区失败, 尝试开盘啦竞价榜兜底")
            raw_all = _fetch_kpl_fallback()
        # 双源并存(P2-1, 2026-09-12): 东财之外并发第二源 TickPlus fullbid。
        # 串行放在东财之后(不并入上面的线程池): TP 是新域名不受东财限流约束,
        # 但它挂了绝不能拖累主链路 —— 这里整体 try, 失败只记日志(见 tickplus 模块)。
        # 只在 full=True(时点全市场快照)时启用: 秒级采样窗口 18 秒, 全推成本不划算。
        if full:
            n_em = len(raw_all)
            try:
                tp_map = tickplus.snapshot_map()
            except Exception as e:                              # noqa: BLE001
                tp_map = {}
                log.warning("[快照采集] TickPlus 采集异常(已忽略) err=%s", e)
            if tp_map:
                st = _merge_tickplus(raw_all, tp_map)
                log.info("[快照采集] 双源合并 东财%d只 + TickPlus%d只 → 补票%d只 补值%d项 → 合计%d只",
                         n_em, len(tp_map), st["added"], st["filled"], len(raw_all))
        # 2026-08-31 可观测性: 采集阶段耗时单独记录(与落库耗时分离, 定位时点失真来源)
        log.info("[快照采集] 行情拉取完成 full=%s 数量%d 耗时%.0fms", full, len(raw_all),
                 (time.time() - _t0) * 1000)
        return raw_all


def _merge_tickplus(raw_all, tp_map):
    """把 TickPlus 全市场竞价数据并入东财结果(原地改 raw_all), 返回 {"added","filled"}。

    合并纪律(**只补缺, 绝不覆盖** —— 两源口径不同, 覆盖会让同一时点出现两套数):
      ① 东财没有的 code → 新增行(TickPlus 补票; 市值/名称由 mv_cache 后补)
      ② 东财有但 bid_change=0 或 bid_amt=0(东财竞价期常给不出值) → 用 TP 补位
      ③ 已有正常值 → 一律不动
    TickPlus 无涨幅(zf=None)的行不新增: 没涨幅的票进不了评分, 只会污染名单。"""
    added = filled = 0
    for code, t in (tp_map or {}).items():
        bc = t.get("bid_change")
        v = raw_all.get(code)
        if v is None:
            if bc is None:
                continue
            raw_all[code] = {
                "bid_change": bc,
                "bid_amt": t.get("bid_amt") or 0.0,
                "name": "",
                "bid_buy_amt": 0,
                "float_mv": 0,        # TickPlus 不给市值 → 由 mv_cache.fill 后补
                "free_mv": 0,
                "board": "",
                "_src": "tp",
            }
            added += 1
            continue
        if not (v.get("bid_change") or 0) and bc:
            v["bid_change"] = bc
            filled += 1
        if not (v.get("bid_amt") or 0) and (t.get("bid_amt") or 0):
            v["bid_amt"] = t["bid_amt"]
            filled += 1
    return {"added": added, "filled": filled}


def _fetch_kpl_fallback():
    """开盘啦竞价榜兜底: 东财故障时用竞价委买/爆量榜构造部分快照
    返回 {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv, board}}
    非全市场(仅竞价活跃股), 但保证竞价异动页有数据可显示"""
    fallback = {}
    try:
        from . import kpl
        kpl.clear_cache()
        # 竞价委买榜: 涨停股封单额 + 概念
        seal_list = kpl.fetch_bid_seal() or []
        for s in seal_list:
            code = s.get("code") or ""
            if not code:
                continue
            # 单位: 开盘啦 bidAmt/floatMv 是**元**, 本表 bid_amt 是**万元** → 必须 /1e4。
            # 2026-09-09 实锤: 漏除导致 9_20 时点竞价额中位 577.5 亿(应为 577 万), 放大 1e4 倍;
            # float_mv 恒 0 会让这批兜底票全被 floatMvFloor 剔除 → 兜底白做。
            fallback[code] = {
                "bid_change": s.get("bidChange") or 0,
                "bid_amt": (s.get("bidAmt") or 0) / 1e4,
                "name": s.get("name") or "",
                "bid_buy_amt": s.get("bidSealAmt") or 0,
                "float_mv": s.get("floatMv") or 0,
                "board": s.get("board") or "",
            }
        # 竞价爆量榜: 高竞价量股票
        boom_list = kpl.fetch_bid_boom() or []
        for s in boom_list:
            code = s.get("code") or ""
            if not code or code in fallback:
                continue
            fallback[code] = {
                "bid_change": s.get("bidChange") or 0,
                "bid_amt": (s.get("bidAmt") or 0) / 1e4,
                "name": s.get("name") or "",
                "bid_buy_amt": 0,
                "float_mv": s.get("floatMv") or 0,
                "board": s.get("board") or "",
            }
        log.info("[快照采集] 开盘啦兜底: 委买%d只 爆量%d只 合并去重%d只",
                 len(seal_list), len(boom_list), len(fallback))
    except Exception as e:
        log.error("[快照采集] 开盘啦兜底失败 err=%s", e)
    return fallback


def _zero_chg_rate(date, time_point):
    """某时点快照中 bid_change=0(含 NULL) 的占比: 用于判断竞价数据是否已生成。
    9:15 竞价刚开始时行情源字段常未更新(bid_change=0), 据此触发窗口内重采。"""
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN bid_change IS NULL OR bid_change=0 THEN 1 ELSE 0 END) z "
            "FROM snapshot_bid WHERE date=? AND time_point=?", (date, time_point)).fetchone()
        conn.close()
        # 注意: database.get_conn() 未设 row_factory → 返回 tuple, 必须用下标取值
        # (写 row["n"] 会 TypeError 被下面 except 吞掉 → 重采逻辑静默失效)
        n = (row[0] if row else 0) or 0
        if n < 100:      # 样本太小不判断(异常采集)
            return 0.0
        return ((row[1] if row else 0) or 0) / n
    except Exception as e:
        log.warning("[快照采集] 零值率统计失败 date=%s tp=%s err=%s", date, time_point, str(e)[:80])
        return 0.0


def _same_as_prev_rate(date, time_point, prev_point):
    """9_25 定格"未发布"保险丝: 与上一时点(9_24)逐票竞价额**完全相等**的占比。

    判据: 9:25 撮合后东财定格值发布前, 接口返回的仍是 9:24 的残值 → 与 9_24 逐票相等;
    一旦发布, 竞价额普遍变化 → 相等率骤降。

    实测(生产全库 snapshot_bid, 2026-09-11 校准):
        9/2~9/10 共 7 个正常交易日, 9_25 vs 9_24 相等率**恒为 0.0%**;
        熔断日 9/11(仅 132 行兜底)为 7.8%。
    → 阈值取 0.50: 正常日(0%)绝不会误触发, 真未发布(≈100%)必触发, 中间留足余量。

    只统计**有额(>0)**的票: 否则大量 0==0 会把比率虚高到 1, 造成误判。
    与 _zero_chg_rate 同款: database.get_conn() 未设 row_factory → 必须用下标取值。
    """
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN a.bid_amt = b.bid_amt THEN 1 ELSE 0 END) s "
            "FROM snapshot_bid a JOIN snapshot_bid b "
            "  ON b.date = a.date AND b.code = a.code AND b.time_point = ? "
            "WHERE a.date = ? AND a.time_point = ? AND a.bid_amt > 0",
            (prev_point, date, time_point)).fetchone()
        conn.close()
        n = (row[0] if row else 0) or 0
        if n < 100:      # 样本太小不判断(异常采集)
            return 0.0
        return ((row[1] if row else 0) or 0) / n
    except Exception as e:
        log.warning("[快照采集] 同额率统计失败 date=%s tp=%s err=%s",
                    date, time_point, str(e)[:80])
        return 0.0


def _snapshot_quality(date, time_point):
    """9:31 盘点用: 返回 (行数, 有竞价额占比)。

    为什么需要: 旧自检只查 store 里"时点完成标记"是否存在, 不查实际落了多少行 ——
    2026-09-11 东财熔断日 9_25 仅落 132 行(正常 5500+), 自检仍打印"采集完整",
    故障静默到人工发现为止。行数与有额率是唯一能暴露"采集了但采废了"的指标。
    """
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN bid_amt > 0 THEN 1 ELSE 0 END) a "
            "FROM snapshot_bid WHERE date=? AND time_point=?", (date, time_point)).fetchone()
        conn.close()
        n = (row[0] if row else 0) or 0
        if n <= 0:
            return 0, 0.0
        return n, ((row[1] if row else 0) or 0) / n
    except Exception as e:
        log.warning("[快照采集] 质量统计失败 date=%s tp=%s err=%s",
                    date, time_point, str(e)[:80])
        return -1, 0.0   # -1 = 统计失败, 调用侧按"不告警"处理(避免误报)


# ---- 竞价额: 单位防御 + 缺失质量门(2026-09-10) ----
# 实测(生产全库统计): 9_25 定格竞价额 P99=4452万、max=5.19亿 → 单票竞价额超过
# 10亿 即可判定**单位错误**(元当万元存储, 差 1e4 倍)。历史脏数据如
# 9/9 9_20 中位 5,775,000"万元"(实为 577 万元)、9/9 9_24 max 341,698,188。
BID_AMT_MAX_WAN = 100000.0      # 单票竞价额合理上限(万元) = 10 亿
BID_AMT_COVER_MIN = 0.30        # 竞价额覆盖率下限: 低于此值判定"竞价额缺失"

# ---- 9_25 定格采集时刻下限(2026-09-11 P0-1) ----
_BID25_MIN_SEC = 20             # 9:25 后至少 20 秒才采(原 10 秒)
_SAME_PREV_MAX = 0.50           # 9_25 与 9_24 逐票竞价额"相等"占比上限, 超此值判定"定格值未发布"
_BID25_RETRY_UNTIL = 9 * 3600 + 25 * 60 + 50   # 9:25:50 前允许回滚重采, 之后接受最后一次

# ---- 9:31 盘点质量阈值(2026-09-11 P0-2) ----
_SNAP_MIN_ROWS = 3000           # 单时点行数下限: 正常 5500+, 熔断日实测 132 → 3000 足够安全
_SNAP_MIN_AMT_RATE = 0.50       # 有竞价额占比下限, **仅对 9_15/9_25 生效**
# 9_20/9_24 不做有额率告警: 实测长期仅 0.7%~2.3%(东财限流→腾讯兜底, 竞价期无额字段),
# 属结构性缺口而非故障, 若一并告警会造成"正常日天天误报"。
_SNAP_AMT_RATE_POINTS = ("9_15", "9_25")


def _fix_bid_amt_unit(raw_all):
    """单位防御(就地修正): bid_amt 超合理上限 → 按元→万元换算; 换算后仍离谱 → 置 0

    返回 (换算只数, 置0只数)。宁可置 0 也不能让放大 1e4 倍的值落库 ——
    竞价额参与评分与加速度计算, 一个脏值会带歪整批名单。
    """
    n_fix = n_zero = 0
    for _code, v in raw_all.items():
        amt = v.get("bid_amt") or 0
        if amt <= BID_AMT_MAX_WAN:
            continue
        fixed = amt / 1e4
        if 0 < fixed <= BID_AMT_MAX_WAN:
            v["bid_amt"] = fixed
            n_fix += 1
        else:                       # 换算后仍离谱(如历史 4.9e15)= 不是单纯单位错, 弃用
            v["bid_amt"] = 0.0
            n_zero += 1
    if n_fix or n_zero:
        log.warning("[快照采集] 竞价额单位异常已修正: 换算%d只 置0%d只(阈值>%.0f万)",
                    n_fix, n_zero, BID_AMT_MAX_WAN)
    return n_fix, n_zero


def _guard_bid_amt_missing(date, time_point, raw_all):
    """竞价额缺失质量门: 行情源通(有涨幅)却几乎没竞价额 → 保留库内已有正值, 不让 0 冲掉

    判定: 总量>=1000 只 且 竞价额覆盖率<30% 且 涨幅覆盖率>50%
    典型场景(生产 9/4、9/7、9/9、9/10 实测): 9_20/9_24 时点东财被限流 → 回退腾讯
    兜底, 而腾讯无真实竞价额(竞价窗口内字段被清零) → 5500 只里只有 0~130 只有额。
    若不设防, INSERT OR REPLACE 会把窗口内上一轮重采集到的好数据直接冲成 0。
    """
    n = len(raw_all)
    if n < 1000:
        return 0
    n_amt = sum(1 for v in raw_all.values() if (v.get("bid_amt") or 0) > 0)
    n_chg = sum(1 for v in raw_all.values() if (v.get("bid_change") or 0) != 0)
    if n_amt / n >= BID_AMT_COVER_MIN or n_chg / n <= 0.5:
        return 0
    prev = {}
    conn = None
    try:
        conn = database.get_conn()
        prev = {r[0]: r[1] for r in conn.execute(
            "SELECT code, bid_amt FROM snapshot_bid WHERE date=? AND time_point=? AND bid_amt>0",
            (date, time_point)).fetchall()}
    except Exception as e:                                     # noqa: BLE001
        log.warning("[快照采集] 竞价额质量门读历史失败(跳过保留) err=%s", e)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass
    restored = 0
    for code, v in raw_all.items():
        if (v.get("bid_amt") or 0) <= 0 and (prev.get(code) or 0) > 0:
            v["bid_amt"] = prev[code]
            restored += 1
    log.warning("[快照采集] 竞价额疑似缺失 date=%s tp=%s: 有额%d/%d(%.1f%%) 有涨幅%d/%d "
                "→ 判定源降级, 保留库内已有正值%d只",
                date, time_point, n_amt, n, 100.0 * n_amt / n, n_chg, n, restored)
    return n_amt


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
    n_fill = 0        # 2026-09-07: 开盘啦补位(涨幅/竞价额)计数
    kpl_cnt = 0
    try:
        from . import kpl
        kpl.clear_cache()          # 保证拿到当前时点的新鲜数据(非前一时点缓存)
        kpl_seal = kpl.fetch_bid_seal() or []
        kpl_map = {s["code"]: s for s in kpl_seal}
        kpl_cnt = len(kpl_seal)
        for code, v in raw_all.items():
            s0 = kpl_map.get(code)
            # 2026-09-07(主人要求): 开盘啦竞价榜(约 100-200 只强势/涨停股)的
            # **涨幅与竞价额补位** —— 行情源在竞价期常给不出值:
            #   9:15 东财竞价数据未生成(bid_change=0 占 70%)
            #   9:20/9:24 腾讯兜底无真实竞价额(f616=成交额近似=0)
            # 只在原值为 0/缺失时补(不覆盖已有正常值, 避免跨源口径冲突),
            # 且必须**早于 is_zt 判定**, 否则补来的涨停涨幅不参与封单/涨停判定。
            if s0:
                if not (v.get("bid_change") or 0) and (s0.get("bidChange") or 0):
                    v["bid_change"] = s0["bidChange"]
                    n_fill += 1
                # 单位: 开盘啦 bidAmt 是**元**, 本表 bid_amt 是**万元**(与 scorer.get_bid_amt 一致)
                if not (v.get("bid_amt") or 0) and (s0.get("bidAmt") or 0):
                    v["bid_amt"] = (s0.get("bidAmt") or 0) / 1e4
                    n_fill += 1
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
                # 2026-09-07 修复(主人质疑"既然 9:20 能显示开盘啦封单, 9:15 为啥不能"):
                # 开盘啦委买榜 bidSealAmt 是**真实封单**(该榜只含涨停/委买强势股),
                # 不再要求 is_zt —— is_zt 依赖东财 bid_change, 而 9:15 东财竞价数据
                # 常未生成(bid_change=0 占 70%) → 判定"非涨停"把真实封单误清零,
                # 这正是 9:15 列空白的另一半原因(与采集过早叠加)。
                # 注: 上面"非涨停清零"只针对**东财 f10×f5 伪封单**(2026-08-16 防开板股
                # 残留误导), 真实封单不受该约束。
                if seal:
                    v["bid_buy_amt"] = seal
                    n_seal += 1
                b = s.get("board") or ""
                if b:
                    v["board"] = b     # 开盘啦概念覆盖东财
                    n_board += 1
        if n_seal or n_board or n_fill:
            log.info("[快照采集] time=%s 开盘啦叠加 封单%d只 概念%d只 补位%d项 "
                     "(开盘啦返回%d只)", time_point, n_seal, n_board, n_fill, kpl_cnt)
        else:
            log.info("[快照采集] time=%s 开盘啦叠加 0 只 (开盘啦返回%d只, 可能非交易时段或接口异常)",
                     time_point, kpl_cnt)
    except Exception as e:
        log.warning("[快照采集] time=%s 开盘啦封单叠加失败 err=%s", time_point, e, exc_info=True)
    # 落库前体检: ①单位防御(元当万元) ②竞价额缺失质量门(不让 0 冲掉已有正值)
    _fix_bid_amt_unit(raw_all)
    _guard_bid_amt_missing(date, time_point, raw_all)
    # P2-2(2026-09-12) 市值/名称兜底 + 写日频缓存。
    # 必须在**落库之前**: 补到的市值要跟本次快照一起入库, 否则 TickPlus 补进来的票
    # float_mv=0 → 被市值门槛当小盘股全部误杀(补了等于白补)。
    try:
        mst = mv_cache.fill(raw_all, date=date)
        if mst["need"]:
            log.info("[快照采集] 市值兜底 time=%s 缺%d只 → 缓存补%d 腾讯补%d 仍缺%d (落缓存%d行)",
                     time_point, mst["need"], mst["from_cache"], mst["from_tencent"],
                     mst["miss"], mst["saved"])
    except Exception as e:                                     # noqa: BLE001
        log.warning("[快照采集] 市值兜底异常(不影响落库) time=%s err=%s", time_point, e)
    conn = None
    try:
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, free_mv, board, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(date, time_point, code, v["bid_change"], v["bid_amt"], v.get("name", ""),
              v.get("bid_buy_amt", 0), v.get("float_mv", 0), v.get("free_mv", 0), v.get("board", ""), int(time.time()))
             for code, v in raw_all.items()])
        conn.commit()
    except Exception as e:
        log.error("[快照采集] 落库失败 time=%s err=%s", time_point, e, exc_info=True)
        return 0
    finally:
        # 落库失败也必须关连接: 否则写锁残留 → 后续 "database is locked" 雪崩
        if conn is not None:
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass
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
            "SELECT code, bid_change, bid_buy_amt, float_mv, bid_amt FROM snapshot_bid "
            "WHERE date=? AND time_point=?", (date, time_point)).fetchall()
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    # 竞价额覆盖率(2026-09-10): 行情源降级(东财限流→腾讯兜底)时竞价额大面积缺失,
    # 表现是"涨幅正常但竞价额几乎全 0" → 加速度不可算, 必须告警(不等用户发现)
    n_amt = sum(1 for r in rows if (r[4] or 0) > 0)
    amt_cover = (n_amt / len(rows)) if rows else 0.0
    n_nonzt_seal = 0
    nonzt_samples = []
    n_zt_no_seal = 0
    abnormal_ratio = []
    n_zt = 0
    for code, bc, buy, mv, amt in rows:
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
    if len(rows) >= 1000 and amt_cover < BID_AMT_COVER_MIN:
        problems.append("竞价额缺失 %d/%d 只(%.1f%%, 阈值%.0f%%): 源降级无竞价额, 加速度不可算"
                        % (n_amt, len(rows), 100.0 * amt_cover, 100.0 * BID_AMT_COVER_MIN))
    if abnormal_ratio:
        problems.append("封单/流通比异常 >50%%: %s" % ", ".join("%s:%s" % (c, r) for c, r in abnormal_ratio[:5]))
    if not problems:
        log.info("[数据质量] date=%s tp=%s 自检通过(涨停%d只 缺封单%d 非涨停挂封单%d "
                 "竞价额覆盖%.1f%%)",
                 date, time_point, n_zt, n_zt_no_seal, n_nonzt_seal, 100.0 * amt_cover)
        return {"ok": True, "n_zt": n_zt, "n_zt_no_seal": n_zt_no_seal,
                "n_nonzt_seal": n_nonzt_seal, "amt_cover": amt_cover}
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
    # 2026-09-09: conn.close() 必须在 finally —— 原写法放在 try 末尾, 查询一旦抛异常
    # (SQLite locked / 磁盘满) 连接就泄漏。本函数是每次选股必读的高频路径。
    conn = None
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, time_point)).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass
    return {r[0]: {"bid_change": r[1], "bid_amt": r[2]} for r in rows}


def load_snapshot_full(date=None, time_point="9_25"):
    """读某日某时点**全市场快照行**(盘后 filter 候选池用, 2026-09-07 主人要求:
    候选池=全市场且直接用已自动采集的快照表, 不再实时拉全市场 28 页)。
    返回 {code: {name, bid_change, bid_amt, float_mv(元), free_mv(元), board}};
    float_mv/free_mv 保持原始单位(元, 与行情 f20/f21 一致, /1e8=亿)。
    当日该时点无快照(周末/休市/采集缺失)→ 自动回退**最近一个有快照的交易日**,
    保证休市/盘后浏览仍能按最近竞价结果筛股。"""
    date = date or _bj_date()
    conn = None
    try:
        conn = database.get_conn()
        # 找最近的可用日期(含当天, 往前最多 15 个自然日; 交易日快照才有 9_25 行)
        row = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid "
            "WHERE date<=? AND time_point=? AND date>=" +
            "date('now', '-15 days', '+8 hours')",
            (date, time_point)).fetchone()
        use_date = row[0] if row and row[0] else date
        rows = conn.execute(
            "SELECT code, name, bid_change, bid_amt, float_mv, free_mv, board "
            "FROM snapshot_bid WHERE date=? AND time_point=?", (use_date, time_point)).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass
    if use_date != date:
        log.info("load_snapshot_full: %s 无快照, 回退最近交易日 %s (time_point=%s)",
                 date, use_date, time_point)
    return {
        r[0]: {"name": r[1] or "", "bid_change": r[2], "bid_amt": r[3],
               "float_mv": r[4] or 0.0, "free_mv": r[5] or 0.0, "board": r[6] or ""}
        for r in rows}


# 竞额定格读取(2026-09-03): 盘中「竞额」列应显示当日 9:25 定格竞价额而非行情实时成交额
# (东财封禁期全市场行情走腾讯兜底, 腾讯无竞价额字段, fetcher 把累计实时成交额塞进 f616 近似 →
#  盘中直接读行情 bidAmt=实时成交额失真)。9:30 前无连续竞价, 各时点快照 bid_amt=该时点竞价累计
# 撮合额, 9:25 时点即当日最终定格竞价额, 全天(盘中/收盘)恒定可信。
# 时点优先级: 9_25 定格 > 9_24 抢筹尾声 > 9_20 > 9_15(越晚越接近定格)
_BID_AMT_POINT_RANK = {"9_25": 0, "9_24": 1, "9_20": 2, "9_15": 3}

# 定格回退(2026-09-08): 凌晨 0:00-9:25 采集前 / 周末 / 节假日当日无快照 → 定格 map 若返回 {}
# 会导致: ① bidAmt 缺定格 → 竞价额门槛(bidAmtFloor)把整个名单滤空(主人反馈"周末名单消失");
# ② bidChange 缺定格 → scorer 退 f3(收盘涨幅) → 重现"竞涨=现涨"。与 load_snapshot_full 同口径:
# 9_25 每交易日必采, 以其存在性代表"该交易日已完成竞价定格"; 回退窗口 15 自然日(覆盖周末/法定长假)
_FALLBACK_SNAP_DAYS = 15


def _latest_snapshot_date(date):
    """取应查快照日期: 当日已有 9_25 定格行(已过 9:25 采集) → 当日;
    当日无(凌晨 0:00-9:25 前/周末/节假日/当日采集缺失) → 表内最近一个 ≤date 且有 9_25 行
    的交易日(自动覆盖跨周末周一凌晨); 15 自然日内无任何 9_25 行(空库/长假超窗) → 原 date
    (查询自然返回 {}, 保持现状兜底, 不把陈旧数据当最近交易日)。"""
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE date<=? AND time_point='9_25' "
            "AND date>=date('now', '-%d days', '+8 hours')" % _FALLBACK_SNAP_DAYS,
            (date,)).fetchone()
        conn.close()
    except Exception:
        return date
    return row[0] if row and row[0] else date


def load_day_bid_amt(date=None):
    """当日竞价额定格 map: {code: bid_amt(万元)} — 取每只股票当日最晚时点的非空 bid_amt。
    返回 {code: amt}; 当日无快照/无数据返回 {}。调用方(stocks.py/system_batch)在评分时传入
    picker 选股(day_bid_amt_wan=...), 使 bidAmt/bidRatio 以 9:25 定格竞价额为准。
    2026-09-08 回退: 当日无 9_25 快照(凌晨 0:00-9:25 前/周末/节假日) → 自动用最近一个
    交易日的定格(与 load_snapshot_full 同口径), 主人要求"非交易时段用上个交易日数据"。
    正常交易日 9:25 采集完成后当日有行 → 行为不变(用当日)。"""
    date = date or _bj_date()
    use_date = _latest_snapshot_date(date)
    if use_date != date:
        log.info("load_day_bid_amt: %s 无定格快照, 回退最近交易日 %s", date, use_date)
    conn = None
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, time_point, bid_amt FROM snapshot_bid "
            "WHERE date=? AND bid_amt>0", (use_date,)).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass
    best = {}
    for code, tp, amt in rows:
        r = _BID_AMT_POINT_RANK.get(tp)
        if r is None:
            continue
        cur = best.get(code)
        if cur is None or r < cur[0]:
            best[code] = (r, float(amt))
    return {c: v[1] for c, v in best.items()}


def load_day_bid_change(date=None):
    """当日竞价涨幅定格 map: {code: bid_change(%)} — 取每只股票当日最晚时点的非空 bid_change。
    2026-09-08 生产事故修复: 东财行情 f615 收盘后返回 "-"(float 转换抛异常), scorer
    get_bid_change 退 f3(现价/收盘涨幅) → 盘后 filter 竞涨=现涨 + 「涨幅≤bidGt」过滤按现价判,
    筛出当日大跌票。与 bidAmt 定格(load_day_bid_amt)同理: 窗口外评分/展示以 9:25 定格
    竞价涨幅为准(全天恒定)。返回 {code: %}; 当日无快照/无数据返回 {}。
    2026-09-08 回退: 当日无 9_25 快照(凌晨 0:00-9:25 前/周末/节假日) → 自动用最近一个
    交易日的定格(与 load_snapshot_full 同口径), 主人要求"非交易时段用上个交易日数据"。
    正常交易日 9:25 采集完成后当日有行 → 行为不变(用当日)。"""
    date = date or _bj_date()
    use_date = _latest_snapshot_date(date)
    if use_date != date:
        log.info("load_day_bid_change: %s 无定格快照, 回退最近交易日 %s", date, use_date)
    conn = None
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, time_point, bid_change FROM snapshot_bid "
            "WHERE date=? AND bid_change IS NOT NULL AND bid_change<>''", (use_date,)).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass
    best = {}
    for code, tp, chg in rows:
        r = _BID_AMT_POINT_RANK.get(tp)
        if r is None:
            continue
        try:
            chg = float(chg)
        except (TypeError, ValueError):
            continue
        cur = best.get(code)
        if cur is None or r < cur[0]:
            best[code] = (r, chg)
    return {c: v[1] for c, v in best.items()}


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
            "SELECT time_point, code, bid_change, bid_amt, bid_buy_amt, COALESCE(NULLIF(free_mv,0), float_mv), name FROM snapshot_bid "
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
            "SELECT time_point, code, bid_change, bid_amt, name, bid_buy_amt, COALESCE(NULLIF(free_mv,0), float_mv), board FROM snapshot_bid "
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
                elif tp == "9_25" and g.tm_sec < _BID25_MIN_SEC:
                    # 2026-08-18 主人要求: 9:25 竞价撮合后数据定格, 晚几秒采保证一致 —
                    # 9:25:00-10 是撮合瞬间, 接口返回中间态(如中石科技 20% vs 定格后 19.53%),
                    # 各机器轮询时刻不同导致快照不一致; 延迟到 9:25:10 后采, 拿最终竞价值
                    # 注: 9_25 窗口仍为 9:25:00-9:26:00, 9:25:10 后轮询触发(10s 间隔保证命中)
                    #
                    # 2026-09-11 P0-1: 阈值 10 → 20(常量 _BID25_MIN_SEC)。
                    # 依据: 生产库 9_25 落库时刻实测 9/7=09:25:20 9/8=09:25:22 9/9=09:25:20
                    #   9/10=09:25:32, 但熔断日 9/11=**09:25:12** —— 轮询相位(10s)使落库时刻
                    #   在 10~40s 间抖动, 下限 10s 会踩到撮合未完成的中间态。
                    #   提到 20s 消除相位抖动下限; 窗口内仍有 20/30/40/50 四次机会。
                    # 注: 同额率实测(生产全库)正常日恒为 0.0%, 故下方"未发布"校验留作保险丝,
                    #   正常日绝不会误触发(见 _same_as_prev_rate 注释)。
                    continue
                elif tp == "9_15" and g.tm_sec < 5:
                    # 2026-09-07 修复(主人反馈"竞价封单表格 9:15 列为空"):
                    # 9:15:00 集合竞价刚开始, 行情源(东财/腾讯)竞价字段尚未生成 →
                    # 实测 9:15:02 采集 **70% 的票 bid_change=0**(对照 9:20 仅 28%),
                    # 一字板/强势票的竞价额与封单拿不到 → 表格 9:15 列大片空白。
                    # 策略(**自适应**, 主人要求不要固定延后 30s 那么迟):
                    #   9:15:05 即首采(尽量贴近 9:15 原始竞价状态) → 采完立刻校验
                    #   零值率, >60% 说明数据未就绪 → 回滚标记, 10s 后重采覆盖,
                    #   直到就绪或 9:17 窗口结束。数据早就早定格, 晚就晚定格。
                    continue
                else:
                    if store.setnx(key, 1, ttl=86400):
                        log.info("[快照采集] 触发时点窗口 tp=%s hm=%d:%02d date=%s", tp, hm // 60, hm % 60, date)
                        if snapshot_at(tp):
                            # 2026-09-07: 9_15 数据就绪度校验 —— 涨幅=0 占比 >60% 说明
                            # 行情源竞价数据仍未生成(极端行情/接口延迟), 回滚完成标记
                            # 让窗口内下一轮(10s 后)重采覆盖, 避免整列空白
                            if tp == "9_15" and _zero_chg_rate(date, tp) > 0.6:
                                store.delete(key)
                                log.warning("[快照采集] 9_15 数据未就绪(涨幅0占比>60%%), "
                                            "窗口内重采 date=%s hm=%d:%02d", date, hm // 60, hm % 60)
                                continue
                            # 2026-09-11 P0-1: 9_25 定格值"未发布"保险丝
                            # 与 9_24 逐票竞价额完全相等 → 说明接口仍在返回 9:24 残值,
                            # 回滚完成标记让窗口内下一轮(10s 后)重采覆盖。
                            # 9:25:50 之后不再回滚: 宁可保留中间值也不能整点缺失 ——
                            # 9_25 缺失会连锁砸坏选股名单(9/11 熔断日仅 132 行即导致候选池塌陷)。
                            # 必须放在 aipick / system_batch 触发之前: 不能用残值跑预测与锁仓。
                            if tp == "9_25" and _same_as_prev_rate(date, tp, "9_24") > _SAME_PREV_MAX:
                                if hm * 60 + g.tm_sec < _BID25_RETRY_UNTIL:
                                    store.delete(key)
                                    log.warning(
                                        "[快照采集] 9_25 定格值疑似未发布(与9_24同额率>%.0f%%), "
                                        "窗口内重采 date=%s hm=%d:%02d:%02d",
                                        _SAME_PREV_MAX * 100, date, hm // 60, hm % 60, g.tm_sec)
                                    continue
                                log.warning(
                                    "[快照采集] 9_25 临近窗口末尾, 接受当前值不再重采 "
                                    "date=%s hm=%d:%02d:%02d", date, hm // 60, hm % 60, g.tm_sec)
                            log.info("[快照采集] 时点完成并入完成集 tp=%s date=%s", tp, date)
                            # 2026-08-18 主人要求: 9_25 竞价快照落库后立即触发 AI 采集+预测
                            # (不等 9:27 轮询窗口, 数据到手就预测, 9:30 前出结果)
                            if tp == "9_25":
                                try:
                                    from . import aipick_scheduler
                                    aipick_scheduler.trigger_after_bid_snapshot()
                                except Exception as e:
                                    log.error("aipick 采集/预测触发失败 err=%s", e)
                                # 2026-08-30 主人需求: 9_25 落库后自动跑 system batch 存历史回看
                                # (即使当天没点选股, 也能看到系统当时推荐的 top 30)
                                try:
                                    from . import system_batch
                                    system_batch.run_system_batch("9_25")
                                except Exception as e:
                                    log.error("system_batch 触发失败 err=%s", e)
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
                                # 2026-09-07: 收盘后 market_brief_last 被**今日**覆盖,
                                # 而它被用作"较昨日全天"的对比基准 → 自我比较恒 0
                                # (前端显示"放量 0 亿")。写入前把上一份滚动存为 prev。
                                _old = conn.execute(
                                    "SELECT value FROM settings WHERE key='market_brief_last'"
                                ).fetchone()
                                if _old and _old[0]:
                                    try:
                                        _od = json.loads(_old[0]).get("date")
                                    except Exception:
                                        _od = None
                                    if _od and _od != brief["date"]:
                                        conn.execute(
                                            "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                                            "VALUES (?,?,?)",
                                            ("market_brief_prev", _old[0], int(time.time())))
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
                except Exception as e:
                    log.warning("板块轮动/两市概况 日终快照失败 err=%s", e, exc_info=True)
            # 15:50-15:55 板块轮动+人气热榜兜底补跑:
            # 查 DB 当日各源是否已入库, 缺则尝试补抓(避开开盘啦15:30瞬时未冻结/接口抖动)
            if g.tm_wday < 5 and 15 * 60 + 50 <= hm <= 15 * 60 + 55:
                try:
                    from . import sector_rotation, hot_rank
                    from ..db import database
                    conn = database.get_conn()
                    existing = {r[0] for r in conn.execute(
                        "SELECT source FROM daily_sector_top WHERE date=?", (date,))}
                    conn.close()
                    for src in ("kpl", "em", "ths"):
                        if src in existing:
                            continue
                        key = "sched:done:sector_%s_%s" % (src, date)
                        store.delete(key)   # 清锁(可能是15:30那次窗口内重试耗尽后留的)
                        if store.setnx(key, 1, ttl=86400):
                            n = sector_rotation.record_today_top(source=src)
                            if n:
                                log.info("板块轮动兜底补跑成功 src=%s date=%s n=%d", src, date, n)
                            else:
                                store.delete(key)
                    # 人气热榜兜底: 同理查 history 表缺失补跑
                    try:
                        hot_conn = database.get_conn()
                        hot_exist = {r[0] for r in hot_conn.execute(
                            "SELECT source FROM hot_rank_history WHERE date=?", (date,))}
                        hot_conn.close()
                        for src in ("kpl", "em", "ths"):
                            if src in hot_exist:
                                continue
                            hkey = "sched:done:hot_%s_%s" % (src, date)
                            store.delete(hkey)
                            if store.setnx(hkey, 1, ttl=86400):
                                hot_rank.save_hot_rank_history(date, source=src)
                    except Exception as he:
                        log.warning("人气热榜兜底补跑异常 err=%s", he)
                except Exception as e:
                    log.warning("板块轮动兜底补跑异常 err=%s", e)
            # 人气热榜/龙虎榜/连板梯队/竞价异动 15:30-15:35 日终快照(与板块轮动同一窗口并行)
            if g.tm_wday < 5 and 15 * 60 + 30 <= hm <= 15 * 60 + 35:
                try:
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
                    log.warning("日终快照(热榜/龙虎/连板/竞价异动)失败 err=%s", e, exc_info=True)
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
                    msg = "今日快照采集缺失时点: %s (date=%s), 相关功能(加速度/回放)会缺数据" % (",".join(missing), date)
                    log.warning(msg)
                    # 推送告警(2026-08-25): 不等用户发现, 主动通知管理员
                    try:
                        from . import notify
                        notify.send_text("[快照告警] " + msg)
                    except Exception:
                        pass
                else:
                    # 2026-09-11 P0-2: 四个时点"标记都在" ≠ 采集成功。
                    # 9/11 东财熔断日 9_25 仅落 132 行(正常 5500+), 旧逻辑照样打印"采集完整",
                    # 故障静默 —— 名单从 132 只里选、竞价强度失分, 全靠人工发现。
                    # 补行数 + 有额率双阈值; 有额率只对 9_15/9_25 生效(见常量注释)。
                    bad = []
                    for tp in TIME_POINTS:
                        n, rate = _snapshot_quality(date, tp)
                        if n < 0:          # 统计失败: 不告警, 避免误报
                            continue
                        if n < _SNAP_MIN_ROWS:
                            bad.append("%s仅%d行(<%d)" % (tp, n, _SNAP_MIN_ROWS))
                        elif tp in _SNAP_AMT_RATE_POINTS and rate < _SNAP_MIN_AMT_RATE:
                            bad.append("%s有额率%.1f%%(<%.0f%%)"
                                       % (tp, rate * 100, _SNAP_MIN_AMT_RATE * 100))
                    if bad:
                        msg = ("今日快照采集质量异常: %s (date=%s), 名单/竞价强度可能失真, "
                               "请检查东财限流或熔断日志" % ("; ".join(bad), date))
                        log.warning(msg)
                        try:
                            from . import notify
                            notify.send_text("[快照告警] " + msg)
                        except Exception:
                            pass
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
