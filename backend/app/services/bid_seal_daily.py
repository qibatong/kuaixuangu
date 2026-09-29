# -*- coding: utf-8 -*-
"""连续 N 日竞价封单(2026-09-29 主人拍板: 多列并排视图)

数据源
------
猫爪 `daily_auc_fd` 的**分时封单**字段(实测**历史日期完全可用**, 09-22~09-29 逐日拉到):
    `fa_0915` / `fa_0920` / `fa_0925l`
        官方语义 = 该时刻前最后一笔「匹配价=涨停价」的竞价金额(元)
        ⇒ **非涨停股恒无值**, 天然满足契约「非涨停股无封单, 置 0」
    `auc_pct_chg`      → 竞价涨幅(%)
    `theme_names_kpl`  → 概念

为什么不用自采库 `snapshot_bid`
-------------------------------
历史交易日的 `snapshot_bid` 9_15/9_20 行里 `auc_vol_ratio`/`bid_buy_amt` 是**旧代码产物**
(四时点共取 9:25 口径) ⇒ 9:15/9:20 列必然为空, 无法支撑多日视图。
猫爪分时字段对**任意历史交易日**都可用, 且与主人给的模板逐只逐时点吻合。

口径
------
【展示集合 · 2026-09-30 主人明确规则(四步)】
    9:15 涨停 → 展示; 否则 9:20 涨停 → 展示; 否则 9:25 涨停 → 展示。
    各时点「是否涨停」的判据 = 该时点的封单字段非零
    (`fa_0915` / `fa_0920` / `fa_0925`|`fa_0925l`) —— 猫爪官方语义即
    「该时刻前最后一笔『匹配价=涨停价』的竞价金额」, 非零 ⟺ 该时刻涨停;
    9:25 另加竞价涨幅(`is_zt_by_change`, 分板块 10/20/30%)取或, 不漏判。
    🔴 旧实现只收「9:25 涨停」⇒ 漏掉大量**盘中炸板**票(9:15/9:20 封过板、9:25 已回落,
    涨幅为负), 例如模板里的「跨境通 -0.51%」。

【统计口径 · 已与模板四日逐位对拍】
    ① **「一字」= 9:25 涨停的只数**;
    ② **封单总额 = Σ `fa_0925l`(9:25 后末笔)**, 只统计上面那批 9:25 涨停的票 ——
       四日实测 104.1 / 113.8 / 109.9 / 106.6 亿, 与模板**四个日期逐位一致**。
    ⇒ 展示集 ⊋ 统计集(炸板票进展示, 但不计入一字/封单总额)。
    ③ 排序 = 三层: ①9:25 涨停 ②9:20 涨停回落 ③仅 9:15 涨停(与同页「单日榜」一致)。
"""
from __future__ import annotations

from ..core import logger
from ..core import trade_calendar as tc
from ..db import database
from . import auction_snapshot, meoz_client

log = logger.get_logger(__name__)

# 展示用三个时点 → 猫爪字段(9:25 取「末笔」fa_0925l, 与已确认的金额口径同一字段)
POINT_FIELDS = (("9_15", "fa_0915"), ("9_20", "fa_0920"), ("9_25", "fa_0925l"))
# 「9:25 有封单」的判据字段(末笔优先, 主时刻兜底) —— 只用于判定该股是否**进榜**
SEAL25_KEYS = ("fa_0925l", "fa_0925")
MAX_DAYS = 10


def _f(v):
    """惰性转 float: None/''/异常 → 0.0(封单缺值按 0 处理, 不抛)。"""
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def last_trade_days(n: int = 5, end: str = "") -> list:
    """最近 n 个交易日(**含** end 当日), 由近到远, 格式 YYYY-MM-DD。

    end 缺省=北京今天; 非交易日先回退到最近交易日 —— 周末/长假打开页面
    不会把休市日当列头(2026-09-25 中秋幽灵快照事故的同类防线)。
    """
    d = (end or "").strip() or tc.bj_date()
    if not tc.is_trade_day(d):
        d = tc.prev_trade_date(d)
    out = []
    cur = d
    while cur and len(out) < max(1, min(MAX_DAYS, int(n))):
        out.append(cur)
        cur = tc.prev_trade_date(cur)
    return out


def day_seal(date: str, limit: int = 200) -> dict:
    """单日竞价封单榜(猫爪分时字段 → 涨停过滤 → 按 9:25 封单降序)。

    Args:
        date:  YYYY-MM-DD / YYYYMMDD。
        limit: 最多返回多少只(模板一列约 16~20 行)。

    Returns:
        {"date", "yizi", "sealTotal", "rows": [...]}; 取数失败/无数据时 rows 为空、
        yizi=0、sealTotal=0(**不抛异常**, 由调用方按空列渲染)。
    """
    d8 = str(date).replace("-", "")
    iso = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8]) if len(d8) == 8 else str(date)
    try:
        fd = meoz_client.auc_fd_map(date=d8) or {}
    except Exception as e:                                        # noqa: BLE001
        log.warning("[连续封单] 猫爪 daily_auc_fd 读取失败 date=%s err=%s", iso, str(e)[:120])
        fd = {}
    # 防串日兜底(与 auction_snapshot 同纪律): 本函数用**显式 `date=`** 查询, 上游语义是
    #   「该日无数据即返空」, 理论上不会串日; 但这是一道廉价保险 —— 一旦上游语义变化,
    #   宁可当列空着, 也不把别日封单显示成今日(静默错数是最难排查的一类)。
    fd = {c: r for c, r in fd.items()
          if str((r or {}).get("tradedate") or "").replace("-", "") in ("", d8)}

    # 连板数(2026-09-29 补, 与模板 12/12 逐位对拍确认): 源 = 猫爪 `limit_pool.limit_times`
    #   (1 → 前端显示「首板」)。**独立降级**: 取不到就只少一个标签, 不影响封单主数据。
    #   🔴 串日纪律同 daily_auc: 只有 tradedate == 目标日才认, 否则宁可不显示 ——
    #      当日竞价时段取 limit_pool 可能拿不到当日池, 把别日的连板挂上去是静默错数。
    lp = {}
    try:
        _lp_raw = meoz_client.limit_pool_map(date=d8) or {}
        lp = {c: r for c, r in _lp_raw.items()
              if str((r or {}).get("tradedate") or "").replace("-", "") in ("", d8)}
    except Exception as e:                                        # noqa: BLE001
        log.warning("[连续封单] 猫爪 limit_pool 读取失败 date=%s err=%s", iso, str(e)[:120])

    rows = []
    row_yizi = []          # 9:25 涨停的行(「一字」计数 + 封单总额的统计集)
    for code, r in fd.items():
        if not isinstance(r, dict):
            continue
        chg = r.get("auc_pct_chg")
        if chg in (None, "", "-"):
            continue
        try:
            chg = float(chg)
        except (TypeError, ValueError):
            continue

        code = str(code)
        vals = {pt: _f(r.get(fk)) for pt, fk in POINT_FIELDS}
        v15, v20, v25 = vals["9_15"], vals["9_20"], vals["9_25"]

        # 各时点「是否涨停」的判据(刻意**分时点用不同来源**, 这不是不一致, 是实测决定的):
        #   · 9:15 / 9:20: 该时点**没有**独立的涨幅字段可用, 唯一判据 = 该时点封单字段非零
        #     (`fa_0915` / `fa_0920` 官方语义 = 该时刻前最后一笔「匹配价=涨停价」的竞价金额,
        #      非零 ⟺ 该时刻处于涨停; 与契约「非涨停股无封单」自洽)。
        #   · 9:25: 有独立真相源 —— 竞价涨幅 `is_zt_by_change`(分板块 10/20/30%)。
        #     🔴 这里**不能**把 `fa_0925`(主时刻)非零也当作 9:25 涨停: 实测会多认 4 只
        #     (2026-09-29 博纳影业 +6.92% / 中新赛克 +4.08% / 跨境通 +2.27% / 九阳股份 +6.97%
        #      都存在非零 fa_0925 但竞价涨幅远未涨停) ⇒ 「一字」由 7 变 11, 且与模板
        #      逐位对拍过的封单总额口径脱钩。故 9:25 只认涨幅。
        try:
            is_925 = bool(auction_snapshot.is_zt_by_change(code, chg))
        except Exception:                                          # noqa: BLE001
            is_925 = False
        is_920 = bool(v20)
        is_915 = bool(v15)

        # 🔴 2026-09-30 主人明确口径(四步规则): **9:15 / 9:20 / 9:25 任一时点涨停都要展示** ——
        #     9:15 涨停 → 展示; 否则 9:20 涨停 → 展示; 否则 9:25 涨停 → 展示。
        #   旧实现只收「9:25 涨停」(is_zt_by_change(auc_pct_chg)) ⇒ 漏掉大量**盘中炸板**票
        #   (9:15/9:20 封过板、9:25 已回落, 涨幅为负; 模板里的「跨境通 -0.51%」正是此类)。
        if not (is_915 or is_920 or is_925):
            continue

        # 展示层: ①9:25 涨停(最强) ②9:20 涨停回落 ③仅 9:15 涨停 —— 与同页「单日榜」三层排序一致
        layer = 1 if is_925 else (2 if is_920 else 3)
        if is_925:
            row_yizi.append(v25)
        rows.append({
            "code": code,
            "name": str(r.get("name") or ""),
            "board": str(r.get("theme_names_kpl") or ""),
            "chg": round(chg, 2),
            # 连板数: 0/缺失 = 未知(前端不显示该标签; 1 显示「首板」)
            "limitTimes": int(_f((lp.get(code) or {}).get("limit_times"))),
            "layer": layer,
            "v9_15": v15,
            "v9_20": v20,
            "v9_25": v25,
            "_seal25": v25,                 # 汇总口径 = fa_0925l(已与模板逐位对拍)
            "_rank": v25 if layer == 1 else (v20 if layer == 2 else v15),
        })

    # 概念列: 优先取**自采库** `snapshot_bid.board` —— 该列与同页「单日榜」用的是同一列,
    #   且采集时被**开盘啦概念 overlay** 覆盖过, 比猫爪 `theme_names_kpl` 更具体
    #   (实测 2026-09-29 广汽集团: 猫爪=「并购重组」, 模板显示「固态电池/AI眼镜」属开盘啦口径)。
    #   自采库缺该日/该票 → 回落到猫爪 theme_names_kpl。**独立降级**, 失败不影响主数据。
    if rows:
        try:
            conn = database.get_conn()
            try:
                codes = [r["code"] for r in rows]
                cur = conn.execute(
                    "SELECT code, board FROM snapshot_bid "
                    "WHERE date=? AND time_point='9_25' AND code IN (%s)"
                    % ",".join("?" * len(codes)), [iso] + codes)
                dbb = {a: (b or "") for a, b in cur.fetchall()}
            finally:
                conn.close()
            for r in rows:
                b = (dbb.get(r["code"]) or "").strip()
                if b:
                    r["board"] = b
        except Exception as e:                                     # noqa: BLE001
            log.warning("[连续封单] 自采库概念读取失败 date=%s err=%s", iso, str(e)[:120])

    # 三层排序: ①9:25 涨停(按 9:25 封单降序) ②9:20 涨停回落(按 9:20) ③仅 9:15 涨停(按 9:15)
    rows.sort(key=lambda x: (x["layer"], -(x["_rank"] or 0)))
    # 🔴 「一字」与「封单总额」只统计 **9:25 涨停**的行(Σ fa_0925l) —— 这是与模板
    #   四日逐位对拍(104.1/113.8/109.9/106.6亿)定下的口径, 不能被上面放宽的**展示集**带偏:
    #   展示集 ⊋ 统计集(炸板票进展示, 但不计入一字/封单总额)。
    yizi = len(row_yizi)
    seal_total = sum(_f(v) for v in row_yizi)
    for x in rows:
        x.pop("_seal25", None)
        x.pop("_rank", None)
    return {"date": iso, "yizi": yizi, "sealTotal": seal_total,
            "rows": rows[:max(1, int(limit))]}


def build(days: int = 5, end: str = "", limit: int = 200) -> dict:
    """连续 N 日封单(由近到远排列), 并补**环比**趋势(与更早一个交易日比)。"""
    dates = last_trade_days(days, end)
    out = [day_seal(d, limit) for d in dates]
    # 环比: out[i] vs out[i+1](i+1 更早一天); 最早一天无环比 → None
    for i, item in enumerate(out):
        prev = out[i + 1] if i + 1 < len(out) else None
        if not prev or not prev.get("sealTotal"):
            item["diff"] = None
            item["diffPct"] = None
            item["prevDate"] = (prev or {}).get("date")
            continue
        cur_t, prev_t = _f(item.get("sealTotal")), _f(prev.get("sealTotal"))
        item["diff"] = cur_t - prev_t
        item["diffPct"] = round((cur_t - prev_t) / prev_t * 100, 1)
        item["prevDate"] = prev.get("date")
    return {"days": out, "dates": dates}
