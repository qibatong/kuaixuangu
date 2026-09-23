# -*- coding: utf-8 -*-
"""
全市场预计算 + 物化表 (P1, 2026-09-12)
=================================================================================
为什么能预计算: 评分 5 因子的输入**全部在 9:25 定格**, 无一依赖实时行情 ——

    因子          权重   来源                        是否漂移
    竞价涨幅      34%    snapshot_bid 9_25 定格       否
    竞价换手      32%    bid_amt ÷ free_mv(派生)      否
    竞价强度      17%    bid_strength(快照+开盘啦)    否
    流通市值      11%    snapshot_bid mv(free 优先)   否(半静态)
    昨日涨幅       6%    yday_amount 表(收盘落库)     否

于是竞价结束后可对全市场(≈5557只)**一次性**算好分数, 写 stock_score_daily;
用户改筛选条件时只对本表做一次 SELECT + 纯 CPU 过滤(毫秒级), 不必重跑取数与评分。

三条铁律(违反任何一条都会让本模块失去意义):
  1. **绝不因网络可用性漂移**: 竞价/评分字段全部来自已落库数据。唯一例外是
     `prev_close`(昨收)—— 它是**静态值**(全天恒定), 拉一次进表即缓存, 不会漂移;
     拉取失败降级为 None(priceGt 门槛对该批不生效, 只放宽不误杀), 并告警。
  2. **缺失写 None, 绝不写 0**: 与 P0-3 同源 —— 0 = 实测值, None = 未知。
     物化表所有数值列均可空, 就是为了让"不知道"能被表达。
  3. **失败不落半张表**: 全市场行数 < MIN_ROWS 判定本次失败, 不写库, 下次调度重跑
     (沿用 system_batch「空名单不落库」精神)。读路径因此要么读到完整的, 要么读到空。
"""
import json
import time
from typing import Dict, List, Optional, Sequence, Tuple

from ...core import logger
from ...db import database
from .. import settings
from .contract import QuoteRow
from .score import ScoreResult, score_rows, coarse_rank_score

log = logger.get_logger(__name__)

# ---- settings 开关(与既有 picker_lock / history_null_restore 同一模式, 可即时回滚) ----
READ_SWITCH = "precompute_read"      # 读: pipeline 是否改走物化表
WRITE_SWITCH = "precompute_write"    # 写: system_batch 是否执行预计算
DETAIL_SWITCH = "precompute_detail"  # 是否落因子明细 JSON(排查/对拍用, 约 2MB/天)

MIN_ROWS = 500
"""物化成功的行数下限: 全市场约 5557 只, 低于 500 说明定格数据塌了(如 9/11 熔断日
只落 132 行)。此时**宁可不落库**也不要写一张残缺表 —— 读路径会自动回退原路径。"""

_MARKETS = ["hs", "cyb", "kcb"]


def read_enabled() -> bool:
    return bool(settings.get(READ_SWITCH, 0))


def write_enabled() -> bool:
    return bool(settings.get(WRITE_SWITCH, 0))


def detail_enabled() -> bool:
    return bool(settings.get(DETAIL_SWITCH, 0))


# ---------------------------------------------------------------- 数据装配
def load_yday_chg() -> Dict[str, float]:
    """全市场昨日涨幅 {code: %} — 来自 yday_amount 表(收盘批量落库, 零网络)。

    实测覆盖 5550/5557(99.9%)。缺失返回空 dict(该因子走 default, 不阻塞)。
    """
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT code, chg FROM yday_amount WHERE chg IS NOT NULL").fetchall()
        conn.close()
        return {str(c): float(v) for c, v in rows
                if c and v is not None}
    except Exception as e:                                        # noqa: BLE001
        log.warning("[预计算] 昨日涨幅加载失败(该因子走 default) err=%s", e)
        return {}


def load_prev_close(markets: Optional[Sequence[str]] = None) -> Dict[str, float]:
    """全市场昨收 {code: 元} — 唯一的网络调用, 且**只在预计算时跑一次**。

    合理性: 昨收是**静态值**(当日全天恒定), 不像现价会漂; 拿不到只是 priceGt
    门槛对该批不生效(放宽, 不误杀), 不会造成名单漂移。
    失败返回空 dict 并告警 —— 宁可门槛失效, 也不让预计算整体失败。
    """
    try:
        from .. import fetcher, scorer
        fs = scorer.market_fs(list(markets or _MARKETS))
        raw, err = fetcher.ensure_cache("filter", fs, True)
        if err or not raw:
            log.warning("[预计算] 昨收拉取失败(priceGt 门槛本次不生效) err=%s", err)
            return {}
        out: Dict[str, float] = {}
        for s in raw or []:
            code = str(s.get("f12") or "")
            v = s.get("f18")
            if not code or v in (None, "", "-"):
                continue
            try:
                out[code] = float(v)
            except (TypeError, ValueError):
                continue
        log.info("[预计算] 昨收拉取 %d 只", len(out))
        return out
    except Exception as e:                                        # noqa: BLE001
        log.warning("[预计算] 昨收拉取异常(priceGt 门槛本次不生效) err=%s", e)
        return {}


def build_universe(date: Optional[str] = None, *,
                   prev_close: Optional[Dict[str, float]] = None,
                   yday: Optional[Dict[str, float]] = None,
                   with_prev_close: bool = True) -> Dict[str, QuoteRow]:
    """装配全市场定格行 {code: QuoteRow}(纯 DB + 可选一次昨收拉取)。

    竞价字段权威 = snapshot_bid 9_25 定格; 昨日涨幅 = yday_amount; 昨收 = 参数/一次拉取。
    返回的 QuoteRow **不含实时字段**(price/real_change/turnover/vol_ratio),
    那属于 L3 展示层, 由 patch 源在查询时补 —— 预计算不碰, 否则名单会漂。
    """
    from .. import auction_snapshot
    snap = auction_snapshot.load_snapshot_full(date) or {}
    if not snap:
        return {}
    yd = load_yday_chg() if yday is None else (yday or {})
    pc = prev_close if prev_close is not None else (
        load_prev_close() if with_prev_close else {})

    rows: Dict[str, QuoteRow] = {}
    for code, v in snap.items():
        if not code:
            continue
        d = dict(v)
        if pc.get(code) is not None:
            d["pre_close"] = pc[code]
        row = QuoteRow.from_snapshot(d)
        if not row.code:
            row.code = code
        # 昨日涨幅: 权威是 yday_amount(真实 T 日涨跌幅), 覆盖快照里的 change 代理值
        if yd.get(code) is not None:
            row.yesterday_change = yd[code]
        rows[row.code] = row
    return rows


# ---------------------------------------------------------------- 计算与落库
def precompute_all(date: Optional[str] = None, *,
                   rows: Optional[Dict[str, QuoteRow]] = None,
                   strengths: Optional[Dict[str, float]] = None,
                   zt_codes: Optional[set] = None,
                   cfg: Optional[dict] = None,
                   min_rows: Optional[int] = None) -> dict:
    """对全市场算分并幂等写入 stock_score_daily。

    rows / strengths / zt_codes 可注入(单测与手动重跑用), 不传则自动装配。
    min_rows: 覆盖行数闸门(单测小样本用; None = 取模块常量 MIN_ROWS)。
    返回统计 dict: {date, n, n_score, n_strength, n_prev_close, elapsed_ms, ok, error}

    幂等: PRIMARY KEY(date, code) + INSERT OR REPLACE → 同日重跑结果一致。
    """
    gate = MIN_ROWS if min_rows is None else min_rows
    t0 = time.time()
    from .mode import bj_date
    date = date or bj_date()
    stat = {"date": date, "n": 0, "n_score": 0, "n_strength": 0,
            "n_prev_close": 0, "elapsed_ms": 0, "ok": False, "error": ""}
    try:
        universe = rows if rows is not None else build_universe(date)
        stat["n"] = len(universe)
        if len(universe) < gate:
            stat["error"] = "全市场仅 %d 行(< %d), 判定定格数据缺失, 不落库" % (
                len(universe), gate)
            log.warning("[预计算] %s", stat["error"])
            return stat

        # 竞价强度(三层: 快照自算量比 + 开盘啦抢筹 + 加速度, 对东财免疫)
        st = strengths
        if st is None:
            try:
                from .. import bid_strength
                st = bid_strength.load_scores(list(universe.keys()), date=date) or {}
            except Exception as e:                                # noqa: BLE001
                log.warning("[预计算] 竞价强度加载失败(退回 warn 因子) err=%s", e)
                st = {}
        stat["n_strength"] = len(st or {})

        srows = score_rows(list(universe.values()), cfg, st)
        detail = detail_enabled()
        # 昨涨停/连板(limitUp 过滤用, 2026-09-12 P3): 判据必须与 pipeline **完全同口径**
        #   —— zt_codes 可用(非 None) → code 属于昨涨停池; 不可用 → 降级 concept 文本匹配
        #   (picker/filter.is_first_board)。物化表这一列是前端本地筛选唯一的昨涨停依据,
        #   口径不一致会让本地名单凭空多/少一批昨涨停票(而用户看不出来)。
        from .filter import is_first_board

        conn = database.get_conn()
        try:
            cur = conn.cursor()
            payload = []
            for i, sr in enumerate(srows):
                r = sr.row
                sc = sr.score
                # 异动档位: 与 pipeline 输出口径一致(强5/中4/弱3/极弱0)
                sval = (st or {}).get(r.code)
                if sval is not None:
                    warn = 5 if sval >= 0.85 else 4 if sval >= 0.65 else 3 if sval >= 0.40 else 0
                else:
                    warn = int(r.warn_type or 0)
                payload.append((
                    date, r.code, r.name, sc.probability, sc.confidence,
                    r.bid_change, r.bid_amt, r.bid_vol,
                    r.mv,        # ★ 2026-09-20: 门槛/评分统一市值(自由流通优先) → 列名仍 float_mv
                    sc.bid_turnover, sval, warn,
                    r.yesterday_change, r.prev_close, r.auction_price,
                    1 if ("ST" in (r.name or "")) else 0,
                    1 if is_first_board(r.code, r.concept, zt_codes) else 0,
                    None, r.industry, r.concept, i + 1,
                    json.dumps(sc.parts, ensure_ascii=False) if detail else None,
                    int(time.time()),
                ))
                if r.prev_close:
                    stat["n_prev_close"] += 1
            cur.executemany(
                "INSERT OR REPLACE INTO stock_score_daily (date, code, name, "
                "probability, confidence, bid_change, bid_amt, bid_vol, float_mv, "
                "bid_turnover, strength, warn_type, yday_chg, prev_close, "
                "auction_price, is_st, is_zt_yday, board, industry, concept, "
                "rank, detail, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                payload)
            conn.commit()
            stat["n_score"] = len(payload)
            stat["ok"] = True
        finally:
            conn.close()
        log.info("[预计算] 完成 date=%s 全市场=%d 落库=%d 强度=%d 昨收=%d 耗时%dms",
                 date, stat["n"], stat["n_score"], stat["n_strength"],
                 stat["n_prev_close"], stat["elapsed_ms"])
    except Exception as e:                                        # noqa: BLE001
        stat["error"] = str(e)[:200]
        log.error("[预计算] 失败 date=%s err=%s", date, e, exc_info=True)
    stat["elapsed_ms"] = int((time.time() - t0) * 1000)
    return stat


# ---------------------------------------------------------------- 读路径
def read_materialized(date: Optional[str] = None, *,
                      min_rows: Optional[int] = None
                      ) -> Tuple[Dict[str, QuoteRow], Dict[str, ScoreResult]]:
    """读物化表 → ({code: QuoteRow}, {code: ScoreResult})。

    min_rows: 覆盖行数闸门(单测小样本用; None = 取模块常量 MIN_ROWS)。
    """
    gate = MIN_ROWS if min_rows is None else min_rows
    from .mode import bj_date
    date = date or bj_date()
    rows: Dict[str, QuoteRow] = {}
    scores: Dict[str, ScoreResult] = {}
    try:
        conn = database.get_conn()
        cur = conn.execute(
            "SELECT code, name, probability, confidence, bid_change, bid_amt, "
            "bid_vol, float_mv, bid_turnover, strength, warn_type, yday_chg, "
            "prev_close, industry, concept FROM stock_score_daily WHERE date=?",
            (date,))
        data = cur.fetchall()
        conn.close()
    except Exception as e:                                        # noqa: BLE001
        log.warning("[预计算] 物化表读取失败(回退原路径) err=%s", e)
        return {}, {}
    if len(data) < gate:
        if data:
            log.warning("[预计算] 物化表 %s 仅 %d 行(< %d), 回退原路径",
                        date, len(data), gate)
        return {}, {}
    # 两个 dict **同键**; 行数不足(表缺失/批跑失败)已在上面返回空 —— 调用方据此
    # 回退原路径, 接口永不报错(容灾: P1 上线后任何异常都不能导致首页空白)。
    for (code, name, prob, conf, bchg, bamt, bvol, mv, bto, stg,
         warn, ychg, prev, ind, con) in data:
        if not code:
            continue
        rows[code] = QuoteRow(
            code=code, name=name or "", bid_change=bchg, bid_amt=bamt,
            bid_vol=bvol, prev_close=prev, free_mv=mv,   # ★ 列存统一市值 → 回填 free_mv
            industry=ind or None, concept=con or None,
            yesterday_change=ychg, warn_type=warn, source="precompute")
        scores[code] = ScoreResult(probability=prob or 0, confidence=conf or 0,
                                   bid_turnover=bto)
    return rows, scores


def read_snapshot_rows(date: Optional[str] = None, *,
                       min_rows: Optional[int] = None) -> List[dict]:
    """P3(2026-09-12): 读物化表全市场行 → **前端本地筛选**用的轻量 dict 列表。

    与 read_materialized 的分工:
      * read_materialized 服务 pipeline 评分(返回 QuoteRow + ScoreResult, 内部口径)
      * 本函数服务前端(JSON 友好, 单位直接对齐前端 filters 的门槛单位)

    单位对齐(关键): 前端 filters 的门槛是「亿」(floatMvFloor) 与 「万元」(bidAmtFloor),
    后端 apply_filters 内部同样按 `mv_yi(=mv/1e8)` / `bid_amt/1e4` 比较 —— 两边都从
    **同一个整数元值**做同一次除法, 结果位级相同, 本地筛选与后端名单才能逐票一致。

    ★ 2026-09-20: 物化表 `float_mv` 列存的是 **mv(free_mv 优先, 缺则 float_mv)** ——
      列名保留以兼容既有 schema, 语义已统一为"门槛/评分市值"。前端 `floatMv` 字段名不变。

    行数不足(< min_rows)返回 [] —— 调用方视为"物化表不可用"并回退原路径,
    与 read_materialized 同一闸门语义(宁可不发, 不发半张表)。
    """
    gate = MIN_ROWS if min_rows is None else min_rows
    from .mode import bj_date
    date = date or bj_date()
    try:
        conn = database.get_conn()
        cur = conn.execute(
            "SELECT code, name, probability, confidence, bid_change, bid_amt, "
            "float_mv, prev_close, auction_price, is_st, is_zt_yday, warn_type, "
            "industry, concept, rank FROM stock_score_daily WHERE date=? "
            "ORDER BY rank", (date,))
        data = cur.fetchall()
        conn.close()
    except Exception as e:                                        # noqa: BLE001
        log.warning("[预计算] 快照行读取失败 err=%s", e)
        return []
    if len(data) < gate:
        if data:
            log.warning("[预计算] 物化表 %s 仅 %d 行(< %d), 快照不可用",
                        date, len(data), gate)
        return []
    out: List[dict] = []
    # ★ 2026-09-23: 下发 coarsRank —— 前端本地筛选的粗筛排队键。
    #   前端必须与后端 picker.filter.coarse_filter 用**同一把尺子**截断候选(否则
    #   「本地秒筛名单」与后端名单会不一致 → 锁定/历史/推送全线错位)。而该排队键
    #   需要评分分档表与权重, **不宜下发到前端**(2026-08-31 主人要求评分构成保密),
    #   故由后端算好一个标量随行下发 —— 前端只排序, 不重算公式, 公式也就无从漂移。
    try:
        from .. import scorer                                     # 延迟导入: 避免模块循环
        _cfg = scorer.get_scoring_cfg()
    except Exception as e:                                        # noqa: BLE001
        log.warning("[预计算] 评分配置读取失败(粗排分置空, 前端退回竞价额键) err=%s", e)
        _cfg = None
    for (code, name, prob, conf, bchg, bamt, mv, prev, ap,
         is_st, is_zt, warn, ind, con, rank) in data:
        if not code:
            continue
        coarse_rank = None
        if _cfg is not None:
            try:
                coarse_rank = round(coarse_rank_score(QuoteRow(
                    code=str(code), name=name or "", bid_change=bchg,
                    bid_amt=bamt, float_mv=mv, prev_close=prev,
                    warn_type=(lambda x: None if x is None else int(x))(warn),
                ), _cfg), 4)
            except Exception as e:                                # noqa: BLE001
                log.warning("[预计算] 粗排分计算失败 code=%s err=%s", code, e)
        out.append({
            "code": str(code), "name": name or "",
            "probability": prob, "confidence": conf,
            "bidChange": bchg,                       # %
            "bidAmt": None if bamt is None else bamt / 1e4,     # 元 → 万元
            "floatMv": None if mv is None else mv / 1e8,        # 元 → 亿
            "prevClose": prev,
            "auctionPrice": ap,                      # 定格竞价价(价格门槛用)
            "isSt": 1 if is_st else 0,
            "isZt": 1 if is_zt else 0,               # 昨涨停/连板(limitUp 过滤用)
            "warnType": warn, "industry": ind, "concept": con,
            "rank": rank,
            "coarseRank": coarse_rank,               # 粗筛排队分(前端排序键, 见上)
        })
    return out


def clear_date(date: Optional[str] = None) -> int:
    """清理某日物化结果(回滚/重跑用), 返回删除行数"""
    from .mode import bj_date
    date = date or bj_date()
    try:
        conn = database.get_conn()
        cur = conn.execute("DELETE FROM stock_score_daily WHERE date=?", (date,))
        n = cur.rowcount or 0
        conn.commit()
        conn.close()
        return n
    except Exception as e:                                        # noqa: BLE001
        log.warning("[预计算] 清理失败 date=%s err=%s", date, e)
        return 0
