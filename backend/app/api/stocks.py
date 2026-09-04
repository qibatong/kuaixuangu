# -*- coding: utf-8 -*-
"""
选股路由: GET /api/stocks?action=lock|filter|refresh|ping&mode=auction|spot&筛选参数...
===================================================================
mode=auction  竞价选股(默认, 行为不变): lock 9:30 前唯一锁定, 评分=竞价涨幅/竞价换手/异动...
mode=spot     盘中实时选股: 随时 refresh, 评分=实时涨幅/量比/换手/封单强度..., 涨停留池接口
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import auction_snapshot, fetcher, history, kpl, notify, scorer, stats
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


def _apply_kpl_board(result, log_tag=""):
    """用开盘啦概念覆盖选股结果 concept(统一走 kpl.apply_board_concept 双层覆盖)
    result: scorer.process_all_stocks 输出, 每项含 code/concept"""
    kpl.apply_board_concept(result, log_tag)


@router.get("/api/stocks")
def api_stocks(request: Request, uid: int = Depends(get_uid)):
    q = qs(request)
    action = (q.get("action") or ["filter"])[0]
    mode = (q.get("mode") or ["auction"])[0]
    force = (q.get("force") or ["0"])[0] in ("1", "true", "True")   # 主动重锁(绕过当日幂等)
    if action not in ("lock", "filter", "refresh", "ping"):
        log.warning("选股非法参数 action=%s uid=%s", action, uid)
        return jr({"ok": False, "msg": "非法参数"}, 400)
    if mode not in ("auction", "spot"):
        log.warning("选股非法参数 mode=%s uid=%s", mode, uid)
        return jr({"ok": False, "msg": "非法参数 mode"}, 400)
    if action == "ping":
        _, _, before930 = scorer.bj_now()
        return jr({"ok": True, "before930": before930})

    f = scorer.validate_filters(q)
    fs = scorer.market_fs(f["markets"])
    _, _, before930 = scorer.bj_now()
    t0 = time.time()

    try:
        # 盘中模式: 全市场拉取 + 与竞价同一套评分/过滤逻辑(诗人需求: 盘中=不锁定的竞价)
        if mode == "spot":
            raw, err = fetcher.ensure_spot_cache("refresh", fs, before930)
            if err:
                log.warning("选股被拒 uid=%s action=%s mode=%s err=%s", uid, action, mode, err)
                return jr({"ok": False, "msg": err}, 403)
            # 全市场实时行情 map(供锁定名单 merge 用, 不参与评分)
            spot_map = {}
            for s in raw:
                spot_map[s.get("f12")] = {
                    "realChange": scorer.parse_float(s.get("f3")),
                    "entityChange": scorer.get_entity_change(s),
                    "price": scorer.parse_float(s.get("f2")),
                    "volRatio": scorer.parse_float(s.get("f10")),
                    "turnover": scorer.parse_float(s.get("f8")),
                    "name": s.get("f14") or "",
                }
            # 复用竞价评分 + 竞价过滤: 盘中=不锁定的竞价, 9:30 后持续刷新, 名单会变(符合诗人预期)
            yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw])
            snapshot_map = auction_snapshot.load_snapshot()
            # 2026-09-03 竞额定格 map(9_25 快照): 盘中「竞额」不以腾讯伪 f616(=实时成交额)为准
            bid_amt_map = auction_snapshot.load_day_bid_amt()
            # 2026-09-01 抢筹口径: 左视图抢筹=右视图竞价异动"竞价抢筹"代码集(9:20→9:25涨幅/最后一秒段)
            qc_codes = kpl.get_qiangchou_codes()
            result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map, qiangchou_codes=qc_codes,
                                               day_bid_amt=bid_amt_map)
            _apply_kpl_board(result, "spot")
            log.info("盘中选股(同竞价逻辑) uid=%s markets=%s raw=%d只 返回%d只 耗时%.0fms",
                     uid, ",".join(f["markets"]), len(raw), len(result), (time.time() - t0) * 1000)
            # 盘中 refresh 不落库、不推送(避免高频刷屏); 只返回实时结果
            return jr({
                "ok": True, "mode": "spot",
                "list": result, "count": len(result),
                "before930": before930,
                "spotMap": spot_map,
                "dataTime": int(fetcher._cache[fs]["ts"]),
            })

        # ---- 竞价模式 ----
        # 2026-09-02 当日幂等(主人确认): 9:30 前页面自动 lock 若当日已存在
        # "9:25 后落库 + 同筛选参数"的手动 lock 批次 → 直读批次返回,
        # 不再全量重拉行情/重复落库/重复推送(解决"每次重新登录都重算一次+堆 lock 历史")。
        # 仅拦自动 lock(force=0): 9:25 前数据未定型仍每次重算; 用户主动点「锁定」(force=1)
        # 或参数已改 → 正常重算。命中返回结构含 idempotent=True 供前端识别。
        if (action == "lock" and before930 and mode == "auction" and not force):
            try:
                lock_b = history.find_today_lock_matching(uid, f)
                if lock_b:
                    lst = history.get_batch_stocks_mapped(lock_b["id"])
                    log.info("选股lock当日幂等 uid=%s batch=%s 直读%d只(跳重拉/落库/推送)",
                             uid, lock_b["id"], len(lst))
                    return jr({
                        "ok": True, "mode": "auction", "list": lst,
                        "count": len(lst), "before930": True,
                        "spotMap": {}, "dataTime": int(lock_b.get("ts") or time.time()),
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
        if (action == "refresh" and not before930 and mode == "auction"):
            try:
                reuse_bid, reuse_src = history.find_today_reusable_batch(uid, f)
                if reuse_bid:
                    lst = history.get_batch_stocks_mapped(reuse_bid)
                    # 全市场实时行情(缓存命中≈0ms; 拉取失败降级: 定格名单无实时覆盖, 下轮自愈)
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
                        "ok": True, "mode": "auction", "list": lst,
                        "count": len(lst), "before930": False,
                        "spotMap": spot_map, "dataTime": int(time.time()),
                        "reused": True, "source": reuse_src, "batch_id": reuse_bid,
                    })
            except Exception as e:
                log.warning("refresh 直读批次失败(降级正常重算) uid=%s err=%s", uid, e)
        # 评分筛选: 沿用原逻辑(9:30 前 lock 强制, refresh/filter 走 TTL 缓存, 至多200只)
        raw, err = fetcher.ensure_cache(action, fs, before930)
        if err:
            log.warning("选股被拒 uid=%s action=%s mode=%s err=%s", uid, action, mode, err)
            return jr({"ok": False, "msg": err}, 403)
        # 全市场实时行情 map(仅 9:30 后需要; 独立拉取, 不参与评分, 供锁定名单 merge)
        spot_map = {}
        if not before930:
            try:
                all_raw = fetcher.fetch_spot_quote_map(fs)
                spot_map = all_raw
            except Exception as e:
                log.warning("竞价模式全市场行情拉取失败(降级: spotMap 为空) err=%s", e)
        # 昨日成交额(并发拉日K, 当日缓存), 用于计算竞价/昨日成交占比
        yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw])
        # 9:20 快照(用于 9:25 涨幅加速度); 非竞价时段读库无数据返回空 map
        snapshot_map = auction_snapshot.load_snapshot()
        # 2026-09-03 竞额定格 map(9_25 快照): 竞价/落库 bidAmt 以当日定格竞价额为准,
        # 避免腾讯兜底期把实时成交额当竞额落库/展示
        bid_amt_map = auction_snapshot.load_day_bid_amt()
        # 竞价上下文日志(排查关键): 窗口状态/快照覆盖/昨日额命中
        auction_ok = scorer.in_auction_window()
        log.info("选股上下文 uid=%s action=%s auction_window=%s 9_20快照=%d只 昨日额命中=%d/%d raw=%d只",
                 uid, action, auction_ok, len(snapshot_map), len(yesterday_map), len(raw), len(raw))
        # 竞价窗口内 lock 但当日 9:20 快照缺失 → 加速度无法计算, 必须告警(数据过了点无法补采)
        if action == "lock" and auction_ok and not snapshot_map:
            log.warning("9:25 lock 时当日 9:20 快照缺失! 加速度无法计算, 请检查9:20调度/东财接口 uid=%s", uid)
        # 评分计算不持锁: 多用户并发选股互不阻塞, 只共享只读的行情快照
        # 2026-09-01 抢筹口径: 左视图抢筹=右视图竞价异动"竞价抢筹"代码集(9:20→9:25涨幅/最后一秒段)
        qc_codes = kpl.get_qiangchou_codes()
        result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map, qiangchou_codes=qc_codes,
                                           day_bid_amt=bid_amt_map)
        # 概念用开盘啦覆盖(落库前覆盖: 页面/历史批次/推送全部统一开盘啦概念)
        _apply_kpl_board(result, "auction")
    except Exception as e:
        log.error("选股处理失败 uid=%s action=%s mode=%s err=%s", uid, action, mode, e, exc_info=True)
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

    return jr({
        "ok": True,
        "mode": "auction",
        "list": result,
        "count": len(result),
        "before930": before930,
        "spotMap": spot_map,   # 全市场实时行情(9:30 后锁定名单 merge 用)
        "dataTime": int(fetcher._cache[fs]["ts"]),
    })


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
