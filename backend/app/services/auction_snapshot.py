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
import sqlite3
import threading
import time

from ..core import logger
from ..core import trade_calendar as tc
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
    # 2026-09-24 主人拍板「拿到猫爪数据再定格」: 窗口末端 9:26 → 9:27
    #   (含) —— 即 9:25:00~9:27:59。窗口只表示"允许采到几点":
    #   猫爪 daily_auc(竞价量比/额)与 fundflow_kp(竞价主力净额)**实测 09:25:35~09:26:16
    #   才产出**, 而旧首采(9:25:20~49)必然扑空 ⇒ 定格行 auc_vol_ratio 恒 0。
    #   **何时开始采**由 _BID25_FREEZE_SEC(09:26:30)决定;
    #   **何时定稿/是否回滚重采**由 meoz_bid_ready 就绪判定 + _BID25_RETRY_UNTIL 决定 ——
    #   🔴 2026-09-29 主人新口径: 截止已收到 **= 定格时刻 09:26:30** ⇒ **单枪定格、
    #      之后不再回滚改数据**(9:26:30 起 snapshot_bid 的 9_25 行即终值,
    #      与下游 9:26 系统批次/AI 预测消费的口径一致)。
    "9_25": (9 * 60 + 25, 9 * 60 + 27),
}
DEFAULT_POINT = "9_20"     # 加速度计算使用的时点

# 最后一秒高频采样窗口: (9:24:45) ~ (9:25:03), 每秒一次(ts 记实际时刻)
LASTSEC_START = 9 * 3600 + 24 * 60 + 45
LASTSEC_END = 9 * 3600 + 25 * 60 + 3

# 竞价主力净额补采(2026-09-24 实测定位): 9:25 定格采集那枪(落库实测 09:25:22~49)
# 早于上游生成(实测落地 09:25:35~09:26:16) ⇒ snapshot_bid.auc_main_net 定格行恒 0
# (全库 8 个交易日 × 4 时点复现, 与"是否漏请求字段"无关 —— 请求链完整)。
# 9:26:10 起独立轻量补采, 达标即停。
# 🔴 2026-09-29 主人新口径: 补采**不得晚于 09:26:30(= 定格时刻)** ⇒ 硬上限 09:29:50 → 09:26:30。
#    效果: 定格(09:26:30)那一轮写完快照后**同轮立即补一次**(猫爪此时已出满 09:26:16),
#    此后不再有任何补采 ⇒ 定格即终值, 不再回滚改数据(与下游 9:26 消费口径一致)。
#    ⚠️ 代价: 失去"猫爪抖动后的多轮兜底"(原 09:26:10~09:29:50 约 6 轮)。
#    ⚠️ 注意 09:26:10 那一轮在定格前是**空转**(定格行尚不存在 ⇒ UPDATE 0 行),
#       真正有效的一轮是 09:26:30(与 _BID25_FREEZE_SEC 同刻)。
# ★ 只 UPDATE 单列 —— 绝不走 snapshot_at(它会连带重触发 aipick/system_batch,
#   重复跑预测、重复写历史名单)。
# ---- 补采窗口与阈值: 由字段契约推导(2026-09-24 WP1b) ----
#   起点 : contracts.fields.auc_main_net.ready_after(09:25:35) + margin 35s = 09:26:10
#   上限 : 09:26:30(与 _BID25_FREEZE_SEC 同值 —— 定格后不再补)
#   阈值 : probe.min(0.19) × 全市场只数中位数(5209) ≈ 990 ≈ 旧 NETFILL_MIN_N(1000)
#   ★ margin=35s 与下面的轮询间隔 35s 是**两个不同含义的数字**, 当前取值只是巧合相等。
#
#   契约推导失败时**回退硬编码**(而非让 import 失败) —— 选股链路只有一条、无开关可回滚,
#   绝不能因登记表自身的小毛病导致服务起不来; 回退时打 ERROR 日志, 不静默。
_MARKET_SIZE_MEDIAN = 5209      # 全市场只数中位数(2026-09 实测), 仅用于阈值换算

# ★ 刻意**不**在热路径上设"screening 行数下限"阈值: 换源后猫爪是**主源但非唯一源**,
#   合并纪律是"只补缺" ⇒ 上游返回得少只是"补得少", 东财第二级会把缺的票补回来,
#   名单不会变瘦。反之, 加行数阈值会引入两类误判: ① 上游正常但当日标的确实少时整源被弃;
#   ② 测试夹具(小样本)被判"半残"。行数充裕度是**验收/巡检指标**(施工图验收矩阵:
#   全市场 ≥5000), 不是热路径判据。串日防护另有其法 —— 见 _screening_today 的
#   tradedate 校验(串日返回的是**完整**上一日全市场, 行数完全正常, 阈值根本挡不住)。


def netfill_interval() -> int:
    """补采轮询间隔(秒)。**显式依赖**猫爪 fundflow_kp 缓存 TTL —— 必须比它大,
    否则每轮都命中上一轮自己写的缓存, 表现为"改了但没效果"。

    2026-09-24(WP2a): 由硬编码 35 改为按 TTL 推导(30+5=35, 行为零变化),
    并把该不变量写成 tests/test_netfill_interval.py —— 注释不会报错, 断言会。
    WP2b 后补采走 fresh 直读, 本约束已非必需, 保留作双保险(上游调用次数护栏)。
    """
    try:
        from . import meoz_client          # 惰性导入: 避免模块级循环引用
        return max(5, int(meoz_client.cache_ttl("fundflow_kp")) + 5)
    except Exception as e:                                     # noqa: BLE001
        log.warning("[净额补采] TTL 推导失败, 回退硬编码间隔 35 err=%s", e)
        return 35


def _netfill_turn_key(kind, date):
    """补采「每轮一取」令牌的键(2026-09-29 P0-c2)。"""
    return "netfill:turn:%s:%s" % (kind, date)


def _netfill_turn_ttl() -> int:
    """令牌存活时长 = 轮询间隔的 0.9 倍。

    🔴 取值理由: 必须**短于**轮询间隔 —— 抢到令牌的进程若异常/卡住, 另一进程最坏只被
    连带跳过**一轮**, 下一轮必定重新竞争(取 1.0 倍以上会出现"某进程永久接不到活")。
    """
    return max(5, int(netfill_interval() * 0.9))


# 就绪判定的**判定结果**微缓存 TTL(秒)。见 meoz_bid_ready 的说明:
# 必须 **< 快照轮询间隔(10s)** —— 否则"每轮都看得到新的上游真值"这一语义被破坏。
_BIDREADY_TTL = 8


def _contract_window(field_name, margin_sec, hard_end_sec):
    """由字段契约推导补采窗口与达标阈值。

    Args:
        field_name: 契约字段名。
        margin_sec: 就绪时刻之后的缓冲(等上游落库稳定)。
        hard_end_sec: 硬停时刻(当日秒)。

    Returns:
        (起点秒, 终点秒, 达标只数); 达标只数 = probe.min × 全市场只数中位数。
    """
    from . import contracts
    c = contracts.field(field_name)
    h, m, s = (int(x) for x in c.ready_after.split(":"))
    start = h * 3600 + m * 60 + s + margin_sec
    min_n = int(round((c.probe.min if c.probe else 0.0) * _MARKET_SIZE_MEDIAN))
    return start, hard_end_sec, min_n


def _ready_sec(field_name, margin_sec, fallback_sec):
    """由字段契约的 `ready_after` 推导「上游就绪时刻 + 缓冲」的当日秒。

    契约推导失败时回退硬编码值并打 ERROR(不让登记表的小毛病拖垮采集)。

    Args:
        field_name: 契约字段名。
        margin_sec: 就绪时刻之后的缓冲秒数(等上游落库稳定)。
        fallback_sec: 推导失败时的兜底当日秒。

    Returns:
        当日秒(如 9*3600+27*60+30)。
    """
    try:
        return int(_contract_window(field_name, margin_sec, fallback_sec)[0])
    except Exception as e:                                         # noqa: BLE001
        log.error("[快照采集] %s 就绪时刻契约推导失败, 回退硬编码 %d err=%s",
                  field_name, fallback_sec, e)
        return fallback_sec


# 🔴 硬上限(2026-09-29 主人拍板): **09:26:30 = 定格时刻** —— 补采/重采一律不得晚于定格。
_NETFILL_HARD_END_SEC = 9 * 3600 + 26 * 60 + 30                 # 09:26:30(与 _BID25_FREEZE_SEC 同值)
# 竞价抢筹结果快照的"轮采(含失败重试)"上界: 同上, 不得晚于 09:26:30(原为 9:30)。
_BID_QC_UNTIL_SEC = 9 * 3600 + 26 * 60 + 30                     # 09:26:30
try:
    NETFILL_START_SEC, NETFILL_END_SEC, NETFILL_MIN_N = _contract_window(
        "auc_main_net", margin_sec=35, hard_end_sec=_NETFILL_HARD_END_SEC)
except Exception as e:                                         # noqa: BLE001
    log.error("[净额补采] 补采窗口契约推导失败, 回退硬编码(不影响采集) err=%s", e)
    NETFILL_START_SEC = 9 * 3600 + 26 * 60 + 10     # 09:26:10 起采(此后上游已开始产出)
    NETFILL_END_SEC = _NETFILL_HARD_END_SEC         # 09:26:30 硬停(定格时刻 ⇒ 定格后不再补)
    NETFILL_MIN_N = 1000                            # 非零只数达标即停(盘后基线 30.1%)

_fetch_lock = threading.Lock()       # 东财拉取串行化(时点快照 vs 秒级采样 双线程防并发限流)
# 调度去重已外置 CacheStore(跨进程): setnx("sched:done:date:tp", ttl=1天) 等
# 旧 _sched_lock/_sched_done/_sched_checked/_lastsec_done 移除(2026-08-16 Phase1)


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


# ---------------- 采集主源顺序(2026-09-24「去东财换猫爪」换源 WP1) ----------------
#   True  = 猫爪 screening 为**第一主源**(全市场建行), 东财 clist 降为第二级(**只补缺, 不删**);
#   False = 东财为第一主源(= 换源前的旧行为)。
# ★ 单点可回退: 改这一个常量即可切回旧顺序(不必 git revert); 完整回滚仍 `git revert`。
# ★ 只作用于 full=True(**时点快照**): full=False 是 9:24:45~9:25:03 的秒级采样, 必须每秒
#   拿一份**新鲜**全市场 —— 猫爪 screening 是 30s 缓存的整市场拉取(_AUC_SNAP_TTL), 塞进去
#   只会采到同一份数据(秒级序列退化成一条直线), 故维持东财单页, 该路径源不变。
# ⚠️ 编号消歧: 本仓凡写「换源 WPn」均指《快选-去东财换猫爪-施工图》的工作包;
#    与 v4.11.42 的 "WP1b/WP2a"(契约注册表 / 补采)是**两套独立编号**, 勿混。
_MEOZ_PRIMARY = True


def _fetch_market_map(full=False):
    """抓取全市场快照, 返回 {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv}}
    过滤异常涨幅(±30% 外, A股涨跌停上限20%/新股44%, 非交易时段字段可能异常)
    full=True : 时点快照(9:15/9:20/9:24/9:25)。主源见 _MEOZ_PRIMARY —— 猫爪 screening
                (全市场 5553~5651 只)优先建行, 东财 clist 降为第二级只补缺。
    full=False: fetch_eastmoney 单页200只×3分区(按涨幅倒序=竞价最强前600, 秒级采样用,
                9:24:45-9:25:03 共18秒窗口, 秒级采样窗口内无法全市场分页)
    并发(2026-08-19 起): 全市场 30 页 ThreadPoolExecutor 并发(实测 386ms/次, 2026-08-31),
          单页模式 3 分区并发 3×~1.5s → ~1.5s, 秒级采样 8 秒窗口可采 5-8 个点
    三源冗余(2026-08-25): 主源与第二级**全部失败**时用开盘啦竞价委买/爆量榜兜底,
          至少保存竞价异动关键股票(非全市场, 好过完全缺失)
    合并纪律(**只补缺, 绝不覆盖**): 各源竞价额等价(≤1% 内 100%)但市值系统性差 ~1.25%,
          覆盖会串数 —— "谁先谁后"只决定**两者都有值时谁的赢**, 不改变覆盖度上限,
          故换主源不会让名单变瘦(缺的列由后一级/补采补上, 见 docs/history.md v4.11.44)。
    """
    _t0 = time.time()
    with _fetch_lock:
        raw_all = {}
        em = {}
        # ── ① 主源(2026-09-24 换源 WP1): 时点快照下猫爪 screening 先建行 ──
        #   raw_all 为空 ⇒ _merge_meoz 的"只补缺"在此等价于"全量建行", 直接复用同一份
        #   字段映射(不再写第二遍 —— 同口径两处维护必漏改一处, 是换源最典型的坑)。
        if full and _MEOZ_PRIMARY:
            try:
                mz = _merge_meoz(raw_all)
                log.info("[快照采集] 主源①猫爪 screening: 选股%d只 估值%d只 竞价%d只 封单%d只 "
                         "→ 建行%d只(补量比%d)", mz["val_n"], mz["auc_n"], mz["fd_n"],
                         mz["seal_n"], len(raw_all), mz["vr"])
            except Exception as e:                              # noqa: BLE001
                log.warning("[快照采集] 主源①猫爪失败 err=%s", str(e)[:120])

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
                    em[code] = {
                        "bid_change": bc,
                        "bid_amt": scorer.get_bid_amt(s),
                        "name": str(s.get("f14") or ""),          # 名称
                        # 竞价封单额(元) = f10 买一量(手) × f5 买一价 × 100(股/手)
                        # 涨停时买一委托即封单; 非涨停时=买一委托金额(竞价强弱参考)
                        "bid_buy_amt": scorer.parse_float(s.get("f10")) * scorer.parse_float(s.get("f5")) * 100,
                        "float_mv": scorer.parse_float(s.get("f21")),             # 流通市值(元, 东财 f21)
                        "free_mv": scorer.parse_float(s.get("f117")),  # 自由流通市值(元): 只取东财 f117, 缺失留空让猫爪 free_float_mv 补(2026-09-20 修: 原用 f21 流通市值兜底, 导致 free_mv 存成流通市值, 自由流通口径失效)
                        "board": str(s.get("f103") or s.get("f100") or ""),       # 概念(f103优先, 行业f100兜底)
                        # 异动等级(东财 f630, 2026-09-18 v4.11.30): 评分 17%「异动」因子在
                        # 定格链路的唯一来源。东财**点查被封**(实测 push2 ulist 直接
                        # RemoteDisconnected) → 只能靠全市场 clist 在采集时落库
                        # (config.FIELDS 本就含 f630; 详见 database.init_db 的
                        #  snapshot_bid.warn_type 迁移注释)。
                        # 0 = 无异动(与历史 f630=0 同义, 评分落 default); 异常/缺失一律 0。
                        "warn_type": int(scorer.parse_float(s.get("f630"))),
                    }
        # ── ② 第二级(旧顺序下则是主源): 东财 clist, **只补缺** ──
        #   东财独有且必须保留的字段: f630(warn_type 异动等级) —— 该因子虽已由
        #   bid_strength 替代而"失活", 但快照列仍在用, 保持与旧行为一致;
        #   f10×f5(买一委托金额=涨停封单近似) —— 猫爪有 fd_amount 成品, 仅在猫爪缺时用。
        if em:
            st = _merge_em_rows(raw_all, em)
            log.info("[快照采集] 东财(%s): 原始%d只 → 补票%d只 补值%d项 → 合计%d只",
                     "第二级" if (full and _MEOZ_PRIMARY) else "主源",
                     len(em), st["added"], st["filled"], len(raw_all))
        # 兜底链(2026-09-19 重排): 主源与第二级**全失败**时 —— ① 猫爪(全市场5553只, 首选)
        #   ② 开盘啦竞价榜(仅活跃股, 二线)。猫爪能出全市场, 远比开盘啦的百来只强。
        if not raw_all:
            log.warning("[快照采集] 主源+第二级均失败, 兜底链启动")
            try:
                mz = _merge_meoz(raw_all)
                log.info("[快照采集] 兜底①猫爪成功: 选股%d只 估值%d只 竞价%d只 → %d只",
                         mz["val_n"], mz["auc_n"], mz["fd_n"], len(raw_all))
            except Exception as e:                              # noqa: BLE001
                log.warning("[快照采集] 兜底①猫爪失败 err=%s", str(e)[:120])
            if not raw_all:
                log.warning("[快照采集] 兜底②改用开盘啦竞价榜")
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
                log.info("[快照采集] 双源合并 主源+东财%d只 + TickPlus%d只 → 补票%d只 补值%d项 → 合计%d只",
                         n_em, len(tp_map), st["added"], st["filled"], len(raw_all))
        # 猫爪**补缺**(P0, 2026-09-19): 补齐 name/circ_mv/bid_amt/bid_change/封单额。
        #   目标 = **彻底摆脱东财**: 东财全挂时猫爪仍能产出全市场(5565只)完整快照。
        #   合并纪律同 _merge_tickplus「只补缺, 绝不覆盖」(两源口径不同, 覆盖会串数)。
        #   只在 full=True(时点全市场快照)时启用 —— 竞价额/市值是全市场级数据,
        #   秒级采样窗口(18秒)内拉两源会超时。
        #   2026-09-24 换源 WP1: _MEOZ_PRIMARY=True 时猫爪已在①作为**主源**跑过, 此处跳过
        #   (再跑一次语义等价但白费一次全市场拉取); 旧顺序下这里仍是补缺入口。
        if full and not _MEOZ_PRIMARY:
            try:
                mz = _merge_meoz(raw_all)
                log.info("[快照采集] 猫爪合并 选股%d只 估值%d只 竞价%d只 封单%d只 → 补票%d只 "
                         "补名%d 补流通市值%d 补自由流通%d 补额%d 补涨幅%d 补封单%d 补昨日封单%d "
                         "补量比%d → 合计%d只",
                         mz["val_n"], mz["auc_n"], mz["fd_n"], mz["seal_n"], mz["added"],
                         mz["name"], mz["mv"], mz["frmv"], mz["amt"], mz["chg"],
                         mz["seal"], mz["prefd"], mz["vr"], len(raw_all))
            except Exception as e:                              # noqa: BLE001
                log.warning("[快照采集] 猫爪合并异常(已忽略) err=%s", e)
        # 北交所(4/8/920)全链路排除 —— 2026-09-21 主人拍板: 系统不需要北交所数据。
        # 东财 clist 本就只采 hs/cyb/kcb 不含北交所; 但兜底链(猫爪 screening/TickPlus/
        # 开盘啦竞价榜)可能补入北交所, 此处统一过滤, 覆盖 full=True 时点快照与
        # full=False 秒级采样两条路径。
        if raw_all:
            _n0 = len(raw_all)
            raw_all = {c: v for c, v in raw_all.items() if not scorer.is_bse(c)}
            _drop = _n0 - len(raw_all)
            if _drop:
                log.info("[快照采集] 过滤北交所 %d 只(剩余 %d)", _drop, len(raw_all))
        # 2026-08-31 可观测性: 采集阶段耗时单独记录(与落库耗时分离, 定位时点失真来源)
        log.info("[快照采集] 行情拉取完成 full=%s 数量%d 耗时%.0fms", full, len(raw_all),
                 (time.time() - _t0) * 1000)
        return raw_all


def _merge_em_rows(raw_all, em):
    """把东财行并入已有结果(原地改 raw_all), 返回 {"added","filled"}。

    合并纪律: **只补缺, 绝不覆盖**(与 _merge_meoz / _merge_tickplus 同一纪律) ——
    换主源后东财的角色变成"第二级 + 补它独有的字段", 而各源口径不同(竞价额等价但
    市值系统性差 ~1.25%), 覆盖会把同一时点变成两套数。

    东财**独有**的字段(猫爪 screening 没有)在此补位:
      * `warn_type`(f630 异动等级): 判据用 falsy 而非 `is None` —— 与 `bid_change`
        同例: 0 在这里是"无异动/未知"双关值, 猫爪建行时统一写 0, 只有东财的非 0
        f630 才带信息量。用 falsy 才能维持**换源前"warn_type 由东财提供"的行为**
        (否则列会全是 0, 是"换源静默改变了落库内容"的隐形回归)。
      * `bid_buy_amt`(f10 买一量×f5 买一价 = 涨停封单近似): 猫爪有 fd_amount 成品
        且已建行时优先, 仅在缺/为 0 时用东财值。
    """
    added = filled = 0
    for code, e in (em or {}).items():
        v = raw_all.get(code)
        if v is None:
            raw_all[code] = dict(e)        # 主源没覆盖到的票 → 整行搬入(不丢票)
            added += 1
            continue
        if not (v.get("name") or "").strip() and (e.get("name") or "").strip():
            v["name"] = e["name"]
            filled += 1
        # 市值: 流通(float_mv)与自由流通(free_mv)分别补, 同 _merge_meoz 纪律
        if not (v.get("float_mv") or 0) and (e.get("float_mv") or 0):
            v["float_mv"] = e["float_mv"]
            filled += 1
        if not (v.get("free_mv") or 0) and (e.get("free_mv") or 0):
            v["free_mv"] = e["free_mv"]
            filled += 1
        if not (v.get("bid_amt") or 0) and (e.get("bid_amt") or 0):
            v["bid_amt"] = e["bid_amt"]
            filled += 1
        if not (v.get("bid_change") or 0) and (e.get("bid_change") or 0):
            v["bid_change"] = e["bid_change"]
            filled += 1
        if not (v.get("bid_buy_amt") or 0) and (e.get("bid_buy_amt") or 0):
            v["bid_buy_amt"] = e["bid_buy_amt"]
            filled += 1
        if not (v.get("board") or "").strip() and (e.get("board") or "").strip():
            v["board"] = e["board"]
            filled += 1
        if not (v.get("warn_type") or 0) and (e.get("warn_type") or 0):
            v["warn_type"] = e["warn_type"]
            filled += 1
    return {"added": added, "filled": filled}


def _screening_today(want_compact):
    """取**当日**猫爪 screening 全市场 map, 防串日。

    want_compact: 当日 YYYYMMDD(如 "20260924")。

    两级取法(2026-09-24 换源 WP1 新增):
      ① 显式 `tradedate=当日` —— openapi 语义是"查该交易日", 该日无数据即返回空。
         显式查询的**契约就是当日**, 故此处只做"字段缺失(上游没回 tradedate)放行"的
         宽松校验。
      ② 若①为空, 退回 `tradedate_offset=0`("最新交易日")再取一次, 但**严格校验**
         tradedate == 当日 —— offset 查询在目标日尚未产出时会返回**上一交易日**那份
         (与 daily_auc 同款串日陷阱, 2026-09-24 实测: 早盘 9:15 拿到昨日 9:25 的 5567 行),
         故这条路径必须逐行验明正身, 不匹配一律丢弃。
    两级都拿不到 → 返回 {}: 本枪猫爪不供水, 由第二级东财/补采兜住。**绝不返回串日数据**。

    Args:
        want_compact: 目标交易日, YYYYMMDD。

    Returns:
        {symbol: {字段: 值}}; 拿不到当日数据时为空 dict。
    """
    from . import meoz_client
    want = str(want_compact or "").replace("-", "")
    if not want:
        return {}
    m = {c: r for c, r in meoz_client.screening_map(date=want).items()
         if str((r or {}).get("tradedate") or "").replace("-", "") in ("", want)}
    if m:
        return m
    alt = {c: r for c, r in meoz_client.screening_map(date_offset=0).items()
           if str((r or {}).get("tradedate") or "").replace("-", "") == want}
    if alt:
        log.info("[快照采集] 猫爪 screening tradedate=%s 显式查询无行, "
                 "经 offset=0 并按 tradedate 严格校验后取到 %d 只", want, len(alt))
    return alt


def _merge_meoz(raw_all):
    """把猫爪(实时选股 + 竞价额 + 封单额)并入快照结果(原地改 raw_all), 返回统计字典。

    ★ 两个角色, 同一份映射(2026-09-24 换源 WP1 起):
      · **主源建行**: `_fetch_market_map` 在 full=True 且 `_MEOZ_PRIMARY` 时先调用本函数,
        此时 raw_all 为空 ⇒ "只补缺"等价于"全量建行";
      · **补缺**: 旧顺序下在末尾调用, 只补东财缺的字段。
      两种角色共用同一份字段映射 —— 换主源若另写一遍映射, 必然出现"同口径两处维护、改一处漏一处"。

    2026-09-20 重构: 主源改为 **screening(实时选股)** —— 一个接口全市场 5553 只,
    同时给出 free_float_mv/circ_mv/name/auc_amt/auc_pct_chg, 免去四接口拼装。
    ★★ 2026-09-26 修订: **float_mv 的主源改为 valuation.circ_mv**(主人拍板统一训练基座口径),
      screening.circ_mv 降为兜底; 其余字段主源不变(仍是 screening)。
    字段映射(实测核实):
      screening.name          → name                     [5553只]
      valuation.circ_mv       → float_mv(流通市值, 元)    ★2026-09-26 起**主源**
      screening.circ_mv       → float_mv 兜底(valuation 当日缺时才用)
      screening.free_float_mv → free_mv(自由流通市值, 元)  [5553只] ★ 全市场唯一来源
      screening.auc_amt       → bid_amt(竞价额, 万元)     [5444只, 元 → 需 /1e4]
      screening.auc_pct_chg   → bid_change(竞价涨幅 %)     [4818只]
      screening.fa_0925l / fd_amount → bid_buy_amt(封单额, 元)
      screening.theme_names_kpl      → board(题材兜底)
      screening.pre_fd_amount        → pre_fd_amount(昨日封单额, 元)      ★ 2026-09-20 新增
      screening.fd_to_yesterday      → fd_to_yesterday(封昨比, 官方成品)  ★ 2026-09-20 新增

    ⑤ 主力资金(2026-09-20 新增, 独立于上面四源): fundflow_kp 全市场批量
      fundflow_kp.auction_main_net_amount → auc_main_net(竞价主力净额, 元, 9:25 起更新)
      ★ 供评分 17% 异动分新因子(主人拍板: 删加速度修正+低开 gate, 换主力净额)。
      ★ 覆盖实测(2026-09-18): 非零仅 32% —— 有大单才有值, 0=无信号(评分走 default)。
      ★ 单位: 元, 不换算(与封单额同例; bid_amt 才需要 /1e4)。

    后备(主源字段偶缺时):
      screening.circ_mv / daily_auc.auc_amt / daily_auc.auc_pct_chg
      auc_kp.free_float_mv(138只) / daily_auc_fd.fa_0925(涨停封单)

    🔴 单位: 猫爪 auc_amt 是**元**; 本表 bid_amt 存**万元**(见 BID_AMT_MAX_WAN)。
       必须 /1e4, 否则落库放大 1e4 倍(历史上开盘啦兜底就踩过这个坑)。
    🔴 float_mv 与 free_mv 是**两个不同口径**(流通 vs 自由流通), 分别落列不可混。
       ★ 2026-09-20 主人拍板「所有流通市值改自由流通市值」→ 门槛/评分统一取
         free_mv 优先(QuoteRow.mv); float_mv 仅为 free_mv 缺失时的兜底 + 展示列。
    ★ bid_buy_amt(封单额) 由 daily_auc_fd.fa_0925 提供(口径对拍 0.986~1.0000),
      非涨停股无封单(置 0, 与旧东财行为一致)。
    ★ pre_fd_*(昨日封单三字段) 仅 screening 提供 —— 官方原生值, 不换算(单位元/次)。
      东财链路完全无此三字段, 故只在猫爪侧填, 不存在"覆盖东财正确值"的风险。
    🔴 无 warn_type: f630 已失活(bid_strength 替代) → 保持 0。
    """
    from . import meoz_client

    stats = {"val_n": 0, "auc_n": 0, "fd_n": 0, "seal_n": 0, "added": 0, "name": 0,
             "mv": 0, "frmv": 0, "amt": 0, "chg": 0, "seal": 0, "prefd": 0, "ff": 0, "vr": 0}
    if not meoz_client.enabled():
        return stats

    # ⓪ 主源: 实时选股 screening(全市场 5553 只, 含 free_float_mv)
    #   ★ 2026-09-24 换源 WP1: 显式传**当日**(防串日)。
    #     原先用 date_offset=0("最新交易日") —— 当日行尚未产出时它会返回**上一交易日**
    #     那份, 于是 9:15/9:20/9:24 三枪会把昨日全市场当成今日写进定格(与 daily_auc
    #     同类的串日陷阱, 2026-09-24 已对 daily_auc 修过, 此处一并收口)。
    #     显式 tradedate 查询的语义是"该日无数据就返回空", 天然防串日。
    #   ★ 行数健全性守卫: 即便上游语义变化, 宁可"本枪不补"也不串日 —— 快照缺值可由
    #     第二级东财/补采回填, 而串日残值一旦落库就再也分不出真假(历史教训)。
    _today = _bj_date()
    _today_compact_sc = _today.replace("-", "")
    try:
        sc_map = _screening_today(_today_compact_sc)
        stats["val_n"] = len(sc_map)
    except Exception as e:                                      # noqa: BLE001
        sc_map = {}
        log.warning("[快照采集] 猫爪 screening 读取失败 err=%s", str(e)[:120])

    # ① 估值: valuation(全市场市值 + 名称)。
    #   ★★ 2026-09-26 主人拍板「circ_mv 统一到**训练基座口径**」→ float_mv 的
    #     **主源改为 valuation.circ_mv**(原为 screening.circ_mv, valuation 仅兜底)。
    #     依据: 训练基座 build_trainset_v2.py 的 VAL_KEEP=["circ_mv"] 读的正是
    #     meoz-data/valuation-*.ndjson.gz 的 valuation.circ_mv ⇒ 线上必须同源,
    #     否则"训练 6 维/推理 6 维自洽"之下仍存在**同名两义** skew(原说明书 E-8)。
    #     实测两源差异(09-24 全市场): 相对差中位 1.4% / P95 5.3% / max 56.6%,
    #     30~100 亿门槛边界翻转 71 只 —— 足以改变当天名单, 故必须收口。
    #   ★ 同时把 valuation 改为**显式当日**(date=_today), 与 ⓪ 步 screening 同纪律:
    #     原先 date_offset=0("最新交易日") 在当日行未产出时会返回**上一交易日**那份,
    #     一旦 valuation 由"兜底"升为"主源", 这个串日陷阱就会直接污染全市场 float_mv。
    #     date= 查询语义是"该日无数据即返回空" ⇒ 天然防串日, 空则自动回退 screening。
    try:
        val_map = meoz_client.valuation_map(date=_today)
        stats["auc_n"] = len(val_map)
    except Exception as e:                                      # noqa: BLE001
        val_map = {}
        log.warning("[快照采集] 猫爪 valuation 读取失败 err=%s", str(e)[:120])

    # ② 后备竞价: daily_auc 0925(金额 + 涨幅 + 名称), screening 缺竞价字段时兜底。
    #   ★ 防串日(2026-09-24 实测)在这一步就做掉 —— 不传 date 时, 目标日 9:25 尚未产出,
    #     上游会返回**最近可用**那份(早盘 9:15/9:20/9:24 三枪整批是上一交易日, 实测 5567 行)。
    #     历史教训: 旧代码把原始行数直接当 "竞价%d只" 打进日志 → 日志显示"竞价5567只"看着
    #     一切正常, 实际写进去的全是昨日值, 排查时被误导数轮。故此处按目标日过滤后再计数。
    _today_compact = _bj_date().replace("-", "")
    try:
        _raw_auc = meoz_client.daily_auc_amt("0925", date_offset=0)
        auc_map = {c: r for c, r in _raw_auc.items()
                   if str((r or {}).get("tradedate") or "").replace("-", "")
                   in ("", _today_compact)}
        _cross_n = len(_raw_auc) - len(auc_map)
        if _cross_n:
            log.warning("[快照采集] 猫爪 daily_auc 剔除串日残值 %d/%d 只(tradedate≠%s, 该源本枪弃用)",
                        _cross_n, len(_raw_auc), _today_compact)
        stats["fd_n"] = len(auc_map)                 # 可用行数(已剔串日), 不是上游原始行数
    except Exception as e:                                      # noqa: BLE001
        auc_map = {}
        log.warning("[快照采集] 猫爪 daily_auc 读取失败 err=%s", str(e)[:120])

    # ③ 封单: 9:25 涨停封单额(daily_auc_fd.fa_0925, 涨停/一字竞价池)
    try:
        fd_map = meoz_client.auc_fd_map(date_offset=0)
        stats["seal_n"] = sum(1 for r in fd_map.values() if r.get("fa_0925") is not None)
    except Exception as e:                                      # noqa: BLE001
        fd_map = {}
        log.warning("[快照采集] 猫爪 daily_auc_fd 读取失败 err=%s", str(e)[:120])

    if not sc_map and not val_map and not auc_map and not fd_map:
        return stats

    # 全量 code 集合 = 东财 ∪ 猫爪四源(猫爪可能带来东财没有的票)
    # 北交所(4/8/920)全链路排除 —— 2026-09-21 主人拍板: 系统不需要北交所数据。
    # 猫爪 screening 是全市场(含北交所 344 只), 若不在此过滤会把北交所补进快照。
    codes = {c for c in set(raw_all) | set(sc_map) | set(val_map) | set(auc_map)
             if not scorer.is_bse(c)}

    # ⑤ 主力资金: fundflow_kp 全市场批量(竞价主力净额 auction_main_net_amount)。
    #   独立 try: 挂了不影响四源与主链路(独立降级), 调用方按"无信号"处理。
    ff_map = {}
    try:
        ff_map = meoz_client.fundflow_map(sorted(codes))
        stats["ff"] = sum(1 for r in ff_map.values()
                          if r.get("auction_main_net_amount") not in (None, 0, "", "-"))
        log.info("[快照采集] 猫爪 fundflow_kp: %d只(竞价净额非零%d)", len(ff_map), stats["ff"])
    except Exception as e:                                      # noqa: BLE001
        log.warning("[快照采集] 猫爪 fundflow_kp 读取失败 err=%s", str(e)[:120])

    for code in codes:
        code = str(code)
        v = raw_all.get(code)
        s = sc_map.get(code) or {}
        vm = val_map.get(code) or {}
        am = auc_map.get(code) or {}
        fd = fd_map.get(code) or {}
        ff = ff_map.get(code) or {}
        # ★ 防串日已在 ② 读入处按 tradedate 整批过滤(见那段注释), 此处 am 必为目标日数据。
        # 竞价主力净额(元, 9:25 起更新): 无值/0 → 0(=无信号, 评分走 default)
        auc_main_net = _f(ff.get("auction_main_net_amount")) or 0.0

        # 封单额: screening.fd_amount/fa_0925l 优先, daily_auc_fd.fa_0925 兜底(均元)
        seal = _f(s.get("fd_amount")) or _f(s.get("fa_0925l")) or _f(fd.get("fa_0925"))
        # 竞价涨幅: screening 优先, daily_auc 兜底
        chg = _f(s.get("auc_pct_chg"))
        if chg is None:
            chg = _f(am.get("auc_pct_chg"))
        # 竞价额(元): screening 优先, daily_auc 兜底
        amt = _f(s.get("auc_amt"))
        if amt is None:
            amt = _f(am.get("auc_amt"))
        # 竞价量比(标准口径, 2026-09-24 新增): 竞价成交量 ÷ 近 5 日平均每分钟成交量。
        #   🔴 唯一来源 = daily_auc.auc_vol_ratio(am) —— 不用 screening:
        #      screening 的 openapi 字段清单里没有 auc_vol_ratio(实测能返回但官方未收录),
        #      放进其字段串是「零收益、主源 422 全挂」的风险(见 meoz_client._SCREENING_FIELDS)。
        #   无值 → 0.0, 落库为 0 = **不可用**(评分层按缺值走 default, 不当作"量比=0")。
        vol_ratio = _f(am.get("auc_vol_ratio")) or 0.0

        # 昨日封单额 + 封昨比(仅 screening 提供, 官方原生; 无则 0/None)
        pre_fd = _f(s.get("pre_fd_amount"))
        fd_yday = _f(s.get("fd_to_yesterday"))

        if v is None:
            # 东财没这只票 → 新建行(至少要能进评分: 有涨幅)
            if chg is None:
                continue                    # 没涨幅的票进不了评分, 不新增(同 TickPlus 纪律)
            v = raw_all[code] = {
                "bid_change": chg,
                "bid_amt": (amt or 0.0) / 1e4,
                "name": str(s.get("name") or am.get("name") or vm.get("name") or fd.get("name") or ""),
                "bid_buy_amt": seal if seal is not None else 0,
                "float_mv": _f(vm.get("circ_mv")) or _f(s.get("circ_mv")) or 0.0,   # 流通市值(元) ★valuation优先(统一训练基座口径, 2026-09-26)
                "free_mv": _f(s.get("free_float_mv")) or 0.0,                      # 自由流通市值(元)
                "pre_fd_amount": pre_fd if pre_fd is not None else 0.0,            # 昨日封单额(元)
                "fd_to_yesterday": fd_yday if fd_yday is not None else 0.0,        # 封昨比
                "auc_turnover": _f(s.get("auc_turnover")) or 0.0,                 # 真实竞价换手率%(自由流通口径)
                "auc_vol_ratio": vol_ratio,                                        # 竞价量比(标准口径: 竞价量÷近5日每分钟量)
                "auc_main_net": auc_main_net,                                      # 竞价主力净额(元)
                "board": str(s.get("theme_names_kpl") or fd.get("theme_names_kpl") or ""),
                "warn_type": 0,             # f630 已失活
                "_src": "meoz",
            }
            stats["added"] += 1
            if pre_fd:
                stats["prefd"] += 1
            if vol_ratio:                 # 量比非零计次(与"已有行补缺"路径同口径, 便于日志观测)
                stats["vr"] += 1
            continue
        # ---- 已有行: 只补缺, 绝不覆盖 ----
        if not (v.get("name") or "").strip():
            nm = str(s.get("name") or am.get("name") or vm.get("name") or fd.get("name") or "")
            if nm:
                v["name"] = nm
                stats["name"] += 1
        # 市值: 流通市值(float_mv)与自由流通(free_mv)分别补
        if not (v.get("float_mv") or 0):
            mv = _f(vm.get("circ_mv")) or _f(s.get("circ_mv"))   # ★valuation优先(统一训练基座口径, 2026-09-26)
            if mv and mv > 0:
                v["float_mv"] = mv
                stats["mv"] += 1
        if not (v.get("free_mv") or 0):
            fmv = _f(s.get("free_float_mv"))
            if fmv and fmv > 0:
                v["free_mv"] = fmv
                stats["frmv"] += 1
        if not (v.get("bid_amt") or 0):
            if amt and amt > 0:
                v["bid_amt"] = amt / 1e4
                stats["amt"] += 1
        if not (v.get("bid_change") or 0):
            if chg is not None:
                v["bid_change"] = chg
                stats["chg"] += 1
        # 封单额: 东财缺(0)且猫爪有 → 补(涨停票才有)
        if not (v.get("bid_buy_amt") or 0) and seal:
            v["bid_buy_amt"] = seal
            stats["seal"] += 1
        # 概念: 东财空且猫爪题材有 → 补(仅兜底, 不覆盖东财 f103)
        if not (v.get("board") or "").strip():
            bd = str(s.get("theme_names_kpl") or fd.get("theme_names_kpl") or "")
            if bd:
                v["board"] = bd
        # 昨日封单三字段: 仅猫爪 screening 有(东财无此字段) → 缺则补, 只补不覆盖。
        #   补缺判据统一用 `is None`(键不存在) —— 不用 falsy, 因为 0 是有效值
        #   (0 = 昨日无封单/未炸板), 与新建行无条件落值保持**同一行为**。
        if v.get("pre_fd_amount") is None and pre_fd is not None:
            v["pre_fd_amount"] = pre_fd
            if pre_fd:                       # 只对有意义的(非0)封单计次, 便于日志观测
                stats["prefd"] += 1
        if v.get("fd_to_yesterday") is None and fd_yday is not None:
            v["fd_to_yesterday"] = fd_yday
        # 真实竞价换手率: 猫爪 screening 官方成品(自由流通口径) → 缺则补, 只补不覆盖
        if not (v.get("auc_turnover") or 0):
            at = _f(s.get("auc_turnover"))
            if at and at > 0:
                v["auc_turnover"] = at
        # 竞价量比(标准口径 5 日每分钟量) → 缺则补, 只补不覆盖(同 auc_turnover 纪律)
        if not (v.get("auc_vol_ratio") or 0) and vol_ratio:
            v["auc_vol_ratio"] = vol_ratio
            stats["vr"] += 1
        # 竞价主力净额: 仅 fundflow_kp 有(东财/其余猫爪源均无) → 东财行无此键, 直接写。
        #   已有值(理论上不存在, 该键只由本函数写)则不覆盖 —— 同「只补缺」纪律。
        if not (v.get("auc_main_net") or 0) and auc_main_net:
            v["auc_main_net"] = auc_main_net
    return stats


def _f(x):
    """安全转 float(None/异常 → None)。"""
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


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
                "warn_type": 0,       # TickPlus 无 f630 → 0(未知按无异动处理, 与东财缺省一致)
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
    返回 {code: {bid_change, bid_amt, name, bid_buy_amt, float_mv, free_mv, board}}
    非全市场(仅竞价活跃股), 但保证竞价异动页有数据可显示

    🔴 2026-09-18 口径修正(v4.11.28): 开盘啦 `floatMv` 是**实际流通**(≈自由流通市值),
       不是东财 f21 的**流通市值**。此前直接写进 float_mv, 导致 49 亿流通的票被落库成
       14.75 亿 → floatMvFloor=30 把真大盘股**系统性误剔**(9/17 实测 56 只, 其中 3 只
       其它门槛全过)。行级签名 = float_mv>0 且 free_mv=0, 起始日 9/11。
       现在: 开盘啦值写 **free_mv**(语义正确的列), float_mv 留 0 → 由 mv_cache.fill
       用东财 f21 / 腾讯 f44 补真流通市值(补不到则保持未知, 粗筛不误杀, 见 filter)。
    """
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
            # 2026-09-18: 恒 0 由 mv_cache.fill 补齐; 把开盘啦值塞进 float_mv 是**更坏的错**
            #   (拿到自由流通量级的错值, 远比"未知"危险 —— 未知不会误剔真大盘股)。
            fallback[code] = {
                "bid_change": s.get("bidChange") or 0,
                "bid_amt": (s.get("bidAmt") or 0) / 1e4,
                "name": s.get("name") or "",
                "bid_buy_amt": s.get("bidSealAmt") or 0,
                "float_mv": 0,                          # 留给 mv_cache.fill 补真流通市值
                "free_mv": s.get("floatMv") or 0,       # 开盘啦口径 = 实际流通(≈自由流通)
                "board": s.get("board") or "",
                "warn_type": 0,                         # 开盘啦无 f630 异动等级 → 0(未知=无异动)
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
                "float_mv": 0,                          # 同委买榜: 留给 mv_cache.fill 补
                "free_mv": s.get("floatMv") or 0,       # 开盘啦口径 = 实际流通(≈自由流通)
                "board": s.get("board") or "",
                "warn_type": 0,                         # 同上: 兜底源无 f630
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
# 2026-09-24 主人拍板「拿到猫爪数据再定格」: 20 → 45 秒。
#   依据: 猫爪竞价字段(daily_auc/fundflow_kp)实测 09:25:35 起才产出; 原 9:25:20 首采
#   (全市场一轮约 25s)必然扑空, 白烧一轮拉取。改 45s 后首采落在 9:25:45+, 把轮次让给就绪重采。
# ★ 2026-09-24 主人**二次拍板**(晚于上文, 冲突时以本条为准): 定格那一枪**固定在 09:26:30**
#   —— 45 → 90 秒。9:25:00~9:26:29 全程**静默不采**: 该段猫爪竞价字段大概率仍在产出中
#   (实测 09:25:35~09:26:16), 采了必扑空, 只会白烧一轮 8~15s 的全市场拉取, 还把
#   `9_25` 完成标记反复 set/delete。09:26:30 打首采枪; 若此刻仍未就绪, 由下方
#   `_bid25_retry_open` 判断是否回滚重采 —— 🔴 2026-09-29 主人新口径:
#   **截止已收到 = 定格时刻 09:26:30** ⇒ 定格那一枪即终值, **不再回滚重采**
#   (9:26:30 之后 snapshot_bid 的 9_25 行不再变化, 与下游 9:26 系统批次/AI 预测一致)。
_BID25_MIN_SEC = 90             # 9:25 后至少 90 秒才采(原 45 秒) ⇒ 定格首采 = 09:26:30
# 定格首采时刻(**当日绝对秒**, 由 _BID25_MIN_SEC 唯一推导 —— 同一个事实只有一个真相源,
#   禁止在别处再写一份字面量)。与 _BID25_RETRY_UNTIL / _bid25_retry_open 同口径:
#   三者都含 9*3600, 比较时两侧**都不得**再减 9*3600。
_BID25_FREEZE_SEC = 9 * 3600 + 25 * 60 + _BID25_MIN_SEC      # 09:26:30 = 33990
_SAME_PREV_MAX = 0.50           # 9_25 与 9_24 逐票竞价额"相等"占比上限, 超此值判定"定格值未发布"

# ---- 猫爪竞价字段就绪阈值(2026-09-24, 与「推迟定格」配套) ----
# auc_vol_ratio 实测非零率 98.3%(2026-09-24 猫爪 daily_auc 5474/5567), 下限取 90%:
#   既容得下猫爪偶缺, 又能把"整批 0"(未产出)与"串日残值"稳稳判成"未就绪"。
_VR_READY_MIN = 0.90
_VR_READY_MIN_N = int(round(_VR_READY_MIN * _MARKET_SIZE_MEDIAN))   # ≈ 4688

# 定格重采截止(**当日绝对秒**, 含 9*3600): 由 auc_vol_ratio 契约 ready_after(09:25:35)
#   + 55s 缓冲 = **09:26:30(= _BID25_FREEZE_SEC)**。
#   沿革: 2026-09-24 主人拍板推迟定格(原 09:26:00 早于猫爪产出上限 09:26:16, 等于没等),
#         当时截止取 09:27:30(留 10s 轮询重采); 2026-09-29 主人再收紧到 **09:26:30**
#         ⇒ **单枪定格**: 即便未就绪也接受当前值, 不再回滚
#         (宁可缺量比, 也不在定格之后再改数据 —— 与"截止后不再回滚"同一原则的更强版本)。
#   ★ 不变式①: 必须 ≤ 窗口末端(TIME_POINTS['9_25'][1]=567 分钟 → 9:27:59), 否则重采分支不会被触发;
#     不变式②: 必须 > 上游就绪时刻(09:25:35), 否则"等了个寂寞"。
_BID25_RETRY_UNTIL = _ready_sec("auc_vol_ratio", 55, 9 * 3600 + 26 * 60 + 30)


def _bid25_retry_open(hm: int, sec: int) -> bool:
    """9:25 定格是否仍处于「可回滚重采」的时间窗内(未到 _BID25_RETRY_UNTIL)。

    抽成纯函数的唯一理由: **单位口径必须能被断言钉死**。此处历史上是内联式
    `hm * 60 + g.tm_sec < _BID25_RETRY_UNTIL - 9 * 3600` —— 左边是当日绝对秒
    (9:25:45 → 33945), 右边减成了「9 点后秒数」(1650), 于是条件**恒 False**,
    定格重采保险丝静默失效(2026-09-19 ~ 2026-09-24), 直到排查 auc_vol_ratio
    恒 0 才暴露。注释不会报错, 断言会 —— 见 tests/test_bid25_defer.py。

    Args:
        hm: 当日**分钟**数(hour * 60 + min), 与 TIME_POINTS / 调度器的 hm 同口径。
        sec: 当前秒(0~59)。
    Returns:
        True  = 未到截止 → 调用方删除完成标记, 窗口内下一轮重采;
        False = 已过截止 → 接受当前值(宁可缺量比, 不可整点缺失)。
    """
    # 🔴 _BID25_RETRY_UNTIL 是**当日绝对秒**(含 9*3600), 故此处**不得**再减 9*3600。
    return hm * 60 + sec < _BID25_RETRY_UNTIL


def _bid25_before_freeze(hm: int, sec: int) -> bool:
    """当前时刻是否**尚未**到 9_25 定格首采时刻(`_BID25_FREEZE_SEC` = 09:26:30)。

    语义(2026-09-24 拍板 + 2026-09-29 收紧): `9:25:00 ~ 9:26:29` 是「等猫爪竞价字段产出」
    的**静默段** —— 此段内一律不采(把全市场拉取轮次让给定格那一枪); 09:26:30 起打首采枪,
    🔴 且**这一枪即终值**: `_BID25_RETRY_UNTIL` 已 = 09:26:30 ⇒ 未就绪也不再回滚重采。

    ★ 为什么**必须**抽成纯函数(与 `_bid25_retry_open` 同款理由): 单位口径要能被断言钉死。
      本函数与 `_BID25_FREEZE_SEC` 同为**当日绝对秒**(`hm*60+sec`)口径, 任一侧额外
      加减 `9*3600` 都会让条件恒真或恒假 —— 这类错误**不会报错, 只会静默失效**
      (2026-09-19~24 的 `_BID25_RETRY_UNTIL` 单位事故就是同一形态, 静默失效近一周)。
      注释不会报错, 断言会 —— 见 tests/test_bid25_defer.py。

    ★ 为什么**不能**沿用历史的 `hm == 9*60+25 and sec < _BID25_MIN_SEC` 写法:
      `_BID25_MIN_SEC` 已由 45 涨到 90(> 59)。该写法在 9:25 整分钟恒真(全跳过, 符合意图),
      但 **9:26:00 就会进采集分支** —— 恰好在定格时刻之前 30 秒打一枪必然扑空的采集,
      正是本次要消除的行为。整点分钟判定无法表达「9:26:30」这个跨分钟时刻。

    Args:
        hm: 当日**分钟**数(hour * 60 + min), 与 `TIME_POINTS` / 调度器的 hm 同口径。
        sec: 当前秒(0~59)。
    Returns:
        True  = 未到定格首采时刻 → 调用方 `continue`(不采、不置完成标记、不触发副作用);
        False = 已到 → 正常进入「首采 / 就绪重采」分支。
    """
    # 🔴 两侧同为当日绝对秒: _BID25_FREEZE_SEC 含 9*3600, 此处**不得**再减/加 9*3600。
    return hm * 60 + sec < _BID25_FREEZE_SEC

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


def _is_trade_day(g):
    """是否交易日 = 周一~周五 **且非法定休市日**。g 为北京时间 struct_time。

    ★ 2026-09-25（中秋节 · 星期五）复盘新增：
      此前各处只用裸 `g.tm_wday < 5`，没有节假日日历 ⇒ 法定假日里
      行情源返回的**上一交易日复制行**被写进 `snapshot_bid`
      （`bid_change`/`bid_amt` 逐位相同，而猫爪补的 `price`/`bid_turnover` 全 0），
      既污染快选 App 展示、又喂给 AI 选股产出 30 只假名单。
      日历实现见 `app/core/trade_calendar.py`（上交所官方休市表，区间外 fail-open）。
    """
    return tc.is_trade_day_of(g)


def _brief_date_ok(brief, date) -> bool:
    """两市概况收盘快照的**写前守卫**: brief 的日期必须**就是今日**(`date`)。

    ★ 2026-09-26 (v4.11.57) 新增。为什么必要:
      该快照只在交易日 15:30-15:35 落库, 而**行情源在"收盘定格尚未生成"时会返回
      上一交易日的复制行** —— 2026-09-25(中秋 · 星期五)正是这类残值被写进
      `snapshot_bid`(见 `_is_trade_day` 的复盘), 同类残值也污染过 settings。
      若不校验直接落库, `market_brief_last` 会被写成**非今日**的日期; 而读侧
      (`kpl._mb_baseline_is_today`)靠"last.date 是否等于今天"决定「较昨日全天」基准
      ⇒ 拿到这种值后**永不相等**, 基准长期错位且**不会自愈**。

    纪律与 v4.11.53「三源收盘确认」一致: **宁可不写, 也不写错日期的数据**。
    返回 False 时调用方应**删掉当日 setnx 标记**以在 15:30-15:35 窗口内重试。
    """
    return bool(brief) and str(brief.get("date") or "") == str(date)


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
        if not _is_trade_day(g):
            log.warning("[快照采集] 拒绝非交易日写入 tp=%s date=%s(周%d%s) 防御性跳过",
                        time_point, date, g.tm_wday,
                        ", 法定休市" if tc.is_holiday(date) else "")
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
            "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, bid_amt, name, bid_buy_amt, float_mv, free_mv, board, warn_type, pre_fd_amount, fd_to_yesterday, auc_turnover, auc_vol_ratio, auc_main_net, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(date, time_point, code, v["bid_change"], v["bid_amt"], v.get("name", ""),
              v.get("bid_buy_amt", 0), v.get("float_mv", 0), v.get("free_mv", 0), v.get("board", ""),
              int(v.get("warn_type") or 0),
              v.get("pre_fd_amount", 0), v.get("fd_to_yesterday", 0),
              v.get("auc_turnover", 0),
              v.get("auc_vol_ratio", 0),
              v.get("auc_main_net", 0),
              int(time.time()))
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


def meoz_bid_ready(date):
    """猫爪竞价字段是否**已就绪** —— 「拿到猫爪数据再定格」的判据(2026-09-24 主人拍板)。

    背景
    ----
    9:25 定格枪原打在 09:25:20~49, 早于猫爪竞价字段产出(实测 09:25:35~09:26:16) ⇒ 定格行
    `auc_vol_ratio` 恒 0(2026-09-18~24 全库 4 时点复现), 因子的量比层于是静默回退旧口径。
    今日(09-24)更极端: 09:25:48 恰逢猫爪 8 秒抖动, `daily_auc` 直接给了 0 行。

    判据(两条**都必须**满足)
    ----------------------
      ① 取到的正是**目标交易日**的数据 —— 防串日: 实测 09:15/9:20/9:24 调
         `daily_auc(date_offset=0, trademin=0925)` 会返回**上一交易日**的 9:25 数据(5567 行),
         而当日 9:25 竞价尚未发生。缺这条, 早盘任何时刻都会判"就绪"并把昨日值写进今日定格。
      ② 该日 `auc_vol_ratio` 非零只数 ≥ `_VR_READY_MIN_N`(实测 98.3%, 下限取 90%)。

    只读: **不改任何业务状态**(只写一个 8s 的"判定结果"微缓存, 见下)。异常/未启用一律放行
    (True) —— 猫爪没启用就不存在"等猫爪", 不能因为探测失败把定格永久卡死; 真正的保护是
    候选值本身(未就绪就不 DONE)。

    2026-09-29 (P0-c1「收窄 fresh」): 探测本身是全市场 fresh `daily_auc`(无缓存, 5567 行),
    而它每轮快照(10s)被调一次、2 个 worker 各一次 ⇒ 一个定格窗口能打出 ~12 次全市场探测。
    治法 = **只把"判定结果"缓存 8 秒**(`_BIDREADY_TTL` < 10s 轮询间隔 ⇒ 下一轮必然重新探测,
    「每轮都看得到新的上游真值」语义不变), 同轮两个 worker 于是共享一次探测。
    刻意**不**给 `daily_auc_amt` 加 TTL —— 那会把主链共用的 `meoz:daily_auc:*` 键写成短命
    条目, 连累抢筹/竞价一进二(与 WP2b「不污染主链缓存」同一纪律)。

    Args:
        date: 目标交易日(YYYY-MM-DD 或 YYYYMMDD)。

    Returns:
        True 表示猫爪竞价字段已就绪、定格可以定稿。
    """
    from . import meoz_client
    try:
        if not meoz_client.enabled():
            return True
        want = str(date or _bj_date()).replace("-", "")
        ck = "bidready:" + want
        hit = store.get(ck)
        if hit in (0, 1):                      # 同轮内另一 worker 刚探过(8s 内) → 复用判定
            return bool(hit)
        am = meoz_client.daily_auc_amt("0925", date=want, fresh=True)
        if not am:
            store.set(ck, 0, ttl=_BIDREADY_TTL)
            return False
        n_same_day = nz = 0
        for r in am.values():
            td = str((r or {}).get("tradedate") or "").replace("-", "")
            if td and td != want:
                continue                       # 串日残值 → 不计入
            n_same_day += 1
            if (r or {}).get("auc_vol_ratio") or 0:
                nz += 1
        if n_same_day == 0:
            store.set(ck, 0, ttl=_BIDREADY_TTL)   # 一行都不是目标日 → 未就绪(防串日核心)
            return False
        verdict = nz >= _VR_READY_MIN_N
        # 只缓存**判定结果**(不是行情): 8s 后必然重新探测 ⇒ 不牺牲"看得见上游真值"。
        store.set(ck, 1 if verdict else 0, ttl=_BIDREADY_TTL)
        return verdict
    except Exception as e:                                     # noqa: BLE001
        log.warning("[快照采集] 猫爪就绪判定失败(视为未就绪) date=%s err=%s", date, str(e)[:120])
        return False


def _netfill_due(nf_sec, wday, last_ts, now_ts, done, date=None):
    """竞价净额补采是否该触发(纯函数, 便于单测时间边界)。

    nf_sec: 北京当日秒(= hm*60 + 秒); wday: 0=周一; last_ts: 上次补采时刻;
    now_ts: 当前时刻; done: 达标完成标记(置位后不再轮询)。
    date:   北京日期 "YYYY-MM-DD"(**可选**)。传入后额外判**法定休市日**
            (2026-09-25 中秋节复盘新增); 不传则只判周末, 与旧调用点/单测保持兼容。
    """
    if wday >= 5:                                   # 非交易日(周末)
        return False
    if date is not None and tc.is_holiday(date):    # 非交易日(法定休市)
        return False
    if not (NETFILL_START_SEC <= nf_sec <= NETFILL_END_SEC):
        return False
    if done:
        return False
    return now_ts - last_ts >= netfill_interval()


def refill_bid_main_net(date, point="9_25"):
    """竞价主力净额补采: 只回填 snapshot_bid.auc_main_net 一列, 不重跑整表快照。

    背景(2026-09-24 实测定位): 定格采集那枪落库于 09:25:22~49, 而上游该字段
    09:25:35~09:26:16 才生成 ⇒ 定格行 auc_main_net 恒 0(全库 8 日 × 4 时点复现)。
    上游一旦发布即为**竞价定格终值**(不随后续盘中变化), 故补采到即可回填。

    ★ 与 snapshot_at 的分工: 本函数**只做列回填** —— 不触发 aipick / system_batch,
      也不覆盖已有非零值(WHERE auc_main_net=0) ⇒ 幂等, 可安全重复调用。
    返回 (上游非零只数, 实际回填行数, 定格行总数)。
    """
    from . import meoz_client

    if not meoz_client.enabled():
        return (0, 0, 0)

    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT code FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, point)).fetchall()
    finally:
        conn.close()
    codes = [str(r[0]) for r in rows]
    if not codes:
        return (0, 0, 0)

    # 走 fundflow_map(内部 _FUNDFLOW_BATCH=2000 分片): 全市场单轮 ≈ 3 次上游调用。
    # fresh=True(WP2b): 补采**直打上游**, 既不读也不写缓存 —— 与"间隔必须 > TTL"彻底解耦,
    # 且不污染主链缓存(主链读的是同一个 meoz:fundflow_kp:* key)。
    # 🔴 2026-09-29 (P0-c2): 上面这条 WP2b 设计**保留不动**(仍不读写缓存、仍直打上游),
    #   只补一层"**每轮只让一个进程取**"的跨进程令牌 —— 生产 2 worker 各有自己的
    #   `_last_netfill_ts`, 于是同一轮两个进程各取一遍(净额 3 片 + 量比 1 次全市场),
    #   而这两处取的**是同一份竞价定格终值**(幂等回填, 谁写都一样) ⇒ 重复纯属浪费上游。
    #   抢不到 = 另一 worker 本轮已在取 ⇒ 本轮直接跳过(它在写库, 结果一致)。
    if not store.setnx(_netfill_turn_key("net", date), 1, ttl=_netfill_turn_ttl()):
        log.info("[竞价补采] 净额: 另一 worker 本轮已取, 跳过 date=%s", date)
        return (0, 0, len(codes))
    ff_map = meoz_client.fundflow_map(codes, date_offset=0, fresh=True)
    if not ff_map:
        return (0, 0, len(codes))

    updates = []
    nz = 0
    for code in codes:
        v = _f((ff_map.get(code) or {}).get("auction_main_net_amount"))
        if v is None or v == 0:
            continue                    # 无值 / 真 0(竞价无大单异动) → 不回填
        nz += 1
        updates.append((v, date, point, code))
    if not updates:
        return (0, 0, len(codes))

    conn = database.get_conn()
    try:
        cur = conn.executemany(
            "UPDATE snapshot_bid SET auc_main_net=? "
            "WHERE date=? AND time_point=? AND code=? AND auc_main_net=0",
            updates)
        conn.commit()
        n_upd = cur.rowcount or 0
    finally:
        conn.close()
    return (nz, n_upd, len(codes))


def refill_bid_vol_ratio(date, point="9_25"):
    """竞价量比补采: 只回填 snapshot_bid.auc_vol_ratio 一列(**兜底网**)。

    与 refill_bid_main_net 同通道同时刻(09:26:10 起轮询)。「拿到猫爪数据再定格」上线后
    正常那一枪就采到了; 本函数兜住两种残余:
      ① 猫爪偶发抖动 —— 今日 09:25:48 实测 8 秒故障, daily_auc 直接返回 0 行;
      ② 上游产出晚于定格截止(_BID25_RETRY_UNTIL = 09:26:30, 与定格同刻)。

    ★ 与 snapshot_at 的分工: 只做列回填, **不触发 aipick / system_batch**;
      不覆盖已有非零值(WHERE auc_vol_ratio=0) ⇒ 幂等, 可安全重复调用。
    ★ 取数**显式传 date 且 fresh 直打上游** —— 不用 date_offset=0: 那条路在早盘会串到
      上一交易日的 9:25 数据, 把昨日量比当今日值回填(见 meoz_client.daily_auc_amt 串日语义)。
    返回 (上游非零只数, 实际回填行数, 定格行总数)。
    """
    from . import meoz_client

    if not meoz_client.enabled():
        return (0, 0, 0)

    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT code FROM snapshot_bid WHERE date=? AND time_point=?",
            (date, point)).fetchall()
    finally:
        conn.close()
    codes = [str(r[0]) for r in rows]
    if not codes:
        return (0, 0, 0)

    want = str(date).replace("-", "")
    # 🔴 2026-09-29 (P0-c2): 与 refill_bid_main_net 同一理由 —— fresh 直打上游**保留**,
    #   只加"每轮只让一个进程取"的跨进程令牌(独立键 `vr`, 不与净额互相阻塞)。
    if not store.setnx(_netfill_turn_key("vr", date), 1, ttl=_netfill_turn_ttl()):
        log.info("[竞价补采] 量比: 另一 worker 本轮已取, 跳过 date=%s", date)
        return (0, 0, len(codes))
    am = meoz_client.daily_auc_amt("0925", date=want, fresh=True)
    if not am:
        return (0, 0, len(codes))

    updates = []
    nz = 0
    for code in codes:
        r = am.get(code) or {}
        td = str(r.get("tradedate") or "").replace("-", "")
        if td and td != want:
            continue                      # 串日残值 → 跳过(绝不回填昨日量比)
        v = _f(r.get("auc_vol_ratio"))
        if v is None or v <= 0:
            continue                      # 无值 / 真 0 → 不回填
        nz += 1
        updates.append((v, date, point, code))
    if not updates:
        return (0, 0, len(codes))

    conn = database.get_conn()
    try:
        cur = conn.executemany(
            "UPDATE snapshot_bid SET auc_vol_ratio=? "
            "WHERE date=? AND time_point=? AND code=? AND auc_vol_ratio=0",
            updates)
        conn.commit()
        n_upd = cur.rowcount or 0
    finally:
        conn.close()
    return (nz, n_upd, len(codes))


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
    if not _is_trade_day(g):
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
            if _is_trade_day(g) and LASTSEC_START <= ts_total <= LASTSEC_END:
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


def _select_snap_rows(conn, use_date, time_point):
    """读某日某时点定格行(列序: code,name,bid_change,bid_amt,float_mv,free_mv,board,warn_type)。

    2026-09-18 (v4.11.30): `warn_type`(东财 f630 异动等级) 是后加的列。**未迁移的老库**
    (或测试里自建的精简 snapshot_bid 表) 缺该列 → 这里显式降级为「无 warn_type」而非
    让整个 load_snapshot_full 抛异常返回 {} —— 那会让**整个名单独不出来**(比"异动缺值"
    严重得多)。降级行补 None → QuoteRow.warn_type=None → 评分走 default, 与改动前一致。
    """
    base = "SELECT code, name, bid_change, bid_amt, float_mv, free_mv, board "
    try:
        return conn.execute(
            base + ", warn_type FROM snapshot_bid WHERE date=? AND time_point=?",
            (use_date, time_point)).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            base + "FROM snapshot_bid WHERE date=? AND time_point=?",
            (use_date, time_point)).fetchall()
        return [tuple(r) + (None,) for r in rows]


def latest_trade_snap_date(date=None, time_point="9_25", days=None):
    """快照表里 `<= date` 的最近一个**交易日**快照日期(2026-09-27 v4.11.66 新增)。

    ★ 为什么需要: 原实现各处都是裸的 `SELECT MAX(date) FROM snapshot_bid ...`, **没有任何
      交易日历过滤** ⇒ 非交易日因"当天还没装日历门禁"而落下的**幽灵快照**会被当成"最近
      交易日"。2026-09-25(中秋·周五·法定休市)就是这样: 当天 09:15/09:20/09:24/9_25 四枪
      全部照采, 源端三路(daily_auc tradedate≠20260925、screening 竞价0只、TickPlus 0 条)
      皆空, 系统自己打了 `[数据质量] 竞价封单数据异常` 也照落库 —— 于是 09-27(周日)整站
      把 09-25 当"最近交易日", 竞价异动十个 tab 全被顶成休市日静态值(竞价爆量/昨涨停直接变空)。
      生产机当晚 18:49 补了门禁并清掉了残留, 测试机只补了门禁、残留一直在 ⇒ 两机分叉。

    做法: 回溯候选日期(降序) → `tc.latest_trade_in()` 取第一个交易日。
    fail-open: 查库异常 / 无合规候选 → 返回原 `date`(与改造前一致, 绝不主动留空)。
    """
    date = date or _bj_date()
    days = _FALLBACK_SNAP_DAYS if days is None else days
    conn = None
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT DISTINCT date FROM snapshot_bid "
            "WHERE date<=? AND time_point=? AND date>=date('now', '-%d days', '+8 hours') "
            "ORDER BY date DESC" % int(days),
            (date, time_point)).fetchall()
    except Exception:
        return date
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass
    picked = tc.latest_trade_in([r[0] for r in rows if r and r[0]], date)
    return picked or date


def load_snapshot_full(date=None, time_point="9_25"):
    """读某日某时点**全市场快照行**(盘后 filter 候选池用, 2026-09-07 主人要求:
    候选池=全市场且直接用已自动采集的快照表, 不再实时拉全市场 28 页)。
    返回 {code: {name, bid_change, bid_amt, float_mv(元), free_mv(元), board, warn_type}};
    float_mv/free_mv 保持原始单位(元, 与行情 f20/f21 一致, /1e8=亿)。
    当日该时点无快照(周末/休市/采集缺失)→ 自动回退**最近一个有快照的交易日**,
    保证休市/盘后浏览仍能按最近竞价结果筛股。"""
    date = date or _bj_date()
    # 2026-09-27 v4.11.66: 原内联 `SELECT MAX(date)` 无交易日历过滤 → 休市日幽灵快照会被
    # 当成"最近交易日"。统一走 latest_trade_snap_date()(见其 docstring 的事故说明)。
    use_date = latest_trade_snap_date(date, time_point)
    conn = None
    try:
        conn = database.get_conn()
        rows = _select_snap_rows(conn, use_date, time_point)
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
               "float_mv": r[4] or 0.0, "free_mv": r[5] or 0.0, "board": r[6] or "",
               # 异动等级(东财 f630, v4.11.30): 定格链路的 17% 异动因子来源。
               # None(老库该列不存在/行缺失) → QuoteRow.warn_type=None → 走 default,
               # 与改动前行为一致, 不制造回归。
               "warn_type": r[7]}
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
    (查询自然返回 {}, 保持现状兜底, 不把陈旧数据当最近交易日)。

    ★ 2026-09-27 v4.11.66: 改为委托 `latest_trade_snap_date()` —— 原实现只有 15 自然日窗口
      而**没有交易日历过滤**, 休市日幽灵快照会被当成"最近交易日"(2026-09-25 中秋事故)。
    """
    return latest_trade_snap_date(date, "9_25", _FALLBACK_SNAP_DAYS)


def has_today_snapshot(date=None) -> bool:
    """当日是否已有 9_25 定格快照行(选股闸门的**快照维**)。

    2026-09-16 新增, 配合 picker/mode.is_pick_open 做双闸门; v4.11.26 随闸门回退
    一并摘除, v4.11.27 **原样恢复**(口径重做只动时间维, 快照维语义不变):
    9_25 定格**落库时刻**取决于定格首采时刻 _BID25_FREEZE_SEC(=09:26:30)、「拿到猫爪数据
    再定格」的就绪判定 meoz_bid_ready 与重采截止 _BID25_RETRY_UNTIL(=09:26:30, 2026-09-29 收紧) —— 推迟定格后
    正常落在 09:26:1x~09:26:4x(2026-09-24 之前实测 09:25:23~09:25:32)。若某日
    重采一次越过放行点, 纯时间闸门会放行, 而 load_snapshot_full 仍会**静默回退昨日**
    (9/16 事故根因: 9:25:14/9:25:29 两个用户拿到 9/15 名单)。故此维不能省。

    与 _latest_snapshot_date 的区别: 后者返回"最近有快照的日期"(15 自然日窗口内无
    任何 9_25 行时会**返回原 date**, 无法区分"当日有"和"当日没有") —— 闸门必须精确
    知道"当日有没有", 故直接 `SELECT 1 ... LIMIT 1`(走 PK 索引, 亚毫秒)。

    查库异常 → 返回 False(保守: 宁可拦住, 也不放行一个可能回退昨日的名单)。
    """
    date = date or _bj_date()
    conn = None
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT 1 FROM snapshot_bid WHERE date=? AND time_point='9_25' LIMIT 1",
            (date,)).fetchone()
        return row is not None
    except Exception as e:                                        # noqa: BLE001
        log.warning("has_today_snapshot 查询失败(保守视为无快照) date=%s err=%s", date, e)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                # noqa: BLE001
                pass


def freeze_source_date(date=None):
    """当前选股**实际使用的定格数据来源日期**(v4.11.29, 2026-09-18)。

    与 load_day_bid_change / load_day_bid_amt / load_snapshot_full 同源口径:
    当日已有 9_25 定格行 → 当日; 否则回退最近 15 自然日内有 9_25 行的交易日
    (盘前/收盘后/周末/节假日), 都没有 → 返回原 date(表示"无定格可用")。

    为什么要透出: 盘前(00:00-9:15)与非交易日按设计**仍允许出名单**, 用的是上一交易日
    定格 —— 但用户无法从名单本身分辨, 容易误认为"当日名单"(9/18 主人反馈"刷出来是
    昨天的数据"的来源之一)。前端据此在顶部常驻标注"当前为 X 日定格数据"。
    """
    date = date or _bj_date()
    return _latest_snapshot_date(date)


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


def _consume_once(date, tp, label, fn):
    """副作用**一次性**执行: 成功才落 done 键, 失败回滚允许窗口内重试(2026-09-24 WP2c)。

    ★ 与 2026-09-17 auto_apply 事故同源教训(C5 铁律): 守卫键**绝不能在成功之前被消费**,
      否则一次失败就烧掉当日全部重试机会 —— 那天正是 setnx 先落键、_pick_result() 提前
      return error, 导致 9:26-9:30 约 24 轮全被挡掉、当日系统批次永久缺失。

    Args:
        date: 交易日。
        tp: 时点标识(如 9_25)。
        label: 日志标签(aipick / sysbatch)。
        fn: 无参副作用函数。
    """
    done_key = "snap:consumed:%s:%s:%s" % (date, tp, label)
    if store.get(done_key):
        log.info("[快照采集] %s 消费已完成过, 跳过(幂等) date=%s tp=%s", label, date, tp)
        return
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        log.error("[快照采集] %s 消费失败(窗口内可重试) err=%s", label, e)
        return                                                 # ← 不落键, 允许重试
    store.set(done_key, 1, ttl=86400)


def _scheduler_loop():
    """后台调度: 工作日按时点窗口抓取一次, 每 10 秒轮询; 9:31 后盘点当日采集情况"""
    # 去重标记走 CacheStore: qc/weekend 各自 setnx 1 天
    global _last_intraday_ts, _last_netfill_ts   # 模块级时间戳, 否则函数内赋值会被视为局部变量 → UnboundLocalError
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            date = _bj_date()
            hm = g.tm_hour * 60 + g.tm_min
            # ★ 2026-09-25（中秋节 · 星期五）复盘新增: 非交易日**整轮跳过**。
            #   原判定只在下方 for 内做 `g.tm_wday >= 5`, 漏掉法定假日 ⇒ 2026-09-25
            #   当天照常把「上一交易日复制行」写进 snapshot_bid(`price`/`bid_turnover` 全 0),
            #   既污染快选 App 又喂出 AI 选股 30 只假名单。提到循环顶部可一次性覆盖
            #   下面所有窗口任务(净额补采 / 分时快照 / 9:31 盘点 ...)。
            if not _is_trade_day(g):
                # 只在当天首次记录一次, 避免每 10 秒刷日志
                if store.setnx("sched:weekend:" + date, 1, ttl=86400):
                    log.info("[快照采集] 非交易日(周%d%s) date=%s 跳过采集",
                             g.tm_wday, ", 法定休市" if tc.is_holiday(date) else "", date)
                # ⚠️ `continue` 会跳过循环末尾的 time.sleep(10) ⇒ 必须在此补睡, 否则热转吃满 CPU
                time.sleep(10)
                continue
            for tp, (start, end) in TIME_POINTS.items():
                key = "sched:done:%s:%s" % (date, tp)
                if store.get(key):
                    continue
                if not _is_trade_day(g):
                    # 顶部门禁已拦下非交易日, 此处为**冗余保险**(防未来重构把顶部门禁挪走);
                    # 同样只在当天记一次日志, 避免每分钟刷屏
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
                elif tp == "9_25" and _bid25_before_freeze(hm, g.tm_sec):
                    # ★ 定格首采前的**静默段**(2026-09-24 主人拍板: 定格枪固定在 09:26:30):
                    #   9:25:00~9:26:29 一律不采 —— 猫爪竞价字段实测 09:25:35~09:26:16 才产出,
                    #   此刻采必然是空车, 还白烧一轮全市场拉取(8~15s)并把完成标记反复 set/delete。
                    #   09:26:30 起进入首采; 未就绪则由下方保险丝 10s 轮询重采兜到 09:27:30。
                    # ★ 历史写法 `hm == 9*60+25 and g.tm_sec < _BID25_MIN_SEC` 已**废止**:
                    #   _BID25_MIN_SEC 涨到 90(> 59)后, 该式只挡得住 9:25 整分钟, 9:26:00 就会开采
                    #   —— 恰在定格时刻之前 30 秒, 正是本次要消除的空车采集。
                    # 单位口径已收进 _bid25_before_freeze 单一入口(两侧同为当日绝对秒)。
                    # 2026-08-18 主人要求: 9:25 竞价撮合后数据定格, 晚几秒采保证一致 —
                    # 9:25:00-10 是撮合瞬间, 接口返回中间态(如中石科技 20% vs 定格后 19.53%),
                    # 各机器轮询时刻不同导致快照不一致; 延迟采, 拿最终竞价值。
                    # 2026-09-11 P0-1: 阈值 10 → 20(常量 _BID25_MIN_SEC); 生产库实测落库时刻
                    #   9/7=09:25:20 9/8=09:25:22 9/9=09:25:20 9/10=09:25:32, 熔断日 9/11=09:25:12
                    #   —— 轮询相位(10s)使落库时刻在 10~40s 间抖动, 下限过低会踩到撮合未完成的中间态。
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
                            # 9:26:00 之后不再回滚: 宁可保留中间值也不能整点缺失 ——
                            # 9_25 缺失会连锁砸坏选股名单(9/11 熔断日仅 132 行即导致候选池塌陷)。
                            # 必须放在 aipick / system_batch 触发之前: 不能用残值跑预测与锁仓。
                            # 单位口径已收进 _bid25_retry_open 单一入口并由测试钉死 ——
                            # 2026-09-19 的内联「减 9*3600」把两侧口径弄反, 令重采静默
                            # 失效近一周(2026-09-24 修 auc_vol_ratio 恒 0 时才发现)。
                            # 回滚重采的**两条**触发条件(2026-09-24 起):
                            #   ① 与 9_24 同额率过高 → 东财仍返回 9:24 残值(原有保险丝);
                            #   ② 猫爪竞价字段未就绪 → daily_auc/量比还没产出。
                            #      主人拍板「拿到猫爪数据再定格」新增; 今日 09:25:48 实测
                            #      猫爪 8 秒抖动 + daily_auc 返回 0 行, 恰好砸在定格那一枪。
                            _not_ready = ""
                            if tp == "9_25":
                                if _same_as_prev_rate(date, tp, "9_24") > _SAME_PREV_MAX:
                                    _not_ready = "定格值疑似未发布(与9_24同额率>%.0f%%)" % (
                                        _SAME_PREV_MAX * 100)
                                elif not meoz_bid_ready(date):
                                    _not_ready = "猫爪竞价字段未就绪(等 daily_auc/量比)"
                            if tp == "9_25" and _not_ready:
                                if _bid25_retry_open(hm, g.tm_sec):
                                    store.delete(key)
                                    log.warning(
                                        "[快照采集] 9_25 %s, 窗口内重采 date=%s hm=%d:%02d:%02d",
                                        _not_ready, date, hm // 60, hm % 60, g.tm_sec)
                                    continue
                                log.warning(
                                    "[快照采集] 9_25 临近窗口末尾(截止 %02d:%02d), 接受当前值不再重采 "
                                    "[%s] date=%s hm=%d:%02d:%02d",
                                    _BID25_RETRY_UNTIL // 3600,
                                    (_BID25_RETRY_UNTIL % 3600) // 60,
                                    _not_ready, date, hm // 60, hm % 60, g.tm_sec)
                            log.info("[快照采集] 时点完成并入完成集 tp=%s date=%s", tp, date)
                            # 2026-08-18 主人要求: 9_25 竞价快照落库后立即触发 AI 采集+预测
                            # (不等 9:27 轮询窗口, 数据到手就预测, 9:30 前出结果)
                            if tp == "9_25":
                                # 2026-09-24 (WP2c): 两个副作用各自加"成功才落键"的幂等守卫 ——
                                # 定格重跑不再重复跑预测、重复写历史名单。日志语义保持不变。
                                try:
                                    from . import aipick_scheduler
                                    _consume_once(date, tp, "aipick",
                                                  aipick_scheduler.trigger_after_bid_snapshot)
                                except Exception as e:
                                    log.error("aipick 采集/预测触发失败 err=%s", e)
                                # 2026-08-30 主人需求: 9_25 落库后自动跑 system batch 存历史回看
                                # (即使当天没点选股, 也能看到系统当时推荐的 top 30)
                                try:
                                    from . import system_batch
                                    _consume_once(date, tp, "sysbatch",
                                                  lambda: system_batch.run_system_batch("9_25"))
                                except Exception as e:
                                    log.error("system_batch 触发失败 err=%s", e)
                        else:
                            # 失败回滚 setnx 标记: 窗口内下一轮轮询(10s)重试, 东财/KPL 瞬时故障自愈
                            # 窗口结束后(hm>end)不再触发, 9:31 盘点告警兜底
                            store.delete(key)
                            log.warning("[快照采集] 时点失败(返回0) tp=%s date=%s 窗口内将重试", tp, date)
            # 抢筹结果快照: 触发一次 fetch_bid_qiangcang 落库
            # (2026-08-17 修复: 9:26 触发太晚 —— 开盘啦 Type4 竞价净额 9:25 撮合后清零,
            #  会导致 list20=0 落库失败; 故首采落在 9:24 那一轮, 此时 Type4 最接近定格且有效)
            # 🔴 2026-09-29 主人新口径: 该轮采(**含失败重试**)不得晚于 **09:26:30** ——
            #   原上界 9:30 会让"失败重试"一直打到开盘前, 且定格之后仍可能改写竞价存档;
            #   现窗口 = [09:24:00, 09:26:30](首采仍在 9:24 那轮, setnx 只打一次)。
            if (_is_trade_day(g) and 9 * 60 + 24 <= hm
                    and hm * 60 + g.tm_sec <= _BID_QC_UNTIL_SEC
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
                    # 失败回滚: 窗口 [9:24:00, 09:26:30] 内下一轮轮询重试
                    # (避免 KPL 瞬时故障导致抢筹 tab 当日无数据; 2026-09-29 起上界由 9:30 收到 09:26:30)
                    store.delete("sched:qc:" + date)
                    log.warning("竞价抢筹结果快照失败(窗口内将重试) err=%s", e)
            # 竞价迟到字段补采(2026-09-24 主人要求「9:26:10 起轮询, 取到为止」):
            #   净额 auc_main_net(fundflow_kp) 与 量比 auc_vol_ratio(daily_auc) 同属"上游
            #   09:25:35~09:26:16 才产出"的迟到列。定格推迟后正常那枪已能采到, 本通道兜住
            #   两种残余: 猫爪偶发抖动(今日 09:25:48 实测 8 秒)、产出晚于定格截止。
            #   09:26:10 起每 netfill_interval() 秒补一次, 两项都达标即停;
            #   🔴 2026-09-29 主人新口径: 硬上限 **09:26:30**(= 定格时刻, 原 09:29:50) ——
            #     定格那一轮写完快照后同轮立即补一次(猫爪已出满), 此后不再补/不再改数据。
            if _netfill_due(hm * 60 + g.tm_sec, g.tm_wday, _last_netfill_ts,
                            time.time(), store.get("sched:done:netfill_" + date),
                            date=date):
                _last_netfill_ts = time.time()
                try:
                    nz, n_upd, n_all = refill_bid_main_net(date)
                    # 竞价量比走**同一通道**兜底(2026-09-24): 定格推迟后正常那枪已采到,
                    # 这里兜猫爪抖动(今日 9:25:48 实测 8 秒)与产出晚于定格截止两种残余。
                    nz_vr, n_upd_vr, _ = refill_bid_vol_ratio(date)
                    log.info("[竞价补采] date=%s %d:%02d:%02d 净额非零%d/%d回填%d行 | "
                             "量比非零%d回填%d行",
                             date, hm // 60, hm % 60, g.tm_sec, nz, n_all, n_upd,
                             nz_vr, n_upd_vr)
                    if nz >= NETFILL_MIN_N and nz_vr >= _VR_READY_MIN_N:
                        store.setnx("sched:done:netfill_" + date, 1, ttl=86400)
                        log.info("[竞价补采] 达标(净额%d≥%d, 量比%d≥%d) 停止轮询 date=%s",
                                 nz, NETFILL_MIN_N, nz_vr, _VR_READY_MIN_N, date)
                except Exception as e:
                    log.warning("[竞价补采] 失败(窗口内下一轮重试) err=%s", e)
            # 9:26-9:30 自动应用选股(2026-08-16 用户反馈): 用户打开应用但没点"应用"按钮,
            # 当天历史为空; 9:25 快照齐后给所有活跃用户跑一次自动应用(标记 auto_applied=True).
            # 后台守护线程执行(全市场评分一次+按用户过滤), 不阻塞本调度循环.
            #
            # 🔴 2026-09-17 (v4.11.27 修复): 判据收敛到 auto_apply.should_trigger()。
            #   原写法是 `store.setnx("sched:auto_apply:" + date, 1, ttl=86400)` ——
            #   **每日一次性**锁, 且它在**成功之前**就被消费: 无票/异常时 _pick_result()
            #   提前 return error, 锁却已烧掉 → 9:26-9:30 剩余约 24 轮(10s 一轮)全被挡掉 →
            #   **当日系统统一批次永久缺失** → 用户刷新只能跨日回退到上一个交易日的名单。
            #   这是 2026-09-17「9:30 后出来的数据好像是昨天的」事故的放大部分。
            #   现在 = 「当日未成功(done 键) 且 不在 60s 节流窗口内」→ 无票可继续重试到成功。
            # 🔴 2026-09-28 (v4.11.76): 窗口加**快照维守卫** has_today_snapshot(date) —— 双保险防绕过判据重演
            if _is_trade_day(g) and 9 * 60 + 26 <= hm <= 9 * 60 + 30 and has_today_snapshot(date):
                try:
                    from . import auto_apply
                    if auto_apply.should_trigger(date):
                        auto_apply.trigger_auto_apply()
                except Exception as e:
                    log.warning("9:26 自动应用 调度失败(不影响抢筹落库) err=%s", e)
            # 15:30-15:35 板块轮动日终快照: 抓当日板块强度 Top10 落库(多数据源), 形成轮动数据基础
            if _is_trade_day(g) and 15 * 60 + 30 <= hm <= 15 * 60 + 35:
                try:
                    # 两市概况收盘快照(2026-08-16): 供次日"两市总量/较上一日"对比
                    # 收盘后 f6=全天成交额, stockCount=全市场股票数
                    if store.setnx("sched:done:market_brief_" + date, 1, ttl=86400):
                        try:
                            from . import fetcher
                            brief = fetcher.fetch_market_brief(max_age=0)  # 强制刷新
                            # ★ 2026-09-26 (v4.11.57) 写前守卫 —— brief 的日期**必须就是今日**。
                            #   理由与"宁可拒写也不写错日期"的纪律见 _brief_date_ok 的 docstring。
                            #   置 None ⇒ 复用下方 `else: store.delete(...)` 的"窗口内重试"。
                            if brief and not _brief_date_ok(brief, date):
                                log.warning("两市概况收盘快照日期非今日(取到 %s, 今日 %s)"
                                            " → 拒绝落库, 15:30-15:35 窗口内重试",
                                            brief.get("date"), date)
                                brief = None
                            if brief:
                                # 2026-09-19(主人指令, v4.11.31): 收盘定格的两市资金改取
                                # 开盘啦 MarketSCLNKLine 日级历史(his 域名, Type=0) ——
                                # 与主数字(MarketCapacityKLine)同源同口径; 取不到/日期
                                # 未定格回退东财自算值。开关 market_vol_rt=0 时完全不调。
                                try:
                                    from . import settings as _st_svc
                                    if _st_svc.get("market_vol_rt", 0):
                                        from . import kpl as _kpl_svc
                                        _h = _kpl_svc.parse_market_scln_hist_latest(
                                            _kpl_svc.fetch_kpl_market_scln_hist(),
                                            expect_date=brief["date"])
                                        if _h:
                                            brief = dict(brief)
                                            brief["amount"] = _h["amount"]
                                            brief["amountSrc"] = "kpl_hist"
                                except Exception as _e:
                                    log.warning("MarketSCLNKLine 收盘定格取数异常(回退自算) err=%s", _e)
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
                    # 收盘后回收 kv_cache 过期行(2026-09-26 v4.11.53)。
                    # 为什么挂在这一段: ①本窗口已有 `_is_trade_day(g)` 守卫, 天然满足
                    #   "只在交易日盘后跑一次"的频次要求; ②purge 是纯维护动作, 与
                    #   market_brief / sector 同属"日终收尾", 语义一致; ③被删的行读侧
                    #   本来就取不到(见 CacheStore.purge_expired) ⇒ 零行为影响。
                    # 实测背景: 生产 kv_cache 3885 行里 3802 行早已过期仍在库, 最早 40 天前。
                    if store.setnx("sched:done:kvpurge_" + date, 1, ttl=86400):
                        n_purged = store.purge_expired()
                        if n_purged:
                            log.info("kv_cache 过期行回收 %d 行 date=%s", n_purged, date)
                except Exception as e:
                    log.warning("板块轮动/两市概况 日终快照失败 err=%s", e, exc_info=True)
            # 15:50-15:55 板块轮动+人气热榜兜底补跑:
            # 查 DB 当日各源是否已入库, 缺则尝试补抓(避开开盘啦15:30瞬时未冻结/接口抖动)
            if _is_trade_day(g) and 15 * 60 + 50 <= hm <= 15 * 60 + 55:
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
            # 人气热榜/连板梯队/竞价异动 15:30-15:35 日终快照(与板块轮动同一窗口并行)
            # 注: 龙虎榜**不在此窗口** —— 见下方 18:30-18:40 晚间窗口(P1-a)
            if _is_trade_day(g) and 15 * 60 + 30 <= hm <= 15 * 60 + 35:
                try:
                    # 人气热榜历史快照(三源): 供人气榜回看历史
                    from . import hot_rank
                    for src in ("kpl", "em", "ths"):
                        if store.setnx("sched:done:hot_%s_%s" % (src, date), 1, ttl=86400):
                            hot_rank.save_hot_rank_history(date, source=src)
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
                    log.warning("日终快照(热榜/连板/竞价异动)失败 err=%s", e, exc_info=True)
            # 龙虎榜当日快照 18:30-18:40 晚间窗口(P1-a, 2026-09-13):
            # 龙虎榜是**盘后公布**(通常 18:00 后), 旧逻辑放在 15:30 采必然为空 ——
            # 生产实证 lhb_history 仅 7 行(全靠一次性回补脚本填充), 每日调度从未成功。
            # 挪到晚间窗口, 并补失败回滚: 旧逻辑 setnx 占锁后判空不释放(对照同窗口
            # ladder 子块有 store.delete 回滚), 窗口内不会重试 → 一次空就当天废弃。
            if _is_trade_day(g) and 18 * 60 + 30 <= hm <= 18 * 60 + 40:
                try:
                    if store.setnx("sched:done:lhb_" + date, 1, ttl=86400):
                        from . import kpl
                        from ..db import database
                        lst = kpl.fetch_lhb(date)
                        if lst:
                            conn = database.get_conn()
                            conn.execute(
                                "INSERT OR REPLACE INTO lhb_history (date, list, ts) VALUES (?,?,?)",
                                (date, json.dumps(lst, ensure_ascii=False), int(time.time())))
                            conn.commit()
                            conn.close()
                            log.info("龙虎榜当日快照已存 date=%s 共%d条", date, len(lst))
                        else:
                            store.delete("sched:done:lhb_" + date)   # 失败回滚, 窗口内重试
                except Exception as e:
                    log.warning("龙虎榜晚间快照失败 err=%s", e, exc_info=True)
            # 9:31-9:35 盘点当日采集: 缺失时点告警(排查关键, 数据过了点无法补)
            if _is_trade_day(g) and 9 * 60 + 31 <= hm <= 9 * 60 + 35 and store.setnx("sched:checked:" + date, 1, ttl=86400):
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
            if _is_trade_day(g) and 9 * 60 + 30 <= hm <= 15 * 60:
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
_last_netfill_ts = 0.0    # 上次竞价净额补采时刻(模块级, 用于 netfill_interval() 节流)


def start_scheduler():
    """main.py startup 调用: 启动后台抓取线程(单 worker 下唯一实例)"""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=_lastsec_loop, daemon=True)
    t2.start()
    log.info("竞价多时点快照调度已启动(9:15/9:20/9:24/9:25/9:26抢筹结果快照/9:24:45-9:25:03最后一秒高频采样)")
