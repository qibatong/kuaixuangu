# -*- coding: utf-8 -*-
"""
股性功能路由: 排行 + 个股画像
======================================================
- GET /api/stock-temper/rank        股性排行(按综合分降序, 支持分页/最少涨停数)
- GET /api/stock-temper/{code}      单只股票股性画像
- POST /api/stock-temper/backfill   历史回补(管理/一次性调用, 需VIP)
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import stock_temper
from .deps import get_uid, jr, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/stock-temper/rank")
def api_stock_temper_rank(request: Request, uid: int = Depends(get_uid),
                          page: int = 1, size: int = 50, min_zt: int = 0,
                          keyword: str = ""):
    """股性排行列表: 综合分降序 + 封板率/炸板率/次日溢价/高开率/大阴线(intuit: 直读画像落库表)
    keyword 支持按代码/名称模糊搜索"""
    page = max(1, int(page or 1))
    size = max(1, min(200, int(size or 50)))
    data = stock_temper.rank(page=page, size=size, min_zt=max(0, int(min_zt or 0)),
                             keyword=keyword or "")
    if request.query_params.get("light"):
        # 轻量模式: 只返回排行列表, 不触发逐股日K现算(limit_history 聚合足够)
        for it in data.get("list", []):
            it.pop("ratio_detail", None)
    return jr({"ok": True, **data})


@router.get("/api/stock-temper/{code}")
def api_stock_temper_profile(request: Request, code: str,
                             uid: int = Depends(get_uid),
                             refresh: int = 0):
    """单只股票股性画像(含封板率/炸板率/次日溢价/高开率/大阴线/综合分/标签)
    ?refresh=1 强制刷新日K缓存"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code"}, 400)
    p = stock_temper.compute_profile(code, refresh_kline=bool(int(refresh or 0)))
    return jr({"ok": True, **p})


@router.post("/api/stock-temper/backfill")
def api_stock_temper_backfill(request: Request, start: str = None, end: str = None,
                              uid: int = Depends(require_vip_or_paid)):
    """历史回补过去 N 个自然日的涨停/炸板记录(一次性/管理用)。
    start/end 均为 YYYY-MM-DD, 缺省按「最近365天」估。结束后自动全量重建画像。"""
    from datetime import date, timedelta
    end_date = end or date.today().isoformat()
    start_date = start or (date.today() - timedelta(days=365)).isoformat()

    def _bf_task():
        stock_temper.backfill(start_date, end_date)
        try:
            stock_temper.rebuild_profiles()
        except Exception as e:
            log.warning("回补后画像重建异常 err=%s", e)

    # 幂等且可能较慢: 用线程异步执行, 立即返回
    import threading
    threading.Thread(target=_bf_task, daemon=True,
                     name="stock-temper-backfill").start()
    return jr({"ok": True, "msg": f"回补任务已启动 {start_date}..{end_date}, 后台执行"})


@router.post("/api/stock-temper/rebuild")
def api_stock_temper_rebuild(request: Request,
                             uid: int = Depends(require_vip_or_paid)):
    """立即全量重算并落库全部股性画像(方案B)。数据盘后才会变化, 平时无需调用。"""
    import threading
    threading.Thread(target=stock_temper.rebuild_profiles, daemon=True,
                     name="stock-temper-rebuild").start()
    return jr({"ok": True, "msg": "画像全量重建已启动(后台执行)"})