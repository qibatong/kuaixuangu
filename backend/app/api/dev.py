# -*- coding: utf-8 -*-
"""异动 / 停牌风险路由（《快选异动停牌风险功能工单》§四）

    GET  /api/dev/risk?code=605058   单只个股全量（三条偏离线 + 明日触发空间 + 未来十日投影）
    GET  /api/dev/tomorrow           明日预警名单（读盘后批量结果 dev_risk_daily）
    GET  /api/dev/today              今日已触发名单（同上，筛「触发」）
    GET  /api/dev/status             体检：结果表落在哪一天、多少行、各状态分布
    POST /api/dev/scan               手动触发盘后批处理（管理员；正常由 kx-worker 15:45 自动跑）

口径与阈值见 `services/dev_risk.py` 模块头（★ 交易「区间首尾相减」原文口径，
**不是**工单正文写的逐日累加；阈值按现行规则修正了工单的 4 处不符）。
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import dev_risk
# ★ get_admin 定义在 api/admin.py:52（不是 deps.py —— deps 只有 get_uid）；
#   admin.py 不反向依赖本模块，import 无环。
from .admin import get_admin
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()

# 全市场扫描互斥：两个 worker 进程同时点到也不会并发打满上游（跨进程用 CacheStore）
_SCAN_KEY = "dev_risk:scan"


def _next_trade_date(d):
    """`d`(YYYY-MM-DD) 的**下一个交易日**；算不出返回 None。

    交易日历只有 `prev_trade_date`，故这里逐日前进探测（最长 15 天足够跨过春节长假）；
    `trade_calendar.is_trade_day` 对区间外日期是 fail-open（视作交易日），与本项目既有降级一致。
    """
    if not d:
        return None
    from datetime import datetime, timedelta
    try:
        cur = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except Exception:                                          # noqa: BLE001
        return None
    from ..core import trade_calendar
    for _ in range(15):
        cur += timedelta(days=1)
        if trade_calendar.is_trade_day(cur):
            return cur.strftime("%Y-%m-%d")
    return None


def _fill_lb_for(rows, result_date):
    """给异动名单补「连板高度标签」（2026-09-28 主人要求：异动板块也要有「昨首板」）。

    ★ 基准日 = 结果日的**下一个交易日** —— 本名单是**盘后为"明日"生成**的，故「买入前一日」
      正是结果日自己（今天），与选股名单「买入前一日」的语义逐字对应。
    ★ 复用竞价那份唯一权威实现 `stocks._fill_lb`（局部 import 防模块级循环依赖）；
      取数失败/空池时它自己会留空 ⇒ 前端不渲染胶囊，**绝不影响名单**。
    """
    if not rows:
        return
    nxt = _next_trade_date(result_date)
    if not nxt:
        log.info("异动名单连板标签跳过：算不出结果日的下一交易日 date=%s", result_date)
        return
    try:
        from .stocks import _fill_lb
        _fill_lb(rows, ref_date=nxt)
    except Exception as e:                                     # noqa: BLE001
        log.warning("异动名单连板标签填充失败(名单照常返回, 标签留空) err=%s", e)


@router.get("/api/dev/risk")
def api_dev_risk(request: Request, uid: int = Depends(get_uid)):
    """个股异动风险（异动计算器 tab）。

    ★ 实时重算（读猫爪日K + 落库指数），不读结果表 —— 用户查任意一只票都要能出数，
    不能依赖它当天是否在盘后批量名单里。
    """
    q = qs(request)
    code = ((q.get("code") or [""])[0] or "").strip()
    if not code:
        return jr({"ok": False, "reason": "missing_code", "msg": "缺少 code 参数"}, status=400)
    if len(code) != 6 or not code.isdigit():
        return jr({"ok": False, "reason": "bad_code", "msg": "code 必须是 6 位数字"}, status=400)
    t0 = time.time()
    res = dev_risk.compute(code)
    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    if not res.get("ok"):
        # 200 + ok=false：前端要能区分「票算不出」与「接口挂了」
        return jr(res)
    return jr(res)


@router.get("/api/dev/tomorrow")
def api_dev_tomorrow(request: Request, uid: int = Depends(get_uid)):
    """明日预警：按「明日涨停就会触发」优先，其次「临近」。

    ?all=1 时返回全部有标签的票；默认只返回 red/yellow。
    ?board=main|gem|star|bse 只取某板块。
    """
    q = qs(request)
    want_all = (q.get("all") or ["0"])[0] in ("1", "true", "yes")
    board = (q.get("board") or [""])[0].strip()
    d = dev_risk.load_daily()
    rows = d.get("rows") or []
    out = []
    for r in rows:
        lvl = (r.get("warn_level") or "").strip()
        if not want_all and lvl not in ("red", "yellow"):
            continue
        if board and r.get("board_key") != board:
            continue
        out.append(r)
    # 排序：red 先于 yellow；同色按「距触发还差多少」升序（越接近触发越前）
    rank = {"red": 0, "yellow": 1, "": 2}

    def _key(r):
        ntp = r.get("next_trigger_pct")
        ntp = 999.0 if ntp is None else float(ntp)
        return (rank.get((r.get("warn_level") or "").strip(), 2), ntp)

    out.sort(key=_key)
    _fill_lb_for(out, d.get("date"))
    return jr({"ok": True, "date": d.get("date"), "count": len(out),
               "scanned": len(rows), "list": out})


@router.get("/api/dev/today")
def api_dev_today(request: Request, uid: int = Depends(get_uid)):
    """今日已触发（任一窗口 status=触发）。"""
    q = qs(request)
    board = (q.get("board") or [""])[0].strip()
    d = dev_risk.load_daily()
    out = []
    for r in d.get("rows") or []:
        if board and r.get("board_key") != board:
            continue
        st = [r.get("d3_status"), r.get("d10_status"), r.get("d30_status")]
        if any(s == "触发" for s in st):
            out.append(r)
    # 已触发的排在「偏离最大」前：用 d3/d10/d30 中占阈值比最高者
    def _key(r):
        best = 0.0
        for k, t in (("d3", 20.0), ("d10", 100.0), ("d30", 200.0)):
            v = r.get(k)
            if v is None:
                continue
            try:
                best = max(best, abs(float(v)) / t)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        return -best

    out.sort(key=_key)
    return jr({"ok": True, "date": d.get("date"), "count": len(out), "list": out})


@router.get("/api/dev/status")
def api_dev_status(request: Request, uid: int = Depends(get_uid)):
    """体检：结果表覆盖情况（排障用；不做任何写操作）。"""
    d = dev_risk.load_daily()
    rows = d.get("rows") or []
    dist = {}
    for r in rows:
        lvl = (r.get("warn_level") or "").strip() or "-"
        dist[lvl] = dist.get(lvl, 0) + 1
    boards = {}
    for r in rows:
        b = (r.get("board_key") or "-")
        boards[b] = boards.get(b, 0) + 1
    idx_ok = {}
    for code in ("000002", "399107", "399102", "000688", "899050"):
        s = dev_risk.index_series(code)
        idx_ok[code] = {"bars": len(s), "last": s[-1][0] if s else None,
                        "close": s[-1][1] if s else None}
    return jr({"ok": True, "date": d.get("date"), "rows": len(rows),
               "warn_dist": dist, "board_dist": boards, "index": idx_ok})


@router.post("/api/dev/scan")
def api_dev_scan(request: Request, uid: int = Depends(get_admin)):
    """手动触发全市场批处理（管理员）。正常由 kx-worker 交易日 15:45 自动跑。"""
    q = qs(request)
    date = (q.get("date") or [""])[0].strip()
    if not dev_risk.acquire_scan_lock():
        return jr({"ok": False, "reason": "busy", "msg": "已有扫描在进行中"})
    try:
        stat = dev_risk.run_scan_job(date=date or None)
    finally:
        dev_risk.release_scan_lock()
    return jr({"ok": bool(stat.get("ok")), **stat})
