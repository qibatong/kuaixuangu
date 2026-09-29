# 猫爪 429 整改 · 进度报告（2026-09-28 夜 ~ 09-29 00:0x）

**主人指令**：① 代码改动全做 ② 测试机竞价期停机、不取数 ③ 先实测再接兜底；**补充**：日K 不用东财，改腾讯/新浪。

**当前状态**：代码改动已完成 5/6 + 运维项已完成；**尚未重启两台机的服务、尚未提交推送**（等全部改完一起上）。

---

## 一、已完成（代码，均已在测试机落盘并通过测试）

### 1. ✅ P0-a 修 `Retry-After` 合规（`meoz_client.py`）

**问题**：`_post_one` 抛错时塞的是 `dict(e.headers)`（整个字典），429 分支再包一层
`{"Retry-After": <dict>}` ⇒ `float()` 抛 TypeError 被 except 静默吞掉 ⇒
**上游的 Retry-After 从未生效**，我们固定 1s/2s 连打同线路。官方 `SKILL.md:116` 明确要求
「遵循 Retry-After，单次最多等 30 秒」⇒ 我们此前**违反官方规范**，且是**主动放大限流**。

**改动**：① `_post_one` 只保留 `Retry-After` 头的值；② `_retry_after_seconds()` 兼容
"字符串/数字"与"headers 映射"两种入参；③ 上限由 8s → **30s**（官方口径）；
④ 新增**单次调用的累计等待预算** `_RETRY_AFTER_TOTAL=30s`，预算用尽即放弃，不再敲上游。

**自证（测试机实调）**：
```
'5' → 5.0 ✓    '30' → 30.0 ✓    '99' → 30.0（上限）✓
{'Retry-After':'7'} → 7.0 ✓（兼容旧调用）   None → 1.0/2.0（指数退避）✓
脏值（旧 bug 场景，headers 里没有 Retry-After）→ 1.0 且**不抛异常** ✓
```

### 2. ✅ P0-b 分片前统一排序（`meoz_client.py`）

**根因**：缓存键 = `apiname|fields:json(params)`，而 `symbols` 是逗号串 ⇒ **顺序不同 = 键不同**
⇒ 同一批 5000+ 行数据被重复拉取。今天实测：竞价时段 429 **全部**来自 web 进程的 `a=daily`（昨比），
09:24→163 条、09:25→286 条 —— 就是这个机制。

**改动**：新增 `_norm_symbols_param()`（剥空、去重、**排序**），并在三处批量入口生效：
- `fundflow_map()`（分片 2000/片）
- `screening_map()`（点查/全市场）
- `daily_history_map()`（昨比 500/片、个股日K）

**自证**：`['600519.SH','000001.SZ'] → "000001.SZ,600519.SH"`；`'b,a,c' → "a,b,c"`；
**同一批票两种顺序 → 同一个键 = True** ✓（安全性：出口一律经 `_sym_rows()` 转字典，行序无契约价值）

### 3. ✅ P1 `fields` 纳入缓存键（`meoz_client.py`）

原先只拼 `apiname+params` ⇒ 同参数但 **fields 不同**的调用会**串味**（先写的窄字段集被后调用者读到，
例如题材榜 3 列 vs `screening` 54 列）⇒ 属**正确性**问题，不只是浪费。现已写入键。

### 4. ✅ P1 归一 `screening` 的"全市场实时"键（`meoz_client.py`）

同一份全市场数据原先有 3 种等价键：`{}` / `{tradedate:今天}` / `{tradedate_offset:0}`
（竞价时段实测最多 4 份重复拉取）。现统一为**相对键 `tradedate_offset=0`**；历史绝对日仍走
`tradedate`（以享 3600s 长 TTL）。

### 5. ✅ 日K：东财**退出主链、复位为末位兜底**（`fetcher.py`）

**实测结论（两台机一致）**：
```
腾讯 qfqday 第 9 列 = 真实成交额(万元)  600519 → [348872.06, 386731.09]；涨跌幅自算 0.56%
新浪日K/周K 可取（scale=240/1200），但**只有 volume、无成交额** ⇒ 不能用于昨比
```

**最终口径**（09-28 曾整体摘除 → 09-29 按主人「如果影响逻辑计算，还可以考虑使用东财」**复位**）：

> 链路 = **猫爪 daily(主) → 腾讯 qfqday(首选备源) → 东财 push2his(末位兜底)**

| 为什么腾讯排前面 | 为什么**必须保留**东财 |
|---|---|
| qfqday 第 9 列 = 真实成交额(万元)，实测稳定、无风控 | ① **涨跌幅口径**：东财是**官方 f58**；腾讯只能用**前复权收盘价环比自算** ⇒ **除权除息日会偏**，而"昨日涨幅"是评分因子(权重 6%) ⇒ **确实会改逻辑计算**<br>② **覆盖率**：腾讯稳定缺 ~8 只(0.14%)，东财可补 |

**改动**：
- `_fetch_yesterday_amount_one()`：腾讯优先；**腾讯返回 None 时才走新增的 `_yday_fallback_eastmoney()`**
  （东财熔断 `down_threshold=1`/冷却 60s + 域名冷却 300s ⇒ 风控期不会反复重试、坏域名不拖慢整批）
- `fetch_stock_chart_robust()`：`sources = ["meoz", "tencent", "eastmoney"]`（东财**在末位**），
  东财分支保留并标注"末位兜底"；周K/月K 由腾讯承接（`day/week/month`，200/700/300 根）
- 批级短路条件恢复为「猫爪不可用 **且** 腾讯熔断 **且** 东财熔断」
- `fetch_stock_chart()` 的 K 线腿恢复为可用（末位）；**分时不算日K，全程未动**
- 测试同步：`test_yday_chain_tencent_then_eastmoney`、`test_chart_robust_falls_back_to_tencent_then_eastmoney`
  —— 都钉住「腾讯可用时**不得**打东财」+「腾讯失败才落东财」
  （我自己第一版用例写错了一处桩叠加 ⇒ 已修正：③ 段改为直接调真实实现 ✓）

### 6. ⬜ P0-c 昨比跨进程 single-flight —— **未做**（下一步）

现只有**进程内** `_yday_batch_lock` ⇒ 2 个 uvicorn worker 各自全量拉一遍。
计划：加 `store.acquire_sem("yday", limit=1)` 包裹 `_do_fetch_yesterday`，拿不到令牌直接用现有缓存
（缺失容忍，不改语义）。

---

## 二、已完成（运维）：测试机竞价期停机

**依据**：今天实测竞价窗口（09:15~09:30）测试机 429 **665 条**（全部来自 web 进程的猫爪 `a=daily`，
且其请求 Top1 IP 是主人自己 `124.64.23.98` 126 次）；而**猫爪只有一个 apikey**、两台机共用同一份额度。

**已安装（测试机 crontab，仅工作日）**：
```
5  9 * * 1-5  systemctl stop  kuaixuan.service kx-worker.service   → 日志 kx_auction_pause
40 9 * * 1-5  systemctl start kuaixuan.service kx-worker.service   → 日志 kx_auction_pause
```
**自检**：规则 2 条 ✓、`/usr/bin/systemctl` 存在 ✓、当前服务仍 active（等 9:05 生效）✓、
窗口内**没有别的周期性 cron**（那 12 条 `tp_collect` 是 **9 月 11 日的单次任务**，不会复现）✓

---

## 三、测试与安全闸

| 项 | 结果 |
|---|---|
| **上线前差异安全闸** | `meoz_client.py` / `fetcher.py` 与仓库 HEAD **逐行差异 = 0** ⇒ 覆盖安全（无机器独有补丁）✓ |
| 相关测试 | `test_fetcher_meoz_swap` / `test_circuit_breaker` / `test_tencent_fallback` ⇒ **62 passed** ✓ |
| 全部 meoz 相关 | **111 passed / 0 failed** ✓（含 `test_meoz_client_screening` / `wp345` / `picker` / `snapshot` 等）|
| 用例修正 | `test_daily_history_map_params_recentdays_vs_tradedate` 因"symbols 现入口排序"而红 ⇒ **按新不变量修正**（同时钉"剥后缀 + 排序"），不是照抄输出 ✓ |
| 备份 | 测试机 `backend_bak_meozfix_20260928-235856` / `…-000015` ✓ |

---

## 四、待办（下一步）

1. **P0-c** 昨比跨进程 single-flight（`fetcher.py`，~20 行）
2. **P1** 收窄 `fresh=True`（仅"就绪判定"保留，补采改短 TTL）
3. **P2** 缺口兜底：首页指数带 + 情绪卡（东财/新浪/腾讯指数已具备；`dev_risk` 已有"腾讯→新浪"范式可抄）
4. **全量 pytest**（测试机，预期 ≥1656 passed / 0 failed）
5. **重启两台机**（测试机 + 生产机）+ 生产差异安全闸 + 冒烟
6. **实测复核**：竞价时段（明早 9:05~9:40）抽样看 429 条数是否显著下降（预期：测试机 0 条；生产机降幅来自 排序/键归一/Retry-After）
7. **提交推送**（当前分支 `feature/scoring-v7-meoz`，含前端两处 + 后端这批）
