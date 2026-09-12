# -*- coding: utf-8 -*-
"""
前端本地筛选快照路由 (P3, 2026-09-12)
=================================================================================
GET /api/picker/snapshot —— 一次性下发当日**全市场预计算评分**(物化表 stock_score_daily),
前端据此在浏览器内完成筛选(改条件秒出, 零网络往返), 实时价仍走 /api/quotes 按需补。

为什么可以这么做:
  评分 5 因子的输入全部在 9:25 定格(P1 已论证), 竞价结束后每只票的评分/可信度都已
  算好; 用户改筛选条件只是对**同一份结果**换门槛, 不需要再跑取数/评分。

安全与一致性边界(四条, 缺一不可):
  1. **开关**: settings.frontend_local_filter 默认 0。关闭时接口只回 enabled=false,
     前端静默回退原筛选路径 —— 天然灰度, 不需要前后端同时上线。
  2. **门禁**: require_vip_or_paid(与竞价异动同级)。免费账号拿不到全市场数据,
     回退后端筛选, 行为不变。
  3. **行数闸门**: 物化表不完整(< MIN_ROWS, 如 9/11 熔断日只落 132 行)→ 视为不可用,
     绝不发半张表(前端据此回退, 不会出现"半市场名单")。
  4. **同口径**: 本地筛选的过滤规则由前端 utils/filters.js.pickFromSnapshot 逐条复刻
     picker.filter 的 coarse_filter + apply_filters(含"竞额降序取前 120"截断),
     两侧各有一份对拍测试; 抢筹标复用 pipeline.qc_fields, 避免本地/后端分叉。

下发字段只含"筛选与展示必需的定格值", 不含任何因子权重/算法 —— 即接口暴露的是
当天的结论本身(用户本来就能通过反复调参枚举出来), 不是策略。
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import kpl
from ..services import settings as st
from ..services.picker import pipeline as pl
from ..services.picker import precompute
from .deps import jr, qs, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()

SWITCH = "frontend_local_filter"
"""开关(settings 表, 管理员可改): 1 = 允许下发全市场快照; 0 = 关闭(默认)"""

CACHE_TTL = 60
"""响应缓存秒数: 全市场 ~5500 行序列化约 0.5MB, 同一天内所有用户共享一份,
避免每次打开首页都查库+序列化。TTL 取 60s 是"物化表当天不变"与"管理员重跑预计算
后能自愈"之间的折中。"""

_cache = {}      # {date: (ts, payload)}  —— 进程内, 与 cache_store 无关(这份是只读快照)


def enabled() -> bool:
    try:
        return bool(st.get(SWITCH, 0))
    except Exception:                                             # noqa: BLE001
        return False


def _cached(date: str) -> Optional[dict]:
    item = _cache.get(date)
    if not item:
        return None
    ts, payload = item
    if time.time() - ts > CACHE_TTL:
        _cache.pop(date, None)
        return None
    return payload


def _store(date: str, payload: dict) -> None:
    # 只保留最近若干天(回看用), 防进程内无界增长
    if len(_cache) > 8:
        _cache.clear()
    _cache[date] = (time.time(), payload)


@router.get("/api/picker/snapshot")
def api_picker_snapshot(request: Request, uid: int = Depends(require_vip_or_paid)):
    """当日全市场预计算评分快照(前端本地筛选用)。

    返回:
      {"ok": true, "enabled": false, "msg": "...", "list": []}          —— 未开启/不可用
      {"ok": true, "enabled": true, "date": "YYYY-MM-DD",
       "count": 5557, "list": [...], "ts": 1234567890}
    """
    if not enabled():
        return jr({"ok": True, "enabled": False, "msg": "本地筛选未开启", "list": [], "count": 0})
    q = qs(request)
    date = ((q.get("date") or [""])[0] or "").strip() or None
    from ..services.picker.mode import bj_date
    date = date or bj_date()

    hit = _cached(date)
    if hit is not None:
        return jr(hit)

    try:
        rows = precompute.read_snapshot_rows(date)
    except Exception as e:                                        # noqa: BLE001
        log.warning("快照接口读取失败 uid=%s date=%s err=%s", uid, date, e)
        rows = []
    if not rows:
        # 物化表缺失/不完整 → 前端回退原筛选路径(不报错: 这不是故障而是未就绪)
        log.info("快照不可用(物化表缺失/行数不足) uid=%s date=%s", uid, date)
        return jr({"ok": True, "enabled": False, "date": date,
                   "msg": "物化表不可用", "list": [], "count": 0})

    # 抢筹标: 与 pipeline 输出同口径(复用 pipeline.qc_fields, 避免本地/后端分叉)
    detail = {}
    try:
        detail = kpl.get_qiangchou_detail(date) or {}
    except Exception as e:                                        # noqa: BLE001
        log.warning("快照抢筹明细加载失败(本批无抢筹标) date=%s err=%s", date, e)
    for r in rows:
        r.update(pl.qc_fields(r.get("code") or "", detail))
        r["degraded"] = False

    payload = {"ok": True, "enabled": True, "date": date,
               "count": len(rows), "list": rows, "ts": int(time.time())}
    _store(date, payload)
    log.info("快照下发 uid=%s date=%s 行数=%d 抢筹标=%d", uid, date, len(rows), len(detail))
    return jr(payload)
