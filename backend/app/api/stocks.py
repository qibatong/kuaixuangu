# -*- coding: utf-8 -*-
"""
选股路由: GET /api/stocks?action=lock|filter|refresh|ping&strategy=auction|spot&筛选参数...
===================================================================
strategy=auction  竞价选股(默认, 行为不变): lock 9:30 前唯一锁定, 评分=竞价涨幅/竞价换手/异动...
strategy=spot     盘中实时选股: 随时 refresh, 评分=实时涨幅/量比/换手/封单强度..., 涨停留池接口
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import (auction_snapshot, fetcher, history, kpl, notify, scorer,
                        stats)
from ..services.cache_store import store as _cstore   # 2026-09-04: refresh 计算缓存
from ..services.picker import parity as _parity      # 重构 P3: 新老双跑灰度
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)


def _load_strengths(raw):
    """竞价强度 map {code: 0~1} —— 替代**已失活的 f630 异动等级**(权重 17%)。

    f630 只有东财点查才给真实值, 腾讯兜底行/快照行恒填 0 → 东财一断全员 default
    0.18 → 17%×0.82=13.9 分凭空蒸发(实测 2026-09-08 批次#1585 全部 39 只 warn=0,
    概率天花板从 99.4 崩到 85.5)。

    开关: settings 表 `use_bid_strength=1` 才启用; **默认关闭** → 返回空 dict →
    老链路行为零变化(对拍基线不变)。三层信号(抢筹名单/竞价量比/加速度)全部来自
    快照表 + 开盘啦, 不依赖东财, 任何异常都吞掉退回 f630 行为。
    """
    try:
        from ..services import settings
        if str(settings.get("use_bid_strength") or "0") not in ("1", "true", "True"):
            return {}
    except Exception:                                          # noqa: BLE001
        return {}
    try:
        from ..services import bid_strength
        codes = [str(s.get("f12") or "") for s in (raw or [])]
        codes = [c for c in codes if c]
        if not codes:
            return {}
        st = bid_strength.load(codes)
        out = {c: v for c, v in bid_strength.score_map(st).items() if v is not None}
        log.info("竞价强度启用: %d/%d 只取到强度分", len(out), len(codes))
        return out
    except Exception as e:                                     # noqa: BLE001
        log.warning("竞价强度加载失败(退回 f630 异动等级) err=%s", e)
        return {}


def _gray_enabled():
    """重构 P3 灰度开关: settings 表 picker_gray=1 时旁路跑新链路并打对拍日志。
    **默认关闭**, 且新链路结果不参与返回 —— 只观测不切换。

    P5 之后本开关语义变为「切流后仍旁路跑**老**链路做对拍」(反向对拍), 用于
    观察期验证新链路没有把名单选歪; 老链路开销换可观测性, 稳定后可关闭。"""
    try:
        from ..services import settings
        return str(settings.get("picker_gray") or "0") in ("1", "true", "True")
    except Exception:
        return False


def _cutover_enabled():
    """重构 P5 切流开关: settings 表 picker_cutover=1 → **主链路走 picker.pipeline**。

    新链路不可用(名单源取不到行 / 抛异常)时**自动回退老链路**并打 WARNING,
    故开关打开不等于"无兜底硬切"。默认 0 = 老链路(与切流前行为完全一致)。
    """
    try:
        from ..services import settings
        return str(settings.get("picker_cutover") or "0") in ("1", "true", "True")
    except Exception:
        return False


def _run_new_pipeline(uid, action, f, *, yesterday_map, yesterday_chg_map,
                      snapshot_map, bid_amt_map, bid_chg_map):
    """跑新链路 picker.pipeline; 返回 items(list) 或 **None(=不可用, 调用方回退老链路)**。

    判定"不可用"只看一件事: **名单源有没有给出全市场行**(n_universe>0)。
    - 名单源失败 → n_universe=0 → 回退(此时老链路还有快照池可兜底)
    - 补丁源失败 / 强度缺失 → n_universe>0, 只是 degraded → **不回退**, 名单仍有效
      (这正是新链路的设计目标: 展示字段降级不影响名单)
    - 入选 0 只是合法空名单, 同样不算失败
    异常一律吞掉 → 返回 None, 绝不把新链路的异常抛给用户。
    """
    try:
        from ..services.picker import mode as pmode
        from ..services.picker import pipeline
        ctx = pipeline.PickContext(
            date=pmode.bj_date(),
            markets=f.get("markets"),
            zt_codes=(_safe_zt_codes() if not f.get("limitUp") else None),
            day_bid_change=bid_chg_map or {},
            day_bid_amt_wan=bid_amt_map or {},
            yesterday_chg=yesterday_chg_map or {},
            yesterday_map=yesterday_map or {},
            snapshot_map=snapshot_map or {},
            require_bid_change=True,
        )
        res = pipeline.run(f, ctx=ctx)
    except Exception as e:                                     # noqa: BLE001
        log.error("新链路异常 → 回退老链路 uid=%s action=%s err=%s",
                  uid, action, e, exc_info=True)
        return None
    if res.n_universe <= 0:
        log.warning("新链路名单源无数据 → 回退老链路 uid=%s action=%s pick_mode=%s errors=%s",
                    uid, action, res.mode, res.errors[:3])
        return None
    log.info("选股走新链路 uid=%s action=%s pick_mode=%s 全市场%d 候选%d 入选%d 源=%s "
             "降级=%s 剔除=%s 耗时%dms",
             uid, action, res.mode, res.n_universe, res.n_candidate, len(res.items),
             ",".join(res.sources), res.degraded, res.stats, res.elapsed_ms)
    return res.items


def _gray_run(uid, action, raw, f, result, *, bid_amt_map, bid_chg_map,
              yesterday_chg_map):
    """旁路跑新链路并与老结果对拍(任何异常都不影响老链路返回)。"""
    try:
        rep = _parity.compare(
            result, raw, f,
            day_bid_change=bid_chg_map or {}, day_bid_amt_wan=bid_amt_map or {},
            yesterday_chg=yesterday_chg_map or {},
            zt_codes=_safe_zt_codes(),
            legacy_scored=None)
        if rep.identical:
            log.info("选股灰度对拍一致 uid=%s action=%s 老=%d只 新=%d只 耗时%dms",
                     uid, action, len(rep.legacy), len(rep.new), rep.elapsed_ms)
        else:
            log.warning("选股灰度对拍差异 uid=%s action=%s %s | 仅老=%s 仅新=%s "
                        "分差=%s 字段差=%s 新链路错误=%s",
                        uid, action, rep.summary(), rep.only_legacy[:10],
                        rep.only_new[:10], rep.score_diff[:6],
                        rep.field_diff[:6], rep.errors[:3])
    except Exception as e:
        log.warning("选股灰度对拍失败(不影响返回) uid=%s err=%s", uid, str(e)[:200])


def _parity_reverse(uid, action, raw, f, new_items, *, yesterday_map, snapshot_map,
                    bid_amt_map, bid_chg_map, yesterday_chg_map):
    """切流后的**反向对拍**: 返回给用户的已是新链路结果, 这里旁路跑一遍老链路比对。

    观察期唯一目的: 证明新链路没把名单选歪(只老有/只新有/分差)。
    老链路异常不影响返回 —— 对拍失败只打日志。
    """
    try:
        legacy = scorer.process_all_stocks(
            raw, f, yesterday_map, snapshot_map, qiangchou_detail=kpl.get_qiangchou_detail(),
            day_bid_amt=bid_amt_map, day_bid_change=bid_chg_map,
            yesterday_chg_map=yesterday_chg_map, strengths=_load_strengths(raw))
    except Exception as e:                                     # noqa: BLE001
        log.warning("切流对拍: 老链路旁路执行失败(不影响返回) uid=%s err=%s",
                    uid, str(e)[:200])
        return
    try:
        rep = _parity.diff_items(legacy, new_items)
        if rep.identical:
            log.info("切流对拍一致 uid=%s action=%s 老=%d只 新=%d只", uid, action,
                     len(legacy), len(new_items))
        else:
            log.warning("切流对拍差异 uid=%s action=%s %s | 仅老=%s 仅新=%s 分差=%s",
                        uid, action, rep.summary(), rep.only_legacy[:10],
                        rep.only_new[:10], rep.score_diff[:6])
    except Exception as e:                                     # noqa: BLE001
        log.warning("切流对拍失败(不影响返回) uid=%s err=%s", uid, str(e)[:200])


def _safe_zt_codes():
    try:
        v = fetcher.get_yesterday_zt_codes()
        return set(v) if v else None
    except Exception:
        return None

router = APIRouter()


def _apply_kpl_board(result, log_tag=""):
    """用开盘啦概念覆盖选股结果 concept(统一走 kpl.apply_board_concept 双层覆盖)
    result: scorer.process_all_stocks 输出, 每项含 code/concept"""
    kpl.apply_board_concept(result, log_tag)


# 快照候选池粗筛(2026-09-07 主人方案: 盘后 filter 不拉实时全市场, 直接用 9:25 定格快照表):
# 与 scorer.apply_filters 同标准的"快照字段可判定"部分(市值/竞价额/涨幅/板块/ST/昨涨停),
# 价格/停牌/概率等快照无字段的过滤留给点查行情后的 apply_filters(候选集小, 成本低)。
_SNAP_CANDIDATE_MAX = 120    # 候选上限: 点查一批够覆盖, 防极端参数下 URL 过长


def _snapshot_candidate_codes(snap_rows, f, yzt_codes):
    """按快照字段粗筛 9:25 全市场快照 → 候选 code 列表(按竞价额降序)。
    snap_rows: auction_snapshot.load_snapshot_full() 返回 {code: {...}}
    yzt_codes: 昨日涨停/连板 code 集合(limitUp 未勾选时用于剔除, 替代快照缺的 concept)"""
    codes = []
    for code, v in snap_rows.items():
        name = v.get("name") or ""
        # 板块(与 apply_filters._in_markets 同标准)
        if not scorer._in_markets(code, f.get("markets") or []):
            continue
        # ST 剔除(默认 stSuspend=False → 剔 ST, 语义同 apply_filters)
        if not f["stSuspend"] and scorer.is_st(name):
            continue
        # 昨涨停/连板剔除(limitUp 未勾)
        if not f["limitUp"] and (code in yzt_codes):
            continue
        # 竞价涨幅 > bidGt 剔除(与 apply_filters 同: 保留 ≤ bidGt)
        if (v.get("bid_change") or 0) > f["bidGt"]:
            continue
        # 市值(快照 free_mv 单位元 → 亿; 与 circulationMV=f21/1e8 同)
        mv = (v.get("free_mv") or v.get("float_mv") or 0.0) / 1e8
        if mv < f["floatMvFloor"]:
            continue
        if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
            continue
        # 竞价额(9_25 定格, 万元; 与 day_bid_amt 同口径)
        if (v.get("bid_amt") or 0) < f["bidAmtFloor"]:
            continue
        codes.append((code, v.get("bid_amt") or 0))
    # 按竞价额降序, 限制候选量
    codes.sort(key=lambda x: -x[1])
    return [c for c, _ in codes[:_SNAP_CANDIDATE_MAX]]


def _snapshot_rows_to_raw(snap_rows, codes):
    """点查失败降级: 用 9:25 定格快照行直接构造 scorer 最小可行行情行(2026-09-08 方案 A)。

    背景: 快照候选池的点查(fetch_raw_by_codes, 东财 ulist)断连失败时, 原逻辑降级
    ensure_cache 实时拉全市场 → 双 worker 缓存不一致+28 页拉取 → 同条件名单波动、
    大跌票「有概率」混入(9/8 早 Felix518 #8395/#8396 即此降级所致, 日志实锤
    Remote end closed connection without response)。主人拍板方案 A: 失败不再拉
    实时全市场, 直接以 9:25 定格快照行出名单 — 候选固定 → 名单幂等干净。
    快照行字段(bid_change/bid_amt/float_mv)齐全, 足够 apply_filters 的过滤核心
    (竞涨/竞额/市值/板块/ST/昨涨停); 缺失的实时字段(现价/量比/异动)给保守默认,
    使 prob/conf 取保守档(不入双低剔除), 停牌判断不被误伤(9:25 有竞价额的行必非停牌)。
    """
    raw = []
    for code in codes:
        v = snap_rows.get(code)
        if not v:
            continue
        # f616: 行情口径为元(get_bid_amt 内部 /10000 → 万元), 快照 bid_amt 已是万元
        # (score_all_stocks 窗口外 bidAmt 走 day_bid_amt map, 此字段仅窗口内路径兜底)
        raw.append({
            "f12": code,
            "f14": v.get("name") or "",
            "f615": v.get("bid_change") or 0.0,        # 竞价涨幅(9:25 定格)
            "f616": (v.get("bid_amt") or 0.0) * 10000.0,
            "f21": v.get("float_mv") or v.get("free_mv") or 0.0,   # 流通市值(元, 与行情 f21 同构)
            "f117": v.get("free_mv") or 0.0,           # 自由流通(元, f21 兜底列)
            "f2": 0.0,    # 现价未知 → priceGt(≤300)不过滤、竞价换手=0(保守)
            "f3": 0.0,    # 现涨未知 → 昨日涨幅分 0(保守)、realChange 展示 0
            "f4": 0.01,   # >0 防 is_suspended 误判(快照有 bid_amt 即非停牌)
            "f5": 1.0,    # >0 防 is_suspended 误判
            "f17": 0.0, "f18": 0.0,                    # 今开/昨收未知 → 一字/实体 0
            "f8": 0.0, "f10": 0.0, "f630": 0,          # 换手/量比/异动 保守
            "f100": "-", "f102": "-", "f103": "-",     # 概念由 _apply_kpl_board 覆盖
        })
    return raw


@router.get("/api/stocks")
def api_stocks(request: Request, uid: int = Depends(get_uid)):
    q = qs(request)
    action = (q.get("action") or ["filter"])[0]
    # 2026-09-09 命名消歧(主人指示彻底改名): 策略参数 mode → strategy ——
    # 原 mode 与内部时段模式 PickMode(preopen/auction/locked/intraday/closed) 撞名,
    # 排查时极易误读(曾把回显的策略 mode=auction 当成"午休仍在竞价窗口")。
    # 语义: strategy=选股策略(auction 竞价因子表 / spot 盘中实时因子表)。
    # 旧参数 mode 保留为兼容别名(线上缓存前端/书签仍在传), 下版本移除。
    strategy = (q.get("strategy") or q.get("mode") or ["auction"])[0]
    force = (q.get("force") or ["0"])[0] in ("1", "true", "True")   # 主动重锁(绕过当日幂等)
    if action not in ("lock", "filter", "refresh", "ping"):
        log.warning("选股非法参数 action=%s uid=%s", action, uid)
        return jr({"ok": False, "msg": "非法参数"}, 400)
    if strategy not in ("auction", "spot"):
        log.warning("选股非法参数 strategy=%s uid=%s", strategy, uid)
        return jr({"ok": False, "msg": "非法参数 strategy"}, 400)
    if action == "ping":
        _, _, before930 = scorer.bj_now()
        return jr({"ok": True, "before930": before930})

    f = scorer.validate_filters(q)
    fs = scorer.market_fs(f["markets"])
    _, _, before930 = scorer.bj_now()
    t0 = time.time()

    try:
        # 盘中模式: 全市场拉取 + 与竞价同一套评分/过滤逻辑(诗人需求: 盘中=不锁定的竞价)
        if strategy == "spot":
            raw, err = fetcher.ensure_spot_cache("refresh", fs, before930)
            if err:
                log.warning("选股被拒 uid=%s action=%s strategy=%s err=%s", uid, action, strategy, err)
                return jr({"ok": False, "msg": err}, 403)
            # 2026-09-05 B 方案: 不再下发全市场 spotMap(前端实时价已内联在 items, 
            # 跌出锁定票的实时价改走 /api/quotes 按需取, /api/stocks 响应因此大幅瘦身)
            # 复用竞价评分 + 竞价过滤: 盘中=不锁定的竞价, 9:30 后持续刷新, 名单会变(符合诗人预期)
            yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw])
            # 2026-09-08 昨日涨幅真实化: 复用上面日K缓存(零额外请求), 供"昨日涨幅"因子
            yesterday_chg_map = fetcher.fetch_yesterday_changes([s.get("f12") for s in raw])
            snapshot_map = auction_snapshot.load_snapshot()
            # 2026-09-03 竞额定格 map(9_25 快照): 盘中「竞额」不以腾讯伪 f616(=实时成交额)为准
            bid_amt_map = auction_snapshot.load_day_bid_amt()
            # 2026-09-08 竞涨定格 map: 东财 f615 收盘后为 "-"(盘中 9:30+ 同理不可靠),
            # bidChange 以当日 9:25 定格竞价涨幅为准(防退 f3 → 竞涨=现涨/过滤按现价)
            bid_chg_map = auction_snapshot.load_day_bid_change()
            # 2026-09-01 抢筹口径: 左视图抢筹=右视图竞价异动"竞价抢筹"代码集(9:20→9:25涨幅/最后一秒段)
            qc_detail = kpl.get_qiangchou_detail()
            result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map, qiangchou_detail=qc_detail,
                                               day_bid_amt=bid_amt_map, day_bid_change=bid_chg_map,
                                               yesterday_chg_map=yesterday_chg_map,
                                               strengths=_load_strengths(raw))
            _apply_kpl_board(result, "spot")
            log.info("盘中选股(同竞价逻辑) uid=%s markets=%s raw=%d只 返回%d只 耗时%.0fms",
                     uid, ",".join(f["markets"]), len(raw), len(result), (time.time() - t0) * 1000)
            # 盘中 refresh 不落库、不推送(避免高频刷屏); 只返回实时结果
            return jr({
                "ok": True, "strategy": "spot", "mode": "spot",
                "list": result, "count": len(result),
                "before930": before930,
                "dataTime": int(fetcher._cache[fs]["ts"]),
            })

        # ---- 竞价模式 ----
        # 2026-09-02 当日幂等(主人确认): 9:30 前页面自动 lock 若当日已存在
        # "9:25 后落库 + 同筛选参数"的手动 lock 批次 → 直读批次返回,
        # 不再全量重拉行情/重复落库/重复推送(解决"每次重新登录都重算一次+堆 lock 历史")。
        # 仅拦自动 lock(force=0): 9:25 前数据未定型仍每次重算; 用户主动点「锁定」(force=1)
        # 或参数已改 → 正常重算。命中返回结构含 idempotent=True 供前端识别。
        if (action == "lock" and before930 and strategy == "auction" and not force):
            try:
                lock_b = history.find_today_lock_matching(uid, f)
                if lock_b:
                    lst = history.get_batch_stocks_mapped(lock_b["id"])
                    log.info("选股lock当日幂等 uid=%s batch=%s 直读%d只(跳重拉/落库/推送)",
                             uid, lock_b["id"], len(lst))
                    return jr({
                        "ok": True, "strategy": "auction", "mode": "auction", "list": lst,
                        "count": len(lst), "before930": True,
                        "dataTime": int(lock_b.get("ts") or time.time()),
                        "idempotent": True, "batch_id": lock_b["id"],
                    })
            except Exception as e:
                log.warning("lock 当日幂等查询失败(降级正常重算) uid=%s err=%s", uid, e)
        # 2026-09-04 9:30 后直读(主人方案「打开/刷新直接取历史最新, 不再每次重算」):
        # 9:30 后名单定型(当日 lock / 9:26 系统批次已落库), 页面打开与 30s 轮询的 refresh
        # 若筛选参数未变 → 直读当日可复用批次(find_today_reusable_batch: lock→filter→auto)
        # + 全市场实时行情(spotMap, 60s TTL 独立缓存)覆盖现价/涨幅返回。
        # 跳过 ensure_cache 全市场拉取(30s TTL 恰与前端轮询同周期, 每次 miss 锁内拉腾讯
        # 5548只, 多用户排队 = 加载慢/长尾根因)与全市场评分/板块外网覆盖。
        # 名单冻结(评分用定格值, 不再盘中漂移), 行情照常实时; 参数已改/当日无批次才重算。
        if (action == "refresh" and not before930 and strategy == "auction"):
            try:
                reuse_bid, reuse_src = history.find_today_reusable_batch(uid, f)
                reuse_date = None
                if not reuse_bid:
                    # 2026-09-05 主人需求(多用户反馈): 休市时间/当日无批次时回退
                    # **最近交易日**同参批次直读 —— 关闭平台后再打开, 首页直接显示
                    # 关闭前选出的股, 不再全市场重算转圈。参数不一致(改过条件)仍重算。
                    reuse_bid, reuse_src, reuse_date = history.find_recent_reusable_batch(uid, f)
                    if reuse_bid:
                        log.info("选股refresh当日无批次→回退最近交易日直读 uid=%s src=%s "
                                 "batch=%s date=%s", uid, reuse_src, reuse_bid, reuse_date)
                if reuse_bid:
                    lst = history.get_batch_stocks_mapped(reuse_bid)
                    # 2026-09-08 双保险(主人反馈"刷新无数据"): 即便批次非空(stock_count>0),
                    # 明细仍可能为空(落库异常/明细被清理) → 空名单不得直读返回, 视为未命中
                    # 继续走下方重算, 避免页面空白(空名单 ≠ 有效定格名单)。
                    if not lst:
                        log.warning("选股refresh直读批次为空名单, 放弃直读改重算 uid=%s "
                                    "batch=%s src=%s", uid, reuse_bid, reuse_src)
                        reuse_bid = None
                    # 全市场实时行情(缓存命中≈0ms; 拉取失败降级: 定格名单无实时覆盖, 下轮自愈)
                    # 2026-09-05 B 方案: 行情仅内联覆盖 list item, 不再整体下发 spotMap 字段
                    spot_map = {}
                    try:
                        spot_map = fetcher.fetch_spot_quote_map(fs)
                    except Exception as e:
                        log.warning("refresh直读路径全市场行情拉取失败(降级: 定格名单) err=%s", e)
                    # 定格评分(probability/confidence/bidChange/bidAmt...) + 实时行情覆盖
                    # (price/realChange/entityChange/volRatio/turnover): 前端 merge 在榜分支
                    # 用 lt.price/realChange 覆盖, 评分定格不回拨
                    if spot_map:
                        for it in lst:
                            rt = spot_map.get(it["code"])
                            if rt:
                                it["price"] = rt.get("price")
                                it["realChange"] = rt.get("realChange")
                                it["entityChange"] = rt.get("entityChange")
                                it["volRatio"] = rt.get("volRatio")
                                it["turnover"] = rt.get("turnover")
                    log.info("选股refresh直读批次 uid=%s src=%s batch=%s 返回%d只(跳全市场重拉/评分)",
                             uid, reuse_src, reuse_bid, len(lst))
                    return jr({
                        "ok": True, "strategy": "auction", "mode": "auction", "list": lst,
                        "count": len(lst), "before930": False,
                        "dataTime": int(time.time()),
                        "reused": True, "source": reuse_src, "batch_id": reuse_bid,
                        "reusedDate": reuse_date,   # 回退最近交易日时非 None, 前端据此提示
                    })
            except Exception as e:
                log.warning("refresh 直读批次失败(降级正常重算) uid=%s err=%s", uid, e)
        # 2026-09-04: refresh 全量重算**结果**缓存(只有无当日批次的用户才会走到这里 —
        # 有批次用户已在上面 115 行直读返回)。无批次场景(新号 / 当日系统批次为空)
        # 每次 ensure_cache 全市场 + 昨日K线 + 快照 + 全市场评分 ≈ 2.6s(测试机实测),
        # 前端 30s 轮询会反复命中这条慢路径。
        # 名单 + 评分按 (uid, fs, 参数指纹) 缓存 60s: 参数一改 key 就变 → 正常重算;
        # 实时价格由下方 spot_map 每次现拉、前端 merge 覆盖, 故缓存**不影响价格实时性**。
        # TTL 必须 > 前端 30s 轮询(TTL≤轮询周期 ⇒ 命中率≈0, auction-overview 已踩过)。
        ck_full = None
        if action == "refresh" and not before930 and strategy == "auction":
            ck_full = "stocks_refresh:%s:%s:%s" % (
                uid, fs, history._canon_filter_fingerprint(f))
            hit = _cstore.get(ck_full)
            if hit is not None:
                # 2026-09-05 B 方案: 行情仅内联覆盖 list item, 不再整体下发 spotMap 字段
                spot_map = {}
                try:
                    spot_map = fetcher.fetch_spot_quote_map(fs)
                except Exception as e:
                    log.warning("refresh计算缓存命中但全市场行情拉取失败(降级: 无实时覆盖) err=%s", e)
                if spot_map:
                    for it in hit:
                        rt = spot_map.get(it["code"])
                        if rt:
                            it["price"] = rt.get("price")
                            it["realChange"] = rt.get("realChange")
                            it["entityChange"] = rt.get("entityChange")
                            it["volRatio"] = rt.get("volRatio")
                            it["turnover"] = rt.get("turnover")
                log.info("选股refresh命中计算缓存 uid=%s 返回%d只(跳全市场重拉/评分)",
                         uid, len(hit))
                return jr({
                    "ok": True, "strategy": "auction", "mode": "auction", "list": hit,
                    "count": len(hit), "before930": before930,
                    "dataTime": int(time.time()),
                    "reused": True, "source": "calc_cache",
                })
        # 评分筛选: 沿用原逻辑(9:30 前 lock 强制, refresh/filter 走 TTL 缓存)
        # 2026-09-07 主人方案「候选池=全市场, 且直接读快照不实时拉」:
        #   窗口外(9:30 后/盘后/休市) filter 用当日 9:25 定格快照表(已自动采集)粗筛
        #   → 候选几十只按 code 点查完整行情(fetch_raw_by_codes, 东财 ulist)
        #   → 原 scorer 全流程评分过滤。替代原 ensure_cache: ①原 Top200 涨幅榜与
        #   「涨幅≤7%」反向错配(默认只出 5 只) ②实时拉全市场 28 页(打数据源)。
        #   2026-09-08 方案 A+: 东财点查失败**先切腾讯按 code 点查**(名单仍=快照池固定,
        #   实时字段补真实, 过滤不虚胖) — 腾讯也失败才降级 9:25 快照行直出(保名单非空);
        #   全程不再降级实时全市场(双 worker 缓存不一致+28 页拉取 = 名单波动+大跌票有
        #   概率混入的根因)。窗口内(9:15-9:30 实时竞价)/快照池整体不可用(空库/DB 故障)
        #   才降级 ensure_cache。
        raw = None
        err = None
        # 2026-09-08 主人核心诉求(同条件名单波动 + 加载慢): 快照候选池适用时段从
        # 「9:30 后(盘后)」扩展至「9:15 前(凌晨/盘前)」— 该时段当日既无 9_25 定格
        # 快照也无实时竞价(东财只回昨日收盘缓存), 原 ensure_cache 实时拉全市场 28 页:
        # ① 双 uvicorn worker 缓存不一致(raw 5548↔5556)+昨日额命中爬坡 → 同筛选参数
        # 名单波动(9/8 07:26-07:29 生产实测同 filters 交集仅 13/30), 边界票进出即
        # 「有概率」混入大跌票; ② 28 页拉取+全量昨日额 → 加载 5-15s。
        # load_snapshot_full 自动回退最近交易日(15 自然日) → 凌晨拿到最近交易日 9:25
        # 定格候选池: 快照不变 → 名单幂等稳定; 点查仅几十只 → 加载 <3s。
        # 9:15-9:30 竞价窗口保持实时: 当日动态竞价(9:25 前涨幅演进/9:25 定格)只能走
        # 实时源。lock 与 filter 同源(9:30 前 lock=研究锁定, 同样受益于幂等名单);
        # lock 仅限 hm<9:15 走快照池 — 9:30 后 lock 仍需 ensure_cache 的
        # 「9:30 后禁止重新选股」业务拒绝, 不得绕过。
        # 2026-09-08 同源修复(主人反馈"刷新无数据, 点应用才有"): 原 refresh 被排除在
        # 快照候选池之外(条件只认 filter/9:15前lock) → 9:30 后无批次可直读时, refresh 走
        # ensure_cache 实时全市场(盘中 f615 无竞价值 → 常被筛成 0 只), 而 filter 走定格
        # 快照池能出几十只 → 同一套筛选条件刷新与应用结果不一致。
        # 按模式层已定语义(INTRADAY: deterministic=True, source_priority 定格优先),
        # 9:30 后 refresh 必须与 filter 同源走快照池: 名单幂等固定 + 刷新也有数据。
        hm = scorer._bj_hm()
        use_snapshot_pool = strategy == "auction" and (
                (action == "filter" and (not before930 or hm < 9 * 60 + 15))
                or (action == "lock" and hm < 9 * 60 + 15)
                or (action == "refresh" and not before930))
        if use_snapshot_pool:
            try:
                snap_rows = auction_snapshot.load_snapshot_full()
                if snap_rows:
                    yzt = set()
                    if not f["limitUp"]:
                        try:
                            yzt = set(fetcher.get_yesterday_zt_codes())
                        except Exception as e:
                            log.warning("昨涨停集合拉取失败(不剔除昨涨停, 概念层兜底) err=%s", e)
                    snap_codes = _snapshot_candidate_codes(snap_rows, f, yzt)
                    if snap_codes:
                        try:
                            raw = fetcher.fetch_raw_by_codes(snap_codes)
                            log.info("选股快照候选池 uid=%s markets=%s 粗筛%d只 点查%d只 "
                                     "(快照表, 不拉全市场)", uid, ",".join(f["markets"]),
                                     len(snap_codes), len(raw))
                        except Exception as e:
                            # 2026-09-08 方案 A+(主人拍板, 接替方案 A): 东财 ulist 断连
                            # → **先切腾讯按 code 点查**(fetch_tencent_by_codes): 名单仍=
                            # 9:25 快照池粗筛候选(幂等固定), 腾讯只把现价/涨幅/今开/换手等
                            # 实时展示字段补真实, 评分字段(竞涨/竞额)窗口外被 9:25 定格
                            # day_bid_change/day_bid_amt map 覆写不受影响 → 过滤不再虚胖
                            # (9/8 早实测: 同参数点查成功 35 只 vs 快照行直出 70 只 —
                            # 直出行 price=0 让 priceGt 失效、confidence 偏高让双低剔除失效)。
                            # 腾讯也失败(网络全挂/腾讯熔断)才最后降级 9:25 快照行直出保名单。
                            try:
                                raw = fetcher.fetch_tencent_by_codes(snap_codes)
                                log.info("选股快照候选池东财点查失败→腾讯点查兜底成功 "
                                         "uid=%s markets=%s 返回%d只", uid,
                                         ",".join(f["markets"]), len(raw))
                            except Exception as e2:
                                log.warning("选股快照候选池东财+腾讯点查均失败, 降级快照行"
                                            "直出名单(不拉实时全市场) uid=%s err=%s",
                                            uid, str(e2)[:150])
                                raw = _snapshot_rows_to_raw(snap_rows, snap_codes)
            except Exception as e:
                # 快照池整体不可用(空库/DB 异常/粗筛异常)才兜底实时全市场 — 与点查网络
                # 抖动(方案 A 快照行直出)区分: 空库场景无快照行可用, 只能实时拉
                log.warning("快照候选池不可用(空库/异常)降级实时全市场 uid=%s err=%s",
                            uid, str(e)[:150])
                raw = None
        if raw is None:
            raw, err = fetcher.ensure_cache(action, fs, before930)
        if err:
            log.warning("选股被拒 uid=%s action=%s strategy=%s err=%s", uid, action, strategy, err)
            return jr({"ok": False, "msg": err}, 403)
        # 昨日成交额(并发拉日K, 当日缓存), 用于计算竞价/昨日成交占比
        yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw])
        # 2026-09-08 昨日涨幅真实化: 走同一份日K缓存(零额外请求), 供"昨日涨幅"因子评分
        yesterday_chg_map = fetcher.fetch_yesterday_changes([s.get("f12") for s in raw])
        # 9:20 快照(用于 9:25 涨幅加速度); 非竞价时段读库无数据返回空 map
        snapshot_map = auction_snapshot.load_snapshot()
        # 2026-09-03 竞额定格 map(9_25 快照): 竞价/落库 bidAmt 以当日定格竞价额为准,
        # 避免腾讯兜底期把实时成交额当竞额落库/展示
        bid_amt_map = auction_snapshot.load_day_bid_amt()
        # 2026-09-08 竞涨定格 map(9_25 快照): 东财行情 f615 收盘后返回 "-", 评分阶段
        # 窗口外 bidChange 以当日定格竞价涨幅为准(防退 f3 → 竞涨=现涨/涨幅过滤按现价)
        bid_chg_map = auction_snapshot.load_day_bid_change()
        # 竞价上下文日志(排查关键): 窗口状态/快照覆盖/昨日额命中
        auction_ok = scorer.in_auction_window()
        # 2026-09-08: 昨日涨幅命中率入日志 —— 该因子权重 6%, 缺失(default 0.15)与命中
        # (0.4/0.65/0.9)单票概率差 1.5~4.5 分, 是"同参数两次结果 top 票 90↔93"的漂移源。
        # 命中率低 = 本批整体降级, 必须可见(契约铁律2), 否则排查时只能看到分数变化。
        log.info("选股上下文 uid=%s action=%s auction_window=%s 9_20快照=%d只 昨日额命中=%d/%d "
                 "昨涨命中=%d/%d raw=%d只",
                 uid, action, auction_ok, len(snapshot_map), len(yesterday_map), len(raw),
                 len(yesterday_chg_map), len(raw), len(raw))
        # 竞价窗口内 lock 但当日 9:20 快照缺失 → 加速度无法计算, 必须告警(数据过了点无法补采)
        if action == "lock" and auction_ok and not snapshot_map:
            log.warning("9:25 lock 时当日 9:20 快照缺失! 加速度无法计算, 请检查9:20调度/东财接口 uid=%s", uid)
        # 评分计算不持锁: 多用户并发选股互不阻塞, 只共享只读的行情快照
        # 2026-09-01 抢筹口径: 左视图抢筹=右视图竞价异动"竞价抢筹"代码集(9:20→9:25涨幅/最后一秒段)
        qc_detail = kpl.get_qiangchou_detail()
        # 重构 P5 切流: picker_cutover=1 → 主链路走 picker.pipeline, 不可用自动回退老链路
        used_new = False
        result = None
        if _cutover_enabled():
            result = _run_new_pipeline(uid, action, f, yesterday_map=yesterday_map,
                                       yesterday_chg_map=yesterday_chg_map,
                                       snapshot_map=snapshot_map,
                                       bid_amt_map=bid_amt_map,
                                       bid_chg_map=bid_chg_map)
            used_new = result is not None
        if result is None:
            result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map, qiangchou_detail=qc_detail,
                                               day_bid_amt=bid_amt_map, day_bid_change=bid_chg_map,
                                               yesterday_chg_map=yesterday_chg_map,
                                               strengths=_load_strengths(raw))
        # 概念用开盘啦覆盖(落库前覆盖: 页面/历史批次/推送全部统一开盘啦概念)
        _apply_kpl_board(result, "auction")
        if _gray_enabled():
            if used_new:
                # 切流后反向对拍: 返回的是新结果, 旁路跑老链路比对(观察期用, 只打日志)
                _parity_reverse(uid, action, raw or [], f, result,
                                yesterday_map=yesterday_map, snapshot_map=snapshot_map,
                                bid_amt_map=bid_amt_map, bid_chg_map=bid_chg_map,
                                yesterday_chg_map=yesterday_chg_map)
            else:
                _gray_run(uid, action, raw or [], f, result,
                          bid_amt_map=bid_amt_map, bid_chg_map=bid_chg_map,
                          yesterday_chg_map=yesterday_chg_map)
    except Exception as e:
        log.error("选股处理失败 uid=%s action=%s strategy=%s err=%s", uid, action, strategy, e, exc_info=True)
        return jr({"ok": False, "msg": "服务端处理失败: %s" % e}, 500)

    # 落库: 锁定选股与筛选重算都保存为该用户的历史批次, 实时刷新(refresh)不落库
    # 2026-09-02 同参去重: filter 60s 内相同筛选参数不重复落库(防脚本/手滑刷历史批次);
    # lock 保留原语义(9:30 前唯一锁定+推送), system_batch(auto)/auto_apply 直连不经过此路由
    if action in ("lock", "filter"):
        if action == "filter":
            dup = history.recent_same_filter(uid, f, window=60)
            if dup:
                log.info("选股同参去重 uid=%s markets=%s 60s内重复filter命中批次=%s 跳过落库",
                         uid, ",".join(f["markets"]), dup)
                batch_id = dup
            else:
                batch_id = history.save_batch(uid, action, result, f)
        else:
            batch_id = history.save_batch(uid, action, result, f)
        log.info("选股落库 uid=%s action=%s markets=%s 返回%d只 batch=%s 耗时%.0fms",
                 uid, action, ",".join(f["markets"]), len(result), batch_id, (time.time() - t0) * 1000)
    else:
        log.info("选股刷新 uid=%s action=%s markets=%s 返回%d只 耗时%.0fms",
                 uid, action, ",".join(f["markets"]), len(result), (time.time() - t0) * 1000)

    # 竞价锁定选股成功后, 后台推送结果到微信/飞书(失败不影响选股主流程)
    if action == "lock" and result:
        try:
            notify.push_result_async(result, f)
        except Exception as e:
            log.error("推送触发失败 err=%s", e)
        # 顺带统计当日一字涨停(数量+竞价总额), 失败不影响选股
        try:
            stats.record_daily_yizi(raw)
        except Exception as e:
            log.error("一字涨停统计失败 err=%s", e)

    # 写回 refresh 计算缓存(仅 9:30 后 auction refresh; lock/filter 有落库副作用不缓存)
    if ck_full and result:
        try:
            _cstore.set(ck_full, result, ttl=60)
        except Exception as e:
            log.warning("refresh计算缓存写入失败(不影响本次返回) err=%s", e)

    return jr({
        "ok": True,
        "strategy": "auction", "mode": "auction",
        "list": result,
        "count": len(result),
        "before930": before930,
        # 2026-09-08: 快照候选池路径不写 fetcher._cache → 硬取 [fs] 会 KeyError(凌晨
        # 全走快照池后必现)。dataTime 语义=行情数据时间, 兜底用当前时刻即可。
        "dataTime": int((fetcher._cache.get(fs) or {}).get("ts") or time.time()),
    })


@router.get("/api/quotes")
def api_quotes(request: Request, uid: int = Depends(get_uid)):
    """按需实时行情(2026-09-05 B 方案): 前端 9:30 后合并不在返回名单的锁定票时,
    对少量 code 拉实时价; /api/stocks 已不再下发全市场 spotMap, 响应因此大幅瘦身。
    codes = 逗号分隔的 6 位代码(取自名单 item.code, 如 600000,000001)"""
    q = qs(request)
    codes = [c.strip() for c in (q.get("codes") or [""])[0].split(",") if c.strip()]
    if not codes:
        return jr({"ok": False, "msg": "codes 必填"}, 400)
    # 去重 + 限长保护(单个请求避免恶意超长)
    codes = list(dict.fromkeys(codes))[:500]
    try:
        quotes = fetcher.fetch_spot_quotes_by_codes(codes)
    except Exception as e:
        log.warning("按需行情拉取失败 uid=%s err=%s", uid, str(e)[:200])
        quotes = {}
    return jr({"ok": True, "quotes": quotes, "count": len(quotes)})


@router.get("/api/stock/chart")
def api_stock_chart(request: Request, uid: int = Depends(get_uid),
                    code: str = "", period: str = "day"):
    """个股图表(分时/K线): 点击股票弹框用
    code: 股票代码 6位数字
    period: minute 当日分时(价格+均价+成交量) | day 日K(默认120根前复权)
            | week 周K(120根) | month 月K(60根)
    返回标准化 arrays 便于 ECharts 直接消费"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code 参数"}, 400)
    period = (period or "day").lower()
    if period not in ("minute", "day", "week", "month"):
        return jr({"ok": False, "msg": "period 非法: 仅 minute/day/week/month"}, 400)
    data = fetcher.fetch_stock_chart_robust(code, period)
    if not data:
        return jr({"ok": False, "msg": "图表数据拉取失败(所有数据源均不可用)"}, 502)
    return jr({"ok": True, **data})
