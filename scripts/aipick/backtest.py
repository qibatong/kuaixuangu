# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 回测引擎
模拟：每天 9:25 竞价结束后，用模型预测涨停概率，买入 TopN，收盘卖出。
统计：胜率 / 盈亏比 / 最大回撤 / 日均收益 / 与随机基准对比。
输出：HTML 回测报告。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_features  # noqa

import pandas as pd
import numpy as np
import xgboost as xgb

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

FEATURES = [
    "bid_change", "bid_amount", "bid_turnover",
    "circ_mv", "yesterday_chg", "price",
]


def load_model():
    model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
    model.load_model(os.path.join(MODEL_DIR, "model_xgb.json"))
    return model


def backtest(top_n=5, start=None, end=None, model_path=None):
    os.makedirs(OUT_DIR, exist_ok=True)

    # 读取训练报告，默认只在模型未见过的测试期回测（避免数据泄漏）
    report_path = os.path.join(OUT_DIR, "train_report.json")
    if not start and os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            train_info = json.load(f)
        start = train_info.get("test_date_range", [None])[0]
        print(f"⏱️ 检测到训练切分，回测限定在测试期（模型未见过的数据）: {start} 起")

    df = load_features()
    for col in FEATURES + ["close_chg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=FEATURES + ["close_chg"])
    df = df[(np.abs(df["bid_change"]) < 30) & (df["bid_amount"] > 0)]
    df = df.sort_values("trade_date")
    if start:
        df = df[df["trade_date"] >= start]
    if end:
        df = df[df["trade_date"] <= end]

    model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
    model.load_model(model_path or os.path.join(MODEL_DIR, "model_xgb.json"))

    # 逐日预测
    days = df["trade_date"].unique()
    daily = []
    for d in days:
        day_df = df[df["trade_date"] == d]
        if len(day_df) < top_n:
            continue
        X = day_df[FEATURES].astype(float)
        proba = model.predict_proba(X)[:, 1]
        day_df = day_df.copy()
        day_df["prob"] = proba
        top = day_df.nlargest(top_n, "prob")
        avg_chg = top["close_chg"].mean()
        win = (top["close_chg"] > 0).mean()
        daily.append({"date": d, "n": len(top), "avg_chg": avg_chg, "win_rate": win, "codes": top["code"].tolist()})

    bt = pd.DataFrame(daily)
    if bt.empty:
        print("无回测数据")
        return

    # 统计指标（等权每日组合）
    daily_ret = bt["avg_chg"] / 100  # 日收益率
    cum = (1 + daily_ret).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    total_days = len(bt)
    win_days = (daily_ret > 0).mean()
    avg_ret = daily_ret.mean()
    # 随机基准：每天随机取 top_n 只
    rand_ret = []
    rng = np.random.default_rng(42)
    for d in days:
        day_df = df[df["trade_date"] == d]
        if len(day_df) < top_n:
            continue
        pick = day_df.sample(n=top_n, random_state=rng)
        rand_ret.append(pick["close_chg"].mean() / 100)
    rand_avg = np.mean(rand_ret) if rand_ret else 0

    # 平均胜率（按股票计）
    all_picks = []
    for d in days:
        day_df = df[df["trade_date"] == d]
        if len(day_df) < top_n:
            continue
        X = day_df[FEATURES].astype(float)
        proba = model.predict_proba(X)[:, 1]
        day_df = day_df.copy()
        day_df["prob"] = proba
        all_picks.append(day_df.nlargest(top_n, "prob"))
    picks = pd.concat(all_picks)
    stock_win = (picks["close_chg"] > 0).mean()
    avg_win_ret = picks[picks["close_chg"] > 0]["close_chg"].mean()
    avg_loss_ret = picks[picks["close_chg"] <= 0]["close_chg"].mean()
    profit_loss = abs(avg_win_ret / avg_loss_ret) if avg_loss_ret != 0 else float("inf")

    result = {
        "top_n": top_n,
        "period": [bt["date"].min(), bt["date"].max()],
        "days": total_days,
        "cum_return": float(cum.iloc[-1] - 1),
        "max_drawdown": float(max_dd),
        "avg_daily_ret": float(avg_ret),
        "win_days_ratio": float(win_days),
        "stock_win_rate": float(stock_win),
        "avg_win": float(avg_win_ret), "avg_loss": float(avg_loss_ret),
        "profit_loss_ratio": float(profit_loss),
        "random_benchmark": float(rand_avg),
        "n_picks": len(picks),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 生成 HTML 报告
    gen_html(bt, result, top_n)
    return result


def gen_html(bt, r, top_n):
    rows = ""
    for _, row in bt.tail(30).iterrows():
        chg = row["avg_chg"]
        color = "#c0392b" if chg >= 0 else "#27ae60"
        rows += (f"<tr><td>{row['date']}</td><td>{row['n']}</td>"
                 f"<td style='color:{color}'>{chg:+.2f}%</td>"
                 f"<td>{row['win_rate']:.0%}</td>"
                 f"<td style='font-size:11px;color:#888'>{','.join(row['codes'][:5])}</td></tr>")

    cum = (1 + bt["avg_chg"] / 100).cumprod()
    points = ",".join(f"{i},{cum.iloc[i]:.4f}" for i in range(0, len(bt), max(1, len(bt) // 60)))

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>AI竞价选股回测报告</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#f5f6fa;color:#2c3e50;margin:0;padding:24px}}
.card{{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 2px 6px rgba(0,0,0,.08)}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;color:#7f8c8d;font-weight:500;margin:0 0 16px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.metric{{background:#f8f9fb;border-radius:8px;padding:14px;text-align:center}}
.metric .v{{font-size:22px;font-weight:700}} .metric .l{{font-size:12px;color:#7f8c8d;margin-top:4px}}
.up{{color:#c0392b}} .down{{color:#27ae60}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#34495e;color:#fff;padding:8px;text-align:left}} td{{padding:7px 8px;border-bottom:1px solid #ecf0f1}}
.note{{font-size:12px;color:#95a5a6;line-height:1.8;margin-top:8px}}
svg text{{font-size:11px;fill:#7f8c8d}}
</style></head><body>
<div class="card"><h1>📊 AI竞价选股 · 回测报告</h1>
<h2>策略：每日竞价后模型预测，买入 Top{top_n}，收盘卖出（等权）</h2>
<div class="metrics">
<div class="metric"><div class="v {('up' if r['cum_return']>=0 else 'down')}">{r['cum_return']*100:+.1f}%</div><div class="l">累计收益</div></div>
<div class="metric"><div class="v {('up' if r['avg_daily_ret']>=0 else 'down')}">{r['avg_daily_ret']*100:+.2f}%</div><div class="l">日均收益</div></div>
<div class="metric"><div class="v down">{r['max_drawdown']*100:.1f}%</div><div class="l">最大回撤</div></div>
<div class="metric"><div class="v">{r['stock_win_rate']*100:.1f}%</div><div class="l">个股胜率</div></div>
<div class="metric"><div class="v">{r['profit_loss_ratio']:.2f}</div><div class="l">盈亏比</div></div>
<div class="metric"><div class="v">{r['days']}</div><div class="l">回测天数</div></div>
</div>
<p class="note">回测区间 {r['period'][0]} ~ {r['period'][1]} · 每日组合胜率 {r['win_days_ratio']*100:.0f}% · 随机基准日均收益 {r['random_benchmark']*100:+.2f}% · 共 {r['n_picks']} 次买入</p>
</div>
<div class="card"><h2>净值曲线（累计收益）</h2>
<svg viewBox="0 0 680 200" width="100%" xmlns="http://www.w3.org/2000/svg">
<line x1="40" y1="170" x2="650" y2="170" stroke="#ddd" stroke-width="1"/>
<polyline points="{points}" fill="none" stroke="#c0392b" stroke-width="2"/>
</svg>
</div>
<div class="card"><h2>最近 30 个交易日明细</h2>
<table><thead><tr><th>日期</th><th>买入数</th><th>组合均涨幅</th><th>当日胜率</th><th>买入标的</th></tr></thead>
<tbody>{rows}</tbody></table>
</div>
<div class="card note">⚠️ 免责声明：本报告基于历史数据回测，竞价金额为近似估计（新浪日线估算），不代表未来收益。实盘前请用真实竞价数据（collector.py 每日采集）积累后再训练。回测存在过拟合风险，仅供研究。</div>
</body></html>"""
    path = os.path.join(OUT_DIR, "backtest_report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"回测报告已生成: {path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    backtest(top_n=args.top_n, start=args.start, end=args.end)
