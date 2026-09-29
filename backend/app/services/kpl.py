# -*- coding: utf-8 -*-
"""
开盘啦(龙虎榜 App)数据源: 竞价委买额/连板梯队/情绪值/涨停原因/板块强度等
========================================================================
付费接口(每日 80000 次), 用于补充东财拿不到的短线维度。
原则: 低频缓存 + 失败降级(返回 None, 绝不阻塞主流程)。
返回字段均为 App 数组格式(无字段名, 靠位置解析), 统一在此转换为 dict。
"""
import json
import sqlite3
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..core import config, logger
from ..core import net as _net
from ..core import trade_calendar
from .cache_store import store

log = logger.get_logger(__name__)

# ---------------------------------------------------------------- 两市概况"较昨日全天"基准
_MB_CLOSE_SEC = 15 * 3600 + 30 * 60
"""两市概况**收盘快照的落库时刻**(15:30)。

与写入侧同值: `auction_snapshot._scheduler_loop` 的 `15:30 <= hm <= 15:35` 窗口。
读取侧用它判定「`market_brief_last` 是否已被**今日收盘**覆盖」⇒ 两处**必须同值**,
改一处必须改另一处。
"""

_warned_mb_date = None
"""`market_brief_last` 出现非交易日日期时的「每日一次」告警去重(同 trade_calendar 的写法)。"""


def _mb_is_trade_day(d) -> bool:
    """`trade_calendar.is_trade_day` 的**不抛异常**包装。

    库里存的日期串可能是任何形态(历史脏值), 而 `_date.fromisoformat` 对 "2026-99-99"
    这类串会抛 ValueError —— 脏数据绝不该把调用方整体拖死(那会让 `last` 直接变 None,
    前端"放量"整块消失)。不可判定时返回 True(保守放行, 不阻断既有行为)。
    """
    try:
        return trade_calendar.is_trade_day(d)
    except Exception:                                             # noqa: BLE001
        return True


def _mb_baseline_is_today(last: Optional[dict], now_ts: Optional[float] = None) -> bool:
    """`market_brief_last` 是否**已被今日收盘覆盖**(⇒ 该改用 `prev` 作"较昨日全天"基准)。

    `now_ts`: 时间注入(单测用; None = 当前时间)。

    ★ 2026-09-26 (v4.11.57): 判据由「`last.date` == **字面今天**」改为**交易日历显式语义**。
      原写法只在"写入侧恰好只于交易日 15:30 落库"这一前提下才成立 —— 属**靠巧合正确**:

      ① 它用系统时钟的"今天", 与"今天是不是交易日"完全无关;
      ② 写入侧此前没有"日期必须是今日"的**写前守卫**, 而行情源在收盘定格尚未生成时会返回
         **上一交易日的复制行** —— 2026-09-25(中秋)正是这类残值被写成 settings 键
         (`market_brief_*` 2 个 + `kv_cache` 42 个, 见 v4.11.53 复盘)。这种**非交易日**日期
         与"今天"永不相等 ⇒ 脏值会被长期当作"上一交易日全天"喂给前端, 且**不会自愈**。

      现在把语义写全: 「① 今日是交易日 ∧ ② 已过收盘快照时刻(15:30) ∧ ③ `last.date` 确为今日」。
      三条缺一不可 —— 缺① 会误切 `prev`(非交易日并不存在"今日收盘");
      缺② 会在收盘快照写入前就切走(此时 `last` 还是上一交易日, 切了等于跳过一天);
      缺③ 会把陈旧值误当成今日收盘。
    """
    if not last:
        return False
    ld = last.get("date")
    if not ld:
        return False
    ts = time.time() if now_ts is None else float(now_ts)
    if str(ld) != trade_calendar.bj_date(ts):                     # ③ last 确为今日所写
        return False
    if not _mb_is_trade_day(trade_calendar.bj_date(ts)):          # ① 今日是交易日
        return False
    g = time.gmtime(ts + 8 * 3600)
    hm_sec = g.tm_hour * 3600 + g.tm_min * 60 + g.tm_sec
    return hm_sec >= _MB_CLOSE_SEC                                # ② 已过收盘快照时刻


def _mb_warn_if_stale(last: Optional[dict]) -> None:
    """`market_brief_last` 的日期**本身不是交易日** ⇒ 上游曾写下脏值。

    只告警、**不就地篡改** —— 篡改会掩盖根因(真正的修法是写入侧的写前守卫)。
    与铁律2「降级必须可见」一致: 这种值会让"较昨日全天"基准整块错位, 必须留痕。
    每日一次, 避免每次请求刷屏(payload 本身有 TTL 缓存, 但 TTL 到期仍会重算)。
    """
    global _warned_mb_date
    ld = (last or {}).get("date")
    if not ld or _warned_mb_date == ld:
        return
    if _mb_is_trade_day(ld):
        return
    _warned_mb_date = ld
    log.warning("market_brief_last 日期非交易日 date=%s(上游脏值?) → "
                "「较昨日全天」基准可能错位, 请查写入侧写前守卫", ld)


# 进程级共享线程池(2026-09-01 生产线程爆炸修复): 现涨K线兜底 原每次请求新建池 +
# shutdown(wait=False) 后线程滞留后台跑网络超时, 高并发下线程只增不减拖死生产。
# 改常驻池: 线程数有界(6), 超时放弃的任务留池内排队, 不阻塞请求也不新建线程。
_EXECUTOR_FILL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="kf-fill")

# ---------- 健康监控 ----------
_HEALTH = {
    "kpl": {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0, "ms_sum": 0, "ms_cnt": 0, "down_since": 0},
}
_health_lock = threading.Lock()

# 缓存与并发信号量已外置 CacheStore(跨进程共享):
#   - 缓存: store.get/set("kpl:" + key) — 多 worker 共享, 避免付费配额 ×N
#   - 信号量: store.acquire_sem("kpl", limit=3) — 全局并发仍 3
# 旧进程内 _cache / _SEM 移除(2026-08-16 Phase1)

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def clear_cache():
    """清空全部 KPL 缓存(快照采集前强制拿当前时点新鲜数据)"""
    try:
        store.clear_prefix("kpl:")
    except Exception:
        pass


def _record(ok, ms=0):
    with _health_lock:
        h = _HEALTH["kpl"]
        now = time.time()
        if ok:
            h["ok"] += 1
            h["last_ok"] = now
            if ms > 0:
                h["ms_sum"] += ms
                h["ms_cnt"] += 1
            if h["down_since"]:
                log.info("开盘啦数据源恢复(故障%.0f秒)", now - h["down_since"])
                h["down_since"] = 0
        else:
            h["fail"] += 1
            h["last_fail"] = now
            if not h["down_since"]:
                h["down_since"] = now
                log.warning("开盘啦数据源故障(开始降级)")


def _urlopen(req, timeout=10, context=None):
    """开盘啦专用 urlopen: 包一层**出站 IP 轮询**。

    2026-09-09: 此前 kpl 全部裸调 urllib.request.urlopen → 走 OS 默认单出口(eth0),
    而 eth0 的公网 IP 已被新浪等源拉黑(403)。开盘啦是**竞价数据唯一来源**, 却没吃到
    9/7 加的双网卡轮换。改走 core.net.http_get 后两个出口 RR 轮询 + 失败惩罚。
    """
    return _net.http_get(req, timeout=timeout, context=context)


def _call(host_key, params, timeout=12):
    """调用开盘啦接口, 返回解析后的 dict; 失败返回 None(不抛异常)"""
    host = config.KPL_HOSTS.get(host_key, config.KPL_HOSTS["default"])
    common = {
        "PhoneOSNew": "1",
        "DeviceID": config.KPL_DEVICEID,
        "VerSion": "5.20.0.2",
        "Token": config.KPL_TOKEN,
        "UserID": config.KPL_USERID,
    }
    common.update(params)
    url = "https://" + host + "/w1/api/index.php?" + urllib.parse.urlencode(common)
    req = urllib.request.Request(url, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": config.KPL_UA,
    })
    t0 = time.time()
    # 分布式信号量(跨进程全局并发 3): 保护每日 80000 付费配额
    sem_key = store.acquire_sem("kpl", limit=3, timeout=timeout)
    if sem_key is None:
        _record(False)
        log.warning("KPL 并发信号量获取超时(限流) a=%s", params.get("a"))
        return None
    try:
        with _urlopen(req, timeout=timeout, context=_ssl_ctx) as r:
            body = r.read().decode("utf-8", "ignore")
        data = json.loads(body)
        _record(True, int((time.time() - t0) * 1000))
        if data.get("errcode") not in (None, "0"):
            log.warning("开盘啦接口返回异常 errcode=%s a=%s", data.get("errcode"), params.get("a"))
        return data
    except Exception as e:
        _record(False)
        log.warning("开盘啦调用失败 a=%s err=%s", params.get("a"), e)
        return None
    finally:
        store.release_lock(sem_key)


def _cached(key, ttl, loader):
    """带缓存的读取: TTL 内命中直接返回, 否则调 loader 刷新(跨进程共享)
    2026-09-04: 加 single-flight 防击穿 — 首屏 11 并发同刻 miss 时, 原实现 N 个线程
    同时 loader 打外网再抢 KPL sem(limit=3) 排队累积 1.7-2.0s → 现在只放行 1 个 loader"""
    from .cache_store import cached_singleflight
    return cached_singleflight(store, "kpl:" + key, ttl, loader)


# ==================== 竞价涨停委买额 ====================
def _parse_bid_seal(data):
    """MorningBiddingList Type=4: info [[code,name,现价,实时涨幅,涨停委买额,竞价涨幅,竞价净额,
    竞价换手,竞价成交额,20分后委买,?,板块,实际流通,?,?,主力净额,连板数], ...]"""
    info = data.get("info")
    if not isinstance(info, list):
        return []
    out = []
    for row in info:
        if not isinstance(row, list) or len(row) < 17:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "realChange": _f(row[3]),
                "bidSealAmt": _f(row[4]),        # 涨停委买额(元)
                "bidChange": _f(row[5]),
                "bidNetAmt": _f(row[6]),         # 竞价净额(元)
                "bidTurnover": _f(row[7]),       # 竞价换手(%)
                "bidAmt": _f(row[8]),            # 竞价成交额(元)
                "board": str(row[11]) if len(row) > 11 else "",
                "floatMv": _f(row[12]),          # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                "mainNet": _f(row[15]),          # 主力净额(元)
                "limitBoards": _lb(str(row[16])) if len(row) > 16 else 0,  # 连板数
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


def fetch_bid_seal():
    """竞价涨停委买额(实时, 9:15-9:30 有效)"""
    def loader():
        t0 = time.time()
        d = _call("default", {"Order": "1", "a": "MorningBiddingList", "st": config.KPL_BID_ST,
                              "c": "HomeDingPan", "Index": "0", "PidType": "0",
                              "apiv": "w41", "Type": "4"})
        lst = _parse_bid_seal(d) if d else None
        ms = int((time.time() - t0) * 1000)
        if lst is None:
            log.warning("竞价委买额(Type4)返回空/解析失败 耗时%dms", ms)
        else:
            log.info("竞价委买额(Type4)返回%d只 耗时%dms", len(lst), ms)
        return lst
    return _cached("bid_seal", config.KPL_BID_TTL, loader)


def fetch_bid_net():
    """竞价净额榜(实时): docs/112 竞价大于1000万 (MorningBiddingList, apphwshhq host + w44)
    (2026-08-18 主人确认: 净额数据原封不动用 doc112 接口; 晚间接口可能为空 → default/w41 双路兜底)
    结构同 Type=4: [code,name,现价,实时涨幅,?,竞价涨幅,竞价净额,竞价换手,竞价成交额,...]"""
    def loader():
        t0 = time.time()
        # 主路: doc112 官方定义 (after=apphwshhq + w44 + PidType=1)
        d = _call("after", {"Order": "1", "a": "MorningBiddingList", "st": "300",
                            "c": "HomeDingPan", "Index": "0", "PidType": "1",
                            "apiv": "w44", "Type": "2"})
        lst = _parse_bid_seal(d) if d else None
        if not lst:
            # 兜底: default host + w41 (doc115 涨停委买额同款 host 组合, 实测晚间有数据)
            d2 = _call("default", {"Order": "1", "a": "MorningBiddingList", "st": "300",
                                   "c": "HomeDingPan", "Index": "0", "PidType": "0",
                                   "apiv": "w41", "Type": "2"})
            lst = _parse_bid_seal(d2) if d2 else None
        ms = int((time.time() - t0) * 1000)
        if not lst:
            log.warning("竞价净额(doc112)返回空/解析失败 耗时%dms", ms)
        else:
            log.info("竞价净额(doc112>1000万)返回%d只 耗时%dms", len(lst), ms)
        return lst or []
    return _cached("bid_net", config.KPL_BID_TTL, loader)


def bid_net_from_snap(date=None):
    """2026-08-22 非竞价/非交易日回退: 用 9_25 快照重建竞价净额榜(全市场竞价金额>1000万),
    与 doc112(MorningBiddingList Type=2 全额>1000万)口径一致, 按竞价额降序。
    返回 [{code,name,bidAmt(元),bidChange,bidTurnover,bidNetAmt,floatMv,board}, ...]"""
    import sqlite3
    if date is None:
        date = time.strftime("%Y-%m-%d")
    conn = sqlite3.connect(config.DB_FILE)
    try:
        row = conn.execute(
            "SELECT MAX(time_point) FROM snapshot_bid WHERE date=? "
            "AND time_point IN ('9_15','9_20','9_24','9_25')", (date,)).fetchone()
        tp = str(row[0]) if row and row[0] else None
        if not tp:
            return []
        rows = conn.execute(
            # 🔴 2026-09-29 口径统一: 流通列取**实际流通**(free_mv), float_mv 仅兜底
            #   (与三时点榜 / fill_bid_turnover_from_snap / _boom_from_snap 一致)
            "SELECT code, name, bid_amt, bid_change, COALESCE(NULLIF(free_mv,0), float_mv), board "
            "FROM snapshot_bid WHERE date=? AND time_point=? AND bid_amt >= 1000 ORDER BY bid_amt DESC",
            (date, tp)).fetchall()
    finally:
        conn.close()
    out = []
    for _code, _name, _amt, _chg, _fmv, _board in rows:
        amt = _amt or 0
        fmv = _fmv or 0
        out.append({
            "code": str(_code),
            "name": _name or "",
            "bidAmt": amt * 10000,          # 万元 → 元
            "bidNetAmt": amt * 10000,
            "bidChange": _chg or 0,
            "bidTurnover": round(amt * 10000 / fmv * 100, 4) if fmv else 0.0,
            "floatMv": fmv,
            "board": _board or "",
        })
    log.info("竞价净额快照重建 %d 只 date=%s (9_%s)", len(out), date, tp)
    return out


def _boom_spot_map(codes=None):
    """竞价爆量 实时涨幅合并。
    注意: 不能用 ensure_cache("filter") — 那只有涨幅前 200 只, 量比榜多数票不在其中 → 0

    🔴 2026-09-29 P0③: 传 codes 时**按代码点查**(东财 ulist, 每批 60) —— 原实现无论
      多少只都拉全市场 5561 只(33 请求), 而调用方手里就有"过滤后存活的这批票"。
      codes 为空/点查失败 → 退回全市场 spot map(旧行为保留, 不降可用性)。"""
    try:
        from . import fetcher as _fetcher
        from . import scorer as _scorer
        if codes:
            m = _fetcher.fetch_spot_quote_map_by_codes(list(codes))
            if m:
                return m
            log.warning("竞价爆量 实时涨幅: 按code点查为空, 退回全市场 spot map(%d只)", len(codes))
        _fs = _scorer.market_fs(list(_scorer.ALL_MARKETS))
        return _fetcher.fetch_spot_quote_map(_fs)
    except Exception as e:
        log.warning("竞价爆量 实时涨幅合并失败(降级0) err=%s", e)
        return {}


def _boom_from_snap(snap_date, spot_map=None):
    """从某交易日 snapshot_bid 重建竞价爆量(量比榜)。2026-09-05 抽出, 供 实时加载器 与
    历史回看/非交易日重建 复用(历史 auction_daily_history 长期无 boom 落库 → 用快照重建)。
    过滤口径与 fetch_bid_boom 一致: 竞价量比 > 2 且 竞价成交额 > 100万(万元=100) 且 竞价涨幅≥0.01%。
    返回 [{code,name,bidAmt,bidChange,bidRatioYest,bidTurnover,floatMv,board,yestBidAmt},...] 按量比降序;
    当日无快照/无昨日 → []。"""
    import sqlite3
    conn = sqlite3.connect(config.DB_FILE)
    try:
        # 当日最新时点: 字典序 9_15 < 9_20 < 9_24 < 9_25, MAX 即最新
        row = conn.execute(
            "SELECT MAX(time_point) FROM snapshot_bid WHERE date=? "
            "AND time_point IN ('9_15','9_20','9_24','9_25')", (snap_date,)).fetchone()
        cur_tp = str(row[0]) if row and row[0] else None
        if not cur_tp:
            return []          # 该日暂无快照
        # 昨日(最近小于 snap_date 的**交易日**) 9_25 竞价额
        # 2026-09-27 v4.11.66: 加交易日历过滤 —— 原裸 MAX(date) 会把休市日(09-25 中秋)的
        # 幽灵行当成"昨日", 于是量比的分子分母都来自同一份静态值 ⇒ 量比恒 1.0。
        yest = _latest_trade_snap_date(snap_date, strict=True)
        if not yest:
            return []          # 无昨日数据
        # 当日全市场: code -> (bid_amt万元, name, bid_change, 实际流通市值free_mv, board)
        today_map = {}
        for code, amt, name, chg, fmv, board in conn.execute(
                "SELECT code, bid_amt, name, bid_change, COALESCE(NULLIF(free_mv,0), float_mv), board FROM snapshot_bid "
                "WHERE date=? AND time_point=?", (snap_date, cur_tp)):
            today_map[code] = (amt or 0, name or "", chg or 0, fmv or 0, board or "")
        # 昨日 9_25 竞价额(万元)
        ymap = {}
        for code, amt in conn.execute(
                "SELECT code, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_25'",
                (yest,)):
            ymap[code] = amt or 0
    finally:
        conn.close()
    out = []
    for code, (amt, name, chg, fmv, board) in today_map.items():
        ya = ymap.get(code)
        if not ya or amt <= 100:      # 竞价成交额 ≤ 100万(万元=100) 或 昨日无竞价 → 跳过
            continue
        if chg < 0.01:                # 竞价涨幅 < 0.01%(基本零涨幅/未上涨) → 跳过
            continue
        ratio = round(amt / ya, 2)
        if ratio <= 2:                # 竞价量比 ≤ 2 → 跳过
            continue
        bid_turnover = round(amt * 10000 / fmv * 100, 4) if fmv else 0.0   # 竞价换手 = 竞价额/流通市值×100
        out.append({"code": code, "name": name,
                    "realChange": 0.0,                # 占位: 下面按**存活代码**点查后回填
                    "bidChange": chg,
                    "bidAmt": amt * 10000,            # 万元 → 元(前端口径)
                    "bidRatioYest": ratio,            # 竞价量比
                    "bidTurnover": bid_turnover,      # 竞价换手(%)
                    "floatMv": fmv, "board": board,
                    "yestBidAmt": ya * 10000})        # 昨日竞价额(元)
    out.sort(key=lambda x: x["bidRatioYest"], reverse=True)
    # 🔴 2026-09-29 P0③: 现涨按**过滤后的存活代码**点查 —— 过滤只用快照字段(today_map/ymap),
    #   与现涨无关, 故"先过滤、再取现涨"安全; 原实现为它们拉全市场 5561 只(33 请求)。
    if spot_map is None:
        _codes = [r["code"] for r in out]
        spot_map = _boom_spot_map(_codes) if _codes else {}
    for r in out:
        # 实时涨幅(东财 map, 全天有值); 缺失保持 0.0(与改动前一致)
        r["realChange"] = (spot_map.get(r["code"]) or {}).get("realChange", 0.0)
    return out


def fetch_bid_boom():
    """竞价爆量榜(2026-08-19 主人要求改版):
    **按竞价量比排序(不限条数)** — 竞价量比 = 今日竞价额 / 昨日竞价额。
    全市场计算(不再只取 Type10 竞价额前 60): snapshot_bid 表
      - 今日竞价额: 当日最新时点(9_25 > 9_24 > 9_20 > 9_15)
      - 昨日竞价额: 最近(严格小于当日)交易日的 9_25 快照
    过滤(2026-08-19 23:10 主人要求): 竞价量比 > 2 且 竞价成交额 > 100万(万元=100)
    2026-09-05 非交易日(周末/节假日)/盘后今日无快照 → 自动回退最近交易日(与抢筹/bid-seal 一致)。
    返回 [{code,name,bidAmt(元),bidChange,bidRatioYest,floatMv,board}, ...] 按量比降序(全部)"""
    def loader():
        import sqlite3
        g2 = time.gmtime(time.time() + 8 * 3600)
        # 2026-09-27 v4.11.67: 先取**定格基准日**(非交易日 = 最近交易日), 再判竞价时段。
        # `fd == _bj_today()` 等价于"今天是交易日" ⇒ 法定休市日(如 09-25 中秋)的
        # 9:15-9:30 **不再**被当成竞价时段(否则会对休市日算爆量 ⇒ 表空)。
        fd = freeze_day()
        hm_in_bid = (fd == _bj_today()) and (9 * 60 + 15) <= (g2.tm_hour * 60 + g2.tm_min) <= (9 * 60 + 30)
        today = fd
        # 2026-09-05 修复"竞价爆量表格无数据": 非交易日(周末/节假日)/盘后今日无快照时,
        # 自动回退最近有快照的交易日 — 与 bid-seal/bid-net/抢筹 等 tab 盘后仍显示最近交易日一致
        # (竞价时段不回退, 避免 9:15-9:25 早段拿昨日冒充今日实时)
        if not hm_in_bid:
            try:
                conn = sqlite3.connect(config.DB_FILE)
                try:
                    has_today = conn.execute(
                        "SELECT COUNT(*) FROM snapshot_bid WHERE date=? ", (today,)).fetchone()[0]
                    if not has_today:
                        # 2026-09-27 v4.11.66: 加交易日历过滤。原裸 MAX(date) 会"回退"到休市日
                        # 幽灵快照(2026-09-25 中秋), 而幽灵日的竞价额恰是 09-24 的 9_25 复制值
                        # ⇒ 「今日÷昨日」量比恒等于 1.0 ⇒ 被「量比>2」全量滤掉 ⇒ 整个 tab 变空。
                        d0 = _latest_trade_snap_date(today)
                        if d0:
                            log.info("竞价爆量[回退] date=%s 今日无快照, 自动回退最近交易日 %s", today, d0)
                            today = str(d0)
                finally:
                    conn.close()
            except Exception as e:
                log.warning("竞价爆量 交易日回退判断失败(按今天处理) err=%s", e)
        out = _boom_from_snap(today)
        log.info("竞价爆量(量比榜) date=%s 全市场候选=%d (不限条数)", today, len(out))
        return out
    return _cached("bid_boom_ratio_v3", config.KPL_BID_TTL, loader)   # v3: 实时涨幅全市场map(2026-08-19)


def _parse_bid_boom(data):
    """Type=10 竞价爆量榜(实测字段, 2026-08-13 验证):
    [code,name,现价,实时涨幅,委买额(恒0),竞价涨幅,竞价净额,0,0,0,竞价成交额,
    板块,实际流通,主买,主卖,主力净额,连板]"""
    info = data.get("info")
    if not isinstance(info, list):
        return []
    out = []
    for row in info:
        if not isinstance(row, list) or len(row) < 17:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "realChange": _f(row[3]),
                "bidChange": _f(row[5]),
                "bidNetAmt": _f(row[6]),         # 竞价净额(元)
                "bidAmt": _f(row[10]),           # 竞价成交额(元) - 爆量主指标
                "board": str(row[11]) if len(row) > 11 else "",
                "floatMv": _f(row[12]),          # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                "mainBuy": _f(row[13]),
                "mainSell": _f(row[14]),
                "mainNet": _f(row[15]),
                "limitBoards": _lb(str(row[16])) if len(row) > 16 else 0,
                # 2026-08-18: Type=10 无换手列 → 竞价换手 = 竞价成交额/自由流通市值×100
                # (与开盘啦 Type4 bidTurnover 口径一致, 中石科技验算 2.96 vs 2.97)
                "bidTurnover": round(_f(row[10]) / _f(row[12]) * 100, 4) if _f(row[12]) else 0.0,
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


# ==================== 市场情绪 ====================
def fetch_market_breadth():
    """涨跌家数分布(2026-08-16): xuangubao rise_count,fall_count 分时曲线
    今日取最新一点, 昨日取同时刻最近一点(对比用)
    返回 {rise, fall, ts, day, yesterday: {rise, fall, ts, day}}; 失败 None"""
    def _latest(fields, date=None):
        rows = _flash_line(fields, date)
        if not rows:
            return None
        return rows[-1]   # 曲线按时间升序, 最后一条最新

    now_ts = int(time.time())
    today = _latest("rise_count,fall_count")
    if not today or today.get("rise_count") is None:
        return None
    # 昨日同时刻: 前一天日期, 取与 now_ts 最接近(不晚于)的点
    from datetime import datetime, timedelta
    ydate = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yest = _latest("rise_count,fall_count", ydate)
    y = None
    if yest:
        diff = abs(now_ts - int(yest.get("ts") or 0))
        y = {"rise": int(yest["rise_count"]), "fall": int(yest["fall_count"]),
             "ts": int(yest.get("ts") or 0), "day": ydate}
    return {
        "rise": int(today["rise_count"]),
        "fall": int(today["fall_count"]),
        "ts": int(today.get("ts") or now_ts),
        "day": (datetime.now()).strftime("%Y-%m-%d"),
        "yesterday": y,
    }


# ==================== 市场概览聚合(2026-09-04) ====================
# 生产实测(14:40-14:50 journald, uid=49): /api/kpl/market-brief 冷请求 1174ms,
# 是首屏最慢接口且此前**完全无结果缓存**。耗时拆解:
#   1) fetch_market_breadth() → _flash_line 两次 xuangubao 外网(无缓存, timeout=10s)
#   2) fetch_market_brief()   → 300s 缓存 miss 时拉全市场(5556 只, 秒级)
#   3) get_same_time_yesterday() + settings 读库
# 修复: 整段聚合结果走跨进程缓存(30s) + single-flight 防击穿 → 命中 <50ms;
#       数据低频(分时涨跌家数/两市成交额), 30s 新鲜度足够;
#       预热线程(_kpl_prewarm_once)每 12s 兜底刷新, 消除冷窗口。
# 注: 预热周期(12s) < TTL(30s) 时, 重算只跑第 1)3) 步 — 第 2) 步命中
#     fetch_market_brief 的 300s 跨进程缓存, 不会每 12s 拉全市场。
MARKET_BRIEF_TTL = 30
_MB_PAYLOAD_KEY = "market_brief_payload"


def build_market_brief_payload():
    """市场概览聚合(纯 dict, 不含 ok 字段): breadth/market/last_same_time/last/ts
    聚合逻辑下沉到 services 层, 供 api 与预热线程共用(预热在 services 层跑)"""
    from ..db import database
    from . import fetcher as _fetcher
    from . import settings as _settings_svc
    breadth = fetch_market_breadth()
    market = _fetcher.fetch_market_brief()
    last_same_time = _fetcher.get_same_time_yesterday()
    # 2026-09-13: 两市量能可切到开盘啦实时接口(a=MarketSCLN), 开关 market_vol_rt(默认 0=关)
    # 背景(主人反馈"相比昨日永远比的昨日总成交, 不是同一时点"): 原基准来自 worker 自存
    # 快照 market_brief_intraday_*, 有两个硬伤 ——
    #   ① 09:30 存在 amount=0 脏点 → 早盘匹配到它后增量虚高 7.7 倍
    #      (9/11 10:00 显示 +6631 亿, 正确应为 9/10 10:00 的 5750 亿 → +857 亿)
    #   ② 东财分页失败即**静默少算**(9/8 少 1491 亿 = -7.6%), 基准侧与今日侧都可能失真。
    # 实时接口一次请求即给「今日此刻 + 昨日同一时点」, 两侧同源同口径, 且不因东财失败缩水。
    # 🔴 2026-09-14 补正: 该接口 `zrtj` 系字段是**昨日全天**、`zrcs` 系才是**同一时点** ——
    #    9/13 初版把两者判反, 基准实际取了昨日**全天**量(虚高 6.6 倍); 9/14 盘中实测后已纠正,
    #    并加"同期>全天即丢弃"自检。字段语义细节见 parse_market_volume_rt docstring。
    # ⚠️ 取不到(amount<=0 / 网络失败 / 异常)时**完全回退**原值, 不改变任何行为。
    if _settings_svc.get("market_vol_rt", 0):
        try:
            vol = parse_market_volume_rt(fetch_kpl_market_scln())
        except Exception as e:
            vol = None
            log.warning("实时市场量能取数异常(回退自算) err=%s", e)
        # 2026-09-19(主人指令): 两市资金主数字改 MarketCapacityKLine(after 域名,
        # 与开盘啦 App「市场量能」页同源); 昨日同一时点基准/全天预测仍由 MarketSCLN
        # 提供(新接口无该字段); 两源各自独立回退, 互不影响。
        try:
            cap = parse_market_capacity(fetch_kpl_market_capacity())
        except Exception as e:
            cap = None
            log.warning("市场量能KLine取数异常(主数字回退 MarketSCLN/自算) err=%s", e)
        if cap or vol:
            market = dict(market or {})
            if cap:
                market["amount"] = cap["amount"]
                market["volSrc"] = "kpl_capacity"
            elif vol:
                market["amount"] = vol["amount"]
                market["volSrc"] = "kpl"
            if vol and vol.get("forecast_str"):
                market["volForecast"] = vol["forecast_str"]
            if vol and vol.get("prev_same_time"):
                last_same_time = {"amount": vol["prev_same_time"],
                                  "stockCount": (last_same_time or {}).get("stockCount"),
                                  "ts": vol.get("ts"),
                                  "date": (last_same_time or {}).get("date"),
                                  "src": "kpl"}
    last = None
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key='market_brief_last'").fetchone()
        conn.close()
        if row and row[0]:
            last = json.loads(row[0])
        # 2026-09-07: 15:30 后 last 已被**今日收盘**覆盖 → 与今日自比恒 0,
        # 此时改用 prev(上一交易日全天)作为"较昨日全天"基准。
        # ★ 2026-09-26 (v4.11.57): 判据统一收敛到 _mb_baseline_is_today()(交易日历语义),
        #   不再写「last.date == 字面今天」那种**靠巧合成立**的比较 —— 理由见该函数 docstring。
        _mb_warn_if_stale(last)
        if _mb_baseline_is_today(last):
            prev = _settings_svc.get("market_brief_prev")
            if prev:
                last = prev
    except Exception:
        last = None
    return {"breadth": breadth, "market": market,
            "last_same_time": last_same_time, "last": last,
            "ts": int(time.time())}


def fetch_market_brief_payload():
    """带跨进程缓存 + single-flight 的市场概览(api 与预热统一入口)
    预热用法: store.delete(_MB_PAYLOAD_KEY) 后再调本函数强制重算写缓存
    2026-09-07 健壮性: 瞬时故障(重启/东财分页失败)算出的 **amount<=0 坏值**会被缓存
    30s → 前端显示"两市资金 0 亿/放量 0 亿"。坏值不留存: 立刻清缓存, 下次请求重算。"""
    from .cache_store import cached_singleflight
    p = cached_singleflight(store, _MB_PAYLOAD_KEY, MARKET_BRIEF_TTL,
                            build_market_brief_payload)
    try:
        if p and ((p.get("market") or {}).get("amount") or 0) <= 0:
            store.delete(_MB_PAYLOAD_KEY)
            log.warning("market_brief 缓存为坏值(amount<=0), 已清除待下轮重算")
    except Exception:
        pass
    return p


def fetch_sentiment():
    """情绪值/连板高度: {ztjs 涨停家数, strong 情绪, lbgd 连板高度, df_num 大幅回撤}"""
    def loader():
        d = _call("market", {"a": "ChangeStatistics", "st": "10", "c": "HomeDingPan"})
        if not d:
            return None
        info = d.get("info")
        if isinstance(info, list) and info and isinstance(info[0], dict):
            r = info[0]
            # 跌停家数: doc35 zt_dt_line 涨跌停数曲线最后一条(2026-08-18 主人需求)
            dt_count = 0
            try:
                line = fetch_zt_dt_line()
                if line and isinstance(line[-1], dict):
                    dt_count = int(_num(line[-1].get("limit_down_count")) or 0)
            except Exception:
                pass
            return {
                "ztCount": int(_num(r.get("ztjs"))),      # 涨停家数
                "dtCount": dt_count,                       # 跌停家数(doc35 曲线最新值)
                "strong": int(_num(r.get("strong"))),     # 情绪指标(0-100)
                "lbgd": int(_num(r.get("lbgd"))),         # 连板高度
                "dfNum": int(_num(r.get("df_num"))),      # 大幅回撤
                "day": r.get("Day", ""),
                "tip": d.get("tip", ""),
            }
        return None
    return _cached("sentiment", config.KPL_SENTI_TTL, loader)


# ==================== 连板梯队 ====================
# PidType: 1=首板 2=二板 3=三板 4=四板 5=五板及以上
LADDER_LABEL = {1: "首板", 2: "二板", 3: "三板", 4: "四板", 5: "五板+"}


def _parse_ladder(data, pid_type):
    """DailyLimitPerformance: info [[row,...], ...]; row=[code,name,首次,原因,时间戳,板块,封单,
    最大封单,主力净额,主力买,主力卖,成交额,板块全,实际流通,实际换手,...,板块代码,涨停数量,今,振幅]"""
    info = data.get("info")
    if not isinstance(info, list):
        return []
    rows = info[0] if info and isinstance(info[0], list) else info
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 23:
            continue
        try:
            out.append({
                "code": str(row[0]),
                "name": str(row[1]),
                "reason": str(row[3]) if row[3] else "",
                "limitTime": int(_num(row[4])),           # 涨停时间戳
                "boardName": str(row[5]) if row[5] else "",
                "seal": _f(row[6]),                        # 封单(元)
                "maxSeal": _f(row[7]),                     # 最大封单(元)
                "mainNet": _f(row[8]),                     # 主力净额(元)
                "mainBuy": _f(row[9]),
                "mainSell": _f(row[10]),
                "amount": _f(row[11]),                     # 成交额(元)
                "concept": str(row[12]) if row[12] else "",
                "floatMv": _f(row[13]),                    # 自由流通市值(元) — 开盘啦"实际流通"字段≈自由流通
                "turnover": _f(row[14]),                   # 实际换手(%)
                "boardCode": str(row[19]) if len(row) > 19 else "",
                "ztCount": int(_num(row[20])) if len(row) > 20 else 0,
                "amplitude": _f(row[22]) if len(row) > 22 else 0,  # 振幅
                "ladder": pid_type,
                "ladderLabel": LADDER_LABEL.get(pid_type, ""),
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


def fetch_ladder(pid_type=1):
    """连板梯队(实时), pid_type 1~5"""
    def loader():
        d = _call("default", {"Order": "0", "a": "DailyLimitPerformance", "st": "300",
                              "c": "HomeDingPan", "Index": "0", "PidType": str(pid_type),
                              "apiv": "w39", "Type": "4"})
        return _parse_ladder(d, pid_type) if d else None
    return _cached("ladder_" + str(pid_type), config.KPL_LADDER_TTL, loader)


def fetch_ladder_all():
    """连板梯队全档(首板~五板+), 返回 {1:[...],2:[...],...}"""
    out = {}
    for pid in (1, 2, 3, 4, 5):
        out[pid] = fetch_ladder(pid) or []
    return out


# ==================== 连板梯队历史落库与回看 ====================
def _inject_real_limit_days(all_, date):
    """连板梯队 {1..5} 落库前注入东财涨停池真实连板数 limitUpDays(东财优先, pid 兜底)。

    根治: 连板天梯图此前依赖生成时实时拉 real_limit_days 注入, 盘后 15:30 东财偶发未就绪
    → 图回退成「五板+」(龙版传媒真实 6 板却显示 5 板)。落库即带真实板数, 图/历史回看
    不再依赖生成时实时拉取。deepcopy 每票, 不污染 fetch_ladder 的进程内缓存。"""
    try:
        real = real_limit_days(date)
    except Exception:
        real = {}
    out = {}
    for pid, lst in (all_ or {}).items():
        out[pid] = []
        for it in (lst or []):
            c = dict(it)
            code = str(c.get("code", ""))
            lu = int(real.get(code) or 0)
            # 东财真实连板数优先; 缺失则用开盘啦 pid 档位(≥5 即五板+下限)兜底
            c["limitUpDays"] = max(int(pid), lu) if lu >= 1 else int(pid)
            out[pid].append(c)
    return out


def save_ladder_history(date):
    """抓取当日连板梯队全档落库 ladder_history(覆盖式), 返回总条数(失败 0)。
    2026-09-07 根治: 落库即注入东财真实连板数(见 _inject_real_limit_days), 连板天梯图/
    历史回看不再依赖图生成时实时拉取 —— 避免盘后 15:30 东财偶发未就绪导致图回退五板+。"""
    all_ = _inject_real_limit_days(fetch_ladder_all(), date)
    total = sum(len(v or []) for v in all_.values())
    if total == 0:
        return 0
    from ..db import database
    conn = database.get_conn()
    for pid, lst in all_.items():
        conn.execute(
            "INSERT OR REPLACE INTO ladder_history (date, pid_type, list, ts) VALUES (?,?,?,?)",
            (date, pid, json.dumps(lst, ensure_ascii=False), int(time.time())))
    conn.commit()
    conn.close()
    return total


def query_ladder_history(date):
    """按日期回看连板梯队, 返回 {1:[...],2:[...],...}(无数据返回空 dict)"""
    from ..db import database
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT pid_type, list FROM ladder_history WHERE date=?", (date,)).fetchall()
    conn.close()
    out = {}
    for pid, raw in rows:
        try:
            out[int(pid)] = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            out[int(pid)] = []
    return out


# ==================== 涨停梯队一期聚合(2026-09-20) ====================
def _promote_rate(ladders_today):
    """晋级率: 今日N板家数 / 昨日(N-1)板家数。昨日取 ladder_history 最近非今日日期。"""
    from ..db import database
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    conn = database.get_conn()
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM ladder_history WHERE date < ? "
            "ORDER BY date DESC LIMIT 30", (today,)).fetchall()]
    finally:
        conn.close()
    # 2026-09-27 v4.11.66: 加交易日历过滤 —— 原裸 `ORDER BY date DESC LIMIT 1` 会把休市日
    # (2026-09-25 中秋)的幽灵行当"昨日"; 无合规候选时**保留原值**(fail-open, 不主动置空)
    yest = trade_calendar.latest_trade_in(dates, today) or (dates[0] if dates else None)

    def _today_n(lu):
        return len(ladders_today.get(lu) or [])

    if not yest:
        return {"date": None, "r1to2": None, "r2to3": None, "r3to4": None, "overall": None}

    y = query_ladder_history(yest) or {}
    y_cnt = {}
    for pid, lst in y.items():
        for it in (lst or []):
            lu = int(it.get("limitUpDays") or pid)
            y_cnt[lu] = y_cnt.get(lu, 0) + 1

    def _y_n(lu):
        return y_cnt.get(lu, 0)

    def _pct(a, b):
        return round(a / b, 4) if b else None

    return {
        "date": yest,
        "r1to2": _pct(_today_n(2), _y_n(1)),
        "r2to3": _pct(_today_n(3), _y_n(2)),
        "r3to4": _pct(_today_n(4), _y_n(3)),
        "overall": _pct(_today_n(2) + _today_n(3) + _today_n(4),
                        _y_n(1) + _y_n(2) + _y_n(3)),
    }


def build_zt_echelon():
    """涨停梯队一期聚合: 顶部统计 + 晋级率 + 分层梯队(按真实连板数降序) + 题材分组。

    复用 fetch_ladder_all(开盘啦实时) + ladder_history(昨日梯队算晋级率)。
    一期不含龙头星级/分歧预期等开盘啦私有标签(接口未提供)。
    """
    all_ = fetch_ladder_all()
    # rebin 到真实连板数(东财涨停池 limitUpDays), 6板以上不再塞五板+档
    rebin = rebin_ladder(all_, time.strftime("%Y-%m-%d")) or {}
    stocks = []
    for lu in (8, 7, 6, 5, 4, 3, 2, 1):
        for it in (rebin.get(lu) or []):
            c = dict(it)
            c["limitUpDays"] = lu
            stocks.append(c)

    # 按真实连板数分层(降序)
    ladders = {}
    for c in stocks:
        lu = c["limitUpDays"]
        ladders.setdefault(lu, []).append(c)

    max_lu = max(ladders) if ladders else 0
    space_dragon = ""
    if max_lu:
        top = sorted(ladders[max_lu], key=lambda x: float(x.get("seal") or 0), reverse=True)
        space_dragon = top[0].get("name", "") if top else ""

    stat = {"ztCount": len(stocks), "maxLadder": max_lu, "spaceDragon": space_dragon}
    promote = _promote_rate(ladders)

    ladder_list = [{"ladder": k, "count": len(v), "stocks": v}
                   for k, v in sorted(ladders.items(), reverse=True)]

    # 题材分组(取首题材)
    board_map = {}
    for c in stocks:
        b = (c.get("boardName") or c.get("concept") or "").strip()
        b = (b.split("、")[0].split(",")[0].strip() if b else "其他")
        g = board_map.setdefault(b, {"name": b, "count": 0, "maxLadder": 0})
        g["count"] += 1
        g["maxLadder"] = max(g["maxLadder"], c["limitUpDays"])
    boards = sorted(board_map.values(), key=lambda x: (-x["maxLadder"], -x["count"]))
    if boards:
        boards[0]["main"] = True    # 主线 = 最高板题材(家数/高度综合第一)

    return {"stat": stat, "promote": promote, "ladders": ladder_list, "boards": boards}


# ==================== 涨停原因 ====================
def fetch_zt_reason(code):
    """个股当天/历史涨停原因: 返回 [{date, reason, sclt(龙一龙二), boom}, ...]"""
    def loader():
        d = _call("market", {"a": "GetKLineZhangTing", "apiv": "w24",
                             "c": "StockLineData", "StockID": str(code)})
        if not d:
            return None
        lst = d.get("List")
        if not isinstance(lst, list):
            return []
        out = []
        for it in lst:
            if isinstance(it, dict):
                out.append({
                    "date": it.get("Date", ""),
                    "reason": it.get("Reason", "") or it.get("GNSM", ""),
                    "sclt": it.get("SCLT", ""),        # 日内龙一/龙二
                    "boom": it.get("Boom_ZS", ""),
                })
        return out
    return _cached("zt_reason_" + str(code), 300, loader)


# ==================== 板块强度排行 ====================
def _parse_board_rank(data):
    """RealRankingInfo: list [[板块代码,名称,强度,涨幅,涨速,成交额,主力净额,主买,主卖,量比,
    流通值,300万大单,?,总市值,机构增仓,今PE,明PE,强度,涨幅], ...]"""
    lst = data.get("list")
    if not isinstance(lst, list):
        return []
    out = []
    for row in lst:
        if not isinstance(row, list) or len(row) < 17:
            continue
        try:
            out.append({
                "boardCode": str(row[0]),
                "name": str(row[1]),
                "strength": _f(row[2]),        # 强度
                "change": _f(row[3]),          # 涨幅(%)
                "speed": _f(row[4]),           # 涨速(%)
                "amount": _f(row[5]),          # 成交额(元)
                "mainNet": _f(row[6]),         # 主力净额(元)
                "mainBuy": _f(row[7]),
                "mainSell": _f(row[8]),
                "volRatio": _f(row[9]),        # 量比
                "floatMv": _f(row[10]),        # 流通值(元)
                "totalMv": _f(row[13]),        # 总市值(元)
                "instAdd": _f(row[14]),        # 机构增仓(元)
                "peNow": _f(row[15]),          # 今PE
                "peNext": _f(row[16]),         # 明PE
            })
        except (IndexError, ValueError, TypeError):
            continue
    return out


def fetch_board_rank():
    """板块强度排行(实时)"""
    def loader():
        d = _call("market", {"Order": "1", "a": "RealRankingInfo", "st": "60",
                             "apiv": "w26", "Type": "1", "c": "ZhiShuRanking",
                             "Index": "0", "ZSType": "7"})
        return _parse_board_rank(d) if d else None
    return _cached("board_rank", config.KPL_BOARD_TTL, loader)


def fetch_board_rank_by_date(date):
    """精选板块列表-历史(doc42 apiv=w41, apphis host):
    按 Date='YYYY-MM-DD' 取指定交易日 9:25-15:00 期间的板块强度 Top60
    实测保留期=最近 3 个交易日(超出日期返回空)
    返回 [{boardCode, name, strength, change, amount, mainNet, volRatio, floatMv, ...}]"""
    d = _call("his", {"Order": "1", "a": "RealRankingInfo", "st": "60", "apiv": "w41",
                      "c": "ZhiShuRanking", "PhoneOSNew": "1",
                      "Start": "0925", "VerSion": "5.20.0.2", "End": "1500",
                      "Date": date, "Type": "5", "ZSType": "7"})
    return _parse_board_rank(d) if d else []


def fetch_board_stocks(plate_id, date=None, st=30):
    """板块成分股(开盘啦 doc46 ZhiShuStockList_W8, **apphis host + apiv=w41**):
    板块强度点开看成分股.
    PlateID: 板块代码(如 801001 芯片); date: 'YYYY-MM-DD' 历史.
    2026-08-18 修复: 原实现用 apphwshhq+w44 实时模式(无 Date) — 当日数据冻结前开盘啦一律返回
    errcode=1020(实时模式被拒), 历史模式(带 Date)正常. 改走 apphis+w41 且**始终带 Date**:
    实时模式自动带上一交易日(当天未冻结前取最近交易日成分股, 消除空白).
    实测 30 条(按强度/涨幅排序); 返回 [{code, name, concept, price, change, turnover, amount,
    floatMv, mainNet, volRatio, limitTag, ladder, totalMv}, ...].
    st: 返回条数上限(默认 30; 2026-08-18 加: 昨涨停/昨断板成分需全量, 传 500)

    🔴 2026-09-28 v4.11.79 加缓存(此前**无任何缓存**):
      前端本轮给「板块题材」右栏成分股加了 60s 轮询 ⇒ 若不加缓存, 每个客户端
      盘中约 330 次/日 × N 客户端, 会线性吃开盘啦 **8 万/日付费配额**。
      按本仓既有 `_cached` 范式加 TTL(见 broken_zt 同款):
        · 实时(st 未指定日期) → KPL_BOARD_STOCKS_TTL(默认 30s), 与 KPL_BOARD_TTL 对齐,
          左右栏同频刷新, 不会"左边新右边旧"
        · 历史(date 显式指定) → KPL_BOARD_STOCKS_HIST_TTL(默认 1800s) —— 历史数据
          **永不变化**, 长 TTL 避免反复回读同一历史日
      ⚠️ `is_hist` 必须在**进 live 分支前**从入参 date 判定 —— live 分支会把 date
         改写成"上一交易日"(回退用), 之后再判会把实时请求误当历史、缓存半小时。
      ⚠️ 缓存 key 必须含 st —— 同板块不同 st(30 看成分 / 500 看全量)是不同结果集,
         不含 st 会让 500 的全量结果污染 30 的展示(或反之)。
    """
    is_hist = bool(date)
    cache_key = ("board_stocks_" + str(plate_id)
                 + "_" + str(st)
                 + (("_" + str(date).replace("-", "")) if date else ""))
    req_date = date        # 入参快照: live 分支会改写局部 date(回退用), 故此处先固化

    def loader():
        base = {
            "Order": "1", "a": "ZhiShuStockList_W8", "st": str(st),
            "c": "ZhiShuRanking", "PhoneOSNew": "1",
            "IsZZ": "0", "Index": "0", "RStart": "0925", "REnd": "1500",
            "Type": "5", "IsKZZType": "0",
            "PlateID": str(plate_id), "TSZB": "0", "TSZB_Type": "0",
        }
        date = req_date
        if not date:
            # 盘中优先用实时接口(apphwshhq + w44, 不带Date)取当日数据;
            # 实时接口被拒或空时回退历史接口(apphis + w41 + 上一交易日Date)
            # 2026-08-30 修复: _call host_key "app" 不存在 → fallback default(apphwhq 竞价域名),
            #   对板块成分股返回空导致盘中一直回退昨日; 实时 host 应为 "after"(apphwshhq)
            params = dict(base, apiv="w44")
            d = _call("after", params)
            lst = d.get("list") if isinstance(d, dict) else None
            if not isinstance(lst, list) or not lst:
                date = _prev_trade_day()
                params = dict(base, apiv="w41", Date=date)
                d = _call("his", params)
                lst = d.get("list") if isinstance(d, dict) else None
        else:
            params = dict(base, apiv="w41", Date=date)
            d = _call("his", params)
            lst = d.get("list") if isinstance(d, dict) else None
        if not isinstance(lst, list):
            return []
        out = []
        for row in lst:
            if not isinstance(row, list) or len(row) < 12:
                continue
            try:
                out.append({
                    "code": str(row[0]),
                    "name": str(row[1]),
                    "concept": str(row[4] or ""),
                    # 字段对照(2026-08-17 东财交叉验证):
                    # [5]=最新价(元), [6]=涨跌幅%(20% 涨停板验证: 华民19.95/奥来德19.99/聚和20.01)
                    # [21]=量比(2.31=东财f10), [25]=换手率%(26.91=东财f8)
                    "price": _f(row[5]),          # 最新价(元)
                    "change": _f(row[6]),         # 涨跌幅(%)
                    "amount": _f(row[7]),         # 成交额(元)
                    "floatMv": _f(row[10]),       # 流通市值(元)
                    "mainNet": _f(row[11]),       # 主力净额(元)
                    "volRatio": _f(row[21]) if len(row) > 21 else 0,   # 量比
                    "limitTag": str(row[23] or "") if len(row) > 23 else "",   # 首板/连板标识
                    "ladder": str(row[24] or "") if len(row) > 24 else "",     # 龙一/龙二等梯队
                    "turnover": _f(row[25]) if len(row) > 25 else 0,          # 换手率(%)
                    "totalMv": _f(row[38]) if len(row) > 38 else 0,           # 总市值(元)
                })
            except (IndexError, ValueError, TypeError):
                continue
        return out

    ttl = config.KPL_BOARD_STOCKS_HIST_TTL if is_hist else config.KPL_BOARD_STOCKS_TTL
    return _cached(cache_key, ttl, loader)


# ==================== 尾盘竞价抢筹 ====================
def fetch_wpqc():
    """尾盘竞价抢筹(14:57 后): List [[code,name,资金标签,类型,概念,涨跌幅,抢筹委托,收盘金额,
    抢筹买,抢筹卖,抢筹净额,大单买,大单卖,连板数,涨停标识,抢筹涨幅,抢筹强度], ...]"""
    def loader():
        d = _call("after", {"Order": "1", "st": "30", "a": "GetWPQC", "Index": "0",
                            "apiv": "w44", "Type": "1"})
        if not d:
            return None
        lst = d.get("List")
        if not isinstance(lst, list):
            return []
        out = []
        for row in lst:
            if not isinstance(row, list) or len(row) < 17:
                continue
            try:
                out.append({
                    "code": str(row[0]),
                    "name": str(row[1]),
                    "concept": str(row[4]) if row[4] else "",
                    "change": _f(row[5]),           # 涨跌幅(%)
                    "qcAmt": _f(row[6]),            # 抢筹委托金额(元)
                    "qcBuy": _f(row[8]),
                    "qcSell": _f(row[9]),
                    "qcNet": _f(row[10]),           # 抢筹净额(元)
                    "limitBoards": int(_num(row[13])) if len(row) > 13 else 0,
                    "qcChange": _f(row[15]) if len(row) > 15 else 0,   # 抢筹涨幅(%)
                    "qcStrength": _f(row[16]) if len(row) > 16 else 0, # 抢筹强度
                })
            except (IndexError, ValueError, TypeError):
                continue
        return out
    return _cached("wpqc", 30, loader)


# ==================== 人气热榜 ====================
def fetch_hot_rank():
    """盘中人气热榜: List [[code,name,涨跌幅,?,排名,?,?], ...]"""
    def loader():
        d = _call("market", {"Order": "1", "a": "GetHotPHB", "st": "50",
                             "apiv": "w29", "Type": "1", "c": "StockBidYiDong"})
        if not d:
            return None
        lst = d.get("List")
        if not isinstance(lst, list):
            return []
        out = []
        for row in lst:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                out.append({
                    "code": str(row[0]),
                    "name": str(row[1]),
                    "change": _f(row[2]),        # 涨跌幅(%)
                    "rank": int(_num(row[4])),   # 人气排名
                })
            except (IndexError, ValueError, TypeError):
                continue
        return out
    return _cached("hot_rank", 60, loader)


# ==================== 龙虎榜 ====================
def fetch_lhb(date=""):
    """龙虎榜上榜股票(当天/指定历史日期): [{code,name,change,limitBoards,buyIn,amount,floatMv,turnover,amplitude,totalMv,joinNum}, ...]
    date: 空=当天; 'YYYY-MM-DD' 查历史(实测 Time 参数支持历史)"""
    def loader():
        d = _call("lhb", {"a": "GetStockList", "st": "500", "c": "LongHuBang",
                          "Time": date, "Index": "0", "apiv": "w44", "Type": "2"})
        if not d:
            return None
        lst = d.get("list")
        if not isinstance(lst, list):
            return []
        out = []
        for it in lst:
            if not isinstance(it, dict):
                continue
            out.append({
                "code": str(it.get("ID", "")),
                "name": str(it.get("Name", "")),
                "change": _pct(it.get("IncreaseAmount")),   # 涨幅(%)
                "limitBoards": int(_num(it.get("D3"))),     # 连板数
                "buyIn": _f(it.get("BuyIn")),               # 买入金额(元)
                "joinNum": int(_num(it.get("JoinNum"))),    # 上榜营业部数
                "amount": _f(it.get("Turnover")),           # 成交额(元)
                "floatMv": _f(it.get("CircPrice")),         # 流通市值(元)
                "amplitude": _f(it.get("Amplitude")),       # 振幅(%)
                "turnover": _f(it.get("TurnoverRatio")),    # 换手率(%)
                "totalMv": _f(it.get("Capitalization")),    # 总市值(元)
            })
        return out
    return _cached("lhb:" + (date or "today"), 120, loader)


# 知名游资关键词（★ 2026-09-28 从前端 `LhbPanel.vue` 的 `HOT_KEYS` **搬到后端** —— 判定必须
#   只有一份口径，否则前后端各一套必然漂移）。
# 为什么不能只靠上游字段：明细行里虽有 `YouZiIcon`/`GroupID`，但实测**整批为 0/空**
#   （上游只给自己收录的那批游资打标）⇒ 单用它会漏掉绝大多数知名游资席。
#   ⚠️ 这份关键词表偏宽（含「宁波 / 温州 / 量化 / 深圳分公司」这类地名与泛称），属于"宁多勿漏"：
#      「知名游资」tab 里出现的席位，请以名字自行判断成色。
_HOT_KEYWORDS = ('章盟主', '方新侠', '炒股养家', '作手新一', '桑田路', '呼家楼', '上塘路', '柯桥',
                 '解放北', '溧阳路', '江苏路', '益田路', '银河绍兴', '宁波', '台州', '温州',
                 '深圳分公司', '上海分公司', '量化')


def _seat_flags(x):
    """席位行的机构/游资判定（★ 2026-09-28 上移到后端；前端原先靠关键词表猜）。

    · 机构：席位名含「机构专用」—— 开盘啦的标准机构席位名（实测 ID=896、YouZiIcon=0）。
    · 游资：上游 `YouZiIcon==1` 或 `GroupID` 非空（若上游标了就用它）**并集**关键词表兜底
      （上游实测常整批不标，见 `_HOT_KEYWORDS` 注释）。
    """
    name = str((x or {}).get("Name") or "")
    inst = "机构专用" in name
    if inst:
        return inst, False
    hot = (int(_num(x.get("YouZiIcon")) or 0) == 1
           or bool(str(x.get("GroupID") or "").strip())
           or any(k in name for k in _HOT_KEYWORDS))
    return inst, hot


def _lhb_seats(lst):
    """把上游席位列表转成 [{name,buy,sell,inst,hot}]（机构/游资标记由上游字段判定）。"""
    out = []
    for x in (lst or []):
        if not isinstance(x, dict):
            continue
        inst, hot = _seat_flags(x)
        out.append({"name": str(x.get("Name", "")), "buy": _f(x.get("Buy")),
                    "sell": _f(x.get("Sell")), "inst": inst, "hot": hot})
    return out


def fetch_lhb_detail(code, date=""):
    """龙虎榜个股营业部明细: {name,time,change,limitBoards,buyTotal,sellTotal,upReason,
    buyList:[{name,buy,sell}], sellList:[{name,buy,sell}]}"""
    def loader():
        d = _call("lhb", {"c": "Stock", "a": "GetNewOneStockInfo", "Type": "0",
                          "Time": date, "StockID": str(code)})
        if not d:
            return None
        lst = d.get("List")
        item = {}
        if isinstance(lst, list) and lst and isinstance(lst[0], dict):
            item = lst[0]
        return {
            "name": str(d.get("Name", "")),
            "time": str(d.get("Time", "")),
            "change": _pct(d.get("QuoteChange")),
            "limitBoards": int(_num(d.get("lbnum"))),
            "buyIn": _f(d.get("BuyIn")),
            "amount": _f(d.get("Turnover")),
            "turnover": _f(d.get("TurnoverRatio")),
            "buyTotal": _f(item.get("BuyTotal")),
            "sellTotal": _f(item.get("SellTotal")),
            "upReason": str(item.get("UpReason", "") or ""),
            "buyList": _lhb_seats(item.get("BuyList")),
            "sellList": _lhb_seats(item.get("SellList")),
        }
    return _cached("lhb_detail_" + str(code) + "_" + str(date), 300, loader)


def fetch_lhb_tags(codes, date=""):
    """龙虎榜「机构/游资」汇总: `{code: {inst, hot, instNet, hotNet}}`（金额单位: 元）。

    ★ 2026-09-28 新增。为什么必须这么做：列表接口(doc100)的原始字段只有 11 个
      （ID/Name/IncreaseAmount/D3/BuyIn/JoinNum/Turnover/CircPrice/Amplitude/TurnoverRatio/
      Capitalization），**完全没有机构/游资线索** ⇒ 只能按代码取席位明细(doc101)再汇总。
    ★ 为什么不并进 `/api/kpl/lhb`：55 只逐个取明细会把这页首屏拖慢数秒，而这两个标签
      只在「机构席位 / 知名游资」两个 tab 里才用得到 ⇒ 做成独立端点由前端**按需**调用。
    ★ 成本：明细自身有 300s 缓存；本函数再叠 10 分钟缓存（键含代码集合指纹）⇒ 同一批代码
      10 分钟内只打一轮上游；并发度 6（`_call` 内部还有 KPL 信号量限流）。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}
    import hashlib as _hashlib
    from concurrent.futures import ThreadPoolExecutor
    fp = _hashlib.md5(",".join(sorted(codes)).encode("utf-8")).hexdigest()[:10]

    def loader():
        out = {}

        def one(code):
            try:
                d = fetch_lhb_detail(code, date) or {}
            except Exception:                                  # noqa: BLE001
                return None
            inst = hot = False
            inst_net = hot_net = 0.0
            for x in (d.get("buyList") or []) + (d.get("sellList") or []):
                net = float(x.get("buy") or 0) - float(x.get("sell") or 0)
                if x.get("inst"):
                    inst = True
                    inst_net += net
                if x.get("hot"):
                    hot = True
                    hot_net += net
            return code, {"inst": inst, "hot": hot,
                          "instNet": round(inst_net, 2), "hotNet": round(hot_net, 2)}

        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="lhb-tag") as ex:
            for r in ex.map(one, codes):
                if r:
                    out[r[0]] = r[1]
        return out

    return _cached("lhb_tags:" + str(date or "today") + ":" + fp, 600, loader) or {}


# ==================== 昨日涨停今表现(策略验证) ====================
def fetch_yesterday_perf():
    """昨日涨停/连板/破板今日平均表现: {zt:{change,net,date}, lb:{...}, pb:{...}}
    List[4]=平均涨幅(%), List[3]=主力净额(元)。用于验证"剔除昨日涨停"策略合理性。"""
    def loader():
        out = {}
        for pid, key in [("801900", "zt"), ("801901", "lb"), ("801902", "pb")]:
            d = _call("after", {"a": "GetPlate_Info_QJ", "apiv": "w42",
                                "c": "ZhiShuRanking", "PlateID": pid, "Date": ""})
            if not d:
                continue
            lst = d.get("List")
            if isinstance(lst, list) and len(lst) >= 5:
                out[key] = {
                    "change": _f(lst[4]),   # 今日平均涨幅(%)
                    "net": _f(lst[3]),      # 主力净额(元)
                    "date": d.get("Date", ""),
                }
        return out
    return _cached("yesterday_perf", 300, loader)


# ==================== 炸板/涨停池(选股宝 flash 公开接口, 无需 Token) ====================
# 2026-09-13 修正: 注释原写「东财 flash」, 实际域名 flash-api.xuangubao.cn = **选股宝**。
#   写错源名会直接误导排障(按东财的风控口径去查, 得出错误结论)。
#   另一坑: 该接口当日数据在 **15:50 之后才发布**(生产实证 8/27 当天 15:25/15:37/15:47
#   三次抓取均「池为空」) → 采集窗口必须晚于该时刻, 见 stock_temper.BACKFILL_AT。
#
# 2026-09-28 新增缓存(`_flash_pool` 改为带缓存; 原实现见 `_flash_pool_raw`):
#   生产实测:「龙虎榜」实时路径里 `fill_reason_from_pool` 单步 119ms —— 每请求都重拉一次
#   今日涨停池, 而它**只用到 reason 一个字段**; 全站 16 处调用点绝大多数也在裸打选股宝。
#   缓存策略:
#     · 今日池 60s —— 与既有 `real_limit_days` 同口径(其注释: "梯队页每分钟轮询,
#       避免每轮都打东财")。结合上一条"当日数据 15:50 后才发布": 盘中该池本就是未发布态,
#       15:50 后即定型 ⇒ 60s 陈旧度对任何消费方都不构成语义变化。
#     · 历史池 6h —— 历史数据不可变。
#   ★ 「失败」与「有效空池」必须区分: 请求异常/结构异常 → `_flash_pool_raw` 返回 None
#     → `_cached` 不缓存, 下次请求重试(仓内既有约定, 见本文件末尾注释); 而**有效空池**
#     返回 [] 并照常缓存 60s —— 否则盘中"当日数据未发布"这一**合法**空结果会被每请求重打上游。
_FLASH_BASE = "https://flash-api.xuangubao.cn/api/pool/detail?pool_name="


def _flash_pool_raw(pool_name, date=None):
    """选股宝 flash 池**原始请求(不缓存)**: pool_name=limit_up_broken/limit_up_pool 等,
    date 可选(YYYY-MM-DD)。返回 [{code,name,change,limitUpDays,breakTimes,reason,...}, ...];
    **失败/结构异常返回 None**(与"有效空池 []"区分, 由缓存层决定是否缓存)。"""
    url = _FLASH_BASE + pool_name + (("&date=" + date) if date else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("flash 池请求失败 pool=%s date=%s err=%s", pool_name, date or "-", e)
        return None
    lst = d.get("data")
    if not isinstance(lst, list):
        return None
    out = []
    for it in lst:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol", "") or "")
        out.append({
            "code": sym.split(".")[0],
            "name": str(it.get("stock_chi_name", "") or ""),
            "change": _f(it.get("change_percent")) * 100,
            "limitUpDays": int(_num(it.get("limit_up_days"))),
            "breakTimes": int(_num(it.get("break_limit_up_times"))),
            "firstLimitUp": int(_num(it.get("first_limit_up"))),
            "firstBreak": int(_num(it.get("first_break_limit_up"))),
            "reason": _surge_reason(it.get("surge_reason")),
            "day": date or time.strftime("%Y-%m-%d"),
        })
    return out


def _flash_pool(pool_name, date=None):
    """选股宝 flash 池(**带缓存, 一律走这里**): 今日池 60s / 历史池 6h; 失败返回 []

    缓存策略与安全性论证见上方 2026-09-28 注释。返回值契约与改造前一致(失败/无数据 → []),
    调用方无需改动; `real_limit_days` 自身那层 60s 缓存保留(重复命中无害)。
    """
    key = "flash_%s_%s" % (pool_name, date or "today")
    ttl = 6 * 3600 if (date and date != _bj_today()) else 60
    return _cached(key, ttl, lambda: _flash_pool_raw(pool_name, date)) or []


def real_limit_days(date):
    """当日涨停池(封住)每只股票的真实连板数 {code: limitUpDays}。
    用于连板天梯图/梯队表: 开盘啦连板梯队 pid 只分到'五板+'(≥5), 无法区分 6 板以上;
    用东财 flash 涨停池的 limit_up_days 取真实连板数, 修正显示的连板与顶部最高连板。
    60s 缓存(梯队页每分钟轮询, 避免每轮都打东财)。失败/为空返回 {}(调用方回退到 pid 档位)。"""
    def loader():
        m = {}
        for it in _flash_pool("limit_up_pool", date):
            lu = int(it.get("limitUpDays") or 0)
            if lu >= 1:
                m[it.get("code")] = lu
        return m
    return _cached("real_lb_" + date.replace("-", ""), 60, loader) or {}


def rebin_ladder(d, date):
    """连板梯队 {1..5} → {1..8}: 用东财涨停池真实连板数把五板+ 拆分为 5/6/7/8+ 档。
    真实连板 = max(pid, 东财 limitUpDays), ≥8 归「八板+」; 东财缺失的股票保持 pid 档位
    (以开盘啦梯队为准, 不新增/不删股)。东财数据源失败(real_limit_days 空)时原样返回,
    前端兼容 5 档结构(第 5 档标签回退「五板+」)。"""
    real = real_limit_days(date)
    if not real:
        return d
    out = {t: [] for t in (1, 2, 3, 4, 5, 6, 7, 8)}
    for pid, lst in (d or {}).items():
        for it in (lst or []):
            try:
                code = str(it.get("code", ""))
                zt = max(int(pid), int(real.get(code) or 0))
            except (TypeError, ValueError):
                zt = int(pid)
            out[min(zt, 8)].append(it)
    return out


def _prev_cal_days(n, end_date=None):
    """返回 end_date(缺省今天) 往前 n 个自然日(跳过周末)的日期列表, 不含 end_date 本身。
    用于涨停池历史回溯(法定节假日由日期较近的交易日对齐容忍)。"""
    from datetime import datetime, timedelta
    d = datetime.strptime(end_date or time.strftime("%Y-%m-%d"), "%Y-%m-%d")
    out = []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
    return out


def fetch_fanbao_stocks(date=None):
    """断板反包检测(东财 flash 涨停池):
    当日涨停池中 limitUpDays==1(今日重新起板) 且 昨日不在涨停池(断板)
    且 近 5 个交易日内曾涨停(有涨停史, 反包而非新首板) 的股票。
    返回 [{code,name,reason,change,day}, ...]; 数据源失败返回 []。
    用途: 连板天梯图「断板反包」区, 复盘一眼识别反包梯队。"""
    day = date or time.strftime("%Y-%m-%d")

    def loader():
        today = _flash_pool("limit_up_pool", day)
        if not today:
            return []
        prev = _prev_cal_days(6, day)          # 往前 6 自然日 ≈ 4 个交易日
        pools = {}
        for d in [day] + prev[:4]:             # 当日 + 近 4 个交易日(约 5 个自然日窗口)
            pools[d] = {x["code"] for x in _flash_pool("limit_up_pool", d)}
        yest_codes = pools.get(prev[0], set()) if prev else set()
        hist_codes = set()
        for d in prev[1:4]:                    # 更早 2~4 个交易日的涨停池并集
            hist_codes |= pools.get(d, set())
        out = []
        for it in today:
            if (it.get("limitUpDays") == 1 and it.get("code") not in yest_codes
                    and it.get("code") in hist_codes):
                out.append({
                    "code": it.get("code", ""),
                    "name": it.get("name", ""),
                    "reason": it.get("reason", ""),
                    "change": it.get("change", 0),
                    "day": day,
                })
        return out

    return _cached("fanbao_" + day.replace("-", ""), 30 * 60, loader) or []



# ==================== xuangubao 免费接口封装(kaipanla 文档收录, 无需 Token) ====================
# 16 个接口: 涨停/炸板/跌停(实时+历史) + 曲线(涨跌家数/涨停跌停/炸板率/昨涨停今表现/市场温度)
# + 热点解读/板块题材 + 直播 + 个股大单净额
# 响应格式: {code:20000, message:OK, data:...}

_FLASH_LINE = "https://flash-api.xuangubao.cn/api/market_indicator/line?fields="
_FLASH_SURGE = "https://flash-api.xuangubao.cn/api/surge_stock/"


def _flash_line(fields, date=None):
    """xuangubao 曲线接口: fields=逗号分隔指标; date 可选(YYYY-MM-DD)
    返回 [{field: value, timestamp: 秒}, ...]; 失败返回 []"""
    url = _FLASH_LINE + fields + (("&date=" + date) if date else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("xuangubao 曲线失败 fields=%s err=%s", fields, e)
        return []
    data = d.get("data")
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if isinstance(it, dict):
            row = {k: v for k, v in it.items() if k != "timestamp"}
            row["ts"] = it.get("timestamp")
            out.append(row)
    return out


def fetch_zt_pool(day=None):
    """涨停实时池(doc10): day=None 今日; YYYY-MM-DD 历史. 复用 _flash_pool"""
    def loader():
        rows = _flash_pool("limit_up", day)
        if not rows and not day:
            return []
        return rows
    key = "zt_pool" + (("_" + day.replace("-", "")) if day else "")
    return _cached(key, (30 * 60) if day else 30, loader)


def fetch_dt_pool(day=None):
    """跌停实时池(doc12): day=None 今日; YYYY-MM-DD 历史"""
    def loader():
        return _flash_pool("limit_down", day) or []
    key = "dt_pool" + (("_" + day.replace("-", "")) if day else "")
    return _cached(key, (30 * 60) if day else 30, loader)


def fetch_yest_zt_pool(day=None):
    """昨日涨停池(doc28): 默认今日的昨日; 可指定 YYYY-MM-DD"""
    def loader():
        d = day or _prev_trade_day()
        if not d:
            return []
        return _flash_pool("yesterday_limit_up", d) or []
    key = "yest_zt_pool" + (("_" + day.replace("-", "")) if day else "")
    return _cached(key, 30 * 60, loader)


def fetch_updown_line(date=None):
    """上涨/下跌家数曲线(doc34)"""
    return _cached("updown_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("rise_count,fall_count", date) or [])


def fetch_zt_dt_line(date=None):
    """涨停数与跌停数曲线(doc35)"""
    return _cached("zt_dt_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("limit_up_count,limit_down_count", date) or [])


def fetch_broken_line(date=None):
    """炸板数量曲线(doc36): limit_up_broken_count + ratio"""
    return _cached("broken_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("limit_up_broken_count,limit_up_broken_ratio", date) or [])


def fetch_yest_zt_perf_line(date=None):
    """昨日涨停今日表现曲线(doc37): yesterday_limit_up_avg_pcp"""
    return _cached("yest_zt_perf_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("yesterday_limit_up_avg_pcp", date) or [])


def fetch_market_temp_line(date=None):
    """市场温度曲线(doc38): market_temperature"""
    return _cached("mkt_temp_line" + (("_" + date.replace("-", "")) if date else ""), 60,
                   lambda: _flash_line("market_temperature", date) or [])


def _flash_surge(path, params=""):
    """xuangubao 热点接口: path=stocks/plates; params 查询串"""
    url = _FLASH_SURGE + path + (("?" + params) if params else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("xuangubao 热点失败 path=%s err=%s", path, e)
        return {}
    data = d.get("data")
    return data if isinstance(data, dict) else {}


def fetch_hot_stocks():
    """热点解读-强势股列表(doc39): items 是二维数组(fields 作列头)
    → [{code,name,price,change,circulation,desc,plates}, ...]"""
    d = _flash_surge("stocks", "normal=true&uplimit=true")
    fields = d.get("fields") or []
    lst = d.get("items") or []
    if not isinstance(lst, list):
        return []
    out = []
    for row in lst:
        if not isinstance(row, list):
            continue
        it = dict(zip(fields, row))
        code = str(it.get("code", "")).split(".")[0]      # 去 .SZ/.SH 后缀
        plates = it.get("plates") or []
        if isinstance(plates, list):
            plates = "、".join(str(p.get("name", "")) for p in plates if isinstance(p, dict) and p.get("name"))
        out.append({
            "code": code,
            "name": str(it.get("prod_name", "") or ""),
            "price": it.get("cur_price"),
            "change": round(_f(it.get("px_change_rate")) * 100, 2),
            "circulation": it.get("circulation_value"),     # 流通市值(元)
            "desc": str(it.get("description", "") or ""),
            "plates": plates,                               # 所属板块(拼接)
            "enterTime": it.get("enter_time"),
            "upLimit": it.get("up_limit"),
        })
    return out[:100]


def fetch_hot_plates():
    """板块名称与对应题材(doc40): [{id,name,description}, ...]"""
    d = _flash_surge("plates")
    items = d.get("items") or []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("name"):
            out.append({"id": it.get("id"), "name": it.get("name"), "description": it.get("description", "")})
    return out[:100]


def fetch_live_room():
    """涨停直播(doc32): fupanwang 实时涨停播报"""
    url = "https://api.fupanwang.com/kpl/zhibo"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen(req, timeout=10, context=_ssl_ctx) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        log.warning("涨停直播失败 err=%s", e)
        return []
    lst = d.get("data") or d.get("list") or []
    return lst if isinstance(lst, list) else []


# ==================== 资讯（2026-09-27 v4.11.59 新增） ====================
# 背景: 「盘前资讯」页需要开盘啦的资讯内容。此前 doc95/doc96 早已生成但 **从未接线**,
#   且 host_key "article" 不在 config.KPL_HOSTS ⇒ `_call("article", …)` 静默回落
#   default(apphwhq 竞价域名) ⇒ 实测该域名对该参数返回非 JSON ⇒ json.loads 抛错被
#   _call 吞掉返回 None。⇒ 本批先把 host 补齐(见 core/config.py), 再在此处接线。
#
# 实测(2026-09-27 01:0x, 测试机 47.99.153.123 真实账号):
#   doc95 头条     @apparticle  errcode=0  List[0].Detail[]  (每日 1 篇, 富 HTML)
#   doc96 新闻快讯 @apparticle  errcode=0  List[]            (7x24 实时, 默认 3 条)
#   doc97 明天炒什么@applhb      errcode=0  List[0].List[]    (盘后选题榜, 带 HotVal)
#   doc99 文章内容 @applhb      errcode=0  传 ID 返回全文
#   注: doc96 的 st / Order 参数实测**无效**(仍返回 3 条) ⇒ 快讯条数靠前端累积, 见 news_feed.py

def _as_epoch(v):
    """开盘啦的时间字段**格式不统一**, 统一转 epoch(北京时间), 非法一律返回 0。

    ★ 2026-09-27 实测踩坑(值 = 一整个发布被拦下的一次真实崩溃):
        doc96 快讯  Time = "1790434626"            —— epoch 字符串
        doc97 选题  Time = "1790160366"            —— epoch 字符串
        doc95 头条  AddTime = "1790230460"         —— epoch 字符串
        doc99 正文  Time = "2026-09-23 18:46:06"   —— **格式化日期时间字符串**
      原实现一律 `int(d.get("Time"))` ⇒ doc99 直接 `ValueError: invalid literal for
      int() with base 10: '2026-09-23 18:46:06'` ⇒ /api/news/topic 500。
      ⇒ 教训: **同一家的接口, 字段名相同不代表类型相同**; 跨接口复用解析代码前,
        必须逐个接口核对真实返回(本次已逐个实测)。
    """
    if v is None:
        return 0
    s = str(v).strip()
    if not s:
        return 0
    if s.isdigit():
        try:
            return int(s)
        except Exception:                                   # noqa: BLE001
            return 0
    try:
        import calendar
        from datetime import datetime
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return int(calendar.timegm(dt.timetuple())) - 8 * 3600
    except Exception:                                       # noqa: BLE001
        return 0


def fetch_kpl_top_news():
    """头条(doc95, apparticle host): 最新一天的头条文章
    返回 [{cid, date, title, content_html, add_time}, ...]（通常 1 篇/天）"""
    d = _call("article", {"a": "GetTopList", "c": "PCNewsFlash", "apiv": "w44"})
    if not d:
        return []
    out = []
    for day in (d.get("List") or []):
        if not isinstance(day, dict):
            continue
        for it in (day.get("Detail") or []):
            if not isinstance(it, dict):
                continue
            out.append({
                "cid": str(it.get("CID") or it.get("ID") or ""),
                "date": str(it.get("Date") or day.get("Date") or ""),
                "title": str(it.get("Title") or "").strip(),
                "content_html": str(it.get("Content") or ""),
                "add_time": _as_epoch(it.get("AddTime")),
            })
    return out


def fetch_kpl_news_flash():
    """7x24 快讯(doc96, apparticle host): 财联社等实时快讯
    返回 [{cid, ts, source, title, content, stocks}, ...] 按时间倒序"""
    d = _call("article", {"a": "GetList", "c": "PCNewsFlash", "apiv": "w44"})
    if not d:
        return []
    out = []
    for it in (d.get("List") or []):
        if not isinstance(it, dict):
            continue
        title = str(it.get("Title") or "").strip()
        content = str(it.get("Content") or "").strip()
        if not title and content:
            # doc96 的 Title 经常为空, 正文首句【…】即标题；否则截断正文首 40 字
            if content.startswith("【") and "】" in content:
                title = content[1:content.index("】")]
            else:
                title = content[:40]
        stocks = it.get("Stocks") or it.get("code") or []
        out.append({
            "cid": str(it.get("CID") or ""),
            "ts": _as_epoch(it.get("Time")),
            "source": str(it.get("Source") or "开盘啦").strip() or "开盘啦",
            "title": title,
            "content": content,
            "stocks": stocks if isinstance(stocks, list) else [],
        })
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def fetch_kpl_topic_list():
    """明天炒什么-列表(doc97, applhb host): 盘后选题/热度榜
    返回 {day, items:[{id, title, ts, hot_val, hot_tag, is_vote}, ...]}"""
    d = _call("lhb", {"a": "InfoList", "c": "Topic", "apiv": "w44"})
    if not d:
        return {"day": "", "items": []}
    days = [x for x in (d.get("List") or []) if isinstance(x, dict)]
    if not days:
        return {"day": "", "items": []}
    head = days[0]
    items = []
    for it in (head.get("List") or []):
        if not isinstance(it, dict):
            continue
        items.append({
            "id": str(it.get("ID") or ""),
            "title": str(it.get("Title") or "").strip(),
            "ts": _as_epoch(it.get("Time")),
            "hot_val": int(it.get("HotVal") or 0),
            "hot_tag": int(it.get("HotTag") or 0),
            "is_vote": str(it.get("IsVote") or ""),
        })
    return {"day": str(head.get("Day") or ""), "items": items}


def fetch_kpl_topic_detail(topic_id):
    """明天炒什么-文章内容(doc99, applhb host): 传 doc97 的 ID 取全文
    返回 {title, content, source, ts, num, hot_val} 或 {}"""
    if not topic_id:
        return {}
    d = _call("lhb", {"a": "InfoGet", "c": "Topic", "apiv": "w44", "ID": str(topic_id)})
    if not d or not d.get("Title"):
        return {}
    return {
        "title": str(d.get("Title") or ""),
        "content": str(d.get("Content") or ""),
        "source": str(d.get("Source") or ""),
        "ts": _as_epoch(d.get("Time")),
        "num": int(d.get("Num") or 0),
        "hot_val": int(d.get("HotVal") or 0),
    }


def fetch_dadan_net(StockID, Time=None):
    """指定个股-大单净额分时(doc75): GetStockDaDanTrendIncremental
    返回 {dadanjinge: [[时间, 大单净额], ...], max, min, ...}"""
    if not Time:
        Time = int(time.time())
    d = _call("default", {"a": "GetStockDaDanTrendIncremental", "c": "StockL2Data",
                           "apiv": "w44", "StockID": str(StockID), "Time": str(Time)})
    if not d:
        return {}
    return {
        "code": str(d.get("code", "")),
        "dadanjinge": d.get("dadanjinge") or [],
        "max": d.get("max"),
        "min": d.get("min"),
        "day": d.get("day", ""),
    }


def _bj_today():
    """当前**北京时间**日期 `YYYY-MM-DD` —— 与 api 层 `_bj_now()` **同口径**。

    ★ 必须用**显式 +8h** 的 `time.gmtime(time.time() + 8 * 3600)`(项目惯例, 见
      `api/kpl._bj_now()` 的 docstring), **不能**用单参 `time.strftime("%Y-%m-%d")` 走本机本地时区。
      2026-09-27 v4.11.67 我一度写成单参形式(理由是"两台服务器本来就是 UTC+8, 且能让某个
      单参 `time.strftime` 替身的旧用例继续通过") —— 那是**让测试的桩形状倒逼生产代码**, 两个后果:
      ① **潜在时区错位**: 本模块走本机时区、api 层走显式 +8h, 一旦服务器时区不是 UTC+8
         (哪怕只是 systemd 里 `TZ=UTC`), 两边就会**差一天**, 且没有任何报错;
      ② **与仓内既有测试的约定冲突**: 全仓控制"今天"的标准手法是把 `time.gmtime` 换成常量
         (test_kpl/test_snapshot/test_concept_refresh 等 10+ 处) ⇒ 单参形式**根本接不住**这些桩。
      正确做法是**改测试的桩位置**(打在 `kpl._bj_today` 这个函数边界上), 而不是改生产代码迁就它。
    """
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def freeze_day(day=None):
    """**定格基准日**: 交易日 → 该日; 非交易日(周末 / 法定休市) → 最近一个有快照的交易日。

    ★ 为什么需要(2026-09-27 · 主人指令「**非交易日数据要定格才行**」):
      竞价异动的每个 tab 都得先回答两个问题 ——"今天是哪天"、"昨天是哪天"。改造前全仓是
      **裸取自然日**: 非交易日时"今天"= 周六/周日(库里根本没有这天的数据) ⇒ 各 tab 各自
      "回退", 而且**回退深度不一致**, 同一屏上不同 tab 停在不同日子 —— 这就是"没定格":
        - 「昨涨停」股票池锚到最近交易日(09-24)、行情字段却锚到周六(空) ⇒ 51 行只有 11 行有数;
        - 「昨断板」同型 ⇒ 7 行只有 2 行有数;
        - 「竞价爆量」概念列全空(直接读无 date 路径时)、量比的"昨日"锚位偏移;
        - 最刺眼的是「昨」与「今」落在同一天 ⇒ 「今炸板」与「昨炸板」显示同一批 11 只。

    ★ 口径: 非交易日整页等价于「把最近一个交易日的**收盘定格画面**冻结下来」——
      「今日」= 定格基准日 FD、「昨日」= FD 的前一交易日、行情字段一律取 FD 的落库/收盘值
      (不再调实时接口)。**交易日则 FD = 今天, 行为与改造前完全一致**(零回归面)。

    ★ 与 `_latest_trade_snap_date` 的分工: 本函数先用**日历**判"是不是交易日", 只有非交易日
      才去表里找最近交易日; 表里也找不到时降级为纯日历推算 `prev_trade_date`(fail-open,
      **绝不返回空** —— 返回空会把"回退"变成"无数据", 比回退错更糟)。
    """
    base = day or _bj_today()
    if trade_calendar.is_trade_day(base):
        return base
    return _latest_trade_snap_date(base) or trade_calendar.prev_trade_date(base) or base


def _latest_trade_snap_date(day=None, time_point="9_25", strict=False, limit=30):
    """`snapshot_bid` 里最近的**交易日**快照日期(交易日历过滤, 2026-09-27 v4.11.66 新增)。

    ★ 为什么需要: 本模块多处用裸 `SELECT MAX(date) FROM snapshot_bid ...` 表示"今日/昨日
      交易日" —— 隐含假设「表里只可能有交易日行」。该假设被 2026-09-25(中秋 · 周五 ·
      法定休市, 当天傍晚才补上日历门禁)照常采集落下的**幽灵快照**打破, 而且是连锁的:
      ① 幽灵日的四个时点 bid_change/bid_amt 各自都等于 09-24 的 9_25 定格值
         ⇒ 竞价爆量算「今日÷昨日」得**量比恒 1.0**, 被 `量比>2` 全量滤掉 ⇒ **tab 空**;
      ② 幽灵日被当成"昨日" ⇒ 之后所有比值都拿静态值做分母;
      ③ 幽灵日被当成"上一交易日" ⇒ 「昨涨停/昨断板/昨炸板」去问选股宝要休市日的数据 ⇒
         **空表**; 同时「昨炸板」与「今炸板」塌到同一天, 两 tab 显示同一批股票。

    `strict=True` → 严格早于 `day`(取"昨日"); False → 允许等于 `day`(取"最近有数据的交易日")。
    查库异常 / 无合规候选 → None, 由调用方保留原值(绝不主动留空)。
    """
    base = day or time.strftime("%Y-%m-%d")
    op = "<" if strict else "<="
    try:
        conn = sqlite3.connect(config.DB_FILE)
        try:
            rows = conn.execute(
                "SELECT DISTINCT date FROM snapshot_bid WHERE date%s? AND time_point=? "
                "ORDER BY date DESC LIMIT %d" % (op, int(limit)), (base, time_point)).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    return trade_calendar.latest_trade_in([r[0] for r in rows if r and r[0]], base)


def _prev_trade_day(base=None):
    """**定格基准日**的上一交易日: snapshot_bid 记录优先(自动跳过节假日); 失败降级为日历推算

    ★ 2026-09-27 v4.11.66: 表内 `MAX(date)` 改为**交易日历过滤**。原实现隐含假设"表里只
      可能有交易日行" —— 该假设被 2026-09-25(中秋·法定休市, 当天傍晚才补门禁)照采落下的
      幽灵快照打破, 后果是**「昨炸板」与「今炸板」塌到同一天**(两 tab 显示同一批 11 只),
      以及「昨涨停」去选股宝要休市日数据直接返空。
      降级分支不再手写"跳周末"循环, 直接用 `trade_calendar.prev_trade_date()`
      (含法定假日 + 区间外 fail-open)。

    ★ 2026-09-27 v4.11.67: 参照系由**自然日**改为**定格基准日** `freeze_day()` ——
      非交易日时"昨日"必须是「**最近交易日的前一交易日**」, 而不是最近交易日自己
      (那正是「今炸板」≡「昨炸板」的直接成因)。交易日 `freeze_day()` = 今天 ⇒ 行为不变。
    """
    fd = freeze_day(base)
    d = _latest_trade_snap_date(fd, strict=True)
    if d:
        return d
    return trade_calendar.prev_trade_date(fd)


def fetch_broken_zt(day=None):
    """炸板列表(东财 flash, 无需Token): day=None 今日; 'yesterday' 上一交易日; 'YYYY-MM-DD' 指定日
    今日炸板: merge 昨日涨停池补连板数(今日炸板票若昨日涨停 → 显示昨日连板数)
    返回 [{code,name,change,limitUpDays,breakTimes,firstLimitUp,firstBreak,reason,day}, ...]"""
    is_hist = False
    if day == "yesterday":
        day = _prev_trade_day()
        if not day:
            return []
    if day:
        is_hist = True
    cache_key = "broken_zt" + (("_" + day.replace("-", "")) if day else "")

    def loader():
        lst = _flash_pool("limit_up_broken", day)
        if not lst:
            return lst
        if day:      # 历史日不做连板补全(无昨日池语义), 但补竞价涨幅/换手
            return _merge_broken_bid_snap(lst)
        # 今日炸板: 接口 limit_up_days 常为0, 用昨日涨停池补连板数(昨日N板 → 今日炸板显示N板)
        yest_day = _prev_trade_day()
        yest_map = {}
        if yest_day:
            yest_map = {x["code"]: x["limitUpDays"]
                        for x in _flash_pool("limit_up_pool", yest_day)}
        for it in lst:
            if not it.get("limitUpDays") and it["code"] in yest_map:
                it["limitUpDays"] = yest_map[it["code"]]
        return _merge_broken_bid_snap(lst)
    return _cached(cache_key, (30 * 60) if is_hist else 30, loader)


# ==================== 昨日涨停(flash 涨停池 + 今日竞价表现) ====================
def _seal_map(date=None):
    """竞价委买榜 code → 完整行(概念/流通/换手/净额/连板), 用于字段补全

    `date` 给定 → 读**该交易日的落库快照** `auction_daily_history[date]["seal"]`
                   (定格/回看口径, 不调外网 —— 非交易日调开盘啦拿不到 09-24 的委买榜);
    `date` 空   → 实时开盘啦 Type4(仅竞价时段有意义)。
    ★ 2026-09-27 v4.11.67: 原只有无参版本(恒调实时), 非交易日调用会拿到"最近的残留"
      (实测周日只回 11 行) ⇒ 「昨涨停」51 行里只有 11 行有行情字段。"""
    if date:
        try:
            return {s["code"]: s for s in (query_auction_history(date, "seal") or [])}
        except Exception as e:
            log.warning("竞价委买快照读取失败 date=%s(降级为空) err=%s", date, e)
            return {}
    try:
        return {s["code"]: s for s in (fetch_bid_seal() or [])}
    except Exception:
        return {}


# 9_25 快照的**进程内**缓存: {date: (ts, map)}。刻意不走共享 kv_cache —— 见 _snap25_map 注释。
_SNAP25_CACHE = {}
_SNAP25_CACHE_MAX = 4          # 最多保留 4 个日期(每份约 1.7MB), 防长期运行累积
_SNAP25_TTL_TODAY = 60         # 今日: 短 TTL, 9:25 采集/重采要及时反映
_SNAP25_TTL_HIST = 6 * 3600    # 历史: 不可变


def _snap25_map(date=None):
    """指定日 9_25 全市场快照 code → {bid_change, bid_amt, name, float_mv, free_mv, board}
    (全市场5549只, 字段补全兜底)

    ★ 2026-09-27 v4.11.67: `date` 空的默认值由**裸自然日**改为**定格基准日** `freeze_day()`。
      原实现周日 `date=None` ⇒ 查 2026-09-27(库里没有) ⇒ 返回 0 行 ⇒ 「昨涨停/昨断板」
      的行情字段全靠 Type4 兜底 ⇒ 大量空白行。非交易日应定格在最近交易日(09-24, 5561 行)。

    ★ 2026-09-28 加**进程内**缓存: 原实现无缓存, 每次新建 sqlite 连接全表查 ~5500 行 ——
      而「龙虎榜」一次请求就要连查三次(补竞价涨幅 / 流通市值 / 竞价换手), 实测合计 103ms;
      全仓 11 处调用点、单点内多次调用都在重复查库(项目自己在 `_merge_broken_bid_snap`
      已写过"按 day 分组查快照(避免重复查库)")。

      ⚠️ 为何**不用共享 kv_cache**: 先按 `_flash_pool` 的做法改成了 `_cached`(共享缓存),
         实测反而更慢 —— 该映射约 5561 条, 共享层 set/get 需 **49ms / 18ms**
         (json 序列化 + 落库 + 读回), 本身就超过一次查询(~35ms):
         「龙虎榜」第 1 次补全从 53ms 涨到 164ms, 后两次命中也要 26ms(与原来一次查询打平)。
         ⇒ 这份体量的全市场映射放**进程内**(零序列化), 与 `fetcher._quote_map_cache`
           对全市场行情 map 的既有选择一致。两个 worker 各存一份, 可接受。

      缓存安全性: 本函数取用的 6 个字段在 `INSERT OR REPLACE` 之后**不再被 UPDATE** ——
      全仓对 snapshot_bid 的更新只有 `auc_main_net` / `auc_vol_ratio` 两列(auction_snapshot.py),
      均不在本函数字段内。今日 60s / 历史 6h；**空结果不缓存** —— 0 行查询本身极便宜,
      且 9:25 采集落库后必须立刻可见(不能压 60s)。
    """
    if not date:
        date = freeze_day()
    key = str(date)
    now = time.time()
    ttl = _SNAP25_TTL_TODAY if key == _bj_today() else _SNAP25_TTL_HIST
    ent = _SNAP25_CACHE.get(key)
    if ent and ent[1] and now - ent[0] <= ttl:
        return ent[1]
    out = {}
    conn = None
    try:
        conn = sqlite3.connect(config.DB_FILE)
        for r in conn.execute(
                "SELECT code, bid_change, bid_amt, name, float_mv, free_mv, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (date,)):
            out[r[0]] = {"bid_change": r[1], "bid_amt": r[2], "name": r[3] or "",
                         "float_mv": r[4] or 0, "free_mv": r[5] or 0, "board": r[6] or ""}
    except Exception as e:
        log.warning("9_25快照查询失败 date=%s(降级) err=%s", date, e)
        return {}                    # ★ 异常 → 降级且不缓存, 下次重试
    finally:
        if conn:
            conn.close()
    if out:                          # ★ 空结果不缓存(见 docstring)
        _SNAP25_CACHE[key] = (now, out)
        while len(_SNAP25_CACHE) > _SNAP25_CACHE_MAX:
            _SNAP25_CACHE.pop(min(_SNAP25_CACHE, key=lambda x: _SNAP25_CACHE[x][0]), None)
    return out


def _merge_broken_bid_snap(lst, bid_date=None):
    """炸板列表补竞价涨幅/竞价换手: 按每条 day 查该日 9_25 快照;
    若传入 bid_date(形如 "2026-08-24"), 则强制用 bid_date 的快照统一补竞价字段
    (用于"昨炸板看今日竞价"场景: 股票池=昨日炸板, 但bidChange/bidTurnover/floatMv/bidAmt 用今日9_25)。
    bidTurnover = 竞价额(元)/自由流通市值(元)×100(短线侠同口径近似)
    返回补全后的列表(原地修改+返回)"""
    if not lst:
        return lst
    import time as _t
    today_default = _t.strftime("%Y-%m-%d")
    if bid_date:
        # 强制统一日期: 单次查 bid_date 快照即可, 覆盖 bidChange/bidTurnover/floatMv/bidAmt
        snap = _snap25_map(bid_date) or {}
        for it in lst:
            code = str(it.get("code") or "")
            s = snap.get(code)
            if not s:
                continue
            if s.get("bid_change") is not None:
                it["bidChange"] = s["bid_change"]
            amt = s.get("bid_amt") or 0       # 万元
            # 🔴 2026-09-29 口径统一: 实际流通(free_mv)优先, float_mv 仅兜底 —— 原用 float_mv
            #   (东财流通市值, ≈自由流通 2 倍), 导致「昨炸板」的流通/竞换与其它 tab 差 2 倍。
            fmv = s.get("free_mv") or s.get("float_mv") or 0      # 元
            if fmv:
                it["floatMv"] = fmv
            if amt:
                it["bidAmt"] = amt * 10000    # 万元→元(与其他表口径一致, 前端再fmt)
            if amt > 0 and fmv > 0:
                # 精度4位: 大盘小额股不再被round到0
                it["bidTurnover"] = round(amt * 10000 / fmv * 100, 4)
        return lst
    # 按 day 分组查快照(避免重复查库) — 默认兼容行为: 按每条记录自己的 day
    by_day = {}
    for it in lst:
        d = it.get("day") or today_default
        by_day.setdefault(d, [])
    snap_cache = {d: _snap25_map(d) for d in by_day}
    for it in lst:
        s = snap_cache.get(it.get("day") or today_default, {}).get(it["code"], {})
        if not s:
            continue
        it["bidChange"] = s.get("bid_change")
        amt = s.get("bid_amt") or 0      # 万元
        fmv = s.get("free_mv") or s.get("float_mv") or 0     # 实际流通市值(元), 快照 free_mv 优先(f117), f21 兜底
        it["floatMv"] = fmv or it.get("floatMv") or 0   # 实际流通市值(元), 2026-08-19 竞价异动统一流通列改实际流通
        if amt > 0 and fmv > 0:
            it["bidTurnover"] = round(amt * 10000 / fmv * 100, 4)   # 万元→元 口径统一(精度4位, 避免大盘小额股如0.0017%显示为0)
    return lst


def fill_float_mv_from_snap(lst, date=None):
    """用 date(空=今日) 的 9_25 全市场快照给列表补实际流通市值(floatMv, 元); 已带的不覆盖
    用于历史回看快照/炸板等数据源补流通列(2026-08-17; 2026-08-19 改实际流通 free_mv 优先)"""
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        for it in lst:
            code = str(it.get("code") or "")
            if code and not it.get("floatMv") and code in snap:
                fmv = snap[code].get("free_mv") or snap[code].get("float_mv") or 0
                if fmv:
                    it["floatMv"] = fmv
    except Exception as e:
        log.warning("实际流通市值补齐失败 date=%s err=%s", date or "-", e)
    return lst


def _snap25_kpl_map():
    """9_25 快照中『已被开盘啦覆盖』的涨停股 board map: code → 开盘啦 board
    启发式: 涨停股(board 已是开盘啦概念, 短字符串 2-30 字符, 顿号或短逗号分隔)
    非涨停股的 snapshot_bid.board 是东财 f103(短线侠多概念, 字符串长), 不进入此 map
    用于 fetch_board_map 兜底: 周末/非交易时段 KPL 实时接口空时仍能给涨停股用开盘啦 board"""
    import sqlite3
    out = {}
    try:
        conn = sqlite3.connect(config.DB_FILE)
        for r in conn.execute(
                "SELECT code, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25' AND board IS NOT NULL AND board != ''",
                (time.strftime("%Y-%m-%d"),)):
            b = r[1] or ""
            # 启发式: 开盘啦概念短 (典型 2-30 字符), 东财 f103 短线侠长 (平均 60+)
            # 例: "创新药、AI应用"(9字), "股权转让、算力"(7字), "实控人变更、金融概念"(10字)
            if 2 <= len(b) <= 30:
                out[r[0]] = b
        conn.close()
    except Exception as e:
        log.warning("9_25快照KPL board 查询失败(降级) err=%s", e)
    return out


def fetch_board_map():
    """全市场个股概念 map: {code: "概念1、概念2"}
    开盘啦概念优先: 多接口合并(连板梯队 + 竞价封板 + 竞价爆量) 覆盖一字板/连板/封板/爆量
    9_25 快照 board 兜底(已含采集时的开盘啦覆盖)。
    返回 {code: board}; 覆盖不到的概念为空(前端显示东财 f103)"""
    def loader():
        out = {}
        # 1) 连板梯队(覆盖一字板/连板/封板股, 主力: 用户截图 9 只里有 8 只涨幅 200%+ 一字板在这里)
        try:
            for pid in (1, 2, 3, 4, 5):
                for s in (fetch_ladder(pid) or []):
                    b = s.get("concept") or ""
                    if b and s.get("code") not in out:
                        out[s["code"]] = b
        except Exception:
            pass
        # 2) 竞价涨停委买额(竞价时段刚封板股, Type=4 榜 - 早晨刚封涨停)
        try:
            for s in (fetch_bid_seal() or []):
                b = s.get("board") or ""
                if b and s["code"] not in out:
                    out[s["code"]] = b
        except Exception:
            pass
        # 3) 竞价爆量榜(竞价量异动非涨停股, 开盘啦也带 board)
        try:
            for s in (fetch_bid_boom() or []):
                b = s.get("board") or ""
                if b and s["code"] not in out:
                    out[s["code"]] = b
        except Exception:
            pass
        # 4) 热点解读强势股(doc39) - plates 是开盘啦风格的板块拼接
        try:
            for s in (fetch_hot_stocks() or []):
                p = s.get("plates") or ""
                if p and s["code"] not in out:
                    out[s["code"]] = p
        except Exception:
            pass
        # 4) 全天兜底: 当日 9_25 快照中已被开盘啦覆盖的 board (短字符串启发式, 避免短线侠污染)
        try:
            for code, b in _snap25_kpl_map().items():
                if code not in out:
                    out[code] = b
        except Exception:
            pass
        return out
    # 2026-08-18 性能优化: 30s → 300s — 概念归属日内稳定, 避免竞价异动页 10+ tab 每 tab 重建(冷 778ms)
    return _cached("board_map", 300, loader)


def apply_board_concept(result, log_tag="", deep=True, field="concept",
                        truncate=None, blank_if_missing=False,
                        time_budget=3.0):
    """用开盘啦概念覆盖选股/历史回看结果指定字段(2 层覆盖)
    1) 榜单合并(ladder+bid_seal+bid_boom+hot_stocks+snap25) - 快速覆盖热点/板块
    2) 按股查询(doc94 fetch_stock_plate) - 百分百覆盖, 1 天缓存限制频次
    field: 写入的目标字段, 默认 "concept"(选股/历史回看使用);
           竞价异动各 tab 传 "board", 把概念统一覆盖到 board 字段, 保证概念均来自开盘啦
    result: [{code, ...}, ...], 原地修改 field 字段; 返回覆盖数
    deep=False: 只做榜单合并层(历史回看/大列表用, 避免海量按股查询拖慢接口)
    truncate: 概念最多保留前 N 个(按 '、' 分档); None/0=不截断
    time_budget: **按股查询的总耗时预算(秒)**(2026-09-10 生产 504 事故新增)。
      背景: 竞价异动抢筹右表 listLast 固定 100 只, 共享池命中率低(实测 10/100) →
      90 只逐股打开盘啦, 并发受 _SEM=3 限流 → 单次耗时 183~198 秒(日志实锤),
      远超 nginx 60s → 504; 且两个 uvicorn worker 被这种请求占满, 连累 /api/stocks
      一起 504(08:56 同批 504)。概念只是展示字段, 不值得让整个接口赌上 3 分钟。
      超预算即停止后续批次, 未查到的保留榜单值/原值(下轮 1h 共享池命中后自动补齐)。
      传 0 = 不设预算(仅离线/回补任务用)。
    blank_if_missing: True 时, 开盘啦完全未覆盖到的股票, 把原 field(东财)清空,
                     保证概念只看开盘啦; False 则保留原值兜底"""
    if not result:
        return 0

    def _trunc(s):
        if not s:
            return s
        if truncate and truncate > 0:
            parts = [p for p in str(s).split("、") if p]
            return "、".join(parts[:truncate])
        return s

    # code -> [item,...] 索引(便于第二层精确覆盖, 避免二次遍历 result)
    by_code = {}
    for it in result:
        c = str(it.get("code"))
        by_code.setdefault(c, []).append(it)
    covered = set()  # 已被开盘啦覆盖的 code

    # 第一层: 榜单合并（全市场一次接口）
    board_map = {}
    n = 0
    try:
        board_map = fetch_board_map() or {}
        for it in result:
            code = str(it.get("code"))
            b = board_map.get(code)
            if b:
                it[field] = _trunc(b)
                covered.add(code)
                n += 1
        if n:
            log.info("选股概念开盘啦覆盖[榜单] %s 覆盖%d只/共%d只", log_tag, n, len(result))
    except Exception as e:
        log.warning("选股概念开盘啦覆盖[榜单]失败 %s err=%s", log_tag, e)

    # 第二层: 按股查询 GetStockIDPlate (开盘啦真实概念, 用户要求所有表格概念以开盘啦为准)
    # 2026-08-21 修复: 此前只对"第一层未覆盖"的股票查开盘啦, 但第一层 board_map 混合了
    # 东财板块/上榜标签(如竞价爆量表的 "昨日炸板、昨日触板" 状态词, 见 001225),
    # 导致这些污染值被当成"已覆盖"跳过开盘啦查询 → 概念来源错误。
    # 现在 deep=True 时对全部股票都走开盘啦 doc94 按股查询, 保证概念统一来自开盘啦前 N 个。
    if not deep:
        if blank_if_missing:
            for it in result:
                if str(it.get("code")) not in covered:
                    it[field] = ""
        return n
    miss_codes = [str(it.get("code")) for it in result if it.get("code")]
    if not miss_codes:
        if blank_if_missing:
            for it in result:
                if str(it.get("code")) not in covered:
                    it[field] = ""
        return n
    # 2026-08-18 性能优化: 概念 deep 结果跨 tab 共享 —
    # 竞价异动页 10+ tab 首次加载都走 deep 按股查询(冷缓存 26只=1.6s), 叠加后接口 2-3s
    # 共享池 kpl:concept_deep (TTL 1h): 任一 tab 查过的股票, 后续 tab 直接命中, 秒回
    n2 = 0
    try:
        pool = store.get("kpl:concept_deep") or {}
        # 1) 池内命中(其他 tab 已查过)
        pool_hit = [c for c in miss_codes if c in pool]
        for code in pool_hit:
            p = pool.get(code)
            if p:
                for it in by_code.get(code, []):
                    it[field] = _trunc(p)
                n2 += 1
                covered.add(code)
        miss_codes = [c for c in miss_codes if c not in pool]
        # 2) 未命中 → 按股查询, 结果写回共享池
        if miss_codes:
            import concurrent.futures
            # 并发受限流信号量(_SEM=3)保护, 分批执行避免一次开太多线程
            BATCH = 20
            new_pool = {}
            t_deep = time.time()
            for i in range(0, len(miss_codes), BATCH):
                # 2026-09-10 生产 504 止血: 逐股外网查询必须有总耗时上限,
                # 否则"展示字段"能把接口拖到 200s 并占满 worker 拖垮全站。
                if time_budget and time_budget > 0 and (time.time() - t_deep) > time_budget:
                    log.warning("选股概念开盘啦覆盖[按股] %s 耗时预算%.1fs已用尽(已补%d只), "
                                "剩余%d只本轮放弃(保留原值, 后续共享池命中自动补齐)",
                                log_tag, time_budget, n2, len(miss_codes) - i)
                    break
                chunk = miss_codes[i:i + BATCH]
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                    plates = list(ex.map(fetch_stock_plate, chunk))
                for code, plate in zip(chunk, plates):
                    if plate:
                        for it in by_code.get(code, []):
                            it[field] = _trunc(plate)
                        n2 += 1
                        covered.add(code)
                        new_pool[code] = plate
            if new_pool:
                pool.update(new_pool)
                store.set("kpl:concept_deep", pool, 3600)   # 1h 共享, 概念归属日内稳定
        if n2:
            log.info("选股概念开盘啦覆盖[按股] %s 补%d只/共%d只(池命中%d)", log_tag, n2, len(result), len(pool_hit))
    except Exception as e:
        log.warning("选股概念开盘啦覆盖[按股]失败 %s err=%s", log_tag, e)
    # 未覆盖到的(开盘啦无概念): 按 blank_if_missing 决定是否清空原东财值
    if blank_if_missing:
        for it in result:
            if str(it.get("code")) not in covered:
                it[field] = ""
    return n + n2


def apply_board_concept_db(result, log_tag="", field="board", truncate=2,
                           blank_if_missing=True, date=None):
    """2026-08-21 : 从库内当日已落库的概念覆盖 result 的 field 列, 不再实时逐股查开盘啦
    =====================================================================
    背景: 概念由 concept_refresh 每半小时从开盘啦 doc94 定时回写库
    (auction_daily_history 各 tab / qc_snapshot / lhb_history 的 board 字段),
    前端竞价接口直接读库即可, 避免每次请求实时打开盘啦。
    result: [{code, ...}, ...], 原地修改 field 字段; 返回覆盖数
    field:  目标字段(竞价各 tab 用 "board")
    truncate: 概念最多保留前 N 个(按 '、' 分档); None/0=不截断
    blank_if_missing: True 时, 库内无该股概念 → 清空原值(概念只看落库的开盘啦);
                       False 则保留原值兜底
    未命中库(如早盘竞价还没落库)时: 有原值则 truncate 后保留, 避免把已有概念清空"""
    if not result:
        return 0
    codes = [str(it.get("code")) for it in result if it.get("code")]
    if not codes:
        return 0
    board_map = _load_board_map_db(codes, date)
    n = 0
    for it in result:
        c = str(it.get("code"))
        b = board_map.get(c)
        if b:
            it[field] = b
            n += 1
        elif blank_if_missing:
            # 库内确实无该股概念: 若字段带东财污染, 清空保证只看开盘啦;
            # 若无概念原本就是空则不动
            it[field] = ""
    if n:
        log.info("竞价概念读库覆盖 %s 覆盖%d只/共%d只", log_tag, n, len(result))
    return n


def _load_board_map_db(codes, date=None):
    """从当日竞价落库表读取 code -> board 映射(概念均来自开盘啦, concept_refresh 定时回写)
    读取顺序(命中即用): auction_daily_history 各 tab → qc_snapshot → lhb_history
    date: None=今日; 指定 'YYYY-MM-DD' 读历史(供回看接口)
    返回 {code: board}"""
    import sqlite3
    from ..core import config as _cfg
    if date is None:
        g = time.gmtime(time.time() + 8 * 3600)
        date = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    code_set = {str(c) for c in codes}
    if not code_set:
        return {}
    board_map = {}
    try:
        conn = sqlite3.connect(_cfg.DB_FILE)
        # 0) 优先独立概念映射表 stock_concept:
        # concept_refresh 每30分钟从**所有实时接口**采集概念全量写本表,
        # 能覆盖盘中新增股票(如竞价爆量实时407只, 而落库9:26仅97只)。
        try:
            ph = ",".join("?" * len(code_set))
            rows = conn.execute(
                f"SELECT code, board FROM stock_concept WHERE date=? AND code IN ({ph})",
                (date, *code_set)).fetchall()
            for c, b in rows:
                c = str(c).strip()
                if b:
                    board_map[c] = b
        except Exception as e:
            log.warning("读库概念[stock_concept]失败 date=%s err=%s", date, e)
        # 1) 竞价异动各 tab
        tabs = ("seal", "boom", "bid_net", "qiangcang", "yest_zt", "yest_broken",
                "broken_yest", "broken_today")
        for tab in tabs:
            try:
                row = conn.execute(
                    "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
                    (date, tab)).fetchone()
            except Exception:
                continue
            if not row or not row[0]:
                continue
            try:
                for it in json.loads(row[0]):
                    c = str(it.get("code", "")).strip()
                    b = it.get("board")
                    if c in code_set and b and c not in board_map:
                        board_map[c] = b
            except Exception:
                pass
        # 2) 竞价抢筹快照
        try:
            rows = conn.execute(
                "SELECT code, board FROM qc_snapshot WHERE date=?", (date,)).fetchall()
            for c, b in rows:
                c = str(c).strip()
                if b and c not in board_map:
                    board_map[c] = b
        except Exception:
            pass
        # 3) 龙虎榜
        try:
            row = conn.execute("SELECT list FROM lhb_history WHERE date=?",
                               (date,)).fetchone()
            if row and row[0]:
                for it in json.loads(row[0]):
                    c = str(it.get("code", "")).strip()
                    b = it.get("board")
                    if c in code_set and b and c not in board_map:
                        board_map[c] = b
        except Exception:
            pass
        conn.close()
    except Exception as e:
        log.warning("读库概念映射失败 date=%s err=%s", date, e)
    return board_map


def fetch_yest_zt():
    """昨日涨停股今日竞价表现: **flash limit_up_pool&date=昨日** (2026-08-18 主人确认:
    开盘啦 doc19/801900 的 Date 是"指数交易日"语义 — 传 8/17 返回的是 8/17 的"昨日"(8/14)涨停股,
    而当日(8/18)数据未冻结返回空 → 盘后拿不到正确的"昨日涨停"; flash 法日期直接对)
    字段补全: Type4(今日竞价涨停榜)优先 → snapshot_bid 9_25(全市场)兜底
    返回 [{code,name,yestChange,limitUpDays,stillLimit,change,bidChange,bidNetAmt,bidAmt,
           bidTurnover,floatMv,board}, ...]"""
    def loader():
        fd = freeze_day()                    # 定格基准日 (= "今日")
        day = _prev_trade_day()              # 上一交易日 (= "昨日涨停池" 发生的日子)
        if not day:
            return []
        # 主路: flash 昨日涨停池(日期语义直接正确: date=8/17 = 昨日涨停110只)
        lst = _flash_pool("limit_up_pool", day)
        if not lst:
            return []
        # "今日是否仍涨停(连板)" 的"今日" = 定格基准日: 交易日 → 实时池(None);
        # 非交易日 → 定格日 fd 的池(2026-09-27 v4.11.67, 原恒用 None ⇒ 周日拿到的是
        # 最近交易日自己的池 ⇒ 与"昨日池"同日 ⇒ stillLimit 恒 True 自己比自己)
        today_codes = {x["code"] for x in _flash_pool("limit_up_pool", None if fd == _bj_today() else fd)}
        seal_map = _seal_map(fd)
        snap25 = _snap25_map(fd)
        out = []
        for it in lst:
            code = it["code"]
            s = seal_map.get(code, {})
            sn = snap25.get(code, {})
            bid_amt = s.get("bidAmt") or (sn["bid_amt"] * 10000 if sn and sn.get("bid_amt") else None)
            float_mv = s.get("floatMv") or (sn.get("free_mv") or sn.get("float_mv") if sn else None)
            # 竞价换手: Type4 真值优先, 无则 竞价额/自由流通市值 近似(与短线侠 0.1-0.4% 量级一致)
            # 精度4位: 大盘小额股(如58万/344亿≈0.0017%)不再被round到0
            bid_turnover = s.get("bidTurnover")
            if not bid_turnover and bid_amt and float_mv:
                bid_turnover = round(bid_amt / float_mv * 100, 4)
            out.append({
                "code": code,
                "name": it["name"],
                "yestChange": it["change"],              # 昨日涨停涨幅
                "limitUpDays": it["limitUpDays"],        # 昨日连板数
                "stillLimit": code in today_codes,       # 今日是否仍涨停(连板)
                "reason": it.get("reason", ""),          # 昨日涨停原因
                # 今日实时涨幅: Type4 实时涨幅优先, 无则 9_25 竞价涨幅
                "change": s.get("realChange") if s.get("realChange") is not None
                          else (sn.get("bid_change") if sn else None),
                "bidChange": s.get("bidChange") if s.get("bidChange") is not None
                             else (sn.get("bid_change") if sn else None),
                "bidNetAmt": s.get("bidNetAmt"),         # 竞价承接(净额,元) Type4 专有
                "bidAmt": bid_amt,
                "bidTurnover": bid_turnover,
                "floatMv": float_mv,
                "board": s.get("board") or sn.get("board") or "",   # 概念: Type4 → 9_25快照(f103/f100)
            })
        return out
    return _cached("yest_zt", 60 * 5, loader)


def fetch_yest_broken():
    """昨断板(2026-08-18 主人定义): **前一日连板(涨停≥2板)且昨日未涨停 = 昨日连板中断**
    (doc21/801902 是"破板"语义≠断板, 主人反馈作废; 恢复 flash 计算法)
    返回断板股票的今日竞价表现(从 snapshot_bid 9:25 全市场补)
    返回 [{code,name,yestChange,limitUpDays,change,bidChange,bidAmt,bidNetAmt,bidTurnover,floatMv,board}, ...]"""
    def loader():
        fd = freeze_day()                    # 定格基准日 (= "今日")
        day = _prev_trade_day()              # 昨日(断板发生的日子)
        if not day:
            return []
        # 昨日的前一交易日
        # 2026-09-27 v4.11.66: 原为裸 `SELECT DISTINCT date FROM snapshot_bid WHERE date < ?
        # ORDER BY date DESC LIMIT 1` —— 无交易日历过滤 ⇒ 休市日(09-25 中秋)幽灵快照会被
        # 当成"前一交易日", 进而去问选股宝要休市日的涨停池。改走统一解析(交易日过滤)。
        prev2 = _latest_trade_snap_date(day, strict=True)
        if not prev2:
            log.warning("昨断板 无法定位前一日(day=%s), 返回空", day)
            return []
        prev2_pool = _flash_pool("limit_up_pool", prev2)   # 前一日涨停池
        if not prev2_pool:
            return []
        yest_codes = {x["code"] for x in _flash_pool("limit_up_pool", day)}
        # 前一日连板≥2 + 昨日未涨停 = 昨日断板(连板中断)
        broken = [x for x in prev2_pool
                  if x["code"] not in yest_codes and (x.get("limitUpDays") or 0) >= 2]
        log.info("昨断板 前一日(%s)涨停=%d 昨日(%s)未涨停且≥2板=%d只",
                 prev2, len(prev2_pool), day, len(broken))
        # 今日竞价快照(9_25 全市场)补: 涨幅/竞额/概念 —— "今日" = **定格基准日**
        # (2026-09-27 v4.11.67: 原 `_snap25_map()` 默认裸自然日 ⇒ 非交易日查周日 = 0 行
        #  ⇒ 7 行里只有 2 行有行情字段; 现取 FD(09-24) 的 9_25 定格快照)
        snap = _snap25_map(fd)
        yest_snap = _snap25_map(day)   # 昨日(断板日)快照 → 断板日竞价涨幅
        seal_map = _seal_map(fd)
        out = []
        for it in broken:
            code = it["code"]
            s = snap.get(code, {})
            ys = yest_snap.get(code, {})
            t4 = seal_map.get(code, {})
            bid_amt = (s["bid_amt"] * 10000) if s and s.get("bid_amt") else None
            float_mv = t4.get("floatMv") or (s.get("free_mv") or s.get("float_mv") if s else None)
            # 竞价换手: Type4 真值优先, 无则 竞价额/自由流通市值 近似
            # 精度4位: 大盘小额股不再被round到0
            bid_turnover = t4.get("bidTurnover")
            if not bid_turnover and bid_amt and float_mv:
                bid_turnover = round(bid_amt / float_mv * 100, 4)
            out.append({
                "code": code,
                "name": t4.get("name") or s.get("name") or it["name"],
                "yestChange": ys.get("bid_change") if ys else None,  # 昨日(断板日)竞价涨幅 — 2026-08-18 语义修正
                "limitUpDays": it["limitUpDays"],        # 断板前连板数
                "reason": it.get("reason", ""),          # 前一日涨停原因
                "change": t4.get("realChange") if t4.get("realChange") is not None
                          else (s.get("bid_change") if s else None),   # 今日实时涨幅(9_25竞价涨幅兜底)
                "bidChange": (s.get("bid_change") if s else None),     # 今日竞价涨幅
                "bidAmt": bid_amt,
                "bidNetAmt": t4.get("bidNetAmt"),
                "bidTurnover": bid_turnover,
                "floatMv": float_mv,
                "board": t4.get("board") or s.get("board") or "",   # 概念: Type4 → 9_25快照(f103/f100)
            })
        return out
    return _cached("yest_broken", 60 * 5, loader)


def fill_reason_from_pool(lst, date=None):
    """按 date(空=今日) 的东财涨停池给列表补涨停原因(reason); 已带 reason 的不覆盖
    用于历史回看快照/龙虎榜等无 reason 字段的数据源"""
    if not lst:
        return lst
    try:
        pool = _flash_pool("limit_up_pool", date)
        if not pool:
            return lst
        m = {x["code"]: (x.get("reason") or "") for x in pool}
        for it in lst:
            code = str(it.get("code") or "")
            if code and not it.get("reason") and code in m:
                it["reason"] = m[code]
    except Exception as e:
        log.warning("涨停原因补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_change_from_snap(lst, date=None, override=False):
    """用 date(空=今日) 的 9_25 全市场快照(snapshot_bid)给列表补竞价涨幅(bidChange);
    override=False(默认) 仅补 None; override=True 强制用自采快照覆盖。
    (2026-08-24) 开盘啦 Type4 接口 bidChange(row[5]) 经核对 146 只中 124 只与
    snapshot_bid 9_25 竞价涨幅不一致(养元 list=9.99 快照=3.71 等), 竞价委买等表
    以自采快照为准, 开盘啦值仅作无快照时的兜底。"""
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        for it in lst:
            code = str(it.get("code") or "")
            if not code or code not in snap:
                continue
            if not override and it.get("bidChange") is not None:
                continue
            bc = snap[code].get("bid_change")
            if bc is not None:
                it["bidChange"] = bc
    except Exception as e:
        log.warning("竞价涨幅补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_turnover_from_snap(lst, date=None):
    """2026-08-18 主人要求: 竞价异动全部 tab 加竞价换手。
    用 date(空=今日) 9_25 快照给列表补竞价换手(bidTurnover = 竞价成交额/自由流通市值×100,
    与开盘啦 bidTurnover 口径一致); 已带的不覆盖。

    2026-08-23 修复: 老版 fetch_bid_boom(开盘啦 Type10 解析)落库时把华泰等大盘股 floatMv
    错位为极小值(如华泰=27元), 导致 bidTurnover 算出千万级荒谬百分比。此处对已带值也做
    校验: 若 float_mv 异常过小(<1e7 元, 即<1000万, A股最小流通市值也不至于此) → 视为损坏,
    用当日快照的 float_mv 覆盖并重算 bidTurnover。"""
    import math as _math
    MIN_FMV = 1e7   # 元; float_mv 低于该值(不足1000万流通市值)判定为字段错位损坏
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        n = 0
        n_repair = 0
        for it in lst:
            code = str(it.get("code") or "")
            if not code:
                continue
            s = snap.get(code)
            # 🔴 2026-09-29 口径统一(主人 2026-08-19 定的"流通列=实际流通"):
            #   本函数 docstring 写的就是「自由流通市值×100, 与开盘啦口径一致」, 但实现取的是
            #   `s["float_mv"]`(东财**流通市值**, ≈自由流通 2 倍) ⇒ 同一只票的「竞换/流通(亿)」
            #   与其它 tab(三时点榜、_boom_from_snap、fill_float_mv_from_snap)差 2 倍
            #   (生产实测 2026-09-29: 600825 三时点 47.70 亿/0.89% vs 委买 98.32 亿/0.43%)。
            #   现统一 free_mv(实际流通)优先、float_mv 仅在缺失时兜底。
            _mv = s.get("free_mv") or s.get("float_mv") or 0
            if not s or not _mv:
                continue
            # 单位: snapshot_bid.bid_amt 万元, 流通市值 元 → bid_amt×10000 转元
            # 精度4位: 大盘小额股(如58万/344亿≈0.0017%)不再被round到0
            _bt_raw = (s.get("bid_amt") or 0) * 10000 / _mv * 100
            if _bt_raw <= 0:
                continue
            bt = round(_bt_raw, 4)
            cur_fmv = it.get("floatMv") or 0
            # 🔴 2026-09-29: 流通列**一律以自采快照的实际流通为准**(不再"仅缺失时补") ——
            #   开盘啦各榜自带的那列口径不统一(实测 600241: 榜单 25.31 亿 vs 快照自由流通
            #   13.36 亿, 差 1.9 倍), 导致同一票在「竞价爆量」与「竞价封单」两个 tab 差一倍。
            #   这里统一覆盖成快照值, 与三时点榜 / 净额榜 / 炸板补全同源。
            if _mv:
                it["floatMv"] = _mv
            # 竞换缺失(空/0) → 必须用快照补(不因流通市值正常而跳过)
            # (2026-08-24 修复: 此前流通市值正常(>=MIN_FMV)时直接 continue,
            #  导致 seal/boom 等开盘啦接口项 bidTurnover 恒为0 而无法补填)
            if not it.get("bidTurnover"):
                it["bidTurnover"] = bt
                n += 1
                continue
            # 流通市值异常过小(<1000万) 字段错位 → 修复并重算
            if cur_fmv and cur_fmv < MIN_FMV:
                it["floatMv"] = _mv
                it["bidTurnover"] = bt
                n_repair += 1
            elif _math.isfinite(it["bidTurnover"]) and it["bidTurnover"] > 100:
                it["floatMv"] = _mv
                it["bidTurnover"] = bt
                n_repair += 1
        if n_repair:
            log.warning("竞价换手/流通市值修复异常 %d 只 date=%s(字段错位大盘股)", n_repair, date or "-")
        if n:
            log.info("竞价换手补齐 %d 只 date=%s", n, date or "-")
    except Exception as e:
        log.warning("竞价换手补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_amt_from_snap(lst, date=None):
    """2026-08-18 主人要求: doc112(竞价>1000万)等接口无竞价成交额字段 →
    用 9_25 快照补竞价成交额(bidAmt 元; 快照 bid_amt 万元×10000); 已带的不覆盖"""
    if not lst:
        return lst
    try:
        snap = _snap25_map(date)
        if not snap:
            return lst
        n = 0
        for it in lst:
            code = str(it.get("code") or "")
            if not code or it.get("bidAmt") not in (None, "", 0):
                continue
            s = snap.get(code)
            if s and s.get("bid_amt"):
                it["bidAmt"] = (s.get("bid_amt") or 0) * 10000   # 万元 → 元
                n += 1
        if n:
            log.info("竞价成交额补齐 %d 只 date=%s", n, date or "-")
    except Exception as e:
        log.warning("竞价成交额补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_net_from_snap(lst, date=None):
    """用 9_25 **自采快照**补竞价净额(bidNetAmt, 元); 已带(非 0)的不覆盖。

    🔴 2026-09-29 主人反馈「竞价异动有些展示的数据和实时的数据不一致」的根因之一:
      竞价异动的落库快照由 `save_auction_history(phase='bid')` 在 **09:24:2x** 采集
      (设计如此: 再晚开盘啦 Type4 的竞价净额会被清零), 而那一刻开盘啦**还没产出竞净额**
      (官方 ready_after=09:25:35) ⇒ 落库行里 `bidNetAmt` **全为 0**。
      生产实测(2026-09-29): seal 行 72/72 全 0、bid_net 行 37/37 全 0;
      而自采定格 `snapshot_bid.auc_main_net`(9_25, 口径 = 猫爪 fundflow_kp 官方成品, 单位元,
      契约原文「竞价主力净额(特大单+大单), 9:25 定格后即为当日终值」) 当日 **1292 只有值**。
      ⇒ 盘后/历史看「竞价净额」tab 一直是 0/空, 与实时(竞价时段接口直给)不一致。

    ⚠️ 刻意**不走 `_snap25_map`**: 那个进程内缓存按设计不含 `auc_main_net`/`auc_vol_ratio`
      (这两列会被 09:26:10~09:26:30 的补采 UPDATE, 缓存会把 0 固化 60s) ⇒ 这里按 code 定向查一次。
    """
    if not lst:
        return lst
    try:
        import sqlite3
        d = date or freeze_day()
        codes = [str(it.get("code")) for it in lst if it.get("code") and not it.get("bidNetAmt")]
        if not codes:
            return lst
        conn = sqlite3.connect(config.DB_FILE)
        try:
            sql = ("SELECT code, auc_main_net FROM snapshot_bid "
                   "WHERE date=? AND time_point='9_25' AND code IN (%s)"
                   % ",".join("?" * len(codes)))
            m = {r[0]: r[1] for r in conn.execute(sql, [d] + codes)}
        finally:
            conn.close()
        n = 0
        for it in lst:
            v = m.get(str(it.get("code")))
            if v:
                it["bidNetAmt"] = float(v)
                n += 1
        if n:
            log.info("竞价净额补齐 %d 只 date=%s (源: snapshot_bid.auc_main_net 9_25)", n, d)
    except Exception as e:
        log.warning("竞价净额补齐失败 date=%s err=%s", date or "-", e)
    return lst


def fill_bid_ratio_yest(lst, date=None):
    """2026-08-18 主人要求(竞价爆量): 补昨日竞价成交额(yestBidAmt 元) + 竞价量比
    (bidRatioYest = 今日竞价成交额/昨日竞价成交额); 昨日 = 最近(严格小于今日)交易日 9_25 快照"""
    if not lst:
        return lst
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        today = date or time.strftime("%Y-%m-%d")
        # 2026-09-27 v4.11.66: 今日/昨日两处都加交易日历过滤(原裸 MAX(date) 会把休市日
        # 幽灵行当成"今日"与"昨日" —— 09-25 的竞价额恰是 09-24 的 9_25 复制值 ⇒ 量比恒 1.0)
        cur = _latest_trade_snap_date(today) or today
        yest = _latest_trade_snap_date(cur, strict=True)
        if not yest:
            conn.close()
            return lst
        ymap = {}
        for code, amt in conn.execute(
                "SELECT code, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_25'", (yest,)):
            ymap[code] = amt
        conn.close()
        n = 0
        for it in lst:
            code = str(it.get("code") or "")
            ya = ymap.get(code)
            if ya is None:
                continue
            ya_yuan = ya * 10000                       # 万元 → 元
            it["yestBidAmt"] = ya_yuan
            ta = it.get("bidAmt") or 0
            if ta > 0 and ya_yuan > 0:
                it["bidRatioYest"] = round(ta / ya_yuan, 2)
                n += 1
        if n:
            log.info("竞价量比补齐 %d 只 (昨日=%s)", n, yest)
    except Exception as e:
        log.warning("竞价量比补齐失败 date=%s err=%s", date or "-", e)
    return lst


def _save_qc_snapshot(date, items):
    """竞价时段抢筹结果持久化(qc_snapshot 表), 非竞价时段读库展示"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        conn.executemany(
            "INSERT OR REPLACE INTO qc_snapshot (date, code, name, real_change, bid_amt, qc_delta, "
            "bid_turnover, bid_change, float_mv, board, bid_ratio, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(date, x["code"], x.get("name", ""), x.get("realChange", 0), x.get("bidAmt", 0),
              x.get("qcDelta", 0), x.get("bidTurnover", 0), x.get("bidChange", 0),
              x.get("floatMv", 0), x.get("board", ""), x.get("bidRatio", 0) or 0, int(time.time()))
             for x in items])
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("抢筹结果落库失败 err=%s", e)


def _load_qc_snapshot(date):
    """读取某日竞价抢筹快照(按 qc_delta 降序)"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        rows = conn.execute(
            "SELECT code, name, real_change, bid_amt, qc_delta, bid_turnover, bid_change, float_mv, board, bid_ratio "
            "FROM qc_snapshot WHERE date=? ORDER BY qc_delta DESC", (date,)).fetchall()
        conn.close()
    except Exception:
        return []
    return [{
        "code": r[0], "name": r[1], "realChange": r[2], "bidAmt": r[3], "qcDelta": r[4],
        "bidTurnover": r[5], "bidChange": r[6], "floatMv": r[7], "board": r[8] or "",
        "bidRatio": r[9],
    } for r in rows]


LASTSEC_DIFF_THRESHOLD = 0.5   # 最后一秒"明显抢筹"差值阈值(%), 可调


def _calc_lastsec_qc(chg25, seq):
    """最后一秒抢筹(差值回退, 对抗接口延迟):
    seq = [(ts, bid_change, bid_amt), ...] 按 ts 升序(9:24:45-9:25:03 每秒采样)
    规则:
      ① 优先 9_25涨幅 − 最新一秒涨幅: |差|≥阈值 → 视为最后一秒抢筹
      ② 差太小(接口延迟导致最新秒已含变化, 或该秒无变化) → 向前回退:
         最新秒 − 倒数第二秒, 依此类推, 取第一个 |差|≥阈值的相邻对
      ③ 全部差值都小 → 返回差值最大的对(或 None 表示无抢筹)
    返回 (qcDeltaLast, base_ts); 无可用序列返回 (None, None)"""
    if not seq:
        return None, None
    points = sorted(seq, key=lambda x: x[0])          # 升序
    pairs = []
    cur_chg = chg25                                    # 9_25 视为最新锚点
    cur_ts = None
    for ts, chg, _amt in reversed(points):             # 从最新一秒往前
        pairs.append((round(cur_chg - chg, 2), cur_ts, ts))
        cur_chg, cur_ts = chg, ts
    # ① 找第一个 |差| ≥ 阈值的相邻对
    for diff, ts_a, ts_b in pairs:
        if abs(diff) >= LASTSEC_DIFF_THRESHOLD:
            return diff, ts_b or ts_a
    # ② 全部小 → 取差值最大的一对(仍可能有参考意义)
    if pairs:
        best = max(pairs, key=lambda x: abs(x[0]))
        return best[0], best[2] or best[1]
    return None, None


def _load_lastsec_fallback(today, date, hhmm):
    """listLast 旧口径兜底(2026-09-19 抽为独立函数, 猫爪不可用时使用):
    优先 snapshot_lastsec 秒级序列(差值回退对抗接口延迟), 无秒级时回退 9_24 时点。
    返回 list, 异常返回 []。"""
    listLast = []
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE)
        # 秒级序列: code -> [(ts, bid_change, bid_amt), ...] 升序
        rows_ls = conn.execute(
            "SELECT code, bid_change, bid_amt, ts FROM snapshot_lastsec WHERE date=? ORDER BY ts",
            (today,)).fetchall()
        rows24 = conn.execute(
            "SELECT code, bid_change, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_24'",
            (today,)).fetchall()
        rows25 = conn.execute(
            "SELECT code, bid_change, bid_amt, COALESCE(NULLIF(free_mv,0), float_mv), name FROM snapshot_bid "
            "WHERE date=? AND time_point='9_25'", (today,)).fetchall()
        conn.close()
        seq = {}
        for code, chg, amt, ts in rows_ls:
            seq.setdefault(code, []).append((ts, chg, amt))
        if not rows_ls:
            log.warning("抢筹[listLast兜底] date=%s %s snapshot_lastsec=0条, 回退 9_24 时点", today, hhmm)
        if not rows25:
            log.warning("抢筹[listLast兜底] date=%s %s 9_25时点快照=0条, 右表将为空", today, hhmm)
            return []
        seal_map = {} if date else _seal_map()
        m24 = {r[0]: (r[1], r[2]) for r in rows24}
        used_lastsec = 0
        for code, chg, amt25, fmv, name in rows25:
            # 最后一秒抢筹过滤链: 自由流通市值≥5亿 + 竞价金额>500万
            if fmv <= 0 or amt25 <= 0 or amt25 < 500 or fmv < 5e8:
                continue
            t4 = seal_map.get(code, {})
            bid_turnover = t4.get("bidTurnover")
            if not bid_turnover and fmv:
                bid_turnover = round(amt25 * 10000 / fmv * 100, 2)
            base = {
                "code": code,
                "name": name or t4.get("name", ""),
                "realChange": t4.get("realChange"),
                "bidAmt": amt25 * 10000,
                "bidChange": chg,
                "bidTurnover": bid_turnover,
                "floatMv": fmv,
                "board": t4.get("board", ""),
            }
            # ① 秒级序列差值回退(优先): 9_25 − 最新秒; 差值小则向前回退找大差值
            s = seq.get(code)
            if s and len(s) >= 2:
                qc, base_ts = _calc_lastsec_qc(chg, s)
                if qc is not None:
                    base["bidChange24"] = None
                    base["lastsecTs"] = base_ts
                    base["qcDeltaLast"] = qc
                    listLast.append(base)
                    used_lastsec += 1
                    continue
            # ② 兜底: 9_24 时点
            v24 = m24.get(code)
            if v24:
                chg24, amt24 = v24
                if amt24 > 0 and abs(amt25 - amt24) > 1e-6:
                    base["bidChange24"] = chg24
                    base["qcDeltaLast"] = round(chg - chg24, 2)
                    listLast.append(base)
        listLast.sort(key=lambda x: x["qcDeltaLast"], reverse=True)
        log.info("抢筹[listLast兜底] date=%s %s 秒级=%d只 9_24=%d条 9_25=%d条 结果=%d只(秒级%d只)",
                 today, hhmm, len(seq), len(rows24), len(rows25), len(listLast), used_lastsec)
    except Exception as e:
        log.warning("抢筹 listLast 兜底读取失败 err=%s", e)
    return listLast


def _list20_fundflow_fallback(today, hhmm, _meoz_date, _meoz_off):
    """list20 兜底(2026-09-21): 猫爪 auc_kp 的 auc_net_amount 大面积缺失时,
    用 fundflow_kp.auction_main_net_amount(竞价主力净额, 同语义) 全市场自算净额层。

    背景: 2026-09-21 猫爪 auc_kp 竞价净额字段整体为 0(203 只仅 9 只>0, 竞价类数据缺),
    list20 过滤后 0 只 → 面板空。fundflow_kp 同日 auction_main_net_amount 有 880 只>0,
    可作净额层兜底。qcDelta 口径与主源一致 = 净额/自由流通市值*100, 阈值 0.5% 不变。
    返回: list; 异常/无候选返回 []。"""
    try:
        from . import meoz_client
        # 市值字典(≥2亿候选): free_mv_map 内部已走 screening(同一 call_cached 缓存), 零额外请求
        mv_map = meoz_client.free_mv_map(date=_meoz_date, date_offset=_meoz_off) or {}
        cand = [c for c, v in mv_map.items() if float(v or 0) >= 2e8]
        if not cand:
            log.warning("抢筹[list20兜底] date=%s %s 市值字典为空, 放弃", today, hhmm)
            return []
        # 全市场竞价主力净额(fundflow_kp.auction_main_net_amount)
        ff = meoz_client.fundflow_map(cand, date=_meoz_date, date_offset=_meoz_off) or {}
        # 名称/竞额/竞涨/竞换 补全: screening 全市场字段(同一 call_cached 缓存)
        sc = meoz_client.screening_map(date=_meoz_date, date_offset=_meoz_off) or {}
        out = []
        for code, r in ff.items():
            try:
                net = float(r.get("auction_main_net_amount") or 0)
                floatMv = float(mv_map.get(code) or 0)
                if floatMv < 2e8 or net <= 0:
                    continue
                qcDelta = round(net / floatMv * 100, 2)
                if qcDelta <= 0.5:
                    continue
                s = sc.get(code) or {}
                out.append({
                    "code": code,
                    "name": str(r.get("name") or s.get("name") or ""),
                    "realChange": None,                       # 无盘中实时涨幅, 前端显示 "-"
                    "bidAmt": float(s.get("auc_amt") or 0),    # 竞价成交额(元)
                    "qcDelta": qcDelta,
                    "bidTurnover": float(s.get("auc_turnover") or 0),
                    "bidChange": float(s.get("auc_pct_chg") or 0),
                    "floatMv": floatMv,
                    "board": "",
                })
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda x: x["qcDelta"], reverse=True)
        log.info("抢筹[list20兜底] date=%s %s fundflow净额=%d只 过滤后=%d只",
                 today, hhmm, len(ff), len(out))
        return out
    except Exception as e:                                     # noqa: BLE001
        log.warning("抢筹[list20兜底] 失败 err=%s", e)
        return []


def fetch_bid_qiangcang(date=None):
    """竞价抢筹(左右三表, 对标短线侠) —— 数据源: 猫爪(meoz), 2026-09-19 全量换源:

    左表 list20  = 猫爪 auc_kp(涨停委买) 的竞价主力净额池
                   抢筹强度 qcDelta = auc_net_amount / free_float_mv * 100
                   (与开盘啦 bidNetAmt/floatMv 口径逐字一致, 只是换猫爪原生字段)
                   过滤: 自由流通市值≥2亿, 净额>0, 抢筹强度>0.5%
                   竞价时段(9:15-9:30)实时拉取并持久化 qc_snapshot 表;
                   非竞价时段接口为空 → 读库展示今天已选出的结果(不丢失)
    中表 list20Chg = 猫爪 daily_auc_detail 快照模式两个时点原生成品涨幅相减:
                   auc_pct_chg(trademin=0925,side=after) − auc_pct_chg(trademin=0920,side=before)
                   过滤: 自由流通市值≥2亿, 竞价成交额≥500万元, 9:25涨幅≥5%, 差值>5
    右表 listLast= 猫爪 daily_auc.open_bid_pct(开盘抢筹幅度, 官方原生字段)
                   = 9:25开盘价相对9:24最后一笔有效竞价价的涨跌幅 → 零自算
                   过滤: 自由流通市值≥5亿, 竞价成交额≥500万元, 幅度>0

    三表均优先走猫爪; 猫爪不可用时各自回退旧口径(snapshot_bid 自算 / snapshot_lastsec 秒级)。
    date: 空=今天; 指定 'YYYY-MM-DD' 回看历史(猫爪历史 + qc_snapshot 历史)
    返回 {"list20": [...], "list20Chg": [...], "listLast": [...], "date": ...}"""
    # 竞价时段判断(9:15-9:26): 9:25 竞价撮合定格后, 竞价涨幅/净额/抢筹强度/换手均不再变化,
    # 9:26 即转读库定格快照, 不再每 30s 拉猫爪 5 接口(≈4.8s 浪费) —— 2026-09-20 优化。
    # 提到 loader 外, 供 loader 分支 + 下方缓存 TTL 分层共用。
    _g = time.gmtime(time.time() + 8 * 3600)
    _hm = _g.tm_hour * 60 + _g.tm_min
    in_bid = (not date) and _g.tm_wday < 5 and (9 * 60 + 15) <= _hm <= (9 * 60 + 26)

    def loader():
        t0 = time.time()
        today = date or time.strftime("%Y-%m-%d")
        hhmm = time.strftime("%H:%M")
        # 实时模式且非竞价时段: 若今天还没有竞价快照(盘前/周末/节假日), 自动回退到最近
        # 有数据的交易日, 与 bid-seal/bid-boom 等 tab 盘后仍显示最近交易日保持一致
        if not date and not in_bid:
            try:
                # 2026-09-27 v4.11.66: 原 `SELECT MAX(date) FROM snapshot_bid WHERE date <= ?`
                # 无交易日历过滤 ⇒ 周末/节假日会"回退"到休市日(09-25 中秋)的幽灵快照
                # (该日四时点数值全等于 09-24 的 9_25 定格, 抢筹 tab 整屏静态假数据)。
                # ⚠️ 判据保持原样 = "今天有**任一时点**快照"(不加 time_point 条件):
                #    若收窄成"今天有 9_25 行", 则交易日 9_25 定格缺失时会被误判成"今天无数据"
                #    而回落到昨天 —— 那是另一处语义变更, 不在本次范围。
                conn = sqlite3.connect(config.DB_FILE)
                rows = conn.execute(
                    "SELECT DISTINCT date FROM snapshot_bid WHERE date<=? "
                    "ORDER BY date DESC LIMIT 30", (today,)).fetchall()
                conn.close()
                cands = [r[0] for r in rows if r and r[0]]
                # 全不合规 → None ⇒ 保留原日期(与全局 fail-open 口径一致, 不硬塞一个可疑日)
                d_new = trade_calendar.latest_trade_in(cands, today)
                if d_new and d_new != today:
                    log.info("抢筹[回退] date=%s %s 今日无快照, 自动回退最近交易日 %s",
                             today, hhmm, d_new)
                    today = d_new
                elif not d_new:
                    log.info("抢筹[回退] date=%s %s 今日无快照且无合规历史日, 保留原日期",
                             today, hhmm)
            except Exception as e:
                log.warning("抢筹 交易日回退判断失败(按今天处理) err=%s", e)

        # 猫爪取数参数: 实时模式用 tradedate_offset=0(最新交易日), 历史模式用 tradedate
        _meoz_date = None
        _meoz_off = 0
        if date:
            _meoz_date = str(date).replace("-", "")
            _meoz_off = None
        # 延迟导入猫爪客户端(与 kpl 存在互相引用风险, 运行期导入更稳)
        try:
            from . import meoz_client
        except Exception as e:                                   # noqa: BLE001
            log.warning("猫爪客户端导入失败(三表全部回退旧口径) err=%s", e)
            meoz_client = None

        def _mz(fn, *a, **kw):
            """调用猫爪函数; 客户端不可用返回 {} (调用方自动回退)。"""
            if meoz_client is None:
                return {}
            return fn(*a, **kw) or {}

        def _chg_from_snapshot(day):
            """从 snapshot_bid 9_20/9_25 快照自算涨幅抢筹(旧口径回退 / 非竞价时段兜底)。
            返回已按 qcDeltaChg 降序的 list20Chg 列表。"""
            import sqlite3
            conn = sqlite3.connect(config.DB_FILE)
            rows20c = conn.execute(
                "SELECT code, bid_change FROM snapshot_bid WHERE date=? AND time_point='9_20'",
                (day,)).fetchall()
            rows25c = conn.execute(
                "SELECT code, bid_change, bid_amt, COALESCE(NULLIF(free_mv,0), float_mv), name, board "
                "FROM snapshot_bid WHERE date=? AND time_point='9_25'", (day,)).fetchall()
            conn.close()
            m20c = {r[0]: r[1] for r in rows20c}
            out = []
            for code, chg25, amt25, fmv, name, board in rows25c:
                if fmv < 2e8 or amt25 <= 0 or amt25 < 500 or chg25 < 5:
                    continue
                chg20 = m20c.get(code)
                if chg20 is None:
                    continue
                qcChg = round(chg25 - chg20, 2)
                if qcChg <= 5:
                    continue
                out.append({
                    "code": code, "name": name or "",
                    "realChange": None, "bidAmt": amt25 * 10000,
                    "qcDeltaChg": qcChg, "bidChange20": chg20,
                    "bidTurnover": round(amt25 * 10000 / fmv * 100, 2) if fmv else None,
                    "bidChange": chg25, "floatMv": fmv, "board": board or "",
                })
            out.sort(key=lambda x: x["qcDeltaChg"], reverse=True)
            return out

        list20 = []
        if in_bid or date:
            # ===== 竞价时段(或历史回看): 猫爪 auc_kp 拉取 + 落库 =====
            try:
                net_map = meoz_client.auc_qc_net(date=_meoz_date, date_offset=_meoz_off) or {}
            except Exception as e:
                log.warning("抢筹[list20] 猫爪 auc_kp 拉取失败 err=%s", e)
                net_map = {}
            if net_map:
                log.info("抢筹[live] date=%s %s 猫爪 auc_kp 返回%d只", today, hhmm, len(net_map))
                # 市值字典(口径统一): auc_kp 自身 free_float_mv 优先, 本地快照兜底
                mv_map = meoz_client.free_mv_map(date=_meoz_date, date_offset=_meoz_off,
                                                 auc_kp_map=net_map)
                for code, r in net_map.items():
                    try:
                        bidNetAmt = float(r.get("auc_net_amount") or 0)   # 竞价主力净额(元)
                        floatMv = float(mv_map.get(code) or 0)            # 自由流通市值(元)
                        if floatMv < 2e8 or bidNetAmt <= 0:               # 放宽阈值, 纳入中盘股
                            continue
                        qcDelta = round(bidNetAmt / floatMv * 100, 2)     # 抢筹强度%
                        # 阈值 0.5%: 实测强抢筹票 qcDelta 仅 0.4~3%, 5% 会全过滤
                        if qcDelta <= 0.5:
                            continue
                        list20.append({
                            "code": code,
                            "name": str(r.get("name") or ""),
                            "realChange": None,                           # 猫爪 auc_kp 无盘中实时涨幅, 前端显示 "-"
                            "bidAmt": float(r.get("auc_amt") or 0),       # 竞价成交额(元)
                            "qcDelta": qcDelta,
                            "bidTurnover": float(r.get("auc_turnover") or 0),
                            "bidChange": float(r.get("auc_pct_chg") or 0),
                            "floatMv": floatMv,
                            "board": "",
                        })
                    except (ValueError, TypeError):
                        continue
                list20.sort(key=lambda x: x["qcDelta"], reverse=True)
                if list20:
                    _save_qc_snapshot(today, list20)   # 持久化, 供非竞价时段展示
                    log.info("抢筹[live] date=%s %s 过滤后list20=%d只 已落库qc_snapshot",
                             today, hhmm, len(list20))
                else:
                    log.warning("抢筹[live] date=%s %s 猫爪 auc_kp 返回%d只但过滤后0只, 尝试 fundflow 兜底",
                                today, hhmm, len(net_map))
                    list20 = _list20_fundflow_fallback(today, hhmm, _meoz_date, _meoz_off)
                    if list20:
                        _save_qc_snapshot(today, list20)
                        log.info("抢筹[live兜底] date=%s %s fundflow 净额层兜底 list20=%d只 已落库",
                                 today, hhmm, len(list20))
            else:
                # 猫爪不可用 → 回落读库(不留空)
                try:
                    list20 = _load_qc_snapshot(today)
                except Exception as e:
                    log.warning("抢筹[list20] 猫爪空+读库失败 err=%s", e)
                    list20 = []
                log.warning("抢筹[list20→读库] date=%s %s 猫爪返回空, 读库 list20=%d只",
                            today, hhmm, len(list20))
        else:
            # ===== 非竞价时段: 直接读库展示今天已选结果 =====
            try:
                list20 = _load_qc_snapshot(today)
            except Exception as e:
                log.warning("抢筹结果读库失败 err=%s", e)
                list20 = []
            log.info("抢筹[saved] date=%s %s 非竞价时段读库 list20=%d只", today, hhmm, len(list20))

        # 涨幅抢筹(全市场, 短线侠真实口径): qcDeltaChg = 9:25竞价涨幅 − 9:20竞价涨幅
        # 2026-09-19 换源猫爪: daily_auc_detail 快照模式两个时点原生成品涨幅相减(一步减法)
        #   9:20 时点 → trademin=0920 side=before
        #   9:25 定格 → trademin=0925 side=after
        # 猫爪可取(实时/历史均可) → 只用猫爪; 猫爪不可用则退回首日 snapshot_bid 自算(保险)
        list20Chg = []
        if in_bid or date:
            try:
                snap20 = meoz_client.auc_snapshot("0920", "before",
                                                  date=_meoz_date, date_offset=_meoz_off) or {}
                snap25 = meoz_client.auc_snapshot("0925", "after",
                                                  date=_meoz_date, date_offset=_meoz_off) or {}
            except Exception as e:
                log.warning("抢筹[list20Chg] 猫爪 daily_auc_detail 拉取失败 err=%s", e)
                snap20, snap25 = {}, {}
            if snap20 and snap25:
                log.info("抢筹[涨幅] date=%s %s 猫爪 9:20=%d只 9:25=%d只",
                         today, hhmm, len(snap20), len(snap25))
                # 市值字典: daily_auc_detail 不返回市值 → auc_kp + 本地快照兜底
                mv_map = meoz_client.free_mv_map(date=_meoz_date, date_offset=_meoz_off)
                # 名称补全: daily_auc_detail 不返回 name(铁律) → screening_map 补缺。
                # free_mv_map 内部已调用过 screening(同一 call_cached 缓存), 此处零额外请求。
                try:
                    sc_names = {
                        str(c): (r.get("name") or "")
                        for c, r in (meoz_client.screening_map(date=_meoz_date, date_offset=_meoz_off) or {}).items()
                    }
                except Exception as e:                                 # noqa: BLE001
                    log.warning("抢筹[list20Chg] screening 补名失败 err=%s", e)
                    sc_names = {}
                seal_map = {} if date else _seal_map()   # 历史日期不拉今天 Type4(字段用猫爪自身)
                for code, r25 in snap25.items():
                    try:
                        chg25 = float(r25.get("auc_pct_chg") or 0)      # 9:25 竞价涨幅(%)
                        amt25 = float(r25.get("auc_amt") or 0)          # 9:25 竞价成交额(元)
                        fmv = float(mv_map.get(code) or 0)              # 自由流通市值(元)
                        # 过滤: 自由流通市值≥2亿, 竞价额>0, 竞价成交额≥500万元, 9:25涨幅≥5%
                        if fmv < 2e8 or amt25 <= 0 or amt25 < 5e6 or chg25 < 5:
                            continue
                        r20 = snap20.get(code)
                        if not r20:
                            continue
                        chg20 = float(r20.get("auc_pct_chg") or 0)      # 9:20 竞价涨幅(%)
                        qcChg = round(chg25 - chg20, 2)                 # 涨幅抢筹(9:20→9:25 差值)
                        if qcChg <= 5:                                  # 阈值 5 个百分点
                            continue
                        t4 = seal_map.get(code, {})
                        bid_turnover = t4.get("bidTurnover")
                        if not bid_turnover and fmv:
                            bid_turnover = round(amt25 / fmv * 100, 2)
                        list20Chg.append({
                            "code": code,
                            # name 三级兜底: t4(开盘啦实时) → screening(猫爪全市场) 
                            "name": str(r25.get("name") or t4.get("name", "") or sc_names.get(str(code), "")),
                            # realChange: 只取开盘啦盘中实时, 无值不退回竞价涨幅, 前端显示 "-"
                            "realChange": t4.get("realChange"),
                            "bidAmt": amt25,
                            "qcDeltaChg": qcChg,
                            "bidChange20": chg20,
                            "bidTurnover": bid_turnover,
                            "bidChange": chg25,
                            "floatMv": fmv,
                            "board": t4.get("board") or "",
                        })
                    except (ValueError, TypeError):
                        continue
                list20Chg.sort(key=lambda x: x["qcDeltaChg"], reverse=True)
                log.info("抢筹[涨幅] date=%s %s 猫爪涨幅抢筹=%d只", today, hhmm, len(list20Chg))
            else:
                # 猫爪不可用 → 回退旧口径(snapshot_bid 9_20/9_25 自算), 不留空
                log.warning("抢筹[list20Chg] 猫爪返回空, 回退 snapshot_bid 自算")
                try:
                    list20Chg = _chg_from_snapshot(today)
                    log.warning("抢筹[涨幅→回退] date=%s %s 结果=%d只", today, hhmm, len(list20Chg))
                except Exception as e:
                    log.warning("抢筹涨幅列表计算失败 err=%s", e)
                    list20Chg = []
        else:
            # 非竞价时段: list20(listLast 同理)均有非竞价兜底, 唯独 list20Chg 缺失 → 快照自算补齐
            try:
                list20Chg = _chg_from_snapshot(today)
                log.info("抢筹[涨幅→兜底] date=%s %s 非竞价时段快照自算 list20Chg=%d只",
                         today, hhmm, len(list20Chg))
            except Exception as e:
                log.warning("抢筹涨幅列表计算失败 err=%s", e)
                list20Chg = []

        # 右表"最后一秒": 2026-09-19 换源猫爪 daily_auc.open_bid_pct(开盘抢筹幅度)
        #   官方原生字段 = 9:25 开盘价相对 9:24 最后一笔有效竞价价的涨跌幅 → 零自算
        #   实测与 [auc_pct_chg(9:25:00) − auc_pct_chg(9:24:57)] 吻合 81.4%
        # 猫爪不可用 → 回退旧口径(snapshot_lastsec 秒级序列 + 9_24 兜底)
        listLast = []
        if in_bid or date:
            try:
                ob_map = meoz_client.auc_open_bid("0925", date=_meoz_date, date_offset=_meoz_off) or {}
            except Exception as e:
                log.warning("抢筹[listLast] 猫爪 daily_auc 拉取失败 err=%s", e)
                ob_map = {}
            if ob_map:
                # 市值字典: daily_auc 不返回市值 → auc_kp + 本地快照兜底
                mv_map = meoz_client.free_mv_map(date=_meoz_date, date_offset=_meoz_off)
                seal_map = {} if date else _seal_map()
                for code, r in ob_map.items():
                    try:
                        qc = r.get("open_bid_pct")
                        if qc is None:
                            continue
                        qc = float(qc)                                  # 开盘抢筹幅度(%)
                        fmv = float(mv_map.get(code) or 0)               # 自由流通市值(元)
                        amt25 = float(r.get("auc_amt") or 0)             # 竞价成交额(元)
                        chg25 = float(r.get("auc_pct_chg") or 0)         # 9:25 竞价涨幅(%)
                        # 过滤链(与旧口径一致): 自由流通市值≥5亿 + 竞价成交额≥500万
                        if qc <= 0 or fmv < 5e8 or amt25 <= 0 or amt25 < 5e6:
                            continue
                        t4 = seal_map.get(code, {})
                        # 竞价换手: 猫爪 auc_turnover(真实竞价换手率) 优先, 回退开盘啦/自算
                        bid_turnover = r.get("auc_turnover")
                        if bid_turnover is not None:
                            bid_turnover = float(bid_turnover)
                        else:
                            bid_turnover = t4.get("bidTurnover")
                        if not bid_turnover and fmv:
                            bid_turnover = round(amt25 / fmv * 100, 2)
                        # 竞额/昨比: 猫爪 auc_to_pre_vol_pct(竞昨成交比%) 优先, 缺失回退东财昨比(_fill_ratio)
                        bid_ratio = r.get("auc_to_pre_vol_pct")
                        if bid_ratio is not None:
                            bid_ratio = float(bid_ratio)
                        listLast.append({
                            "code": code,
                            "name": str(r.get("name") or t4.get("name", "")),
                            "realChange": t4.get("realChange"),
                            "bidAmt": amt25,
                            "bidChange": chg25,
                            "bidTurnover": bid_turnover,
                            "bidRatio": bid_ratio,
                            "floatMv": fmv,
                            "board": t4.get("board", ""),
                            "bidChange24": None,        # 猫爪 open_bid_pct 无 9_24 语义
                            "lastsecTs": None,
                            "qcDeltaLast": round(qc, 2),
                        })
                    except (ValueError, TypeError):
                        continue
                listLast.sort(key=lambda x: x["qcDeltaLast"], reverse=True)
                log.info("抢筹[listLast] date=%s %s 猫爪 open_bid_pct=%d只 结果=%d只",
                         today, hhmm, len(ob_map), len(listLast))
            else:
                log.warning("抢筹[listLast] 猫爪返回空, 回退 snapshot_lastsec/9_24 自算")
                listLast = _load_lastsec_fallback(today, date, hhmm)
        else:
            # 非竞价时段且非历史: 读今天的秒级兜底(若当天曾采集) → 无则空
            listLast = _load_lastsec_fallback(today, date, hhmm)

        # 竞额/昨比: 今日竞价额(元) / 昨日全天成交额(万元) → 百分比。昨日额按 code 并发拉取(当日缓存)
        # ⚠️ 只对展示上限内(各表前100)拉昨比: listLast 全量可达5000+只, 全拉会被东财限流拖到60s+
        try:
            from . import fetcher as _fetcher
            codes = []
            for it in list20[:100] + list20Chg[:100] + listLast[:100]:
                c = str(it.get("code", ""))
                if c and c not in codes:
                    codes.append(c)
            yest_map = _fetcher.fetch_yesterday_amounts(codes) if codes else {}
        except Exception as e:
            log.warning("抢筹 昨日成交额拉取失败(昨比置空) err=%s", e)
            yest_map = {}

        def _fill_ratio(items):
            for it in items:
                if it.get("bidRatio") is not None:   # 读库项已有昨比, 不覆盖
                    continue
                pair = yest_map.get(str(it.get("code", "")))
                y_amt = pair[0] if isinstance(pair, (list, tuple)) else pair
                bid_amt = float(it.get("bidAmt") or 0)
                if y_amt and bid_amt > 0:
                    it["bidRatio"] = round(bid_amt / y_amt / 100, 2)   # 元 / 万元 / 100 → %
                else:
                    it["bidRatio"] = None
        _fill_ratio(list20)
        _fill_ratio(list20Chg)
        _fill_ratio(listLast)

        log.info("抢筹[result] date=%s %s list20=%d只 list20Chg=%d只 listLast=%d只 昨比命中=%d/%d 耗时%dms",
                 today, hhmm, len(list20[:100]), len(list20Chg[:100]), len(listLast[:100]),
                 len(yest_map), len(codes), int((time.time() - t0) * 1000))
        return {"list20": list20[:100], "list20Chg": list20Chg[:100], "listLast": listLast[:100],
                "date": today}
    # 2026-09-20 缓存分层: 竞价定格后, 定格字段(涨幅/净额/抢筹强度/换手/昨比)不再变化 → 长缓存;
    # 现涨(realChange)与自由流通市值(floatMv)由 api 层每次轻量刷新(各自短缓存), 不随本缓存。
    #   - 历史回看(date 早于今天): 1800s(数据不可变) —— 2026-09-28 由 600s 提升
    #   - 历史回看(正好是今天): 600s(今天的竞价数据仍在变, 不变)
    #   - 实时竞价(9:15-9:26): 30s(竞价进行中需实时感)
    #   - 实时非竞价(盘后/周末/盘中): 300s(读库定格快照, 不再每 30s 重读)
    # ★ 为什么回看日要提到 1800s: 与上游的 `_hist_ttl_for` 长 TTL 配套 —— 结果层过期时
    #   上游仍然热, 重建只需 ~0.345s(实测) 而非 5.5~6.9s; 拉长结果层只是减少重建次数。
    if date:
        _d8 = str(date).replace("-", "")
        _today8 = time.strftime("%Y%m%d", time.gmtime(time.time() + 8 * 3600))
        _ttl = 1800 if _d8 < _today8 else 600
    else:
        _ttl = 30 if in_bid else 300
    return _cached("bid_qiangcang" + (("_" + date.replace("-", "")) if date else ""), _ttl, loader)


def _surge_reason(sr):
    """surge_reason 是 dict: {stock_reason, related_plates:[{plate_name, plate_reason}]} → 拼接文本"""
    if not isinstance(sr, dict):
        return str(sr or "")
    parts = []
    if sr.get("stock_reason"):
        parts.append(str(sr["stock_reason"]))
    plates = sr.get("related_plates")
    if isinstance(plates, list):
        for p in plates:
            if isinstance(p, dict) and p.get("plate_name"):
                parts.append("%s:%s" % (p["plate_name"], p.get("plate_reason", "")))
    return "；".join(p for p in parts if p)


# ==================== 工具函数 ====================
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _num(v):
    if v is None:
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _pct(v):
    """解析百分比字符串: "10.00%" -> 10.0, "-2.95%" -> -2.95"""
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _lb(v):
    """连板数解析: "3连板"->3, "首板"->1, "6天4板"->4"""
    import re
    m = re.search(r"(\d+)连板", v or "")
    if m:
        return int(m.group(1))
    if "首板" in (v or ""):
        return 1
    m2 = re.search(r"(\d+)天(\d+)板", v or "")
    if m2:
        return int(m2.group(2))
    return 0









# ==================== 开盘啦 Kaipanla 全部接口封装 (按 /docs/{id} 编号) ====================
# 自动生成于 2026-08-13, 共 87 个 longhuvip.com 原始接口
# 调用约定: fetch_kpl_doc{N}(**extra) -> dict | None

def fetch_kpl_doc7(**extra):
    r"""k线-个股 (apphis.longhuvip.com) -> dict
    a=GetKLineDay_W14, c=StockLineData, apiv=w40 + extra
    resp 示例: {\"StockID\":\"302132\",\"name\":\"\",\"Time\":1786610805,\"x\":[\"20260305\",\"20260306\",\"20260309\",\"20260310\",\"20260311\",\"20260312\",\"20260
    """
    base = {"a": "GetKLineDay_W14", "c": "StockLineData", "apiv": "w40"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc8(**extra):
    r"""分时与、实时涨幅 (apphwhq.longhuvip.com) -> dict
    a=GetStockTrendIncremental, c=StockL2Data, apiv=w41 + extra
    resp 示例: {\"trend\":[[\"09:30\",8.07,8.07,114,0],[\"09:31\",8.04,8.054,744,0],[\"09:32\",7.99,8.01,2662,0],[\"09:33\",7.97,7.996,2032,0],[\"09:34\",7.98,7.984,
    """
    base = {"a": "GetStockTrendIncremental", "c": "StockL2Data", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc9(**extra):
    r"""盘口五档 (apphwhq.longhuvip.com) -> dict
    a=GetStockPanKou, c=StockL2Data, apiv=w41 + extra
    resp 示例: {\"day\":20260813,\"code\":\"000001\",\"name\":\"\\u5e73\\u5b89\\u94f6\\u884c\",\"preclose_px\":11.25,\"status\":86,\"real\":{\"time\":154603000,\"las
    """
    base = {"a": "GetStockPanKou", "c": "StockL2Data", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc13(**extra):
    r"""大单成交 (apphq.longhuvip.com) -> dict
    a=GetMainMonitor_w30, c=StockYiDongKanPan, apiv=w31 + extra
    resp 示例: {\"List\":[[\"2\",\"1786604400\",\"1498\",\"1044106\",\"6.97\",\"2026-08-13 15:00:00\"],[\"2\",\"1786604400\",\"3434\",\"2393498\",\"6.97\",\"2026-08-
    """
    base = {"a": "GetMainMonitor_w30", "c": "StockYiDongKanPan", "apiv": "w31"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc14(**extra):
    r"""大单委托 (apphq.longhuvip.com) -> dict
    a=GetWeiTuo_W14, c=StockL2Data, apiv=w39 + extra
    resp 示例: {\"start\":1404,\"end\":1503,\"List\":[[\"14:47:22\",\"52421517_CD\",\"11.24\",\"309\",\"347316\",\"2\",\"2\",\"0\",\"1\",\"1786603642\"],[\"14:47:22\
    """
    base = {"a": "GetWeiTuo_W14", "c": "StockL2Data", "apiv": "w39"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc15(**extra):
    r"""涨停复盘 - 复盘啦 (apphwshhq.longhuvip.com) -> dict
    a=GetPlateInfo_w38, c=DailyLimitResumption, apiv=w42 + extra
    resp 示例: {\"nums\":{\"SZJS\":1142,\"XDJS\":4317,\"ZT\":59,\"DT\":4,\"ZBL\":37.8947,\"yestRase\":1.188},\"list\":[],\"date\":\"2026-08-13\",\"Day\":[\"2026-08-1
    """
    base = {"a": "GetPlateInfo_w38", "c": "DailyLimitResumption", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc16(**extra):
    r"""涨停跌停-数量 (apphwshhq.longhuvip.com) -> dict
    a=RiseFallAnalysis, c=HomeDingPan, apiv=w43 + extra
    resp 示例: {\"info\":[[59,4,47,1,37.8947,36,\"2026-08-13\"]],\"ttag\":0.0009409999999999696,\"errcode\":\"0\"}
    """
    base = {"a": "RiseFallAnalysis", "c": "HomeDingPan", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc17(**extra):
    r"""涨停数量历史 (apphis.longhuvip.com) -> dict
    a=RiseFallAnalysis, c=HisHomeDingPan, apiv=w43 + extra
    resp 示例: {\"info\":[[62,4,47,1,37.8947,36,\"2026-08-13\"],[96,0,85,1,11.5385,12,\"2026-08-12\"],[60,2,54,4,22.6667,17,\"2026-08-11\"],[103,5,96,3,12.3894,14,\"
    """
    base = {"a": "RiseFallAnalysis", "c": "HisHomeDingPan", "apiv": "w43"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc18(**extra):
    r"""上涨/下跌家数 (apphwshhq.longhuvip.com) -> dict
    a=MoodNumCount, c=MarketMood, apiv=w43 + extra
    resp 示例: {\"list\":{\"SZJS\":1142,\"XDJS\":4317,\"ZTJS\":59,\"DTJS\":4,\"qscln\":255091673,\"q_zrcs\":215242310,\"bl\":18.51,\"color\":1},\"ttag\":0.0061760000
    """
    base = {"a": "MoodNumCount", "c": "MarketMood", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc19(**extra):
    r"""昨日涨停今表现 (apphwshhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"List\":[\"--\",-175,113518654777,-19041063,-1.26,0,0,0],\"Time\":1786610811,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"ttag\":0.00
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc20(**extra):
    r"""昨日连板今表现 (apphwshhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"List\":[\"--\",219,17928168874,0,0.32,0,0,0],\"Time\":1786610812,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"ttag\":0.0030540000000
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc21(**extra):
    r"""昨日破板今日表现 (apphwshhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"List\":[\"--\",-145,17020349482,0,-0.84,0,0,0],\"Time\":1786610812,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"ttag\":0.00369799999
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc22(**extra):
    r"""今日破板率 (apphwshhq.longhuvip.com) -> dict
    a=RiseFallAnalysis, c=HomeDingPan, apiv=w43 + extra
    resp 示例: {\"info\":[[59,4,47,1,37.8947,36,\"2026-08-13\"]],\"ttag\":0.0010620000000000074,\"errcode\":\"0\"}
    """
    base = {"a": "RiseFallAnalysis", "c": "HomeDingPan", "apiv": "w43"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc23(**extra):
    r"""情绪值指标/连板高度 (apphq.longhuvip.com) -> dict
    a=ChangeStatistics, c=HomeDingPan, apiv=w41 + extra
    resp 示例: {\"info\":[{\"ztjs\":\"59\",\"Day\":\"2026-08-13\",\"df_num\":\"15\",\"strong\":\"51\",\"lbgd\":\"5\"}],\"tip\":\"\\u6e29\\u99a8\\u63d0\\u793a\\uff1a\
    """
    base = {"a": "ChangeStatistics", "c": "HomeDingPan", "apiv": "w41"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc24(**extra):
    r"""情绪-强度-历史 (apphis.longhuvip.com) -> dict
    a=ChangeStatistics, c=HisHomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[{\"strong\":\"51\",\"ztjs\":\"59\",\"lbgd\":\"5\",\"Day\":\"2026-08-13\",\"df_num\":\"15\"},{\"strong\":\"78\",\"ztjs\":\"92\",\"lbgd\":\"7
    """
    base = {"a": "ChangeStatistics", "c": "HisHomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc30(**extra):
    r"""竞价涨停委买额-历史接口 (apphis.longhuvip.com) -> dict
    a=MorningBiddingList, c=HisHomeDingPan, apiv=w41 + extra
    resp 示例: {\"info\":[[\"002579\",\"\\u4e2d\\u4eac\\u7535\\u5b50\",0,9.99,926858516,9.9889,36522513,1.26,47704956,138678558,0,\"\\u5370\\u5236\\u7535\\u8def\\u67
    """
    base = {"a": "MorningBiddingList", "c": "HisHomeDingPan", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc31(**extra):
    r"""竞价-个股竞价分时 (apphwhq.longhuvip.com) -> dict
    a=GetStockBid, c=StockL2Data, apiv=w41 + extra
    resp 示例: {\"code\":\"000785\",\"day\":20260813,\"bid\":[[\"09:15\",2.29,1,25],[\"09:15\",2.3,1,188],[\"09:16\",2.3,1,189],[\"09:16\",2.3,1,188],[\"09:17\",2.3,
    """
    base = {"a": "GetStockBid", "c": "StockL2Data", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc33(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=ZhiBoContent, c=HisConceptionPoint, apiv=w40 + extra
    resp 示例: {\"JHJJYD\":[\"\",\"\",0],\"List\":[],\"Notice\":\"\\u76f4\\u64ad\\u5373\\u5c06\\u5f00\\u59cb\\uff01\\uff01\\uff01\",\"Time\":1786550400,\"Status\":0,
    """
    base = {"a": "ZhiBoContent", "c": "HisConceptionPoint", "apiv": "w40"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc41(**extra):
    r"""精选板块列表-实时： (apphq.longhuvip.com) -> dict
    a=RealRankingInfo, c=ZhiShuRanking, apiv=w26 + extra
    resp 示例: {\"list\":[[\"801045\",\"\\u533b\\u836f\",9969,0.712,0.616,271057587087,4072089148,50934541266,-46862452118,1.218,7675361415847,0.83,1565506580,907194
    """
    base = {"a": "RealRankingInfo", "c": "ZhiShuRanking", "apiv": "w26"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc42(**extra):
    r"""精选板块列表-历史 (apphis.longhuvip.com) -> dict
    a=RealRankingInfo, c=ZhiShuRanking, apiv=w41 + extra
    resp 示例: {\"list\":[[\"801057\",\"\\u77f3\\u6cb9\\u77f3\\u5316\",7271,3.349,0.242,30557059554,1527890563,7324844425,-5796953862,2.791,2929208764309,0.6,6610840
    """
    base = {"a": "RealRankingInfo", "c": "ZhiShuRanking", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc43(**extra):
    r"""精选板块-当天历史 (apphwshhq.longhuvip.com) -> dict
    a=RealRankingInfo, c=ZhiShuRanking, apiv=w42 + extra
    resp 示例: {\"list\":[[\"801807\",\"\\u7b97\\u529b\",2473,0.767,0,9139413402,289778705,1723841474,-1434062769,2.664,25212470958016,0,169194018,30764529295442,263
    """
    base = {"a": "RealRankingInfo", "c": "ZhiShuRanking", "apiv": "w42"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc46(**extra):
    r"""板块成分股 (apphis.longhuvip.com) -> dict
    a=ZhiShuStockList_W8, c=ZhiShuRanking, apiv=w41 + extra
    resp 示例: {\"list\":[[\"300164\",\"\\u901a\\u6e90\\u77f3\\u6cb9\",\"\",0,\"\\u77f3\\u6cb9\\u77f3\\u5316\\u3001\\u897f\\u90e8\\u5927\\u5f00\\u53d1\",5.06,19.91,1
    """
    base = {"a": "ZhiShuStockList_W8", "c": "ZhiShuRanking", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc47(**extra):
    r"""当天涨停原因： (apphq.longhuvip.com) -> dict
    a=GetKLineZhangTing, c=StockLineData, apiv=w24 + extra
    resp 示例: {\"StockID\":\"000001\",\"List\":[],\"Time\":1786610831,\"ttag\":0.0003049999999999997,\"errcode\":\"0\"}
    """
    base = {"a": "GetKLineZhangTing", "c": "StockLineData", "apiv": "w24"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc48(**extra):
    r"""历史涨停原因： (apphis.longhuvip.com) -> dict
    a=GetKLineZhangTing, c=StockLineData, apiv=w24 + extra
    resp 示例: 
    """
    base = {"a": "GetKLineZhangTing", "c": "StockLineData", "apiv": "w24"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc49(**extra):
    r"""1，涨停的首板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"002322\",\"\\u7406\\u5de5\\u80fd\\u79d1\",0,\"\",1786584300,\"\\u4e2d\\u62a5\\u589e\\u957f\",78142528,132711224,40581357,53265627,-1268
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc50(**extra):
    r"""2，涨停的2板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"001260\",\"\\u5764\\u6cf0\\u80a1\\u4efd\",0,\"\",1786584300,\"\\u6c7d\\u8f66\\u96f6\\u90e8\\u4ef6\",292252896,310460672,17041676,394585
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc51(**extra):
    r"""3，涨停的3板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"603887\",\"\\u57ce\\u5730\\u9999\\u6c5f\",1,\"\",1786584331,\"\\u7b97\\u529b\",271131680,971528404,107874665,223678217,-115803552,22805
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc52(**extra):
    r"""4，涨停的4板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"000802\",\"\\u5317\\u4eac\\u6587\\u5316\",0,\"\",1786584300,\"\\u6587\\u5316\\u4f20\\u5a92\",167948928,350260576,-150012849,449495967,-
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc53(**extra):
    r"""5，涨停的更高 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HomeDingPan, apiv=w39 + extra
    resp 示例: {\"info\":[[[\"603758\",\"\\u79e6\\u5b89\\u80a1\\u4efd\",1,\"\",1786584333,\"\\u673a\\u5668\\u4eba\\u6982\\u5ff5\",179320240,303773449,16890234,460223
    """
    base = {"a": "DailyLimitPerformance", "c": "HomeDingPan", "apiv": "w39"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc54(**extra):
    r"""1，历史涨停的首板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[[\"603887\",\"\\u57ce\\u5730\\u9999\\u6c5f\",0,\"\",1728955559,\"\\u5b9e\\u63a7\\u4eba\\u53d8\\u66f4\",551947392,3058554835,13281816,16827
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc55(**extra):
    r"""2，涨停的2板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[[\"002628\",\"\\u6210\\u90fd\\u8def\\u6865\",0,\"\",1728955551,\"\\u897f\\u90e8\\u5927\\u5f00\\u53d1\",125249472,170181152,371185,15692555
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc56(**extra):
    r"""3，涨停的3板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[[\"600622\",\"\\u5149\\u5927\\u5609\\u5b9d\",0,\"\",1728955551,\"\\u5730\\u4ea7\\u94fe\",330943808,1527980573,36962441,81879174,-44916733,
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc57(**extra):
    r"""4，涨停的4板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[],\"2024-10-15\"],\"ttag\":0.0009799999999999809,\"errcode\":\"0\"}
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc58(**extra):
    r"""5，涨停的更高 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance, c=HisHomeDingPan, apiv=w31 + extra
    resp 示例: {\"info\":[[],\"2024-10-15\"],\"ttag\":0.0009430000000000271,\"errcode\":\"0\"}
    """
    base = {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "apiv": "w31"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc59(**extra):
    r"""1，未涨停的首板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"920367\",\"\\u65b0\\u8d63\\u6c5f\",0,\"\",31.13,29.6,\"\\u533b\\u836f\\u3001\\u5317\\u4ea4AI\\u533b\\u7597\",0,0,0,378548380,647078331,
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc60(**extra):
    r"""2，未涨停的2板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"301602\",\"\\u8d85\\u7814\\u80a1\\u4efd\",1,\"\",20.4,10.99,\"AI\\u533b\\u7597\\u3001AI\\u5e94\\u7528\",18735671,110116516,-91380845,56
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc61(**extra):
    r"""3，未涨停的3板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"603897\",\"\\u957f\\u57ce\\u79d1\\u6280\",1,\"\",34.68,8.21,\"\\u673a\\u5668\\u4eba\\u6982\\u5ff5\\u3001\\u6241\\u7ebf\",91351854,35975
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc62(**extra):
    r"""4，未涨停的4板 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"002248\",\"\\u534e\\u4e1c\\u6570\\u63a7\",0,\"\",11.74,-3.53,\"\\u5de5\\u4e1a\\u6bcd\\u673a\\u3001\\u4e00\\u5b63\\u62a5\\u589e\\u957f\"
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc63(**extra):
    r"""5，未涨停的更高 (apphwhq.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HomeDingPan, apiv=w40 + extra
    resp 示例: {\"info\":[[[\"600721\",\"\\u767e\\u82b1\\u533b\\u836f\",0,\"\",14.5,3.35,\"CRO\\u3001\\u51cf\\u80a5\\u836f\",-309427187,594053313,-903480500,25717093
    """
    base = {"a": "DailyLimitPerformance2", "c": "HomeDingPan", "apiv": "w40"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc64(**extra):
    r"""1，未涨停首板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"688591\",\"\\u6cf0\\u51cc\\u5fae  \",0,\"\",58.47,10.57,\"\\u5e76\\u8d2d\\u91cd\\u7ec4\\u3001\\u6570\\u5b57\\u7ecf\\u6d4e\",-28478897,7
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc65(**extra):
    r"""2，未涨停2板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"688006\",\"\\u676d\\u53ef\\u79d1\\u6280\",0,\"\",30.15,17.13,\"\\u56fa\\u6001\\u7535\\u6c60\\u3001\\u9502\\u7535\\u8bbe\\u5907\",-18730
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc66(**extra):
    r"""3，未涨停3板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"000831\",\"\\u4e2d\\u56fd\\u7a00\\u571f\",0,\"\",59.12,1.37,\"\\u7a00\\u571f\\u6c38\\u78c1\\u3001\\u6709\\u8272\\u91d1\\u5c5e\",-142416
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc67(**extra):
    r"""4，未涨停4板 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"002053\",\"\\u4e91\\u5357\\u80fd\\u6295\",0,\"\",14.47,-3.47,\"\\u7eff\\u8272\\u7535\\u529b\\u3001\\u5929\\u7136\\u6c14\",-7477684,2805
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc68(**extra):
    r"""5，未涨停 更高 (apphis.longhuvip.com) -> dict
    a=DailyLimitPerformance2, c=HisHomeDingPan, apiv=w42 + extra
    resp 示例: {\"info\":[[[\"002053\",\"\\u4e91\\u5357\\u80fd\\u6295\",0,\"\",14.47,-3.47,\"\\u7eff\\u8272\\u7535\\u529b\\u3001\\u5929\\u7136\\u6c14\",-7477684,2805
    """
    base = {"a": "DailyLimitPerformance2", "c": "HisHomeDingPan", "apiv": "w42"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc69(**extra):
    r"""百日新高-板块排序 (apphwshhq.longhuvip.com) -> dict
    a=GroupCount_w28, c=StockNewHigh, apiv=w41 + extra
    resp 示例: {\"List\":[[\"\\u533b\\u836f\",\"46,19\",801045],[\"AI\\u5e94\\u7528\",\"8,4\",803023],[\"\\u5730\\u4ea7\\u94fe\",\"4,1\",801676],[\"\\u673a\\u5668\\u
    """
    base = {"a": "GroupCount_w28", "c": "StockNewHigh", "apiv": "w41"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc70(**extra):
    r"""短线精灵 (apphq.longhuvip.com) -> dict
    a=Radar, c=HomeDingPan, apiv=w33 + extra
    resp 示例: {\"list\":[{\"time\":1786604219,\"status\":\"\\u5c01\\u6da8\\u5927\\u51cf\",\"stock_name\":\"\\u795e\\u5947\\u5236\\u836f\",\"plate_type\":1,\"status_
    """
    base = {"a": "Radar", "c": "HomeDingPan", "apiv": "w33"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc71(**extra):
    r"""盘中人气热榜 (apphq.longhuvip.com) -> dict
    a=GetHotPHB, c=StockBidYiDong, apiv=w29 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"List\":[[\"600721\",\"\\u767e\\u82b1\\u533b\\u836f\",3.35,0,1,0,0],[\"600664\",\"\\u54c8\\u836f\\u80a1\\u4efd\",0.57,0,2,0,0
    """
    base = {"a": "GetHotPHB", "c": "StockBidYiDong", "apiv": "w29"}
    base.update(extra)
    return _call("q", base)

def fetch_kpl_doc72(**extra):
    r"""全球指数 (apphwshhq.longhuvip.com) -> dict
    a=GlobalCommon, c=GlobalIndex, apiv=w44 + extra
    resp 示例: {\"CYWWZS\":[{\"code\":\"DJI\",\"prod_name\":\"\\u9053\\u743c\\u65af\",\"last_px\":\"53770.270\",\"turnover\":\"0.000\",\"increase_rate\":\"-0.04%\",\
    """
    base = {"a": "GlobalCommon", "c": "GlobalIndex", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc74(**extra):
    r"""历史： (apphis.longhuvip.com) -> dict
    a=GetStockChouMa_New, c=StockL2History, apiv=w41 + extra
    resp 示例: {\"List\":[[0,-30458,-578717,609153,0,0,2453334,315666,-346124,\"09:30\",0.02],[-3245806,1148760,-331922,2428867,0,8,53159424,8009574,-10106620,\"09:3
    """
    base = {"a": "GetStockChouMa_New", "c": "StockL2History", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc76(**extra):
    r"""涨停基因 (apphwhq.longhuvip.com) -> dict
    a=GetZhangTingGene, c=StockL2Data, apiv=w42 + extra
    resp 示例: {\"List\":[12,4,88.8889,64.2857,35.7143,20],\"ttag\":0.00023400000000001198,\"errcode\":\"0\"}
    """
    base = {"a": "GetZhangTingGene", "c": "StockL2Data", "apiv": "w42"}
    base.update(extra)
    return _call("after", base)


def fetch_kpl_doc116(**extra):
    r"""大面股-实时 (apphwshhq.longhuvip.com) -> dict
    a=GetPMSL_KQXY, c=FuPanLa, apiv=w35 + extra (与 doc77 历史同参, host 换实时)
    resp 示例: {"date":"2026-08-14","Time":1786760421,"List":[["000692","惠天热电","-6.15%",-14.07,"",0,"热力、股权转让"],...]}
    实测: after host 返回当日实时 9 条; doc77 走 his(历史) 需 Date 参数
    """
    base = {"a": "GetPMSL_KQXY", "c": "FuPanLa", "apiv": "w35"}
    base.update(extra)
    return _call("after", base)


def fetch_kpl_doc77(**extra):
    r"""大面股 (apphis.longhuvip.com) -> dict
    a=GetPMSL_KQXY, c=FuPanLa, apiv=w35 + extra
    resp 示例: {\"List\":[[\"002676\",\"\\u987a\\u5a01\\u80a1\\u4efd\",\"-1.18%\",-10.2,\"\",0,\"\\u805a\\u4e19\\u70ef\\u3001\\u58f3\\u8d44\\u6e90\"],[\"300889\",\"\
    """
    base = {"a": "GetPMSL_KQXY", "c": "FuPanLa", "apiv": "w35"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc78(**extra):
    r"""板块内涨停数 (apphwhq.longhuvip.com) -> dict
    a=GetPlate_Info_QJ, c=ZhiShuRanking, apiv=w41 + extra
    resp 示例: {\"List\":[28,295,352854517421,-1282084796,-1.97,1,72905796,35879198],\"Time\":1786610857,\"Date\":\"2026-08-13\",\"Min\":\"0925\",\"Max\":\"1500\",\"
    """
    base = {"a": "GetPlate_Info_QJ", "c": "ZhiShuRanking", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc79(**extra):
    r"""板块竞价异动 (apphwhq.longhuvip.com) -> dict
    a=GetBKJJ_W36, c=StockBidYiDong, apiv=w41 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"List1\":[[\"801003\",\"5G\",12.3,969312364,163,28233453],[\"801004\",\"\\u9502\\u7535\\u6c60\",6.5,166418828,663,16296398],[
    """
    base = {"a": "GetBKJJ_W36", "c": "StockBidYiDong", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc80(**extra):
    r"""异动板块的个股 (apphwhq.longhuvip.com) -> dict
    a=GetBKJJBL, c=StockBidYiDong, apiv=w41 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"List\":[[\"301107\",\"\\u745c\\u6b23\\u7535\\u5b50\",21.1,-5.8,84,1126356,-0.62,0,0.12,863416093,\"\\u673a\\u5668\\u4eba\\u6
    """
    base = {"a": "GetBKJJBL", "c": "StockBidYiDong", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)

def fetch_kpl_doc81(**extra):
    r"""板块列表（end为当日） (apphwshhq.longhuvip.com) -> dict
    a=GetInterviewsByDateZS, c=StockLineData, apiv=w41 + extra
    resp 示例: {\"List\":[],\"Count\":0,\"ttag\":0.001762999999999959,\"errcode\":\"0\"}
    """
    base = {"a": "GetInterviewsByDateZS", "c": "StockLineData", "apiv": "w41"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc82(**extra):
    r"""全市场个股区间统计（end为当日） (apphwshhq.longhuvip.com) -> dict
    a=GetInterviewsByDateStock, c=StockLineData, apiv=w41 + extra
    resp 示例: {\"List\":[],\"Count\":0,\"ttag\":0.0018840000000000245,\"errcode\":\"0\"}
    """
    base = {"a": "GetInterviewsByDateStock", "c": "StockLineData", "apiv": "w41"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc83(**extra):
    r"""实时接口（最新季度）: (apphis.longhuvip.com) -> dict
    a=GGList_JGCC, c=ZhuLiChiCang, apiv=w41 + extra
    resp 示例: {\"List\":[[\"801001\",\"\\u82af\\u7247\",\"210781176639\",\"32.3844\",\"1230624973443\",\"36.09\",\"37.15\",\"76764146365125\",\"0\"],[\"801660\",\"\
    """
    base = {"a": "GGList_JGCC", "c": "ZhuLiChiCang", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc84(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=GGList_JGCC, c=ZhuLiChiCang, apiv=w41 + extra
    resp 示例: {\"List\":[[\"801660\",\"\\u901a\\u4fe1\",\"58971507956\",\"16.6\",\"489677063576\",\"25.45\",\"31.85\",\"25570439788797\",\"0\"],[\"801081\",\"\\u8bc
    """
    base = {"a": "GGList_JGCC", "c": "ZhuLiChiCang", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc85(**extra):
    r"""实时接口（最新季度）、历史接口： (apphis.longhuvip.com) -> dict
    a=GGList_JGCC_Plate_Stocks, c=ZhuLiChiCang, apiv=w41 + extra
    resp 示例: {\"List\":[[\"688256\",\"\\u5bd2\\u6b66\\u7eaa  \",\"13877500939\",\"15.51\",\"103140966888\",\"753951562800\",\"16181148\",\"76177030\",\"2.31\",\"27
    """
    base = {"a": "GGList_JGCC_Plate_Stocks", "c": "ZhuLiChiCang", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc86(**extra):
    r"""实时接口：（最新季度） (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"List\":[[\"801001\",\"\\u82af\\u7247\",\"85882395225\",\"35.77\",\"541406743418\",\"36.09\",\"37.15\",\"76764146365125\",\"1\"],[\"801004\",\"\\u95
    """
    base = {"a": "GGList_BXZJ", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc87(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"List\":[[\"801088\",\"\\u6709\\u8272\\u91d1\\u5c5e\",\"20122564633\",\"21.76\",\"131838432453\",\"12.3\",\"15.38\",\"8580912833302\",\"0\"],[\"8010
    """
    base = {"a": "GGList_BXZJ", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc88(**extra):
    r"""实时接口：（最新季度） (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ_Stocks, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"State\":1,\"Date\":\"2026-06-30\",\"DateList\":[\"2026-06-30\",\"2026-03-31\",\"2025-12-31\",\"2025-09-30\",\"2025-06-30\",\"2025-03-31\",\"2024-12
    """
    base = {"a": "GGList_BXZJ_Stocks", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc89(**extra):
    r"""历史 (apphis.longhuvip.com) -> dict
    a=GGList_BXZJ_Stocks, c=ZhuLiChiCang, apiv=w44 + extra
    resp 示例: {\"State\":1,\"Date\":\"2025-12-31\",\"DateList\":[\"2026-06-30\",\"2026-03-31\",\"2025-12-31\",\"2025-09-30\",\"2025-06-30\",\"2025-03-31\",\"2024-12
    """
    base = {"a": "GGList_BXZJ_Stocks", "c": "ZhuLiChiCang", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc90(**extra):
    r"""异动实时接口 (apphwshhq.longhuvip.com) -> dict
    a=GetPianLiZhi_Index, c=StockBidYiDong, apiv=w44 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"Many_Num\":19,\"Time\":1786610872,\"List\":[[\"603221\",\"\\u7231\\u4e3d\\u5bb6\\u5c45\",0,\"\\u80a1\\u7968\\u4ea4\\u6613\\u
    """
    def _load():
        base = {"a": "GetPianLiZhi_Index", "c": "StockBidYiDong", "apiv": "w44"}
        base.update(extra)
        return _call("default", base)
    # 2026-09-04: 无参(页面轮询)走共享缓存 KPL_YIDONG_TTL(15s); 原无缓存每请求真拉开盘啦(avg0.96s)
    if extra:
        return _load()
    return _cached("yidong_doc90", config.KPL_YIDONG_TTL, _load)

def fetch_kpl_doc91(**extra):
    r"""股东变更 (applhb.longhuvip.com) -> dict
    a=GuDongRenShu, c=YiDianCangWei, apiv=w44 + extra
    resp 示例: {\"DateList\":[{\"StratDate\":\"2026-08-01\",\"EndDate\":\"2026-08-15\",\"ShowDate\":\"08\\u670801\\u65e5-08\\u670815\\u65e5\"},{\"StratDate\":\"2026-
    """
    base = {"a": "GuDongRenShu", "c": "YiDianCangWei", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc92(**extra):
    r"""股东追踪，追股东 (applhb.longhuvip.com) -> dict
    a=JGStockListox, c=JGTracking, apiv=w41 + extra
    resp 示例: {\"Time\":1786610871,\"StockList\":[{\"StockID\":\"603986\",\"name\":\"\\u5146\\u6613\\u521b\\u65b0\",\"lpx\":\"404.50\",\"rate\":\"-2.10%\"}],\"List\
    """
    base = {"a": "JGStockListox", "c": "JGTracking", "apiv": "w41"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc93(**extra):
    r"""股东追踪，追个股 (applhb.longhuvip.com) -> dict
    a=GetJGNameID, c=JGTracking, apiv=w44 + extra
    resp 示例: {\"List\":[{\"JG\":\"\\u5f20\\u5f3a\",\"JGID\":\"11828\"}],\"errcode\":\"0\",\"t\":0.0020139999999999603}
    """
    base = {"a": "GetJGNameID", "c": "JGTracking", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc94(**extra):
    r"""\u4e2a\u80a1 - \u5168\u90e8\u76f8\u5173 \u6982\u5ff5\u677f\u5757 (apphwhq/apphwshhq.longhuvip.com) -> dict
    a=GetStockIDPlate, c=StockL2Data, apiv=w43, Type=2 + extra(StockID=xxx \u5fc5\u4f20)
    resp \u793a\u4f8b: {"List":[],"ListJX":[["801159","\u673a\u5668\u4eba\u6982\u5ff5",-1.343],["801273","\u80a1\u6743\u8f6c\u8ba9",-1.147],...]
    \u6ce8: Type=2 \u5fc5\u4f20, \u9ed8\u8ba4 0 \u65f6 ListJX \u8fd4\u7a7a; host \u662f default(apphwhq) \u6216 after(apphwshhq) \u90fd\u53ef
    \u6587\u6863\u793a\u4f8b URL appvipshhq.longhuvip.com \u5b9e\u9645 DNS \u65e0\u6cd5\u89e3\u6790, \u8d70 default host"""
    base = {"a": "GetStockIDPlate", "c": "StockL2Data", "apiv": "w43", "Type": "2"}
    base.update(extra)
    return _call("default", base)


def fetch_stock_plate(code, use_cache=True):
    """\u4e2a\u80a1\u5168\u90e8\u76f8\u5173\u6982\u5ff5\u677f\u5757(\u5f00\u76d8\u5566 doc94 GetStockIDPlate):
    \u8fd4\u56de\u62fc\u63a5\u7684\u677f\u5757\u5b57\u7b26\u4e32(\u5982 "\u673a\u5668\u4eba\u6982\u5ff5\u3001\u80a1\u6743\u8f6c\u8ba9\u3001\u6c7d\u8f66\u96f6\u90e8\u4ef6"), \u5931\u8d25\u8fd4\u56de ""
    \u6309\u80a1\u7f13\u5b58 1 \u5929(\u677f\u5757\u5f52\u5c5e\u53d8\u52a8\u4f4e), \u5927\u5e45\u51cf\u5c11 KPL \u8c03\u7528\u6b21\u6570
    \u9009\u80a1\u7ed3\u679c 39 \u53ea \xd7 30s \u7f13\u5b58\u5237\u65b0 \u2192 \u9996\u6b21 39 \u6b21, \u4e4b\u540e\u547d\u4e2d"""
    key = "stock_plate_" + str(code)
    def loader():
        d = fetch_kpl_doc94(StockID=str(code))
        # 接口失败/返回异常(err None 或非 "0")→ 返回 None, _cached 不缓存, 下次重试
        # 避免瞬时失败被缓存 1 天空串导致概念永远覆盖不上
        if not d:
            return None
        err = d.get("errcode")
        if err is not None and str(err) != "0":
            return None
        lst = d.get("ListJX") or []
        names = []
        for it in lst:
            if isinstance(it, list) and len(it) >= 2 and it[1]:
                nm = str(it[1]).strip()
                if nm:
                    names.append(nm)
        return "\u3001".join(names) if names else None
    return _cached(key, 86400, loader) if use_cache else loader()  # 1 \u5929\u7f13\u5b58(仅成功结果), \u677f\u5757\u5f52\u5c5e\u7a33\u5b9a


def fetch_kpl_doc95(**extra):
    r"""头条 (apparticle.longhuvip.com) -> dict
    a=GetTopList, c=PCNewsFlash, apiv=w44 + extra
    resp 示例: {\"List\":[{\"Date\":\"2026-08-13\",\"Detail\":[{\"ID\":\"80872996205972158\",\"Date\":\"2026-08-13\",\"Title\":\"DeepSeek V4 Pro\\u6b63\\u5f0f\\u7248
    """
    base = {"a": "GetTopList", "c": "PCNewsFlash", "apiv": "w44"}
    base.update(extra)
    return _call("article", base)

def fetch_kpl_doc96(**extra):
    r"""新闻 (apparticle.longhuvip.com) -> dict
    a=GetList, c=PCNewsFlash, apiv=w44 + extra
    resp 示例: {\"List\":[{\"CID\":\"1783359\",\"Time\":\"1786610643\",\"Title\":\"\",\"Type\":\"1\",\"PushUrl\":\"\",\"Source\":\"\\u534e\\u5c14\\u8857\",\"IsSDXZ\"
    """
    base = {"a": "GetList", "c": "PCNewsFlash", "apiv": "w44"}
    base.update(extra)
    return _call("article", base)

def fetch_kpl_doc97(**extra):
    r"""明天炒什么（列表） (applhb.longhuvip.com) -> dict
    a=InfoList, c=Topic, apiv=w44 + extra
    resp 示例: {\"List\":[{\"Day\":\"2026-08-12\",\"List\":[{\"ID\":\"2359\",\"Title\":\"\\u9ad8\\u6807\\uff1a\\u518d\\u6da8\\u5c31\\u505c\\u724c\\uff01\\u6bb5\\u6c3
    """
    base = {"a": "InfoList", "c": "Topic", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc98(**extra):
    r"""明天炒什么 利好个股 (applhb.longhuvip.com) -> dict
    a=InfoZS, c=Topic, apiv=w44 + extra
    resp 示例: {\"List\":[{\"StockID\":\"000066\",\"Name\":\"\\u4e2d\\u56fd\\u957f\\u57ce\",\"last_px\":\"1.61\",\"HotVal\":53766,\"HotTag\":3,\"Click\":0,\"Trad\":\
    """
    base = {"a": "InfoZS", "c": "Topic", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc99(**extra):
    r"""明天炒什么 文章内容 (applhb.longhuvip.com) -> dict
    a=InfoGet, c=Topic, apiv=w44 + extra
    resp 示例: {\"Title\":\"\\u8054\\u624b\\u82f1\\u4f1f\\u8fbe\\uff01\\u5eb7\\u5b81\\u62df\\u5341\\u500d\\u6269\\u4ea7\\u5149\\u8fde\\u63a5\\uff0c\\u5149\\u7ea4\\u4
    """
    base = {"a": "InfoGet", "c": "Topic", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc100(**extra):
    r"""上榜股票 (applhb.longhuvip.com) -> dict
    a=GetStockList, c=LongHuBang, apiv=w44 + extra
    resp 示例: {\"Time\":\"2026-08-13\",\"UserType\":0,\"list\":[{\"ID\":\"002792\",\"Name\":\"\\u901a\\u5b87\\u901a\\u8baf\",\"IncreaseAmount\":\"4.10%\",\"D3\":\"0
    """
    base = {"a": "GetStockList", "c": "LongHuBang", "apiv": "w44"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc101(**extra):
    r"""买入、卖出营业部详细数据 (applhb.longhuvip.com) -> dict
    a=GetNewOneStockInfo, c=Stock, apiv=w41 + extra
    resp 示例: {\"Name\":\"\\u65b0\\u80fd\\u6cf0\\u5c71\",\"Time\":\"2026-04-02\",\"Group\":{\"Buy\":[],\"Sell\":[]},\"KlineDay\":{\"S\":\"2026-04-10\",\"E\":\"2026-
    """
    base = {"a": "GetNewOneStockInfo", "c": "Stock", "apiv": "w41"}
    base.update(extra)
    return _call("lhb", base)

def fetch_kpl_doc103(**extra):
    r"""尾盘竞价抢筹 (apphwshhq.longhuvip.com) -> dict
    a=GetWPQC, c=StockBidYiDong, apiv=w44 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"State\":0,\"List\":[[\"603***\",\"****\",\"\\u6e38\\u8d44\",0,\"\\u7b97\\u529b\\u79df\\u8d41\\u3001\\u7b97\\u529b\",4.73,731
    """
    base = {"a": "GetWPQC", "c": "StockBidYiDong", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc104(**extra):
    r"""竞价砸盘 (apphwshhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[[\"600272\",\"\\u5f00\\u5f00\\u5b9e\\u4e1a\",16,-7.35,0,-9.9,9438701,0,0,0,27413608,\"\\u533b\\u836f\\u96f6\\u552e\\u3001SPD\",1529344000,5
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc105(**extra):
    r"""竞价撮合大于2000万 (apphwshhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[[\"688825\",\"\\u957f\\u946b\\u79d1\\u6280\",52.88,-1.2,0,2.39,56546659,0,0,0,579284936,\"\\u5b58\\u50a8\\u3001\\u4e2d\\u62a5\\u589e\\u957f
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc106(**extra):
    r"""历史分时 (apphis.longhuvip.com) -> dict
    a=GetStockTrend, c=StockL2History, apiv=w41 + extra
    resp 示例: {\"trend\":[[\"09:30\",15,15,909,1],[\"09:31\",14.95,14.982,5750,0],[\"09:32\",14.91,14.962,4951,0],[\"09:33\",14.95,14.958,2505,1],[\"09:34\",14.97,1
    """
    base = {"a": "GetStockTrend", "c": "StockL2History", "apiv": "w41"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc107(**extra):
    r"""指数k线 (apphis.longhuvip.com) -> dict
    a=GetZhiShuKLine, c=ZhiShuKLine, apiv=w44 + extra
    resp 示例: {\"StockID\":\"SH000001\",\"x\":[20240105,20240108,20240109,20240110,20240111,20240112,20240115,20240116,20240117,20240118,20240119,20240122,20240123,
    """
    base = {"a": "GetZhiShuKLine", "c": "ZhiShuKLine", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc108(**extra):
    r"""重点监控股票 (apphwshhq.longhuvip.com) -> dict
    a=GetYDTP_ZDJK_Today, c=StockBidYiDong, apiv=w43 + extra
    resp 示例: {\"Time\":1786610881,\"List\":[[\"600721\",\"\\u767e\\u82b1\\u533b\\u836f\",\"2026-08-13\",\"2026-08-26\",2],[\"605255\",\"\\u5929\\u666e\\u80a1\\u4ef
    """
    def _load():
        base = {"a": "GetYDTP_ZDJK_Today", "c": "StockBidYiDong", "apiv": "w43"}
        base.update(extra)
        return _call("default", base)
    if extra:
        return _load()
    return _cached("yidong_doc108", config.KPL_YIDONG_TTL, _load)

def fetch_kpl_doc109(**extra):
    r"""多次异动个股 (apphwshhq.longhuvip.com) -> dict
    a=GetPianLiZhi_Many, c=StockBidYiDong, apiv=w43 + extra
    resp 示例: {\"Day\":\"2026-08-13\",\"Time\":1786610883,\"List\":[[\"000593\",\"\\u5fb7\\u9f99\\u6c47\\u80fd\",1,\"10\\u65e5\\u51852\\u6b21\\u5f02\\u52a8\\u4e2a\\
    """
    def _load():
        base = {"a": "GetPianLiZhi_Many", "c": "StockBidYiDong", "apiv": "w43"}
        base.update(extra)
        return _call("default", base)
    if extra:
        return _load()
    return _cached("yidong_doc109", config.KPL_YIDONG_TTL, _load)

def fetch_kpl_pianli_hot(**extra):
    r"""热门股偏离值(热门度严重异常) (apphwshhq.longhuvip.com) -> dict
    a=GetPianLiZhi_Hot, c=StockBidYiDong, apiv=w44 + extra
    resp 示例: {\"Day\":\"2026-08-21\",\"Time\":1787404488,\"List\":[[\"300570\",\"\\u592a\\u8fb0\\u5149\",\"10\\u65e5100%\",0.5,53.11,\"\",30.86,30.71,\"CPO/MPO\\u3001\\u5149\\u6a21\\u5757\",0,\"8\\u65e5\",\"10\\u65e5100%\"], [\"002412\",\"\\u6c49\\u68ee\\u5236\\u836f\",\"10\\u65e5100%\",10.04,42.18,\"3\\u8fde\\u677f\",45.58,42.26,\"\\u4e2d\\u836f\\u3001\\u4e2d\\u62a5\\u589e\\u957f\",0,\"7\\u65e5\",\"10\\u65e5100%\"]], ...}
    字段([0]代码 [1]名称 [2]偏离类型 [3]今日涨跌% [4]偏离值 [5]连板/标签 [6]异动前涨幅 [7]偏离基准 [8]概念 [9]0 [10]偏离天数 [11]偏离规则)
    """
    def _load():
        base = {"a": "GetPianLiZhi_Hot", "c": "StockBidYiDong", "apiv": "w44"}
        base.update(extra)
        return _call("default", base)
    if extra:
        return _load()
    return _cached("yidong_pianli_hot", config.KPL_YIDONG_TTL, _load)

def fetch_kpl_doc110(**extra):
    r"""日级量能序列 (apphis.longhuvip.com) -> dict
    2026-09-13 更正: 原名"实时接口"**名不符实** — 实测只返回 125 个交易日的日级
    lastPoint, 拿不到盘中此刻值, 也拿不到昨日同一时点(试遍 st/Period/Type/apiv
    均无效)。盘中实时量能请用 fetch_kpl_market_scln()(a=MarketSCLN, market 域名)。
    a=MarketSCLNKLine, c=HisHomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[{\"lastPoint\":\"255091673\",\"Date\":\"2026-08-13\"},{\"lastPoint\":\"215242310\",\"Date\":\"2026-08-12\"},{\"lastPoint\":\"232098591\",\"
    """
    base = {"a": "MarketSCLNKLine", "c": "HisHomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc111(**extra):
    r"""历史接口 (apphis.longhuvip.com) -> dict
    a=MarketSCLNKLine, c=HisHomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[{\"lastPoint\":\"255091673\",\"Date\":\"2026-08-13\"},{\"lastPoint\":\"215242310\",\"Date\":\"2026-08-12\"},{\"lastPoint\":\"232098591\",\"
    """
    base = {"a": "MarketSCLNKLine", "c": "HisHomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_market_scln(**extra):
    r"""实时市场量能 (apphq 域名) -> dict; 2026-09-13 新增
    a=MarketSCLN, c=HomeDingPan, apiv=w44 + extra
    ⚠️ 与 doc110/111(MarketSCLNKLine) **三处不同**, 混用会静默拿到日级历史:
      接口名 MarketSCLN(无 KLine) / 域名 market(apphq, 非 his) / c=HomeDingPan(非 HisHomeDingPan)
    单位 **万元**(÷1e4 = 亿元)。无 extra 时走 60s 跨进程缓存(防打爆 8 万/日配额)。
    resp: {\"info\":{\"last\":\"102144082\",\"s_zrcs\":\"116604857\",\"s_zrtj\":\"197189848\",
           \"s3_zrtj\":\"182488431\",\"ycln\":\"16957亿\",\"yclnstr\":\"16957亿(-14%,缩量2761亿)\",
           \"csbl\":-14,\"color\":\"2\",\"time\":1789355099,
           \"trends\":[[\"09:30\",\"1645452\",\"1794187\",\"1670202\",\"-8.83\",\"17978亿\",\"2\",\"2\"]]}}
    字段(**2026-09-14 盘中实测钉死**, 单位万元): last=今日此刻累计(==trends末[1]),
          s_zrcs=昨日**同一时点**(==trends末[2]) ★, s_zrtj=昨日**全天**,
          s3_zrtj=前3日**全天均值**(≠同期!), ycln/yclnstr/csbl=全天预测量能/串/完成度%,
          trends=当日分钟级序列 [时刻, 今日累计, 昨日同期, 前3日同期, 完成度%, 预测串, ...]
    ⚠️ 字段名极易误读: `zrtj` 系=**天级基准**, `zrcs` 系=**时点成交**; 9/13 曾把两者判反,
       导致"较昨日"拿昨日**全天**量当基准(虚高 6.6 倍)。
    ⚠️ 非交易日或收盘后请求: "此刻"已过收盘 → 同期与全天**退化重合**(两值相等),
       此窗口**无法区分**这两类字段, 任何语义结论都不可靠 → 只能在盘中验证。
    """
    def _load():
        base = {"a": "MarketSCLN", "c": "HomeDingPan", "apiv": "w44"}
        base.update(extra)
        return _call("market", base)
    if extra:
        return _load()
    return _cached("market_scln", config.KPL_MARKET_SCLN_TTL, _load)


def fetch_kpl_market_capacity(**extra):
    r"""市场量能K线 (apphwshhq 域名, 配置键 after) -> dict; 2026-09-19 新增
    a=MarketCapacityKLine, c=HomeDingPan, apiv=w44, Type=0(全市场) + extra
    主人 9/19 指令: 两市资金主数字改用本接口(与开盘啦 App「市场量能」页同源)。
    resp(2026-09-19 周六实测): {"info":[{"lastPoint":"207710029","Date":"2026-09-18"}],"errcode":"0"}
    字段: lastPoint=量能(万元, /1e4=亿元), Date=交易日。
    Type 实测(9/19): 0=20771亿(全市场) 1=9942亿 2=5224亿 3=161亿。
    ⚠️ 休市时只回最近交易日**日级单点**; 盘中是否逐分钟实时待 9/21 盘中验证。
    ⚠️ 无昨日同一时点/全天预测字段 → 同期基准与预测仍由 MarketSCLN 提供。
    无 extra 时走跨进程缓存(与 market_scln 同 TTL, 防打爆 8 万/日配额)。
    """
    def _load():
        base = {"a": "MarketCapacityKLine", "c": "HomeDingPan", "apiv": "w44", "Type": "0"}
        base.update(extra)
        return _call("after", base)
    if extra:
        return _load()
    return _cached("market_capacity", config.KPL_MARKET_SCLN_TTL, _load)


def parse_market_capacity(data):
    """解析 fetch_kpl_market_capacity() 返回 -> {amount(亿), date}; 取不到 None
    ⚠️ lastPoint<=0 视为取不到(同 parse_market_volume_rt 的 0 值脏点纪律),
       绝不把 0 当实测值。"""
    info = (data or {}).get("info")
    if not isinstance(info, list) or not info or not isinstance(info[0], dict):
        return None
    try:
        v = float(info[0].get("lastPoint"))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return {"amount": round(v / _KPL_AMT_WAN2YI, 2), "date": info[0].get("Date")}


def fetch_kpl_market_scln_hist(**extra):
    r"""市场量能日级历史 (apphis 域名, 配置键 his) -> dict; 2026-09-19 新增
    a=MarketSCLNKLine, c=HisHomeDingPan, apiv=w44, Type=0(全市场) + extra
    主人 9/19 指令: 两市资金**历史**(收盘定格基准 market_brief_last)改用本接口,
    与主数字(MarketCapacityKLine)同源同口径, 摆脱东财自算(分页失败静默少算 -7.6%)。
    resp(9/19 实测): {"info":[{"lastPoint":"207710029","Date":"2026-09-18"},
                     {"lastPoint":"182313427","Date":"2026-09-17"}, ...约125个交易日]}
    交叉验证: 9/11=19718.98亿 == MarketSCLN 的 s_zrtj(昨日全天) == 自算 19716.63 亿。
    与 doc110/111 同接口, 区别在带 Type=0 且语义钉死为「收盘定格基准源」。
    调用频次极低(每交易日收盘 1 次), 不加缓存。
    """
    base = {"a": "MarketSCLNKLine", "c": "HisHomeDingPan", "apiv": "w44", "Type": "0"}
    base.update(extra)
    return _call("his", base)


def parse_market_scln_hist_latest(data, expect_date=None):
    """取最新一个交易日 {amount(亿), date}; expect_date 给了就必须等于它
    (防接口滞后/盘中未定格拿到昨日值); 形状不符/0 值/日期不匹配 → None(调用方回退)"""
    info = (data or {}).get("info")
    if not isinstance(info, list) or not info or not isinstance(info[0], dict):
        return None
    d0 = info[0]
    if expect_date and d0.get("Date") != expect_date:
        log.warning("MarketSCLNKLine 最新日期 %s != 期望 %s(接口未定格?), 本次回退自算",
                    d0.get("Date"), expect_date)
        return None
    try:
        v = float(d0.get("lastPoint"))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return {"amount": round(v / _KPL_AMT_WAN2YI, 2), "date": d0.get("Date")}


# 开盘啦量能单位: 万元 → 亿元
_KPL_AMT_WAN2YI = 1e4


def parse_market_volume_rt(data):
    """解析 fetch_kpl_market_scln() 的返回为结构化量能(亿元); 取不到返回 None

    返回 {amount, prev_same_time, prev_full, prev3_same_time, forecast, forecast_str, ts}
    ⚠️ last<=0(非交易时段 0 值脏点) 视为**取不到** → 返回 None 由调用方回退,
       绝不把 0 当实测值(0 会被前端当成"无量"渲染, 比不显示更糟)。

    🔴 2026-09-14 字段语义纠正(盘中实测钉死, 推翻 9/13 的臆断):
       字段名极易误读 —— `zrtj` 系是**天级基准**, `zrcs` 系才是**时点成交**:
         last   == trends 末行[1] 今日累计   → 今日此刻累计
         s_zrcs == trends 末行[2] 昨日同期   → ★昨日**同一时点** ← prev_same_time
         s_zrtj             昨日**全天**量   → prev_full
         s3_zrtj            前 3 日**全天均值** → (≠同期! 不可用作 prev3_same_time)
       前 3 日**同期**只能从 trends 末行[3] 取。
       实测判据(9/14 11:05): last=10214.41 / s_zrcs=11660.49 / s_zrtj=19718.98 亿,
         其中 s_zrtj **恰等于 9/11 全天实测 19716.63 亿**(settings market_brief_last);
         trends 09:30 首行"昨日同期"= 179.42 亿, 远小于昨日全天 → 自洽。
       ⚠️ 9/13 判反的根因: 在**周日**验证, 收盘后"同期"退化为"全天", 两字段完全相等,
         看不出任何破绽。**字段语义只能在活跃窗口(盘中)实测钉死**。"""
    info = (data or {}).get("info")
    if not isinstance(info, dict):
        return None

    def _wan2yi(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return round(v / _KPL_AMT_WAN2YI, 2) if v > 0 else None

    amount = _wan2yi(info.get("last"))
    if not amount:
        return None
    # 🔴 2026-09-14 纠正: prev_same_time 取 s_zrcs(昨日**同一时点**),
    #    prev_full 取 s_zrtj(昨日**全天**) —— 9/13 把这两个判反了(详见 docstring)。
    prev_same_time = _wan2yi(info.get("s_zrcs"))
    prev_full = _wan2yi(info.get("s_zrtj"))
    # 前 3 日同期: s3_zrtj 是前 3 日**全天**均值(≠同期), 只能从 trends 末行取。
    # trends 行 = [时刻, 今日累计, 昨日同期, 前3日同期, 完成度%, 预测串, ...]
    prev3 = None
    tr = info.get("trends")
    if isinstance(tr, list) and tr:
        row = tr[-1]
        if isinstance(row, (list, tuple)) and len(row) > 3:
            prev3 = _wan2yi(row[3])
    # 自检: 盘中「同一时点累计」必然 **小于** 昨日全天量; 若反了 → 字段语义又漂了。
    # (收盘后同期==全天属正常退化, 故用严格 > 判断, 不误伤)
    if prev_same_time and prev_full and prev_same_time > prev_full:
        log.warning("market_vol_rt 同期基准异常(%.2f > 昨日全天 %.2f), "
                    "字段语义可能已漂移, 本次丢弃同期值", prev_same_time, prev_full)
        prev_same_time = None
    return {
        "amount": amount,
        "prev_same_time": prev_same_time,
        "prev_full": prev_full,
        "prev3_same_time": prev3,
        "forecast": info.get("ycln"),
        "forecast_str": info.get("yclnstr"),
        "ts": info.get("time"),
    }

def fetch_kpl_doc112(**extra):
    r"""竞价大于1000万 (apphwshhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w44 + extra
    resp 示例: {\"info\":[[\"300308\",\"\\u4e2d\\u9645\\u65ed\\u521b\",921.04,0,0,4.23,130182720,0,0,0,478656000,\"\\u5149\\u6a21\\u5757\\u3001OCS\\u4ea4\\u6362\\u67
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w44"}
    base.update(extra)
    return _call("default", base)

def fetch_kpl_doc113(**extra):
    r"""副图688523 (apphis.longhuvip.com) -> dict
    a=GetBidVolKLine, c=StockLineData, apiv=w44 + extra
    resp 示例: {\"ZJJE\":[0,0,-715017,484999,0,0,0,0,0,0,0,0,0,0,-336154,427825,0,0,0,0,311907,60020,-307949,-3477854,-476202,0,-357327,0,-693120,0,0,359041,563813,0
    """
    base = {"a": "GetBidVolKLine", "c": "StockLineData", "apiv": "w44"}
    base.update(extra)
    return _call("his", base)

def fetch_kpl_doc115(**extra):
    r"""竞价涨停委买额-实时接口： (apphwhq.longhuvip.com) -> dict
    a=MorningBiddingList, c=HomeDingPan, apiv=w41 + extra
    resp 示例: {\"info\":[[\"300862\",\"\\u84dd\\u76fe\\u5149\\u7535\",39.41,20.01,2352414428,20.01,17708290,1.39,76736590,65369367,76736590,\"\\u5e76\\u8d2d\\u91cd\
    """
    base = {"a": "MorningBiddingList", "c": "HomeDingPan", "apiv": "w41"}
    base.update(extra)
    return _call("after", base)


# 共生成 87 个 fetch_kpl_doc{N} 函数

# ==================== 竞价异动日终快照(历史回看) ====================
def save_auction_history(date, phase="bid"):
    """抓当日竞价异动各 tab 落库 auction_daily_history
    phase='bid'  (9:26-9:30 竞价窗口调用, 竞价类数据必须此时落库!):
        seal(竞价委买)/boom(竞价爆量)/qiangcang(抢筹list20)
        ⚠️ 这些是竞价实时接口, 15:30 收盘后返回空 → 必须在 9:30 前落库
    phase='close' (15:30 日终调用, 非竞价类):
        yest_zt(昨日涨停)/yest_broken(昨断板)/broken_yest(昨炸板)/broken_today(今炸板)
    返回落库 tab 数; 某 tab 抓取失败不影响其他"""
    import sqlite3 as _sql
    if phase == "bid":
        items = [
            ("seal", fetch_bid_seal()),
            ("boom", fetch_bid_boom()),
            ("qiangcang", (fetch_bid_qiangcang() or {}).get("list20", [])),
            ("bid_net", fetch_bid_net()),   # 2026-08-22: 竞价净额榜加入落库, 支持历史回看
        ]
    else:
        items = [
            ("yest_zt", fetch_yest_zt()),
            ("yest_broken", fetch_yest_broken()),
            ("broken_yest", fetch_broken_zt("yesterday")),
            ("broken_today", fetch_broken_zt()),
        ]
    n = 0
    for tab, lst in items:
        if not lst:
            log.warning("竞价异动快照[%s] date=%s 抓取为空, 跳过", tab, date)
            continue
        try:
            # 2026-08-22: 落库前补竞换/竞额, 否则历史回看/非交易日回退这两列空
            # 🔴 2026-09-29: boom 也纳入 —— 落库行的流通列此前是开盘啦榜单自带口径(与其它 tab
            #   差近一倍), 统一用自采快照的实际流通覆盖。
            if phase == "bid" and tab in ("seal", "bid_net", "boom"):
                fill_bid_turnover_from_snap(lst, date)
                if tab == "bid_net":
                    fill_bid_amt_from_snap(lst, date)
            conn = _sql.connect(config.DB_FILE)
            conn.execute(
                "INSERT OR REPLACE INTO auction_daily_history (date, tab, list, ts) VALUES (?,?,?,?)",
                (date, tab, json.dumps(lst, ensure_ascii=False), int(time.time())))
            conn.commit()
            conn.close()
            n += 1
        except Exception as e:
            log.warning("竞价异动快照落库失败 date=%s tab=%s err=%s", date, tab, e)
    log.info("竞价异动快照[%s] date=%s 落库 %d 个 tab", phase, date, n)
    return n


def query_auction_history(date, tab):
    """读取某日某 tab 竞价异动历史快照; 无数据返回 []"""
    try:
        import sqlite3 as _sql
        conn = _sql.connect(config.DB_FILE)
        row = conn.execute(
            "SELECT list FROM auction_daily_history WHERE date=? AND tab=?",
            (date, tab)).fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        log.warning("竞价异动历史查询失败 date=%s tab=%s err=%s", date, tab, e)
    return []


_CLOSE_CHG_CACHE = {}       # date -> (ts, {code: pct}) 内存热缓存
_CLOSE_CHG_TTL = 6 * 3600
# 交易日盘中被旧代码误写的脏"收盘涨幅"(实为竞价涨幅)自愈: 记录已强制重拉纠正过的日期
_CLOSE_CHG_RESYNCED = set()

try:
    import re as _re_cls_chg
    _CLOSE_CHG_JSON_RE = _re_cls_chg.compile(r"=\s*(\{[\s\S]*\})\s*;?\s*$")
except Exception:
    _CLOSE_CHG_JSON_RE = None


def _close_chg_db_get(date, codes):
    """从 close_change_history 批量读 pct 命中; 返回 {code: pct}"""
    out = {}
    if not codes or not date:
        return out
    try:
        from ..db import database
        conn = database.get_conn()
        for code in codes:
            row = conn.execute(
                "SELECT pct FROM close_change_history WHERE date=? AND code=?",
                (date, code)).fetchone()
            if row:
                out[code] = row[0]
        conn.close()
    except Exception:
        pass
    return out


def _close_chg_persist_allowed(date):
    """是否允许把 date 的收盘涨幅持久化到 close_change_history。

    根因修复(2026-08-24): 盘中(未收盘)当日 K 线的 last close 是实时价,
    此时把"当日涨幅"当"当日收盘涨幅"写入会永久污染该日数据 —— 盘后/历史
    回看 fill_close_change_from_kline 先命中库表读到脏值, 导致现涨=竞涨/
    现涨错误(用户反馈)。    规则: date<今天 → 早已收盘, 允许; date==今天 →
    仅北京时间已过 15:00(收盘)才允许; 其它 → 禁止。

    ★ 2026-09-27 v4.11.66 补交易日门禁(**第一道**, 先于收盘判断):
      原规则只有"是否已收盘"这一个维度, **完全没有交易日判断** ⇒
        · `date < 今天` 一律放行 ⇒ 任何以"上一天/回退日"为 serve_date 的写路径都能把
          休市日 K 线固化下来;
        · `date == 今天 且 已过 15:00` 放行 ⇒ **周六/节假日 15:00 后**直接命中。
      实测后果: 测试机 2026-09-26(周六) 在 `close_change_history` 落 57 行、09-05(周六)
      落 47 行 —— 而该表的读侧是"按 date 精确命中"+ 多处"取最近日期"兜底 ⇒ 非交易日行
      会被当"当日收盘涨幅"。非交易日本就没有收盘价, 写进去的是相邻交易日 K 线的重复值。
      `is_trade_day` 对**覆盖范围外的年份** fail-open(见 trade_calendar), 不会误拦历史回填。
    """
    import time as _t
    if not date:
        return False
    if not trade_calendar.is_trade_day(date):      # ★ v4.11.66: 非交易日绝不落库
        return False
    today_bj = _t.strftime("%Y-%m-%d", _t.gmtime(_t.time() + 8 * 3600))
    if date < today_bj:
        return True
    if date > today_bj:
        return False
    bj = _t.gmtime(_t.time() + 8 * 3600)
    return (bj.tm_hour, bj.tm_min) >= (15, 0)


def _close_chg_db_put(date, pairs):
    """把 {code: pct} 持久化到 close_change_history, 后续历史回看免请求东财"""
    if not pairs or not date:
        return
    try:
        from ..db import database
        conn = database.get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO close_change_history(date,code,pct) VALUES(?,?,?)",
            [(date, c, v) for c, v in pairs.items()])
        conn.commit()
        conn.close()
    except Exception:
        pass


def _close_chg_pct_sina(date, code):
    """新浪日K取某股某日收盘涨跌幅(%)兜底; 失败返回 None"""
    try:
        import urllib.request, ssl, json as _json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sym = "sh" + code if code[0] == "6" else ("bj" + code if code[0] in "48" else "sz" + code)
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData"
               f"?symbol={sym}&scale=240&ma=no&datalen=160")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        with _urlopen(req, timeout=12, context=ctx) as r:
            arr = _json.loads(r.read().decode("utf-8", "ignore"))
        prev = None
        for row in arr:
            d = str(row.get("day", ""))[:10]
            c = float(row.get("close") or 0)
            if d == date and prev:
                return round((c - prev) / prev * 100, 2)
            if c:
                prev = c
    except Exception:
        pass
    return None


def _close_chg_pct_tencent(date, code):
    """腾讯日K(qq-web行情)取某股某日收盘涨跌幅(%)兜底; 失败返回 None"""
    try:
        import urllib.request, ssl, json as _json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sym = "sh" + code if code[0] == "6" else ("bj" + code if code[0] in "48" else "sz" + code)
        # 腾讯 qq 日K: 最近 160 根日线足够回溯 ~8 月
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,160,qfq")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with _urlopen(req, timeout=12, context=ctx) as r:
            txt = r.read().decode("utf-8", "ignore")
        obj = _json.loads(txt)
        # data.{sym}.qfqday / data.{sym}.day
        dat = (obj.get("data") or {}).get(sym) or {}
        arr = dat.get("qfqday") or dat.get("day") or []
        prev = None
        for row in arr:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            d = str(row[0])[:10]
            c = float(row[2] or 0)
            if d == date and prev:
                return round((c - prev) / prev * 100, 2)
            if c:
                prev = c
    except Exception:
        pass
    return None


def _close_chg_pct_ths(date, code):
    """同花顺(10jqka)日线接口取某股某日收盘涨跌幅(%)兜底; 失败返回 None"""
    try:
        import urllib.request, ssl, json as _json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # 同花顺 10jqka 代码: 沪=1_xxxxxx 深=0_xxxxxx 北=1_xxxxxx(保守)
        if code[0] == "6":
            secid = f"1_{code}"
        elif code[0] in "48":
            secid = f"1_{code}"
        else:
            secid = f"0_{code}"
        url = (f"https://d.10jqka.com.cn/v6/line/hs_{secid}/01/last.js")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://stockpage.10jqka.com.cn/{code}/"})
        with _urlopen(req, timeout=12, context=ctx) as r:
            js = r.read().decode("gbk", "ignore")
        # last.js 返回 json_hex = {...}
        m = _CLOSE_CHG_JSON_RE.search(js) if _CLOSE_CHG_JSON_RE else None
        if not m:
            return None
        obj = _json.loads(m.group(1))
        # klines: "date,open,high,low,close,vol,amount"
        rows = obj.get("data") or obj.get("klines") or []
        prev = None
        for row in rows:
            parts = row.split(",") if isinstance(row, str) else row
            if not parts or len(parts) < 5:
                continue
            d = str(parts[0])[:10]
            c = float(parts[4] or 0)
            if d == date and prev:
                return round((c - prev) / prev * 100, 2)
            if c:
                prev = c
    except Exception:
        pass
    return None


def fill_close_change_from_kline(lst, date):
    """历史回看: 把列表中股票 change/realChange 覆盖为所选交易日 date 的**当日收盘涨跌幅(%)
    数据来源优先级: 进程内存 → close_change_history 库表(持久化) → 多源日K(缺失才拉, 并写库)。
    多源顺序: fetch_stock_chart_robust(东财→腾讯) → 新浪 → 腾讯 → 同花顺。
    因此历史日首次补齐后, 后续回看不再请求外部接口。返回被覆盖的股票数。"""
    if not lst or not date:
        return 0
    from ..services import fetcher
    now = time.time()
    for k, (ts, _) in list(_CLOSE_CHG_CACHE.items()):
        if now - ts > _CLOSE_CHG_TTL:
            _CLOSE_CHG_CACHE.pop(k, None)
    ent = _CLOSE_CHG_CACHE.get(date)
    if ent is None or now - ent[0] > _CLOSE_CHG_TTL:
        ent = (now, {})
        _CLOSE_CHG_CACHE[date] = ent
    table = ent[1]
    today_bj = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    # ★ 2026-09-28 修复「盘前时段整页卡 15 秒」:
    #   目标日 == 今天 且**今天尚未开盘**(北京 < 09:15) 时, 今天连一笔竞价都没有 ⇒
    #   多源日K 里不可能有"今天那根"(下方 _one 判 `str(t)[:10] == date` 必然落空),
    #   但整体超时要白等满 _FILL_TIMEOUT(15s), 而且把上百个请求并发打出上游。
    #   生产实测(2026-09-28 02:00, freeze_day=今天): 「竞价爆量」157 只**无一命中**、
    #   `_CLOSE_CHG_CACHE[今天]` 恒为 0 只; 日志「现涨K线兜底整体超时 15s, 放弃剩余 6 只」
    #   并伴随大量「猫爪 429 限流 a=daily」⇒ 「竞价爆量」「昨涨停」「龙虎榜」三个 tab
    #   各自的 15.0s 全部耗在这里(接口自身 loader 仅 2~646ms)。
    #   短路只跳过"注定拿不到"的拉取, **不动任何字段**(现涨仍由快照/东财全市场 map 提供)
    #   ⇒ 盘前表现为"显示最新可得的定格值", 与 `_close_chg_persist_allowed()` 的
    #     「未收盘不落库」判据同源 ⇒ 零语义变更。
    #   注: 09:15 之后不短路 —— 那时日K可能已有今天那根, 保留原有实时覆盖行为。
    #   覆盖用例见 backend/tests/test_perf_v41174.py（含 09:14/09:15 边界、回看日不得被短路、
    #   以及"短路条件置 False / 丢掉 date==today_bj 守卫 / 边界放宽"四组变异验证）。
    if date == today_bj:
        _bjt = time.gmtime(time.time() + 8 * 3600)
        if (_bjt.tm_hour, _bjt.tm_min) < (9, 15):
            return 0
    # 收盘自愈(2026-08-24): 盘中旧代码把"竞价涨幅"误当"当日收盘涨幅"写入 close_change_history,
    # 导致收盘/历史回看时现涨=竞涨。针对"今天且已收盘"一次性强制重拉纠正脏值(去重, 之后走库/缓存)。
    force_resync = (date == today_bj and _close_chg_persist_allowed(date)
                    and date not in _CLOSE_CHG_RESYNCED)
    # 1) 缺的 code 先查库命中
    todo = [it for it in lst if it.get("code") and it.get("code") not in table]
    if todo:
        dbhit = _close_chg_db_get(date, [it["code"] for it in todo])
        for it in todo:
            v = dbhit.get(it["code"])
            if v is not None:
                table[it["code"]] = v
        todo = [it for it in todo if it["code"] not in table]
    # 收盘自愈(2026-08-24): 今日盘中旧代码误写的脏"收盘涨幅"=竞价涨幅, 收盘后强制纠正。
    # 优先用批量实时行情(单次分页拉全市场, 收盘后其"实时涨幅"即当日收盘涨幅, 避免逐只日K→限流熔断);
    # 未命中批量行情的才落到逐只多源日K兜底。
    fetched = {}
    if force_resync:
        todo = [it for it in lst if it.get("code")]
        if todo:
            try:
                from ..services import fetcher as _fet, scorer as _sco
                spot = _fet.fetch_spot_quote_map(_sco.market_fs(list(_sco.ALL_MARKETS)))
                got = 0
                for it in todo:
                    q = spot.get(str(it["code"])) if spot else None
                    if not q:
                        continue
                    rc = q.get("realChange")
                    if rc is None:
                        rc = q.get("change")
                    if rc is not None:
                        val = float(rc) if rc else 0.0
                        table[it["code"]] = val
                        fetched[it["code"]] = val
                        got += 1
                log.info("收盘自愈: 批量实时行情纠正今日收盘涨幅 %d 只 date=%s", got, date)
            except Exception as e:
                log.warning("收盘自愈 批量行情失败(转逐只日K兜底) date=%s err=%s", date, e)
        todo = [it for it in todo if it["code"] not in table]
        _CLOSE_CHG_RESYNCED.add(date)
    # 2) 仍缺的才拉多源日K, 并写库持久化(任一源命中即写入；收盘自愈命中批量行情的也已写库)

    def _one(it):
        code = it.get("code") or ""
        try:
            # 2a) robust chart (东财→腾讯, 见 fetcher.fetch_stock_chart_robust)
            k = fetcher.fetch_stock_chart_robust(code, "day")
            if k and k.get("time"):
                times, closes = k["time"], k["close"]
                for i, t in enumerate(times):
                    if str(t)[:10] == date:
                        if i > 0 and closes[i - 1]:
                            v = round((closes[i] - closes[i - 1]) / closes[i - 1] * 100, 2)
                            table[code] = v
                            fetched[code] = v
                            return
            # 2b) 新浪日K兜底
            v = _close_chg_pct_sina(date, code)
            if v is not None:
                table[code] = v
                fetched[code] = v
                return
            # 2c) 腾讯日K兜底
            v = _close_chg_pct_tencent(date, code)
            if v is not None:
                table[code] = v
                fetched[code] = v
                return
            # 2d) 同花顺日K兜底
            v = _close_chg_pct_ths(date, code)
            if v is not None:
                table[code] = v
                fetched[code] = v
                return
        except Exception:
            pass

    if todo:
        import concurrent.futures as cf
        # 2026-08-31 线上事故修复: 数据源(东财/同花顺)熔断抖动时, 多源日K逐只兜底无整体超时,
        # 曾导致三时点榜接口卡 693s 占死全部 worker → 全站刷不出数据。
        # 现在整体超时 15s: 超时未完成的放弃(不阻塞当前请求), 已提交任务留在常驻池排队。
        # 2026-09-01: 线程爆炸修复 — 由每次新建池+shutdown(wait=False) 改进程级常驻池 _EXECUTOR_FILL
        _FILL_TIMEOUT = 15
        ex = _EXECUTOR_FILL
        futs = [ex.submit(_one, it) for it in todo]
        try:
            for f in cf.as_completed(futs, timeout=_FILL_TIMEOUT):
                pass
        except cf.TimeoutError:
            log.warning("现涨K线兜底整体超时 %ds, 放弃剩余 %d 只 (数据源抖动, 下次回看自动补齐)",
                        _FILL_TIMEOUT, sum(1 for f in futs if not f.done()))
    # 收盘自愈修复(2026-08-24): 批量实时行情已把纠正值写入 fetched 并把 todo 清空,
    # 持久化必须放在 if todo 之外, 保证批量命中的纠正值也能写回库表。
    if fetched and _close_chg_persist_allowed(date):
        _close_chg_db_put(date, fetched)
    n = 0
    for it in lst:
        v = table.get(it.get("code"))
        if v is None:
            continue
        it["change"] = v
        it["realChange"] = v
        it["real_change"] = v   # 2026-08-24: 统一回填 real_change(三时点表展示 key)
        n += 1
    return n


# ==================== KPL 首屏接口预热(2026-09-04) ====================
# 背景: 竞价异动首页 loadAll 同时请求 yidong/sentiment/bid-seal 等 9 个 KPL key,
#       缓存 TTL(15-60s) 内首用户/轮询周期>TTL 后全部 miss → 各自调 loader 抢
#       KPL sem(limit=3) 排队 → 首屏 1.7-2.0s(生产 nginx maxRt 锁死 1.71s)
# 修复: 交易日 9:15-15:05 后台线程每 12s 预拉首屏 key 写缓存(single-flight 已保证
#       同刻只 1 个 loader), 用户请求路径 100% 命中缓存(<50ms) 不再打外网
_KPL_PREWARM_PERIOD = 12        # 秒; < KPL_YIDONG_TTL(15) 保证常新鲜
_kpl_prewarm_started = False    # 幂等: 重复 startup 不叠线程


def kpl_prewarm_active(now_ts):
    """KPL 首屏预热窗口: 工作日北京时间 9:15-15:05(竞价+盘中+尾盘)"""
    g = time.gmtime(now_ts + 8 * 3600)
    if g.tm_wday >= 5:
        return False
    hm = g.tm_hour * 60 + g.tm_min
    return 9 * 60 + 15 <= hm <= 15 * 60 + 5


def _kpl_prewarm_once():
    """预拉首屏 key(顺序执行避免与真实请求抢 KPL sem 槽位)
    注意: 必须先 store.delete 再调 fn —— _cached 在 TTL 内命中直接返回不重写,
    预热周期 < TTL 时若不清缓存, 调用全部命中空转, 真实刷新被 TTL 拉长,
    缓存到期瞬间仍有空窗(与 spot-prewarm 直写内存 map 语义对齐)。
    只预热 yidong 4 key(TTL 15s 最短): sentiment(60s)/bid_seal(30s)/bid_net(30s)
    TTL 较长, 冷时 single-flight 只放行 1 个 loader(0.3-0.6s) 已足够 → 不浪费 KPL
    付费配额(80000/日): 12s×4key×1worker ≈ 1200 次/小时, 占盘中日配额 <10%
    2026-09-04 追加 market_brief_payload(TTL 30s): 它是首屏最慢接口(冷 1174ms),
    且子调用 fetch_market_breadth 走 xuangubao(非 KPL 付费配额), 预热成本低;
    重算时子调用 fetch_market_brief 命中 300s 跨进程缓存, 不会每 12s 拉全市场"""
    for name, fn in (
        ("yidong_doc90", fetch_kpl_doc90),
        ("yidong_doc108", fetch_kpl_doc108),
        ("yidong_doc109", fetch_kpl_doc109),
        ("yidong_pianli_hot", fetch_kpl_pianli_hot),
        (_MB_PAYLOAD_KEY, fetch_market_brief_payload),
    ):
        try:
            # yidong_* 走 _cached → 缓存 key 带 "kpl:" 前缀;
            # market_brief_payload 的 key 本身已含命名空间, 不再加前缀
            ck = ("kpl:" + name) if name.startswith("yidong_") else name
            store.delete(ck)      # 强制 miss → fn 内部 loader 重拉并写缓存
            data = fn()
            if data is None:
                log.warning("KPL预热 %s 返回空(数据源抖动, 下轮自愈)", name)
        except Exception as e:
            log.warning("KPL预热 %s 异常 err=%s", name, str(e)[:100])


def _kpl_prewarm_loop():
    """常驻后台循环(main.py startup 启动; 每 web worker 各一份, 与 spot-prewarm 同模式)
    跨 worker 协调: uvicorn --workers 2 → 2 个循环都会到点执行, 用 cache_store.setnx
    抢 12s 窗口锁, 只有抢到的 worker 真拉(避免双倍 KPL 调用浪费付费配额)"""
    while True:
        try:
            if kpl_prewarm_active(time.time()):
                # setnx 抢窗口锁(ttl=周期*1.5): 抢到者执行, 未抢到跳过本轮
                if store.setnx("kpl_prewarm:turn", 1, ttl=int(_KPL_PREWARM_PERIOD * 1.5)):
                    try:
                        _kpl_prewarm_once()
                    finally:
                        store.delete("kpl_prewarm:turn")
        except Exception as e:
            log.warning("KPL预热循环异常 err=%s", str(e)[:100])
        time.sleep(_KPL_PREWARM_PERIOD)


def start_kpl_prewarm():
    """启动 KPL 首屏接口预热线程: 用户打开竞价异动页时 9 个 KPL key 恒热命中。
    幂等: 重复调用不起第二线程"""
    global _kpl_prewarm_started
    if _kpl_prewarm_started:
        return
    _kpl_prewarm_started = True
    t = threading.Thread(target=_kpl_prewarm_loop, daemon=True, name="kpl-prewarm")
    t.start()
    log.info("KPL首屏预热线程已启动(工作日9:15-15:05每%ss刷新一次)", _KPL_PREWARM_PERIOD)


# ==================== KPL 回看日预热(2026-09-28) ====================
# 背景(生产实测): 回看日(date=) 的「竞价抢筹」要打 4 个「全市场 5000+ 行」的猫爪接口。
#   方案 A(v4.11.74) 已让历史日上游缓存保持 3600s, 但生产日活仅 ~8.7 人 ⇒ 某个回看日
#   只要 1 小时无人访问, 上游就过期 ⇒ 首次访问全额冷取数 **4.8~7.4s**
#   (2026-09-28 生产实测: 9/21 6288ms / 9/22 4821ms / 9/23 7404ms / 9/24 6152ms,
#    而同一天再访问仅 139~429ms —— 缓存本身正常, 缺的是"保持热")。
#   ⇒ 后台把最近 N 个交易日的回看结果保持热, 用户首次访问从秒级降到亚秒级。
#
# 只预热「竞价抢筹」一个接口: 同日实测同样带 date 的 bid-seal 152ms / bid-net 126ms /
#   yest-broken 179ms / lhb 229ms 都便宜(走 auction_daily_history 读库), 只有
#   bid-qiangcang 是秒级 —— 与既有首屏预热"只预热最贵的 key"同一取舍, 不浪费配额。
#
# 周期取 1200s(20min): 必须 < 结果层 TTL(1800s) 才能保证结果层永不冷;
#   且 1200 整除 3600(上游 TTL) ⇒ 上游到期后最近一轮预热即补上, 冷窗口最小。
#
# ⚠️ 串行 + 间隔(_KPL_REPLAY_GAP): 文档 §10 教训 —— 一次清缓存后连打十余接口会触发
#    上游 429 与 eastmoney_kline 熔断。本预热每轮最多 N 次调用, 每次间隔 1.2s。
#
# ⚠️ 必须挂 web 进程(main.py startup) 而非 kx-worker: 与首屏预热同理 —— 结果层/上游层
#    虽在 kv_cache(跨进程), 但接口层还有 fetcher._quote_map_cache(进程级), worker 预热不到。
_KPL_REPLAY_PERIOD = 1200       # 秒; < 结果层 TTL(1800) 且整除上游 TTL(3600)
_KPL_REPLAY_DAYS = 5            # 预热最近 N 个交易日
_KPL_REPLAY_GAP = 1.2           # 两次调用之间的间隔(秒)
_KPL_REPLAY_WINDOW = (8 * 60 + 30, 20 * 60)    # 服务窗口 08:30~20:00(北京时间, 两端含)
_KPL_REPLAY_SKIP = (9 * 60 + 5, 9 * 60 + 40)   # 竞价时段跳过(上游此时最紧张)
_kpl_replay_started = False     # 幂等: 重复 startup 不叠线程
_kpl_replay_last_active = None  # 仅在"进入/退出窗口"各播报一次(逐轮打日志会刷屏)


def kpl_replay_prewarm_active(now_ts):
    """回看日预热窗口: 08:30~20:00 且非竞价时段(北京时间)

    🔴 2026-09-28 主人拍板收敛（原为"全天可用, 仅跳竞价时段"）。生产实测依据:
      收盘后 `20:58/21:18/21:38/21:58/22:18/22:38/22:58` 每 20 分钟仍在跑一轮(7 轮)，
      而回看数据是**历史、不可变**的 —— 结果层 TTL 1800s、上游 TTL 3600s ⇒ 夜里无人
      访问时，每轮预热出的"热"在**下一个用户到来前必然已过期** ⇒ 纯粹是重复打上游
      (生产机已有猫爪 429 记录)。
      ⇒ 收敛到**服务窗口 08:30~20:00**：
        · 08:30 起先焐热 5 个回看日，恰好覆盖 09:00 起的首访高峰（4.8~7.4s 冷启动的收益保住）；
        · 20:00 后停到次日 08:30 ⇒ 夜间零上游调用。
      窗口内的竞价时段(09:05~09:40)仍照旧跳过。
    """
    g = time.gmtime(now_ts + 8 * 3600)
    hm = g.tm_hour * 60 + g.tm_min
    if hm < _KPL_REPLAY_WINDOW[0] or hm > _KPL_REPLAY_WINDOW[1]:
        return False
    return not (_KPL_REPLAY_SKIP[0] <= hm <= _KPL_REPLAY_SKIP[1])


def _kpl_replay_dates(n=_KPL_REPLAY_DAYS):
    """最近 n 个交易日(从昨天往前; 项目纪律: 判"哪一天"必须走交易日历)"""
    out = []
    day = trade_calendar.bj_date()
    for _ in range(n):
        try:
            prev = trade_calendar.prev_trade_date(day)
        except Exception:                                   # noqa: BLE001
            break
        if not prev or prev >= day:
            break
        out.append(prev)
        day = prev
    return out


def _kpl_replay_prewarm_once():
    """串行预热最近 n 个交易日的「竞价抢筹」回看结果(失败不抛, 下轮自愈)"""
    dates = _kpl_replay_dates()
    if not dates:
        log.warning("KPL回看预热: 取不到交易日(跳过本轮)")
        return
    t0 = time.time()
    ok = 0
    for d in dates:
        try:
            if fetch_bid_qiangcang(d):
                ok += 1
        except Exception as e:                              # noqa: BLE001
            log.warning("KPL回看预热 %s 异常 err=%s", d, str(e)[:100])
        time.sleep(_KPL_REPLAY_GAP)
    log.info("KPL回看预热完成 %d/%d 天 耗时%.1fs", ok, len(dates), time.time() - t0)


def _kpl_replay_prewarm_loop():
    """常驻后台循环: 与首屏预热同模式(每 web worker 一份 + setnx 跨进程抢锁)"""
    global _kpl_replay_last_active
    win = "%02d:%02d~%02d:%02d" % (_KPL_REPLAY_WINDOW[0] // 60, _KPL_REPLAY_WINDOW[0] % 60,
                                   _KPL_REPLAY_WINDOW[1] // 60, _KPL_REPLAY_WINDOW[1] % 60)
    while True:
        try:
            active = kpl_replay_prewarm_active(time.time())
            if active != _kpl_replay_last_active:
                # 只在"进入/退出窗口"各播报一次 —— 循环每 20min 一轮, 逐轮打日志会刷屏;
                # 但这一条必须留: 否则"夜里有没有停"从日志上就看不出来了(2026-09-28 教训)
                if active:
                    log.info("KPL回看预热进入服务窗口(%s), 恢复预热", win)
                else:
                    log.info("KPL回看预热已离开服务窗口(%s), 停歇期间不再打上游", win)
                _kpl_replay_last_active = active
            if active:
                if store.setnx("kpl_replay_prewarm:turn", 1,
                               ttl=int(_KPL_REPLAY_PERIOD * 1.5)):
                    try:
                        _kpl_replay_prewarm_once()
                    finally:
                        store.delete("kpl_replay_prewarm:turn")
        except Exception as e:                              # noqa: BLE001
            log.warning("KPL回看预热循环异常 err=%s", str(e)[:100])
        time.sleep(_KPL_REPLAY_PERIOD)


def start_kpl_replay_prewarm():
    """启动回看日预热线程(幂等): 最近 N 个交易日的竞价抢筹恒热, 首次访问亚秒级"""
    global _kpl_replay_started
    if _kpl_replay_started:
        return
    _kpl_replay_started = True
    t = threading.Thread(target=_kpl_replay_prewarm_loop, daemon=True,
                         name="kpl-replay-prewarm")
    t.start()
    log.info("KPL回看预热线程已启动(每%ds预热最近%d个交易日的竞价抢筹)",
             _KPL_REPLAY_PERIOD, _KPL_REPLAY_DAYS)
