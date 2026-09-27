# 竞价抢筹「刷新慢」根因定位 + 客户端缓存评估

- **日期**：2026-09-28（北京时间凌晨）
- **对象**：`/auction` 竞价抢筹 tab（左表 = 9:20→9:25 竞额抢筹；右表 = 涨幅抢筹；下表 = 9:24→9:25 最后 1 秒段）
- **触发**：主人 ——「看一下竞价抢筹页面数据刷新的很慢，有没有设置缓存，因为这个数据竞价后基本上
  只更新实时涨幅就可以了，其他的数据是不动的，是不是可以在用户端进行缓存，你评估一下」
- **结论口径**：全部数字为 **生产机 `121.196.230.80` 实测**（2026-09-28 01:20~01:35），非测试机推断。

---

## 0. 一句话结论

1. **缓存是有的，而且分了 5 层**（浏览器 / 前端内存 / 后端结果 / 后端上游 / 东财行情），设计基本正确。
2. **「慢」不是缓存缺失，而是「两级缓存同时失效时的冷取数尾巴」** —— 热路径只要 **66~84ms**，
   冷取数要 **5.2s（有数据）~ 11.5s（极冷）**，生产日志 190 次抽样 **p50=191ms / p90=4644ms**。
   根因是 **kpl 结果缓存 600s 与猫爪上游缓存 30s 严重错配**。
3. **主人的判断方向对，但结论要反过来**：客户端**不该再加缓存，而该减**。
   现在 `kplBidQiangcang` 已带 `cache: 300`（前端内存缓存 5 分钟），它与 30s 轮询**直接冲突** ——
   主人想看的「实时涨幅」实际 **5 分钟才刷一次**。把这一行的 `300` 去掉，慢的问题在体感上立解。

---

## 1. 缓存全景（回答「有没有设置缓存」）

| # | 层 | 位置 | 缓存内容 | TTL | 判定 |
|---|---|---|---|---|---|
| 1 | 浏览器 HTTP | 后端 `api/deps.py:15-17` `jr()` 设 `Cache-Control: no-store`；nginx `/api/` **无** `proxy_cache` | — | **禁用** | ✅ 正确（响应带 per-uid 副作用，绝不能被中间层缓存） |
| 2 | **前端内存** | `frontend/src/api/request.js` `memCache`，由 `api/kpl.js` 传 `cache: 300` | 整包 JSON | **300s** | 🔴 **问题所在** |
| 3 | 后端结果 | `services/kpl.py:3014` `_cached("bid_qiangcang[_YYYYMMDD]")` | 组装好的三表 | 回看 **600s** / 竞价中 **30s** / 实时非竞价 **300s** | ✅ 分层思路正确 |
| 4 | 后端上游 | `services/meoz_client.py:291` `call_cached("meoz:<api>:<params>")` | 猫爪原始表 | **30s**（`_AUC_SNAP_TTL`，见 `meoz_client.py:372`） | ⚠️ 与第 3 层错配（见 §3） |
| 5 | 东财行情 | `services/fetcher.py:1157` `_quote_map_cache`（**进程内**） | 全市场现价 | **60s**（`SPOT_CACHE_TTL`）+ 每 40s 预热 | ✅ 正确 |

静态资源另有两档（`/etc/nginx/conf.d/kuaixuan.conf`）：`location /`（index.html）`no-store`、
`location /assets/`（带内容 hash）`public, max-age=31536000, immutable` —— 均正确。

> **顺带澄清**：主人问的「有没有设缓存」——**浏览器级缓存是刻意关掉的**（`no-store`），
> 因为该响应含按用户计费的配额副作用；真正在起作用的是第 2 层（前端内存）与第 3/4 层（服务端）。
> 所以「客户端缓存」不是"要不要加"的问题，**是"这一层已经存在、而且加多了"**。

---

## 2. 「慢」的实测画像

### 2.1 生产机 HTTP 端到端（本机直连 8010，自签临时 token）

| 场景 | 第 1 次（冷） | 第 2 次 | 第 3 次 | 第 4 次 | 第 5 次 |
|---|---|---|---|---|---|
| **无 `date`（实时，落最近交易日 09-24）** | **2089.0 ms** | 73.1 | 65.6 | 66.0 | 76.3 |
| **`?date=2026-09-24`（回看，有数据）** | **5159.8 ms** | 71.3 | 83.9 | 66.9 | — |
| `?date=2026-09-25`（回看，**空日**：中秋休市） | 602.9 ms | 9.0 | 8.6 | — | — |

⇒ **热路径 66~84ms**（107 行数据、约 25KB JSON）；**冷取数 2.1s / 5.2s**。

### 2.2 生产 nginx access.log（决定性旁证）

```
GET /api/kpl/bid-qiangcang?date=2026-09-24   rt=0.186 / 6.429 / 5.366 / 6.448 s
GET /api/kpl/bid-qiangcang?                  rt=0.692 / 0.186 s
```

与 §2.1 精确吻合：**慢只出现在冷取数，且只在带 `date`（日历回看）时显著**。

### 2.3 生产日志 190 次 loader 抽样（`抢筹[result]` 自带耗时）

```
n=190   min=1ms   p50=191ms   p90=4644ms   max=55290ms   avg=1699ms
```

⇒ **中位数 191ms 很快，但 p90 有 4.6s、最坏 55s**。主人感受到的「慢」**就是这条长尾**。
（55s 那条大概率出现在竞价时段 `deep=True` 逐股查概念的路径上，本轮未复现。）

### 2.4 冷取数成本分解（cProfile，kpl 结果缓存 + 猫爪上游缓存**同时清空**）

| 分段 | 耗时 |
|---|---|
| 全冷总耗时（单次） | **11547.9 ms** |
| `call_cached` 10 次累计 | 9.061 s |
| └ `call` → `_post_one` → `net.http_get` **5 次**累计 | **7.328 s** ← **全部是猫爪上游网络** |
| 只清 kpl 层、猫爪上游仍热 | **518.3 ms** |

**逐个猫爪接口的冷耗时**（清上游缓存后单独测）：

| 猫爪接口 | 冷耗时 | 返回行数 |
|---|---|---|
| `screening_map`（实时选股，全市场） | **2795.5 ms** | 5557 |
| `free_mv_map`（自由流通市值，内部再走 screening） | **2188.1 ms** | 5904 |
| `auc_open_bid("0925")`（daily_auc，全市场） | 1291.8 ms | 5569 |
| `auc_snapshot("0925","before")`（daily_auc_detail） | 636.0 ms | 5567 |
| `auc_qc_net`（auc_kp） | 130.1 ms | 128 |

⇒ **冷尾巴 ≈ 5s 集中在两个"全市场扫描"接口**（`screening` + `free_mv_map`），
而它们产出的数据**最终只用于展示各表前 100 只**。

### 2.5 根因：两级 TTL 错配

- 第 3 层（kpl 结果）**回看 600s / 实时非竞价 300s**
- 第 4 层（猫爪上游）**统一 30s**

⇒ 第 3 层一过期，第 4 层几乎**必然也早已过期** ⇒ **每次重算都付全额冷成本**。
第 4 层的 30s 是为"竞价进行中需要实时感"设计的（`_AUC_SNAP_TTL` 注释即是此意），
但**对历史回看日毫无意义** —— 历史日的数据不可变。

> 缓解因素（事实，需如实记录）：`cached_singleflight` 让并发请求共享同一次加载，
> 所以「同一个回看日，10 分钟内只有第一个人等 5s」，其余人拿缓存。这也解释了为什么
> 主人感觉"时快时慢、有时要等好几秒"。

---

## 3. 前端 300s 缓存：为什么它才是体感元凶

### 3.1 与 30s 轮询直接冲突

```js
// frontend/src/views/AuctionView.vue:832-843
polling = usePolling(async () => {
  if (datePicker.value) return true      // 历史回看模式: 不轮询
  signalRefreshing.value = true
  loadedTabs.clear()
  const ok = await ensureTabData(tab.value, { silent: true })   // → kplBidQiangcang(dt)
  ...
}, 30000, { backoff: true })
```

```js
// frontend/src/api/kpl.js
export function kplBidQiangcang(date = '') {
  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {}, cache: 300 })
}
```

- 页面**每 30s 轮询一次**；
- 但 `cache: 300` 让 `request.js` 的 `memCache` **5 分钟内直接返回旧值，根本不发请求**；
- ⇒ **实际刷新频率 = 每 5 分钟 1 次**，30s 轮询里 **9 次被客户端缓存吃掉**。

这正好对上主人的原话：「**竞价后基本上只更新实时涨幅就可以了**」——
而实时涨幅（`realChange`）现在的刷新周期恰恰是 **300s**，不是 30s。

### 3.2 非交易日 / 盘后：轮询会被整拍跳过

`AuctionView.vue:657-664`：非交易日 `loadAll` 会把 `datePicker` 自动设为最近交易日
（并 toast「当前非交易时段，自动显示最近交易日…」）⇒ `AuctionView.vue:833` 的
`if (datePicker.value) return true` **整拍 return**，一点请求都不发。

⇒ 于是出现两种截然不同的体感：

| 场景 | `datePicker` | 轮询 | 前端 300s 缓存影响 |
|---|---|---|---|
| **交易日盘中/盘后**（`days[0] == 今天`） | `''` | **开**（30s） | 🔴 **吃掉 9/10 次刷新** |
| 非交易日 / 自动回退到历史日 | 最近交易日 | **关（整拍跳过）** | 无影响（不发请求） |
| 用户手点日历回看 | 所选日 | 关（整拍跳过） | 无影响（每点一次发一次，300s 内重复点才命中） |

⇒ **真正的伤害只在"交易日"这一种场景**，但那是主人最常用的场景。

### 3.3 服务端每次重算现涨是**刻意设计**，不要动

`backend/app/api/kpl.py:733-790`：**每次请求**都跑

- `apply_board_concept_db` + `_ensure_concepts`（非竞价时段，轻量）或 `apply_board_concept`
  `deep=True`（竞价时段）
- `_apply_change_for(l20/l20Chg/lLast, serve_date)` → 盘中调 `_update_spot_change`
  （东财全市场现涨，走第 5 层 60s 缓存 + 40s 预热，**实测热 0ms、冷 886ms**）
- `fill_bid_turnover_from_snap`

这是**必须的** —— 否则「现涨」永远不会随日期/盘中变化。所以：
**要刷新现涨，请求必须真的到达后端**；前端 300s 缓存把这条通路掐断了。

---

## 4. 硬约束：配额（决定了"不能靠调短轮询来救"）

```python
# backend/app/api/kpl.py:734
def api_kpl_bid_qiangcang(request: Request, uid: int = Depends(quota_guard("auction")), date: str = "")
```

`quota_guard("auction")`（`api/deps.py:99-128`）：

- **会员 / 管理员：直接放行，不计数、不限次**
- **免费用户：`QUOTA_AUCTION_DAILY = 1` 次/日**，`QUOTA_DEDUP_SECONDS = 10` 去重窗口；
  超额返 **429**（`code=quota_exceeded`），前端弹"开通会员"引导

⇒ 三条推论：

1. **不能靠"把轮询间隔调短"来掩盖慢** —— 免费用户第 2 次就被 429 拦住（已是既成事实）。
2. **把 `cache: 300` 去掉不会增加免费用户的配额消耗** —— 免费用户第 2 次请求本来就被 429 拒绝，
   `ensureTabData` 返回 `false` → `usePolling` 退避（30s→60s→120s…上限 5min）。
3. **对会员只是把热路径多打几次** —— 66~84ms/次、30s 一次 = 每小时 120 次，成本可忽略
   （现涨走 60s 进程内缓存 + 40s 预热，几乎总是热的）。

---

## 5. 建议

### 5.1 立即（1 行，风险极低，直接解决主人抱怨的"现涨不刷新"）

按 `date` 分流 —— 历史回看数据不可变（300s 客户端缓存**是安全的**），实时必须走网络：

```diff
// frontend/src/api/kpl.js
 export function kplBidQiangcang(date = '') {
-  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {}, cache: 300 })
+  // 2026-09-28: 历史回看数据不可变 → 300s 客户端缓存安全;
+  //             实时(无 date)必须让 30s 轮询真刷现涨 → 不缓存(否则 30s 轮询实际变成 300s)
+  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {}, cache: date ? 300 : 0 })
 }
```

**预期效果**：交易日页面现涨刷新周期 **300s → 30s**；每次请求服务端耗时 66~84ms（热）。
**风险**：免费用户配额行为不变（见 §4.2）；会员网络请求量 ×10，绝对值仍很小。

**配套检查**（`AuctionView.vue:643`）：

```js
function withTimeout(p, ms = 12000) { ... }
```

冷取数实测最坏 **11.5s**，12s 的 `withTimeout` 会**在临界点上截断**成空列表
（`withTimeout` 超时返回 `{list20: [], ...}`），表现为"点了没数据"。
建议提到 **15000ms**，或对回看日单独放宽。

### 5.2 中期（治本：消掉 5s 冷尾巴）

**核心改法：历史回看日让猫爪上游按"日键"长 TTL。**
安全性有据可查 —— `call_cached` 的键是 `meoz:<api>:<json(params, sort_keys=True)>`，
而回看路径 `kpl.py:2704-2708` 传的是**绝对日期**：

```python
_meoz_date = None
_meoz_off = 0
if date:
    _meoz_date = str(date).replace("-", "")   # 绝对日 → 进缓存键
    _meoz_off = None
```

⇒ 历史日的上游缓存键**天然按日隔离**，且该日数据不可变 ⇒ 长 TTL 无风险。
（⚠️ **实时路径不能用长 TTL**：那时 `_meoz_date=None / _meoz_off=0`，
键里是 `tradedate_offset:0` 这种**相对键**，跨自然日会窜味。）

```diff
// backend/app/services/meoz_client.py
+def _hist_ttl(apiname: str, date) -> float:
+    """回看日(绝对日期键)数据不可变 → 与 kpl 结果层(600s)同寿甚至更长，避免两级 TTL 错配。"""
+    return 3600 if date else cache_ttl(apiname)
```

并把 5 处 `ttl=_AUC_SNAP_TTL` 改为 `ttl=_hist_ttl("<api>", date)`：
`screening_map` / `free_mv_map`(内部 `screening_map`) / `auc_snapshot` /
`auc_open_bid` / `auc_qc_net`。

**预期效果**：回看日「第 2 次之后即便 kpl 层过期」的重算从 **5.2s → ≈0.5s**（实测 518ms）。
把 p90 从 4.6s 压到亚秒级。

**可选增强 2：收窄返回体。** `screening_map(symbols=...)` **已支持点查**
（`meoz_client.py:646-689`，注释实测「传 symbols 回指定行、字段齐全」）。
各表只展示前 100 只 ⇒ 可只点查这批 code，把 5557 行的全市场扫描降为百行级。
⚠️ **注意例外**：`list20` 的兜底路径 `_list20_fundflow_fallback`（`kpl.py:2592-2611`）
需要**全市场**做「自由流通市值 ≥ 2 亿」的候选筛选，那条路径不能收窄。

**可选增强 3**：kpl 结果层历史日 TTL 600s → 1800s（数据不可变，纯收益）。

### 5.3 明确不建议做的事

| 不建议 | 原因 |
|---|---|
| 给 `/api/kpl/bid-qiangcang` 加 HTTP 缓存 / nginx `proxy_cache` | 响应含 per-uid 配额副作用；且第 1 层 `no-store` 是刻意设计 |
| 调短前端轮询间隔（<30s）来"抢"现涨 | 免费用户配额 1 次/日（§4），会立刻 429；对会员也只是多打热路径 |
| 拆"只取现涨"的独立接口后**复用 `auction` 配额** | 会把现涨轮询和主接口抢同一份额度；若要做，须走**独立且不计费的 feature 键**，与主接口解耦 |
| 降低第 3 层（kpl 结果）TTL 来"提新鲜度" | 现涨本来就由 api 层每请求重算，与第 3 层 TTL 无关；降 TTL 只会放大冷尾巴 |

---

## 6. 复现脚本（本轮新增，均在 `scripts/deploy_tmp/`）

| 脚本 | 用途 | 副作用 |
|---|---|---|
| `_kx_qc_profile.py` | 测试机：loader / api 层各段计时 | 只读 |
| `_kx_be/_kx_probe_qc_prod.py` | 生产：实时接口连打 5 次 + 回看日冷热 + 缓存键自证 | 只读 + 少量无害请求；自签 token 用后即删 |
| `_kx_be/_kx_probe_qc_hist.py` | 生产：回看「有数据的历史日」冷/热（含删该日 kpl 缓存键） | 删 1 个历史日缓存键（可自愈） |
| `_kx_be/_kx_prof_qc_cold.py` | 生产：cProfile 定位（kpl 层冷） | 只读 |
| `_kx_be/_kx_prof_qc_cold2.py` | 生产：**真·全冷**（kpl + 猫爪上游都清）+ 各猫爪接口分项冷耗时 | 清猫爪缓存（30s 内自愈） |
| `_kx_be/_kx_push_and_run.py` | 推脚本到生产并执行（一次调用内完成） | — |

---

## 7. 待主人裁决

本报告**未改任何一行运行时代码**。需主人点头的两项：

- **A（立即，1 行）**：`api/kpl.js` 的 `cache` 按 `date` 分流 + `withTimeout` 12000→15000。
- **B（中期，治本）**：`meoz_client` 历史日上游长 TTL（5 处），p90 从 4.6s 压到亚秒级。

A 单独做即可解决"现涨不刷新"的体感；B 解决"第一次点回看要等 5 秒"。
