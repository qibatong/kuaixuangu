# AI 竞价选股系统

为「顺势而为竞价终极版」工具增加真正的 AI 能力：历史回测 + 涨停概率预测。

## 环境
```bash
Python: C:/Users/User/.workbuddy/binaries/python/envs/aipick/Scripts/python.exe
依赖:   akshare, xgboost, pandas, scikit-learn（已装好）
```

## 一键命令（在 scripts/ 目录下）
```bash
# 1. 初始化数据库（首次）
python run.py init

# 2. 回补历史数据（首次跑一次，约 5-10 分钟）
python run.py backfill --days 90

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

## 数据说明
- **历史回补**：用新浪日线近似重建竞价特征（竞价涨幅≈开盘涨幅、竞价金额≈开盘价×量×0.15），用于快速回测验证
- **每日采集**：用东财竞价接口真实字段（f615 竞价涨幅 / f616 竞价成交额），9:25-9:30 最准
- **标签**：当日收盘是否涨停（主板≥9.8%，创业板/科创板≥19.8%）

## 注意
- 竞价打板风险极高，预测仅供参考，严格控制仓位
- 数据积累越多模型越准（建议至少积累 1-2 个月真实竞价数据后重新评估）
- 回测存在过拟合风险，务必以测试集（后20%时间段）指标为准
