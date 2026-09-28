# 勘察报告 · 把「锁定」的底层算法从竞价换成盘中实时

> **勘察时间**：2026-09-28 15:2x
> **需求**：点「锁定」时，用盘中实时（spot）六因子算出一份名单 → 落批次固定下来 → 9:30 后照旧不被跌出洗掉
> **状态**：**仅勘察，零代码改动**
> **结论速览**：**可行，但有 1 个必须先解决的问题（NOT NULL 字段缺口）和 3 个需要决策的点**

---

## 一、勘察的核心发现

### 1.1 ✅ 好消息：「锁定」与「算法」是解耦的

锁定链路（`stores/stocks.js` 第 526 行起）：

```js
const action = (isBefore930() || (force && isBeforeRelockEnd())) ? 'lock' : 'refresh'
data = await fetchStocks(action, this.buildFilterParams(), 'auction', force && action === 'lock')
```

`strategy` 是**独立传参**（第 3 个参数，现在写死 `'auction'`）。后端 `action=lock` 的处理是
**「重算 → 落库 → 存快照」**，这套流程与「用什么算法算」**没有耦合**。

⇒ 换算法 = 换掉 `strategy` 指的那个引擎，**落库/快照/merge 那套原封不动**。

### 1.2 ✅ 好消息：pipeline 是分层的，只有两层是竞价专用

`backend/app/services/picker/pipeline.py::run()` 的分层结构：

| 层 | 代码位置 | 是否竞价专用 | 换 spot 要动吗 |
|---|---|---|---|
| ① 名单源 | `_fetch_list` L246 | 否（拿全市场行情） | ❌ 不动 |
| ② 粗筛 | `coarse_filter` L265 | **是**（排队键是定格竞价涨幅） | ⚠️ 要看 |
| ③ 昨日涨幅 | `fill_yesterday` L275 | 否 | ❌ 不动 |
| ④ 补丁源 | `_fetch_patch` L280 | 否（补实时展示字段） | ❌ 不动 |
| ⑤ 竞价强度 | `_load_strength` L311 | **是**（竞价强度因子） | ⚠️ 要看 |
| **⑥ 评分** | `score_rows` L319 | **是** | 🔴 **要换** |
| **⑦ 精筛** | `apply_filters` L320 | **是** | 🔴 **要换** |
| ⑧ 输出组装 | L329-354 | 部分（`bidRatio`/`accel`） | ⚠️ 要看 |

**关键**：spot 引擎的签名与竞价**同构**：

```python
# 竞价
score_rows(rows, cfg, strengths)          → List[ScoredRow]
apply_filters(cand_rows, filters, fctx)   → FilterOutcome

# 盘中实时（签名兼容：都是 QuoteRow → ScoredRow / FilterOutcome）
compute_score_spot(row, zt_info, cfg)     → SpotScoreResult
apply_spot_filters(rows, f, ctx)          → FilterOutcome
```

⇒ **技术上可以在 `run()` 里按 `strategy` 分支**，不需要复制整个 pipeline。

### 1.3 🔴 坏消息：批次表有 4 个 NOT NULL 列 spot 填不上

`backend/app/db/database.py` 第 43-62 行的表结构：

```sql
CREATE TABLE batch_stocks (
    ...
    probability     INTEGER NOT NULL,   -- ✅ spot 有
    confidence      INTEGER NOT NULL,   -- ✅ spot 有
    bid_change      REAL NOT NULL,      -- ⚠️ spot 无（仅展示字段，可为 null）
    real_change     REAL NOT NULL,      -- ✅ spot 有（且是核心因子）
    entity_change   REAL NOT NULL,      -- ✅ spot 有
    bid_turnover    REAL NOT NULL,      -- 🔴 spot 无（spot 是实时 turnover，口径不同）
    warn_type       INTEGER NOT NULL,   -- 🔴 spot 无（竞价强度档位，spot 没这个概念）
    circulation_mv  REAL NOT NULL,      -- ✅ spot 有
    industry        TEXT,               -- ✅
    concept         TEXT,               -- ✅
    bid_amt         REAL NOT NULL       -- 🔴 spot 无（spot 刻意不消费竞价额）
)
```

**4 个字段有缺口**：`bid_change`、`bid_turnover`、`warn_type`、`bid_amt`。

`history.py` 第 54-58 行有**现成的兜底机制**：

```python
_NOT_NULL_DEFAULTS = {
    "probability": 0.0, ..., "warnType": 0, "circulationMV": 0.0, "bidAmt": 0.0,
}
```

但注意第 76-82 行的设计意图：

> `_safe_num` 把 None 兜成 0 以绕过 NOT NULL 约束，但 0 是有业务含义的实测值…
> 于是"未知"伪装成了"实测 0" —— 9/11 现涨全 0 即此类误导。
> 落库仍存 0…同时记下哪些字段是"兜底出来的"，读侧据此还原为 null

**⇒ 好消息：有 `miss_fields` 机制**，兜底成 0 的同时会打标，读侧还原成 `null`（前端显示「—」）。
**⇒ 但直接复用会让锁定名单里有一列恒为「—」** —— 这引出决策点。

---

## 二、需要决策的 3 个点

### 决策点 1：`bid_change` / `bid_turnover` / `bid_amt` 怎么填？

spot 名单里没有竞价口径的值。三个选项：

| 方案 | 做法 | 后果 |
|---|---|---|
| **A. 填 9:25 定格值** | 落库时补读 `snapshot_bid`，把定格竞涨/竞额/竞价换手填进去 | 名单里有"竞价信息"作**参考列**（不参与评分），语义诚实 |
| **B. 留空打标** | 走 `miss_fields`，读侧还原 null | 这三列恒显示「—」，但**绝对不误导** |
| **C. 改表结构** | 加 `seal_ratio`/`seal_fund` 等 spot 专属列 | 改动最大，但语义最干净 |

**倾向 A** —— spot 接口本身**已经在下发** `bidChange`/`bidAmt`（v4.11.78 刚修的），
数据现成、零额外成本；用户看锁定名单时「这票竞价多少」是有价值的参考。

### 决策点 2：`warn_type`（竞价强度）怎么办？

这是**真正没有对应物**的字段 —— 竞价强度是「9:25 买盘强度」，spot 完全没这个概念。

- 填 0 安全（表结构允许，且有 `miss_fields` 打标）
- 但**如果锁定名单表格还展示「异动/强度」列，它会恒为空**

→ 需确认：锁定名单表格里有这列吗？有的话要不要一并隐藏？

### 决策点 3：粗筛（`coarse_filter`）和竞价强度（`_load_strength`）要不要跳过？

| 层 | spot 需要吗 |
|---|---|
| 粗筛 `coarse_filter` | ⚠️ 排队键是**定格竞价涨幅**，对 spot 不适用。spot 接口现在**不做粗筛**（全市场直评） |
| 竞价强度 `_load_strength` | 🔴 **必须跳过** —— spot 评分不含该因子，加载它是纯浪费（要拉快照表 + AI 推理） |

→ spot 现在**无粗筛**，全市场直接评分，实测约 1.x 秒（可接受）。
  若走锁定的 pipeline，是否要加 spot 专用粗筛（为性能）？

---

## 三、改动清单（待确认后执行）

### 3.1 后端

| 文件 | 位置 | 改动 | 风险 |
|---|---|---|---|
| `api/stocks.py` | L673-675 | **放行** `strategy=spot`（现在硬拒 400） | 🟡 中 |
| `api/stocks.py` | L690 | 闸门 `pick_window_guard` → **spot 应跳过**（盘中无 9:26 概念） | 🟢 低 |
| `api/stocks.py` | L707 | 当日幂等分支判 `strategy == "auction"` → spot 要不要同款？ | 🟡 中 |
| `api/stocks.py` | L945/963 | `action=lock` 落库分支 → spot 落库 | 🔴 **高** |
| `picker/pipeline.py` | L313-320 | 评分+精筛按 strategy 分支 | 🔴 **高** |
| `services/history.py` | L140-160 | 落库字段映射（4 个缺口字段处理） | 🔴 **高** |

### 3.2 前端

| 文件 | 改动 | 风险 |
|---|---|---|
| `stores/stocks.js` L526 | `fetchStocks(..., 'auction', ...)` → 传 `strategy` | 🟢 低 |
| `stores/stocks.js` L132-135 | 注释需更新（现写"spot 不落批次"） | 🟢 低 |
| `FilterPanel.vue` | spot 分支复用（已存在，v4.11.75 做的） | 🟢 低 |

### 3.3 测试

- 落库测试：spot 名单能否成功落批次（**重点验 NOT NULL**）
- 幂等测试：spot 的当日幂等行为
- 端到端：锁定 → 9:30 后 merge → 名单不漂移

---

## 四、🔴 三个必须提醒的风险

### 风险 1：`/api/stocks` 已 71K，且「无开关可回滚」

`AGENTS.md`「6 条必背」第 1 条：

> **选股只有一条链路 = `picker.pipeline.run()`，无开关可回滚**，回滚只能 git 回版本

往 `run()` 里插分支 = 在**唯一链路**上加条件。出错时**没有开关能关掉**，只能 git 回退 + 重启。

**缓解建议**：加一个 `settings` 开关（如 `spot_lock_enabled`），出问题能立刻关掉 ——
这不违背那条规则的初衷（规则本意是"别指望开关救你"，但**新增功能**给它加开关是合理的）。

### 风险 2：`batch_stocks` 是「9:30 后回看」的数据源

锁定名单落库后，**历史回看 / 复盘 / 9:30 后 merge** 全读这张表。字段填错会**污染历史数据**。

**缓解建议**：先只推测试机；用 `batches.filters` 或新加一列标记「本批次是 spot 策略」，
避免 spot 批次与竞价批次混淆（两者的 `bid_change` 语义完全不同）。

### 风险 3：语义混淆 —— 两种「锁定」会共存

如果 spot 与 auction **两个 tab 都能锁定**，`batches` 表会有两类批次，
`loadLockedBatchFromServer()` 取「今天最近一次 lock」时**可能取错**。

**缓解建议**：`batches.filters` 已存筛选参数 JSON —— 若把 `strategy` 也存进去，
读取时就能按策略区分。**这点要确认清楚再动手。**

---

## 五、建议的推进步骤

**分三步走，每步可独立验证**：

1. **第一步（先行验证）**：只改后端 `api/stocks.py` 放行 `strategy=spot`，
   让 `action=filter` 走 spot 引擎（**先不碰 lock/落库**）。
   → 验证点：spot 名单能经 `/api/stocks` 正常返回，字段映射正确。
2. **第二步**：解决 4 个 NOT NULL 字段的填充策略（决策点 1、2 定案后）。
3. **第三步**：接 `action=lock` 落库 + 前端传参。

**先做第一步的价值**：风险最低（不碰落库、不污染历史数据），
但能验证「pipeline 里插 spot 分支」这个最核心的技术假设。若第一步走不通，后两步方案要重设计。

---

## 六、待确认清单

1. **决策点 1**：`bid_change`/`bid_turnover`/`bid_amt` 三列 —— 填定格值（A）还是留空打标（B）？
2. **决策点 2**：`warn_type` 恒 0，锁定名单表格里的「异动/强度」列要不要一并隐藏？
3. **决策点 3**：spot 要不要加粗筛？（现在无粗筛，全市场直评约 1.x 秒）
4. **是否接受**：加 `spot_lock_enabled` 开关（风险 1 缓解）？还是坚持不加？