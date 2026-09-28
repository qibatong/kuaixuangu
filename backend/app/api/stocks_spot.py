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
from ..services import auction_snapshot, fetcher, scorer
from ..services.picker.contract import QuoteRow
from ..services.picker.filter import apply_spot_filters, FilterContext
from ..services.picker.score import ScoredRow
from ..services.picker.score_spot import compute_score_spot, SpotScoreResult
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

router = APIRouter()

# 盘中最少需要的实时字段: 涨幅/量比/换手缺一不可(缺则评分会走 default 分, 名单不可信)
_REQUIRED_SPOT_FIELDS = ("real_change",)


def _bj_date():
    """今天(北京时间 YYYY-MM-DD) —— 服务器时区无关(显式 +8h), 与本文件 bj_now 同源口径。"""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def _spot_rows_from_raw(raw, f, zt_map, yesterday_map, bid_chg_map=None, bid_amt_map=None):
    """东财/腾讯 diff 行 → list[(QuoteRow, zt_info)]。

    单位与字段映射全走 contract.QuoteRow.from_eastmoney, 不在此重复口径。

    🔴 2026-09-28 v4.11.78 修复「竞涨/竞额无数据」:
      前端 spot 名单新增了「竞涨 / 竞额」两列, 但首版**值恒为 null** —— 根因是
      `from_eastmoney(auction_window=False)` 的竞价字段取值有三条来源
      (定格 map 优先 → 窗口内实时 f615/f616 → 否则 None), 而本函数**只传了
      auction_window=False, 没传定格 map** ⇒ 前两条都不成立 ⇒ 恒 None。
      (这正是「键下发了 ≠ 值有内容」——上轮只核了 key 存在, 没核 value。)

      修法: 与竞价侧同源, 读当日 9:25 定格快照(snapshot_bid)——
        · `load_day_bid_change()` → {code: 竞涨%}
        · `load_day_bid_amt()`    → {code: 竞价额(万元)}
      两者**自带非交易时段回退最近交易日**(主人 2026-09-08 定的口径), 故盘前/
      周末/节假日调用也能拿到"最近一次竞价"的值, 不会又变回 null。

      ⚠️ 参数是**标量**(`Optional[float]`), 不是 map —— 必须按 code 逐行取,
         传 map 会 `TypeError: unsupported operand type(s) for *: 'dict' and 'float'`。
      ⚠️ 单位: `load_day_bid_amt` 返回**万元**, 正好对应 `day_bid_amt_wan`(内部 ×1e4 转元);
         上层 `_spot_payload` 再 /1e4 折回万元下发 → 前端 `bidAmtText` 按万元处理。
    """
    bid_chg_map = bid_chg_map or {}
    bid_amt_map = bid_amt_map or {}
    out = []
    for s in raw:
        code = str(s.get("f12") or "")
        if not code:
            continue
        try:
            row = QuoteRow.from_eastmoney(
                s, auction_window=False,
                day_bid_change=bid_chg_map.get(code),
                day_bid_amt_wan=bid_amt_map.get(code),
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
        # 🔴 2026-09-28 v4.11.78: 读当日 9:25 定格(竞涨/竞额), 供前端「竞涨/竞额」两列展示。
        #   失败一律降级为 {} ⇒ 那两列显示 "-"(与修复前一致), **绝不阻塞选股主流程**
        #   (它们只是展示字段, 不参与 spot 六因子评分)。
        bid_chg_map, bid_amt_map = {}, {}
        try:
            bid_chg_map = auction_snapshot.load_day_bid_change() or {}
            bid_amt_map = auction_snapshot.load_day_bid_amt() or {}
        except Exception as e:                             # noqa: BLE001
            log.warning("spot 9_25 定格读取失败(竞涨/竞额降级为 -) err=%s", str(e)[:120])

        pairs = _spot_rows_from_raw(raw, f, zt_map, None,
                                    bid_chg_map=bid_chg_map, bid_amt_map=bid_amt_map)
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

        # 连板高度标签(2026-09-28 主人要求: 实时动态选股也要有「昨首板」)。
        #   ★ 复用竞价那份 **唯一权威实现** `stocks._fill_lb`(局部 import 避免与 stocks.py 形成
        #     模块级循环依赖); 口径(买入前一日真实连板数)与文案(utils/lb.js)因此天然一致。
        #   ★ spot 没有"定格日"概念 ⇒ 显式传**今天**作基准日 ⇒ `_fill_lb` 取"今天之前的最近
        #     交易日"= 买入前一日(非交易日/盘前调用同样得到最近交易日的连板数)。
        #   ★ 独立降级: `_fill_lb` 内部已 try/except + 空池不打标 ⇒ 这里再兜一层,
        #     保证**任何情况都不影响名单本身**(标签缺失时前端不渲染胶囊)。
        try:
            from .stocks import _fill_lb
            _fill_lb(lst, ref_date=_bj_date())
        except Exception as e:                             # noqa: BLE001
            log.warning("spot 连板标签填充失败(名单照常返回, 标签留空) err=%s", e)
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
