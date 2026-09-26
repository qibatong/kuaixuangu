# -*- coding: utf-8 -*-
"""
盘前资讯路由 (2026-09-27 v4.11.59 新增)
======================================
- GET /api/news/flash?limit=80   7x24 快讯(猫爪 news + 开盘啦 doc96 合并去重, 时间倒序)
- GET /api/news/premarket        盘前精选(开盘啦头条 doc95 + 明天炒什么 doc97)
- GET /api/news/topic?id=2454    明天炒什么 正文(doc99)

鉴权: 三个接口都要登录(与其它业务页一致)。
埋点: 由前端在页面挂载时上报 feature="news"(见 services/activity.FEATURES 白名单)。
      ⚠️ 白名单是**显式 8 键 → 本轮扩到 9 键**, 加页面时必须同批改,
         否则上报只 warning + counted=false(参见 /lhb 刻意不上报的处置)。

失败语义: 上游挂了**不返回 500** —— 返回 ok=true + degraded=["meoz"/"kpl"] + 现有数据,
          由前端显式显示「某源暂缺」。绝不用空数组冒充「今天没有资讯」。
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import news_feed
from .deps import get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/news/flash")
def api_news_flash(request: Request, limit: int = 80,
                   uid: int = Depends(get_uid)):
    """7x24 快讯: 两源合并去重, 按时间倒序"""
    try:
        d = news_feed.flash(limit)
    except Exception as e:                                  # noqa: BLE001
        log.error("资讯快讯失败 uid=%s err=%s", uid, e, exc_info=True)
        return jr({"ok": False, "msg": "资讯加载失败"}, 500)
    log.info("资讯快讯 uid=%s 返回%d条(猫爪%d/开盘啦%d) degraded=%s",
             uid, d["total"], d["counts"]["meoz"], d["counts"]["kpl"],
             ",".join(d["degraded"]) or "-")
    return jr({"ok": True, **d})


@router.get("/api/news/premarket")
def api_news_premarket(request: Request, uid: int = Depends(get_uid)):
    """盘前精选: 头条 + 明天炒什么"""
    try:
        d = news_feed.premarket()
    except Exception as e:                                  # noqa: BLE001
        log.error("盘前精选失败 uid=%s err=%s", uid, e, exc_info=True)
        return jr({"ok": False, "msg": "盘前精选加载失败"}, 500)
    log.info("盘前精选 uid=%s 头条%d篇 选题%d条 degraded=%s",
             uid, len(d.get("top") or []), len((d.get("topics") or {}).get("items") or []),
             ",".join(d["degraded"]) or "-")
    return jr({"ok": True, **d})


@router.get("/api/news/topic")
def api_news_topic(request: Request, id: str = "",
                   uid: int = Depends(get_uid)):
    """明天炒什么 正文(doc99)"""
    # 2026-09-27: 首版这里没包 try/except, 而 doc99 的 Time 是**格式化日期字符串**,
    # 解析处抛 ValueError → 整页 500。上游字段类型不由我们控制 ⇒ 这里兜住。
    try:
        d = news_feed.topic_detail(id)
    except Exception as e:                                  # noqa: BLE001
        log.error("资讯正文失败 uid=%s id=%s err=%s", uid, id, e, exc_info=True)
        return jr({"ok": False, "msg": "文章加载失败"}, 500)
    if not d:
        return jr({"ok": False, "msg": "文章不存在或暂不可读"}, 404)
    return jr({"ok": True, **d})
