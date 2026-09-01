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
FEATURES = [
    "bid_change",     # 竞价涨幅
    "bid_amount",     # 竞价金额(万元)
    "bid_turnover",   # 竞价换手率
    "circ_mv",        # 流通市值(亿)
    "yesterday_chg",  # 昨日涨幅
    "price",          # 价格
]

TARGET = "is_limit_up"  # 当日是否涨停


def train():
    init_db()
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    df = load_features()
    print(f"加载数据: {len(df)} 行, 日期范围 {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    if len(df) < 200:
        print("⚠️ 数据不足 200 行，先跑 backfill.py 回补历史数据")
        return

    # 涨停率统计
    limit_rate = df[TARGET].mean()
    print(f"涨停样本占比: {limit_rate:.2%} ({int(df[TARGET].sum())}/{len(df)})")

    # 过滤异常值
    df = df.dropna(subset=FEATURES)
    df = df[(np.abs(df["bid_change"]) < 30) & (df["bid_amount"] > 0)]

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
