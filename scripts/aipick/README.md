# AI 竞价选股系统

为「顺势而为竞价终极版」工具增加真正的 AI 能力：历史回测 + 涨停概率预测。

## 环境
```bash
Python: /opt/bid-venv/bin/python  (测试机, 3.11.8)
依赖:   xgboost, pandas, scikit-learn（akshare 已弃用, 2026-09-20 起不再需要）
```
**猫爪 apikey**：`meoz_source.py` 复用快选 `backend/app/services/meoz_client.py`，
读取 `settings.meoz_apikey` 或环境变量 `MEOZ_APIKEY`。

## 一键命令（在 scripts/ 目录下）
```bash
# 1. 初始化数据库（首次）
python run.py init

# 2. 回补历史数据（猫爪真实回溯, 190 自然日约 125 个交易日, 需数分钟）
python run.py backfill --days 190

# 3. 训练模型（每周自动重训）
python run.py train

# 4. 回测（评估模型有效性）
python run.py backtest --top-n 5

# 5. 每日预测（9:25-9:30 打开预测报告）
python run.py predict

# 6. 采集当日竞价快照（9:27 自动）
python run.py collect

# 7. 收盘打标签（15:05 自动）
python run.py label
```

## 输出
| 文件 | 说明 |
|------|------|
| `output/predictions_YYYY-MM-DD.html` | 每日 AI 涨停概率预测报告 |
| `output/backtest_report.html` | 回测报告（胜率/盈亏比/回撤） |
| `output/train_report.json` | 训练评估（AUC/特征重要性） |
| `data/aipick.db` | SQLite 历史数据（自动积累） |

## 已配置的自动任务（工作日）
| 时间 | 任务 |
|------|------|
| 9:27 | 采集当日竞价快照 |
| 15:05 | 收盘打标签（是否涨停） |
| 19:00 | 重训模型 + 生成次日预测 |

## 数据说明（2026-09-20 起：**全部走猫爪**）
- **主数据源**：猫爪 `screening` 接口（`meoz_source.py` 统一封装）
  - 不传 `symbols` = **全市场 5553 只**，单次调用拿全
  - **支持 `tradedate` 回溯**（实测可回溯至 **2026-03-18**，约 6 个月）
  - 字段映射：`auc_pct_chg`→竞价涨幅 / `auc_amt`→竞价金额 / `turnover_rate_f`→竞价换手率
    / `close`→价格 / `circ_mv`→流通市值 / `pct_chg`→最新涨幅
- **采集（collector.py）**：优先读快选 `snapshot_bid`（权威同源），缺的 3 字段由猫爪补
- **回补（backfill.py）**：猫爪逐日回溯（**真实竞价口径**，~5500 行/天）
- **预测（predict_daily.py）**：快选快照 → 猫爪自拉 → 东财兜底
- **标签**：当日收盘是否涨停（主板≥9.8%，创业板/科创板≥19.8%），由 `pct_chg` 判定

### ⚠️ 换手率口径变更（重要）
| 源 | 字段 | 口径 |
|---|---|---|
| 旧·东财 | `f8` | 换手率（**流通股本**） |
| **新·猫爪** | `turnover_rate_f` | **实际换手率（自由流通股本）** |

实测系统性更大（平安银行 0.44→1.05、万科A 4.54→6.81）。
→ 与本项目「所有流通市值改自由流通市值」的**全局口径一致**，故直接采用（主人已拍板）。

### 东财回退（仅兜底）
`collector.py --legacy-eastmoney` 可回退到「完全自拉东财」的旧逻辑（`fetch_market_eastmoney()`）。
⚠️ 东财 `f8` 与猫爪 `turnover_rate_f` 口径不同，回退会产生口径撕裂，**仅在猫爪故障时用**。

## 🔴 训练数据口径（2026-09-20 更新）
历史遗留的 **akshare 新浪日线近似回补段**（`~784 行/天`，编出来的假特征）
已于 2026-09-20 **清空重建**，现库内数据**全部为猫爪真实竞价口径**。

`train_model.py` 仍保留 `_select_training_frame()` 自适应逻辑（`REAL_DAY_MIN_ROWS`=3000
/ `MIN_REAL_ROWS`=5000），作为**历史兼容与护栏**——若未来又混入低覆盖日可自动隔离。

历史实测依据（当时用于决策清空重建）：
| 训练数据 | 均值 AUC | 中位 AUC | 胜出 |
|---|---|---|---|
| 全量（回补+真实） | 0.7600 | 0.7676 | 1/8 |
| **仅真实段** | **0.7841** | **0.7835** | **7/8** |

## 注意
- 竞价打板风险极高，预测仅供参考，严格控制仓位
- 数据积累越多模型越准（现有猫爪真实回溯已覆盖约 6 个月，样本充足）
- 回测存在过拟合风险，务必以测试集（后20%时间段）指标为准
