# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 模型训练
目标：预测当日涨停概率（二分类）
- XGBoost 分类器
- 时间序列切分（前 80% 训练，后 20% 测试），避免未来函数
- 输出：模型文件 + 评估报告（AUC / 特征重要性）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_features, init_db  # noqa

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import roc_auc_score, classification_report
from sklearn.model_selection import TimeSeriesSplit

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

# 特征列（与工具评分逻辑一致 + 基础特征）
# ★ 2026-09-25 拍板：**移除 `yesterday_chg`**（6 维 → 5 维），见 docs/BACKLOG-特征集5维化.md
#   原因：线上实时链路的 `yesterday_chg` 实为**当日竞价涨幅**（9:25 集合竞价已撮合出开盘价，
#         此刻市场上唯一的价格就是开盘价 ⇒ 读到的 pct_chg ≡ 竞价涨幅），与 `bid_change` 同信息；
#         而训练侧历史基座那一列只能是**前一交易日涨幅**（历史无法重建 9:25 时点的 pct_chg）
#         ⇒ 同名不同义，换基座即 train/serve skew。
#   实测（同基座/同协议/同容量，500 天样本外/TopN=5）：
#     6 维 含真·昨日涨幅    池化AUC 0.7859
#     5 维 删列             池化AUC 0.7844   ← 本文件采用
#     6 维 该列=竞价涨幅    池化AUC 0.7843   ← 与 5 维等价 ⇒ 该列在竞价口径下是 bid_change 的复制列
#   ⚠️ 模型与特征列表必须**同批发布**：只改这里不改 backend/app/services/ai_predict.py，
#     该层会因特征宽度不符失败并 fail-open 静默降级（不报错、不告警）。
FEATURES = [
    "bid_change",     # 竞价涨幅
    "bid_amount",     # 竞价金额(万元)
    "bid_turnover",   # 竞价换手率
    "circ_mv",        # 市值(亿)=**自由流通市值**(2026-09-26 口径统一, 与线上 scorer 一致)
    "price",          # 价格（9:25 竞价价）
]

TARGET = "is_limit_up"  # 当日是否涨停

# ---------------------------------------------------------------- 训练数据来源
# 🔴 2026-09-20 主人要求「重训模型」时实测发现: 库内数据**口径混杂** ——
#   · 回补段(backfill.py, 新浪日线近似): ~784 行/天, 竞价涨幅≈开盘涨幅、
#     竞价额≈开盘价×量×0.15 —— **是编出来的近似值**, 与真实采集口径不一致;
#   · 真实段(collector.py, 东财/猫爪真实字段): >3000 行/天, 全市场覆盖。
#   把两段混在一起训练 = 让模型去拟合一批"假特征" → 实测拖累明显。
#
#   8 个滚动窗口对拍(训练=该日之前全部数据, 测试=该日):
#       全量(回补+真实) 均值 AUC 0.7600, 仅真实段 均值 AUC 0.7841 → **仅真实段 7/8 胜出**。
#   故默认**只用真实采集段**; 真实段样本不足(< MIN_REAL_ROWS)时回退全量并告警。
#   ⚠️ "真实段"判据 = 当日行数 > REAL_DAY_MIN_ROWS(3000): 全市场~5200+ vs 回补~784,
#      中间无过渡, 阈值稳健。
REAL_DAY_MIN_ROWS = 3000     # 单日行数 > 此值 = 真实采集日
MIN_REAL_ROWS = 5000         # 真实段至少这么多行才单独用, 否则回退全量


def _select_training_frame(df):
    """挑训练数据: 优先**只用真实采集段**, 不足则回退全量。

    返回 (df_selected, label)。label 用于日志/报告说明本次数据来源。
    """
    per_day = df.groupby("trade_date").size()
    real_days = set(per_day[per_day > REAL_DAY_MIN_ROWS].index)
    n_real_rows = int(df["trade_date"].isin(real_days).sum())

    print(f"  数据来源判定: 全量 {len(df)} 行/{df['trade_date'].nunique()} 天; "
          f"真实采集段 {n_real_rows} 行/{len(real_days)} 天 (单日>{REAL_DAY_MIN_ROWS}行)")
    if n_real_rows >= MIN_REAL_ROWS:
        d = df[df["trade_date"].isin(real_days)].copy()
        print(f"  → 采用**仅真实采集段** (回补近似数据不参与训练: 口径不一致, 实测拖累 AUC)")
        return d, "real_only"
    print(f"  ⚠️ 真实段不足 {MIN_REAL_ROWS} 行 → 回退全量(含回补近似数据, 模型精度会受损)")
    return df, "all_fallback"


def train():
    init_db()
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    df = load_features()
    print(f"加载数据: {len(df)} 行, 日期范围 {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    if len(df) < 200:
        print("⚠️ 数据不足 200 行，先跑 backfill.py 回补历史数据")
        return

    df, data_source = _select_training_frame(df)
    if len(df) < 200:
        print(f"⚠️ 选出的训练数据仅 {len(df)} 行，不足 200，放弃本次训练")
        return

    # 涨停率统计
    limit_rate = df[TARGET].mean()
    print(f"涨停样本占比: {limit_rate:.2%} ({int(df[TARGET].sum())}/{len(df)})")

    # 过滤异常值
    df = df.dropna(subset=FEATURES)
    df = df[(np.abs(df["bid_change"]) < 30) & (df["bid_amount"] > 0)]
    if df[TARGET].nunique() < 2 or len(df) < 200:
        print(f"⚠️ 清洗后仅 {len(df)} 行/标签类别 {df[TARGET].nunique()} 种，放弃本次训练")
        return

    X = df[FEATURES].astype(float)
    y = df[TARGET].astype(int)

    # 时间序列切分：按日期排序，前80%训练后20%测试
    dates = df["trade_date"].unique()
    split_idx = int(len(dates) * 0.8)
    train_dates, test_dates = set(dates[:split_idx]), set(dates[split_idx:])
    Xtr, Xte = X[df["trade_date"].isin(train_dates)], X[df["trade_date"].isin(test_dates)]
    ytr, yte = y[df["trade_date"].isin(train_dates)], y[df["trade_date"].isin(test_dates)]

    print(f"训练集 {len(Xtr)} 行 ({dates[0]}~{sorted(train_dates)[-1]}), 测试集 {len(Xte)} 行 ({dates[split_idx]}~{dates[-1]})")

    # XGBoost（类别不平衡 → scale_pos_weight）
    scale_pos = (ytr == 0).sum() / max(1, (ytr == 1).sum())
    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        eval_metric="auc",
        use_label_encoder=False,
        verbosity=0,
        random_state=42,
    )
    model.fit(Xtr, ytr)

    # 测试集评估
    proba = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, proba)
    print(f"\n测试集 AUC: {auc:.4f}")
    print("分类报告(阈值0.5):")
    print(classification_report(yte, (proba >= 0.5).astype(int), target_names=["非涨停", "涨停"], zero_division=0))

    # 特征重要性
    imp = sorted(zip(FEATURES, model.feature_importances_), key=lambda x: -x[1])
    print("\n特征重要性:")
    for f, v in imp:
        print(f"  {f}: {v:.4f}")

    # 保存模型
    model_path = os.path.join(MODEL_DIR, "model_xgb.json")
    model.save_model(model_path)
    print(f"\n模型已保存: {model_path}")

    # 保存评估报告（numpy 类型转 Python 原生类型）
    report = {
        "trained_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": data_source,
        "n_train": int(len(Xtr)), "n_test": int(len(Xte)),
        "test_date_range": [dates[split_idx], dates[-1]],
        "auc": float(auc),
        "limit_rate": float(limit_rate),
        "feature_importance": {k: float(v) for k, v in imp},
        "scale_pos_weight": float(scale_pos),
    }
    with open(os.path.join(OUT_DIR, "train_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("评估报告已保存: output/train_report.json")


if __name__ == "__main__":
    train()
