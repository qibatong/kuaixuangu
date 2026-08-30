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
| **竞价异动页** | 10 Tab（封单/委买/爆量/抢筹/净额/昨涨停/断板/上榜/炸板）+ 三时点封单榜 + 表头排序 + 历史回看 |
| **竞价抢筹双表** | 左=竞价净额强度（开盘啦 Type4）；右=**最后一秒秒级差值回退**（9:24:55-9:25:03 每秒采样） |
| **AI 竞价预测（aipick）** | 独立 XGBoost 涨停概率模型：9:25 快照落库后立即触发，9:30 前出 Top 榜（`/aipick/`，**仅 VIP/付费/管理员**，登录 cookie / 分享 `?token=` 鉴权） |
| **aipick 历史兜底** | backfill 按交易日历扫描缺失报告自动补生成 + 每日 15:07 自动补跑（当天没看也能复盘） |
| **全市场快照库** | 9:15/9:20/9:24/9:25 四时点全市场自动归档 + 秒级采样 → 历史回放库 |
| **9:26 自动应用** | 系统统一标准筛一份推所有用户（`auto_applied=1`），没点"应用"也有历史；**9:26 自动检查缺失补跑 + 飞书告警** |
| **概念开盘啦化** | 全部股票概念替换为开盘啦风格 + 概念定时落库（`stock_concept` 表，100% 覆盖低延迟） |
| **会员体系** | 三层会员（免费试用/付费/VIP）；竞价/盘中/竞价异动会员专属；邀请码裂变 +7 天奖励 |
| **短信验证码找回密码** | 阿里云号码认证·短信（免资质），忘记密码双通道：手机验证码（主，60s 倒计时）/ 邮箱重置（备） |
| **主题与字号** | 背景黑白切换（导航栏圆点）+ 字号三档 + 三字体切换器（思源黑/宋/霞鹜） |
| **移动端适配** | 全站响应式（≤768px）：导航自动换行、宽表格横滑、弹窗近全屏 |
| **多数据源容灾** | 开盘啦（104 接口 100% 封装）+ 东方财富（主源）+ **腾讯行情兜底**（东财被墙/熔断自动切换）+ 同花顺兜底 + 通达信导入 |
| **数据源可观测性** | 熔断器（故障 60s 冷却）+ 健康快照 `/api/health`（`serviceable` 字段）+ 板块/热榜源故障前端警示 |
| **推送提醒** | 竞价锁定结果推飞书/Server酱/企业微信；尾盘抢筹 14:57 自动推送 |

## 页面总览

| 页面 | 路由 | 功能 |
|---|---|---|
| 选股主页 | `/` | 双模式 Tab（会员专属）+ 筛选 + 评分 + 市场情绪 + 奖牌区/自选池 |
| 竞价异动 | `/auction` | 10 Tab 竞价分析（VIP/付费门禁）+ 多时点对比 + 自动回退最近交易日 |
| 自选池 | `/pool` | 独立自选股票池管理 |
| 连板天梯 | `/ladder` | 首板~五板+ 实时梯队 + 涨停原因归因 |
| 市场雷达 | `/market` | 板块强度 + 人气热榜 + 龙虎榜（含营业部明细）+ 板块轮动历史（3 源） |
| AI 预测 | `/aipick` | 涨停概率 Top 榜（VIP/付费）+ 完整 HTML 报告（VIP 门禁）+ 历史日期回看 |
| 历史回看 | `/history` | 按批次分组 / 条件分页查询，全表排序（含系统自动批次） |
| 邀请裂变 | `/invite` | 邀请码生成与名单 |
| 管理后台 | `/admin` | 用户列表/会员到期+等级/评分权重/全局默认参数（仅管理员） |

## 快速开始

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

**注意**：验证走测试机（47.99.153.123），本地开发服务不作为验证手段。

## 构建与部署

服务器目录 `/opt/kuaixuan` · 双 systemd 服务：**`kuaixuan.service`（web）+ `kx-worker.service`（调度/推送）** · 部署详见 **[docs/deploy.md](docs/deploy.md)**。

```bash
cd frontend && npm run build    # 构建 dist
python scripts/sync_test_server.py   # 同步测试机(后端 md5 对比)
```

| 环境 | 地址 | 说明 |
|---|---|---|
| 测试机 | 47.99.153.123 | 默认部署目标（验证通过自动部署） |
| 生产机 | 121.196.230.80 | **必须主人明确指令才更新**（venv 路径与测试机不同） |

## 测试

后端 pytest（**约定：每次改动必须配套测试全量绿才提交/部署**）：

```bash
cd backend
python -m pytest tests/ -q     # 391 用例全绿(3 个东财网络偶发, 单跑通过) + 4 跳过(Redis 未装)
```

覆盖：评分筛选算法、抢筹双表（差值回退/持久化/兜底）、快照存取与多时点、三时点榜分层、会员三层/邀请奖励/邮箱认证、**短信验证码找回密码（阿里云闭环+防重放）**、**腾讯兜底源（字段映射/熔断切换）**、9:26 自动应用、**system_batch 自动批次监控**、stats 全路由、CacheStore 跨进程状态等。

**真实浏览器回归**（测试机 chromium CDP，`scripts/browser_reg.py`）：登录 → /auction 各 Tab 数据断言 + 弹窗 + 排序，全过才算部署成功；systemd `ExecStartPost` 自动触发，失败推飞书。

## 目录结构

```
kuaixuan/
├── backend/                    # FastAPI 分层
│   ├── app/
│   │   ├── main.py             # web 入口(纯 API, 无调度)
│   │   ├── worker.py           # worker 入口(快照调度/推送/队列, kx-worker.service)
│   │   ├── core/ db/           # 配置/日志 + SQLite 建表迁移
│   │   ├── services/           # cache_store/security/fetcher/scorer/kpl/auction_snapshot/system_batch/sms_verify/aipick_scheduler/notify...
│   │   └── api/                # auth/stocks/history/invite/prefs/admin/kpl/stats/health/aipick/sms
│   └── tests/                  # pytest 391 用例
├── frontend/                   # Vue 3 + Vite
│   └── src/
│       ├── router/ stores/ views/ components/ composables/
│       └── styles/main.css     # 明暗主题 + 移动端断点
├── docs/                       # 架构/功能/接口/部署/版本文档
├── scripts/                    # 运维/回归脚本(browser_reg/sync_test_server 等)
└── README.md
```

## 安全说明

- 密码 PBKDF2-SHA256 加盐；Token 12h 有效、改密后全端失效；另一设备登录踢出旧会话
- 每 IP 限流（跨进程 CacheStore 固定窗口）；注册防刷（同 IP 上限 + 自邀识别）；强制邮箱认证
- 历史查询按 user_id 隔离；数据源 Token / 阿里云短信 AK 走 systemd drop-in 不进 git
- 短信接口无登录鉴权（找回密码场景）→ 同号 60s + 同 IP 60s/10 次限流；验证码消费标记防重放
- 会员过期按权限拦截（管理员豁免）；生产已启用 HTTPS（www.kuaixuangu.cn）

## 品牌

- 中文品牌：**快选 Kuaixuan**（AI 助手「悟空」同名 IP）
- Logo：`frontend/public/logo.jpg`（红色实底 + 柱状图 + 上升箭头）；favicon 自动加载

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/features.md](docs/features.md) | 功能详述：双模式/会员体系/主题字号/竞价异动 10 Tab/抢筹双表/快照采集/概念开盘啦化/移动端/数据源 |
| [docs/deploy.md](docs/deploy.md) | 构建与部署手册：双服务/venv 差异/drop-in/推送配置/日志排查 |
| [docs/api.md](docs/api.md) | 接口概览（用户/kpl/stats）+ 数据表 |
| [docs/history.md](docs/history.md) | 完整版本历史（v1 → v4.2） |
| [docs/backend-architecture.md](docs/backend-architecture.md) | 后端架构重构方案（现状盘点/目标架构/分阶段计划） |
| [docs/kpl-interfaces.md](docs/kpl-interfaces.md) | 开盘啦接口索引（自动生成） |
| [docs/kpl-docs-coverage.md](docs/kpl-docs-coverage.md) | 104 接口全量核对报告 |
