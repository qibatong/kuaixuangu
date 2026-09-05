# -*- coding: utf-8 -*-
"""
连板天梯图片路由: 查看/下载每日盘后生成的连板天梯 PNG
======================================================
- GET /api/ladder/dates           已生成天梯的日期列表(降序)
- GET /api/ladder/image/{date}    查看当日天梯图(内嵌展示)
- GET /api/ladder/image/{date}/download  下载当日天梯图(attachment)
- POST /api/ladder/generate      手动补生成某日天梯(管理/校验用, 需 VIP/付费)
"""
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from ..core import config, logger
from ..services import ladder_image
from .deps import get_uid, jr, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()


def _image_path(date):
    return os.path.join(config.LADDER_IMG_DIR, f"{date}.png")


def _list_dates():
    """返回已生成 PNG 的日期列表(降序)"""
    if not os.path.isdir(config.LADDER_IMG_DIR):
        return []
    dates = []
    for f in os.listdir(config.LADDER_IMG_DIR):
        if f.endswith(".png") and len(f) == 14 and f[4] == "-" and f[7] == "-":
            dates.append(f[:10])
    dates.sort(reverse=True)
    return dates


@router.get("/api/ladder/dates")
def api_ladder_dates(request: Request, uid: int = Depends(get_uid)):
    """已生成连板天梯图的日期列表(降序)(App 内日期选择用)"""
    return jr({"ok": True, "dates": _list_dates()})


@router.get("/api/ladder/image/{l_date}")
def api_ladder_image(request: Request, l_date: str, uid: int = Depends(get_uid)):
    """查看当日连板天梯图; ?download=1 时转为下载附件"""
    path = _image_path(l_date)
    if not l_date or not os.path.isfile(path):
        return jr({"ok": False, "msg": "该日期暂无天梯图, 盘后(15:30)自动生成"}, 404)
    filename = f"连板天梯-{l_date}.png"
    media = "image/png"
    # 每日图重生成后需即时可见, no-store 防浏览器启发式缓存旧图(2026-09-05)
    no_store = {"Cache-Control": "no-store"}
    if request.query_params.get("download"):
        return FileResponse(path, media_type=media, filename=filename,
                            headers=no_store)
    return FileResponse(path, media_type=media, headers=no_store)


@router.get("/api/ladder/image/{l_date}/download")
def api_ladder_image_download(request: Request, l_date: str, uid: int = Depends(get_uid)):
    """下载当日连板天梯图(显式 attachment)"""
    path = _image_path(l_date)
    if not l_date or not os.path.isfile(path):
        return jr({"ok": False, "msg": "该日期暂无天梯图"}, 404)
    return FileResponse(path, media_type="image/png",
                        filename=f"连板天梯-{l_date}.png",
                        headers={"Cache-Control": "no-store"})


@router.post("/api/ladder/generate")
def api_ladder_generate(request: Request, l_date: str,
                        uid: int = Depends(require_vip_or_paid)):
    """手动补生成指定日期的连板天梯图(数据来自 ladder_history)"""
    if not l_date:
        return jr({"ok": False, "msg": "缺少 date"}, 400)
    img = ladder_image.generate_for_date(l_date)
    if not img:
        return jr({"ok": False, "msg": f"{l_date} 无连板数据或生成失败"}, 404)
    return jr({"ok": True, "path": img, "date": l_date})