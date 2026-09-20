# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 每日实时采集器
每天 9:25-9:30 运行：抓取全市场竞价快照，写入 SQLite。
15:00 后运行（加 --label 参数）：回填当日收盘标签（是否涨停、收盘涨幅）。

======================================================================
数据源演进
======================================================================
2026-08-18：改读快选系统 snapshot_bid 表（9_25 全市场快照，权威采集同源），
            仅对 aipick 模型缺的 3 个特征（换手率/价格/最新涨幅）从东财轻量补拉。

2026-09-20（主人指令「东财数据换成猫爪数据」）：**东财 → 猫爪**。
  · 主路径 fetch_from_kuaixuan：仍读快选 snapshot_bid（权威同源，不动的部分）
  · 补字段路径：**东财 clist → 猫爪 screening**（见 meoz_source.py）
  · --legacy-eastmoney 参数仍保留：回退到「完全自拉东财」的旧逻辑

  ★ 为什么换：主人铁律「能通过猫爪获取的，都用猫爪的数据，自己不计算」。
    实测猫爪 screening 不传 symbols 即返回全市场 5553 只，且
    `turnover_rate_f`（实际换手率）是**自由流通口径**，与本项目全局口径一致
    （东财 f8 是流通股本口径，系统性偏小）。
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import init_db, upsert_features, update_labels, today  # noqa
import meoz_source as MZ  # noqa  猫爪数据源(2026-09-20 替换东财)

# 快选系统快照库(服务器): 9_25 时点全市场竞价快照(权威)
KX_DB = "/opt/kuaixuan/kuaixuan.db"


def fetch_market(trade_date=None, historical=None):
    """拉取全市场竞价快照（**猫爪 screening**，2026-09-20 由东财改造而来）。

    trade_date: None → 最近交易日；"YYYY-MM-DD" → 该日
    historical: 是否历史回溯模式（决定 yesterday_chg 语义）
        · None（默认）→ **自动判定**：trade_date 早于今天 = 历史模式
        · 历史模式：yesterday_chg 取**前一交易日**收盘涨幅（防标签泄漏）
        · 实时模式：yesterday_chg 取当日 pct_chg（此时尚未收盘，即"当时涨幅"，无泄漏）

    ★ 为什么区分（2026-09-20 踩坑）：对历史日期，猫爪 `pct_chg` 返回的是**该日收盘**涨跌幅，
      与标签同源 → 会把 AUC 虚高到 0.9998。实时采集时则不存在该问题。

    返回: list[dict]（aipick features 行，已单位换算）；失败返回 []。
    """
    rows_map = MZ.fetch_market(trade_date)
    if not rows_map:
        return []
    d = trade_date or today()
    if historical is None:
        historical = bool(trade_date) and str(trade_date) != today()
    pchg = MZ.prev_chg_map(d, quiet=True) if historical else None
    return MZ.to_features(rows_map, d, prev_chg_map=pchg)


def fetch_extra_fields(trade_date=None):
    """拉取「价格 / 最新涨幅 / 实际换手率」三字段（猫爪 screening）。

    注：猫爪 screening 一次即返回全量字段，本函数与 fetch_market 同源同调用，
    单独保留是为了兼容旧调用点（fetch_from_kuaixuan 的补字段逻辑）。
    返回: {code: {"price","chg","turnover"}}
    """
    rows_map = MZ.fetch_market(trade_date)
    out = {}
    for code, s in rows_map.items():
        out[code] = {
            "price": s.get("close"),
            "chg": s.get("pct_chg"),
            "turnover": s.get("turnover_rate_f"),   # ★ 自由流通口径实际换手率
        }
    return out


def fetch_market_eastmoney():
    """【回退路径】东财 push2dycalc 自拉全市场竞价快照（--legacy-eastmoney 用）。

    保留原因：猫爪不可用（未配 apikey / 服务故障）时的兜底。
    ⚠️ 注意口径差异：东财 f8 是流通股本换手率，猫爪 turnover_rate_f 是自由流通口径。
    """
    import json
    import time
    import urllib.request

    API = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
    UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
    FIELDS = ("f2,f3,f4,f5,f6,f8,f10,f12,f14,f17,f18,f20,f21,f615,f616,f617,"
              "f618,f630,f100,f102,f103")
    all_rows = []
    for fs in ["m:1+t:2", "m:0+t:6", "m:0+t:80", "m:1+t:23"]:
        pn = 1
        while True:
            url = (f"{API}?fs={fs}&fltt=2&invt=2&fields={FIELDS}"
                   f"&fid=f3&po=1&pn={pn}&pz=500&np=1&ut={UT}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            except Exception as e:
                print(f"  [东财 {fs}] 拉取失败: {e}")
                break
            diff = (data.get("data") or {}).get("diff") or []
            if not diff:
                break
            all_rows.extend(diff)
            if pn * 500 >= ((data.get("data") or {}).get("total") or 0):
                break
            pn += 1
            time.sleep(0.3)
    return all_rows


def fetch_from_kuaixuan(trade_date):
    """从快选系统 snapshot_bid 读 9_25 全市场竞价快照(权威采集, 同源)
    覆盖特征: bid_change / bid_amount / circ_mv / name;
    缺: 换手率/价格/昨涨 → fetch_extra_fields() 东财补拉"""
    rows = []
    if not os.path.exists(KX_DB):
        print(f"⚠️ 快选快照库不存在: {KX_DB}, 回退猫爪自拉")
        return None
    try:
        conn = sqlite3.connect(KX_DB)
        cur = conn.execute(
            """SELECT code, name, bid_change, bid_amt, float_mv FROM snapshot_bid
               WHERE date=? AND time_point='9_25'""", (trade_date,))
        snap = {r[0]: r for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        print(f"⚠️ 读快选快照库失败: {e}, 回退猫爪自拉")
        return None
    if not snap:
        print(f"⚠️ 快选 {trade_date} 9_25 快照为空, 回退猫爪自拉")
        return None
    # 补字段来源：猫爪 screening（2026-09-20 由东财改造而来）
    extra = fetch_extra_fields(trade_date)
    for code, (_, name, bid_change, bid_amt, float_mv) in snap.items():
        ex = extra.get(code, {})
        rows.append({
            "trade_date": trade_date,
            "code": code,
            "name": name or "",
            "bid_change": round(float(bid_change or 0), 2),
            "bid_amount": round(float(bid_amt or 0), 1),        # 快选 bid_amt 单位万元(与aipick一致)
            "bid_volume": None,
            "bid_turnover": _sf(ex.get("turnover")),            # 猫爪 turnover_rate_f(自由流通口径)
            "warn_type": 0,
            "price": _sf(ex.get("price")),
            "circ_mv": round(float(float_mv or 0) / 1e8, 2),    # 元 → 亿
            # 实时场景(9:25-9:30, 尚未收盘) pct_chg 即"当时涨幅", 无标签泄漏;
            # 历史回溯场景必须改用前一交易日涨幅 —— 见 meoz_source.prev_chg_map()
            "yesterday_chg": round(_sf(ex.get("chg")), 2),
            "industry": "",
            "concept": "",
        })
    print(f"  [快选snapshot_bid {trade_date} 9_25] 读 {len(snap)} 只, 猫爪补字段 {len(extra)} 只")
    return rows


def _sf(v, default=0.0):
    try:
        if v is None or v in ("-", ""):
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def to_features(stocks, trade_date):
    """【回退路径】东财原始行 → 特征行（--legacy-eastmoney 用）。

    ⚠️ 2026-09-20 起主路径已改猫爪（meoz_source.to_features）。
       本函数仅在东财回退时使用，字段语义与猫爪版一致：
         f615→bid_change(竞价涨幅) / f616→bid_amount / f8→bid_turnover
         f2→price / f21→circ_mv / f3→yesterday_chg(最新涨幅)
       ★ 口径差异提醒：东财 f8 是**流通股本**换手率，猫爪 turnover_rate_f 是**自由流通**口径。
    """

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
        rows.append({
            "trade_date": trade_date,
            "code": s.get("f12"),
            "name": s.get("f14"),
            "bid_change": round(bid_change, 2),
            "bid_amount": round(bid_amt / 10000, 1),      # 万元
            "bid_volume": s.get("f617"),
            "bid_turnover": round(safe_float(s.get("f8")), 2),
            "warn_type": int(safe_float(s.get("f630"))),
            "price": s.get("f2"),
            "circ_mv": round(mv, 2),
            "yesterday_chg": round(yesterday_chg, 2),
            "industry": s.get("f100") or "",
            "concept": s.get("f103") or "",
        })
    return rows


def label_today(trade_date, legacy=False):
    """收盘后回填标签：是否涨停（主板 ≥9.8%、创业板/科创板 ≥19.8%）、收盘涨幅。

    主路径走猫爪（meoz_source.to_labels）；legacy=True 时走东财自拉。
    """
    if legacy:
        stocks = fetch_market_eastmoney()
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
            rows.append({
                "code": code,
                "is_limit_up": 1 if chg >= MZ.limit_pct(code) else 0,
                "close_chg": round(chg, 2),
            })
    else:
        rows_map = MZ.fetch_market(trade_date)
        if not rows_map:
            print(f"⚠️ 猫爪取数为空, 无法回填标签 @ {trade_date}")
            return
        rows = MZ.to_labels(rows_map, trade_date)

    update_labels(trade_date, rows)
    n_up = sum(r["is_limit_up"] for r in rows)
    print(f"已回填 {len(rows)} 条标签 @ {trade_date} (涨停 {n_up} 只, "
          f"{n_up / max(1, len(rows)) * 100:.2f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", action="store_true", help="收盘后回填标签")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD")
    parser.add_argument("--legacy-eastmoney", action="store_true",
                        help="回退旧逻辑: 完全自拉东财(默认走猫爪为主)")
    args = parser.parse_args()

    init_db()
    d = args.date or today()

    if args.label:
        label_today(d, legacy=args.legacy_eastmoney)
    else:
        if args.legacy_eastmoney:
            rows = to_features(fetch_market_eastmoney(), d)
        else:
            # 主路径: 快选快照(权威同源) → 猫爪自拉
            rows = fetch_from_kuaixuan(d) or fetch_market(d)
        if not rows:
            print(f"⚠️ {d} 未取到任何行情数据(猫爪不可用?) → 跳过采集")
            sys.exit(1)
        upsert_features(rows)
        print(f"已采集 {len(rows)} 只竞价快照 @ {d}  [{datetime.now().strftime('%H:%M:%S')}]")
