# 快选 Kuaixuan · 竞价 AI 选股系统

**技术栈**：后端 FastAPI (Python 3.11) + 前端 Vue 3 / Vite / Pinia / Vue Router + Nginx + systemd

选股算法（评分权重、筛选逻辑、数据抓取）全部在服务端，浏览器只有界面代码；
用户体系（注册/登录/邀请裂变）、历史记录、筛选偏好按用户隔离。

**覆盖市场**：沪 / 深 / 创 / 科 四板全市场（5549 只）

---

## 核心亮点

| 能力 | 说明 |
|---|---|
| **双模式选股** | 竞价选股（9:15-9:31 锁定）+ 盘中实时（同一套评分/筛选，名单不锁定） |
| **竞价异动页** | 8 个 Tab：竞价委买 / 竞价爆量 / 竞价抢筹 / 竞价净额 / 昨日涨停 / 昨断板 / 昨上榜 / 昨炸板 + 今炸板，全部表头可排序 |
| **竞价抢筹双表** | 左表 = 开盘啦 Type4 竞价净额强度（200 只涨停委买榜）；右表 = **最后一秒秒级差值回退**（9:24:55-9:25:03 每秒采样） |
| **全市场快照库** | 9:15 / 9:20 / 9:24 / 9:25 四时点全市场（5549 只）自动归档，形成历史回放库 |
| **结果持久化** | 9:29 竞价结果自动落库，非竞价时段读库展示，全天可回看 |
| **多数据源容灾** | 开盘啦（付费 8 万次/天）+ 东方财富 + 同花顺兜底 + 通达信导入 |
| **推送提醒** | 竞价锁定结果推飞书 / Server酱 / 企业微信；尾盘抢筹 14:57 自动推送 |
| **管理后台** | 用户列表 / 评分权重 / 全局默认筛选参数，仅管理员可访问 |
| **邀请裂变** | 用户级邀请码注册（生产 `LCFV77U8` / 测试 `T2FUR4ZC`） |

---

## 双模式选股

**v3.1 起盘中与竞价共用同一套评分与筛选逻辑**（盘中 = 不锁定的竞价），区别只在"是否锁定名单"：

| 维度 | 竞价选股 (auction) | 盘中实时选股 (spot) |
|---|---|---|
| 时段 | 9:15-9:31 竞价锁定 | 9:30-15:00 实时 |
| 数据 | 竞价字段(f615/f616) + 昨日成交额 | 实时行情（同一套竞价字段） |
| 评分 | 竞价五因子(涨幅34/换手32/异动17/市值11/昨涨6) | **同左（同一套评分）** |
| 筛选 | 竞价条件（剔除ST/停牌、剔除昨日涨停、竞价涨幅≤7%、市值/股价/竞价金额） | **同左（同一套筛选）** |
| 名单 | **9:30 前锁定后恒定** | **每次刷新全市场重筛**（名单会变） |
| 落库/推送 | lock 落库 + 推送 | 不落库不推送 |

**竞价锁定名单语义**：9:30 前 lock 的名单恒定不变（后端 lock 批次为权威 + 本地快照兜底）。9:30 后刷新时名单不增删，只按 code 合并全市场实时行情（spotMap）更新实时涨幅/评分；被筛选条件剔除的票直接移除，条件放行但行情不在榜的标"已跌出"。

---

## 页面总览

| 页面 | 路由 | 功能 |
|---|---|---|
| 选股主页 | `/` | 双模式 Tab（竞价/盘中）+ 筛选面板 + 评分面板 + 策略股票池 + 市场情绪面板 |
| **竞价异动** | `/auction` | 8 Tab 竞价分析 + 顶部多时点对比卡 + 时点个股弹窗 + 表头排序 |
| 连板天梯 | `/ladder` | 首板~五板+ 实时梯队 + 涨停原因题材归因 |
| 市场雷达 | `/market` | 板块强度 + 人气热榜 + 龙虎榜（含营业部明细弹窗） |
| 历史回看 | `/history` | 按批次分组视图 / 条件分页查询，全表排序 |
| 邀请裂变 | `/invite` | 邀请码生成与名单 |
| 管理后台 | `/admin` | 用户列表/重置密码/评分权重/全局默认参数（仅管理员） |

### 竞价异动页（/auction）8 Tab

| Tab | 数据源 | 说明 |
|---|---|---|
| 竞价委买 | 开盘啦 Type4 (st=200) | 涨停委买额榜（含竞价净额/连板/概念） |
| 竞价爆量 | 开盘啦 Type10 | 竞价成交额榜（Type4 补委买额） |
| **竞价抢筹** | 开盘啦 Type4 + 秒级快照 | **双表**：左=竞价净额/流通市值强度(>5%过滤)，右=最后一秒涨幅差 |
| 竞价净额 | 开盘啦 Type4 | 按竞价净额排序 |
| 昨日涨停 | 东财 flash + Type4 + 9_25 快照 | 昨日涨停股今日竞价表现（连板标记） |
| 昨断板 | 昨日涨停池 − 今日池 | 断板股今日竞价表现 |
| 昨上榜 | 开盘啦龙虎榜 | 昨日龙虎榜 |
| 昨炸板 / 今炸板 | 东财 flash limit_up_broken | 炸板列表（涨停时间/炸板时间/原因），今炸板连板数 merge 昨日涨停池 |

**时点对比卡 + 个股弹窗**：顶部展示最近 4 交易日 × 9:15/9:20/9:25 竞价均值与一字数；点格子弹窗看该时点竞价个股（涨幅/竞价额/+池）。

### 竞价抢筹双表（核心）

- **左表（9:20-9:25 段）**：开盘啦 Type4 全市场竞价异动 200 只，`抢筹强度 = 竞价净额 / 流通市值 × 100`，过滤 `>5%` 且 `流通市值 ≥ 2亿`；竞价时段实时拉取 + 持久化 `qc_snapshot` 表
- **右表（最后一秒）**：`snapshot_lastsec` 秒级高频采样（9:24:55-9:25:03 每秒一次，ts 记实际时刻），**差值回退算法**：
  1. `9_25 涨幅 − 最新秒涨幅`，|差| ≥ 0.5% → 直接用（最后一秒抢筹）
  2. 差太小（接口延迟导致最新秒已含变化）→ 向前回退：最新秒 − 倒数第二秒，依此类推取第一个大差值
  3. 全部差值小 → 取最大差（弱信号）；无秒级序列 → 回退 9_24 时点兜底
- **非竞价时段**：读库展示今天已选结果（不丢失）

---

## 快照采集体系（历史回放库）

后台双线程调度（工作日）：

```
9:15:00 ── 9_15 全市场快照(5549只) ──────────────┐
9:20:00 ── 9_20 全市场快照 ─────────────────────┤ snapshot_bid 表
9:24:30 ── 9_24 重采覆盖(每10秒, 最后≈9:24:5x) ──┤ (date+time_point+code)
9:24:55 ─ 9_25:03 每秒高频采样 ─────────────────┼→ snapshot_lastsec 表
9:25:00 ── 9_25 全市场快照 ─────────────────────┤
9:29:00 ── 抢筹结果自动落库(qc_snapshot) ────────┘ → 全天可回看
```

- 采集器 3 分区（沪深创科）**ThreadPoolExecutor 并发**拉取：单页 600 只 0.14s / 全市场 5549 只 2.2s
- `_fetch_lock` 串行化双线程，防东财并发限流
- 快照含：竞价涨幅、竞价额、名称、委买额、**流通市值(f21)**、**概念(f103/f100)**
- 9:31 盘点缺失时点告警（数据过了点无法补）

---

## 数据源

| 数据源 | 用途 | 说明 |
|---|---|---|
| **开盘啦** (longhuvip.com) | 竞价/连板/情绪/板块/热榜/龙虎榜/抢筹 | 87 个原始接口 + 16 个选股宝免费接口，全部封装在 `services/kpl.py`；Token/UserID/DeviceID 走 systemd drop-in 注入，**不进 git** |
| **东方财富** (clist) | 全市场行情/选股/快照 | clist 分页（f12 排序 30 页×200 只全市场），K 线/昨日成交额有限流 → 同花顺兜底 |
| **选股宝** (xuangubao flash) | 涨停/跌停/炸板池 + 市场曲线 | 免费公开接口，`limit_up_pool` / `limit_up_broken` 支持历史日期 |
| **通达信导入** | 用户自选股导入 | 独立小工具（PyInstaller 打包），`/download/` 提供下载 |

---

## 目录结构

```
kuaixuan/                        # 仓库根（GitHub: felix-rich/kuaixuan）
├── backend/                      # 后端 (FastAPI 分层)
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py               # 入口: 中间件(限流) + 路由注册 + 调度启动
│   │   ├── core/                 # config(含数据源Token占位) / logger
│   │   ├── db/database.py        # 建表 + WAL + 自动迁移(含 qc_snapshot/snapshot_lastsec)
│   │   ├── services/
│   │   │   ├── security.py       # 密码哈希 / Token / 限流 / 防刷
│   │   │   ├── fetcher.py        # 东财+同花顺(兜底) 抓取/缓存/熔断/全市场分页
│   │   │   ├── scorer.py         # 选股评分与筛选算法(核心: 竞价五因子, 盘中共用)
│   │   │   ├── kpl.py            # 开盘啦/选股宝数据层(87+16接口, 缓存+降级+抢筹双表)
│   │   │   ├── auction_snapshot.py # 快照调度: 时点全市场 + 秒级高频采样(差值回退)
│   │   │   ├── stats.py          # 统计/时点快照查询
│   │   │   ├── notify.py         # 推送(飞书/Server酱/企微) + 去重
│   │   │   ├── wpqc_push.py      # 尾盘抢筹 14:57 定时推送
│   │   │   ├── users.py / history.py / settings.py
│   │   └── api/
│   │       ├── deps.py           # 鉴权依赖 / 响应辅助 / 真实IP
│   │       ├── auth.py / stocks.py / history.py / invite.py / prefs.py
│   │       ├── admin.py          # 管理后台(仅管理员)
│   │       ├── kpl.py            # 开盘啦全部路由(23 个)
│   │       ├── stats.py          # 统计路由(auction-overview/auction-snapshot 等)
│   │       └── health.py         # 健康检查
│   └── tests/                    # pytest (169 用例)
│       ├── conftest.py           # 临时库 + 数据源 Mock + TestClient
│       ├── test_kpl.py           # 抢筹双表/差值回退/持久化/昨涨停/断板/炸板
│       ├── test_snapshot.py      # 快照存取/多时点/回放
│       └── ...                   # auth/admin/history/invite/notify/phase1 等
├── frontend/                     # 前端 (Vue 3 + Vite)
│   └── src/
│       ├── router/index.js       # / /login /auction /ladder /market /history /invite /admin
│       ├── stores/               # Pinia: user / stocks / pool
│       ├── views/                # StockView / AuctionView(8Tab) / LadderView / MarketView / HistoryView / LoginView / InviteView / AdminView
│       ├── components/           # FilterPanel / MedalPanel / StockPoolPanel / StockTable / SentimentPanel / ChangePwdModal
│       ├── composables/          # useSortable(通用表头排序)
│       └── styles/main.css       # 全局样式(含 .sortable / .pool-add-btn)
├── scripts/                      # 运维/回归脚本
│   ├── browser_reg.py            # 测试机真实浏览器回归(八Tab+弹窗)
│   ├── qc_layout_reg.py          # 抢筹双表布局专项回归
│   ├── sort_reg.py               # 表格排序交互回归
│   ├── sync_test_server.py       # 本地→测试机全量同步(含密码, gitignore)
│   ├── deploy_frontend.py        # 前端部署(含密码, gitignore)
│   └── backfill_*.py             # 快照回填脚本
└── README.md
```

---

## 本地开发

```bash
# 后端 (Python 3.11)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload

# 前端 (Node 18+)
cd frontend
npm install
npm run dev        # 默认 5173, /api 代理到 8010
```

## 构建与部署

服务器目录: `/opt/kuaixuan` · systemd 服务: `kuaixuan.service` · 数据库: `kuaixuan.db`

| 环境 | 地址 | 说明 |
|---|---|---|
| 测试机 | 47.99.153.123 | 默认部署目标（验证通过自动部署） |
| 生产机 | 121.196.230.80 | **必须主人明确指令才更新** |

```bash
# 前端构建 -> dist/
cd frontend && npm run build

# 部署 (Nginx + systemd)
# - dist/      → /opt/kuaixuan/dist   (Nginx 静态托管, SPA try_files; 部署后需 chmod -R a+rX)
# - backend/   → /opt/kuaixuan/backend
#   ExecStart=/opt/bid-venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
#   Environment=SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt  (自编译 OpenSSL 需指 CA)
# - systemd drop-in: /etc/systemd/system/kuaixuan.service.d/
#   kpl.conf (开盘啦 Token/UserID/DeviceID) / notify.conf (推送 webhook) — 不进 git
# - 数据库: /opt/kuaixuan/kuaixuan.db
# - 日志: /opt/kuaixuan/logs/app.log
```

**同步远程代码**（测试机无 git）：`python scripts/sync_test_server.py`（md5 对比差异上传）
注意：后端 sftp 同步后，**前端必须本地 `npm run build` 再上传 dist**（测试机无 node/npm 无法远端构建）。

Nginx 关键配置（/etc/nginx/conf.d/kuaixuan.conf）：
- `/` → 静态托管 dist + `try_files $uri /index.html`（SPA）
- `/assets/` → **no-store**（Vue 构建产物禁止长缓存，避免用户看到旧版）
- `/api/` → 反代 127.0.0.1:8010（传 X-Real-IP / X-Forwarded-For）
- `/download/` → 通达信工具静态下载

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

## 接口概览

**选股/用户**：`/api/stocks`（lock/filter/refresh/ping + mode=auction/spot）、`/api/login`、`/api/register`、`/api/change-password`、`/api/forgot`、`/api/reset`、`/api/history`、`/api/invite`、`/api/prefs`、`/api/admin/*`

**开盘啦/选股宝（/api/kpl/）**：

| 接口 | 说明 |
|---|---|
| sentiment | 市场情绪（涨停家数/情绪值/连板高度/回撤） |
| bid-seal | 竞价涨停委买额榜（Type4） |
| bid-boom | 竞价爆量榜（Type10） |
| bid-qiangcang | **竞价抢筹双表**（左=净额强度 / 右=秒级差值回退） |
| yest-zt / yest-broken | 昨日涨停今表现 / 昨断板 |
| broken | 炸板列表（今日/历史日期） |
| ladder / zt-reason | 连板梯队 / 涨停原因 |
| board-rank / hot-rank / hot-stocks / hot-plates | 板块强度 / 人气热榜 / 热点强势股 / 板块题材 |
| lhb / lhb-detail | 龙虎榜 / 营业部明细 |
| wpqc | 尾盘抢筹 |
| yesterday-perf | 昨日涨停/连板/破板今日表现 |
| zt-pool / dt-pool / yest-zt-pool | 涨停/跌停/昨日涨停池 |
| market-line / live-room / dadan-net | 市场曲线 / 涨停直播 / 个股大单净额 |

**统计（/api/stats/）**：`auction-overview`（多时点对比卡）、`auction-snapshot`（时点个股）、`performance`、`daily-yizi`、`bid-snapshot`

鉴权：`Authorization: Bearer <token>`，401 前端自动跳登录。

## 数据表

| 表 | 说明 |
|---|---|
| users / batches / batch_stocks | 用户 / 选股批次 / 批次明细 |
| snapshot_bid | 四时点全市场快照（date+time_point+code，含 float_mv/board） |
| snapshot_lastsec | 最后一秒高频采样（date+code+ts，差值回退用） |
| qc_snapshot | 竞价抢筹结果快照（date+code，非竞价时段读库展示） |
| daily_yizi | 每日一字涨停汇总 |
| settings | 全局默认筛选参数 / 评分权重 |
| reset_tokens | 密码重置令牌 |

## 测试

后端 pytest（**约定：每次改动必须配套测试全量绿才提交/部署**）：

```bash
cd backend
python -m pytest tests/ -q     # 169 用例全绿
```

覆盖：选股接口、评分筛选算法、抢筹双表（差值回退/持久化/兜底）、快照存取与多时点、昨涨停/断板/炸板、开盘啦接口、权限、邀请裂变、管理后台等。

**真实浏览器回归**（测试机 chromium CDP，`scripts/browser_reg.py`）：登录 → /auction 八 Tab 数据断言 + 时点弹窗 + 抢筹双表布局 + 排序交互，全过才算部署成功；systemd `ExecStartPost` 自动触发，失败推飞书。

## 日志与排查

- **后端日志**：`/opt/kuaixuan/logs/app.log`（10MB 轮转保留 5 份）
  - 抢筹链路关键词：`抢筹[live]`（Type4 返回/过滤后落库）、`抢筹[saved]`（非竞价读库）、`抢筹[listLast]`（9_24/9_25 条数+秒级序列）、`抢筹[result]`（每次汇总）
  - 快照采集：`快照已存`、`最后一秒采样已存`、`今日快照采集缺失时点`（告警）
  - 排查示例：`grep 抢筹 /opt/kuaixuan/logs/app.log`、`grep ERROR`、`grep 限流`
- **前端日志**：JS 错误与 API 失败写入 localStorage（key `kuaixuan_front_log`，环形 50 条）

## 安全说明

- 密码 PBKDF2-SHA256 加盐；Token 12 小时有效、改密后全部失效
- 每 IP 限流（按 X-Real-IP）；注册防刷（同 IP 10 分钟 5 次）；重置邮件防轰炸
- 历史查询按 user_id 隔离；数据源 Token 走 systemd drop-in 不进 git
- 建议尽快上 HTTPS（当前 http 明文传输密码）

## 品牌

- 中文品牌：**快选 Kuaixuan**（AI 助手「悟空」同名 IP）
- Logo：`frontend/public/logo.png`；favicon 自动加载

## 历史里程碑

- **v1**：单文件 server.py + index.html
- **v2 (08-09)**：FastAPI 分层后端 + Vue3 工程化前端 + 同花顺兜底 + 统一日志 + 品牌定名
- **v3 (08-11)**：盘中实时模式 + 竞价锁定名单 + 历史批次 + 管理后台 + 推送去重 + pytest（127 用例）
- **v3.1 (08-12)**：盘中/竞价共用评分 + 全市场分页(6000只) + 锁定名单按条件过滤 + 全局默认参数后台可调 + 正式域名上线（pytest 131）
- **v3.2 (08-13 上午)**：接入开盘啦数据源（87 接口）+ 情绪面板 + 连板天梯 + 市场雷达 + 龙虎榜明细 + 尾盘抢筹推送 + 昨日涨停今表现（pytest 142）
- **v3.3 (08-13 下午)**：竞价异动页 8 Tab（委买/爆量/抢筹/净额/昨涨停/断板/上榜/炸板）+ 多时点对比卡 + 时点个股弹窗 + 抢筹双表（开盘啦 Type4）+ 全市场快照 5549 只 + 结果持久化 + 秒级高频采样差值回退（pytest 169）
- **v3.4 (08-13 晚)**：采集器 3 分区并发（600只 0.14s / 5549只 2.2s）+ 全表排序（useSortable）+ 字段补全（概念 f103 / 流通市值 f21）+ 浏览器回归体系（browser_reg / qc_layout_reg / sort_reg）
