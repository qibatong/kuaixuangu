# -*- coding: utf-8 -*-
"""
盘中实时选股(spot)路由 —— 独立端点 /api/stocks_spot
===================================================================
2026-09-28 重建。原 spot 挂在 /api/stocks?strategy=spot, 2026-09-09 随
提交 4c56083 下线(前端无入口 / 后端显式 400)。

**为什么新开端点而不是复活 strategy=spot 参数**:
  /api/stocks 已长到 71K, 内含竞价专用闸门(pick_window_guard)、当日幂等、
  直读批次、冻结字段等一整套逻辑 —— 那些**只对竞价成立**。把 spot 塞回同一
  路由会: (a) 每个分支都要重判"这逻辑对 spot 适用吗", 极易漏判; (b) 动大文件
  的行尾/缩进风险高。独立端点 = 独立语义 + 独立可回滚(不挂载即下线)。

与其他链路的关系:
  · 数据源: 复用 fetcher.ensure_spot_cache(全市场实时行情, SPOT_CACHE_TTL)
    + fetcher.fetch_zt_pool(涨停池封单, 供封单强度因子)
  · 评分:  picker.score_spot.compute_score_spot(六因子, 见该模块文档)
  · 过滤:  picker.filter.apply_spot_filters(消费 chgGt/volRatioFloor/turnoverFloor
    /turnoverGt/spotExcludeZT 等盘中参数)
  · 与竞价 pipeline 完全隔离 —— 不落批次、不推送、不参与定格。

调用: GET /api/stocks_spot?action=filter|ping&<筛选参数...>
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import fetcher, scorer
from ..services.picker.contract import QuoteRow
from ..services.picker.filter import apply_spot_filters, FilterContext
from ..services.picker.score import ScoredRow
from ..services.picker.score_spot import compute_score_spot, SpotScoreResult
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()

# 盘中最少需要的实时字段: 涨幅/量比/换手缺一不可(缺则评分会走 default 分, 名单不可信)
_REQUIRED_SPOT_FIELDS = ("real_change",)


def _spot_rows_from_raw(raw, f, zt_map, yesterday_map):
    """东财/腾讯 diff 行 → list[(QuoteRow, SpotScoreResult, zt_info)]。

    单位与字段映射全走 contract.QuoteRow.from_eastmoney, 不在此重复口径。
    """
    out = []
    for s in raw:
        code = str(s.get("f12") or "")
        if not code:
            continue
        try:
            row = QuoteRow.from_eastmoney(s, auction_window=False,
                                          yesterday_chg=(yesterday_map or {}).get(code))
        except Exception:                                  # noqa: BLE001
            continue
        zt = (zt_map or {}).get(code)
        out.append((row, zt))
    return out


def _spot_payload(row, sc, zt_info, f):
    """单票输出(前端字段与竞价 list item 对齐, 缺失透 None 不填 0)。"""
    return {
        "code": row.code,
        "name": row.name,
        "probability": sc.probability,
        "confidence": sc.confidence,
        "realChange": row.real_change,
        "entityChange": row.entity_change,
        "volRatio": row.vol_ratio,
        "turnover": row.turnover,
        "sealRatio": sc.seal_ratio,
        "sealFund": sc.seal_fund,
        "limitBoards": sc.limit_boards,
        "breakCount": sc.break_count,
        "circulationMV": None if not row.mv else round(row.mv_yi or 0.0, 4),
        "freeCirculationMV": None if not row.free_mv else round(row.free_mv / 1e8, 4),
        "price": row.price,
        "amount": None if row.amount is None else round(row.amount / 1e8, 4),
        "bidChange": row.bid_change,
        "bidAmt": None if row.bid_amt is None else round(row.bid_amt / 1e4, 2),
        "industry": row.industry or "-",
        "concept": row.concept or "-",
        "source": row.source,
        "degraded": row.degraded,
    }


@router.get("/api/stocks_spot")
def api_stocks_spot(request: Request, uid: int = Depends(get_uid)):
    """盘中实时选股。

    action=ping   探测(返回可否用, 不受交易时段限制)
    action=filter **盘中实时出名单**(默认)。与竞价不同: 随时可调, 无 9:26 定格闸门
                  —— 盘中选股本就是"看当下", 不该被竞价窗口逻辑拦。
    """
    q = qs(request)
    action = (q.get("action") or ["filter"])[0]
    if action not in ("filter", "ping"):
        log.warning("spot 非法参数 action=%s uid=%s", action, uid)
        return jr({"ok": False, "msg": "非法参数(仅支持 filter/ping)"}, 400)
    if action == "ping":
        return jr({"ok": True, "strategy": "spot", "available": True})

    f = scorer.validate_filters(q)
    fs = scorer.market_fs(f["markets"])
    _, _, before930 = scorer.bj_now()
    t0 = time.time()

    try:
        # 1) 全市场实时行情(东财 diff; 失败沿用旧缓存, 无缓存则抛错如实报)
        raw, err = fetcher.ensure_spot_cache("refresh", fs, before930)
        if err:
            log.warning("spot 选股被拒 uid=%s err=%s", uid, err)
            return jr({"ok": False, "msg": "行情获取失败: %s" % err, "list": [], "count": 0})

        # 2) 涨停池(封单强度因子; 失败返回 {} → 封单因子降级为 0, 不阻塞)
        zt_map = {}
        try:
            zt_map = fetcher.fetch_zt_pool() or {}
        except Exception as e:                             # noqa: BLE001
            log.warning("spot 涨停池获取失败(封单因子降级) err=%s", str(e)[:120])

        # 3) 评分
        pairs = _spot_rows_from_raw(raw, f, zt_map, None)
        scored = []
        for row, zt in pairs:
            sc = compute_score_spot(row, zt)
            scored.append(ScoredRow(row=row, score=sc))
        scored.sort(key=lambda x: (-x.score.probability, x.row.code))

        # 4) 过滤(注入 _spot_zt 供 spotExcludeZT 判定: 涨停池有该 code 即视为封板)
        for it in scored:
            zt = zt_map.get(it.row.code)
            lb = int((zt or {}).get("lb") or 0)
            try:
                setattr(it.row, "_spot_zt", lb > 0)
            except Exception:                              # noqa: BLE001
                pass
        ctx = FilterContext(markets=f["markets"], zt_codes=None,
                            require_bid_change=False, price_gate="realtime")
        outcome = apply_spot_filters(scored, f, ctx)

        lst = [_spot_payload(it.row, it.score, zt_map.get(it.row.code), f)
               for it in outcome.kept]
        log.info("spot 选股 uid=%s 全市场%d→入选%d 剔除=%s 耗时%.0fms",
                 uid, len(scored), len(lst), dict(outcome.stats),
                 (time.time() - t0) * 1000)
        return jr({
            "ok": True, "strategy": "spot", "mode": "spot",
            "list": lst, "count": len(lst),
            "before930": before930,
            "dataTime": int(time.time()),
            "degraded": False,
            "stats": dict(outcome.stats),
        })
    except Exception as e:                                 # noqa: BLE001
        log.error("spot 选股处理失败 uid=%s err=%s", uid, e, exc_info=True)
        return jr({"ok": False, "msg": "选股失败", "list": [], "count": 0})
