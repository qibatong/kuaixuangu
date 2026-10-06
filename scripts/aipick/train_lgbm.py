# -*- coding: utf-8 -*-
"""
AI 竞价选股 - LightGBM 版训练（与 train_model.py 平行，绝不覆盖其产物）
=====================================================================================
目标：用**同一份数据、同一个切分口径、同一份特征**训练 LightGBM，产出与 XGBoost
      **同构**的结果，便于直接对拍与灰度切换。

与 train_model.py 的差异（刻意保持最小）
--------------------------------------------------------------------------------
相同：load_features → _select_training_frame → 清洗 → 按日期 80/20 时序切分 → AUC 报告
不同：估计器由 xgb.XGBClassifier 换成 lightgbm.LGBMClassifier

🔴 产物隔离（2026-09-25，必须遵守）
--------------------------------------------------------------------------------
    models/model_lgb.txt            LightGBM Booster 文本模型（XGB 是 models/model_xgb.json）
    models/model_meta_lgb.json      模型元信息（algo/model_file/trained_at/auc/版本）
    output/lgb/train_report.json    **故意不放 output/train_report.json**
                                   —— 该文件被 scripts/aipick/backtest.py:38 消费，
                                      写入会污染 XGB 链路的回测依据。

用法
--------------------------------------------------------------------------------
    /opt/kuaixuan-venv/bin/python train_lgbm.py                # 训练并保存
    /opt/kuaixuan-venv/bin/python train_lgbm.py --dry-run      # 只评估不落盘
    /opt/kuaixuan-venv/bin/python train_lgbm.py --early-stop   # 用测试段早停（仅对比用）
    /opt/kuaixuan-venv/bin/python train_lgbm.py --n-jobs 1     # 限制线程（线上并行时）
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_features, init_db, apply_gene_filter  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import roc_auc_score, classification_report  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ★ 2026-10-06: 支持环境变量重定向 —— 生产机训练必须在临时目录出产物,
#   否则直接覆盖线上 models/model_lgb.txt（见 train_model.py 同一处注释）。
MODEL_DIR = os.path.normpath(os.environ.get("AIPICK_MODEL_DIR")
                             or os.path.join(SCRIPT_DIR, "..", "models"))
# ★ 与 XGB 的 ../output 隔离
OUT_DIR = os.path.normpath(os.environ.get("AIPICK_OUT_DIR")
                           or os.path.join(SCRIPT_DIR, "..", "output", "lgb"))

# ★ 必须与 train_model.py / predict_daily.py / ai_predict.py 逐字一致
# ★ 2026-09-25：移除 `yesterday_chg`（6 维 → 5 维），理由与实测见 train_model.py 顶部注释。
#   简言之：线上它是竞价涨幅（≡ bid_change），训练基座里它是前一交易日涨幅 ⇒ 同名两义。
#   500 天样本外代价仅 −0.0015 池化AUC。
FEATURES = [
    "bid_change",     # 竞价涨幅
    "bid_amount",     # 竞价金额(万元)
    "bid_turnover",   # 竞价换手率
    "price",          # 价格（9:25 竞价价）
    # ★ 2026-10-02 主人指令(命中率优先)：改用**口径无关**派生特征。
    #   动因：线上 circ_mv 是**自由流通市值**、离线基座是**流通市值**（实测 947 vs 2251 亿，
    #   比值因股而异 1.5~2.4 倍）⇒ 直接用 circ_mv 原始值必然 train-serve skew。
    #   改分位/昨日侧后两侧都能就地算出，与数据源口径无关。实测(1620 天基座, ≤10% 口径):
    #   top3 命中 72.9% → 75.7%、top5 67.9% → 70.2%、池化 AUC 0.8422 → 0.8489。
    #   ⚠️ 与 db.py 的 MODEL_FEATURES 及线上模型文件**必须同批发布**（宽度失配会静默降级）。
    "mv_rank",        # 当日市值分位（口径无关，替代 circ_mv 原始值）
    "amt_rank",       # 当日竞价额分位
    "rank_diff",      # amt_rank − mv_rank（相对市值的热度）
    "price_inv",      # 1/价格（低价股偏好）
    "yday_zt",        # 昨日是否涨停
    "yday_lb",        # 截至昨日连板数
    "prev_mkt_zt",    # 昨日全市场涨停家数（情绪）
]
TARGET = "is_limit_up"

# 与 train_model.py 完全一致的数据口径护栏
REAL_DAY_MIN_ROWS = 3000     # 单日行数 > 此值 = 真实采集日
MIN_REAL_ROWS = 5000         # 真实段至少这么多行才单独用, 否则回退全量

# LightGBM 参数：与 XGB 对齐的"同语义"配置（不是调参，是为了公平对拍）
#
# ★ 2026-09-25 生产机实测校准（滚动窗口对拍, 真实采集段, 见 lgbm-deploy/deploy/tune_lgb.py
#   与 decide_lgb.py）
#
#   【第一轮 8 窗口】容量校准 —— 学习率固定 0.05，只动 num_leaves
#   ┌──────────────────────────────┬─────────┬──────────────┐
#   │ 配置                          │ 均值 AUC │ Δ vs XGB     │
#   ├──────────────────────────────┼─────────┼──────────────┤
#   │ LGB num_leaves=7              │ 0.7930  │ +0.0022      │
#   │ XGB max_depth=4（现状基准）    │ 0.7908  │ —            │
#   │ LGB max_depth=4               │ 0.7887  │ −0.0021      │
#   │ LGB num_leaves=15              │ 0.7832  │ −0.0076      │
#   │ LGB num_leaves=31（曾写死）     │ 0.7668  │ −0.0240 ❌   │
#   └──────────────────────────────┴─────────┴──────────────┘
#   🔴 教训：num_leaves=31 **并不等于** xgb max_depth=4 —— depth 4 最多 2^4=16 个叶子，
#      31 个叶子是约 2 倍的容量；在只有 32 个真实采集日的**小数据**上直接过拟合，
#      单次 80/20 切分实测 LGB 0.7574 vs XGB 0.7742（差 0.017），滚动窗口差 0.024。
#      ⇒ 容量必须与 XGB 对齐。
#
#   【第二轮 20 窗口】学习率校准 —— 固定 num_leaves=7，加大样本量防"8 窗口过拟合"
#   ┌──────────────────────────────┬─────────┬──────────┬──────────┬────────┬────────┐
#   │ 配置                          │ 均值 AUC │ Δ vs XGB │ Δ vs 现行 │ p50std │ 平均选出│
#   ├──────────────────────────────┼─────────┼──────────┼──────────┼────────┼────────┤
#   │ **LGB leaves7 n300 lr0.02**   │ 0.8359  │ +0.0109  │ +0.0069  │ 0.1148 │  14.9  │
#   │ LGB leaves7 n600 lr0.02       │ 0.8347  │ +0.0097  │ +0.0057  │ 0.1095 │  14.8  │
#   │ LGB leaves7 n300 lr0.05（旧）  │ 0.8290  │ +0.0040  │ —        │ 0.1088 │  14.2  │
#   │ XGB depth4（基准）             │ 0.8250  │ —        │ —        │ 0.1026 │  12.4  │
#   └──────────────────────────────┴─────────┴──────────┴──────────┴────────┴────────┘
#   ⇒ 学习率 0.05 → 0.02 在**更大样本量上同向更优**（两轮都赢），且方向合理：
#      6 个特征 + 小数据场景，学习率更低 ≈ 更强正则化；n600 反而略降 ⇒ 保持 300 棵树。
#   ⚠️ 代价：单次 80/20 切分的 AUC 会因切分点不同而波动，看**滚动窗口均值**才可靠；
#      数据量上去后（二期 739 万行大训练集）应重新校准回更大的 num_leaves / learning_rate。
#   ⚠️ 校准只用了一个指标（AUC）+ 两个辅助观察（p50 跨日标准差、平均选出只数），
#      样本量仍是 20 个窗口级 —— 属于"够用即止"，不要在其上继续叠更多轮调参。
LGB_PARAMS = dict(
    objective="binary",
    n_estimators=300,
    learning_rate=0.02,      # ★ 校准值(第二轮 20 窗口)，见上表；不要随手调大
    num_leaves=7,            # ★ 校准值(第一轮 8 窗口)，见上表；不要随手调大
    min_child_samples=20,
    subsample=0.8,
    subsample_freq=1,        # LightGBM 必须 >0 才真正启用 bagging
    colsample_bytree=0.8,
    reg_lambda=1.0,
    max_bin=255,
    n_jobs=2,                # 生产机 4C 且同时跑线上服务；训练在 18:59 收盘后
    random_state=42,
    verbosity=-1,
)


def _select_training_frame(df):
    """与 train_model.py 逐字一致：优先只用真实采集段，不足则回退全量。"""
    per_day = df.groupby("trade_date").size()
    real_days = set(per_day[per_day > REAL_DAY_MIN_ROWS].index)
    n_real_rows = int(df["trade_date"].isin(real_days).sum())
    print(f"  数据来源判定: 全量 {len(df)} 行/{df['trade_date'].nunique()} 天; "
          f"真实采集段 {n_real_rows} 行/{len(real_days)} 天 (单日>{REAL_DAY_MIN_ROWS}行)")
    if n_real_rows >= MIN_REAL_ROWS:
        print("  → 采用**仅真实采集段** (回补近似数据不参与训练)")
        return df[df["trade_date"].isin(real_days)].copy(), "real_only"
    print(f"  ⚠️ 真实段不足 {MIN_REAL_ROWS} 行 → 回退全量(含回补近似数据, 精度会受损)")
    return df, "all_fallback"


def _prepare():
    """返回 (Xtr, ytr, Xte, yte, dates, split_idx, limit_rate, data_source) 或 None。"""
    init_db()
    # ★ 2026-10-02: 按天限量（测试机仅 1.75GB 内存，全量 106 万行训练会 OOM 被 Killed）。
    #   默认最近 120 个交易日；AIPICK_TRAIN_DAYS 可覆盖（0/空 = 全量，仅适合大内存机）。
    _days = int(os.environ.get("AIPICK_TRAIN_DAYS", "120") or 0)
    df = load_features(limit_days=_days or None)
    print(f"加载数据: {len(df)} 行, 日期范围 {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    if len(df) < 200:
        print("⚠️ 数据不足 200 行，先跑 backfill.py 回补历史数据")
        return None

    # ★ 顺序要紧：先判"真实采集段"，再按池子筛基因（否则池内每日行数变小会让判据误判）。
    df, data_source = _select_training_frame(df)

    # ★ 2026-10-06 主人订正: 训练池**只**要求「40 个交易日内有过涨停」;
    #   竞价涨幅 / 竞价金额 / 流通市值 训练时不限制, 只在展示侧筛。与 XGB 侧同批。
    _n_before = len(df)
    df = apply_gene_filter(df)[0]
    print(f"  训练池: {_n_before} 行 → 40 交易日内有过涨停 {len(df)} 行")
    if len(df) < 200:
        print(f"⚠️ 池内仅 {len(df)} 行，不足 200，放弃本次训练")
        return None
    if len(df) < 200:
        print(f"⚠️ 选出的训练数据仅 {len(df)} 行，不足 200，放弃本次训练")
        return None

    limit_rate = df[TARGET].mean()
    print(f"涨停样本占比: {limit_rate:.2%} ({int(df[TARGET].sum())}/{len(df)})")

    df = df.dropna(subset=FEATURES)
    df = df[(np.abs(df["bid_change"]) < 30) & (df["bid_amount"] > 0)]
    if df[TARGET].nunique() < 2 or len(df) < 200:
        print(f"⚠️ 清洗后仅 {len(df)} 行/标签类别 {df[TARGET].nunique()} 种，放弃本次训练")
        return None

    X = df[FEATURES].astype(float)
    y = df[TARGET].astype(int)
    dates = df["trade_date"].unique()
    split_idx = int(len(dates) * 0.8)
    tr_d, te_d = set(dates[:split_idx]), set(dates[split_idx:])
    Xtr, Xte = X[df["trade_date"].isin(tr_d)], X[df["trade_date"].isin(te_d)]
    ytr, yte = y[df["trade_date"].isin(tr_d)], y[df["trade_date"].isin(te_d)]
    print(f"训练集 {len(Xtr)} 行 ({dates[0]}~{sorted(tr_d)[-1]}), "
          f"测试集 {len(Xte)} 行 ({dates[split_idx]}~{dates[-1]})")
    return Xtr, ytr, Xte, yte, list(dates), split_idx, float(limit_rate), data_source


def train(dry_run=False, early_stop=False, num_threads=None, out_dir=None):
    import lightgbm as lgb
    global OUT_DIR
    if out_dir:
        OUT_DIR = os.path.abspath(os.path.expanduser(out_dir))

    prep = _prepare()
    if prep is None:
        return None
    Xtr, ytr, Xte, yte, dates, split_idx, limit_rate, data_source = prep

    params = dict(LGB_PARAMS)
    if num_threads:
        params["n_jobs"] = int(num_threads)
    scale_pos = (ytr == 0).sum() / max(1, (ytr == 1).sum())
    print(f"scale_pos_weight = {scale_pos:.3f} | n_jobs={params['n_jobs']} | lightgbm {lgb.__version__}")

    model = lgb.LGBMClassifier(scale_pos_weight=scale_pos, importance_type="gain", **params)
    fit_kw = {}
    if early_stop:
        fit_kw = dict(eval_set=[(Xte, yte)], eval_metric="auc",
                      callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)])
    model.fit(Xtr, ytr, **fit_kw)

    proba = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, proba)
    print(f"\n测试集 AUC: {auc:.4f}")
    print("分类报告(阈值0.5):")
    print(classification_report(yte, (proba >= 0.5).astype(int),
                                target_names=["非涨停", "涨停"], zero_division=0))

    imp = sorted(zip(FEATURES, model.feature_importances_), key=lambda x: -x[1])
    print("\n特征重要性(gain):")
    for f, v in imp:
        print(f"  {f}: {v:.4f}")

    report = {
        "algo": "lightgbm",
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": data_source,
        "n_train": int(len(Xtr)), "n_test": int(len(Xte)),
        "test_date_range": [dates[split_idx], dates[-1]],
        "auc": float(auc),
        "limit_rate": float(limit_rate),
        "feature_importance": {k: float(v) for k, v in imp},
        "scale_pos_weight": float(scale_pos),
        "n_estimators_used": int(getattr(model, "n_estimators_", params["n_estimators"])),
        "lightgbm_version": lgb.__version__,
    }

    if dry_run:
        print("\n[dry-run] 不落盘")
        return report

    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    model_path = os.path.join(MODEL_DIR, "model_lgb.txt")
    # 先写临时文件再 rename：避免服务/预测进程读到半截模型（原子替换）
    tmp_path = model_path + ".tmp"
    model.booster_.save_model(tmp_path)
    os.replace(tmp_path, model_path)
    print(f"\n模型已保存: {model_path}")

    meta = {
        "algo": "lightgbm",
        "model_file": "model_lgb.txt",
        "features": FEATURES,
        "trained_at": report["trained_at"],
        "auc": report["auc"],
        "n_features": len(FEATURES),
        "lightgbm_version": lgb.__version__,
    }
    with open(os.path.join(MODEL_DIR, "model_meta_lgb.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "train_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"评估报告已保存: {os.path.join(OUT_DIR, 'train_report.json')} / models/model_meta_lgb.json")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AI 竞价选股 - LightGBM 训练（与 XGBoost 平行）")
    ap.add_argument("--dry-run", action="store_true", help="只评估不落盘")
    ap.add_argument("--early-stop", action="store_true", help="用测试段早停（仅对比用，不用于生产）")
    ap.add_argument("--n-jobs", type=int, default=None, help="覆盖 n_jobs")
    ap.add_argument("--out-dir", default=None, help="训练报告输出目录(默认 ../output/lgb)")
    _a = ap.parse_args()
    train(dry_run=_a.dry_run, early_stop=_a.early_stop,
          num_threads=_a.n_jobs, out_dir=_a.out_dir)
