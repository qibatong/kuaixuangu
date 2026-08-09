# -*- coding: utf-8 -*-
"""
健康检查路由: 数据源状态 / 服务状态
"""
from fastapi import APIRouter, Depends, Request

from ..services import fetcher
from .deps import get_uid, jr

router = APIRouter()


@router.get("/api/health")
def api_health(request: Request, uid: int = Depends(get_uid)):
    """数据源健康快照(需登录): 东财行情/东财日K/同花顺兜底 状态与统计"""
    return jr({"ok": True, **fetcher.get_health_status()})
