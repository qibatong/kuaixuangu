# -*- coding: utf-8 -*-
"""AI 竞价选股 - 历史数据回补（**猫爪真实回溯**）

======================================================================
2026-09-20 主人指令：「东财数据换成猫爪数据」+「backfill 也改猫爪」

旧实现（已废弃）：用 akshare 新浪日线**近似重建**竞价特征 ——
    bid_change   ≈ (开盘价/昨收 - 1)*100
    bid_amount   ≈ 开盘价 × 昨日成交量 × 0.15
    circ_mv      ≈ 昨收 × 昨日流通股本 / 1e8
    bid_turnover = 竞价金额 / 流通市值
  ✗ 全是**编出来的近似值**，与真实采集口径不一致（实测拖累 AUC：0.7600 vs 0.7841）。
  ✗ 约 784 行/天（只回补 max_stocks 只），与真实段 >3000 行/天 断层。

新实现：**猫爪 screening 逐日回溯**（tradedate 参数）——
  ✓ 实测可回溯至 2026-08 底（逐日 5546~5553 只，全市场覆盖）
  ✓ 字段全部为**真实竞价口径**：auc_pct_chg / auc_amt / turnover_rate_f
  ✓ 与 collector.py 主路径**同一取数函数**（meoz_source），口径零偏差
  ✓ 标签由 pct_chg 阈值判定（主板 ≥9.8 / 创业板科创板 ≥19.8）

用法：
    python backfill.py --days 30            # 回溯最近 30 个自然日
    python backfill.py --days 30 --start 2026-08-01
    python backfill.py --dates 2026-09-18,2026-09-17

🔴 铁律：本脚本**不再引入任何近似计算** —— 所有特征直接取猫爪成品字段。
   主人「能通过猫爪获取的，都用猫爪的数据，自己不计算」。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import init_db, upsert_features, update_labels, today  # noqa
import meoz_source as MZ  # noqa


def _iter_dates(days, start=None, dates=None):
    """产出待回补的日期列表（YYYY-MM-DD，降序）。"""
    if dates:
        return [d.strip() for d in dates.split(",") if d.strip()]
    end = datetime.now()
    begin = (datetime.strptime(start, "%Y-%m-%d") if start
             else end - timedelta(days=int(days * 1.6)))
    out, cur = [], end
    while cur >= begin:
        out.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return out


def backfill_one(trade_date, verbose=True):
    """回补单个交易日。返回写入的行数（0 = 无数据，通常是周末/节假日/超回溯边界）。

    ★ 2026-09-20 修标签泄漏：yesterday_chg 取**前一交易日**收盘涨幅（竞价时点已知），
      绝不用当日 pct_chg（会与标签同源 → AUC 虚高到 0.9998）。
    """
    rows_map = MZ.fetch_market(trade_date)
    if not rows_map:
        if verbose:
            print(f"  {trade_date}: 猫爪无数据（非交易日 / 超出回溯边界）")
        return 0

    # 校验猫爪实际返回的交易日（周末/节假日会自动回落到前一交易日 → 跳过避免写串）
    sample = next(iter(rows_map.values()), {})
    actual = str(sample.get("tradedate") or "")
    if actual and actual != trade_date.replace("-", ""):
        if verbose:
            print(f"  {trade_date}: 猫爪回落到 {actual}（非该日交易日）→ 跳过")
        return 0

    # 前一交易日涨幅（避免标签泄漏）
    pchg = MZ.prev_chg_map(trade_date)

    feats = MZ.to_features(rows_map, trade_date, prev_chg_map=pchg)
    labels = MZ.to_labels(rows_map, trade_date)
    upsert_features(feats)
    update_labels(trade_date, labels)

    if verbose:
        n_up = sum(x["is_limit_up"] for x in labels)
        print(f"  {trade_date}: {len(feats)} 只（涨停 {n_up} 只，"
              f"{n_up / max(1, len(labels)) * 100:.2f}%）")
    return len(feats)


def backfill(days=30, start=None, dates=None):
    init_db()
    if not MZ.available():
        print(f"❌ 猫爪不可用（{MZ.unavailable_reason()}）→ 无法回补")
        return

    todo = _iter_dates(days, start, dates)
    print(f"猫爪回溯：待尝试 {len(todo)} 个日期（{todo[-1]} ~ {todo[0]}）\n")

    total, ok, skipped = 0, 0, 0
    for d in todo:
        n = backfill_one(d)
        if n:
            total += n
            ok += 1
        else:
            skipped += 1

    print(f"\n回补完成：{ok} 个交易日 / {total} 行写入（跳过 {skipped} 个非交易日/无数据日）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="回溯自然天数（默认 30）")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（覆盖 --days）")
    parser.add_argument("--dates", default=None,
                        help="显式指定日期列表（逗号分隔，最高优先级）")
    args = parser.parse_args()
    backfill(days=args.days, start=args.start, dates=args.dates)
