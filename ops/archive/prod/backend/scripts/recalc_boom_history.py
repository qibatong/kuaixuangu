# -*- coding: utf-8 -*-
"""
按当前最新逻辑重算某日"竞价爆量"(boom) 日终历史并落库 auction_daily_history。

背景: 8/19 落库时跑的是旧代码(取前60), 现按最新逻辑回填:
  过滤 竞价量比>2 且 竞价成交额>100万, 不限条数, 按量比降序。
仅 boom 可事后重算(数据来自本地 snapshot_bid);
seal/qiangcang 依赖竞价实时接口(收盘后返回空) 无法补算。

步骤:
  1) free_mv 回填: 旧代码采集的 8/19 快照 free_mv=0, 用东财当前 f117(自由流通≈实际流通) 回填,
     f117 无历史值, 用当前值近似; 缺失用 f21 兜底
  2) 按 fetch_bid_boom 当前逻辑(日期参数化) 重算并 INSERT OR REPLACE 到 auction_daily_history

用法(在 service env 下):
  /opt/bid-venv/bin/python scripts/recalc_boom_history.py 2026-08-19 [--no-freemv]
"""
import json
import logging
import sqlite3
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recalc")

from app.core import config
from app.services import fetcher, scorer

DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-19"
BACKFILL_FM = "--no-freemv" not in sys.argv


def backfill_free_mv():
    """用东财当前 f117 回填该日快照的 free_mv(实际流通市值, 元)"""
    conn = sqlite3.connect(config.DB_FILE)
    try:
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT code FROM snapshot_bid WHERE date=?", (DATE,))]
    finally:
        conn.close()
    if not codes:
        log.warning("date=%s 无快照, 跳过 free_mv 回填", DATE)
        return 0
    fmv = {}
    for m in ("hs", "cyb", "kcb"):
        try:
            for s in fetcher.fetch_eastmoney_all(scorer.market_fs([m])):
                code = s.get("f12")
                if not code:
                    continue
                v = scorer.parse_float(s.get("f117")) or scorer.parse_float(s.get("f21"))
                if v:
                    fmv[code] = v
        except Exception as e:
            log.warning("市场 %s 拉取失败 err=%s", m, e)
    conn = sqlite3.connect(config.DB_FILE)
    n = 0
    try:
        for code in codes:
            v = fmv.get(code)
            if not v:
                continue
            conn.execute("UPDATE snapshot_bid SET free_mv=? WHERE date=? AND code=?",
                         (v, DATE, code))
            n += 1
        conn.commit()
    finally:
        conn.close()
    log.info("free_mv 回填 date=%s 完成 %d/%d 只", DATE, n, len(codes))
    return n


def recalc_boom():
    """镜像 fetch_bid_boom 当前逻辑, 但 today 参数化为 DATE"""
    conn = sqlite3.connect(config.DB_FILE)
    try:
        row = conn.execute(
            "SELECT MAX(time_point) FROM snapshot_bid WHERE date=? "
            "AND time_point IN ('9_15','9_20','9_24','9_25')", (DATE,)).fetchone()
        cur_tp = str(row[0]) if row and row[0] else None
        if not cur_tp:
            log.warning("date=%s 无快照时点", DATE)
            return []
        row2 = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE date < ? AND time_point='9_25'",
            (DATE,)).fetchone()
        yest = str(row2[0]) if row2 and row2[0] else None
        if not yest:
            log.warning("date=%s 无昨日 9_25 数据", DATE)
            return []
        today_map = {}
        for code, amt, name, chg, fmv, board in conn.execute(
                "SELECT code, bid_amt, name, bid_change, COALESCE(NULLIF(free_mv,0), float_mv), board "
                "FROM snapshot_bid WHERE date=? AND time_point=?", (DATE, cur_tp)):
            today_map[code] = (amt or 0, name or "", chg or 0, fmv or 0, board or "")
        ymap = {}
        for code, amt in conn.execute(
                "SELECT code, bid_amt FROM snapshot_bid WHERE date=? AND time_point='9_25'",
                (yest,)):
            ymap[code] = amt or 0
    finally:
        conn.close()
    # 实时涨幅: 东财全市场行情 map(此刻收盘 → 即该日收盘涨跌幅)
    spot_map = {}
    try:
        spot_map = fetcher.fetch_spot_quote_map(scorer.market_fs(["hs", "cyb", "kcb"]))
    except Exception as e:
        log.warning("实时涨幅合并失败(降级0) err=%s", e)
    out = []
    for code, (amt, name, chg, fmv, board) in today_map.items():
        ya = ymap.get(code)
        if not ya or amt <= 100:      # 竞价成交额 ≤ 100万(万元=100) 或 昨日无竞价 → 跳过
            continue
        ratio = round(amt / ya, 2)
        if ratio <= 2:                # 竞价量比 ≤ 2 → 跳过
            continue
        bid_turnover = round(amt * 10000 / fmv * 100, 2) if fmv else 0.0
        out.append({"code": code, "name": name,
                    "realChange": (spot_map.get(code) or {}).get("realChange", 0.0),
                    "bidChange": chg,
                    "bidAmt": amt * 10000,
                    "bidRatioYest": ratio,
                    "bidTurnover": bid_turnover,
                    "floatMv": fmv, "board": board,
                    "yestBidAmt": ya * 10000})
    out.sort(key=lambda x: x["bidRatioYest"], reverse=True)
    log.info("重算 boom date=%s 时点=%s 昨日=%s 条数=%d(不限条数)", DATE, cur_tp, yest, len(out))
    return out


def save(lst):
    conn = sqlite3.connect(config.DB_FILE)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO auction_daily_history (date, tab, list, ts) VALUES (?,?,?,?)",
            (DATE, "boom", json.dumps(lst, ensure_ascii=False), int(time.time())))
        conn.commit()
    finally:
        conn.close()
    log.info("已写入 auction_daily_history date=%s tab=boom 条数=%d", DATE, len(lst))


if __name__ == "__main__":
    if BACKFILL_FM:
        backfill_free_mv()
    lst = recalc_boom()
    if lst:
        save(lst)
        print("OK boom", DATE, "count", len(lst))
        print("sample:", json.dumps(lst[0], ensure_ascii=False)[:300])
    else:
        print("EMPTY boom", DATE)
