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


def _record_kicked(uid, request):
    """记录一次「账号被另一设备顶出」(2026-09-22 v4.11.35).

    ★ 为什么要去重: 被顶出的设备会**持续重试**(前端在轮询, 每个请求都撞 401 kicked),
      若每次都落库, 一次换设备能写出几十上百条重复记录。这里用 CacheStore 的 setnx
      做个 10 分钟窗口, 让「一次顶出事件」只留一条记录。
    ★ 失败一律吞掉: 埋点不能影响鉴权主路径。
    """
    try:
        from ..services import activity
        from ..services.cache_store import store
        if not store.setnx("login:kicked:%s" % uid, 1, ttl=600):
            return
        activity.record_login(uid=uid, result="kicked", ip=client_ip(request),
                              ua=request.headers.get("User-Agent") or "")
    except Exception as e:
        log.warning("被顶出记录失败 uid=%s err=%s", uid, e)


def get_uid(request: Request) -> int:
    """鉴权依赖: query token 或 Authorization Bearer, 无效抛 401;
    账号已过期(且非管理员)抛 403"""
    token = (qs(request).get("token") or [""])[0]
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    status, uid = security._token_status(token)
    if status == "revoked":
        # 被另一设备登录顶出: 明确告知(前端弹"账号已在另一设备登录")
        log.warning("账号被顶出访问 %s uid=%s ip=%s", request.url.path, uid, client_ip(request))
        _record_kicked(uid, request)
        raise HTTPException(status_code=401, detail={"ok": False, "code": "kicked",
                                                     "msg": "账号已在另一设备登录，本设备已退出"})
    if status != "ok":
        log.warning("未登录访问 %s ip=%s", request.url.path, client_ip(request))
        raise HTTPException(status_code=401, detail={"ok": False, "code": "expired",
                                                     "msg": "未登录或登录已过期"})
    # 到期拦截: 普通用户过期即禁入; 管理员豁免(保证管理续费入口可用)
    u = users.find_user_by_id(uid)
    if u and not u.get("is_admin") and users.is_expired(uid):
        log.warning("过期账号访问 %s uid=%s ip=%s", request.url.path, uid, client_ip(request))
        raise HTTPException(status_code=403, detail={"ok": False, "msg": "账号已过期，请联系管理员续费"})
    return uid


def require_vip_or_paid(request: Request) -> int:
    """鉴权依赖 + 竞价异动 VIP/付费门禁(2026-08-17 主人需求):
    管理员 + VIP(member_level=2) + 付费会员(member_level=1) 可用;
    免费试用(member_level=0) 返 403。
    路由示例: def api_xxx(uid: int = Depends(require_vip_or_paid))"""
    uid = get_uid(request)
    u = users.find_user_by_id(uid)
    if u and (u.get("is_admin") or int(u.get("member_level") or 0) >= 1):
        return uid
    log.warning("竞价异动门禁拦截 uid=%s ip=%s member_level=%s",
                uid, client_ip(request), (u or {}).get("member_level"))
    raise HTTPException(status_code=403, detail={"ok": False, "code": "vip_required",
                                                 "msg": "竞价异动仅限 VIP/付费会员，请升级后使用"})


def quota_guard(feature):
    """免费用户每日配额门禁工厂(2026-09-21, 方案 B).
    用法: def api_xxx(uid: int = Depends(quota_guard("picker")))

    行为: 会员/管理员直接放行(不计数); 免费用户消耗 1 次, 超额返 429 且带
    code=quota_exceeded + 剩余/额度信息, 前端据此弹「开通会员」引导。
    去重窗口内(默认 10s)的重复请求视为同一次, 避免前端并发加载瞬间烧完额度。"""
    from ..services import quota as quota_svc

    def _guard(request: Request) -> int:
        uid = get_uid(request)
        ok, info = quota_svc.consume(uid, feature)
        if not ok:
            log.warning("配额耗尽拦截 uid=%s feature=%s used=%s limit=%s ip=%s",
                        uid, feature, info.get("used"), info.get("limit"),
                        client_ip(request))
            raise HTTPException(status_code=429, detail={
                "ok": False,
                "code": "quota_exceeded",
                "feature": feature,
                "feature_label": quota_svc.FEATURE_LABEL.get(feature, feature),
                "limit": info.get("limit"),
                "used": info.get("used"),
                "msg": "今日%s次数已用完（%s/%s），开通会员可不限次数，或明日再来" % (
                    quota_svc.FEATURE_LABEL.get(feature, feature),
                    info.get("used"), info.get("limit")),
            })
        return uid

    return _guard

