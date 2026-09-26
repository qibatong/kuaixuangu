"""
一次性回填: 开盘啦 doc30 竞价涨停委买额历史接口 -> snapshot_bid.bid_buy_amt
字段映射: row[4]=bidSealAmt(元)
运行: 在 service env (含 SSL_CERT_FILE) 下, 单次补全历史日期
"""
import json, logging, sys, urllib.parse, urllib.request, ssl, sqlite3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")

# 复用 kpl 配置
from app.core import config
from app.services import kpl


def resolve_date(d):
    """对齐到 snapshot_bid 已存在的最近交易日"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE date <= ?", (d,)).fetchone()
        return str(row[0]) if row and row[0] else d
    finally:
        conn.close()


def parse_row(row):
    """开盘啦行: [code, name, ?, realChange, bidSealAmt, bidChange, netAmt, turnover, bidAmt, ...]
    参考 _parse_bid_seal: row[4]=涨停委买额(元), row[11]=板块/概念"""
    if not isinstance(row, list) or len(row) < 9:
        return None
    try:
        return {
            "code": str(row[0]).zfill(6),
            "name": str(row[1] or ""),
            "real_change": float(row[3]) if row[3] is not None else None,
            "bid_seal_amt": float(row[4]) if row[4] is not None else None,
            "bid_change": float(row[5]) if row[5] is not None else None,
            "board": str(row[11]) if len(row) > 11 else "",
        }
    except (IndexError, ValueError, TypeError):
        return None


def backfill_one(date):
    """回填某天 9_15/9_20/9_25 各时点的 bidSealAmt
    doc30 接口是该日涨停委买榜快照, 涨停状态 ≠ 9_15 时点涨停 → 三时点榜某层可能用不上
    但写入后可让所有时点的 snapshot_bid 都有真实封单额(只要涨停过)"""
    log.info("回填 date=%s", date)
    d = kpl.fetch_kpl_doc30(Order="1", st="20", c="HisHomeDingPan", Type="4",
                            PidType="0", Date=date, apiv="w41")
    if not d:
        return 0
    info = d.get("info") or []
    if not info:
        log.warning("date=%s info 空 (保留期外或周末)", date)
        return 0
    parsed = [r for r in (parse_row(row) for row in info) if r]
    log.info("date=%s 解析 %d 条", date, len(parsed))

    # 写入: 给所有时点(9_15/9_20/9_25) 都设置 bid_buy_amt (若是涨停股)
    from app.db import database
    def _db():
        return database.get_conn() if hasattr(database, 'get_conn') else sqlite3.connect(config.DB_FILE)
    def _close(c):
        try: c.close()
        except: pass
    conn = _db()
    n = 0
    try:
        for r in parsed:
            seal = r["bid_seal_amt"] or 0
            if seal <= 0 and not r.get("board"):
                continue
            for tp in ("9_15", "9_20", "9_25"):
                cur = conn.execute(
                    "SELECT bid_change FROM snapshot_bid WHERE date=? AND time_point=? AND code=?",
                    (date, tp, r["code"])).fetchone()
                if not cur:
                    continue
                # 关键修复(2026-08-16): 封单额必须只写给"该时点涨停"的股票!
                # 之前不检查时点涨幅, 把 doc30 当日唯一一份封单额写给了所有时点,
                # 导致: ①三时点封单完全相同 ②已开板/大跌的股票仍挂着封单(用户反馈"封单额和标的不对应")
                bid_change = cur[0]
                is_zt = (
                    bid_change >= 19.9 if r["code"][:2] in ("30", "68")
                    else bid_change >= 29.9 if r["code"][:1] in ("8", "4")
                    else bid_change >= 9.9
                )
                sets, vals = [], []
                if seal > 0 and is_zt:
                    sets.append("bid_buy_amt=?")
                    vals.append(seal)
                if r.get("board"):
                    sets.append("board=?")
                    vals.append(r["board"])
                if not sets:
                    continue
                conn.execute(
                    "UPDATE snapshot_bid SET %s WHERE date=? AND time_point=? AND code=?" % ",".join(sets),
                    vals + [date, tp, r["code"]])
                n += 1
        conn.commit()
    finally:
        _close(conn)
    log.info("date=%s 更新 %d 行", date, n)
    return n


if __name__ == "__main__":
    # 默认回填 8/10-8/14 (最近 5 个交易日, 因为 doc30 历史保留期可能短)
    dates = sys.argv[1:] or ["2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11", "2026-08-10", "2026-08-07", "2026-08-06", "2026-08-05"]
    total = 0
    for d in dates:
        total += backfill_one(d)
    log.info("DONE total=%d", total)
