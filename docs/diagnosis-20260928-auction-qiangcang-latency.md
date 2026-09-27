# 竞价异动页「竞价抢筹」Tab 首次加载要等 5~9 秒 —— 真实浏览器实测定位

- **日期**：2026-09-28（北京时间凌晨，非交易时段）
- **修订**：本文**取代**同日早前的同名初版。初版把主人反馈的"慢"误解为"现涨不刷新"，
  方向错了；主人澄清「**是打开网页其他板块数据都出来了，他的数据需要5秒才出来，不是因为实时涨幅，
  是整个页面**」后重新实测，结论如下。初版中仍然成立的旁证（5 层缓存清单、配额约束、盘中
  `cache:300` 压制 30s 轮询）保留在 §6 / §7。
- **全部数字为生产机 `121.196.230.80` + 本机真实 Chromium 实测**（2026-09-28 01:30~01:50），非推断。

---

## 0. 一句话结论

**现象属实，且能稳定复现**：竞价异动页打开后，其他 Tab（爆量/委买/净额/昨涨停/昨断板/昨上榜）
**1 秒内全部出数据**，唯独「**竞价抢筹**」要等 **5.5~9 秒**，期间表格是空的。

**根因**：该 Tab 的取数链路要打 **5 个猫爪上游接口**，其中 4 个是「**全市场 5000+ 行**」的大结果
（`screening` 5557 行 / `free_mv_map` 5904 行 / `auc_snapshot` 5567 行 / `auc_open_bid` 5569 行），
而这些上游结果的缓存 TTL **只有 30 秒**（`_AUC_SNAP_TTL`）。所以只要结果层缓存（回看 600s）一过期，
上游必然也过期 ⇒ **每次重算都要付出全额冷取数成本 ≈ 5.5 秒**。

**量化**：上游缓存热时重建结果层只要 **345 ms**；上游冷时要 **5.5~6.9 s** —— **差 16~20 倍**。

---

## 1. 现象复现（真实浏览器，等价主人登录操作）

本机 Chromium + puppeteer 打开 `https://www.kuaixuangu.cn/auction`（注入会话等价登录），
先清空生产 `kpl:` 结果层缓存制造冷态，然后逐个点 Tab 计时：

| Tab | 触发的接口 | 接口耗时 | 点击到数据就绪 |
|---|---|---|---|
| 竞价爆量 | `kpl/bid-boom?date=2026-09-24` | 136 ms | 666 ms |
| **竞价抢筹** | `kpl/bid-qiangcang?date=2026-09-24` | **5559 ms** | **6190 ms** |
| 竞价委买 | `kpl/bid-seal?date=2026-09-24` | 68 ms | 663 ms |
| 竞价净额 | `kpl/bid-net?date=2026-09-24` | 119 ms | 666 ms |
| 昨涨停 | `kpl/yest-zt?date=2026-09-24` | 170 ms | 665 ms |
| 昨断板 | `kpl/yest-broken?date=2026-09-24` | 175 ms | 661 ms |
| 昨上榜 | `kpl/lhb?date=2026-09-24` | 219 ms | 670 ms |

**逐秒采样**（另一次运行，同样先清缓存）：

```
[竞价委买 1.0s]  {"rows":97,  "active":"竞价委买"}     ← 1 秒就有 97 行
[竞价抢筹  1s]   {"rows":2,   "active":"竞价抢筹"}     ┐
[竞价抢筹  2s]   {"rows":2}                            │ 空表，一直在转
[竞价抢筹  5s]   {"rows":2}                            │
[竞价抢筹  8s]   {"rows":2}                            ┘
[竞价抢筹  9s]   {"rows":101, "active":"竞价抢筹"}     ← 第 9 秒才出数据
```

截图证据（`scripts/deploy_tmp/_kx_be/`）：
`shot_A_seal_1s.png`（竞价委买 1 秒已满屏）、`shot_B_qc_1s.png`（竞价抢筹 1 秒空表）、
`shot_C_qc_done.png`（竞价抢筹数据到齐）。

### 为什么这时候是"带 date"的？

`AuctionView.vue:655-665`：非交易日打开页面 → `loadAll()` 拿 overview 后把 `datePicker`
自动设为最近有数据的交易日（本次实测 = `2026-09-24`）并 toast 提示。
⇒ 此后**每个 Tab 的请求都带 `?date=2026-09-24`**（回看模式）。

---

## 2. 全冷横向对比：只有竞价抢筹断层

清空 `kpl:` 结果层后，逐个接口全冷取数（生产机本机直连 8010）：

| 接口（带 `?date=2026-09-24`） | 全冷耗时 | 返回行数 |
|---|---|---|
| `bid-seal` 竞价委买 | 11 ms | 97 |
| `bid-boom` 竞价爆量 | 35 ms | 55 |
| `bid-net` 竞价净额 | 64 ms | 49 |
| **`bid-qiangcang` 竞价抢筹** | **6443 ms** | 6（左表主表行数少，另有 101 行分表） |
| `yest-zt` 昨日涨停 | 124 ms | 51 |
| `yest-broken` 昨断板 | 117 ms | 15 |
| `lhb` 龙虎榜 | 207 ms | 63 |
| `yidong-realtime / hot / monitor` | 157~169 ms | 13/15/4 |

⇒ **竞价抢筹比其他 Tab 慢 30~580 倍**，完全对得上主人的描述。

---

## 3. 5.5 秒花在哪：冷热分层实测

在生产机直接调 service 层，把「结果层」与「猫爪上游层」分别清掉做对照：

| 状态 | 耗时 |
|---|---|
| ① 两级都热（结果层命中） | **2 ms** |
| ② **上游热 / 结果冷**（只清结果层，上游 30s 内仍有效） | **345 ms** |
| ③ 全冷（两级都失效）—— **主人当前遇到的就是这一档** | **4570~6879 ms** |
| ④ 结果层已重建（热） | 2 ms |

`②=345ms` 与 `③≈5.5s` 的差距，就是「上游缓存有无」的全部代价。

### cProfile（②状态，总 0.479s）热点

```
ncalls  cumtime  function
     4    0.265  meoz_client.py:646 screening_map
     3    0.234  meoz_client.py:543 free_mv_map
    10    0.224  meoz_client.py:419 _sym_rows        ← 遍历 5000+ 行建索引
    10    0.111  json/decoder.py:343 raw_decode      ← 从 SQLite 反序列化大结果
```

⇒ 即便上游全热，**光把 4 份 5000+ 行结果从缓存取回并建索引，也要 0.3~0.5 秒**。

### 各猫爪上游接口冷/热单次耗时

| 接口 | 冷 | 热 | 行数 |
|---|---|---|---|
| `screening_map` | 2008 ms | 47~51 ms | 5557 |
| `auc_snapshot("0920","before")` | 1722 ms | 20 ms | 5567 |
| `auc_open_bid("0925")` | 1549 ms | 29 ms | 5569 |
| `free_mv_map`（内部再走 screening） | 1379 ms | 88 ms | 5904 |
| `auc_qc_net` | 284 ms | 5 ms | 128 |

---

## 4. 根因：两级 TTL 错配（30s vs 600s）

| 层 | 位置 | TTL |
|---|---|---|
| **kpl 结果层** | `services/kpl.py` `_cached("bid_qiangcang[_YYYYMMDD]")` | 回看 **600s** / 竞价中 30s / 实时非竞价 300s |
| **猫爪上游层** | `services/meoz_client.py:372` `_AUC_SNAP_TTL = 30` | **30s**（统一） |

- 结果层命中 → **2 ms**（10 分钟内再访问都是这个速度，所以主人会觉得"有时很快"）
- 结果层过期 → 上游 30s 早已过期 → **全额冷取数 5.5s**
- `cached_singleflight` 让并发者共享同一次加载 ⇒「同一回看日，只有第一个等 5 秒，其余拿缓存」，
  这正是主人感受到的「**时快时慢**」。

**上游 30s 的设计意图**是为"竞价进行中要有实时感"，这对**历史回看日毫无意义** —— 历史日数据不可变。

---

## 5. 修正：关于前端 `cache: 300`（**不是**本次问题的原因）

初版把 `api/kpl.js` 的 `cache: 300` 当成"体感元凶"。**对本次现象这不成立**：
`memCache` 是**首次请求之后**才写入的，而主人抱怨的是**首次打开就要等 5 秒** ——
首次必然缓存未命中，与本行无关。

它真正的问题是另一件事（**盘中模式**下把 30s 轮询压成 300s），详见 §6.3，与本次现象应分开处理。

---

## 6. 仍然成立的旁证（初版保留）

### 6.1 缓存共 5 层，设计基本正确

| # | 层 | 位置 | TTL |
|---|---|---|---|
| 1 | 浏览器 HTTP | `api/deps.py` `jr()` 一律 `Cache-Control: no-store`；nginx `/api/` 无 `proxy_cache` | 禁用（刻意） |
| 2 | 前端内存 | `frontend/src/api/request.js` `memCache`（`kplBidQiangcang` 传 `cache: 300`） | 300s |
| 3 | 后端结果 | `services/kpl.py` `_cached(...)` | 600 / 30 / 300s |
| 4 | 后端上游 | `services/meoz_client.py` `call_cached` | **30s** ← 本次根因 |
| 5 | 东财行情 | `services/fetcher.py` `_quote_map_cache`（进程内） | 60s + 40s 预热 |

### 6.2 缓存后端是 SQLite，不是 Redis

`config.CACHE_BACKEND = "sqlite"`（无 `redis` 模块）⇒ `cache_store.SqliteCacheStore`，
**跨进程 JSON 序列化**。所以第 3 层每次取回都是**新对象**，
`api/kpl.py` 每请求重跑 `_apply_change_for`（现涨）是**刻意且必须**的 —— 否则现涨不会刷新。

### 6.3 盘中模式：`cache: 300` 确实会压住 30s 轮询

```js
// AuctionView.vue:832-843 —— 轮询 30s
polling = usePolling(async () => {
  if (datePicker.value) return true      // 回看模式整拍跳过
  loadedTabs.clear()
  const ok = await ensureTabData(tab.value, { silent: true })
}, 30000, { backoff: true })
```

| 场景 | `datePicker` | 轮询 | `cache:300` 影响 |
|---|---|---|---|
| 交易日盘中（`days[0]==今天`） | `''` | 开（30s） | 🔴 实际刷新被压成 300s |
| 非交易日 / 自动回退 | 最近交易日 | 关（整拍跳过） | 无（不发请求） |
| 手动日历回看 | 所选日 | 关 | 无 |

⇒ 这是**独立于本次现象**的第二个问题，建议一并修（§7 建议 C）。

### 6.4 硬约束：`quota_guard("auction")`

- 会员 / 管理员：直接放行，不限次
- **免费用户：1 次/日**（`QUOTA_AUCTION_DAILY`），`QUOTA_DEDUP_SECONDS=10`，超额 429
⇒ **不能用"调短轮询"来掩盖慢**；但把 `cache: 300 → 0` 不会增加免费用户配额消耗
（第 2 次请求本来就被 429 拦下）。

---

## 7. 建议（按性价比排序）

### A. 治本（推荐，5 处小改）：历史回看日给猫爪上游长 TTL

**安全性已验证**：`call_cached` 的键 = `meoz:<api>:<json(params, sort_keys=True)>`，
而回看路径传的是**绝对日期**：

```python
# kpl.py:2704-2708
_meoz_date = None
_meoz_off = 0
if date:
    _meoz_date = str(date).replace("-", "")   # 绝对日 → 进缓存键（按日隔离）
    _meoz_off = None
```

```python
# meoz_client.py —— 回看键形如 meoz:screening:{"tradedate": "20260924"}
params = {}
if date:
    params["tradedate"] = str(date).replace("-", "")
elif date_offset is not None:
    params["tradedate_offset"] = date_offset   # 实时路径：相对键，跨日会窜 ⇒ 禁用长 TTL
```

⇒ 历史日键**天然按日隔离且值不可变**，长 TTL 无风险。

```diff
--- a/backend/app/services/meoz_client.py
+import datetime as _dt
+
+_HIST_TTL = int(os.environ.get("MEOZ_HIST_TTL", "3600"))   # 回看日上游缓存(秒)
+
+def _ttl_for(apiname: str, params) -> float:
+    """回看日(绝对 tradedate 且**非今天**)数据不可变 → 长 TTL, 避免与 kpl 结果层(600s)错配。
+    ⚠️ 必须排除"今天": 交易日盘中若前端带了今天的日期, 数据仍在变。"""
+    td = (params or {}).get("tradedate")
+    if td and str(td) != _dt.date.today().strftime("%Y%m%d"):
+        return max(float(_HIST_TTL), cache_ttl(apiname))
+    return cache_ttl(apiname)
```

然后把 `screening_map` / `free_mv_map` / `auc_snapshot` / `auc_open_bid` / `auc_qc_net`
五处 `ttl=_AUC_SNAP_TTL` 换成 `ttl=_ttl_for("<api>", params)`。

**预期效果**：回看日「结果层即便过期」的重算 **5.5s → 0.35s**（§3 实测 345ms）；
主人感受到的是"点开就有"。

### B. 补充：回看日结果层 TTL 600s → 1800s（数据不可变，纯收益）

同上，**必须排除"今天"**。

### C. 顺带修（与本次现象独立）：修掉盘中 `cache: 300` 压轮询

```diff
// frontend/src/api/kpl.js
 export function kplBidQiangcang(date = '') {
-  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {}, cache: 300 })
+  // 回看日数据不可变 → 300s 客户端缓存安全；实时(无 date)必须让 30s 轮询真刷现涨
+  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {}, cache: date ? 300 : 0 })
 }
```

### D. 保险：`withTimeout` 12s → 15s

`AuctionView.vue:643` 的 `withTimeout(p, ms=12000)` 超时**返回空列表**（不抛错）。
全冷实测最坏 11.5s ⇒ 12s 会在临界点截断成"点了没数据"。

### E. 可选：收窄返回体

`screening_map(symbols=...)` **已支持点查**（`meoz_client.py:646-689`）。
⚠️ 例外：`list20` 的兜底 `_list20_fundflow_fallback`（`kpl.py:2592-2611`）需要**全市场**
做「自由流通市值 ≥ 2 亿」候选筛选，那条路径不能收窄。

### 明确不建议

| 不建议 | 原因 |
|---|---|
| 给该接口加 HTTP / nginx `proxy_cache` | 响应含 per-uid 配额副作用；第 1 层 `no-store` 是刻意设计 |
| 调短轮询间隔（<30s）抢现涨 | 免费用户 1 次/日，会立刻 429 |
| 降低结果层 TTL 提新鲜度 | 现涨由 api 层每请求重算，与结果层 TTL 无关；降 TTL 只会放大冷尾巴 |

---

## 8. 顺带发现（建议另立工单，非本次范围）

**无 `date`（盘中实时）路径下，部分接口没有有效结果缓存**：清空 `kpl:` 后
`bid-boom` **15850 ms** / `yest-zt` **15380 ms** / `lhb` **7434 ms**；
紧接着再打一遍（本应命中缓存）仍要 7225 / 11422 / 7576 ms。
⇒ 这三个接口的实时路径**每次请求都在重算**，比竞价抢筹更值得排查（待确认其缓存键是否含变动量）。

---

## 9. 复现脚本（本轮全部只读 / 自愈）

| 脚本（`scripts/deploy_tmp/`） | 用途 |
|---|---|
| `_kx_be/_kx_auction_tabs.js` | **真实浏览器**逐 Tab 计时（puppeteer + 本机 Chromium） |
| `_kx_be/_kx_shot_qc.js` | 逐秒采样 + 用户视角截图 |
| `_kx_be/_kx_cold_per_tab.py` | 生产：各 Tab 接口全冷/热横向对比 |
| `_kx_be/_kx_qc_ttl_gain.py` | 生产：冷热分层收益量化 |
| `_kx_be/_kx_prof_qc_hotup.py` | 生产：**上游热/结果冷**剖析（确认 345ms）+ meoz 缓存命中自证 |
| `_kx_be/_kx_purge_kpl.py` | 清 `kpl:` 前缀（30~600s 内自愈；仅制造冷态用） |
| `_kx_be/_kx_push_and_run.py` | 推脚本到生产并执行 |

> ⚠️ **踩坑（本轮新增）**：做「上游热」对照实验时，**不能用"再调一次接口"当预热** ——
> 如果结果层当时还热，那次调用是直接命中结果层、**压根不会打上游**，上游缓存是空的，
> 于是②会得到 5090ms 的**假结论**。必须先 `clear_prefix("kpl:")` + `meoz_client.clear_cache()`
> 再预热，才能得到真实的 345ms。

---

## 10. 状态

**本报告未改任何一行运行时代码。** 建议 A~E 待主人裁决后落地，
届时按「同构替身真跑验证」（先在生产机以临时脚本验证 `_ttl_for` 的边界：
`tradedate=今天` 必须回落到 30s、`tradedate=历史日` 才用长 TTL）再上线。
