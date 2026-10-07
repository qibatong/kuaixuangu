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

from .api import (activity, admin, aipick, auth, chaozhi, dev, health, his_pick, history, invite, kpl, ladder,
                  member, news, notices, pick, picker, prefs, push, sms, stats, stock_temper, stocks,
                  stocks_spot, summary, yijiner)
from .api.deps import client_ip, jr
from .core import logger as app_logger
from .db import database
from .services import security

log = app_logger.get_logger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# 同步 def 路由的线程池容量(2026-09-10 事故缓解, 见下方 _enlarge_thread_pool)
# 同步 def 路由由 FastAPI 放进 anyio 线程池执行, 默认仅 40 槽/worker。竞价结束集中
# 刷新时, 30s 级慢选股把 2 worker 的 80 个槽全占满 → 登录/health/prefs 只能在队列里
# 排队到超时(实测登录排队 2,321,182ms ≈ 38 分钟)。扩容降低排队概率(线程按需创建)。
# 真正的根治: ①fetcher 分页快速失败(慢请求 30s → ~1s) ②登录/验证/探活走独立 executor。
_WORKER_THREAD_TOKENS = 120




# ---------- 会员配置按需重载(多 worker 一致性, 2026-09-30) ----------
def _sync_member_conf():
    """后台改的会员配置原先只 apply 到当前 worker(线上 --workers 2), 另一半要等重启;
    这里借全局中间件在请求前做一次**带 TTL 节流**的指纹比对(见 settings.CFG_TTL),
    变了才重载 ⇒ 所有 worker 最迟 CFG_TTL 秒一致。
    成本 = 每 worker 每 CFG_TTL 秒 1 次主键查询; 异常一律吞掉, 绝不能影响请求。"""
    try:
        from .api import admin as _admin
        _admin.ensure_member_conf_fresh()
    except Exception:
        pass


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

    if not security.rate_allow(ip, uid):
        cost = (time.time() - start) * 1000
        log.warning("限流拦截 ip=%s %s %s uid=%s 429 %.0fms",
                    ip, request.method, request.url.path, uid or "-", cost)
        return jr({"ok": False, "msg": "请求过于频繁, 请稍后再试"}, 429)

    # 会员配置同步放限流之后: 被 429 拦掉的请求不必做同步
    _sync_member_conf()

    response = await call_next(request)
    cost = (time.time() - start) * 1000
    # 只记录 API 与页面请求, 不记录静态资源(避免刷屏)
    path = request.url.path
    log.info("%s %s %s uid=%s %d %.0fms",
             ip, request.method, path, uid or "-", response.status_code, cost)
    return response


# ---------- 注册路由 ----------
app.include_router(auth.router)
app.include_router(chaozhi.router)
app.include_router(stocks.router)
app.include_router(history.router)
app.include_router(invite.router)
app.include_router(prefs.router)
app.include_router(health.router)
# 2026-10-03：《顺势而为竞价终极版》选股逻辑的服务端版（独立接口，不消耗配额）
app.include_router(his_pick.router)
app.include_router(stats.router)
app.include_router(admin.router)
app.include_router(kpl.router)
app.include_router(ladder.router)
app.include_router(stock_temper.router)
app.include_router(sms.router)
app.include_router(aipick.router)
app.include_router(summary.router)
app.include_router(picker.router)   # P3(2026-09-12): 前端本地筛选快照
app.include_router(member.router)   # 2026-09-21: 会员中心(总览/配额/签到)
app.include_router(activity.router)  # 2026-09-22: 用户行为上报(功能使用计数)
app.include_router(news.router)      # 2026-09-27 v4.11.59: 盘前资讯(猫爪 news + 开盘啦 doc95/96/97/99)
app.include_router(pick.router)      # 2026-10-03: pick_daily（可买性分级/卖出建议/回填结果）
app.include_router(dev.router)       # 2026-09-27 v4.11.64: 异动/停牌风险(/api/dev/*)
app.include_router(notices.router)    # 2026-10-04: 站内消息(站方广播 + 会员到期等账户事件)
app.include_router(push.router)       # 2026-10-04: WebPush 订阅(手机端真推送, 需 https)
app.include_router(stocks_spot.router)  # 2026-09-28: 盘中实时选股(/api/stocks_spot)
app.include_router(yijiner.router)   # 2026-09-28: 竞价一进二(/api/yijiner, 严格 VIP 门禁)


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
    # 🔴 2026-10-07 v8: AI 概率图预热线程**已随 AI 层一起删除**(主人拍板)。
    #   原先它存在的理由: `/api/stock/detail` 与竞价选股主链路算评分时会触发
    #   `bid_strength._fill_ai` → `ai_predict._market_prob_map` 的**全市场**推理
    #   (全市场取数 + 猫爪补字段出网 + pandas + 5561 只 predict_proba, 冷启动实测
    #   4.417~5.1s), 故用后台线程把进程内按日缓存保热。
    #   而线上 `w_ai` 早已被置 0 ⇒ 那次推理的结果**乘 0 丢弃**, 属纯浪费; 现连同
    #   `_fill_ai` / `ai_predict.py` 一并移除 ⇒ 该 4.4s 开销从**竞价选股主链路**上消失。
    #   ⚠️ 不影响 aipick 名单页(金睛/火眼)与超智页机会清单 —— 它们读
    #   `predictions_*.json` 与 `aipick.db`, 与评分链路无关。
    # 超智聚合预热(2026-10-06): /api/chaozhi/overview 的 _build() 是 9 个串行环节, 冷算 1.8~3.9s,
    # 而缓存 TTL 仅 60s ⇒ 每个周期后的首个请求都要重算, 生产实测 66 次里 49 次 >2s(74%)。
    # 后台每 30s 走一次带缓存的入口 ⇒ 用户请求恒命中。
    try:
        from .services import chaozhi as _czh
        _czh.start_overview_prewarm()
    except Exception as e:
        log.warning("超智聚合预热启动失败(不影响主服务) err=%s", e)
    # spotMap 预热(2026-09-04): 9:30 后 refresh 直读需全市场行情覆盖, 缓存 60s TTL 到期时
    # 请求内同步拉全市场会出秒级长尾(实测 4.6s) → 交易时段后台每 40s 预刷, 请求永远命中。
    # 必须挂 web 进程: spotMap 缓存是 web 进程级(fetcher._quote_map_cache)。
    try:
        from .services import fetcher
        fetcher.start_spot_prewarm()
    except Exception as e:
        log.warning("spotMap预热启动失败(不影响主服务) err=%s", e)
    # KPL 首屏接口预热(2026-09-04): 竞价异动首页 loadAll 并发请求 yidong/sentiment/
    # bid-seal 等 KPL key, TTL 到期后同刻全 miss → 各 loader 抢 sem(3) 排队 → 首屏
    # 1.7-2.0s。交易时段后台每 12s 预拉首屏 key 写缓存, 用户请求恒命中(<50ms)。
    # 缓存走 kv_cache(sqlite/redis 跨进程共享), 两 worker 重复预热无害(single-flight 兜底)。
    try:
        from .services import kpl as _kpl
        _kpl.start_kpl_prewarm()
    except Exception as e:
        log.warning("KPL首屏预热启动失败(不影响主服务) err=%s", e)
    # KPL 回看日预热(2026-09-28): 首屏预热只覆盖"盘中实时"那批 key, 不覆盖回看日
    # (date=)。而回看日的「竞价抢筹」冷启动实测 4.8~7.4s(要打 4 个全市场 5000+ 行
    # 的猫爪接口), 生产日活仅 ~8.7 人 ⇒ 回看日缓存几乎总是冷的 ⇒ 用户每次打开回看
    # 日都要等。本线程把最近 N 个交易日的回看结果保持热(20min 一轮, 串行+间隔)。
    # 同样必须挂 web 进程: 接口层 fetcher._quote_map_cache 是进程级的。
    try:
        from .services import kpl as _kpl_replay
        _kpl_replay.start_kpl_replay_prewarm()
        # 2026-10-01 P1-5: 开盘啦出网埋点汇总线程(供 10-08 竞价窗口量化; KX_KPL_OUTBOUND_STAT=0 可关)
        try:
            from .services import kpl as _kpl_stat_mod
            _kpl_stat_mod.start_kpl_stat()
        except Exception as _e:                                # noqa: BLE001
            log.warning("KPL 出网埋点线程启动失败: %s", _e)
    except Exception as e:
        log.warning("KPL回看预热启动失败(不影响主服务) err=%s", e)
    # 会员权益配置回灌(2026-09-21): 后台改的会员配置存 settings 表, 重启后必须重新
    # 写回运行时 config, 否则新开进程又用回环境变量默认值 —— 表现就是"后台改了但重启就还原"
    try:
        from .api import admin as _admin
        _conf = _admin.get_member_conf()
        _admin.apply_member_conf(_conf)
        log.info("会员配置已加载: %s", _conf)
    except Exception as e:
        log.warning("会员配置加载失败(用环境变量默认值) err=%s", e)


@app.on_event("startup")
async def _enlarge_thread_pool():
    """扩容 anyio 线程池(同步 def 路由的执行池, 默认 40 槽/worker)。

    两个坑, 都已避开:
    1) 必须在 async 上下文调用 —— anyio 靠 sniffio 识别当前后端, 模块导入期/同步
       startup 里调用会抛 'Not currently running on any asynchronous event loop',
       被 try 吞掉后静默不扩容(第一版就栽在这);
    2) 必须排在 on_startup 之后注册 —— startup handler 按注册顺序执行, 排前面时
       setup_logging() 还没跑, 日志打不出来, 无从确认是否生效。"""
    try:
        import anyio.to_thread
        limiter = anyio.to_thread.current_default_thread_limiter()
        old = limiter.total_tokens
        limiter.total_tokens = _WORKER_THREAD_TOKENS
        log.info("anyio 线程池扩容: %s → %s 槽/worker", old, _WORKER_THREAD_TOKENS)
    except Exception as e:      # 扩容失败绝不能影响启动
        log.warning("anyio 线程池扩容失败(服务照常启动): %s", e)
