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
from ..services import auction_snapshot, fetcher, history, notify, scorer, stats
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/stocks")
def api_stocks(request: Request, uid: int = Depends(get_uid)):
    q = qs(request)
    action = (q.get("action") or ["filter"])[0]
    mode = (q.get("mode") or ["auction"])[0]
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
        # 盘中模式: 全市场拉取 + 盘中评分(按 SPOT_CACHE_TTL 刷新)
        if mode == "spot":
            raw, err = fetcher.ensure_spot_cache("refresh", fs, before930)
            if err:
                log.warning("选股被拒 uid=%s action=%s mode=%s err=%s", uid, action, mode, err)
                return jr({"ok": False, "msg": err}, 403)
            # 全市场实时行情 map(code -> 实时字段)
            spot_map = {}
            for s in raw:
                spot_map[s.get("f12")] = {
                    "realChange": scorer.parse_float(s.get("f3")),
                    "entityChange": scorer.get_entity_change(s),
                    "price": scorer.parse_float(s.get("f2")),
                }
            zt_map = fetcher.fetch_zt_pool()
            result = scorer.process_spot_stocks(raw, f, zt_map)
            log.info("盘中选股 uid=%s markets=%s raw=%d只 涨停池=%d只 返回%d只 耗时%.0fms",
                     uid, ",".join(f["markets"]), len(raw), len(zt_map), len(result),
                     (time.time() - t0) * 1000)
            # 盘中 refresh 不落库、不推送(避免高频刷屏); 只返回实时结果
            return jr({
                "ok": True, "mode": "spot",
                "list": result, "count": len(result),
                "before930": before930,
                "spotMap": spot_map,
                "dataTime": int(fetcher._cache[fs]["ts"]),
            })

        # ---- 竞价模式 ----
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
        # 竞价上下文日志(排查关键): 窗口状态/快照覆盖/昨日额命中
        auction_ok = scorer.in_auction_window()
        log.info("选股上下文 uid=%s action=%s auction_window=%s 9_20快照=%d只 昨日额命中=%d/%d raw=%d只",
                 uid, action, auction_ok, len(snapshot_map), len(yesterday_map), len(raw), len(raw))
        # 竞价窗口内 lock 但当日 9:20 快照缺失 → 加速度无法计算, 必须告警(数据过了点无法补采)
        if action == "lock" and auction_ok and not snapshot_map:
            log.warning("9:25 lock 时当日 9:20 快照缺失! 加速度无法计算, 请检查9:20调度/东财接口 uid=%s", uid)
        # 评分计算不持锁: 多用户并发选股互不阻塞, 只共享只读的行情快照
        result = scorer.process_all_stocks(raw, f, yesterday_map, snapshot_map)
    except Exception as e:
        log.error("选股处理失败 uid=%s action=%s mode=%s err=%s", uid, action, mode, e, exc_info=True)
        return jr({"ok": False, "msg": "服务端处理失败: %s" % e}, 500)

    # 落库: 锁定选股与筛选重算都保存为该用户的历史批次, 实时刷新(refresh)不落库
    if action in ("lock", "filter"):
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
