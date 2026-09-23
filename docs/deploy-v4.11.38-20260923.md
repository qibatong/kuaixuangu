# v4.11.38 部署记录 — 竞价强度去掉「净额档」

- **日期**：2026-09-23 20:45 ~ 20:48（北京时间）
- **目标机**：测试机 `47.99.153.123`（`/opt/kuaixuan`）
- **生产机**：`121.196.230.80` —— **未部署**（须主人明确指令）
- **触发指令**：「去掉净额档，净额档的数给到量比档，你修改后部署到测试机进行测试」

---

## 一、改了什么

| 层 | 改前 | 改后 |
|---|---|---|
| 竞价量比档 `w_vol_ratio` | 0.45 | **0.75** |
| 竞价主力净额档 `w_ff` | 0.30 | **0.00（移除）** |
| AI 预测档 `w_ai` | 0.25 | 0.25（不变） |

合成式：`warn_score = 0.75×量比分档 + 0.25×AI档`（子权重仍自动归一）。

**依据**：竞价主力净额层**连续 12 个交易日全市场非零 0 只**（采集链路 9:25 定格早于猫爪
`fundflow_kp` 生成）⇒ 恒走 `ff_default 0.35` = **常数项 0.105**，对排序零贡献、纯稀释量比。

**保留项**：`BidStrength.ff_pct` 字段、`ff_buckets` / `ff_default` 配置**均保留**（仅权重置 0）
⇒ **恢复只需把 `w_ff` 调回 0.30**，不必改代码。

### 代码文件（5 个）

| 文件 | 改动 |
|---|---|
| `backend/app/services/scorer.py` | `DEFAULT_SCORING.factors.bid_strength`：`w_vol_ratio 0.45→0.75`、`w_ff 0.30→0.0` + 注释 |
| `backend/app/services/bid_strength.py` | `_compose` 子权重兜底 `0.75/0.0/0.25`；文件头/`score_one`/`score_one_live_ff` 文档改「两层」 |
| `backend/app/services/picker/score.py` | 注释同步（3 处「三层合成」→「两层」） |
| `backend/app/services/picker/pipeline.py` | 注释同步（2 处） |
| `backend/app/services/picker/precompute.py` | 注释同步（1 处） |

> 前端**无需改动**：管理页 `AdminView.vue` 的 `factorOrder` 只含
> `bid/activity/warn/market/yesterday`，本就不展示 `bid_strength` 子权重。

### 配置（测试机 `settings.scoring`）

DB 内此前**没有 `factors.bid_strength` 段**（主人 20:07 用管理页保存时被丢掉，一直走代码默认）
⇒ 本次**显式写入**该段，使配置自证可审计：

```json
"bid_strength": {"label":"竞价强度","unit":"合成",
  "buckets":[["3","9999",1.0],["2","3",0.85],["1.5","2",0.7],
             ["1.0","1.5",0.55],["0.6","1.0",0.4],["0","0.6",0.25]],
  "default":0.22, "w_vol_ratio":0.75, "w_ff":0.0, "w_ai":0.25,
  "ff_buckets":[…8 档…], "ff_default":0.35,
  "ai_buckets":[["0.90","1.01",1.0],["0.85","0.90",0.85],["0.80","0.85",0.70]],
  "ai_topn":30, "ai_default":0.35}
```

写入路径按 v4.11.37 固化规范：**备份原始字节 → `_validate_scoring` → `settings.set` → 回读核对**
（**不裸改 DB**）。

---

## 二、部署步骤与事实

1. **本机回归**：相关子集 `127 passed`；全量 `1251 collected`。
2. **md5 比对**：远端 3 文件与本地**行尾归一化后 md5 逐一致**：
   - `scorer.py` `9865e344e7b0340959d89465bcb9f550`
   - `bid_strength.py` `29e5422e98872bc32016e236ac7da2eb`
   - `picker/score.py` `bb4f29e94ce326b869c632bcacdcc2ee`
3. **备份**（3 份）：
   - `/opt/kuaixuan/backups/be_v7_20260923-204531/`（scorer / bid_strength / score + `md5_before.txt`）
   - `/opt/kuaixuan/backups/be_v7b_20260923-204757/`（score / pipeline / precompute + `md5_before.txt`）
   - `/opt/kuaixuan/backups/settings_scoring_v7bak_20260923-204558.json`（评分配置原始字节，971 B）
4. **两阶段部署**：先上传 `/tmp/v7_stage*` 做 `py_compile` 预检 → 通过后原子替换 → `systemctl restart kuaixuan kx-worker`。
5. **配置写入回读**：`READBACK_BID_STRENGTH {"w_vol_ratio":0.75,"w_ff":0.0,"w_ai":0.25}`、`READBACK_MATCH True`。

---

## 三、验证证据（测试机真实数据）

### ① 生效配置（重启后新进程）

```
w_vol_ratio=0.75  w_ff=0.0  w_ai=0.25
buckets=6 条  ff_buckets=8 条  ai_buckets=3 条  ai_topn=30
顶层 w_warn=0.3
```

### ② 全市场合成（2026-09-23 定格 5561 只）

- 逐票方向：**升 825 / 降 1215 / 无合成值 3521**（与部署前预演**逐一致**）
- 按量比档的 Δ异动分均值：`[0,0.6)` **−0.90** → `[0.6,1.0)` **+0.45** → `[1.0,1.5)` **+1.80**
  → `[1.5,2.0)` **+3.15** → `[2,3)` **+4.50** → `[3,9999)` **+5.85**
  ⇒ **缩量票降、放量票升**（因 `vol_score ≈ 0.35` 是分水岭）
- Δ异动分分位：P5 −0.90 / P25 −0.90 / 中位 −0.90 / P75 +0.45 / P95 +4.50 / max +5.85

### ③ 9:25 定格名单（批次 `#1797` lock，30 只）

- Δ总分：**升 25 / 降 3**，中位 **+1.80 分**，min −1.17 / max +5.85
- 新旧序重合：**Top1 1/1 · Top3 2/3 · Top5 4/5 · Top10 10/10 · Top20 18/20**
- 换位 21 只（多为 ±1~4 名）；最大：`600664` 哈药股份 #2→#6、`002564` 天沃科技 #7→#3

### ④ 🔴 置信度加成口径变化（新发现）

`conf_warn_high` 门槛为 `warn_score ≥ 0.85`：

| | 触发只数 |
|---|---|
| 旧权重（0.45/0.30/0.25） | **0 只（恒不触发）** —— 净额常数拖累，理论上限仅 0.805 |
| 新权重（0.75/0.0/0.25） | **11 只** |

因 `probLt` 双低条件永不成立（见 v4.11.37 记录），此项**只改 `confidence` 展示值、不影响名单**。

### ⑤ 真实链路抽样

`pipeline._load_strength`（生产实际调用入口）：输入 30 只 → 返回 28 只，抽样 **5/5** 与手工复算一致。

### ⑥ 服务健康

`kuaixuan` / `kx-worker` 均 **active**；部署后 3 分钟日志 Traceback/ERROR = **0**。

---

## 四、🔴 连带副作用（主人未在指令中提及，已单独量化）

**盘中 9:30-10:00 动态加分层会一并失效。**

`api/stocks._apply_intraday_ff_bonus` → `bid_strength.score_one_live_ff` 的净额层与本次移除的
**是同一层**（取 `max(竞价档, 盘中档)`）。`w_ff = 0` 后 `warn_live ≡ warn_static` ⇒
`bonus = w_warn × Δ × 100 ≡ 0` ⇒ 代码里 `if bonus <= 0: continue` 直接跳过。

- **不报错、不影响名单可用性**（该层本身设计为「独立降级」）。
- **实测影响（2026-09-23）**：原本 **12 只**被 +1~3 分；归零后 **Top5 边界换 1 只，Top10 以内成员不变**。
- **恢复方式**：把 `w_ff` 调回 `0.30`（改配置，不需改代码）；或另立「盘中加成」独立通道（需改代码，未做）。

---

## 五、回滚

```bash
# 代码（二选一，按批）
cp -a /opt/kuaixuan/backups/be_v7_20260923-204531/scorer.py       /opt/kuaixuan/backend/app/services/
cp -a /opt/kuaixuan/backups/be_v7_20260923-204531/bid_strength.py /opt/kuaixuan/backend/app/services/
cp -a /opt/kuaixuan/backups/be_v7_20260923-204531/score.py        /opt/kuaixuan/backend/app/services/picker/
cp -a /opt/kuaixuan/backups/be_v7b_20260923-204757/{score,pipeline,precompute}.py /opt/kuaixuan/backend/app/services/picker/
systemctl restart kuaixuan kx-worker
```

```bash
# 配置（只回滚权重，最轻）
# 把 settings.scoring.factors.bid_strength.w_ff 改回 0.30、w_vol_ratio 改回 0.45
# （推荐走 scripts/_kx_apply_bid_strength_v7.py 同款流程：备份 → 校验 → set → 回读 → 重启）
```

---

## 六、未做 / 边界

- **生产未部署**：`121.196.230.80` 保持旧配置（且其 `settings.scoring` 内仍为
  `0.30/0.30/0.23/0.11/0.06`、`factors.market` 为流通口径）⇒ **两机不可直接互比**。
- **未纳入的调参项**（归管理员）：`limitUp` 语义、`bidAmtFloor` 竞价额门槛、`activity` 换手口径、
  `bidLt` 在快照链路的缺失（v4.11.37 已记录，仍待裁定）。
- **`_kx_apply_bid_strength_v7.py` / `_kx_verify_bid_strength_v7.py` / `_kx_probe_ff_removal.py`**
  留存在测试机 `/opt/kuaixuan/`，可直接复用。
