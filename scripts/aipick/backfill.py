# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 历史数据回补（近似特征，多线程加速）
用新浪日线近似重建竞价特征：
- 竞价涨幅 ≈ (开盘价/昨收 - 1)*100
- 竞价金额 ≈ 开盘价 × 全天量 × 15%（开盘放量占比近似）
- 流通市值 ≈ 收盘价 × outstanding_share / 1e8
- 标签：当日是否涨停（收盘涨幅 >= 涨停价）
注意：这是"近似回测"，真实竞价数据由 collector.py 每天积累。
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import init_db, upsert_features, update_labels, today  # noqa

import akshare as ak


def get_stock_list():
    """获取A股代码列表（沪深主板+创业板，排除科创板/北交所）"""
    try:
        import json
        import urllib.request
        url = "https://push2dycalc.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2&fields=f12,f14"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        diff = data.get("data", {}).get("diff", [])
        out = []
        for s in diff:
            c = str(s["f12"])
            if c.startswith(("60", "00", "30")):
                out.append((c, s["f14"]))
        return out
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return []


def sina_symbol(code):
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def process_one(code, name, start, end):
    """回补单只股票，返回行数"""
    try:
        df = ak.stock_zh_a_daily(symbol=sina_symbol(code), start_date=start, end_date=end, adjust="")
    except Exception:
        return 0
    if df is None or len(df) < 2:
        return 0

    df = df.reset_index(drop=True)
    prev_close = df["close"].shift(1)
    bid_change = (df["open"] / prev_close - 1) * 100
    # 竞价金额近似：开盘价 × 昨日成交量 × 0.15（只用竞价时刻已知的昨日量，避免未来函数）
    prev_volume = df["volume"].shift(1)
    bid_amount_wan = df["open"] * prev_volume * 0.15 / 10000
    # 流通市值（用昨日收盘计算，竞价时刻已知）
    circ_mv = prev_close * df["outstanding_share"].shift(1) / 1e8
    close_chg = (df["close"] / prev_close - 1) * 100
    limit_pct = 19.8 if code.startswith("30") else 9.8
    is_limit = (close_chg >= limit_pct).astype(int)
    # 竞价换手率 = 竞价金额 / 流通市值（全部用竞价时刻已知数据，避免未来函数）
    bid_turnover = bid_amount_wan * 10000 / circ_mv / 1e8 * 100

    count = 0
    for idx in range(1, len(df)):
        d = str(df.loc[idx, "date"])[:10]
        upsert_features([{
            "trade_date": d, "code": code, "name": name,
            "bid_change": round(float(bid_change[idx]), 2),
            "bid_amount": round(float(bid_amount_wan[idx]), 1),
            "bid_volume": None, "bid_turnover": round(float(bid_turnover[idx]), 2),
            "warn_type": 0, "price": round(float(df.loc[idx, "open"]), 2),
            "circ_mv": round(float(circ_mv[idx]), 2),
            "yesterday_chg": round(float(close_chg[idx - 1]), 2),
            "industry": "", "concept": "",
        }])
        update_labels(d, [{"code": code, "is_limit_up": int(is_limit[idx]), "close_chg": round(float(close_chg[idx]), 2)}])
        count += 1
    return count


def backfill(days=90, max_stocks=800, workers=6):
    init_db()
    end = today()
    start = (datetime.now() - timedelta(days=int(days * 1.6))).strftime("%Y-%m-%d")
    stocks = get_stock_list()
    if not stocks:
        print("无法获取股票列表，终止")
        return
    stocks = stocks[:max_stocks]
    print(f"共 {len(stocks)} 只股票（沪深主板+创业板），回补近 {days} 交易日，{workers} 线程")

    total = 0
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_one, c, n, start, end): (c, n) for c, n in stocks}
        for fut in as_completed(futures):
            c, n = futures[fut]
            try:
                cnt = fut.result()
            except Exception:
                cnt = 0
            total += cnt
            done += 1
            if done % 100 == 0:
                el = time.time() - t0
                print(f"  进度 {done}/{len(stocks)}，累计 {total} 行，用时 {el:.0f}s")

    el = time.time() - t0
    print(f"回补完成：{len(stocks)} 只 → {total} 行特征数据，用时 {el:.0f}s（{el/60:.1f} 分钟）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--max-stocks", type=int, default=800)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    backfill(days=args.days, max_stocks=args.max_stocks, workers=args.workers)
