# -*- coding: utf-8 -*-
"""
开盘啦数据路由: 竞价委买额/连板梯队/情绪值/涨停原因/板块强度
==========================================================
所有接口均走 kpl 服务(缓存 + 降级), 失败返回空列表/None, 不影响主流程。
"""
import time as _time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..core import trade_calendar as tc
from ..services import kpl, sector_rotation
from .deps import get_uid, jr, quota_guard, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()


# ====================================================================
# Fast-path 辅助函数 (竞价时段/非竞价时段分流)
# ====================================================================

def _bj_now():
    """当前北京时间 struct_time (服务器走 UTC, 需 +8h 才是北京时间)。
    项目惯例: 北京时间 = time.gmtime(time.time() + 8*3600)

    🔴 2026-09-29 修(可测性缝隙, 生产行为零变化): 原实现在函数体里 `import time as _t`
      —— 那会**绕开本模块的 `_time`**(全仓测试都用 `monkeypatch.setattr(api_kpl, "_time", 固定时钟)`
      注入时刻)。后果: `tests/test_close_change_daily.py::test_apply_change_for_uses_beijing_today`
      只有在**真实日期恰好等于夹具日期**那天才通过, 之后**永久变红**(实测 2026-09-29 起必红,
      与本次 ①②③ 改动无关 —— 用改动前代码跑同样失败)。改用模块级 `_time` 后与本文件其它
      时钟调用同一口径; 生产环境没人打桩 `_time`, 取到的是同一个真 time 模块。
    """
    return _time.gmtime(_time.time() + 8 * 3600)


def _is_auction_hours():
    """当前北京时间是否在竞价时段 (9:15 ~ 9:30), **仅交易日**
    (2026-08-24 修复: 此前用 UTC 判断致竞价时段误判;
     2026-09-27 v4.11.67: 门禁由"非周末"升级为**交易日历** —— 法定休市日如 2026-09-25
     中秋(周五)此前会被当成交易日 ⇒ 非交易日照样走实时分支, 页面不定格。
     主人指令:「**非交易日数据要定格才行**」)"""
    t = _bj_now()
    if not tc.is_trade_day_of(t):   # 含周末 + 法定休市日(先看 tm_wday, 兼容不完整替身)
        return False
    h, m = t.tm_hour, t.tm_min
    if h == 9 and 15 <= m <= 30:
        return True
    return False


def _is_intraday():
    """当前北京时间是否盘中 (9:30 ~ 15:00), **仅交易日**。
    只有盘中现涨(change/realChange)才用实时接口刷新; 盘后/非交易一律用当日收盘固定值。
    (2026-08-24 修复: 此前用 UTC 判断, 盘中/竞价时段全被误判为非盘中)
    (2026-09-27 v4.11.67: 同上, 门禁升级为交易日历)"""
    t = _bj_now()
    if not tc.is_trade_day_of(t):
        return False
    h, m = t.tm_hour, t.tm_min
    if h < 9 or h > 15:
        return False
    if h == 9 and m < 30:
        return False
    if h == 15 and m > 0:
        return False
    return True


def _apply_change_for(lst, serve_date):
    """统一设置列表现涨(change/realChange)口径:
       - 盘中 且 展示的就是"今天" → 东财实时(单次批量, _update_spot_change)
       - 其余全部(历史回看/回退的上交易日/盘后今日/非交易日) → 该交易日收盘涨幅(固定, 不调实时)
    注: 盘中查看历史日期时, 现涨也应是历史那天收盘涨幅, 只有"盘中看今日"才用实时。
    serve_date: 当前展示的交易日 'YYYY-MM-DD'(历史/回退=对应日, 今日=今日)。"""
    if not lst:
        return
    # 2026-09-28 修复: 原用 UTC(`_time.gmtime()`)判"今天" —— 北京时间 00:00~08:00 时
    # UTC 日期还是前一天 ⇒ `serve_date == today` 失效, 把"盘中看今日"误判成历史日,
    # 于是拿不到实时现涨(改用当日收盘涨幅)。与本文件既有的 `_bj_now()` 统一。
    today = _time.strftime("%Y-%m-%d", _bj_now())
    if _is_intraday() and serve_date == today:
        _update_spot_change(lst)
        return
    try:
        kpl.fill_close_change_from_kline(lst, serve_date)
    except Exception as e:
        log.warning("当日收盘涨幅覆盖失败 date=%s err=%s", serve_date, e)


def _update_spot_change(lst):
    """用东方财富实时行情覆盖列表中股票的 change / realChange 字段。
    只改 change / realChange 两个字段, 其他字段(name/board/...)绝不变动。
    返回被更新的股票数量; 任何异常都返回 0, 不崩。
    (2026-08-24 修复: 此前传 ",".join(codes) 给 fetch_spot_quote_map, 但该函数
     fs 参数需市场过滤串(如 m:0+t:6,...), 传代码串致东财返回空 → 现涨永远不更新)"""
    if not lst:
        return 0
    try:
        from ..services import fetcher, scorer
        # 2026-09-29 P0③: 先按代码点查(ulist, 每批60) —— 原来为这几十~几百只拉全市场
        # 5561 只(33 请求), 竞价窗口里最不该浪费; 点查失败才退回全市场 spot map(不降可用性)
        codes = [str(it.get("code") or "") for it in lst if it.get("code")]
        spot_map = fetcher.fetch_spot_quote_map_by_codes(codes)
        if not spot_map:
            spot_map = fetcher.fetch_spot_quote_map(scorer.market_fs(list(scorer.ALL_MARKETS)))
        if not spot_map:
            return 0
        n = 0
        for it in lst:
            code = it.get("code", "")
            if code and code in spot_map:
                spot = spot_map[code]
                rc = spot.get("realChange", spot.get("change"))
                if rc is not None:
                    it["change"] = float(rc) if rc else 0
                    it["realChange"] = it["change"]
                    n += 1
        return n
    except Exception:
        return 0


_CONCEPT_CACHE = {}          # code -> (board, ts)：进程内概念缓存（概念是静态属性）
_CONCEPT_TTL = 24 * 3600


def _ensure_concepts(lst, tag=""):
    """保证列表中至少有 ~30% 股票带概念/板块。

    🔴 性能修复（2026-10-03 实测）：原实现在概念比例 <30% 时调
       `kpl.apply_board_concept(deep=False)` ⇒ **逐股实时查开盘啦**：6 条列表耗时 **1.67s**，
       三张表叠加 2~5s ⇒ 这是「竞价异动打开很慢」的主因（nginx 实测 1.04s / 峰值 4.51s）。
       概念是**静态属性**（某票属于什么板块不随日期变化）⇒ 进程内缓存 24h，命中即免外网；
       且只对「库内 + 缓存都没命中」的股票出网一次（原实现每请求全量重查）。
    """
    if not lst:
        return
    now = _time.time()
    for it in lst:                                    # ① 先吃进程内缓存
        c = str(it.get("code") or "")
        v = _CONCEPT_CACHE.get(c)
        if v and now - v[1] < _CONCEPT_TTL and not (it.get("board") or "").strip() and v[0]:
            it["board"] = v[0]
    total = len(lst)
    filled = sum(1 for it in lst if (it.get("board") or "").strip())
    if total and filled / total >= 0.30:
        return
    miss = [it for it in lst if not (it.get("board") or "").strip()
            and (now - _CONCEPT_CACHE.get(str(it.get("code") or ""), (None, 0))[1]) >= _CONCEPT_TTL]
    if not miss:                                      # ② 缺的都在缓存里（含"确实没有"的负结果）
        return
    try:
        kpl.apply_board_concept(miss, log_tag=tag or "auc", deep=False,
                                field="board", truncate=2, blank_if_missing=False)
    except Exception as e:
        log.warning("竞价异动概念补齐失败 tag=%s err=%s", tag, e)
        return
    for it in miss:                                   # ③ 结果写回缓存（含空值，避免反复出网）
        c = str(it.get("code") or "")
        if c:
            _CONCEPT_CACHE[c] = ((it.get("board") or "").strip(), _time.time())


def _latest_trade_date_in(table, day, op="<="):
    """表内满足 `date {op} day` 的最近一个**交易日**; 无合规候选返回 None。

    ★ 2026-09-27 v4.11.66 新增。原各处直接用 `SELECT MAX(date) FROM <历史表> WHERE date<...`,
      **没有任何交易日历过滤** ⇒ 休市日落下的幽灵行会被当成"最近交易日"。2026-09-25
      (中秋·周五·法定休市)当天尚无语料门禁, 照常采集并落库了 7 个 tab ⇒ 09-27(周日)
      竞价委买/爆量/净额/炸板/昨涨停等 tab 全被顶成休市日静态值(爆量、昨涨停直接变空)。
      同源事故说明见 `core/trade_calendar.py:latest_trade_in()`。

    查库异常 / 无合规候选 → None, 由调用方**保留原值**(绝不主动留空)。
    """
    try:
        from ..db import database
        conn = database.get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT date FROM %s WHERE date%s? "
                "ORDER BY date DESC LIMIT 30" % (table, op), (day,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    return tc.latest_trade_in([r[0] for r in rows if r and r[0]], day)


def _read_auction_fast(tab):
    """非竞价时段快速读取: 优先今日落库数据; 若无则回退最近交易日; 再无返回空。
    返回 (list, date_str) — 用于竞价异动类接口 (bid-seal/bid-boom/broken)。

    2026-09-27 v4.11.67: "今日" 由**裸自然日**改为**定格基准日** `kpl.freeze_day()`
    (非交易日 → 最近一个有快照的交易日)。这样非交易日第一跳就直接命中定格日数据 ——
    改造前要先查周六/周日(必然为空)再回落到"最近交易日", 且回落出来的 serve_date
    还要再被各调用方按自然日覆盖一遍, 是"半定格"的来源之一。"""
    today = kpl.freeze_day()
    try:
        lst = kpl.query_auction_history(today, tab)
        if lst:
            _apply_change_for(lst, today)
            return lst, today
    except Exception:
        pass
    # 🔴 2026-09-29 主人铁律「零值不得回退昨日」:
    #   今天是**交易日**时, 该 tab 当日没数据就返回**空**(调用方按"暂无/待采集"展示),
    #   绝不回退到上一交易日 —— 历史上前端/后端"零值即回退"多次让用户拿到昨天的名单。
    #   只有**非交易日**(周末/法定休市)才允许走到下面的"最近交易日"对齐,
    #   因为那时"最近交易日"才是用户想看的东西。
    try:
        if tc.is_trade_day(_time.strftime("%Y-%m-%d", _bj_now())):
            return [], today
    except Exception:                                          # noqa: BLE001
        pass
    # 今日无数据 → 找最近交易日(仅非交易日会走到这里)
    # 2026-09-27 v4.11.66: 由裸 MAX(date) 改为「交易日历过滤」(休市日幽灵行不得当选);
    # 保持原 `date < today` 的严格语义 —— 今日该 tab 为空时不能回落到今日自己(那会返回空表)。
    try:
        d = _latest_trade_date_in("auction_daily_history", today, op="<")
        if d:
            lst = kpl.query_auction_history(d, tab)
            if lst:
                _apply_change_for(lst, d)
                return lst, d
    except Exception:
        pass
    return [], today


def _resolve_date(date):
    """把用户选的日期对齐到最近交易日(返回对齐后的 'YYYY-MM-DD')

    🔴 2026-09-29 修「竞价异动很多 tab 在盘中拿昨天的数据」:
      原实现**只用数据表** `daily_sector_top` 做对齐, 而该表由
      `sector_rotation.record_today_top` 在**工作日 15:30 之后**才写入 ⇒
      盘中(以及任何"当日板块数据未落库"的交易日) `_latest_trade_date_in("daily_sector_top", 今天)`
      必然退到**上一交易日** ⇒ 走本函数的**11 个端点**(封单/委买/爆量/净额/今炸板/昨炸板/
      昨涨停/昨断板/龙虎榜 …) 全部把昨天的数据当今天返回。
      生产实测(2026-09-29 13:2x, 请求 date=2026-09-29): 龙虎榜返回 date=2026-09-28(55 行)、
      昨涨停 09-28(51 行, 与 09-28 那行名单 100% 重合)、昨断板 09-28(7 行)、炸板 09-28(11 行)。

      现改为: **交易日直接返回该日**(不依赖任何数据表); 只有非交易日(周末/法定休市)才回退,
      且回退仍走数据表 + 交易日历双过滤(保留 v4.11.66 的 09-25 中秋幽灵行修复)。
      语义依据(主人铁律): "如果获取的为零就不要使用昨天的数据" —— 当日数据没落库时,
      该 tab 宁可显示空/暂无数据, 也不得静默退到上一交易日。"""
    if not date:
        return ""
    if tc.is_trade_day(date):
        return date          # 交易日: 原样返回(哪怕当日数据还没落库 —— 由调用方走"空"路径)
    d = _latest_trade_date_in("daily_sector_top", date)
    return d or date


@router.get("/api/kpl/sentiment")
def api_kpl_sentiment(request: Request, uid: int = Depends(get_uid)):
    """市场情绪: 涨停家数/情绪指标/连板高度/大幅回撤"""
    d = kpl.fetch_sentiment()
    return jr({"ok": True, "sentiment": d})


@router.get("/api/kpl/market-brief")
def api_kpl_market_brief(request: Request, uid: int = Depends(get_uid)):
    """市场概览(2026-08-16):
    - breadth: 涨跌家数分布(今日最新 + 昨日同时刻, xuangubao 开盘啦生态)
    - market: 两市股票总数 + 成交额(东财全市场, 5min 缓存)
    - last_same_time: 上一交易日同一时点的成交额 + 股票数(2026-08-16 新增,
      由 worker 每 5 分钟滚动存 market_brief_intraday_{date}, 次日起可对比)
    - last: 上一交易日收盘全天快照(settings market_brief_last, 15:30 存)
    前端据此展示 '两市总量 + 较昨日同时' 与 '涨跌家数分布'
    2026-09-04 缓存: 生产实测(uid=49, 14:40-14:50) 冷请求 1174ms 为首屏最慢接口,
    且此前完全无结果缓存。聚合逻辑已下沉到 services/kpl.py
    build_market_brief_payload, 整段结果走跨进程缓存 60s(2026-10-01 P2-6 对齐前端 60s 轮询)
    + single-flight 防击穿
    → 命中 <50ms; 预热线程每 12s 兜底刷新消除冷窗口"""
    payload = kpl.fetch_market_brief_payload()
    if not payload:      # loader 异常兜底: 直接算一次(不写缓存), 保证接口不返空
        payload = kpl.build_market_brief_payload()
    return jr(dict(payload, ok=True))


@router.get("/api/kpl/index-brief")
def api_kpl_index_brief(request: Request, uid: int = Depends(get_uid)):
    """A股核心指数实时快照 + 猫爪情绪周期(2026-09-20 首页指数带/情绪卡, 主人指令换猫爪数据)。
    指数: 猫爪 index_snapshot(批量 8 指数); 情绪: 猫爪 emoindic(最新交易日)。
    服务端 30s 缓存(与盘中 30s 刷新节奏一致)。非交易时段返回最近交易日数据。

    🔴 2026-09-29 兜底(首页第一屏不能看猫爪脸色): 本接口原先**裸调**两个猫爪函数, 上游一旦
    429/失败, 首屏指数带与情绪卡整块空。现在兜底下沉到 meoz_client:
      * 指数 → 腾讯简版(实测两机 200) → 上次成功值(标 `src=stale`);
      * 情绪 → 上次成功值(**仅当此刻仍然成立**: 盘中要求同一交易日, 见 `_emo_stale_ok`, 标 `stale=1`)。
    两个标记得以保留在响应里, 前端/排查可据此区分"实时"与"降级"。
    """
    from ..services import meoz_client
    rows = meoz_client.index_snapshot()
    emo = meoz_client.emo_daily()
    return jr({"ok": True, "list": rows, "emo": emo})


@router.get("/api/kpl/bid-seal")
def api_kpl_bid_seal(request: Request, uid: int = Depends(quota_guard("auction")), date: str = ""):
    """竞价涨停委买额: date 空=实时, 指定 'YYYY-MM-DD' 回看历史(auction_daily_history)
    2026-08-22: 竞价时段走实时 fetch_bid_seal + deep=True; 非竞价时段走 fast-path 读库
    2026-09-04 二轮缓存: 生产实测冷请求 709ms(14:45 盘后走 fast-path, 每次读库 +
    多次 fill 补齐, 此前**无接口级缓存**) → 结果缓存 + single-flight 防击穿:
    竞价时段 15s(现涨需实时感) / 盘后 300s(数据已定格) / 指定历史日 600s(不可变)"""
    from ..services.cache_store import cached_singleflight, store

    if date:
        ck = "bidseal:hist:" + date

        def _load_hist():
            resolved = _resolve_date(date)
            d = kpl.query_auction_history(resolved, "seal")
            kpl.fill_bid_turnover_from_snap(d, resolved)   # 2026-08-22: 历史快照竞换可能缺, 用当日快照补
            kpl.fill_bid_net_from_snap(d, resolved)        # 2026-09-29: 09:24 快照净额全 0 → 补 9_25 官方值
            # 2026-08-24: 开盘啦 Type4 bidChange 与自采竞价涨幅不一致 → 历史竞涨以自采快照为准强制覆盖
            kpl.fill_bid_change_from_snap(d, resolved, override=True)
            kpl.fill_close_change_from_kline(d, resolved)  # 2026-08-22: 历史回看现涨=当日收盘涨跌幅
            return {"ok": True, "list": d or [], "count": len(d) if d else 0,
                    "date": resolved, "requestedDate": date}
        return jr(cached_singleflight(store, ck, 600, _load_hist))

    auction = _is_auction_hours()
    ck = "bidseal:" + ("live" if auction else "post")

    def _load():
        if not auction:
            # 非竞价时段 → 从库快速读取 (当天优先, 历史回退)
            d, d_str = _read_auction_fast("seal")
            kpl.fill_bid_turnover_from_snap(d, d_str)   # 2026-08-22: 非交易日/历史回退补竞换
            kpl.fill_bid_net_from_snap(d, d_str)        # 2026-09-29: 同因 —— 落库快照净额全 0
            # 2026-08-24: 竞涨以自采快照为准强制覆盖(开盘啦 bidChange 不可靠)
            kpl.fill_bid_change_from_snap(d, d_str, override=True)
            _ensure_concepts(d, tag="auc:bid-seal[fast]")
            # 2026-08-23 口径统一: fast-path 也按 serve_date 覆盖现涨(避免回退到历史日时仍是"最新今天涨幅")
            try:
                _apply_change_for(d, d_str)
            except Exception as e:
                log.warning("bid-seal fast-path 现涨覆盖失败 err=%s", e)
            return {"ok": True, "list": d, "count": len(d), "date": d_str}
        d = kpl.fetch_bid_seal() or []
        # 2026-08-24 实时接口空时兜底: 竞价时段实时返空(开盘啦 Type4 偶发/未就绪) → 回退今天已落库
        if not d:
            _today = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() + 8 * 3600))
            d = kpl.query_auction_history(_today, "seal") or []
            if d:
                log.info("bid-seal 实时为空 → 回退今日落库 %d 只", len(d))
        if not d:
            # 2026-10-09 第三级兜底: 猫爪 screening.ztwme(涨停委买额) —— 仅在**实时与今日落库
            #   都返空**时触发(盘后 / 开盘啦未就绪 / 竞价早段)。
            #   对拍依据: ztwme vs 开盘啦 Type4 bidSealAmt, 10-08/10-09/09-30 三日 419 只
            #   比值精确 1.0000 ⇒ 同源同口径。开关 KX_SEAL_FALLBACK_MEOZ=0 关闭。
            d = kpl.seal_from_meoz_ztwme() or []
            if d:
                log.info("bid-seal 实时+落库均空 → 猫爪ztwme重建 %d 只", len(d))
        # 概念列统一用开盘啦接口覆盖(只取开盘啦概念, 避免东财长串多概念混入)
        try:
            kpl.apply_board_concept(d, log_tag="auc:bid-seal", deep=True,
                                    field="board", truncate=2, blank_if_missing=True)
        except Exception as e:
            log.warning("竞价异动概念开盘啦覆盖失败 bid-seal err=%s", e)
        # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
        try:
            _apply_change_for(d, kpl.freeze_day())
        except Exception as e:
            log.warning("bid-seal 现涨覆盖失败 err=%s", e)
        return {"ok": True, "list": d, "count": len(d)}

    return jr(cached_singleflight(store, ck, 15 if auction else 300, _load))


@router.get("/api/kpl/bid-boom")
def api_kpl_bid_boom(request: Request, uid: int = Depends(quota_guard("auction")), date: str = ""):
    """竞价爆量/撮合>2000万: date 空=实时, 指定日期回看历史
    2026-10-09 C2: 补接口级缓存(对齐 bid-seal)。此前本接口**无接口级缓存**, 而 boom
    每次要全市场自算 + 东财点查 ⇒ 每请求都重跑一遍, 成本明显高于另两个 tab。
    TTL 同 bid-seal: 竞价 15s(需实时感) / 盘后 300s(已定格) / 指定历史日 600s(不可变)。"""
    from ..services.cache_store import cached_singleflight, store

    if date:
        ck = "bidboom:hist:" + date

        def _load_hist():
            resolved = _resolve_date(date)
            d = kpl.query_auction_history(resolved, "boom")
            # 2026-09-05 修复"竞价爆量历史/非交易日无数据": auction_daily_history 长期无 boom 落库
            # (仅历史极早期有), 周末前端自动回退带 date.boom 会读到空 → 当日 snapshot_bid 存在则重建
            if not d:
                try:
                    d = kpl._boom_from_snap(resolved) or []
                    if d:
                        log.info("bid-boom 历史 %s 无落库 → snapshot_bid 重建 %d 只", resolved, len(d))
                except Exception as e:
                    log.warning("bid-boom 历史重建失败 err=%s", e)
                    d = []
            # 2026-08-23: 老版落库把大盘股 floatMv 错位为极小值 → 竞换荒谬; 用当日快照修复
            kpl.fill_bid_turnover_from_snap(d, resolved)
            if d:
                d = [it for it in d if (it.get("bidChange") if it.get("bidChange") is not None else 0) >= 0.01]
            # 2026-08-22 历史回看: 现涨(realChange/change)=当日收盘涨跌幅, 而非最新今天实时
            try:
                kpl.fill_close_change_from_kline(d, resolved)
            except Exception as e:
                log.warning("bid-boom 历史现涨(当日收盘)覆盖失败 err=%s", e)
            return {"ok": True, "list": d, "count": len(d), "date": resolved, "requestedDate": date}
        return jr(cached_singleflight(store, ck, 600, _load_hist))

    auction = _is_auction_hours()
    ck = "bidboom:" + ("live" if auction else "post")

    def _load():
        d = kpl.fetch_bid_boom() or []
        try:
            kpl.apply_board_concept_db(d, log_tag="auc:bid-boom", field="board", truncate=2, blank_if_missing=True)
            # 2026-08-18 主人要求: 竞价爆量补 竞价量比(今/昨竞价额) + 昨日竞价额
            kpl.fill_bid_ratio_yest(d, None)
            # 🔴 2026-09-29 口径统一: **实时路径也要补流通列** —— 开盘啦各榜自带那列口径不一
            #   (实测 600241: 榜单 25.31 亿 vs 自采快照自由流通 13.36 亿, 差 1.9 倍) ⇒ 同一票
            #   在「竞价爆量」与「竞价封单」两个 tab 差一倍。历史路径本就有这一行, 实时路径此前漏了。
            kpl.fill_bid_turnover_from_snap(d, None)
        except Exception as e:
            log.warning("竞价异动概念/量比补齐失败 bid-boom err=%s", e)
        # 统一过滤: 竞价涨幅 < 0.01%(含零/负涨幅) 不展示
        # (落库历史快照可能由旧版逻辑生成, 含零/负涨幅; 接口层兜底保证展示口径一致)
        if d:
            d = [it for it in d if (it.get("bidChange") if it.get("bidChange") is not None else 0) >= 0.01]
        # 现涨(realChange/change)口径: 盘中=实时涨幅; 盘后/非交易日=当日收盘涨幅固定值(不调实时接口)
        try:
            _apply_change_for(d, kpl.freeze_day())
        except Exception as e:
            log.warning("bid-boom 现涨覆盖失败 err=%s", e)
        return {"ok": True, "list": d, "count": len(d)}
    return jr(cached_singleflight(store, ck, 5 if kpl.bid_boom_hot_window() else (15 if auction else 300), _load))


@router.get("/api/kpl/bid-net")
def api_kpl_bid_net(request: Request, uid: int = Depends(quota_guard("auction")), date: str = ""):
    """竞价净额榜(2026-08-18 主人要求): 开盘啦 MorningBiddingList Type=2(全市场竞价金额>1000万)
    替代仅从涨停封单列表按净额排序; 非竞价时段返回空 → 前端回退封单列表
    2026-08-22: 增加历史回看(date) + 非交易/非竞价时段回退上一交易日(历史快照 → 快照重建)
    2026-10-09 C2: 补接口级缓存(对齐 bid-seal) —— 此前本接口**无接口级缓存**,
    每请求都重跑 query + 三次 fill + 概念补齐。TTL: 竞价 15s / 盘后 300s / 历史 600s。"""
    from ..services.cache_store import cached_singleflight, store

    if date:
        ck = "bidnet:hist:" + date

        def _load_hist():
            resolved = _resolve_date(date)
            d = kpl.query_auction_history(resolved, "bid_net") or []
            # 老快照未存竞换/竞额 → 用当日 9_25 快照补
            kpl.fill_bid_turnover_from_snap(d, resolved)
            kpl.fill_bid_amt_from_snap(d, resolved)
            kpl.fill_bid_net_from_snap(d, resolved)        # 2026-09-29: 09:24 快照净额全 0 → 补 9_25 官方值
            kpl.fill_close_change_from_kline(d, resolved)  # 2026-08-22: 历史回看现涨=当日收盘涨跌幅
            kpl.apply_board_concept_db(d, log_tag="auc:bid-net[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
            return {"ok": True, "list": d, "count": len(d), "date": resolved, "requestedDate": date}
        return jr(cached_singleflight(store, ck, 600, _load_hist))

    auction = _is_auction_hours()

    if not auction:
        ck = "bidnet:post"

        def _load_post():
            # 非竞价时段: 只读库。交易日当日为空 ⇒ 就让它空着(铁律); 非交易日由
            # `_read_auction_fast` 内部对齐到最近交易日。
            # 🔴 2026-09-29 移除原"无则用上一交易日 9_25 快照重建"分支 —— 那是把**昨天的票**
            #   当今天的净额榜(与主人两次反馈的"数据是昨天的"同类)。
            d, d_str = _read_auction_fast("bid_net")
            kpl.fill_bid_turnover_from_snap(d, d_str)
            kpl.fill_bid_amt_from_snap(d, d_str)
            kpl.fill_bid_net_from_snap(d, d_str)          # 2026-09-29: 同因 —— 落库快照净额全 0
            _apply_change_for(d, d_str)   # 现涨: 盘中=实时; 盘后/非交易=当日收盘固定值(不调实时)
            kpl.apply_board_concept_db(d, log_tag="auc:bid-net[fast]", field="board", truncate=2, blank_if_missing=True, date=d_str)
            return {"ok": True, "list": d, "count": len(d), "date": d_str}
        return jr(cached_singleflight(store, ck, 300, _load_post))

    ck = "bidnet:live"

    def _load_live():
        d = kpl.fetch_bid_net() or []
        if not d:
            # 2026-10-09 兜底: 猫爪 screening.auc_net_amount(竞价净额)。
            #   ⚠️ 该字段全市场多为 0(2026-10-09 实测 104 只里仅 13 只非 0) ⇒ 只在开盘啦
            #   doc112 返空时救急, **不做主源**。开关 KX_NET_FALLBACK_MEOZ=0 关闭。
            d = kpl.net_from_meoz_auc_net() or []
            if d:
                log.info("bid-net 实时为空 → 猫爪竞价净额重建 %d 只", len(d))
        # 2026-08-18 主人要求: doc112(Type=2)字段结构与Type4不同, 换手/成交额解析为0
        # → 用 9_25 快照补竞价换手 + 竞价成交额(可靠同源)
        try:
            kpl.fill_bid_turnover_from_snap(d, None)
            kpl.fill_bid_amt_from_snap(d, None)
            kpl.apply_board_concept_db(d, log_tag="auc:bid-net", field="board", truncate=2, blank_if_missing=True)
        except Exception as e:
            log.warning("竞价净额换手/成交额/概念补齐失败 err=%s", e)
        # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
        try:
            _apply_change_for(d, kpl.freeze_day())
        except Exception as e:
            log.warning("bid-net 现涨覆盖失败 err=%s", e)
        return {"ok": True, "list": d, "count": len(d)}
    return jr(cached_singleflight(store, ck, 15, _load_live))


@router.get("/api/kpl/broken")
def api_kpl_broken(request: Request, day: str = "", date: str = "",
                   uid: int = Depends(require_vip_or_paid)):
    """炸板(东财 flash, 无需Token): 默认今日; day=yesterday 上一交易日; day=YYYY-MM-DD 指定日;
    date 参数统一回看历史(优先 date, 读 auction_daily_history broken_yest/broken_today)

    ★ 2026-09-27 v4.11.67: 新增组合参数 `date=D & day=yesterday` = **"D 这一天的昨炸板"**
      (股票池 = D 的前一交易日炸板; 字段 = D)。没有它时前端一给 date 就只能拿到"当日炸板",
      于是非交易日的「今炸板」与「昨炸板」完全塌成同一份 —— 主人要求的"定格"没成立。

    🔴 2026-09-30 修「今炸板全天空白」(与 yest-zt / yest-broken 同根因):
      `date` 解析后 == **北京今天** 且**未指定 day=yesterday** ⇒ 回落实时链, 不读历史表 ——
      `broken_today` 是**盘后 15:30** 才落库的, 而前端 `servedDate()` 盘中恒等于今天
      ⇒ 原来整天走历史分支读空表 ⇒ tab 全空白(生产实测 date=2026-09-30 返回 0)。
      ⚠️ `day=yesterday` 分支**刻意不动**: 它取的是「**前一交易日**」的池(那批有落库),
        今日实测正常出数(8 条), 不属本 bug。
    """
    if date and day != "yesterday" and kpl._bj_today() == _resolve_date(date):
        date = ""            # 回看"今天" = 看实时 ⇒ 落下方实时链
    if date:
        resolved = _resolve_date(date)
        if day == "yesterday":
            # 定格/回看模式下的「昨炸板」: 池 = resolved 的前一交易日, 字段 = resolved(定格日)
            prev_pool = kpl._latest_trade_snap_date(resolved, strict=True)
            lst_p = kpl.query_auction_history(prev_pool, "broken_today") if prev_pool else None
            if lst_p:
                # 竞价/流通字段与现涨都取**定格日 resolved** 的值
                # (用户要看的是"这些昨日炸板的票, 在定格日收盘时的表现")
                kpl._merge_broken_bid_snap(lst_p, bid_date=resolved)
                kpl.fill_float_mv_from_snap(lst_p, resolved)
                try:
                    kpl.fill_close_change_from_kline(lst_p, resolved)
                except Exception as e:
                    log.warning("broken 昨炸板现涨(定格日收盘)覆盖失败 err=%s", e)
                kpl.apply_board_concept_db(lst_p, log_tag="auc:broken[hist-yest]", field="board",
                                           truncate=2, blank_if_missing=True, date=prev_pool)
                return jr({"ok": True, "list": lst_p, "count": len(lst_p),
                           "date": resolved, "requestedDate": date, "poolDate": prev_pool,
                           "day": (lst_p[0].get("day") if lst_p else "")})
            # 🔴 2026-09-29 主人铁律: 池日没落库 ⇒ 返回**空**并明确告知, 不再"退回当日炸板"
            #   (原行为会把**今天**的炸板当"昨炸板"展示 —— 语义串位, 属"凑数据")。
            log.warning("broken 昨炸板 date=%s 前一交易日(%s)无落库 ⇒ 返回空(不再退回当日炸板)",
                        resolved, prev_pool)
            return jr({"ok": True, "list": [], "count": 0, "date": resolved,
                       "requestedDate": date, "poolDate": prev_pool, "missingPool": True})
        lst = kpl.query_auction_history(resolved, "broken_today")
        kpl._merge_broken_bid_snap(lst)   # 老快照无竞价字段 → 按 day 补全
        kpl.fill_float_mv_from_snap(lst, resolved)
        # 2026-08-22 历史回看: 现涨(change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            kpl.fill_close_change_from_kline(lst, resolved)
        except Exception as e:
            log.warning("broken 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(lst, log_tag="auc:broken[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": lst or [], "count": len(lst),
                   "date": resolved, "requestedDate": date,
                   "day": (lst[0].get("day") if lst else "")})
    if day == "yesterday":
        # 昨炸板: 读历史快照(优先), 无则实时接口
        # 2026-08-18 修复: 应读 prev 的 broken_today(当日炸板=昨日炸板);
        # 原读 broken_yest 是"当天存的昨日炸板" → 显示上上个交易日(8/17存8/14)
        prev = kpl._prev_trade_day()
        # 2026-09-27 v4.11.67: "今日" 由裸自然日改为**定格基准日**。(`_prev_trade_day()` 也已
        # 改为相对定格基准日 ⇒ 非交易日 prev 不再是"最近交易日自己", 而真的是它的前一交易日)
        today = kpl.freeze_day()
        if prev:
            lst = kpl.query_auction_history(prev, "broken_today")
            if lst:
                # 2026-08-24 口径修复: 股票池=prev日炸板(昨日炸板)没错, 但竞价字段(bidChange/bidTurnover/bidAmt/floatMv)、
                # 现涨(change) 都应该是 **今天** 的, 而不是prev日的(用户要看到这些票今天的承接表现)。
                # bid_date=today 强制用今日 9_25 快照覆盖 bid* 字段
                kpl._merge_broken_bid_snap(lst, bid_date=today)
                kpl.fill_float_mv_from_snap(lst, today)
                kpl.apply_board_concept_db(lst, log_tag="auc:broken[yest]", field="board", truncate=2, blank_if_missing=True, date=prev)
                # 现涨: 盘中=今日实时涨幅; 盘后/非交易日=今日收盘涨幅(固定)
                try:
                    _apply_change_for(lst, today)
                except Exception as e:
                    log.warning("昨炸板 今日现涨覆盖失败 err=%s", e)
                return jr({"ok": True, "list": lst, "count": len(lst), "day": prev})
    # 非竞价时段且无 day 参数: 先尝试读库 broken_today (fast-path)
    if not _is_auction_hours() and not day:
        lst_fast, d_str = _read_auction_fast("broken_today")
        if lst_fast:
            kpl._merge_broken_bid_snap(lst_fast)
            kpl.fill_float_mv_from_snap(lst_fast, d_str)
            _ensure_concepts(lst_fast, tag="auc:broken[fast]")
            # 2026-08-23: broken fast-path 也按 serve_date 覆盖现涨(避免回退历史日时显示最新今天涨幅)
            try:
                _apply_change_for(lst_fast, d_str)
            except Exception as e:
                log.warning("broken fast-path 现涨覆盖失败 err=%s", e)
            return jr({"ok": True, "list": lst_fast, "count": len(lst_fast),
                       "date": d_str,
                       "day": (lst_fast[0].get("day") if lst_fast else "")})
    d = kpl.fetch_broken_zt(day or None)
    lst = d or []
    kpl.fill_float_mv_from_snap(lst, None)
    kpl.apply_board_concept_db(lst, log_tag="auc:broken[now]", field="board", truncate=2, blank_if_missing=True)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(lst, kpl.freeze_day())
    except Exception as e:
        log.warning("broken 现涨覆盖失败 err=%s", e)
    return jr({"ok": True, "list": lst, "count": len(lst),
               "day": (lst[0].get("day") if lst else "")})


@router.get("/api/kpl/ladder")
def api_kpl_ladder(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """连板梯队; date 空=实时(首板~八板+), 指定 'YYYY-MM-DD' 回看历史(ladder_history 快照)
    周末/节假日自动对齐到最近交易日
    2026-09-01: 按东财涨停池真实连板数 rebin 为 1~8 档(五板+ 拆出 6/7/8+ 高度板)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_ladder_history(resolved)
        d = kpl.rebin_ladder(d, resolved)
        return jr({"ok": True, "ladder": d, "date": resolved, "requestedDate": date})
    d = kpl.fetch_ladder_all()
    # 2026-09-07 修复(主人反馈"龙版传媒是 6 连板不是 5 连板"):
    # 开盘啦连板梯队 pid 只分到 **五板+(pid=5)**, 6 板以上全塞进第 5 档 → 前端显示
    # "五板+"/5板。历史回看路径早已用 rebin_ladder(东财涨停池真实 limitUpDays)拆成
    # 1~8 档, **实时路径漏了这一步**。实测龙版传媒 real=6 → rebin 后正确落第 6 档。
    try:
        d = kpl.rebin_ladder(d, kpl.freeze_day())
    except Exception as e:
        log.warning("ladder 实时 rebin 失败(回退 5 档结构) err=%s", str(e)[:120])
    # 2026-08-18 修复: 开盘啦 DailyLimitPerformance 无涨幅字段 → 东财全市场实时行情 merge
    # (前端"实时涨幅"列读 change/realChange, 之前梯队页涨幅全空)
    try:
        from ..services import fetcher
        # 2026-09-04 修复: fs 统一走 market_fs(与 spotMap 预热线程缓存 key 一致), 原硬编码串
        # 顺序不同 → 每次 miss 缓存锁内拉全市场; 现命中预热缓存, ladder 涨幅 merge 秒回
        from ..services.scorer import market_fs
        spot = fetcher.fetch_spot_quote_map(market_fs(list(scorer.ALL_MARKETS)))
        n = 0
        for pid in d:
            for it in (d[pid] or []):
                q = spot.get(str(it.get("code")))
                if q and q.get("realChange") is not None:
                    it["change"] = q.get("realChange")
                    it["realChange"] = q.get("realChange")
                    n += 1
        log.info("ladder 实时涨幅 merge 完成 覆盖%d只", n)
    except Exception as e:
        log.warning("ladder 实时涨幅 merge 失败 err=%s", e)
    d = kpl.rebin_ladder(d, kpl.freeze_day())
    return jr({"ok": True, "ladder": d, "date": ""})


@router.get("/api/kpl/zt-echelon")
def api_kpl_zt_echelon(request: Request, uid: int = Depends(get_uid)):
    """涨停梯队一期聚合(2026-09-20): 顶部统计 + 晋级率 + 分层梯队 + 题材分组。
    一期不含龙头星级/分歧预期等开盘啦私有标签(接口未提供)。"""
    payload = kpl.build_zt_echelon()
    return jr(dict(payload, ok=True))


@router.get("/api/kpl/board-rank")
def api_kpl_board_rank(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """板块强度排行; date 空=实时, 指定 'YYYY-MM-DD' 查历史(开盘啦 doc42 保留最近 5 交易日)
    周末/节假日自动对齐到最近交易日(resolvedDate)"""
    if date:
        resolved = _resolve_date(date)
        d = kpl.fetch_board_rank_by_date(resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_board_rank()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0, "date": ""})


@router.get("/api/kpl/board-stocks")
def api_kpl_board_stocks(request: Request, uid: int = Depends(get_uid), code: str = "",
                         date: str = ""):
    """板块成分股(2026-08-17 主人需求): 板块强度点开看成分股
    code=板块代码(board-rank 的 boardCode, 如 801001); date 空=实时, 指定回看历史"""
    if not code:
        return jr({"ok": False, "msg": "缺少板块代码 code"}, 400)
    if date:
        resolved = _resolve_date(date)
        d = kpl.fetch_board_stocks(code, resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_board_stocks(code)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0, "date": ""})


@router.get("/api/kpl/em-concept-rank")
def api_em_concept_rank(request: Request, uid: int = Depends(get_uid), type: str = ""):
    """题材异动榜左栏(2026-09-21 主人: 先换左栏为猫爪精选板块):
    主源=猫爪 themedaily_jx(level=parent 一级精选板块 267 个, 801xxxk), 失败降级东财概念榜(BKxxxx)。
    type 参数保留兼容但已无意义(精选板块不分 gn/hy)。响应带 source 字段(meoz/em)便于前端观测。"""
    d = sector_rotation.fetch_meoz_jx_rank()
    source = "meoz"
    if not d:
        d = sector_rotation.fetch_em_concept_rank()
        source = "em"
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0, "source": source})


@router.get("/api/kpl/em-board-members")
def api_em_board_members(request: Request, uid: int = Depends(get_uid), code: str = ""):
    """题材异动榜右栏(2026-09-21 切猫爪精选板块): 主源=猫爪 thememembers_jx+screening(801xxxk 板块代码),
    失败或东财体系代码(BKxxxx)时回退东财成分股。"""
    if not code:
        return jr({"ok": False, "msg": "缺少板块代码 code"}, 400)
    code = code.strip()
    if code.endswith("k"):
        # 猫爪精选板块代码体系(801xxxk 带 k): thememembers_jx 拿股票池 + screening 补行情
        d = sector_rotation.fetch_meoz_jx_members(code)
        if d:
            return jr({"ok": True, "list": d, "count": len(d), "source": "meoz"})
    d = sector_rotation.fetch_em_board_members(code)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0, "source": "em"})


@router.get("/api/kpl/hot-rank")
def api_kpl_hot_rank(request: Request, uid: int = Depends(get_uid), source: str = "kpl", date: str = ""):
    """人气热榜; source: kpl/em/ths; date 空=实时, 指定日期回看历史(hot_rank_history)
    周末/节假日自动对齐到最近交易日"""
    from ..services import hot_rank
    source = (source or "kpl").lower()
    if source not in ("kpl", "em", "ths"):
        source = "kpl"
    if date:
        resolved = _resolve_date(date)
        d = hot_rank.query_hot_rank_history(resolved, source)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "source": source, "date": resolved, "requestedDate": date})
    d = hot_rank.fetch_hot_rank(source)
    # 2026-08-30 可观测性(主人要求): 源失败时前端应提示"数据源故障, 请切换"而非"暂无数据"
    # 实时抓取场景: 只要该源刚失败过(最近 60s 内)即标记 failed
    source_failed = False
    if not d:
        err = hot_rank.last_source_error()
        if err and err.get("source") == source and _time.time() - (err.get("ts") or 0) < 60:
            source_failed = True
    # 2026-08-18 修复: 热点榜补开盘啦概念(此前 board/concept 全空)
    try:
        kpl.apply_board_concept_db(d, log_tag="auc:hot-rank", field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("hot-rank 概念覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
               "source": source, "date": "", "source_failed": source_failed})


@router.get("/api/kpl/lhb")
def api_kpl_lhb(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """龙虎榜上榜股票; date 空=当天实时, 指定 'YYYY-MM-DD' 回看历史(lhb_history 快照)
    周末/节假日自动对齐到最近交易日"""
    if date:
        resolved = _resolve_date(date)
        import json as _json
        from ..db import database
        conn = database.get_conn()
        row = conn.execute("SELECT list FROM lhb_history WHERE date=?", (resolved,)).fetchone()
        conn.close()
        lst = []
        if row and row[0]:
            try:
                lst = _json.loads(row[0])
            except (ValueError, TypeError):
                lst = []
        if not lst:
            # 2026-08-22: lhb_history 可能未落库(调度中断/新增日期) → 用盘啦历史接口兜底
            try:
                hist = kpl.fetch_lhb(resolved) or []
                if hist:
                    lst = hist
            except Exception as e:
                log.warning("龙虎榜历史兜底失败 date=%s err=%s", resolved, e)
        kpl.fill_reason_from_pool(lst, resolved)
        kpl.fill_bid_change_from_snap(lst, resolved)
        kpl.fill_float_mv_from_snap(lst, resolved)
        kpl.fill_bid_turnover_from_snap(lst, resolved)
        # 2026-08-23: 历史回看现涨(change/realChange)=当日收盘涨跌幅, 而非最新今天实时
        try:
            kpl.fill_close_change_from_kline(lst, resolved)
        except Exception as e:
            log.warning("lhb 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(lst, log_tag="auc:lhb[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": lst, "count": len(lst), "date": resolved, "requestedDate": date})
    d = kpl.fetch_lhb()
    lst = d or []
    if not lst:
        # 2026-08-22 非交易日/当日无数据 → 回退上一交易日: 优先实时查盘点啦历史, 再读 lhb_history 快照
        d_str = ""
        _prev = kpl._prev_trade_day()
        if _prev:
            hist = kpl.fetch_lhb(_prev) or []
            if hist:
                lst = hist
                d_str = _prev
        if not lst:
            import json as _json
            from ..db import database
            # 2026-09-27 v4.11.67: 裸 `MAX(date)` 改为**交易日历过滤**(同批收口 #149 的一处)
            # —— 原式隐含假设"lhb_history 里只可能有交易日行", 休市日残留会被当成"上一交易日"。
            _d = _latest_trade_date_in("lhb_history", kpl.freeze_day(), op="<")
            if _d:
                conn = database.get_conn()
                r2 = conn.execute("SELECT list FROM lhb_history WHERE date=?", (_d,)).fetchone()
                conn.close()
                if r2 and r2[0]:
                    try:
                        lst = _json.loads(r2[0])
                    except (ValueError, TypeError):
                        lst = []
                if lst:
                    d_str = _d
        kpl.fill_reason_from_pool(lst, d_str or None)
        kpl.fill_bid_change_from_snap(lst, d_str)
        kpl.fill_float_mv_from_snap(lst, d_str)
        kpl.fill_bid_turnover_from_snap(lst, d_str)   # 2026-08-18: 补竞价换手
        # 2026-08-23: 回退到历史日时 现涨=当日收盘涨幅(禁止显示今日最新)
        try:
            _apply_change_for(lst, d_str)
        except Exception as e:
            log.warning("lhb fallback 现涨覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(lst, log_tag="auc:lhb[fallback]", field="board", truncate=2, blank_if_missing=True, date=d_str)
        return jr({"ok": True, "list": lst, "count": len(lst), "date": d_str})
    kpl.fill_reason_from_pool(lst, None)   # 今日涨停池补涨停原因
    kpl.fill_bid_change_from_snap(lst, None)   # 今日 9_25 快照补竞价涨幅
    kpl.fill_float_mv_from_snap(lst, None)
    kpl.fill_bid_turnover_from_snap(lst, None)   # 2026-08-18: 补竞价换手
    kpl.apply_board_concept_db(lst, log_tag="auc:lhb[now]", field="board", truncate=2, blank_if_missing=True, date=kpl.freeze_day())
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(lst, kpl.freeze_day())
    except Exception as e:
        log.warning("lhb 现涨覆盖失败 err=%s", e)
    # 2026-10-04: date 一律返回**定格基准日**(休市日=最近交易日) ⇒ 前端日期选择器
    #   能显示当前榜单真实日期、详情接口回看也锚住同一天(此前返回空串, 前端无从回显)。
    return jr({"ok": True, "list": lst, "count": len(lst), "date": kpl.freeze_day()})


@router.get("/api/kpl/lhb-detail")
def api_kpl_lhb_detail(request: Request, code: str = "", date: str = "", uid: int = Depends(require_vip_or_paid)):
    """龙虎榜个股营业部明细(买入/卖出营业部)"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code"}, 400)
    d = kpl.fetch_lhb_detail(code, date)
    return jr({"ok": True, "detail": d})


@router.get("/api/kpl/lhb-tags")
def api_kpl_lhb_tags(request: Request, codes: str = "", date: str = "",
                     uid: int = Depends(require_vip_or_paid)):
    """龙虎榜「机构/游资」标签（按需调用）：`codes` 逗号分隔，最多 200 只。

    ★ 2026-09-28 新增，修「机构席位 / 知名游资 两个 tab 恒空」：
      列表接口没有机构/游资字段（实测原始字段只有 11 个），只能按代码拉席位明细汇总；
      为保证龙虎榜首屏不被拖慢，做成独立端点由前端在切到那两个 tab 时才调用。
    """
    cl = [c.strip() for c in (codes or "").split(",") if c.strip()][:200]
    if not cl:
        return jr({"ok": False, "msg": "缺少 codes"}, 400)
    tags = kpl.fetch_lhb_tags(cl, date or "")
    return jr({"ok": True, "date": date or "", "tags": tags, "count": len(tags)})


@router.get("/api/kpl/fanbao")
def api_kpl_fanbao(request: Request, date: str = "", uid: int = Depends(get_uid)):
    """断板反包：昨日断板、今日重新起板，且近 5 个交易日有涨停史（不是新首板）。

    ★ 2026-09-28 新增：`kpl.fetch_fanbao_stocks()` 早就存在（供盘后 PNG 天梯图使用），
      但**没有任何 API 暴露** ⇒ 网页端一直看不见这块信息。
    """
    lst = kpl.fetch_fanbao_stocks(date or None) or []
    return jr({"ok": True, "list": lst, "count": len(lst),
               "date": (lst[0].get("day") if lst else (date or ""))})


@router.get("/api/kpl/zt-reason")
def api_kpl_zt_reason(request: Request, code: str = "", uid: int = Depends(require_vip_or_paid)):
    """个股涨停原因(当天/历史)"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code"}, 400)
    d = kpl.fetch_zt_reason(code)
    return jr({"ok": True, "reason": d or []})


@router.get("/api/kpl/wpqc")
def api_kpl_wpqc(request: Request, uid: int = Depends(require_vip_or_paid)):
    """尾盘竞价抢筹(14:57 后)"""
    d = kpl.fetch_wpqc()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


def _bid_qiangcang_payload(date: str = ""):
    # 2026-10-05: 去掉 request/uid 形参 —— 函数体从未使用它们（已 grep 确认）。
    #   去掉后，后台预热可与 HTTP 路由复用同一条计算链（入口见 bid_qiangcang_cached），
    #   这正是「预热写的层 = 用户读的层」的前提。
    """竞价抢筹(左右双表): list20=9:20→9:25 竞额抢筹(开盘啦净额强度),
    list20Chg=9:20→9:25 涨幅抢筹(全市场快照涨幅差), listLast=9:24→9:25 最后1秒段
    date 空=实时; 指定 'YYYY-MM-DD' 回看历史(qc_snapshot + snapshot_bid)
    2026-08-22: 非竞价时段用 _ensure_concepts 轻量补概念; 指定 date 用 deep=True"""
    d = kpl.fetch_bid_qiangcang(date or None) or {}
    l20 = d.get("list20") or []
    l20Chg = d.get("list20Chg") or []
    lLast = d.get("listLast") or []
    lists_to_concept = [l20, l20Chg, lLast]
    # 指定 date → 走 deep=True 深查; 非竞价时段 → _ensure_concepts 轻量补
    # 2026-09-10 生产 504 止血: 概念是**静态属性**(某只票属于什么板块不随回看日期变化),
    # 只有"竞价进行中"才值得为实时性付逐股外网查询的代价。原条件 `date or 竞价时段`
    # 让盘后回看历史日也走 deep(100只×限流3并发 = 单次 198s, 日志实锤) → nginx 60s
    # 504 且两个 worker 被占满, 连累 /api/stocks 一起 504。
    if _is_auction_hours():
        try:
            for lst, tag in zip(lists_to_concept, ["auc:qc20", "auc:qc20Chg", "auc:qcLast"]):
                kpl.apply_board_concept(lst, log_tag=tag, deep=True,
                                        field="board", truncate=2, blank_if_missing=True)
        except Exception as e:
            log.warning("竞价异动概念开盘啦覆盖失败 bid-qiangcang err=%s", e)
    else:
        # 非竞价时段: 先 db 快速合并 + _ensure_concepts 轻量补
        try:
            for lst, tag in zip(lists_to_concept, ["auc:qc20", "auc:qc20Chg", "auc:qcLast"]):
                kpl.apply_board_concept_db(lst, log_tag=tag, field="board", truncate=2, blank_if_missing=True)
                _ensure_concepts(lst, tag=tag)
        except Exception as e:
            log.warning("竞价异动概念补失败 bid-qiangcang[fast] err=%s", e)
    # 2026-08-18 修复: 抢筹三表统一 merge 东财实时涨幅(realChange) —
    # 主人反馈灿勤科技等涨幅抢筹/右表股票实时涨幅为空(开盘啦数据源无该字段)
    # 2026-08-23: 历史回看时**禁止** merge 实时涨幅(否则历史日显示最新涨幅), 改为覆盖当日收盘涨跌幅
    # 统一现涨(realChange/change)口径:
    # - 盘中且展示"今天" → 东财实时(单次批量, _apply_change_for / _update_spot_change)
    # - 其余全部(历史回看 / 回退到上交易日 / 盘后今日 / 非交易日) → 该交易日收盘涨幅(固定, 不调实时)
    # serve_date 推导: 指定 date → date(可能是快照内部 date 或 resolved); 否则今天
    # 2026-09-27 v4.11.67: "今天" 改取**定格基准日**(非交易日 → 最近交易日)。对下面
    # `serve_date != today_str or not _is_auction_hours()` 的判据**结果等价**(交易日两者相同;
    # 非交易日 `_is_auction_hours()` 已恒 False), 但语义从"自然日"统一到"定格日"。
    today_str = kpl.freeze_day()
    serve_date = date or (d.get("date") if isinstance(d, dict) else "") or today_str
    # 三表各自独立填充现涨
    for ql, tag in ((l20, "qc20"), (l20Chg, "qc20Chg"), (lLast, "qcLast")):
        try:
            _apply_change_for(ql, serve_date)
        except Exception as e:
            log.warning("抢筹现涨覆盖失败 tag=%s date=%s err=%s", tag, serve_date, e)
    # 2026-08-18 主人要求: 抢筹右表/涨幅抢筹补竞价换手(快照 bid_amt/float_mv 计算)
    try:
        kpl.fill_bid_turnover_from_snap(l20Chg, serve_date if serve_date != today_str or not _is_auction_hours() else None)
        kpl.fill_bid_turnover_from_snap(lLast, serve_date if serve_date != today_str or not _is_auction_hours() else None)
    except Exception as e:
        log.warning("抢筹竞价换手补齐失败 err=%s", e)
    return {"ok": True, "list20": l20, "list20Chg": l20Chg, "listLast": lLast,
            "count20": len(l20), "count20Chg": len(l20Chg), "countLast": len(lLast),
            "date": d.get("date") or serve_date or ""}


def bid_qiangcang_key_ttl(date: str = ""):
    """竞价抢筹接口层缓存的 **(键, TTL秒)** —— **HTTP 路由与后台预热必须共用本函数**。

    🔴 为什么必须抽出来（2026-10-05 生产复盘）：键原来只在路由里拼，而每 1200s 一轮的
      后台预热调的是服务层 `fetch_bid_qiangcang(date)` —— 两者**不是同一个键**
      ⇒ 预热写的层(上游/猫爪) ≠ 用户读的层(接口) ⇒ 预热跑得再勤也白跑，
      用户每次打开都冷重算：实测盘后 2.39s、竞价时段最坏 9s（三张表各自 3s 概念 deep 补全）。
      主人反馈「竞价抢筹每次打开要 5 秒以上、修很多次没效果」即此因。
    🔴 TTL 按「**数据日是不是过去交易日**」分层，**不能**按「有没有传 date」分层：
      前端 `servedDate()` **总是**带 date 参数 ⇒ 旧写法 `600 if date else (... 15 ...)`
      让竞价时段的 15s 短缓存**永远不生效**，竞价期间拿到的是最长 10 分钟前的陈数据。
    🔴 过去交易日的数据**不可变**（抢筹结果不会因为再算一次而变）⇒ 给 3600s，
      且必须 **> 预热周期**(_KPL_REPLAY_PERIOD=1200s)，否则预热覆盖不住
      （原 600s < 1200s ⇒ 每轮之间有一半时间处于过期 ⇒ 用户撞上就是冷重算）。
    """
    auction = _is_auction_hours()
    if not date:
        return ("bidqc:live" if auction else "bidqc:post", 15 if auction else 300)
    # 只认标准 ISO 日期串（前端 <input type=date> 即此格式）；格式异常一律保守当"当日"走短 TTL
    iso = len(date) == 10 and date[4] == "-" and date[7] == "-"
    try:
        past = iso and date < kpl._bj_today()
    except Exception:                     # noqa: BLE001 - 时钟/日历异常时保守当"当日"
        past = False
    return ("bidqc:hist:" + date, 3600 if past else (15 if auction else 300))


def bid_qiangcang_cached(date: str = ""):
    """竞价抢筹「接口层缓存 + single-flight」的共享入口 —— HTTP 路由 与 后台预热**共用同一键**。

    返回 payload(dict，空 dict 也是 falsy ⇒ 预热侧据此计成功数)。
    """
    from ..services.cache_store import cached_singleflight, store
    ck, ttl = bid_qiangcang_key_ttl(date)
    return cached_singleflight(store, ck, ttl, lambda: _bid_qiangcang_payload(date))


@router.get("/api/kpl/bid-qiangcang")
def api_kpl_bid_qiangcang(request: Request, uid: int = Depends(quota_guard("auction")), date: str = ""):
    """竞价抢筹（接口级缓存 + single-flight + 后台预热，2026-10-03 加 / 10-05 修预热错位）。

    🔴 动机（生产 nginx 实测）：本接口均 **1.04s**、最大 **4.51s**；每次请求都要
      「出网取数 + 概念补全 + 现涨 merge + 补换手」，其中**概念补全单次实测 1.67s**。
      盘后与历史日的这些结果都是**定格**的，重复计算纯属浪费。

    🔴 2026-10-05 修「每次打开要 5 秒以上」：缓存键与 TTL 已抽到 `bid_qiangcang_key_ttl()`
      供**后台预热共用**（原因见该函数注释 —— 原实现里预热写的层与用户读的键不是同一个，
      预热跑得再勤也白跑，用户每次都是冷重算）。
    """
    return jr(bid_qiangcang_cached(date))


@router.get("/api/kpl/yest-zt")
def api_kpl_yest_zt(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """昨日涨停股今日竞价表现: date 空=实时, 指定日期回看历史(auction_daily_history yest_zt)

    🔴 2026-09-30 修「昨涨停 tab 全天空白」: `date` 解析后 == **北京今天** ⇒ 回落实时链,
      **不读历史表**。原因: `yest_zt` / `yest_broken` / `broken_today` 这三类行是**盘后 15:30**
      才落库的(竞价后 09:27 那批只写 seal/boom/bid_net/qiangcang), 而前端 `servedDate()`
      盘中恒等于今天 ⇒ 原来整天走历史分支读空表 ⇒ 三个 tab 全空白, 到 15:30 落库才自愈。
      语义: 回看"今天"本来就是看实时, 不是看历史(与主人铁律「获取为零就不要用昨天的数据」同源)。
      **只**对"今天"生效 —— 盘前(对齐到上一交易日)/非交易日/历史回看的行为一律不变。
    """
    if date and kpl._bj_today() == _resolve_date(date):
        date = ""            # 回看"今天" = 看实时 ⇒ 落下方实时链
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "yest_zt")
        kpl.fill_reason_from_pool(d, resolved)
        kpl.fill_float_mv_from_snap(d, resolved)
        # 2026-08-23: 历史回看现涨(change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            _apply_change_for(d, resolved)
        except Exception as e:
            log.warning("yest-zt 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(d, log_tag="auc:yest-zt[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_yest_zt() or []
    try:
        kpl.apply_board_concept_db(d, log_tag="auc:yest-zt", field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 yest-zt err=%s", e)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(d, kpl.freeze_day())
    except Exception as e:
        log.warning("yest-zt 现涨覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/yest-broken")
def api_kpl_yest_broken(request: Request, uid: int = Depends(require_vip_or_paid), date: str = ""):
    """昨断板: 昨日涨停池中今日未涨停的股票; date 空=实时, 指定日期回看历史

    🔴 2026-09-30 修「昨断板 tab 全天空白」: `date` 解析后 == **北京今天** ⇒ 回落实时链,
      **不读历史表**(`yest_broken` 盘后 15:30 才落库)。详见 api_kpl_yest_zt 的说明。
    """
    if date and kpl._bj_today() == _resolve_date(date):
        date = ""            # 回看"今天" = 看实时 ⇒ 落下方实时链
    if date:
        resolved = _resolve_date(date)
        d = kpl.query_auction_history(resolved, "yest_broken")
        kpl.fill_reason_from_pool(d, resolved)
        kpl.fill_float_mv_from_snap(d, resolved)
        # 2026-08-23: 历史回看现涨(change)=当日收盘涨跌幅, 而非最新今天实时
        try:
            _apply_change_for(d, resolved)
        except Exception as e:
            log.warning("yest-broken 历史现涨(当日收盘)覆盖失败 err=%s", e)
        kpl.apply_board_concept_db(d, log_tag="auc:yest-broken[hist]", field="board", truncate=2, blank_if_missing=True, date=resolved)
        return jr({"ok": True, "list": d or [], "count": len(d) if d else 0,
                   "date": resolved, "requestedDate": date})
    d = kpl.fetch_yest_broken() or []
    try:
        kpl.apply_board_concept_db(d, log_tag="auc:yest-broken", field="board", truncate=2, blank_if_missing=True)
    except Exception as e:
        log.warning("竞价异动概念开盘啦覆盖失败 yest-broken err=%s", e)
    # 2026-08-23 口径统一: 盘中=实时涨幅 覆盖; 盘后/非交易日=当日收盘涨幅固定(不调实时接口)
    try:
        _apply_change_for(d, kpl.freeze_day())
    except Exception as e:
        log.warning("yest-broken 现涨覆盖失败 err=%s", e)
    return jr({"ok": True, "list": d, "count": len(d)})


@router.get("/api/kpl/yesterday-perf")
def api_kpl_yesterday_perf(request: Request, uid: int = Depends(get_uid)):
    """昨日涨停/连板/破板今日平均表现(策略验证)"""
    d = kpl.fetch_yesterday_perf()
    return jr({"ok": True, "perf": d})


# ==================== xuangubao 免费接口(无需 Token) ====================
@router.get("/api/kpl/zt-pool")
def api_kpl_zt_pool(request: Request, uid: int = Depends(get_uid), day: str = ""):
    """涨停池: day 可选(YYYY-MM-DD 历史)"""
    d = kpl.fetch_zt_pool(day or None)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/dt-pool")
def api_kpl_dt_pool(request: Request, uid: int = Depends(get_uid), day: str = ""):
    """跌停池: day 可选"""
    d = kpl.fetch_dt_pool(day or None)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/yest-zt-pool")
def api_kpl_yest_zt_pool(request: Request, uid: int = Depends(get_uid), day: str = ""):
    """昨日涨停池: day 可选"""
    d = kpl.fetch_yest_zt_pool(day or None)
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/market-line")
def api_kpl_market_line(request: Request, uid: int = Depends(get_uid), date: str = ""):
    """市场曲线全家桶: 涨跌家数/涨停跌停数/炸板率/昨涨停今表现/市场温度"""
    return jr({
        "ok": True,
        "updown": kpl.fetch_updown_line(date or None),
        "zt_dt": kpl.fetch_zt_dt_line(date or None),
        "broken": kpl.fetch_broken_line(date or None),
        "yest_perf": kpl.fetch_yest_zt_perf_line(date or None),
        "temperature": kpl.fetch_market_temp_line(date or None),
    })


@router.get("/api/kpl/hot-stocks")
def api_kpl_hot_stocks(request: Request, uid: int = Depends(get_uid)):
    """热点解读-强势股(涨停原因/题材/封单时间)"""
    d = kpl.fetch_hot_stocks()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/hot-plates")
def api_kpl_hot_plates(request: Request, uid: int = Depends(get_uid)):
    """板块名称与对应题材"""
    d = kpl.fetch_hot_plates()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/live-room")
def api_kpl_live_room(request: Request, uid: int = Depends(get_uid)):
    """涨停直播"""
    d = kpl.fetch_live_room()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/dadan-net")
def api_kpl_dadan_net(request: Request, uid: int = Depends(get_uid), code: str = ""):
    """指定个股大单净额分时"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code 参数"})
    d = kpl.fetch_dadan_net(code)
    return jr({"ok": True, **d})


@router.get("/api/kpl/sector-rotation")
def api_kpl_sector_rotation(request: Request, uid: int = Depends(get_uid), days: int = 10, source: str = "kpl"):
    """板块轮动历史: 返回最近 N 个交易日的板块强度 Top10(表格+趋势) + 多窗口排名(近10/20/30/50日)
    source: 数据源 kpl(开盘啦)/ em(东方财富), 默认 kpl"""
    days = max(1, min(int(days or 10), 60))
    source = (source or "kpl").lower()
    if source not in ("kpl", "em", "ths"):
        source = "kpl"
    rot = sector_rotation.query_rotation(days, source)
    win = sector_rotation.query_window_ranking((10, 20, 30, 50), source=source)
    # 2026-08-30 可观测性(主人要求): 源失败标记。板块轮动读库(历史), 若该源从未落库且
    # 最近 12h 内该源抓取失败 → 提示"数据源故障"。kpl/ths 历史为空属正常(数据积累中), 不标记
    source_failed = False
    if not rot.get("dates") and source == "em":
        err = sector_rotation.last_source_error()
        if err and err.get("source") == "em" and _time.time() - (err.get("ts") or 0) < 12 * 3600:
            source_failed = True
    return jr({"ok": True, "rotation": rot, "windows": win,
               "dates": rot.get("dates") or [], "source": source,
               "source_failed": source_failed})


# ==================== 异动监管(doc90/doc108/doc109) ====================
@router.get("/api/kpl/yidong-realtime")
def api_kpl_yidong_realtime(request: Request, uid: int = Depends(require_vip_or_paid)):
    """异动实时接口 (开盘啦 doc90 GetPianLiZhi_Index): 返回全市场实时异动个股列表
    返回: {ok, list, manyNum, day, time}
    list 每项: code, name, type(第4字段index3), trigger(第8字段index7), triggered(末字段index12),
              change(当日涨幅, index4), days(统计天数, index5), deviation(累计偏离值, index6), target(触发阈值, index8)
    原始字段结构: [0]code [1]name [2]typeCode [3]typeDesc [4]change [5]days [6]deviation
                  [7]triggerDesc [8]triggerPct ... [12]triggered"""
    d = kpl.fetch_kpl_doc90() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        code = str(item[0]) if len(item) > 0 else ""
        name = str(item[1]) if len(item) > 1 else ""
        # 异动类型: 第4个字段(index 3)
        yd_type = str(item[3]) if len(item) > 3 else ""
        # 涨幅触发异动: 第8个字段(index 7)
        trigger = str(item[7]) if len(item) > 7 else ""
        # 是否触发异动: 最后一个字段(index 12)
        triggered = str(item[12]) if len(item) > 12 else ""
        # 偏离值字段(2026-08-19 8f5a8c2): 当日涨幅/统计天数/累计偏离值/触发阈值
        try:
            change = float(item[4]) if len(item) > 4 else None
        except (ValueError, TypeError):
            change = None
        try:
            days = int(item[5]) if len(item) > 5 else 0
        except (ValueError, TypeError):
            days = 0
        try:
            deviation = float(item[6]) if len(item) > 6 else None
        except (ValueError, TypeError):
            deviation = None
        try:
            target = float(item[8]) if len(item) > 8 else None
        except (ValueError, TypeError):
            target = None
        lst.append({"code": code, "name": name, "type": yd_type,
                     "trigger": trigger, "triggered": triggered,
                     "change": change, "days": days,
                     "deviation": deviation, "target": target})
    return jr({"ok": True, "list": lst, "count": len(lst),
               "manyNum": d.get("Many_Num", 0), "day": d.get("Day", ""),
               "time": d.get("Time", 0)})


@router.get("/api/kpl/yidong-monitor")
def api_kpl_yidong_monitor(request: Request, uid: int = Depends(require_vip_or_paid)):
    """重点监控股票 (开盘啦 doc108 GetYDTP_ZDJK_Today): 当日重点监控个股
    返回: {ok, list, count}  list 每项格式: [code, name, startDate, endDate, times]"""
    d = kpl.fetch_kpl_doc108() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        code = str(item[0]) if len(item) > 0 else ""
        name = str(item[1]) if len(item) > 1 else ""
        start = str(item[2]) if len(item) > 2 else ""
        end = str(item[3]) if len(item) > 3 else ""
        times = item[4] if len(item) > 4 else 0
        lst.append({"code": code, "name": name, "startDate": start, "endDate": end, "times": times})
    return jr({"ok": True, "list": lst, "count": len(lst)})


@router.get("/api/kpl/yidong-multi")
def api_kpl_yidong_multi(request: Request, uid: int = Depends(require_vip_or_paid)):
    """多次异动个股 (开盘啦 doc109 GetPianLiZhi_Many): 近10日内多次异动个股
    返回: {ok, list, count, day}  list 每项格式: [code, name, times, desc]"""
    d = kpl.fetch_kpl_doc109() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        code = str(item[0]) if len(item) > 0 else ""
        name = str(item[1]) if len(item) > 1 else ""
        times = item[2] if len(item) > 2 else 0
        desc = str(item[3]) if len(item) > 3 else ""
        lst.append({"code": code, "name": name, "times": times, "desc": desc})
    return jr({"ok": True, "list": lst, "count": len(lst), "day": d.get("Day", "")})


@router.get("/api/kpl/yidong-hot")
def api_kpl_yidong_hot(request: Request, uid: int = Depends(require_vip_or_paid)):
    """热门股偏离值 (开盘啦 GetPianLiZhi_Hot): 热门度严重异常个股列表
    返回: {ok, list, count, day, time}
    list 每项: code, name, type(偏离类型), change(今日涨跌%), deviation(偏离值),
              flag(连板/标签), before(异动前涨幅), base(偏离基准), concept(概念), days(偏离天数), rule(偏离规则)
    原始字段结构: [0]code [1]name [2]type [3]change [4]deviation [5]flag\n                [6]before [7]base [8]concept [9]0 [10]days [11]rule"""
    d = kpl.fetch_kpl_pianli_hot() or {}
    raw_list = d.get("List") or []
    lst = []
    for item in raw_list:
        if not item:
            continue
        def g(i): return item[i] if len(item) > i else ""
        lst.append({
            "code": str(g(0)), "name": str(g(1)), "type": str(g(2)),
            "change": g(3), "deviation": g(4), "flag": str(g(5)),
            "before": g(6), "base": g(7), "concept": str(g(8)),
            "days": str(g(10)), "rule": str(g(11)),
        })
    return jr({"ok": True, "list": lst, "count": len(lst),
               "day": d.get("Day", ""), "time": d.get("Time", 0)})


@router.get("/api/kpl/interfaces")
def api_kpl_interfaces(request: Request, uid: int = Depends(get_uid)):
    """已封装开盘啦接口索引(开发调试用):
    返回 kpl 模块所有 fetch_* 函数的:
    name / 第一行 docstring(功能描述) / 是否被其他代码调用
    相关文档: docs/kpl-interfaces.md (自动生成脚本 scripts/kpl_interface_index.py)"""
    import ast
    import inspect
    import os
    # 统计整个 backend 目录的调用点(跨模块, 如 stocks.py 里 kpl.fetch_board_map())
    def _called_count(name):
        cnt = 0
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", "venv", ".venv", "tests")]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                try:
                    t2 = ast.parse(open(os.path.join(root, fn), encoding="utf-8").read())
                except Exception:
                    continue
                for node in ast.walk(t2):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    if (isinstance(f, ast.Name) and f.id == name) or \
                       (isinstance(f, ast.Attribute) and f.attr == name):
                        cnt += 1
        return cnt
    out = []
    for name, fn in vars(kpl).items():
        if not name.startswith("fetch_"):
            continue
        if not inspect.isfunction(fn):
            continue
        doc = (inspect.getdoc(fn) or "").strip()
        title = doc.splitlines()[0] if doc else ""
        called = _called_count(name) > 0
        # doc 编号
        m = name[len("fetch_kpl_doc"):] if name.startswith("fetch_kpl_doc") else ""
        doc_no = int(m) if m.isdigit() else None
        out.append({"name": name, "doc_no": doc_no, "title": title, "called": called})
    out.sort(key=lambda x: (0 if x["doc_no"] is None else 1, x["doc_no"] or 0, x["name"]))
    return jr({"ok": True, "count": len(out), "interfaces": out,
               "hint": "可用 scripts/kpl_interface_index.py 生成 docs/kpl-interfaces.md 文档"})
