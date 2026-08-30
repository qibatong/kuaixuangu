# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 每日实时采集器
每天 9:25-9:30 运行：抓取全市场竞价快照（真实竞价数据 f615/f616/f617），写入 SQLite。
15:00 后运行（加 --label 参数）：回填当日收盘标签（是否涨停、收盘涨幅）。

2026-08-18 改造(主人要求复用快选系统采集数据):
默认改读快选系统 snapshot_bid 表(9_25 全市场快照, 权威采集同源),
仅对 aipick 模型缺的 3 个特征(换手率f8/价格f2/最新涨幅f3)从东财轻量补拉一次。
--legacy-eastmoney 参数可回退旧逻辑(完全自拉东财)。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import init_db, upsert_features, update_labels, today  # noqa

API = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
FIELDS = "f2,f3,f4,f5,f6,f8,f10,f12,f14,f17,f18,f20,f21,f615,f616,f617,f618,f630,f100,f102,f103"
# 补拉字段: f2价格 / f3最新涨幅 / f8换手率
EXTRA_FIELDS = "f2,f3,f8,f12"

# 快选系统快照库(服务器): 9_25 时点全市场竞价快照(权威)
KX_DB = "/opt/kuaixuan/kuaixuan.db"


def fetch_market():
    """拉取沪深A股（主板+创业板+科创板）竞价快照(旧逻辑, 完全自拉)"""
    all_rows = []
    fs_parts = ["m:1+t:2", "m:0+t:6", "m:0+t:80", "m:1+t:23"]
    for fs in fs_parts:
        pn = 1
        while True:
            url = f"{API}?fs={fs}&fltt=2&invt=2&fields={FIELDS}&fid=f3&po=1&pn={pn}&pz=500&np=1&ut={UT}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            except Exception as e:
                print(f"  [{fs}] 拉取失败: {e}")
                break
            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break
            all_rows.extend(diff)
            total = data.get("data", {}).get("total", 0)
            if pn * 500 >= total:
                break
            pn += 1
            time.sleep(0.3)
    return all_rows


def fetch_extra_fields():
    """东财补拉 换手率f8/价格f2/最新涨幅f3(全市场一次, 仅3字段)"""
    out = {}
    fs_parts = ["m:1+t:2", "m:0+t:6", "m:0+t:80", "m:1+t:23"]
    for fs in fs_parts:
        pn = 1
        while True:
            url = f"{API}?fs={fs}&fltt=2&invt=2&fields={EXTRA_FIELDS}&fid=f3&po=1&pn={pn}&pz=500&np=1&ut={UT}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            except Exception as e:
                print(f"  [extra {fs}] 拉取失败: {e}")
                break
            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break
            for s in diff:
                out[s.get("f12")] = {"price": s.get("f2"), "chg": s.get("f3"), "turnover": s.get("f8")}
            total = data.get("data", {}).get("total", 0)
            if pn * 500 >= total:
                break
            pn += 1
            time.sleep(0.3)
    return out


def fetch_from_kuaixuan(trade_date):
    """从快选系统 snapshot_bid 读 9_25 全市场竞价快照(权威采集, 同源)
    覆盖特征: bid_change / bid_amount / circ_mv / name;
    缺: 换手率/价格/昨涨 → fetch_extra_fields() 东财补拉"""
    rows = []
    if not os.path.exists(KX_DB):
        print(f"⚠️ 快选快照库不存在: {KX_DB}, 回退东财自拉")
        return None
    try:
        conn = sqlite3.connect(KX_DB)
        cur = conn.execute(
            """SELECT code, name, bid_change, bid_amt, float_mv FROM snapshot_bid
               WHERE date=? AND time_point='9_25'""", (trade_date,))
        snap = {r[0]: r for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        print(f"⚠️ 读快选快照库失败: {e}, 回退东财自拉")
        return None
    if not snap:
        print(f"⚠️ 快选 {trade_date} 9_25 快照为空, 回退东财自拉")
        return None
    extra = fetch_extra_fields()
    for code, (_, name, bid_change, bid_amt, float_mv) in snap.items():
        ex = extra.get(code, {})
        # bid_turnover: 竞价换手率 = 竞价金额 / 流通市值 × 100
        # - 东财 f8 (ex["turnover"]) 是昨日全天换手率，不可用于竞价模型特征
        #   → 之前误用造成多日 22.53% 固定假值，严重拉低模型概率
        # bid_amt 单位判断: 默认万元；若 bid_amt > 1e7 (万元) 即 > 1e11 元, 明显超过市值，说明单位为元，自动转为万元
        bid_amt_v = float(bid_amt or 0)
        if float_mv and bid_amt_v > float_mv / 10.0:
            # bid_amt 单位为元(异常大)，转万元
            bid_amt_v = bid_amt_v / 1e4
        circ_mv_yi = round(float(float_mv or 0) / 1e8, 2)    # 元 → 亿
        if float_mv and float_mv > 0:
            turnover = round(bid_amt_v * 10000 / float(float_mv) * 100, 4)
        else:
            # 市值缺失兜底: 放弃 f8 昨日值, 用 0 (竞价未成交更合理)
            turnover = 0.0
        rows.append({
            "trade_date": trade_date,
            "code": code,
            "name": name or "",
            "bid_change": round(float(bid_change or 0), 2),
            "bid_amount": round(bid_amt_v, 1),                    # 万元
            "bid_volume": None,
            "bid_turnover": turnover,
            "warn_type": 0,
            "price": _sf(ex.get("price")),
            "circ_mv": circ_mv_yi,
            "yesterday_chg": round(_sf(ex.get("chg")), 2),       # 最新涨幅(昨收盘→今日竞价)
            "industry": "",
            "concept": "",
        })
    print(f"  [快选snapshot_bid {trade_date} 9_25] 读 {len(snap)} 只, 东财补拉 {len(extra)} 只")
    return rows


def _sf(v, default=0.0):
    try:
        if v is None or v in ("-", ""):
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def to_features(stocks, trade_date):
    """原始行情 → 特征行（与工具一致的字段计算）"""

    def safe_float(v, default=0.0):
        try:
            if v is None or v == "-" or v == "":
                return default
            return float(v)
        except (ValueError, TypeError):
            return default

    rows = []
    for s in stocks:
        f615 = s.get("f615")
        bid_change = safe_float(f615) if f615 is not None else safe_float(s.get("f3"))
        f616 = s.get("f616")
        bid_amt = safe_float(f616) if f616 not in (None, "-", "") else safe_float(s.get("f6"))
        mv = safe_float(s.get("f21")) / 1e8
        yesterday_chg = safe_float(s.get("f3"))
        # bid_turnover: 竞价换手率 = 竞价金额 / 流通市值 × 100
        # 东财 f8 为昨日全天换手率，不能用作竞价特征
        bid_amt_wan = bid_amt / 10000 if bid_amt else 0.0
        turnover_fallback = round(safe_float(s.get("f8")), 2)  # 仍保留以备 mv=0 极端情况(竞价未成交)
        if mv > 0 and bid_amt_wan > 0:
            bid_turn = round(bid_amt_wan / mv / 100.0 * 100.0, 4)  # bid_amt_wan/(mv亿*1e8) * 1e4 * 100 = bid_amt_wan/mv/100 *100
        else:
            bid_turn = turnover_fallback if turnover_fallback and turnover_fallback<=50 else 0.0
        rows.append({
            "trade_date": trade_date,
            "code": s.get("f12"),
            "name": s.get("f14"),
            "bid_change": round(bid_change, 2),
            "bid_amount": round(bid_amt_wan, 1),                 # 万元
            "bid_volume": s.get("f617"),
            "bid_turnover": bid_turn,
            "warn_type": int(safe_float(s.get("f630"))),
            "price": s.get("f2"),
            "circ_mv": round(mv, 2),
            "yesterday_chg": round(yesterday_chg, 2),
            "industry": s.get("f100") or "",
            "concept": s.get("f103") or "",
        })
    return rows


def label_today(trade_date):
    """收盘后拉取收盘数据，回填标签：是否涨停（收盘涨幅>=9.8 或科创板/创业板>=19.8）、收盘涨幅"""
    stocks = fetch_market()
    rows = []

    def safe_float(v, default=0.0):
        try:
            if v is None or v == "-" or v == "":
                return default
            return float(v)
        except (ValueError, TypeError):
            return default

    for s in stocks:
        chg = safe_float(s.get("f3"))
        code = s.get("f12")
        is_cyb = code.startswith("30")
        is_kcb = code.startswith("68")
        limit_pct = 19.8 if (is_cyb or is_kcb) else 9.8
        rows.append({
            "code": code,
            "is_limit_up": 1 if chg >= limit_pct else 0,
            "close_chg": round(chg, 2),
        })
    update_labels(trade_date, rows)
    print(f"已回填 {len(rows)} 条标签 @ {trade_date}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", action="store_true", help="收盘后回填标签")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD")
    parser.add_argument("--legacy-eastmoney", action="store_true",
                        help="回退旧逻辑: 完全自拉东财(默认读快选snapshot_bid)")
    args = parser.parse_args()

    init_db()
    d = args.date or today()

    if args.label:
        label_today(d)
    else:
        if args.legacy_eastmoney:
            stocks = fetch_market()
            rows = to_features(stocks, d)
        else:
            rows = fetch_from_kuaixuan(d) or to_features(fetch_market(), d)
        upsert_features(rows)
        print(f"已采集 {len(rows)} 只竞价快照 @ {d}  [{datetime.now().strftime('%H:%M:%S')}]")
