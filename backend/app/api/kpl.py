# -*- coding: utf-8 -*-
"""
开盘啦数据路由: 竞价委买额/连板梯队/情绪值/涨停原因/板块强度
==========================================================
所有接口均走 kpl 服务(缓存 + 降级), 失败返回空列表/None, 不影响主流程。
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import kpl
from .deps import get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/kpl/sentiment")
def api_kpl_sentiment(request: Request, uid: int = Depends(get_uid)):
    """市场情绪: 涨停家数/情绪指标/连板高度/大幅回撤"""
    d = kpl.fetch_sentiment()
    return jr({"ok": True, "sentiment": d})


@router.get("/api/kpl/bid-seal")
def api_kpl_bid_seal(request: Request, uid: int = Depends(get_uid)):
    """竞价涨停委买额(实时): 涨停委买额/竞价净额/连板数"""
    d = kpl.fetch_bid_seal()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/ladder")
def api_kpl_ladder(request: Request, uid: int = Depends(get_uid)):
    """连板梯队(实时): 首板~五板+"""
    d = kpl.fetch_ladder_all()
    return jr({"ok": True, "ladder": d})


@router.get("/api/kpl/board-rank")
def api_kpl_board_rank(request: Request, uid: int = Depends(get_uid)):
    """板块强度排行(实时)"""
    d = kpl.fetch_board_rank()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/hot-rank")
def api_kpl_hot_rank(request: Request, uid: int = Depends(get_uid)):
    """盘中人气热榜(实时)"""
    d = kpl.fetch_hot_rank()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})


@router.get("/api/kpl/zt-reason")
def api_kpl_zt_reason(request: Request, code: str = "", uid: int = Depends(get_uid)):
    """个股涨停原因(当天/历史)"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code"}, 400)
    d = kpl.fetch_zt_reason(code)
    return jr({"ok": True, "reason": d or []})


@router.get("/api/kpl/wpqc")
def api_kpl_wpqc(request: Request, uid: int = Depends(get_uid)):
    """尾盘竞价抢筹(14:57 后)"""
    d = kpl.fetch_wpqc()
    return jr({"ok": True, "list": d or [], "count": len(d) if d else 0})
