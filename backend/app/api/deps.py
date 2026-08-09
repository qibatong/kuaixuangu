# -*- coding: utf-8 -*-
"""
API 公共依赖与响应辅助
======================
"""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ..core import logger
from ..services import security, users

log = logger.get_logger(__name__)


def jr(obj, status=200):
    """与旧版 _send_json 一致的 JSON 响应(带 no-store)"""
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})


def qs(request: Request):
    """模拟旧版 parse_qs: 返回 {k: [v, ...]}"""
    q = {}
    for k, v in request.query_params.multi_items():
        q.setdefault(k, []).append(v)
    return q


def client_ip(request: Request) -> str:
    """真实客户端 IP: Nginx 反代后优先取 X-Real-IP / X-Forwarded-For 第一段"""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("X-Real-IP")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"


def get_uid(request: Request) -> int:
    """鉴权依赖: query token 或 Authorization Bearer, 无效抛 401;
    账号已过期(且非管理员)抛 403"""
    token = (qs(request).get("token") or [""])[0]
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    uid = security.valid_token(token)
    if uid is None:
        log.warning("未登录访问 %s ip=%s", request.url.path, client_ip(request))
        raise HTTPException(status_code=401, detail={"ok": False, "msg": "未登录或登录已过期"})
    # 到期拦截: 普通用户过期即禁入; 管理员豁免(保证管理续费入口可用)
    u = users.find_user_by_id(uid)
    if u and not u.get("is_admin") and users.is_expired(uid):
        log.warning("过期账号访问 %s uid=%s ip=%s", request.url.path, uid, client_ip(request))
        raise HTTPException(status_code=403, detail={"ok": False, "msg": "账号已过期，请联系管理员续费"})
    return uid
