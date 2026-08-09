# -*- coding: utf-8 -*-
"""
选股路由: GET /api/stocks?action=lock|filter|refresh|ping&筛选参数...
===================================================================
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import fetcher, history, scorer
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/stocks")
def api_stocks(request: Request, uid: int = Depends(get_uid)):
    q = qs(request)
    action = (q.get("action") or ["filter"])[0]
    if action not in ("lock", "filter", "refresh", "ping"):
        log.warning("选股非法参数 action=%s uid=%s", action, uid)
        return jr({"ok": False, "msg": "非法参数"}, 400)
    if action == "ping":
        _, _, before930 = scorer.bj_now()
        return jr({"ok": True, "before930": before930})

    f = scorer.validate_filters(q)
    fs = scorer.market_fs(f["markets"])
    _, _, before930 = scorer.bj_now()
    t0 = time.time()

    try:
        raw, err = fetcher.ensure_cache(action, fs, before930)
        if err:
            log.warning("选股被拒 uid=%s action=%s err=%s", uid, action, err)
            return jr({"ok": False, "msg": err}, 403)
        # 昨日成交额(并发拉日K, 当日缓存), 用于计算竞价/昨日成交占比
        yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw])
        # 评分计算不持锁: 多用户并发选股互不阻塞, 只共享只读的行情快照
        result = scorer.process_all_stocks(raw, f, yesterday_map)
    except Exception as e:
        log.error("选股处理失败 uid=%s action=%s err=%s", uid, action, e, exc_info=True)
        return jr({"ok": False, "msg": "服务端处理失败: %s" % e}, 500)

    # 落库: 锁定选股与筛选重算都保存为该用户的历史批次, 实时刷新(refresh)不落库
    if action in ("lock", "filter"):
        batch_id = history.save_batch(uid, action, result, f)
        log.info("选股落库 uid=%s action=%s markets=%s 返回%d只 batch=%s 耗时%.0fms",
                 uid, action, ",".join(f["markets"]), len(result), batch_id, (time.time() - t0) * 1000)
    else:
        log.info("选股刷新 uid=%s action=%s markets=%s 返回%d只 耗时%.0fms",
                 uid, action, ",".join(f["markets"]), len(result), (time.time() - t0) * 1000)

    return jr({
        "ok": True,
        "list": result,
        "count": len(result),
        "before930": before930,
        "dataTime": int(fetcher._cache[fs]["ts"]),
    })
