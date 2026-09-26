# BACKLOG · 特征集 5 维化（移除 `yesterday_chg`）

**决策日期**：2026-09-25
**决策人**：用户（原话「需要竞价涨幅，不要昨日涨幅」）
**状态**：✅ **已上线生产**（模型与 5 个 `FEATURES` 文件均已生效；见下方「状态订正」）

---

## 0. 状态订正（2026-09-26 21:5x · 取生产硬证据后）

本行原文写「✅ 源码已改（本地，**未上线**） · ⏳ 待发布 · 模型已产出（未部署）」——
**与生产实测相反**。实测证据（生产 `121.196.230.80`，全程只读）：

| # | 证据 | 值 |
|---|---|---|
| 1 | `/opt/kuaixuan/aipick/models/model_meta_lgb.json` | `"features": ["bid_change","bid_amount","bid_turnover","circ_mv","price"]`、**`"n_features": 5`**、`trained_at = 2026-09-26 01:27:28`、`auc = 0.8109` |
| 2 | 模型文件 `model_lgb.txt` / `model_xgb.json` | mtime 均 **2026-09-26 01:27**；同目录另有 `_deprecated/`（旧模型留档） |
| 3 | 生产 `backend/app/services/ai_predict.py` 的 `FEATURES` | **5 维**（无 `yesterday_chg`），且文件头已写明「2026-09-25 起由 6 特征降为 5 特征」 |
| 4 | 生产 `aipick/scripts/{train_model,train_lgbm,predict_daily,backtest}.py` | `FEATURES` **全为 5 维** |

> ⚠️ **不要被 `grep yesterday_chg` 的命中数误导**：`ai_predict.py` 里仍有 3 处、
> `scripts/*.py` 里各有 1~2 处 `yesterday_chg` —— 那些都是**注释与列示例**。
> 该列**保留在采集/落库里、但已退出模型特征集**（见 §4 末尾「采集写入逻辑刻意不动」）。
> 判定是否已 5 维化，**只看 `FEATURES` 列表与 `model_meta_lgb.json` 的 `n_features`**。

📌 教训（与 `AGENTS.md` §0.1 同一类）：**文档里的状态行不是证据** ——
引述前先取一条**生产侧实测**（`n_features` / 文件 mtime / `FEATURES` 原文）。

---

## 1. 一句话

把 `yesterday_chg` 从模型特征集里**删掉**（6 维 → 5 维）。
不改成"竞价涨幅口径"——因为「竞价涨幅」已经由 `bid_change` 承载，留着就是**一列同义复制**。

## 2. 为什么

时点先校准（这条以前写错过）：A 股 `9:15–9:25 集合竞价` → **`9:25 撮合出开盘价`** → `9:25–9:30 不接受撤单、无成交` → `9:30 连续竞价`。
所以 **9:25 已经是开盘了**（开盘价已产生），只是连续竞价还没开始。此刻全市场上**唯一存在的价格就是开盘价**，
因此此刻读到的任何"最新价/涨幅" **必然 ≡ 竞价涨幅**（不是"尚未开盘的当时涨幅"这种含混说法）。

由此：

1. **线上实时链路**（`collector.py:194` 的 `else` 分支）取的是猫爪 `screening.pct_chg`，9:25 那一刻它 ≡ 竞价涨幅
   ⇒ 喂给模型的 `yesterday_chg` ≈ `bid_change`。**实测生产端 `predictions_*.json`：逐位相等 92%~100%**
   （09-21 / 09-22 / 09-23 / 09-24 = 94.6% / 99.5% / 99.2% / 99.1%，与真·前一交易日涨幅相符仅 **2.0%**）。
2. **历史基座**（`build_trainset_v2.py`）那一列是**前一交易日涨幅**（历史无法重建 9:25 时点的 `pct_chg`）。
   ⇒ **同名不同义**。旧线上模型给这一列 **0.3998（第一）** 的重要性，实际是把权重压在 `bid_change` 的副本上。
3. 后果：换基座即 **train/serve skew**。实测同一天同一模型：按训练口径喂 Top5 命中 **40%**，
   按线上现状喂 **20%**（09-24 复现，见下）。

## 3. 实测证据（同基座 / 同协议 / 同容量，唯一变量＝那一列）

协议：固定滚动 750 天 · 样本外 500 天 · `test-block 250` · TopN 5 · 池化 AUC · 日等权 · 容量 `lgb l127-lr03-n800`

| 臂 | `yesterday_chg` 的内容 | 池化 AUC | 命中率 | 正收益率 | 平均收益 |
|---|---|---|---|---|---|
| arm6 | 6 维 · 真·前一交易日涨幅 | **0.7859** | 31.5% | 49.9% | −0.19% |
| **arm5** | **5 维 · 删除（本次采用）** | **0.7844** | 31.4% | 49.9% | −0.20% |
| armD | 6 维 · 竞价涨幅（＝`bid_change` 副本） | 0.7843 | 31.4% | 49.9% | −0.20% |

* **armD ≈ arm5**（差 0.0001）⇒ 竞价口径下该列零信息增益，删列与改名等价，**选删列**（少一处坑）。
* **arm6 只高 0.0015** ⇒ 真·昨日涨幅确有独立信息（与竞价涨幅逐位重合度仅 **0.70%**），但边际很小。
  代价 0.0015 AUC 换掉「同名两义 + 换基座必 skew」两个结构性风险，划算。

复现：`lgbm-deploy/run_featset_cmp.py` → `output/lgblab/featset_{arm6,arm5,armD}.json`
（`train_v2.py` 新增 `--drop-features`，`infer_0924.py` 同步新增）。

## 4. 改动清单（共 7 处，**已上生产**）

| # | 文件 | 位置 | 改动 |
|---|---|---|---|
| 1 | `scripts/aipick/train_model.py` | `FEATURES`（L37） | 删 `yesterday_chg` |
| 2 | `scripts/aipick/train_lgbm.py` | `FEATURES`（L50） | 删 `yesterday_chg` |
| 3 | `scripts/aipick/predict_daily.py` | `FEATURES`（L70） | 删 `yesterday_chg` |
| 4 | `scripts/aipick/backtest.py` | `FEATURES`（L24） | 删 `yesterday_chg` |
| 5 | `backend/app/services/ai_predict.py` | `FEATURES`（L50） | 删 `yesterday_chg` + 第 5 特征铁律文案改 5 → 见文件头 docstring |
| 6 | `scripts/aipick/db.py` | 列注释（L26） | 标注「列名为历史遗留，实为竞价涨幅；不参与模型特征」 |
| 7 | `scripts/aipick/collector.py` | 3 处 docstring + L194 注释 | 时点表述改准确（9:25 已开盘/唯一价格＝开盘价），标注该列已退出特征集 |

> ⚠️ **采集写入逻辑刻意不动**：`yesterday_chg` 仍按原样落库（实时＝竞价涨幅、历史回补＝前一交易日涨幅），仅从模型特征集移除。
> 理由：动采集语义风险高（历史分支若改读当日 `pct_chg` 会**重新引入标签泄漏**，那是 09-25 刚修完的），
> 而该列已不影响任何模型输出。若日后要"两分支统一为竞价涨幅"，必须改用 `auc_pct_chg`（竞价时点、无泄漏），
> **不能**改用 `pct_chg`。

## 5. 发布要求：**必须同批，不能分批**（✅ 已按此批次执行）

特征列表在生产里出现在 **5 个文件**，且**每天 18:59:30 后端调度器会自动重训并覆盖线上模型**
（`backend/app/services/aipick_scheduler.py` → `train_model.py` + `train_lgbm.py`；9:27 跑 `predict_daily.py`）。

> **实际执行结果**：线上模型 `trained_at = 2026-09-26 01:27:28`、`n_features = 5`，
> 而生产 5 个 `FEATURES` 文件**同为新版**（见 §0）⇒ 批次 ①②③ 已闭合，
> **不存在「5 维模型 + 6 维后端」的空窗**。运行期宽度断言（§6-4 建议项）也已落到
> `ai_predict.py`：失配会打 **ERROR + 告警**，不再静默降级。

**失败模式（已在本机实测复现）**：只改 aipick 脚本、不改后端 →
当晚 18:59 产出 5 维模型 → 次日 9:27 后端 `ai_predict.py` 仍按 6 维喂 →
`LightGBMError: number of features in data (6) is not the same as it was in training data (5)`
→ `_market_prob_map` 返回 `{}` → **AI 层 fail-open 静默降级走 `ai_default`，不报错、不告警**。

### 发布批次

```
① 同批推送 5 个 FEATURES 文件（上表 #1#2#3#4#5）+ db.py / collector.py 注释
② 立即重启后端服务（kuaixuan.service），确保下一交易日 9:27 之前生效
③ 重训并替换两个模型（同一批）：
     model_lgb.txt  ←  5 维火眼（l127-lr03-n800）
     model_xgb.json ←  5 维金睛（同基座同协议重训）
   同步更新 model_meta_lgb.json / train_report*.json 的 features/n_features（6 → 5）
④ 验收：当日 9:27 后确认 AI 层未降级（看日志无「模型加载失败/降级」；`all` 里 ai_prob 非空且有区分度）
⑤ 若 ② 与 ③ 之间跨过了 18:59，把当次自动重训视为一次发布——它会用新脚本产出 5 维模型，
   所以 ③ 必须与 ② 同日完成，否则会出现「5 维模型 + 6 维后端」的空窗
```

### 回滚

回滚需**同时**回滚 5 个 FEATURES 文件 + 两个模型文件（`models.bak_*` 备份）。只回滚模型会立刻触发上面的静默降级。

## 6. 验收时必须顺带确认的（遗留项）

1. **`bid_change` 源不一致**：基座用猫爪 `auc_pct_chg`，线上用快选 `snapshot_bid.bid_change`。
   09-24 对照：完全一致 15.3%、近一致（<0.011）**99.4%**，最大差 2.42。
   删掉 `yesterday_chg` 后，模型里"竞价涨幅"只剩 `bid_change` 一个来源 ⇒ 该源不一致成为**唯一**的竞价涨幅 skew，
   建议纳入下一轮统一（推荐统一到猫爪 `auc_pct_chg`：唯一能覆盖 1,620 天历史的源）。
2. **`circ_mv` 源不一致**：基座用 `valuation.circ_mv`，线上用 `screening.circ_mv`；
   相对差中位 1.4%、最大 56.6%，30–100 亿边界翻转 71 只 ⇒ 候选池 17 只 vs 9 只。
3. **9:26 live 探针**：确认 5 个特征在 9:25 全部可得（尤其 `auc_turnover` / 竞价价），避免 train-serve skew。
4. 模型与特征列表的一致性校验已加进 `infer_0924.py`（宽度不符时给出人话报错），
   建议同样加进 `predict_daily.py` 与 `ai_predict.py`（现在只会在运行期静默降级）。

## 7. 关联文件

> ⚠️ **路径归属**：`lgbm-deploy/` **不在本仓库内** —— 它是**与仓库同级**的实验目录
> （`/Users/batong/WorkBuddy/2026-09-24-21-22-12/lgbm-deploy/`，未被 `.gitignore` 命中、
> 也未入库）。在仓库里 checkout 后**找不到**下列文件，属正常；引用时请带全路径。

* 训练/对拍：`lgbm-deploy/run_featset_cmp.py`、`lgbm-deploy/train_v2.py --drop-features`
* 推理：`lgbm-deploy/infer_0924.py --drop-features yesterday_chg`
* 结果页：`lgbm-deploy/output/infer0924/RESULT-特征集5维化-三臂验证.html`
* 报告：`lgbm-deploy/REPORT-特征集5维化与0924结果.md`
* 实验臂模型：`lgbm-deploy/models/model_lgb_hist5_l127.txt`（`l63`/`l7` 为同批容器长度的对照臂）
* **线上模型（权威）**：`/opt/kuaixuan/aipick/models/model_lgb.txt` + `model_xgb.json`
  （`n_features = 5`、`trained_at = 2026-09-26 01:27:28`，见 §0）。
  ⚠️ 线上模型由生产脚本**另行重训**产出；与上面那个实验臂是否逐位相同**未做核对**，
  不要据文件名推定"就是它"。
