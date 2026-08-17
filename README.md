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
| **概念开盘啦化** | 选股列表/历史回看/竞价异动全部股票概念自动替换为**开盘啦风格**（双层覆盖：榜单合并 + doc94 按股查询，100% 覆盖），前端只显示前 2 个 |
| **会员体系** | 竞价选股 / 盘中实时选股 / 竞价异动 仅会员可用（**竞价异动为 VIP/付费严格门禁**）；**三层会员**（免费试用/付费/VIP）；**邀请码非必填，填了邀请人 +7 天**；管理后台可续费/永久/自定义到期日/批量设到期；管理员豁免 |
| **邮箱认证** | 新注册强制邮箱验证（6 位码 30 分钟，SMTP 未配置自动降级）；老用户默认已认证；防刷号（同 IP 注册上限 + 自邀识别） |
| **账号安全** | 另一设备登录踢出旧会话（`code=kicked` 前端弹提示）；Token 12h 有效；改密全端失效 |
| **9:26 自动应用** | 抢筹落库后系统用**统一标准筛选一次推给所有用户**（`auto_applied=1` 标记），用户没点"应用"也有历史；手动筛选优先；全市场评分一次+后台线程，不阻塞调度 |
| **主题与字号** | 背景黑白切换 + 字号三档，按账号存服务端 prefs；**切换器收进右上角账号下拉菜单**（导航栏更清爽）；白色主题导航栏**红色渐变**（A股红喜庆） |
| **移动端适配** | 全站响应式（≤768px）：宽表格横滑、Tab/导航横滑、弹窗近全屏、触控加大、自选池两行布局、管理页工具栏 3 行布局 |
| **竞价异动页** | 10 个 Tab：竞价封单 / 竞价爆量 / 竞价抢筹 / 竞价委买 / 竞价净额 / 昨涨停 / 昨断板 / 昨上榜 / 昨炸板 / 今炸板，全部表头可排序 + 概念列（开盘啦前 2）+ 流通市值列 + 涨停原因列 |
| **竞价抢筹双表** | 左表 = 开盘啦 Type4 竞价净额强度（200 只涨停委买榜）；右表 = **最后一秒秒级差值回退**（9:24:55-9:25:03 每秒采样）；双表含「竞额/昨比」；**实时模式自动回退最近交易日**（盘前/周末也能看昨日数据） |
| **全市场快照库** | 9:15 / 9:20 / 9:24 / 9:25 四时点全市场（5549 只）自动归档 + 秒级采样，形成历史回放库 |
| **结果持久化** | 竞价类 tab（委买/爆量/抢筹）9:24-9:30 窗口落库、非竞价 tab（昨涨停/断板/炸板/上榜）15:30 日终落库 → 收盘后/历史回看数据完整 |
| **市场情绪面板** | 情绪值（进度条）→ 两市资金（缩量/放量）→ 涨跌家数（较昨同时刻）→ 涨停板 → **跌停板** → 高度板 → 大幅回撤；紧凑布局（手机端 2 行） |
| **多数据源容灾** | 开盘啦（付费 8 万次/天，**104 个文档接口 100% 已封装**）+ 东方财富 + 同花顺兜底 + 通达信导入 |
| **推送提醒** | 竞价锁定结果推飞书 / Server酱 / 企业微信；尾盘抢筹 14:57 自动推送 |
| **管理后台** | 用户列表（微信名/备注/付款备注独立列，搜索支持 6 字段，⋮ 下拉操作，会员 tab 拆 付费/VIP/普通/管理员，**邀请人列 + 邀请关系弹层**，批量设到期）/ 评分权重 / 全局默认筛选参数，仅管理员可访问 |
| **邀请裂变** | 用户级邀请码注册（生产 `LCFV77U8` / 测试 `T2FUR4ZC`），**被邀人 +7 天试用 + 邀请人 +7 天/人（顺延多邀多得）**，防自邀/同 IP 刷号 |

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

## 会员体系

**会员专属功能**：竞价选股、盘中实时选股、竞价异动（3 个页面）。**竞价异动为严格门禁**（仅 VIP/付费/管理员，免费/试用任何时段都不可访问）。

- **三层会员（`users.member_level`）**：
  | 等级 | 含义 | 拦截 |
  |---|---|---|
  | 0 | 免费试用（注册送 7 天） | 到期拦截 |
  | 1 | 付费会员（按到期时间） | 到期拦截 |
  | 2 | **VIP**（管理后台指定） | 前端展示按实际 expire_at；业务逻辑视为永久权限 |
- **拦截规则**：竞价选股/盘中选股仅**工作日 9:15-15:00** 对非会员拦截；竞价异动**任何时段**仅 VIP/付费/管理员可用（`require_vip_or_paid` 依赖，10 个端点统一）
- **新用户**：注册即送 **7 天试用**（`NEW_USER_DAYS`）
- **邮箱认证**：新注册 `email_verified=0` 强制邮箱验证后才可登录（`/api/verify-email` + 重发冷却 5 分钟）；老用户默认已认证；SMTP 未配置自动降级跳过
- **邀请码非必填**：注册时邀请码为空直接放行；填写则**被邀人 +7 天试用、邀请人 +7 天/人**（`INVITE_REWARD_DAYS`，按 max(now,当前到期) 顺延，多邀多得）
- **防刷**：同 IP 24h 注册上限（`REG_IP_DAY_LIMIT=5`）+ 自邀/同 IP 已邀≥3 不发奖励（`invite_reward_blocked`），注册仍成功
- **管理员豁免**：`isMember = isAdmin || !expired`，管理员永远有权限
- **到期判定**：`users.expire_at` 时间戳（0 = 永久）；登录/注册接口返回 `expire_at` / `expired` / `member_level`
- **开通/续费**：管理后台用户列表 ⋮ 下拉 → 永久 / 本周 / 本月 / 季度 / 年 / 自定义到期日（`/api/admin/users/expire`）；**批量设到期**（多选 checkbox → 批量延长/日期/永久）；**等级切换** → `/api/admin/users/member-level`
- **前端标识**：NavBar 显示会员等级徽标（VIP=金 / 付费会员=红 / 管理员=蓝 / 试用 X 天=紫）；`VipGate` 组件拦截（VIP 专属文案）
- **邀请关系**：管理员可看每用户「被谁邀请 / 邀请了谁」（含注册 IP 识别刷号），列表带邀请人列

---

## 主题与字号

- **切换器位置**：右上角**账号下拉菜单**顶部（2026-08-18 收进，导航栏只留一个用户名按钮）
- **背景明暗切换**（⚫/⚪）：黑色 / 白色（默认），全站通过 CSS 变量 `--bg-*` / `--text-*` / `--border-soft` 双套主题；涨跌语义色（红涨绿跌）、金银铜、健康灯不随主题变
- **白色主题导航栏**：**红色渐变** `linear-gradient(135deg,#e03a2f,#c62828)`（A股红喜庆，2026-08-17 主人钦定）
- **字号三档**（A / A / A）：小 `0.92` / 标准 `1` / 大 `1.12`，用 `document.documentElement.style.zoom` 整体缩放
- **持久化**：登录用户存服务端 `prefs`（`{ bg, font }`，后端合并保存不覆盖其他字段），未登录存 localStorage；换设备登录自动跟随
- 实现：`composables/useTheme.js` + `NavBar.vue` 账号下拉菜单

---

## 页面总览

| 页面 | 路由 | 功能 |
|---|---|---|
| 选股主页 | `/` | 双模式 Tab（竞价/盘中，**会员专属**）+ 筛选面板 + 评分面板 + 市场情绪面板 + **奖牌区（金银铜 + 自选股票池 左右分栏）** |
| **竞价异动** | `/auction` | 10 Tab 竞价分析（**VIP/付费门禁**）+ 顶部多时点对比（**默认折叠**）+ 时点个股弹窗 + 表头排序 + **自动回退最近交易日** |
| 连板天梯 | `/ladder` | 首板~五板+ 实时梯队 + 涨停原因题材归因 |
| 市场雷达 | `/market` | 板块强度（**行点击看成分股弹层**）+ 人气热榜 + 龙虎榜（含营业部明细弹窗） |
| 历史回看 | `/history` | 按批次分组视图 / 条件分页查询，全表排序 |
| 邀请裂变 | `/invite` | 邀请码生成与名单 |
| 管理后台 | `/admin` | 用户列表（**邀请人列**/批量设到期/**⋮ 菜单**）/重置密码/会员到期+等级/评分权重/全局默认参数（仅管理员） |

### 竞价异动页（/auction）10 Tab

| Tab | 数据源 | 说明 |
|---|---|---|
| **竞价封单** | 开盘啦涨停委买额（默认） | **三时点封单榜**：9:25→9:20→9:15 三层排序（9:25 封死 > 9:20 回落 > 9:15 回落）+ 加单趋势 9 种模式 + **弱市自动降级**（三层空时降到 ≥5%/≥3% 异动层）+ 全列排序；列序：#/代码/名称/概念/9:25封单/9:20封单/9:15封单/竞价涨幅/状态/加单趋势/实时涨幅/流通市值/操作 |
| 竞价委买 | 开盘啦 Type4 (st=200) | 涨停委买额榜（含竞价净额/连板/概念/流通） |
| 竞价爆量 | 开盘啦 Type10 | 竞价成交额榜（Type4 补委买额） |
| **竞价抢筹** | 开盘啦 Type4 + 秒级快照 | **双表**：左=竞价净额/流通市值强度(>5%过滤)，右=最后一秒涨幅差；**实时模式自动回退最近交易日**（盘前/周末可看昨日） |
| 竞价净额 | 开盘啦 Type4 | 按竞价净额排序 |
| 昨涨停 | 东财 flash + Type4 + 9_25 快照 | 昨日涨停股今日竞价表现（**涨停原因列** + 连板标记 + 竞价涨幅列） |
| 昨断板 | 昨日涨停池 − 今日池 | 断板股今日竞价表现（**涨停原因列** + 实时涨幅列） |
| 昨上榜 | 开盘啦龙虎榜 | 昨日龙虎榜（**涨停原因/竞价涨幅/流通列**） |
| 昨炸板 / 今炸板 | 东财 flash limit_up_broken | 炸板列表（**概念/涨停原因/竞价涨幅列**），今炸板连板数 merge 昨日涨停池 |

**多时点对比卡**：默认折叠（点击标题展开）；始终显示**最近 4 交易日** × 9:15/9:20/9:25 竞价均值与一字数；点格子弹窗看该时点竞价个股。
**非交易时段自动回退**：最近交易日 ≠ 今天时自动切到最近交易日回看（所有 tab 读历史快照），交易日 9:15 后自动切回实时。

### 竞价抢筹双表（核心）

- **左表（9:20-9:25 段）**：开盘啦 Type4 全市场竞价异动 200 只，`抢筹强度 = 竞价净额 / 流通市值 × 100`，过滤 `>5%` 且 `流通市值 ≥ 2亿`；竞价时段实时拉取 + 持久化 `qc_snapshot` 表。**涨幅抢筹**（第二表）过滤链：流通市值≥2亿 + 竞价额>0 + 竞价额≥500万 + 竞价涨幅>2%
- **右表（最后一秒）**：`snapshot_lastsec` 秒级高频采样（9:24:55-9:25:03 每秒一次，ts 记实际时刻），**差值回退算法**：
  1. `9_25 涨幅 − 最新秒涨幅`，|差| ≥ 0.5% → 直接用（最后一秒抢筹）
  2. 差太小（接口延迟导致最新秒已含变化）→ 向前回退：最新秒 − 倒数第二秒，依此类推取第一个大差值
  3. 全部差值小 → 取最大差（弱信号）；无秒级序列 → 回退 9_24 时点兜底
- **竞额/昨比列**：双表「竞价换手」列改为「竞额/昨比」= 今日竞价额 ÷ 昨日全天成交额（`fetcher.fetch_yesterday_amounts` pair[0]），≥20% 橙色高亮 / ≥10% 黄色
- **非竞价时段**：读库展示今天已选结果（不丢失）；**实时模式自动回退**：今日无快照时自动回退最近交易日（盘前/周末可看昨日数据，`date` 字段透出实际日期）

---

## 概念开盘啦化（全部股票）

选股列表 / 历史回看 / 竞价异动的**所有股票概念**统一替换为开盘啦风格（短板块名顿号分隔，如 `机器人概念、股权转让、汽车零部件`），替代东财 f103 逗号长串。实现为**双层覆盖**（`kpl.apply_board_concept`）：

1. **榜单层（快）**：开盘啦 5 路榜单接口合并（连板梯队/涨停委买/爆量/热点股/9:25 快照）→ 一次拿到 86+ 只热点股板块
2. **按股层（全）**：对榜单未覆盖的股票逐个调 doc94 `GetStockIDPlate`（个股全部相关概念板块）→ **100% 覆盖**；`ThreadPoolExecutor` 分批并发 + 每只 1 天缓存

**覆盖范围**：竞价选股（落库前覆盖 → 页面/历史批次/推送统一）、盘中选股、历史批次明细、历史聚合查询。**前端只显示前 2 个概念**（`shortConcept`），完整列表 hover title 查看。

**防限流设计**（踩坑修复）：`_call` 加全局信号量（并发 3），失败结果不缓存（避免 1 天空串缓存），按股查询分批 20 只/批。

---

## 快照采集体系（历史回放库）

后台调度在**独立进程** `kx-worker.service`（`app/worker.py`）运行，与 web 进程解耦（web 重启不影响采集）：

```
9:15:00 ── 9_15 全市场快照(5549只) ──────────────┐
9:20:00 ── 9_20 全市场快照 ─────────────────────┤ snapshot_bid 表
9:24:30 ── 9_24 重采覆盖(每10秒, 最后≈9:24:5x) ──┤ (date+time_point+code)
9:24:55 ─ 9_25:03 每秒高频采样 ─────────────────┼→ snapshot_lastsec 表
9:25:00 ── 9_25 全市场快照 ─────────────────────┤
9:24-9:30 ─ 竞价类tab落库(委买/爆量/抢筹 auction_daily_history) ──┐ 全天可回看
9:26:00 ── 自动应用: 系统统一标准筛一份推所有用户(batches, auto_applied=1, 后台线程)
9:30-15:00 ─ 两市分时快照滚动存(每5分钟, settings market_brief_intraday, 次日同时刻对比)
15:30:00 ─ 非竞价tab落库(昨涨停/断板/炸板/上榜) + 板块轮动/人气热榜/龙虎榜/连板梯队 日终归档
```

- 采集器 3 分区（沪深创科）**ThreadPoolExecutor 并发**拉取：单页 600 只 0.14s / 全市场 5549 只 2.2s
- `_fetch_lock` 串行化双线程，防东财并发限流
- 快照含：竞价涨幅、竞价额、名称、委买额、**流通市值(f21)**、**概念(f103/f100)**
- 9:31 盘点缺失时点告警（数据过了点无法补）
- **调度去重跨进程**（CacheStore `setnx`）：多 worker 不重复采集

---

## 数据源

| 数据源 | 用途 | 说明 |
|---|---|---|
| **开盘啦** (longhuvip.com) | 竞价/连板/情绪/板块/热榜/龙虎榜/抢筹 | **104 个文档接口 100% 已封装**（88 个编号 `fetch_kpl_docXX` + 16 个具名覆盖）；Token/UserID/DeviceID 走 systemd drop-in 注入，**不进 git** |
| **东方财富** (clist) | 全市场行情/选股/快照 | clist 分页（f12 排序 30 页×200 只全市场），K 线/昨日成交额有限流 → 同花顺兜底 |
| **选股宝** (xuangubao flash) | 涨停/跌停/炸板池 + 市场曲线 + **涨跌家数分布**（rise_count/fall_count 今日+昨日同时刻） | 免费公开接口，`limit_up_pool` / `limit_up_broken` 支持历史日期 |
| **通达信导入** | 用户自选股导入 | 独立小工具（PyInstaller 打包），`/download/` 提供下载 |

**开盘啦接口索引体系**（找接口三件套）：
- `scripts/kpl_interface_index.py` — AST 扫描脚本，提取所有 `fetch_*` 接口元信息（功能名/host/a=/c=/apiv=/调用点）自动生成索引
- `docs/kpl-interfaces.md` — 自动生成的接口索引文档（88 编号 + 31 具名，标已接入/未接入）
- `GET /api/kpl/interfaces` — 运行时查询端点，curl 即返回全部接口 `name/title/called`
- 全量核对报告：`docs/kpl-docs-coverage.md`（104 接口 100% 封装结论 + 具名覆盖映射表）

---

## 目录结构

```
kuaixuan/                        # 仓库根（GitHub: felix-rich/kuaixuan）
├── backend/                      # 后端 (FastAPI 分层)
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py               # web 入口: 中间件(限流) + 路由注册(无调度任务)
│   │   ├── worker.py             # worker 入口: 快照调度 + 尾盘推送 + 任务队列消费 (kx-worker.service)
│   │   ├── core/                 # config(含数据源Token占位) / logger
│   │   ├── db/database.py        # 建表 + WAL + 自动迁移 + task_queue 异步任务队列
│   │   ├── services/
│   │   │   ├── cache_store.py    # 跨进程缓存/限流/计数/锁 (Redis/SQLite 双实现, CACHE_BACKEND 切换)
│   │   │   ├── security.py       # 密码哈希 / Token / 限流(跨进程) / 防刷
│   │   │   ├── fetcher.py        # 东财+同花顺(兜底) 抓取/缓存/熔断/全市场分页
│   │   │   ├── scorer.py         # 选股评分与筛选算法(核心: 竞价五因子, 盘中共用)
│   │   │   ├── kpl.py            # 开盘啦/选股宝数据层(104 接口, 缓存/信号量外置+降级+抢筹双表)
│   │   │   ├── auction_snapshot.py # 快照调度(worker 进程): 时点全市场 + 秒级高频采样(差值回退) + 两市分时快照滚动存
│   │   │   ├── auto_apply.py       # 9:26 自动应用: 统一标准筛一份推所有用户(auto_applied 标记, 后台线程)
│   │   │   ├── stats.py          # 统计/时点快照查询
│   │   │   ├── notify.py         # 推送(飞书/Server酱/企微) + 去重
│   │   │   ├── wpqc_push.py      # 尾盘抢筹 14:57 定时推送(worker 进程, 去重跨进程)
│   │   │   └── users.py / history.py / settings.py / hot_rank.py / sector_rotation.py
│   │   └── api/
│   │       ├── deps.py           # 鉴权依赖 / 响应辅助 / 真实IP
│   │       ├── auth.py / stocks.py / history.py / invite.py / prefs.py
│   │       ├── admin.py          # 管理后台(仅管理员, 含会员等级接口)
│   │       ├── kpl.py            # 开盘啦全部路由 + /api/kpl/interfaces 接口索引
│   │       ├── stats.py          # 统计路由(auction-overview/auction-snapshot 等)
│   │       └── health.py         # 健康检查
│   └── tests/                    # pytest (246 用例 + 4 跳过)
│       ├── conftest.py           # 临时库 + 数据源 Mock + TestClient
│       ├── test_cache_store.py   # CacheStore 双实现(临时 DB)
│       ├── test_kpl.py           # 抢筹双表/差值回退/持久化/昨涨停/断板/炸板
│       ├── test_expire.py        # 会员到期/邀请奖励/等级
│       ├── test_snapshot.py      # 快照存取/多时点/回放
│       └── ...                   # auth/admin/history/invite/notify/phase1 等
├── frontend/                     # 前端 (Vue 3 + Vite)
│   └── src/
│       ├── router/index.js       # / /login /auction /ladder /market /history /invite /admin
│       ├── stores/               # Pinia: user / stocks / pool
│       ├── views/                # StockView / AuctionView(10Tab) / LadderView / MarketView / HistoryView / LoginView / InviteView / AdminView
│       ├── components/           # FilterPanel / MedalPanel / StockPoolPanel / StockTable / SentimentPanel / ChangePwdModal / VipGate(会员拦截) / NavBar(会员徽标)
│       ├── composables/          # useSortable(通用表头排序) / useTheme(背景黑白+字号三档)
│       └── styles/main.css       # 全局样式(含 .sortable / .pool-add-btn / 明暗主题 / 移动端断点)
├── docs/                         # 架构/接口文档
│   ├── backend-architecture.md   # 后端架构重构方案(现状盘点/目标架构/分阶段计划)
│   ├── kpl-interfaces.md         # 开盘啦接口索引(自动生成)
│   └── kpl-docs-coverage.md      # 104 接口全量核对报告
├── scripts/                      # 运维/回归脚本
│   ├── browser_reg.py            # 测试机真实浏览器回归(八Tab+弹窗)
│   ├── kpl_interface_index.py    # 开盘啦接口索引生成器
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

服务器目录: `/opt/kuaixuan` · systemd 服务: **`kuaixuan.service`（web）+ `kx-worker.service`（调度/推送）** · 数据库: `kuaixuan.db`

| 环境 | 地址 | 说明 |
|---|---|---|
| 测试机 | 47.99.153.123 | 默认部署目标（验证通过自动部署） |
| 生产机 | 121.196.230.80 | **必须主人明确指令才更新** |

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
#   kpl.conf (开盘啦 Token/UserID/DeviceID) / notify.conf (推送 webhook) — 不进 git
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

## 移动端适配（手机浏览器）

全站响应式适配（`≤768px` 断点 + `≤480px` 超窄屏），手机浏览器直接可用：

- **宽表格横向滑动**：`.auc-panel` / `.ladder-panel` / `.stock-table-container` 加 `overflow-x: auto`，900px 宽表格左右滑动看全列（不截断、信息完整）
- **Tab / 导航横滑**：竞价异动 10 个 Tab、顶部导航 7 入口改 `nowrap + overflow-x` 一排滑（不换行占纵向空间），隐藏滚动条
- **弹窗近全屏**：`snap-modal` 96vw / 88vh
- **触控优化**：操作按钮/输入框 padding 加大；手机隐藏用户名文本（保留会员徽标）
- **布局紧凑**：标题/副标题/时间换行、多时点对比表字号压缩、页面留白减半

## 接口概览

**选股/用户**：`/api/stocks`（lock/filter/refresh/ping + mode=auction/spot）、`/api/login`（返回 expire_at/expired/member_level，未验证邮箱 401 `need_verify_email`）、`/api/register`（**邀请码非必填**，新用户默认 7 天；带邀请码则被邀人 +7 天、邀请人 +7 天）、`/api/verify-email` / `/api/resend-verify`（邮箱认证）、`/api/change-password`、`/api/forgot`、`/api/reset`、`/api/history`、`/api/invite`、`/api/prefs`（合并保存，不覆盖其他字段）、`/api/admin/*`（含 `/api/admin/users/expire` 续费、`/api/admin/users/expire-batch` 批量设到期、`/api/admin/users/member-level` 会员等级、`/api/admin/user-invites` 邀请关系、`/api/admin/users?keyword=` 支持用户名/手机/邮箱/微信名/备注/付款备注、`memberTab=all|member|paid|vip|normal|admin`）

**开盘啦/选股宝（/api/kpl/）**：

| 接口 | 说明 |
|---|---|
| sentiment | 市场情绪（涨停家数/**跌停家数**/情绪值/连板高度/大幅回撤） |
| market-brief | **市场概览**：两市成交额+股票数（东财全市场 5min 缓存）+ 涨跌家数分布（xuangubao）+ **较昨日同时刻对比**（last_same_time） |
| bid-seal | 竞价涨停委买额榜（Type4） |
| bid-boom | 竞价爆量榜（Type10） |
| bid-qiangcang | **竞价抢筹双表**（左=净额强度 / 右=秒级差值回退） |
| yest-zt / yest-broken | 昨日涨停今表现 / 昨断板 |
| broken | 炸板列表（今日/历史日期） |
| ladder / zt-reason | 连板梯队 / 涨停原因 |
| board-rank / hot-rank / hot-stocks / hot-plates | 板块强度 / 人气热榜 / 热点强势股 / 板块题材 |
| board-stocks | **板块成分股**（开盘啦 ZhiShuStockList_W8，市场雷达行点击弹层） |
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
| users / batches / batch_stocks | 用户（含 expire_at 到期时间戳、member_level 等级、**register_ip/register_ua 防刷、email_verified 邮箱认证**）/ 选股批次（含 **auto_applied** 自动应用标记）/ 批次明细 |
| snapshot_bid | 四时点全市场快照（date+time_point+code，含 float_mv/board） |
| snapshot_lastsec | 最后一秒高频采样（date+code+ts，差值回退用） |
| qc_snapshot | 竞价抢筹结果快照（date+code，含 bid_ratio 竞额昨比，非竞价时段读库展示） |
| auction_daily_history | 竞价异动日终快照（date+tab+list：seal/boom/qiangcang/yest_zt/yest_broken/broken_yest/broken_today，历史回看数据源） |
| lhb_history | 龙虎榜日终快照（date+list，接口不支持历史，必须落库） |
| ladder_history | 连板梯队日终快照（date+pid_type+list） |
| daily_yizi | 每日一字涨停汇总 |
| settings | 全局默认筛选参数 / 评分权重 / **两市分时快照（market_brief_intraday_{date}）** / 两市收盘快照（market_brief_last） |
| reset_tokens | 密码重置令牌 |
| tokens | 登录令牌（含 **revoked 踢出标记**，新登录踢旧会话） |
| kv_cache | **跨进程状态存储**（CacheStore：缓存/限流/调度去重/分布式信号量，`CACHE_BACKEND=sqlite` 时使用） |
| task_queue | 异步任务队列（worker 进程消费，Phase1 落库仍同步，框架就绪） |

## 测试

后端 pytest（**约定：每次改动必须配套测试全量绿才提交/部署**）：

```bash
cd backend
python -m pytest tests/ -q     # 246 用例全绿 + 4 跳过(Redis 未装)
```

覆盖：选股接口、评分筛选算法、抢筹双表（差值回退/持久化/兜底）、快照存取与多时点、昨涨停/断板/炸板、开盘啦接口、权限、**会员三层（默认7天/续费叠加/过期拦截/管理员豁免/邀请奖励/防自邀/邮箱认证）**、邀请裂变、管理后台（**会员 tab 服务端过滤/付费-VIP 拆分/搜索 6 字段/批量设到期**）、**9:26 自动应用（统一标准/手动优先/评分复用）**、**市场概览（涨跌家数/两市概况/同时刻对比）**、**CacheStore 跨进程状态**等。

**真实浏览器回归**（测试机 chromium CDP，`scripts/browser_reg.py`）：登录 → /auction 八 Tab 数据断言 + 时点弹窗 + 抢筹双表布局 + 排序交互，全过才算部署成功；systemd `ExecStartPost` 自动触发，失败推飞书。

## 日志与排查

- **后端日志**：`/opt/kuaixuan/logs/app.log`（10MB 轮转保留 5 份）
  - 抢筹链路关键词：`抢筹[live]`（Type4 返回/过滤后落库）、`抢筹[saved]`（非竞价读库）、`抢筹[listLast]`（9_24/9_25 条数+秒级序列）、`抢筹[result]`（每次汇总）
  - 快照采集：`快照已存`、`最后一秒采样已存`、`今日快照采集缺失时点`（告警）
  - 排查示例：`grep 抢筹 /opt/kuaixuan/logs/app.log`、`grep ERROR`、`grep 限流`
- **前端日志**：JS 错误与 API 失败写入 localStorage（key `kuaixuan_front_log`，环形 50 条）

## 安全说明

- 密码 PBKDF2-SHA256 加盐；Token 12 小时有效、改密后全部失效
- 每 IP 限流（按 X-Real-IP，**跨进程 CacheStore 固定窗口**）；注册防刷（同 IP 10 分钟 5 次）；重置邮件防轰炸
- 历史查询按 user_id 隔离；数据源 Token 走 systemd drop-in 不进 git
- 会员过期后竞价/盘中/竞价异动 3 个页面按权限拦截，管理员豁免；到期时间服务端为准（`users.expire_at`）；VIP 业务逻辑视为永久权限
- 生产环境已启用 **HTTPS**（Nginx 443 证书）；域名 `www.kuaixuangu.cn`

## 品牌

- 中文品牌：**快选 Kuaixuan**（AI 助手「悟空」同名 IP）
- Logo：`frontend/public/logo.jpg`（红色实底 + 柱状图 + 上升箭头）；favicon 自动加载

## 历史里程碑

- **v1**：单文件 server.py + index.html
- **v2 (08-09)**：FastAPI 分层后端 + Vue3 工程化前端 + 同花顺兜底 + 统一日志 + 品牌定名
- **v3 (08-11)**：盘中实时模式 + 竞价锁定名单 + 历史批次 + 管理后台 + 推送去重 + pytest（127 用例）
- **v3.1 (08-12)**：盘中/竞价共用评分 + 全市场分页(6000只) + 锁定名单按条件过滤 + 全局默认参数后台可调 + 正式域名上线（pytest 131）
- **v3.2 (08-13 上午)**：接入开盘啦数据源（87 接口）+ 情绪面板 + 连板天梯 + 市场雷达 + 龙虎榜明细 + 尾盘抢筹推送 + 昨日涨停今表现（pytest 142）
- **v3.3 (08-13 下午)**：竞价异动页 8 Tab（委买/爆量/抢筹/净额/昨涨停/断板/上榜/炸板）+ 多时点对比卡 + 时点个股弹窗 + 抢筹双表（开盘啦 Type4）+ 全市场快照 5549 只 + 结果持久化 + 秒级高频采样差值回退（pytest 169）
- **v3.4 (08-13 晚)**：采集器 3 分区并发（600只 0.14s / 5549只 2.2s）+ 全表排序（useSortable）+ 字段补全（概念 f103 / 流通市值 f21）+ 浏览器回归体系（browser_reg / qc_layout_reg / sort_reg）
- **v3.5 (08-14)**：会员体系（竞价/盘中/竞价异动专属，工作日 9:15-15:00 拦截，新注册送 5 天，管理端续费）+ 背景黑白 / 字号三档主题 + 抢筹「竞额/昨比」列 + 抢筹过滤迭代（竞价额≥500万、竞价涨幅>2%）+ 全站浅色主题适配 + 新 Logo（pytest 177）
- **v3.6 (08-15)**：
  - **概念开盘啦化**：doc94 修 Type=2 + `fetch_stock_plate` 按股查全部概念板块 + 双层覆盖（榜单+按股）100% 全覆盖；历史回看同步覆盖；前端概念列只显示前 2 个（hover 看全）
  - **接口索引体系**：全量核对开盘啦文档 104 接口 100% 已封装（补 doc116 大面股实时）+ 索引三件套（扫描脚本 / docs 文档 / `/api/kpl/interfaces` 端点）
  - **会员优化**：邀请码非必填 + 邀请人 +5 天奖励 + 三层会员（免费/付费/VIP，member_level）
  - **UI 调整**：三时点封单改名**竞价封单** + 列序调整（9:25/9:20/9:15 封单前置）
- **v3.7 (08-16)**：
  - **全站移动端适配**（≤768px）：宽表格横滑看全列、Tab/导航横滑、弹窗近全屏、触控加大
  - **后端架构 Phase 1**（commit `458b9c1`）：CacheStore 跨进程状态外置（缓存/限流/调度去重/分布式信号量，SQLite 零依赖 / Redis 可切换）+ worker 进程拆分（`kx-worker.service` 独立调度推送，web 纯 API）+ task_queue 异步任务队列框架（pytest 213）
- **v3.8 (08-16 晚)**：
  - **9:26 自动应用**（commit `bab701b`+`c772139`+`df3d33e`）：抢筹落库后系统统一标准筛一份推所有用户（`auto_applied=1`），没点"应用"也有历史；手动筛选优先；`scorer` 拆 `score_all_stocks`（评分一次复用，性能 ~150 倍）+ 后台守护线程不阻塞调度
  - **管理后台用户列表优化**（commit `bc799a3`~`62299ed`）：会员 tab 服务端过滤（分页正确）、角色徽标合并显示、微信名/备注/付款备注独立列、⋮ 下拉操作、搜索扩 6 字段、**付费/VIP 拆分 tab**（会员=付费+VIP 不设总览避免歧义）
  - **市场情绪扩展**（commit `d1e8347`+`677a493`）：两市成交额+股票数（东财全市场 5min 缓存）+ 涨跌家数分布（xuangubao）+ **较昨日同时刻对比**（worker 每 5 分钟滚动存分时快照 `market_brief_intraday`，次日起按 ts 匹配）
  - **会员文案**：VIP 老师 → **VIP**（全站统一）；新用户/到期用户拦截页明确"请联系管理员开通权限"
  - **注册防滥用**：手机号+邮箱必填；错误格式不计限流防误锁
  - **UI 修复**：排序箭头默认态 0.25 透明度 → 0.6（黑/白背景均可见）
  - **测试基建修复**：conftest 双载导致 BID_DB_PATH 错位（全量 43 errors 根因），幂等守卫修复（pytest **245**）
- **v3.9 (08-17~08-18)**：
  - **用户裂变扩展**（commit `c13890d`+`9f1662d`）：被邀人/邀请人各 +7 天（顺延多邀多得）、**防自邀/同 IP 刷号**（register_ip/register_ua + 上限）、管理员看**邀请人列 + 邀请关系弹层**、**新注册强制邮箱认证**（6 位码 30 分钟，SMTP 未配降级）
  - **账号安全**（commit `1e1c742`）：另一设备登录踢出旧会话（tokens 加 revoked 列，401 `code=kicked` 前端弹提示）
  - **概念开盘啦化全覆盖**（`e51dd51`+`7fa7f0c`）：竞价异动全部 tab 概念统一开盘啦 + 只取前 2 + 炸板/上榜补概念列；**涨停原因列**（昨涨停/昨断板/昨上榜）+ 统一涨幅列（竞价/实时）
  - **竞价抢筹修复**（`b20529b`+`470ec24`）：实时涨幅不再兜底 9:25 竞价涨幅（真实盘中值，无则 `-`）；**实时模式自动回退最近交易日**（盘前/周末可看昨日数据）
  - **竞价异动体验**（`ad0263c`+`517c483`+`83265e7`+`ab71b2d`）：多时点对比**默认折叠**（始终显示最近 4 交易日）、非交易时段自动回退读历史快照、移除个股封单搜索工具条
  - **导航栏重构**（`e1bc16d`~`7fb92dd`+`962e6f1`+`af7e2e6`）：logo 进导航栏、slogan 定位、**用户名下拉菜单**（资料/改密/退出 + **主题/字号切换器收进菜单**）、白色主题**红色导航栏**
  - **市场情绪面板**（`a2a6603`+`e823551`）：去"昨日涨停今表现"、加**涨停/跌停家数**、排序重排（情绪值→两市资金→涨跌家数→涨停板→跌停板→高度板→大幅回撤）、手机紧凑 2 行
  - **奖牌区迭代**（`04fbd48`~`111ef5e`）：去五因子明细 → 恢复竞涨幅+可信度 → **实时涨幅顶替评分位大字 + 竞涨幅缩字号 + 95分+可信度到底部**；卡片**内容垂直居中 + 四周 8px 间距**；**电脑端放大字号**（涨幅 38px/名称 27px/评分 15px）**手机端独立缩小**（26px/14px/12px，scoped 覆盖）→ 电脑充实、手机紧凑
  - **自选股池**（`b38a2e3`）：手机端两行布局（上行代码+名称 / 下行涨跌幅+分+时间+移除）、时间精度 HH:MM
  - **管理后台 toolbar**（`0983048`+`a577753`）：手机端 3 行布局（5 tab 一行 + 搜索行 + 批量/新建行）；**desktop 显式横排右对齐**（margin-left:auto，修复上版布局塌陷）
  - **选股列表**（`0ae2ba2`）：去掉"已跌出实时榜"保留显示，跌出实时榜的股票**直接移除**（mergeSpotIntoLocked / updateRealTimeOnly 双处）
  - **市场雷达**（`6d830fe`+`27e4f89`）：板块强度**行点击看成分股弹层**（开盘啦 ZhiShuStockList_W8，字段映射修正）
  - **板块轮动修复**（`6b4a1fa`）：scheduler 失败删 setnx key 窗口内重试（源1 kpl 缺今日数据根因）
  - **竞价封单弱市降级**（`e951e29`）：三层涨停榜空时降级 ≥5%/≥3% 异动层 + 顶部弱市提示
  - **批量管理**（`b840bae`+`58e20f5`）：批量设到期（多选 checkbox）+ 输入框放大 + 白底按钮主题化
  - 前端性能（`6453e9a`）：昨比只对展示前 100 拉取（5000→300 只，60s→2.1s）+ loadAll 12s 超时降级
  - **已全量同步生产**（测试/生产/本地一致，pytest **246**）
