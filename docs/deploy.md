# 构建与部署手册

## 环境

服务器目录: `/opt/kuaixuan` · systemd 服务: **`kuaixuan.service`（web）+ `kx-worker.service`（调度/推送）** · 数据库: `kuaixuan.db`

| 环境 | 地址 | 说明 |
|---|---|---|
| 测试机 | 47.99.153.123 | 默认部署目标（验证通过自动部署） |
| 生产机 | 121.196.230.80 | **必须主人明确指令才更新** |

> 测试机 venv `/opt/bid-venv`，生产机 venv **`/opt/kuaixuan-venv`**（部署脚本注意区分）。

## 构建与部署

```bash
# 前端构建 -> dist/
cd frontend && npm run build

# 部署 (Nginx + systemd, 双服务)
# - dist/      → /opt/kuaixuan/dist   (Nginx 静态托管, SPA try_files; 部署后需 chmod -R a+rX)
# - backend/   → /opt/kuaixuan/backend
#   ① kuaixuan.service  (web):   ExecStart=/opt/bid-venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
#   ② kx-worker.service (调度):  ExecStart=/opt/bid-venv/bin/python -m app.worker
#      (快照采集 9:15/9:20/9:25 + 尾盘推送 14:57 + 任务队列; web 重启不影响采集)
#   Environment=SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt  (自编译 OpenSSL 需指 CA)
# - systemd drop-in: /etc/systemd/system/kuaixuan.service.d/
#   kpl.conf (开盘啦 Token/UserID/DeviceID) / notify.conf (推送 webhook)
#   / sms.conf (阿里云 AK: ALIYUN_AK_ID / ALIYUN_AK_SECRET, 短信验证码) — 均不进 git
#   kx-worker 共享环境: /etc/kuaixuan/env.conf (与 drop-in 同源)
# - 数据库: /opt/kuaixuan/kuaixuan.db
# - 日志: /opt/kuaixuan/logs/app.log (web 与 worker 共用)
```

**跨进程状态存储（CacheStore）**：缓存/限流/调度去重默认存 SQLite `kv_cache` 表（零依赖）；生产多 worker 时可切 Redis：`CACHE_BACKEND=redis` + `REDIS_URL`（环境变量）。

**同步远程代码**（测试机无 git）：`python scripts/sync_test_server.py`（md5 对比差异上传）
注意：后端 sftp 同步后，**前端必须本地 `npm run build` 再上传 dist**（测试机无 node/npm 无法远端构建）。

Nginx 关键配置（/etc/nginx/conf.d/kuaixuan.conf）：
- `/` → 静态托管 dist + `try_files $uri /index.html`（SPA）
- `/assets/` → **no-store**（Vue 构建产物禁止长缓存，避免用户看到旧版）
- `/api/` → 反代 127.0.0.1:8010（传 X-Real-IP / X-Forwarded-For）
- `/download/` → 通达信工具静态下载
- `/aipick/` → **AI 预测报告静态托管 + VIP 门禁**：`auth_request /__aipick_auth`（子请求 → 后端 `/api/aipick/auth-check`，透传 X-Original-Authorization / X-Original-URI / Cookie；200 放行静态，401 匿名拒 / 403 免费拒）。子请求 location 固定 URI（**不要用 `$is_args$args` 变量**，CentOS7 nginx 对 auth_request URI 变量支持有问题）

## 前端 dist 同步（轻量差异，防踩坑）

```bash
python scripts/_sync_dist_fresh.py
```
- Vite chunk 文件名含内容 hash → **只对比文件名集合**差异上传，秒级完成
- `index.html` 无 hash 永远同名 → 脚本强制覆盖
- 远端"孤儿 chunk"（有 hash 但本地不存在）自动清理，防 dist 无限膨胀
- ⚠️ 教训：**不要**下载远端内容算 MD5 对比（触发本环境 safe-delete 钩子卡死），也**不要**全量覆盖上传 1000+ 文件（sftp 极慢）
- ⚠️ 含服务器密码的部署/同步脚本一律入 .gitignore（`scripts/_*.py`），提交前 `grep` 密码扫描

## 数据源容灾（东财被墙时）

- **全市场行情主链**：东财 clist 被墙/熔断 → 自动切**腾讯行情**（qt.gtimg.cn，快照库全市场代码清单分批发拉，字段映射 f2/f3/f8/f21 等；无竞价专属字段用现价涨幅/成交额近似）
- 入口已全覆盖：`ensure_cache` / `ensure_spot_cache` / `fetch_market_brief` / `fetch_spot_quote_map` / `auction_snapshot._grab`（grep `fetch_eastmoney\(` 确认无漏网）
- **昨日成交额**：**收盘落库 + 全天读库**（15:10 `yday_prewarm` 写 `yday_amount` 表，此后零网络）；库中缺失的（新股/停牌/任务未跑）才走实时链 **东财日K → 腾讯 `qfqday`** + 双源熔断短路（`_check_circuit` 两源都开时立即跳过，防 5554 只并发卡 504）。同花顺昨比源已于 v4.11.6 删除
- **个股图表**：`fetch_stock_chart_robust` **2 源**（东财 push2his → 腾讯同语义备源；周K/月K 主源全失败时走日线聚合兜底）。tushare / 同花顺 / kpl 三分支已于 v4.11.8 删除（此前 `sources` 收窄后它们运行时不可达）
- **涨停池**：走选股宝 flash-api（非东财，天然免疫）
- **健康监控**：`/api/health` 返回 `{overall, serviceable, sources}`；板块/热榜 em 源失败时前端显示「数据源故障，请切换源」警示
- 熔断器：故障后 60s 冷却直接快速失败（`_CIRCUIT_OPEN_SECONDS`），防单 worker 卡死雪崩

## 推送提醒（微信 / 飞书）

竞价锁定选股（action=lock）成功后自动推送当日 Top N（后台线程，渠道失败不影响主流程，120 秒去重）。
尾盘竞价抢筹：工作日 14:57 自动拉取抢筹榜推送。

| 环境变量 | 说明 |
|---|---|
| NOTIFY_FEISHU_WEBHOOK | 飞书群机器人 webhook（可选，支持签名校验） |
| NOTIFY_SERVERCHAN_KEY | Server酱 SendKey → 个人微信（可选） |
| NOTIFY_WECHAT_WEBHOOK | 企业微信群机器人 webhook（可选） |
| NOTIFY_TOP_N / NOTIFY_TIMEOUT / NOTIFY_DEDUP_SECONDS | Top N（默认8）/ 超时（5s）/ 去重窗口（120s） |

systemd 用 `Environment=` 注入；未配置的渠道自动跳过。

## 日志与排查

- **后端日志**：`/opt/kuaixuan/logs/app.log`（10MB 轮转保留 5 份）
  - 抢筹链路关键词：`抢筹[live]`（Type4 返回/过滤后落库）、`抢筹[saved]`（非竞价读库）、`抢筹[listLast]`（9_24/9_25 条数+秒级序列）、`抢筹[result]`（每次汇总）
  - 快照采集：`快照已存`、`最后一秒采样已存`、`今日快照采集缺失时点`（告警）
  - 排查示例：`grep 抢筹 /opt/kuaixuan/logs/app.log`、`grep ERROR`、`grep 限流`
- **前端日志**：JS 错误与 API 失败写入 localStorage（key `kuaixuan_front_log`，环形 50 条）
