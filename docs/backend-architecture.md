# 快选 Kuaixuan 后端架构重构方案

> 版本: v1.0 · 2026-08-16  
> 作者: 后端架构师  
> 状态: 方案评审稿（待确认后进入 Phase 1 实施）

---

## 1. 现状盘点

### 1.1 架构快照

| 层      | 现状                                                            | 规模                 |
| ------ | ------------------------------------------------------------- | ------------------ |
| Web 框架 | FastAPI 单体（`uvicorn --workers 1`）                             | 7524 行 / 27 个模块    |
| 数据层    | SQLite 单文件（WAL + busy_timeout 5s）                             | 20 张表，建表自动迁移       |
| 数据源    | 开盘啦 KPL（付费，\_SEM=3 信号量限流）→ 东财 → 同花顺兜底                         | 118 个封装接口          |
| 调度     | auction_snapshot（9:15/9:20/9:24/9:25 全市场快照）+ wpqc_push（14:57） | 内嵌 FastAPI startup |
| 推送     | 飞书 webhook（签名）+ 企业微信 webhook                                  | 同步 urllib          |
| 限流     | 每 IP 每分钟 N 次（进程内 deque）                                       | RATE_LIMIT_PER_MIN |
| 缓存     | kpl 内存 `_cache` + `_cache_lock`                               | TTL 键值             |
| 测试     | pytest 191 用例 / 18 文件                                         | 提交前全量绿             |

### 1.2 六大架构瓶颈（按严重度）

| # | 瓶颈                 | 现状                                                     | 后果                                    |
| - | ------------------ | ------------------------------------------------------ | ------------------------------------- |
| ① | **进程内状态**          | `_cache` / `rate_allow` / `_sched_done` / `_SEM` 全部进程内 | 多 worker 后缓存 ×N、限流失效、KPL 付费配额 ×N 触发限流 |
| ② | **调度器耦合 Web 进程**   | 调度在 FastAPI startup 启动                                 | web 重启 = 采集中断；9:25 高峰采集与 API 抢资源      |
| ③ | **workers=1 无法扩展** | 单进程                                                    | 用户裂变后（24+ → 100+）9:25 高峰必然顶不住         |
| ④ | **数据源串行容灾**        | 东财失败 → 重试 → 切同花顺                                       | 某源故障时响应最坏 3×timeout；无熔断，每次打满超时        |
| ⑤ | **SQLite 写并发上限**   | 单写者 + 落库同步                                             | 多用户选股落库 + 全市场快照写入竞争                   |
| ⑥ | **无监控指标**          | 只有日志                                                   | 无法量化 QPS/延迟/KPL 配额消耗，排障靠翻日志           |

---

## 2. 目标架构

### 2.1 设计原则

1. **务实**：1-2 台服务器、CentOS 7、生产 Docker 已就绪。不做 K8s/微服务（过度设计）。
2. **进程拆分**：Web（请求）与 Worker（采集/推送/落库）解耦。
3. **状态外置**：缓存/限流/调度标记/配额计数全部移出进程内 → Redis（生产）/ SQLite 表（测试兜底，零依赖）。
4. **异步化**：落库投递队列，请求快速返回。
5. **容灾增强**：数据源熔断 + 免费源并行竞速 + KPL 配额守护。

### 2.2 拓扑

```
                       ┌──────────────────────┐
                       │  Nginx :80（已部署）   │
                       └──────────┬───────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
     ┌────────▼───────┐   ┌──────▼──────┐   ┌────────▼────────┐
     │ kx-web         │   │ kx-worker   │   │ Redis           │
     │ uvicorn ×2-4   │   │ 独立进程      │   │ 生产容器/测试兜底 │
     │ 纯 API 无调度   │   │ 快照采集/推送  │   │ kpl缓存/限流/锁  │
     │ 读缓存/投递落库  │   │ 落库队列消费   │   │ KPL配额计数      │
     └────────┬───────┘   └──────┬──────┘   └────────┬────────┘
              └─────────┬────────┴────────────────────┘
                        │
              ┌─────────▼──────────┐
              │ SQLite WAL（现役）  │──用户>500 迁移──▶ PostgreSQL
              │ + task_queue 队列   │
              └────────────────────┘
```

### 2.3 组件设计

#### A. CacheStore 抽象层（核心解耦件）

新增 `app/services/cache_store.py`，统一缓存/限流/计数/锁接口：

```python
class CacheStore:
    def get(self, key) -> object | None
    def set(self, key, value, ttl: int) -> None      # ttl 秒
    def incr(self, key, ttl: int) -> int             # 原子自增(限流/配额)
    def delete(self, key) -> None
    def acquire_lock(self, key, timeout) -> bool      # 分布式信号量/锁
    def release_lock(self, key) -> None
```

- 实现 1：`RedisCacheStore`（生产，多 worker 共享）
- 实现 2：`SqliteCacheStore`（测试机零依赖，表 `kv_cache`）
- 由 `config.CACHE_BACKEND = redis|sqlite` 切换，存量键名不变（加 `kpl:` 前缀）

**迁移点（全部是机械替换，风险低）：**

| 现状                    | 迁移后                   | 键示例                          |
| --------------------- | --------------------- | ---------------------------- |
| kpl `_cache` 内存字典     | CacheStore.get/set    | `kpl:stock_plate:002931`     |
| `rate_allow` 内存 deque | CacheStore.incr + ttl | `rate:{ip}`（60s 窗口）          |
| `_sched_done` 集合      | CacheStore 带锁 set     | `sched:done:2026-08-16:9_25` |
| `_SEM=3` 信号量          | Redis 信号量（全局并发仍 3）    | `sem:kpl`                    |
| （新增）KPL 配额计数          | CacheStore.incr + 告警  | `kpl:quota:2026-08-16`       |

#### B. 进程拆分

- **kx-web.service**：`uvicorn app.main:app --workers 2`（1C 机器用 2 + gthread 线程）。移除 startup 中的调度启动，web 只处理请求。
- **kx-worker.service**：`python -m app.worker`（新增 `app/worker.py` 入口），内含：
  - `auction_snapshot._scheduler_loop()` 调度（原逻辑整体搬入）
  - `wpqc_push` 尾盘推送
  - `task_queue` 落库队列消费者（轮询 SQLite `task_queue` 表 / Redis List）
- 独立 systemd 单元：worker 崩溃/升级不影响 web；web 重启不影响采集。

#### C. 异步落库

新增表 `task_queue`：

```sql
CREATE TABLE IF NOT EXISTS task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,              -- 'save_batch' | 'snapshot' | 'notify'
    payload TEXT NOT NULL,           -- JSON
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed
    created_at INTEGER NOT NULL,
    done_at INTEGER
);
```

- 选股落库 `save_batch` → 插入队列 → 立即返回 → worker 批量消费写入 `batches/batch_stocks`
- 快照落库本来就在 worker 内，直接写
- 兜底：worker 挂掉时队列积压，恢复后继续消费（幂等：按 batch_id 去重）

#### D. 数据源熔断 + 竞速

新增 `app/services/circuit.py`（熔断器）：

```
每个数据源独立状态机：
  closed（正常）→ 连续失败 N 次 → open（短路 60s，直接走备源）
  open（冷却）→ 60s 后 → half-open（放 1 个试探请求）
  half-open → 成功回 closed / 失败回 open
```

- 东财/同花顺（免费源）：**并行竞速**（ThreadPoolExecutor 同时发，先回者胜），替代串行重试
- 开盘啦（付费源）：保持 `_SEM` 限流但改 Redis 分布式信号量；**配额守护**：当日调用接近 80000 时降级（直接走东财/同花顺，保底不爆配额）

#### E. 可观测性

新增 `GET /api/metrics`（Prometheus 文本格式）：

```
http_requests_total{path,status}
http_request_duration_ms{path}        # 桶+分位
kpl_quota_used / kpl_quota_remaining
datasource_health{src}                # 0/1 + 熔断状态
task_queue_depth
snapshot_scheduler_last_run{point}
```

- Nginx 反代 `/api/metrics`，可被 Prometheus / 云监控采集
- 结合现有 app.log + 健康监控，形成"日志 + 指标"双通道

---

## 3. 分阶段实施计划（ROI 排序）

| 阶段              | 内容                                                                | 工作量   | 收益                                     | 验证                            |
| --------------- | ----------------------------------------------------------------- | ----- | -------------------------------------- | ----------------------------- |
| **Phase 1 稳定性** | ① CacheStore 抽象 + 缓存/限流/调度标记外置 ② worker 进程拆分（调度剥离）③ 落库队列异步化       | 2-3 天 | 消除单进程瓶颈；重启不丢采集；限流跨进程一致；9:25 高峰采集不拖 API | pytest 191 全绿 + 测试机 9:25 实际观察 |
| **Phase 2 扩展性** | ④ web 多 worker（workers 2-4）⑤ KPL 配额守护 + 熔断器 + 并行竞速 ⑥ /api/metrics | 2-3 天 | 支持 5-10× 用户量；故障自愈；可量化观测                | 压测（ab/wrk）+ 指标采集验证            |
| **Phase 3 演进**  | ⑦ PostgreSQL 迁移（用户 >500）⑧ API 版本化 /v1 ⑨ 更细告警                      | 按需    | 500+ 用户规模                              | 迁移演练 + 双写对比                   |

> 每个 Phase 独立可上线、可回滚；Phase 1 完成即解决当前 90% 稳定性痛点。

---

## 4. 关键设计决策

| 决策       | 选择                                       | 理由                                                      |
| -------- | ---------------------------------------- | ------------------------------------------------------- |
| 缓存存储     | Redis（生产）/ SQLite 表（测试）                  | 测试机无 Docker，SQLite 零依赖过渡；生产已有 Docker 栈，Redis 容器 30 分钟就绪 |
| 队列       | SQLite task_queue 表（不引入消息中间件）            | 体量小（分钟级几十条），零新依赖；任务复杂化后再上 RQ/Celery                     |
| 多 worker | uvicorn --workers 2-4（非 gunicorn/uvloop） | 现有部署体系最小改动                                              |
| 数据库      | SQLite 保留 → 用户 >500 迁 PostgreSQL         | 当前读多写少，SQLite WAL 够用；避免过早引入 PG 运维成本                     |
| API 版本化  | 暂不强制 /v1                                 | 内网工具 + 前端配套改动成本高；新接口走 /v1 前缀渐进式                         |

## 5. 风险与兼容性

| 风险                  | 缓解                                             |
| ------------------- | ---------------------------------------------- |
| 缓存键格式变更             | 键名不变只换存储，双实现共享接口；上线后观察命中率                      |
| worker 剥离导致采集空窗     | worker 与 web 同机部署，独立 systemd；启动顺序 web → worker |
| 多 worker 后 KPL 并发超限 | Redis 信号量保持全局并发 3；配额守护兜底降级                     |
| 落库异步化后用户看不到最新批次     | 队列消费延迟 <1s；接口读库前 flush 本批                      |
| 生产环境变更              | 默认仅测试机；生产需主人明确指令，前端部署后 chmod a+rX（umask=027）   |

## 6. 免责任声明

本方案基于静态分析和经验规则生成，仅供参考，实际重构决策请结合团队情况综合判断。

---

## 附录：Phase 1 任务清单（待确认后拆解）

- [ ] `app/services/cache_store.py`：CacheStore 抽象 + Redis/SQLite 双实现
- [ ] kpl.py `_cache` → CacheStore（键前缀 `kpl:`）
- [ ] security.py `rate_allow` → CacheStore.incr（跨进程限流）
- [ ] auction_snapshot `_sched_done` → CacheStore 带锁
- [ ] `_SEM` → Redis 信号量 / SQLite 锁
- [ ] `app/worker.py`：调度 + 推送 + 队列消费入口
- [ ] `app/db/database.py`：`task_queue` 建表 + save_batch 异步化
- [ ] main.py startup 移除调度启动
- [ ] systemd：`kx-worker.service` 单元（测试机）
- [ ] 测试补充（CacheStore 双实现单测、队列消费幂等测试）
- [ ] 测试机全量验证（pytest 191 + 9:25 观察 + 浏览器回归）

---

## 架构演进现状（2026-08-30 更新）

> 本文档评估时的"目标架构"已大部分落地，以下为当前实际状态对照：

### 已落地（相对 1.1 快照的变化）

| 项 | 当时评估 | 当前实际 |
|---|---|---|
| Web 规模 | 7524 行 / 27 模块 | ~1.7 万行 / 40+ 模块（fetcher 单文件 1727 行） |
| 数据源 | 东财 → 同花顺兜底 | **东财 → 腾讯兜底（全市场行情）** + 同花顺（昨日额/日K） + tushare + kpl + 选股宝（涨停池）；**熔断器 60s 冷却**（`_CIRCUIT_OPEN_SECONDS`）+ `/api/health` 健康快照 |
| 进程内状态 | `_cache` 全进程内 | 已实现 **CacheStore**（SQLite `kv_cache` 表 / Redis 双实现）：限流/调度去重/setnx 跨进程锁 |
| 调度耦合 | 内嵌 FastAPI startup | **已拆分 `kx-worker.service`**（快照调度/aipick/推送/队列独立进程） |
| 多数据源 | 串行容灾无熔断 | 腾讯兜底 + 双源熔断短路 + 5 源图表兜底（fetch_stock_chart_robust） |
| 测试 | 191 用例 | **391 用例**（+3 东财网络偶发单跑通过）+ 4 跳过 |

### 新增能力（评估时未规划）

- **短信验证码**：阿里云号码认证（个人免资质），找回密码双通道；AK 走 systemd drop-in `sms.conf`
- **登录 cookie**：`kx_token`（HttpOnly/SameSite=Lax）→ 静态报告地址栏直接鉴权
- **AI 预测 VIP 门禁**：Nginx auth_request → 后端 auth-check（`/aipick/*`）
- **aipick 调度**：9:27 预测 + 15:07 backfill 补缺失 + 19:00 训练（`aipick_scheduler.py`）
- **system_batch**：9:25 自动选股批次（`user_id=0+auto_applied=1`）→ 历史回看保障 + 9:26 监控告警
- **板块/热榜 3 源并行**（kpl/em/ths）+ em 源故障前端警示（`source_failed`）

### 待落地（附录清单剩余）

- [ ] 主 web 进程 9:25 高峰水平扩展（当前仍 `--workers 1`，靠 worker 进程分担调度）
- [ ] SQLite 写并发压测与落库异步化（task_queue 框架已就绪）
- [ ] 监控指标接入（QPS/延迟/KPL 配额，当前靠日志 + `/api/health`）
