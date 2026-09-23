# v4.11.39 部署记录 —— 竞价量比换口径（今额/昨额 → 竞价成交量 ÷ 近 5 日平均每分钟成交量）

> 2026-09-24 · 主人指令：「这个先不管，现在你办量比指标修改一下，改成 竞价成交量 ÷ 近 5 日平均每分钟成交量。」
> 状态：🚧 **仅测试机（2026-09-24 01:00）；生产未部署**

---

## 一、改了什么

### 1.1 口径变更

| | 旧口径（改前） | 新口径（改后） |
|---|---|---|
| 定义 | 今 9:25 竞价额 ÷ 昨 9:25 竞价额 | **竞价成交量 ÷ 近 5 日平均每分钟成交量** |
| 性质 | 自算「竞价额同环比」（借用了量比之名） | **市场标准量比**（= 东财/通达信量比口径） |
| 数据源 | `snapshot_bid.bid_amt` 自算 | 猫爪官方成品 **`auc_vol_ratio`**（openapi 原文定义，示例值 2.18） |
| 落地 | — | 新列 `snapshot_bid.auc_vol_ratio` |

### 1.2 数据源抉择：**只走 `daily_auc`，不碰 `screening`**

| 接口 | openapi 字段清单 | 实测能否返回 | 决定 |
|---|---|---|---|
| `daily_auc` | ✅ **有** | ✅ 有值 | **唯一取数口** |
| `screening` | ❌ **无**（40 字段里只有 `auc_pct_chg/auc_net_amount/auc_amt/auc_vol/auc_turnover`） | ⚠️ 能返回（09-23 实测 5568 行全带值） | **不请求** |

🔴 为什么不把该字段加进 screening 字段串（即便实测能拿到）：

1. **收益 = 0** —— 同一采集链路 `daily_auc_amt()` 本来就被调用（`snapshot_bid` 的竞价额/涨幅都来自它），
   该字段顺手就有，**零新增调用**；
2. **风险 > 0** —— `screening` 是「不传 symbols = 一次拉全市场」的调用，字段一旦被上游拒收就是
   **422 ⇒ 整个 screening 失败 ⇒ 猫爪主源全挂**（与 2026-09-20 `pre_fd_break_amount` 事故同型）；
3. **官方未收录 = 未承诺**，随时可能收紧校验；项目铁律写明「新增 screening 字段前必须先查 openapi.json，
   或单字段试调确认 code==200」——查 openapi 是**不支持**，按铁律就不该加。

> ⚠️ **一处方法学订正**：本机盘后实测「带 `auc_vol_ratio` 的完整字段串」返回 `code=200` 但 `rows=0`，
> 属**空返回下的假绿**（服务端未走字段校验），**不能**作为字段可用的证据。
> 真正证据 = 采集脚本对两个接口的实调都成功且落盘数据有值。

### 1.3 分档重标定（等分位映射，**机械换算非调参**）

新口径值域远大于旧口径，旧边界不换会退化：

| 池内口径（竞价额≥1000万 + 自由流通 20~500 亿） | 旧口径 n=17668 | 新口径 n=18441 |
|---|---|---|
| 中位 | 1.34 | **3.32** |
| P25 / P75 / P90 | 0.74 / 2.75 / 5.92 | 1.96 / 6.88 / 15.42 |

旧边界在新旧样本的分位映射：

| 旧边界 | 旧样本分位 | **新边界** |
|---|---|---|
| 0.6 | 17.7% | **1.67** |
| 1.0 | 37.5% | **2.53** |
| 1.5 | 54.7% | **3.73** |
| 2.0 | 65.6% | **5.01** |
| 3.0 | 77.2% | **7.52** |

🔴 **不换算的后果（实测）**：新口径值配旧边界，**满分档占比 54.5%**（66 日池内）/
**62.1%**（09-23 当日池内 n=214）⇒ 该层（17% 权重里的 0.75 子权重）退化为准常数。

### 1.4 代码文件（6 个后端 + 1 个前端）

| 文件 | 改动 |
|---|---|
| `backend/app/db/database.py` | `snapshot_bid` 新增列 `auc_vol_ratio REAL NOT NULL DEFAULT 0` + 老库迁移 `ALTER TABLE` |
| `backend/app/services/meoz_client.py` | `_SCREENING_FIELDS` **不加**该字段（含理由注释）；`screening_map` docstring 标注；`daily_auc_amt` docstring 补来源说明（字段串本来就有） |
| `backend/app/services/auction_snapshot.py` | `_merge_meoz` 取 `am.get("auc_vol_ratio")`（**唯一来源 daily_auc**）；新建行/补齐行写入；`INSERT OR REPLACE` 16→17 占位符 |
| `backend/app/services/bid_strength.py` | 层① 改标准口径；`_fill_snapshot` 探测新列并优先读；旧口径降级为**回退**（仅历史行，且已有标准值时不回退，防两口径混用） |
| `backend/app/services/scorer.py` | `DEFAULT_SCORING.factors.bid_strength.buckets` 换新边界（含换算依据注释） |
| `backend/app/services/kpl.py` | `_boom_from_snap` 标准口径优先（阈值 `2.0→5.01`，无值回退旧口径 `2.0`）；`fill_bid_ratio_yest` 标准口径优先；docstring 同步 |
| `frontend/src/views/AuctionView.vue` | 爆量页量比高亮阈值 `2/1.5 → 5.01/3.73`（等分位同步） |

### 1.5 配置（测试机 `settings.scoring.factors.bid_strength.buckets`）

🔴 **DB 内该段存在（v4.11.38 写入），会覆盖代码默认值 ⇒ 必须同步改 DB**，否则新版代码 + 旧分档 = 退化。

本次**只替换 `buckets` 一个键**，其余键原样保留（不动主人的其他调整）：

```json
"buckets": [["7.52","9999",1.0],["5.01","7.52",0.85],["3.73","5.01",0.7],
            ["2.53","3.73",0.55],["1.67","2.53",0.4],["0","1.67",0.25]]
```

未动：`label/unit/default(0.22)/w_vol_ratio(0.75)/w_ff(0.0)/w_ai(0.25)/ff_buckets/ff_default/ai_buckets/ai_topn/ai_default`，
顶层权重仍为 `0.28/0.27/0.30/0.10/0.05`（主人 09-23 20:07 设定）。

写入路径按 v4.11.37 固化规范：**备份原始字节 → `_validate_scoring` → `settings.set` → 回读核对**（**不裸改 DB**）。
脚本：`scripts/_kx_apply_bid_strength_v8.py`（模板同 `_kx_apply_bid_strength_v7.py`）。

---

## 二、部署步骤与事实

1. **本机回归**：相关子集 `93 passed`；全量 `1251 collected`（进度 100% 无 F/E；汇总行被已知 safe-delete 钩子吃掉）；
   前端单测 **75 passed / 0 fail**。
2. **行尾自动对齐**（`_kx_stage_match.py`）：6 文件中 `auction_snapshot.py`、`kpl.py` 做了 CRLF→LF 转换；
   其余按线上现状。上传后**回读远端 md5 三方一致**：
   - `db/database.py` `d775e2907463f9e5180a67c2bf967493`
   - `services/auction_snapshot.py` `f5b32291b9fc6bfd8c9fb323bd3cc296`
   - `services/bid_strength.py` `933aad5df0f0d50e3139dad2af1e8bc6`
   - `services/kpl.py` `0cae838c62b8f04ce223ae0cbd950965`
   - `services/meoz_client.py` `c5c1f7540396ebf906ea08ccdb7aa1b7`
   - `services/scorer.py` `1159a1977dbe2927c2afa8bb5c6ed31f`
3. **备份（3 份）**：
   - `/opt/kuaixuan/backend_bak_vr39_20260924-005756/`（6 文件）
   - `/opt/kuaixuan/kuaixuan.db.bak_20260924_005808`（SQLite `backup()`）
   - `/opt/kuaixuan/backups/settings_scoring_v8bak_20260924-005827.json`（评分配置原始字节 1570 B）
4. **两阶段部署**：Stage1（暂存 md5 → 备份 → 落盘 md5 三方 → `py_compile`）`B1 DONE`
   → Stage2（清 `__pycache__` → 原子替换 → `systemctl restart kuaixuan kx-worker`）`B2 DONE`。
5. **DB 迁移**：由服务启动时 `database.init_db()` 的 `ALTER TABLE` 自动完成（实测列已存在）。
6. **配置写入**：`EXIT=0`，`VALIDATE_ERR ''`、`SET_OK True`、`READBACK_MATCH True`、`READBACK_REST_UNCHANGED True`。

---

## 三、验证证据（测试机真实数据）

**端到端验证 13 PASS / 0 FAIL**（脚本 `scripts/_kx_verify_vr39.py`）：

| 段 | 内容 | 结果 |
|---|---|---|
| A | `_SCREENING_FIELDS` 不含该字段 / `daily_auc_amt` 含之 / `_merge_meoz` 只从 `am` 取 | ✅ 3/3 |
| B | `get_scoring_cfg(force=True)` 合并后 buckets = 新边界；子权重仍 `0.75/0/0.25` | ✅ 2/2 |
| C | `snapshot_bid` 已迁移出 `auc_vol_ratio` 列（20 列；总 633,299 行 / 9_25 158,620 行；日期 08-09~09-23） | ✅ 1/1 |
| D | `daily_auc` 实调（20260923）：5568 行 / 有值 5567 / **非零 5464（98.1%）**，P50 0.72 P90 2.84 | ✅ 1/1 |
| E | **`_merge_meoz` 真实链路**：5561 行中 **5170 行量比非零（93.0%）**（样本：平安银行 0.55 / 万科A 4.38） | ✅ 1/1 |
| F | 全市场口径分档未退化 | ✅ 2/2 |
| G | `kpl.fetch_bid_boom()` 正常返回 **139 行**，字段齐（`bidRatioYest` 等） | ✅ 1/1 |
| H | **池内口径分档（09-23，n=214）** | ✅ 2/2 |

**H 段明细（最有说服力）**：

| 边界 | 0.25 | 0.40 | 0.55 | 0.70 | 0.85 | 1.00 |
|---|---|---|---|---|---|---|
| 新口径 + 旧边界（不换算） | 0.9 | 6.5 | 7.5 | 9.3 | 13.6 | **62.1** |
| **新口径 + 新边界（已实施）** | 20.6 | 13.1 | 12.1 | 9.3 | 12.1 | 32.7 |
| 66 日基准（旧口径+旧边界） | 17.7 | 19.7 | 17.2 | 10.9 | 11.6 | 22.8 |

**66 日池内对照（本地零网络，`_kx_vr_pool_check.py`）**：

| 组合 | 0.25 | 0.40 | 0.55 | 0.70 | 0.85 | 1.00 |
|---|---|---|---|---|---|---|
| 旧口径 + 旧边界（现状语义） | 17.7 | 19.7 | 17.2 | 10.9 | 11.6 | 22.8 |
| 新口径 + 旧边界（不换算） | 0.1 | 3.2 | 10.4 | 12.2 | 19.8 | **54.5** |
| **新口径 + 新边界（本次实施）** | **17.7** | **19.6** | **17.3** | **11.0** | **11.6** | **22.8** |

⇒ **逐档差 ≤0.1pp ⇒ 等分位换算精确保持了原打分语义**。

**逐日稳定性（池内，最新 8 日）**：中位 3.27~4.04、最低档 10.5%~22.9%、满分档 22.2%~33.8% ⇒ 无当日退化。

**服务状态**：`kuaixuan` active / `kx-worker` active；日志 Traceback 计数 **0**。

---

## 四、实测代价（上次会话已跑，供决策参考）

目标函数 = 主人 09-23 拍板的「**买入当天不深跌**」（`p0 = 9:25 竞价成交价`，`MDD = (low−p0)/p0`）。

| 用法（贴近线上池，65 天） | Top5 MDD | Top5 当天收益 |
|---|---|---|
| 现行口径 · 正向取大（= 改前线上用法） | −3.62% | −0.69% |
| **新口径 · 正向取大（本次实施，保持「越大越好」语义）** | **−4.29%** ⚠️ | +0.65% |
| 新口径 · 反向取小 | −3.62% | −0.46% |

- 新口径对当天回撤**无显著预测力**（ρ=+0.0111 t=+0.42；现行 ρ=+0.0142 t=+0.91，两者都是噪声级）；
- 与系统既有的「竞价换手率」因子 **ρ=+0.7819 高度重复**；
- ⇒ **换口径后在「不深跌」目标下 Top5 回撤恶化约 0.67pp**（收益方向反而更高，属高波动特征）。

⚠️ **同时已实施的是「评分层 + 展示层都换」**（报告曾建议只改展示层）。
若主人想**回退到「只改展示层」**，只需两步（可随时做）：
① 配置层把 `factors.bid_strength.buckets` 换回旧边界（走 v8 同款写入路径）；
② `bid_strength._fill_snapshot` 停止读 `auc_vol_ratio`（或把 `w_vol_ratio` 调 0）。
落库列、采集字段、展示层均保留 —— **回退成本低**。

---

## 五、上线状态

| 环境 | 状态 |
|---|---|
| 测试机 `47.99.153.123` | ✅ **已上线** —— 后端 + 配置：2026-09-24 00:57~01:00；**前端换盘：01:09:45** |
| 生产 `121.196.230.80` | 🚧 **未部署**（须主人明确指令） |

**前端换盘事实（两阶段，脚本 `scripts/deploy_tmp/_deploy_fe.sh`）**：

- 入口 `index-Bsi3Utuy.js` → **`index-OFw0lMQg.js`**（`AuctionView-Dbw9fkxR.js` → `AuctionView-DTOzo6_X.js`）
- 备份 `/opt/kuaixuan/dist_bak_20260924-010945`；暂存解包 1035 文件 / 1031 assets，与线上同规模
- 权限目录 755 / 文件 644（Windows tar 带 666，不修则 nginx 403）
- 验证：`/` 200、`/index.html` 200、新入口 200、**旧入口 404**；`nginx -t` ok；两服务 active
- **外网复核**：`http://47.99.153.123/` 200；线上 `AuctionView-*.js` 含 `bidRatioYest>=5.01`×1、`>=3.73`×1，
  旧阈值 `>=2?` / `>=1.5` 残留 **0**；**HTTP 取回体的 md5 与磁盘一致**（`956fd8d3c618…`）⇒ nginx 确实在服务新产物

🔴 **生产部署前必须一并做的**：同步生产 DB 的 `factors.bid_strength.buckets`（生产该段内容与测试机不同，
且 `factors.market` / `factors.bid` 亦存在已知分叉，见 AGENTS 环境分叉表）。

---

## 五之二、改动面审计（回应「有没有瞎改」）

前端产物与线上现产物做**去哈希后的全量逐字节比对**（按「文件名剥掉 `-xxxxxxxx` 后缀」配对，共 1035 个文件）：

| 项 | 结果 |
|---|---|
| 文件集合 | **完全一致**（1035 vs 1035，无只在单侧的条目） |
| 去哈希后逐字节**相同** | **1033 / 1035** |
| 去哈希后**仍不同** | **2 个** —— ① `assets/AuctionView.js`：唯一差异就是 `bidRatioYest>=2/>=1.5` → `>=5.01/>=3.73`；② `assets/AuctionView.css`：仅 Vue scoped 属性 `data-v-78f7cf6b` → `data-v-e7053b0a`（随 `.vue` 源码变化自动生成，无实质差异） |

⇒ 其余 33 个 chunk 的 hash 变化（`VipGate`/`pool`/`tdx`/`stats`/`useSortable` …）经核对**只差内嵌的
import 路径**（如 `from"./index-Bsi3Utuy.js"` → `from"./index-OFw0lMQg.js"`），属 Vite 的**哈希级联重算**，
**不是源码版本漂移**。前端本次改动是外科式的：**只动了 `AuctionView.vue` 一个文件、2 行**。

---

## 六、顺带发现

- 🔴 **现行「竞价量比」因子在「当天不深跌」目标下完全无信息**（ρ=+0.0142/t=+0.91，池1 亦 −0.0030/t=−0.19）
  ⇒ 17% 异动分里的量比档（0.75 子权重）目前约等于噪声。
- ⚠️ 现行口径**反向取小反而更好**（Top5 MDD −3.43% vs 正向 −3.62%），但 t 不显著，仅作观察。
- 🆕 **`screening` 的 openapi 文档滞后**：实测服务器支持但文档未收录的字段（`auc_vol_ratio` 已证实），
  ⇒ **不能仅凭 openapi 判定「服务器不支持」**，反之亦然 —— 两者都要验。

---

## 七、口径边界：全站叫「量比」的四样东西（锁定本次改了什么 / 没改什么）

⚠️ 系统里有 **4 个**不同的东西都挂着「量比」这个名字，互不相通。本次只动了其中**口径确实是
「竞价量比」的两处**（即主人指令所指），另两处**不是这个口径**，故未动。

| # | 位置（file:line） | 界面可见处 | 口径 | 本次 |
|---|---|---|---|---|
| ① | `services/bid_strength.py` 层① `bid_vol_ratio` → `scorer.DEFAULT_SCORING.factors.bid_strength.buckets` | 管理后台「竞价强度」因子的分档；**间接决定选股名单排序**（占 `w_warn`30% × `w_vol_ratio`0.75） | 旧=`今9:25竞价额÷昨9:25竞价额`（**额比**，借用了量比之名）→ 新=`竞价成交量÷近5日平均每分钟成交量` | ✅ **已改** |
| ② | `services/kpl.py` `_boom_from_snap` / `fill_bid_ratio_yest` → `views/AuctionView.vue` `bidRatioYest` 列 | 「竞价异动」页 →「竞价爆量」Tab →「竞价量比」列（表头就叫竞价量比） | 与 ① 完全相同（同一指标，两处落点） | ✅ **已改**（含过滤阈值 2→5.01、高亮阈值 2/1.5→5.01/3.73） |
| ③ | `services/kpl.py`（板块行情 `volRatio`，`row[9]`/`row[21]`） → `views/MarketView.vue` / `views/ConceptView.vue`「量比」列 | 「市场雷达」/「题材异动」页 | **板块量比**，由上游（开盘啦）板块行情直接给出；我方**只展示、无自有计算公式** | ❌ 未动（无公式可改；且板块无「竞价成交量」概念） |
| ④ | `services/fetcher.py:993` `"volRatio": _parse_float(s.get("f10"))` → `stores/stocks.js` `it.volRatio` | **选股首页表格并不显示这一列**（列头为：排名/名称/现涨/竞涨/实体/竞额/主力净额/自由流通/评分/可信/概念） | 东财 `f10` 盘中量比 = 「当前累计成交量 ÷ 近5日每分钟均量」——**非竞价口径** | ❌ 未动（口径不是主人给的那个；且未显示） |

补充：老链路的 `services/picker/score.py` 也有个 `bid_vol_ratio` 字段，但恒 `0.0`（死代码，量比加成从未触发，
见该文件 L76 注释），无改动价值。
