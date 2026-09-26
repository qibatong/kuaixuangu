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
    实测猫爪 screening 不传 symbols 即返回全市场 5553 只。

2026-09-25（字段修正）：`bid_turnover` 由 `turnover_rate_f` 改为 **`auc_turnover`**。
  · `turnover_rate_f` 是**该日累计**换手率 —— 9:25 取=竞价累计(≈0.006)、盘后/回溯取=全天值(≈2.5)，
    **同一列混两种时点语义**，导致线上报告与回补报告量纲差 250~400 倍、
    且 08-18~08-27 回补段写入的全天值构成**标签泄漏**。
  · `auc_turnover` 是**竞价换手率**（auc_ = auction，与 auc_pct_chg/auc_amt 同族），
    竞价结束即定格、时点稳定，且 = auc_amt/free_float_mv×100（自由流通口径）。
  · 详见 meoz_source.py「铁律 1」与 lgbm-deploy/DIAGNOSIS-bid_turnover.md。

2026-09-25（第二处标签泄漏修复）：`fetch_from_kuaixuan()` 的 `yesterday_chg` 此前**不分
  实时/回溯**，一律塞猫爪当日 `pct_chg`。对历史日期而言那是**收盘**涨幅（与标签同源）
  ⇒ `predict_daily.py --date <过去某日>` / `backfill` 重算出的历史报告全部含泄漏。
  实测：重算 2026-09-24 得 6 只，而当日 9:25 线上跑出的是 9 只。
  现按 `fetch_market()` 同口径分模式：历史回溯改用 `meoz_source.prev_chg_map()` 取前一交易日。
  🔴 实时链路（trade_date == 今天）行为不变。

2026-09-25（日历正门）：脚本侧接入 `trade_calendar.py`（桥接 backend 的官方休市表）。
  背景：清掉幽灵 snapshot_bid 行后，休市日的取数链会落到**东财兜底**，
  而东财休市日仍返回陈旧价格 ⇒ `price>0` 数据护栏失效、照样落库。
  现在**采集前先过日历正门**，数据护栏保留为第二道防线。
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import init_db, upsert_features, update_labels, today  # noqa
import meoz_source as MZ  # noqa  猫爪数据源(2026-09-20 替换东财)
import trade_calendar as _tc  # noqa  交易日历桥接(单一事实来源 = backend/app/core/trade_calendar.py)

# 快选系统快照库(服务器): 9_25 时点全市场竞价快照(权威)
KX_DB = "/opt/kuaixuan/kuaixuan.db"


def fetch_market(trade_date=None, historical=None):
    """拉取全市场竞价快照（**猫爪 screening**，2026-09-20 由东财改造而来）。

    trade_date: None → 最近交易日；"YYYY-MM-DD" → 该日
    historical: 是否历史回溯模式（决定 yesterday_chg 语义）
        · None（默认）→ **自动判定**：trade_date 早于今天 = 历史模式
        · 历史模式：yesterday_chg 取**前一交易日**收盘涨幅（防标签泄漏）
        · 实时模式：yesterday_chg 取当日 pct_chg —— 注意时点：9:25 **集合竞价已结束、开盘价已
          产生（即"已开盘"）**，9:30 连续竞价尚未开始，此刻全市场唯一存在的价格就是开盘价，
          故该值**必然 ≡ 竞价涨幅**（不是"尚未开盘"这种含混说法），与 `bid_change` 同信息。

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
    # 市值取 screening.free_float_mv（**自由流通口径**，同源、零额外请求）—— 2026-09-26 主人指令
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
            "turnover": MZ.to_bid_turnover(s),   # ★ 竞价换手率(auc_turnover, 自由流通) — 铁律 1
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


def fetch_from_kuaixuan(trade_date, historical=None):
    """从快选系统 snapshot_bid 读 9_25 全市场竞价快照(权威采集, 同源)
    覆盖特征: bid_change / bid_amount / circ_mv(**自由流通市值口径**) / name;
    缺: 换手率/价格/昨涨 → fetch_extra_fields() 猫爪补拉

    historical: 是否历史回溯模式（决定 `yesterday_chg` 语义），与 `fetch_market()` 同口径
        · None（默认）→ **自动判定**：trade_date 早于今天 = 历史模式
        · 历史模式：`yesterday_chg` 取**前一交易日**收盘涨幅（防标签泄漏）
        · 实时模式：`yesterday_chg` 取当日 pct_chg —— 9:25 集合竞价已撮合出开盘价（=已开盘），
          9:30 连续竞价未开始，此刻唯一价格＝开盘价 ⇒ 该值**必然 ≡ 竞价涨幅**，与 `bid_change` 同信息。

    ★ 2026-09-25 修标签泄漏：原先无论实时/回溯都塞当日 `pct_chg`，而它对该历史日期
      已是**收盘**涨幅（与标签同源）⇒ 任何"重算历史报告"的动作（`predict_daily.py --date`、
      `backfill`）都会产出泄漏报告。实测：重算 09-24 得 6 只，而当日 9:25 线上跑出的是 9 只。
      修法与 `fetch_market()`/`backfill.py::backfill_one()` 完全一致（也见 `meoz_source.prev_chg_map`）。
      🔴 实时链路行为**不变**（今天仍然用当日涨幅），故不影响线上 9:25 预测。
    """
    rows = []
    if not os.path.exists(KX_DB):
        print(f"⚠️ 快选快照库不存在: {KX_DB}, 回退猫爪自拉")
        return None
    try:
        conn = sqlite3.connect(KX_DB)
        cur = conn.execute(
            """SELECT code, name, bid_change, bid_amt,
                      CASE WHEN free_mv > 0 THEN free_mv ELSE float_mv END
               FROM snapshot_bid WHERE date=? AND time_point='9_25'""", (trade_date,))
        snap = {r[0]: r for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        print(f"⚠️ 读快选快照库失败: {e}, 回退猫爪自拉")
        return None
    if not snap:
        print(f"⚠️ 快选 {trade_date} 9_25 快照为空, 回退猫爪自拉")
        return None
    # 历史/实时模式判定（与 fetch_market 同源同口径）
    if historical is None:
        hist = bool(trade_date) and str(trade_date) != today()
    else:
        hist = bool(historical)
    pchg = MZ.prev_chg_map(trade_date, quiet=True) if hist else {}
    if hist:
        print(f"  [快选snapshot_bid {trade_date}] 历史回溯模式 → yesterday_chg 取前一交易日"
              f"（{len(pchg)} 只有值，缺失置 0），不复用当日涨幅")
    # 补字段来源：猫爪 screening（2026-09-20 由东财改造而来）
    extra = fetch_extra_fields(trade_date)
    for code, (_, name, bid_change, bid_amt, mv) in snap.items():
        ex = extra.get(code, {})
        rows.append({
            "trade_date": trade_date,
            "code": code,
            "name": name or "",
            "bid_change": round(float(bid_change or 0), 2),
            "bid_amount": round(float(bid_amt or 0), 1),        # 快选 bid_amt 单位万元(与aipick一致)
            "bid_volume": None,
            "bid_turnover": _sf(ex.get("turnover")),            # 猫爪 auc_turnover(竞价换手率·自由流通) — 铁律 1
            "warn_type": 0,
            "price": _sf(ex.get("price")),
            # 市值 = **自由流通市值**（free_mv 优先，float_mv 兜底）—— 2026-09-26 主人指令：
            #   与线上 scorer 的市值分档口径对齐。旧版只取 float_mv(流通市值)，致
            #   模型学的市值与线上打分用的差 1.5~2.4 倍（free/circ 比值中位 0.63）。
            "circ_mv": round(float(mv or 0) / 1e8, 2),          # 元 → 亿
            # 实时场景: 9:25 集合竞价已撮合出开盘价（= 已开盘），但 9:30 连续竞价尚未开始，
            #           此刻市场上唯一存在的价格就是开盘价 ⇒ 读到的 pct_chg 必然 ≡ **竞价涨幅**。
            #           这不是泄漏（9:25 拿不到收盘价），但**与 bid_change 是同一信息**，
            #           故 2026-09-25 起该列**退出模型特征集**（6 维 → 5 维），仅留痕。
            # 历史回溯场景: 该日 pct_chg 已是**收盘**涨幅（与标签同源）⇒ 必须改用**前一交易日**涨幅，
            #           否则任何重算/backfill（predict_daily.py --date、backfill）都会产出泄漏报告。
            "yesterday_chg": round(_sf(pchg.get(code)) if hist else _sf(ex.get("chg")), 2),
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
         f2→price / f117→circ_mv(自由流通市值) / f3→yesterday_chg(最新涨幅)
       ★ 口径差异提醒：东财 f8 是**流通股本**换手率，猫爪 turnover_rate_f 是**自由流通**口径。
       ★ 2026-09-26 口径统一：市值改取 **f117(自由流通市值)**，f21(流通市值) 兜底。
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
        # 市值 = 自由流通市值(f117) 优先 → f21(流通市值) 兜底（2026-09-26 口径统一）
        mv = (safe_float(s.get("f117")) or safe_float(s.get("f21"))) / 1e8
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
        # === 日期正门 (2026-09-25 第二轮复盘新增) ===
        # 为什么光有下面的"数据侧护栏"不够:
        #   删掉幽灵 snapshot_bid 行后, 取数链变成
        #     `快选快照空 → 猫爪 screening 空 → **东财兜底**`,
        #   而东财在休市日**仍返回上一交易日的陈旧价格**(price 非 0)
        #   ⇒ `price>0` 占比护栏判不出来(实测 2026-09-25 占比≈100%、落库 5211 行)。
        #   backend 调度器的日历门禁只挡自动链路, 手动 `--date` 重算仍会漏。
        # ⇒ 采集前先过**日历正门**; 数据护栏保留为第二道防线。日历见 trade_calendar.py。
        if not _tc.is_trade_day(d):
            print(f"⚠️ {d} 非交易日(周末/法定休市) → 跳过采集(不写库)")
            sys.exit(0)
        if args.legacy_eastmoney:
            rows = to_features(fetch_market_eastmoney(), d)
        else:
            # 主路径: 快选快照(权威同源) → 猫爪自拉
            rows = fetch_from_kuaixuan(d) or fetch_market(d)
        if not rows:
            print(f"⚠️ {d} 未取到任何行情数据(猫爪不可用?) → 跳过采集")
            sys.exit(1)
        # === 非交易日护栏 (2026-09-25 主人反馈「页面数据不对」) ===
        # 根因: aipick_scheduler._is_trade_day() 只判「周一~周五」, **没有节假日日历**
        #   ⇒ 2026-09-25(中秋节, 周五) 被当成交易日, 采集照跑。当天快选快照返回的是
        #   上一交易日的**复制行**: bid_change / bid_amount 与 09-24 逐位相同, 而
        #   price / bid_turnover 全为 0 → 5561 行"幽灵数据"落库, 并据此生成预测报告
        #   (页面默认显示 30 只、95% 概率的假名单)。
        # 判据刻意**用数据本身**而不是交易日历(与日历解耦, 免维护, 也不会被日历漏配坑到):
        #   真实交易日 price>0 占比 ≈ 93%(38 份报告实测最低 87.8%); 休市日快照 price 全 0
        #   ⇒ 阈值 50% 余量充足, 不会误伤真实交易日。
        # 用 exit 0 退出: 这是"今天不该采"的正常分支, 不是故障, 免得调度器日志刷 ERROR。
        _pk = sum(1 for r in rows if _sf(r.get("price")) > 0) / max(1, len(rows))
        if _pk < 0.5:
            print(f"⚠️ {d} price>0 占比仅 {_pk * 100:.1f}% → 判定非交易日/快照未就绪, "
                  f"跳过采集(不写库)")
            sys.exit(0)
        upsert_features(rows)
        print(f"已采集 {len(rows)} 只竞价快照 @ {d}  [{datetime.now().strftime('%H:%M:%S')}]")
