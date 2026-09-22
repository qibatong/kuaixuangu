# -*- coding: utf-8 -*-
"""
用户行为上报路由(2026-09-22, v4.11.35)
=======================================
- POST /api/activity/track   前端「用户主动操作」上报(计入功能使用记录)

★ 为什么由前端上报而不是后端数接口请求(主人 2026-09-22 拍板的口径):
    口径是「用户选股点一次『应用』记 1 次」。后端无法区分「这一次请求是用户点的」
    还是「30s 轮询」——实测 09-21 单日 `/api/stocks` 8,174 次里绝大多数是轮询;
    而 P3 本地筛选路径下, 点「应用」甚至不一定发请求(用缓存快照在浏览器内筛)。
    所以「点击」这个事实只有前端知道, 由前端在动作回调里上报是唯一准确的来源。

★ 不信任客户端: uid / 时间 / 日期一律由服务端决定, 客户端只能给 feature
    与 blocked 标记, 且 feature 必须在白名单内(否则静默忽略, 不报错——
    埋点失败不该在用户侧冒泡)。
"""
from fastapi import APIRouter, Body, Depends, Request

from ..core import logger
from ..services import activity
from .deps import client_ip, get_uid, jr

log = logger.get_logger(__name__)

router = APIRouter()


@router.post("/api/activity/track")
def api_activity_track(request: Request, body: dict = Body(default={}),
                       uid: int = Depends(get_uid)):
    """上报一次用户主动操作. body: {feature, blocked?}
    返回 {ok: true, feature, counted} —— counted=false 表示 feature 不合法被忽略。"""
    feature = str(body.get("feature") or "").strip()
    blocked = bool(body.get("blocked"))
    if feature not in activity.FEATURES:
        # 不合法就安静地忽略(埋点问题不应打断用户)
        log.warning("行为上报 feature 非白名单 uid=%s feature=%r ip=%s",
                    uid, feature, client_ip(request))
        return jr({"ok": True, "counted": False, "feature": feature})
    ok = activity.bump_usage(uid, feature, blocked=blocked)
    return jr({"ok": True, "counted": bool(ok), "feature": feature,
               "label": activity.FEATURES.get(feature)})
