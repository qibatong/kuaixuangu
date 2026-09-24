# -*- coding: utf-8 -*-
"""
健康检查路由: 数据源状态 / 服务状态
"""
import asyncio

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from ..services import fetcher
from .deps import get_uid, jr

router = APIRouter()

_CONTRACTS_TIMEOUT = 3.0        # 字段级体检的硬超时(秒)


@router.get("/api/health")
async def api_health(request: Request, uid: int = Depends(get_uid)):
    """数据源健康快照(需登录): 东财行情/东财日K/同花顺兜底 状态与统计
    2026-09-10: 改 async —— 探活接口若被慢选股堵在 anyio 线程池里会 504, 让监控/运维
    误判成"服务挂了"(实际只是排队)。async 直接在 event loop 执行, 永不进排队队列。

    2026-09-24 (v4.11.42): 追加 `contracts` 段 —— 字段级就绪全景(来自字段契约注册表,
    见 services/contracts)。同步 SQLite 探测放线程池 + 3s 硬超时 + 异常隔离:
    体检失败只写一个 error 段, 既不阻塞 event loop, 也绝不拖垮健康检查本身。
    """
    health = {"ok": True, **fetcher.get_health_status()}
    try:
        from ..services import contracts
        from ..services.auction_snapshot import _bj_date
        health["contracts"] = await asyncio.wait_for(
            run_in_threadpool(contracts.health_snapshot, _bj_date()),
            timeout=_CONTRACTS_TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        health["contracts"] = {"error": str(e)[:120]}
    return jr(health)
