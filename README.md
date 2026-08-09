# 快选 Kuaixuan · 竞价 AI 选股系统

**技术栈**：后端 FastAPI (Python 3.11) + 前端 Vue 3 / Vite / Pinia / Vue Router

选股算法（评分权重、筛选逻辑、数据抓取）全部在服务端，浏览器只有界面代码；
用户体系（注册/登录/邀请）、历史记录、筛选偏好按用户隔离。

## 目录结构

```
kuaixuan/                        # 仓库根（GitHub: felix-rich/kuaixuan）
├── backend/                      # 后端 (FastAPI 分层)
│   ├── requirements.txt
│   └── app/
│       ├── main.py               # 应用入口: 中间件(限流) + 路由注册 + 启动初始化
│       ├── core/                  # 配置 / 日志
│       ├── db/database.py         # 建表 + WAL + 老库自动迁移
│       ├── services/              # 业务逻辑层
│       │   ├── security.py        # 密码哈希 / Token / 限流 / 防刷
│       │   ├── fetcher.py         # 东财+同花顺(兜底) 数据抓取 / 缓存 / 熔断
│       │   ├── scorer.py          # 选股评分与筛选算法(核心机密)
│       │   ├── users.py           # 用户 / 邀请 / 密码重置邮件
│       │   └── history.py         # 历史批次落库与分页查询
│       └── api/                   # 路由层
│           ├── deps.py            # 鉴权依赖 / 响应辅助 / 真实IP
│           ├── auth.py            # 登录/注册/改密/忘记/重置
│           ├── stocks.py          # 选股
│           ├── history.py         # 历史
│           ├── invite.py          # 邀请
│           └── prefs.py           # 偏好
├── frontend/                      # 前端 (Vue 3 + Vite)
│   ├── public/                    # 静态资源(Logo, favicon), Vite 原样拷贝到 dist
│   │   ├── logo.png               # 产品 Logo (1024x1024)
│   │   └── favicon.png            # 浏览器标签
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js             # 构建配置 + 开发代理
│   └── src/
│       ├── main.js / App.vue
│       ├── router/index.js        # 路由: /(选股) /login /history /invite
│       ├── stores/                # Pinia: user(会话) stocks(选股) pool(股票池)
│       ├── api/                   # request 封装 + 各模块接口
│       ├── utils/                 # toast / 北京时间 / 通达信工具
│       ├── views/                 # LoginView / StockView / HistoryView / InviteView
│       ├── components/            # FilterPanel / MedalPanel / StockPoolPanel / StockTable / ChangePwdModal
│       └── styles/main.css
├── tdx_import.py                  # 通达信导入小工具(独立, PyInstaller 打包)
└── README.md
```

## 本地开发

```bash
# 后端 (Python 3.11, 装依赖后启动)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload

# 前端 (Node 18+, 热更新)
cd frontend
npm install
npm run dev        # 默认 5173, /api 代理到 8010
```

## 构建与部署

服务器目录: `/opt/kuaixuan` · 服务: `kuaixuan.service` · 数据库: `kuaixuan.db`

```bash
# 前端构建 -> dist/
cd frontend && npm run build

# 部署 (Nginx + systemd)
# - dist/  → /opt/kuaixuan/dist   (Nginx 静态托管, SPA try_files)
# - backend/ → /opt/kuaixuan/backend
#   systemd: kuaixuan.service
#   ExecStart=/opt/bid-venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
#   Environment=SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt   (自编译 OpenSSL 需指 CA)
# - 数据库: 项目根 kuaixuan.db (BID_DB_PATH 可覆盖)
# - 备份: /root/backup_db.sh (cron 每日 03:00, 保留 30 天)
# - 日志: /opt/kuaixuan/logs/app.log
```

Nginx 关键配置（/etc/nginx/conf.d/kuaixuan.conf）：
- `/`  → 静态托管 dist + `try_files $uri /index.html`（SPA）
- `/assets/` → 长缓存 30 天
- `/api/` → 反代 127.0.0.1:8010（传 X-Real-IP / X-Forwarded-For）
- `/download/` → 通达信工具静态下载

## 日志与排查

- **后端日志**：`/opt/kuaixuan/logs/app.log`（10MB 大小轮转保留 5 份，可用 `BID_LOG_DIR` 覆盖）
  - 每个 HTTP 请求：`IP 方法 路径 uid= 状态码 耗时ms`（含被限流/401 的请求）
  - 业务关键点：登录成功/失败、注册、改密、重置邮件、选股各 action（落库批次号/返回数/耗时）、东财拉取与缓存命中、同花顺兜底、昨日成交额失败统计、历史查询、邀请、偏好
  - 排查示例：`tail -f /opt/kuaixuan/logs/app.log`；找错误 `grep ERROR`，找限流 `grep 限流`
- **前端日志**：JS 运行时错误与 API 失败自动写入浏览器 localStorage（key `kuaixuan_front_log`，环形保留 50 条），控制台可见 `[bid]` 前缀
- **通达信工具**：`C:\Users\{用户}\tdx_import.log`

## 接口概览

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/login /register | POST | 登录(手机/邮箱/用户名)、注册(邀请码) |
| /api/change-password | POST | 修改密码(改后强制下线) |
| /api/forgot /reset | POST | 邮件重置密码 |
| /api/stocks | GET | 选股: action=lock(9:30前锁定)/filter(重算)/refresh(实时)/ping |
| /api/history, /api/history/query | GET | 历史批次 / 条件分页查询 |
| /api/invite, /api/invite/refresh | GET/POST | 邀请码与名单 |
| /api/prefs | GET/POST | 账号级筛选偏好 |

鉴权：`Authorization: Bearer <token>`（或 ?token=），401 时前端自动跳登录。

数据源容灾：昨日成交额 = 东财 K 线（被封时）→ 自动切同花顺兜底；选股主接口 clist(push2dycalc) 正常。

## 安全说明

- 密码 PBKDF2-SHA256 加盐存储；Token 12 小时有效、改密后全部失效
- 每 IP 每分钟限流（Nginx 反代后按 X-Real-IP 计）；注册防刷（同 IP 10 分钟 5 次）；重置邮件防轰炸
- 历史查询按 user_id 隔离
- 建议尽快上 HTTPS（当前 http 明文传输密码）

## 品牌与 Logo

- 中文品牌：**快选 Kuaixuan**（localStorage key `kuaixuan_*` 已统一）
- 完整 Logo：项目内 `frontend/public/logo.png` 或 https://<你的部署地址>/logo.png
- 浏览器标签：自动获取 `favicon.png`（刷新即生效）
- 推广文案：见本文末尾「推广文案」章节

## 历史里程碑

- v1: 单文件 server.py + index.html
- v2 (2026-08-09):
  - Python 3.11 + FastAPI 分层后端
  - Vue 3 工程化前端（Pinia / Vue Router / 组件化）
  - 同花顺数据源兜底（东财被封时仍能拿到竞价/昨比）
  - 统一日志体系（HTTP + 业务全覆盖，10MB 轮转）
  - 工程定名「快选 Kuaixuan」+ 内部标识全链路统一（目录/服务/数据库/Nginx 全部 kuaixuan）
  - Logo 集成 + 浏览器 favicon

## 推广文案（直接复制）

### 朋友圈 / 微博（短）

> 🔥 **快选 Kuaixuan** 上线了！9:25-9:30 竞价时段 AI 选股工具，三秒看穿资金抢筹方向。
> 自带：金叉评分 / 策略股票池 / 竞价占比 / 一键导入通达信。
> 📊 http://<你的部署地址> （完全免费 · 邀请制）

### 群公告 / 群发（详细）

> 【重磅工具】**快选 Kuaixuan** · 竞价 AI 选股系统
>
> 🎯 专为 A 股竞价时段（9:25-9:30）打造：
> - 9:30 前**锁定当日前三强**：AI 综合评分（竞价/活跃度/异动/市值/昨日表现）
> - 9:30 后只更新实时涨幅，竞价缓存不浪费
> - 策略股票池自动收录 + 10 小时防刷新锁定
> - 一键下载 .blk → 自动导入通达信自选股（带"监控剪贴板"免重启弹窗）
> - 账号数据隔离，历史可回看
>
> ⚠️ AI 选股，仅供参考，投资有风险
>
> 🌐 立即体验：http://<你的部署地址>
> 🎁 注册邀请码：**<你的邀请码>**（填了就是我邀请的，多谢支持 🙏）

### 私信邀请（针对单个朋友）

> X 哥，最近在用个工具——**快选**，专门在 9:25-9:30 竞价时段帮你筛强势股的。
> 周末不开盘也能回看历史选股，每天数据自动落库。
> 你试试，挺好用：http://<你的部署地址>
> 注册时填邀请码 **<你的邀请码>**，能给我多一个被邀请名额 😄

### 一句话简介（聊天群/签名档）

> 9:25-9:30 竞价时段 AI 选股 → http://<你的部署地址> (快选 Kuaixuan)

### 海报文案（如需做图）

**主标题**：快选 Kuaixuan
**副标题**：9:25-9:30 竞价时段 AI 选股
**三个卖点**（圆角卡片横排）：
1. 🤖 AI 综合评分
2. 💰 竞价/昨比一眼看穿
3. 📋 一键导入通达信
**底部**：⚠️ 仅供参考，投资有风险
**二维码 + 网址**：http://<你的部署地址>
**邀请码**：<你的邀请码>
