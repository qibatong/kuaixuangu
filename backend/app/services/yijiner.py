# -*- coding: utf-8 -*-
"""竞价一进二 选股(2026-09-28 新增 /api/yijiner)
================================================================================
语义: **昨日主板首板 → 今日竞价阶段评估二连板潜力**, 输出打分排序名单。

🔴 本模块的评分公式是**逐函数照搬主人提供的独立网页版**(`竞价一进二专用版`),
   已按要求**刻意保留其原样行为**, 包括一处已知缺陷:

   ⚠️ 网页版把东财 `f4`(涨跌额) 当作"昨收"使用(实测确认: 000678 → f4=0.97 /
      f18=9.68, 而 9.68×1.1≈10.65 才是涨停价 ⇒ **昨收是 f18, f4 是涨跌额**)。
      后果: `strength_score` 的 `limit_price = f4×(1+涨停幅)` ≈ 1.07, 远低于现价
      ⇒ `distance_to_limit` 恒为 0 ⇒ `price_stability` **恒为满分 12**,
      「距涨停距离」因子事实上失效, 每只票白送 12 分(该项满分 20)。
      另 `price_score` 的一字板判断 `price/f4 >= 0.995` 也随之恒成立。

   ⇒ 主人 2026-09-28 明确裁定:**照搬网页版(含该缺陷)**, 以保证后端产出与网页版
     逐位一致、便于对拍。**请勿"顺手修正"为 f18** —— 那会让分数与网页版不一致。
     若日后要修正, 应作为独立变更并同步前端口径说明。

数据来源(全部复用现有生产链路, 不新开取数):
  · 昨日涨停池: `fetcher.fetch_zt_pool(date)` → {code: {fund, fb(首封 HHMMSS), lb(连板), zbc(炸板), zdp}}
  · 今日行情:   `fetcher.fetch_raw_by_codes(codes, extra_fields="f26")`
                (东财 ulist 点查, 与全市场 clist 同构, 整段复用 config.FIELDS)
  · 交易日:     `core.trade_calendar.prev_trade_date`

与 picker 的关系: **本模块不参与 `picker.pipeline.run()` 那条竞价选股链路**, 也不落批次、
不推送、不参与定格 —— 它是独立功能的只读端点(与 `api/stocks_spot.py` 独立端点同一思路)。
"""
from __future__ import annotations

import json
import time

from ..core import logger, trade_calendar
from ..db import database
from . import fetcher

log = logger.get_logger(__name__)

# ---------------- 筛选口径(逐字照搬网页版 filterSettings 默认值) ----------------
# 网页版 applyNewFilters() 的默认参数; 命中规则: bid 为 [bidMin, bidMax) 半开区间,
# mv 与 price 为闭区间(含边界)。
DEFAULT_FILTERS = {
    "stSuspend": True,        # 剔除 ST / 停牌
    "excludeNewStock": True,  # 剔除上市 < 60 天次新
    "bidMin": 3.0,            # 竞价涨幅下限(%)含
    "bidMax": 8.0,            # 竞价涨幅上限(%)不含
    "mvMin": 10.0,            # 流通市值下限(亿)
    "mvMax": 230.0,           # 流通市值上限(亿)
    "priceMin": 2.0,          # 股价下限(元)
    "priceMax": 100.0,        # 股价上限(元)
}

_MAX_LOOKBACK_DAYS = 12   # 与网页版一致: 从昨日往前最多回溯 12 个交易日找非空涨停池
_ONE_WORD_BOARD_FBT = 92500   # 首封 <= 9:25:00 视为一字板, 剔除(与东财 fbt HHMMSS 口径一致)


def _num(v, default=0.0):
    """东财 fltt=2 缺值会下发字符串 '-'。网页版用 isNaN 兜底 ⇒ 这里等价处理。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:          # NaN
        return default
    return f


def is_main_board(code):
    """网页版 isMainBoard(): 仅沪 60 / 深 00; 剔除 300/301/688 与 8/4(北交所)。"""
    if not code:
        return False
    c = str(code)
    if c.startswith(("300", "301", "688", "8", "4")):
        return False
    return c.startswith(("60", "00"))


def is_st(name):
    """网页版 isST(): 名称含 'ST'(含 *ST)。"""
    return "ST" in str(name or "")


def listing_days(f26):
    """网页版 getListingDays(): f26 为 YYYYMMDD 整数 → 距今天数; 非法返回 None。"""
    s = str(f26 or "")
    if len(s) != 8:
        return None
    try:
        y, m, d = int(s[0:4]), int(s[4:6]), int(s[6:8])
        return (time.time() - time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))) / 86400.0
    except (TypeError, ValueError):
        return None


def get_bid_change(row):
    """网页版 getBidChange(): f615(竞价涨幅) 优先, 缺失/非数 → 退 f3(现涨)。"""
    v = row.get("f615")
    if v is not None and not isinstance(v, str):
        return _num(v, _num(row.get("f3")))
    if isinstance(v, str) and v.strip() not in ("", "-"):
        try:
            return float(v)
        except ValueError:
            pass
    return _num(row.get("f3"))


def get_entity_change(row):
    """网页版 getEntityChange(): (现价 - 今开)/今开×100, 今开为 0 → 0。"""
    o = _num(row.get("f17"))
    c = _num(row.get("f2"))
    if o == 0:
        return 0.0
    return (c - o) / o * 100.0


def is_suspended(row):
    """网页版 isSuspended(): (f4||0) <= 0 || (f5||0) === 0。

    ⚠️ 照搬: 网页版此处也误用了 f4(涨跌额) —— `涨跌额<=0` 即判停牌。
       因本功能已强制 bid>=3(涨跌额必为正), 实际影响可忽略, 故保留原样。
    """
    return _num(row.get("f4")) <= 0 or _num(row.get("f5")) == 0


def simulated_volume_ratio(row):
    """网页版 getSimulatedVolumeRatio(): 用竞价换手率代理值映射量比档位。

    bidTurn = (f5 成交量 × f2 现价) / (f10 量比 × 100), 三值有一为 0 则代理值为 0。
    """
    bid_turn = 0.0
    bv, mv, p = _num(row.get("f5")), _num(row.get("f10")), _num(row.get("f2"))
    if mv > 0 and p > 0 and bv > 0:
        bid_turn = (bv * p) / (mv * 100.0)
    if bid_turn >= 0.8:
        return 1.5
    if bid_turn >= 0.5:
        return 1.0
    if bid_turn >= 0.3:
        return 0.5
    if bid_turn >= 0.15:
        return 0.3
    return 0.2


def volume_score(ratio):
    """网页版 getVolumeScoreFromRatio(): 满分 30。"""
    if ratio >= 1.5:
        return 30
    if ratio >= 1.0:
        return 25
    if ratio >= 0.5:
        return 20
    if ratio >= 0.3:
        return 10
    return 0


def price_score(bid_change, price, f4):
    """网页版 getPriceScore(): 竞价涨幅得分, 满分 35。

    ⚠️ 一字板判断用 f4 当昨收(照搬, 见模块头 docstring)。
    """
    is_yizi = f4 > 0 and price / f4 >= 0.995 and bid_change >= 9.5
    if is_yizi:
        return 35
    if bid_change >= 9.9:
        return 35
    if bid_change >= 8:
        return 32
    if bid_change >= 6:
        return 28
    if bid_change >= 4:
        return 24
    if bid_change >= 2:
        return 18
    if bid_change >= 0:
        return 10
    return 0


def strength_score(price, f4, code, ratio):
    """网页版 getStrengthScore(): 距涨停距离 + 动能, 区间 [8, 20]。

    ⚠️ 因 f4 被当昨收用, price_stability 恒为 12(见模块头 docstring)。
    """
    if f4 <= 0:
        return 10
    limit_up = 0.2 if (str(code).startswith("688") or str(code).startswith("30")) else 0.1
    limit_price = f4 * (1 + limit_up)
    distance = max(0.0, (limit_price - price) / limit_price)
    price_stability = 12 * (1 - min(1.0, distance))
    momentum = min(8.0, (ratio / 1.5) * 8.0)
    return min(20.0, max(8.0, price_stability + momentum))


def build_sector_rank_map(items):
    """网页版 buildSectorRankMap(): 按行业分组, 组内按竞价涨幅降序 → 名次。

    ⚠️ 注意口径: 排名池是**本次候选集**(不是全市场), 与网页版一致。
    """
    groups = {}
    for it in items:
        ind = it.get("industry") or ""
        if ind:
            groups.setdefault(ind, []).append(it)
    rank_map = {}
    for ind, lst in groups.items():
        ordered = sorted(lst, key=lambda x: -_num(x.get("bidChange")))
        rank_map[ind] = {
            "rank": {x["code"]: i + 1 for i, x in enumerate(ordered)},
            "total": len(ordered),
        }
    return rank_map


def sector_score(item, rank_map):
    """网页版 getSectorScore(): 板块地位得分, 满分 15。"""
    ind = item.get("industry") or ""
    if not ind:
        return 5
    info = rank_map.get(ind)
    if not info:
        return 5
    rank = info["rank"].get(item["code"], 999)
    total = info["total"]
    mv = _num(item.get("circulationMV"))
    is_top = rank <= max(1, int(total * 0.1))
    is_front = rank <= max(3, int(total * 0.25))
    if is_top and mv < 100:
        return 15
    if is_top:
        return 13
    if is_front and mv < 150:
        return 11
    if is_front:
        return 9
    if rank <= min(6, total * 0.4):
        return 7
    return 5


def confidence_of(total):
    """网页版 computeNewScore() 里的可信度映射。"""
    if total >= 80:
        return 88
    if total >= 70:
        return 78
    if total >= 60:
        return 70
    if total >= 50:
        return 62
    if total >= 40:
        return 52
    return 45


def compute_score(item, rank_map):
    """网页版 computeNewScore(): 四因子求和 → 红线封顶 45 → 可信度。

    红线: 竞价翻绿(bidChange < 0) 或 量比 < 0.3。
    """
    bid_change = _num(item.get("bidChange"))
    ratio = item["volumeRatio"]
    price = _num(item.get("price"))
    f4 = _num(item.get("f4"))
    total = (price_score(bid_change, price, f4)
             + volume_score(ratio)
             + strength_score(price, f4, item["code"], ratio)
             + sector_score(item, rank_map))
    red = bid_change < 0 or ratio < 0.3
    if red:
        total = min(total, 45)
    total = min(100, max(0, total))
    return int(round(total)), confidence_of(total), red


def _passes_filters(item, raw, f):
    """网页版 applyNewFilters() 的逐条判定(返回 None 表示通过, 否则返回剔除原因)。"""
    if not is_main_board(item["code"]):
        return "not_main_board"
    if f["stSuspend"] and (is_st(item["name"]) or is_suspended(raw)):
        return "st_or_suspend"
    if f["excludeNewStock"]:
        days = listing_days(raw.get("f26"))
        if days is not None and days < 60:
            return "new_stock"
    bid = _num(item.get("bidChange"))
    if bid < f["bidMin"] or bid >= f["bidMax"]:
        return "bid_range"
    mv = _num(item.get("circulationMV"))
    if mv < f["mvMin"] or mv > f["mvMax"]:
        return "mv_range"
    price = _num(item.get("price"))
    if price < f["priceMin"] or price > f["priceMax"]:
        return "price_range"
    return None


def _pick_zt_date():
    """从昨日往前找**第一个非空涨停池**的交易日(Y-M-D)。

    与网页版一致: 数据驱动, 空池(休市/数据未出)继续往前。用 trade_calendar 取候选交易日
    (项目纪律: 判"哪一天"必须走交易日历, 历史上的休市日幽灵数据事故即由此而来)。
    """
    day = trade_calendar.bj_date()
    for _ in range(_MAX_LOOKBACK_DAYS):
        prev = trade_calendar.prev_trade_date(day)
        if not prev or prev >= day:
            break
        day = prev
        pool = fetcher.fetch_zt_pool(day.replace("-", ""))
        if pool:
            return day, pool
    return None, {}


def _hist_raw_rows(codes, date):
    """历史回看: 用**该交易日的 9:25 快照 + 日K**重建东财行情行(f* 同构), 供打分/过滤复用。

    与 9:25 实时**同源同义**的字段:
      f12 code | f14 name | **f615 竞价涨幅(取该日 9_25 快照)** | f2 现价=当日收 | f18 昨收
      f3 现涨=当日收盘涨幅 | f4 涨跌额=收−昨收 | f5 成交量(手, 该日 K 线)
      f10 量比 = 该日量 ÷ 前 5 日均量 | f21 流通市值(元, 取该日快照 float_mv)
    🔴 两处**不可重建**(故历史口径由路由标记 approx=True):
      · f26 上市日期 → None ⇒ 次新过滤自然放行(代码里 `days is None` 不剔除)
      · f100 行业 → "-" ⇒ 板块排名退化为单组(等价于按竞价涨幅排名)
    """
    conn = database.get_conn()
    out = []
    try:
        for code in codes:
            row = conn.execute(
                "SELECT name, bid_change, bid_amt, float_mv FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25' AND code=?", (date, code)).fetchone()
            if not row:
                continue                                   # 该日无 9:25 快照 → 无从重建, 跳过
            name, chg = row[0], _num(row[1], None)
            amt_wan, fmv = _num(row[2], 0.0), _num(row[3], 0.0)
            krow = conn.execute("SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
            if not krow or not krow[0]:
                continue
            try:
                dd = json.loads(krow[0]) if isinstance(krow[0], str) else krow[0]
                t = dd.get("time") or []
                idx = None
                for i, d in enumerate(t):
                    if str(d) == date:
                        idx = i
                if idx is None or idx < 1:
                    continue
                closes = dd.get("close") or []
                opens = dd.get("open") or []
                vols = dd.get("volume") or []
                close, prev = float(closes[idx]), float(closes[idx - 1])
                # 🔴 f17(今开) 必须给: `get_entity_change` 是 (f2-f17)/f17, 缺 f17 时
                #   代码按 o==0 直接返回 0.0 ⇒ 历史行实体涨幅会全变 0.00(实测踩到)。
                open_ = float(opens[idx] or 0) if idx < len(opens) else 0.0
                vol = float(vols[idx] or 0) if idx < len(vols) else 0.0
                pv = [float(x or 0) for x in vols[max(0, idx - 5):idx]]
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            pv = [v for v in pv if v > 0]
            vr = (vol / (sum(pv) / len(pv))) if (vol > 0 and pv) else 0.0
            px = close * (1 + (chg or 0) / 100.0)          # 竞价价(换算竞价量用)
            out.append({
                "f12": code, "f14": name,
                "f615": chg,                               # 竞价涨幅(该日 9:25)
                "f2": round(close, 2), "f18": round(prev, 2), "f17": round(open_, 2),
                "f3": round((close / prev - 1) * 100, 2) if prev > 0 else None,
                "f4": round(close - prev, 2) if prev > 0 else None,
                "f5": vol, "f10": round(vr, 2), "f21": fmv,
                "f26": None, "f100": "-", "f103": "",
            })
    finally:
        conn.close()
    return out


def run(date=None):
    """取数 + 打分 + 过滤 + 排序。返回 (payload_dict, error_msg)。

    date: 空/None = **今天**(实时行情, 与网页版逐位一致);
          'YYYY-MM-DD' = **历史回看**(该交易日的 9:25 快照 + 日K 重建, 见 `_hist_raw_rows`;
          路由会在 payload 上标 `approx=True` —— f26/f100 两项不可重建)。
    不抛异常: 取数失败一律返回 (None, 原因), 由路由层转成 {ok:false}。
    """
    t0 = time.time()
    hist = bool(date)

    # 1) 昨日(最近交易日)涨停池 —— 复用生产在用的涨停池链路
    try:
        if hist:
            # 历史回看: 股票池 = **该日的前一交易日**(不再"从今天往前找非空池")
            prev = trade_calendar.prev_trade_date(date)
            zt_date = prev
            zt_map = fetcher.fetch_zt_pool(prev.replace("-", "")) if prev else {}
        else:
            zt_date, zt_map = _pick_zt_date()
    except Exception as e:                                  # noqa: BLE001
        log.warning("竞价一进二 涨停池获取失败 err=%s", str(e)[:160])
        return None, "涨停池获取失败: %s" % str(e)[:160]
    if not zt_map:
        return None, "近期无涨停池数据(可能长假休市或数据源异常)"

    # 2) 首板候选: 连板数 == 1 且主板 且非 ST 且非一字板(首封 <= 9:25:00)
    first_board = []
    one_word_dropped = 0
    for code, z in zt_map.items():
        code = str(code)
        lb = int(z.get("lb") or 0)
        if lb != 1 or not is_main_board(code):
            continue
        fb = int(z.get("fb") or 0)
        if fb > 0 and fb <= _ONE_WORD_BOARD_FBT:
            one_word_dropped += 1          # 一字板剔除(与网页版口径一致)
            continue
        first_board.append(code)
    if not first_board:
        log.info("竞价一进二 涨停池 %s 无首板候选(池内 %d 只, 一字板剔除 %d)",
                 zt_date, len(zt_map), one_word_dropped)
        return _payload(zt_date, [], {"poolTotal": len(zt_map), "firstBoard": 0,
                                      "oneWordDropped": one_word_dropped,
                                      "candidates": 0, "dropped": {}}, t0), None

    # 3) 行情: 实时走东财按候选点查(几十只, 不拉全市场; extra_fields 追加 f26 供次新过滤);
    #    🔴 历史回看走**该交易日快照+日K重建**(不查实时, 否则会把今天的行情贴到历史日)
    try:
        if hist:
            raw_rows = _hist_raw_rows(first_board, date)
        else:
            raw_rows = fetcher.fetch_raw_by_codes(first_board, extra_fields="f26")
    except Exception as e:                                  # noqa: BLE001
        log.warning("竞价一进二 行情点查失败 codes=%d err=%s", len(first_board), str(e)[:160])
        return None, "行情获取失败: %s" % str(e)[:160]

    # 4) 归一化 → 候选(网页版 processAllStocks 的 baseList)
    by_code = {}
    for r in raw_rows:
        c = str(r.get("f12") or "")
        if c:
            by_code[c] = r
    items = []
    for code in first_board:
        raw = by_code.get(code)
        if not raw:
            continue                                        # 今日无行情(停牌/退市) → 跳过
        z = zt_map.get(code) or {}
        items.append({
            "code": code,
            "name": raw.get("f14") or "",
            "bidChange": get_bid_change(raw),
            "realChange": _num(raw.get("f3")),
            "entityChange": get_entity_change(raw),
            "circulationMV": _num(raw.get("f21")) / 1e8,
            "price": _num(raw.get("f2")),
            "industry": raw.get("f100") or "-",
            "concept": raw.get("f103") or "-",
            "volumeRatio": 0.0,                             # 下步填充
            "f4": _num(raw.get("f4")),
            "limitBoards": int(z.get("lb") or 0),
            "firstSealTime": int(z.get("fb") or 0),
            "breakCount": int(z.get("zbc") or 0),
            "sealFund": _num(z.get("fund")),
            "raw": raw,
        })
    # 量比代理值(网页版 getSimulatedVolumeRatio(stock.rawStock) 在打分时才算)
    for it in items:
        it["volumeRatio"] = simulated_volume_ratio(it["raw"])

    # 5) 打分(板块排名池 = 候选集, 与网页版一致)
    rank_map = build_sector_rank_map(items)
    for it in items:
        total, conf, red = compute_score(it, rank_map)
        it["probability"] = total
        it["confidence"] = conf
        it["redFlag"] = red
    items.sort(key=lambda x: (-x["probability"], x["code"]))

    # 6) 过滤
    f = dict(DEFAULT_FILTERS)
    kept, dropped = [], {}
    for it in items:
        why = _passes_filters(it, it["raw"], f)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
        else:
            kept.append(it)

    stats = {
        "poolTotal": len(zt_map),
        "firstBoard": len(first_board),
        "oneWordDropped": one_word_dropped,
        "candidates": len(items),
        "kept": len(kept),
        "dropped": dropped,
    }
    log.info("竞价一进二 uid-agnostic 池%s %d只 首板%d(剔一字%d) 候选%d 入选%d 剔除=%s 耗时%.0fms",
             zt_date, len(zt_map), len(first_board), one_word_dropped,
             len(items), len(kept), dropped, (time.time() - t0) * 1000)
    return _payload(zt_date, kept, stats, t0), None


def _payload(zt_date, kept, stats, t0):
    """输出形状对齐既有端点风格(camelCase; 缺失透 None 不填 0)。"""
    lst = [{
        "code": it["code"],
        "name": it["name"],
        "probability": it["probability"],
        "confidence": it["confidence"],
        "redFlag": it["redFlag"],
        "bidChange": round(it["bidChange"], 2),
        "realChange": round(it["realChange"], 2),
        "entityChange": round(it["entityChange"], 2),
        "circulationMV": round(it["circulationMV"], 2),
        "price": round(it["price"], 2),
        "industry": it["industry"],
        "concept": it["concept"],
        "limitBoards": it["limitBoards"],
        "firstSealTime": it["firstSealTime"],
        "breakCount": it["breakCount"],
        "sealFund": it["sealFund"],
    } for it in kept]
    return {
        "ok": True,
        "strategy": "yijiner",
        "dataDate": (zt_date or "").replace("-", ""),   # 实际取到的涨停池日期 YYYYMMDD
        "list": lst,
        "count": len(lst),
        "stats": stats,
        "filters": dict(DEFAULT_FILTERS),
        "dataTime": int(time.time()),
        "elapsedMs": int((time.time() - t0) * 1000),
    }
