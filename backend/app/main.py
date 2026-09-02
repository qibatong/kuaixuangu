# -*- coding: utf-8 -*-
"""
快选 · 后端入口 (FastAPI / Python 3.11)
===========================================
与旧 server.py (http.server) API 完全兼容, 按标准分层组织:
    app/core     配置 / 日志
    app/db       数据库
    app/services 业务逻辑(抓取/评分/用户/历史/安全)
    app/api      路由

启动:
    uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
"""
import time

from fastapi import FastAPI, Request

from .api import (admin, aipick, auth, health, history, invite, kpl, ladder,
                  prefs, sms, stats, stock_temper, stocks, summary)
from .api.deps import client_ip, jr
from .core import logger as app_logger
from .db import database
from .services import security

log = app_logger.get_logger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# ---------- 限流 + 访问日志中间件: 每 IP 每分钟 N 次, 全部请求落日志 ----------
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = client_ip(request)
    start = time.time()
    # 轻量解析当前用户(便于日志定位; 无效 token 记为 guest)
    uid = None
    token = request.query_params.get("token") or ""
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    if token:
        uid = security.valid_token(token)

    if not security.rate_allow(ip):
        cost = (time.time() - start) * 1000
        log.warning("限流拦截 ip=%s %s %s uid=%s 429 %.0fms",
                    ip, request.method, request.url.path, uid or "-", cost)
        return jr({"ok": False, "msg": "请求过于频繁, 请稍后再试"}, 429)

    response = await call_next(request)
    cost = (time.time() - start) * 1000
    # 只记录 API 与页面请求, 不记录静态资源(避免刷屏)
    path = request.url.path
    log.info("%s %s %s uid=%s %d %.0fms",
             ip, request.method, path, uid or "-", response.status_code, cost)
    return response


# ---------- 注册路由 ----------
app.include_router(auth.router)
app.include_router(stocks.router)
app.include_router(history.router)
app.include_router(invite.router)
app.include_router(prefs.router)
app.include_router(health.router)
app.include_router(stats.router)
app.include_router(admin.router)
app.include_router(kpl.router)
app.include_router(ladder.router)
app.include_router(stock_temper.router)
app.include_router(sms.router)
app.include_router(aipick.router)
app.include_router(summary.router)


@app.on_event("startup")
def on_startup():
    app_logger.setup_logging()
    log.info("=== 服务启动 ===")
    database.init_db()
    log.info("数据库就绪: %s", database.config.DB_FILE)
    # 注意: 快照采集/尾盘推送调度已拆分到独立进程 app/worker.py (kx-worker.service)
    # web 进程只处理 API 请求: 避免 9:25 高峰采集与请求抢资源, web 重启不影响采集
    # (Phase1 2026-08-16)
    # 昨比预热必须挂 web 进程: 昨比缓存是 web 进程级(fetcher._yesterday_cache),
    # kx-worker 独立进程缓存不共享。交易日 9:05 分批预热, 盘中请求直接命中缓存。
    # (2026-09-02 生产事故后新增; --workers 2 下两 worker 各自预热, 早盘前压力小)
    try:
        from .services import yday_prewarm
        yday_prewarm.start_prewarm_scheduler()
    except Exception as e:
        log.warning("昨比预热调度启动失败(不影响主服务) err=%s", e)
