# -*- coding: utf-8 -*-
"""
历史路由: 批次列表/明细 + 条件分页查询
======================================
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import history, kpl
from ..services.cache_store import store   # 2026-09-04: 批次列表/明细结果缓存
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()

# 2026-09-04: /api/history 生产实测冷请求 1015ms(首屏并发之一), 每次全量查库,
# 此前**完全无缓存**。批次数据落库后基本不变(仅概念列会被 apply_board_concept 幂等补齐),
# 前端 30s 轮询会反复查同一批 → 结果缓存(列表 30s / 明细 300s) + single-flight 防击穿。
# 注意: 400(参数错)/404(批次不存在) 不进缓存, 避免错误响应被固化。
_HIST_LIST_TTL = 30
_HIST_BATCH_TTL = 300


@router.get("/api/history")
def api_history(request: Request, uid: int = Depends(get_uid)):
    q = qs(request)
    batch_id = (q.get("batch") or [None])[0]
    if batch_id:
        try:
            batch_id = int(batch_id)
        except ValueError:
            log.warning("历史批次参数错误 uid=%s batch=%s", uid, batch_id)
            return jr({"ok": False, "msg": "参数错误"}, 400)

        from ..services.cache_store import cached_singleflight
        ck = "hist_batch:%s:%s" % (uid, batch_id)

        def _load():
            batch, stocks = history.get_batch(batch_id, uid)
            if batch is None:
                return None
            # 历史批次概念统一补开盘啦(旧批次落库存的是东财 f103; 新批次落库时已覆盖, 幂等)
            kpl.apply_board_concept(stocks, "history_batch")
            return {"ok": True, "batch": batch, "stocks": stocks}

        payload = cached_singleflight(store, ck, _HIST_BATCH_TTL, _load)
        if payload is None:      # 批次不存在(不缓存) → 走原逻辑返回 404
            log.warning("批次不存在 uid=%s batch=%s", uid, batch_id)
            return jr({"ok": False, "msg": "批次不存在"}, 404)
        log.info("历史批次明细 uid=%s batch=%s 明细%d只", uid, batch_id,
                 len(payload.get("stocks") or []))
        return jr(payload)

    from ..services.cache_store import cached_singleflight
    ck = "hist_list:%s" % uid
    payload = cached_singleflight(store, ck, _HIST_LIST_TTL,
                                  lambda: {"ok": True, "batches": history.list_batches(uid)})
    log.info("历史批次列表 uid=%s 共%d批", uid, len(payload.get("batches") or []))
    return jr(payload)


@router.get("/api/history/query")
def api_history_query(request: Request, uid: int = Depends(get_uid)):
    t0 = time.time()
    try:
        r = history.query_history(uid, qs(request))
    except Exception as e:
        log.error("历史查询失败 uid=%s err=%s", uid, e, exc_info=True)
        return jr({"ok": False, "msg": "查询失败: %s" % e}, 500)
    log.info("历史查询 uid=%s total=%d 返回%d条 耗时%.0fms", uid, r["total"], len(r["rows"]), (time.time() - t0) * 1000)
    # 历史聚合概念补开盘啦: 并发按股查(≤120 只全量覆盖, 超限只做榜单层避免拖慢)
    try:
        kpl.apply_board_concept(r["rows"], "history_query", deep=len(r["rows"]) <= 120)
    except Exception as e:
        log.warning("历史查询概念覆盖失败 err=%s", e)
    return jr({"ok": True, "count": len(r["rows"]), "total": r["total"],
               "page": r["page"], "pageSize": r["pageSize"], "list": r["rows"]})
