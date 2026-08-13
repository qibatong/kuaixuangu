# 快选 Kuaixuan · 竞价 AI 选股系统

**技术栈**：后端 FastAPI (Python 3.11) + 前端 Vue 3 / Vite / Pinia / Vue Router

选股算法（评分权重、筛选逻辑、数据抓取）全部在服务端，浏览器只有界面代码；
用户体系（注册/登录/邀请）、历史记录、筛选偏好按用户隔离。

**双模式（同一套选股逻辑）**：竞价选股（9:15-9:31 竞价锁定，9:30 后名单恒定） + 盘中实时选股（9:30-15:00 同一套评分/筛选持续实时刷新，名单会变）。主页 Tab 切换。

## 双模式选股

**v3.1 起盘中与竞价共用同一套评分与筛选逻辑**（诗人需求：盘中 = 不锁定的竞价），区别只在"是否锁定名单"：

| 维度 | 竞价选股 (auction) | 盘中实时选股 (spot) |
|---|---|---|
| 时段 | 9:15-9:31 竞价锁定 | 9:30-15:00 实时 |
| 数据 | 竞价字段(f615/f616) + 昨日成交额 | 实时行情（同一套竞价字段） |
| 评分 | 竞价五因子(涨幅34/换手32/异动17/市值11/昨涨6) | **同左（同一套评分）** |
| 筛选 | 竞价条件（剔除ST/停牌、剔除昨日涨停、竞价涨幅≤7%、市值/股价/竞价金额） | **同左（同一套筛选）** |
| 名单 | **9:30 前锁定后恒定**，9:30 后只更新实时行情 | **每次刷新全市场重筛**（名单会变） |
| 落库/推送 | lock 落库 + 推送 | 不落库不推送 |

**竞价锁定名单语义**：9:30 前 lock 的名单恒定不变（后端 lock 批次为权威 + 本地快照兜底）。9:30 后刷新时：
- 名单不增删，只按 code 合并全市场实时行情（spotMap）更新实时涨幅/评分
- 按**当前筛选条件**过滤：被条件剔除的票直接移除（如勾选剔除昨日涨停）；条件放行但行情不在榜的标"已跌出"
- 涨回来的票自动恢复正常（每次刷新重新判定）
- **筛选重算（filter）不受锁定约束**：改条件点"筛选重算"即按新条件全市场重新筛选（修复了"改条件永远同一批票"问题）

**跌出票的实时行情一致性**：后端返回全市场 spotMap（code→实时涨幅/量比/换手/价格），跌出票与在榜票的实时涨幅列数据同源一致，行灰显 + "已跌出"标签区分。

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
│       │   ├── fetcher.py         # 东财+同花顺(兜底) 数据抓取 / 缓存 / 熔断 / 全市场分页(f12排序30页)
│       │   ├── scorer.py          # 选股评分与筛选算法(核心机密: 竞价五因子, 盘中共用)
│       │   ├── kpl.py             # 开盘啦数据源(竞价委买额/连板/情绪/涨停原因/板块/热榜/龙虎榜, 缓存+降级)
│       │   ├── users.py           # 用户 / 邀请 / 密码重置邮件
│       │   └── history.py         # 历史批次落库与分页查询
│       └── api/                   # 路由层
│           ├── deps.py            # 鉴权依赖 / 响应辅助 / 真实IP
│           ├── auth.py            # 登录/注册/改密/忘记/重置
│           ├── stocks.py          # 选股(竞价/盘中同一套逻辑, mode 区分锁定与否)
│           ├── history.py         # 历史
│           ├── invite.py          # 邀请
│           └── prefs.py           # 偏好 + 全局默认筛选参数读取
├── frontend/                      # 前端 (Vue 3 + Vite)
│   ├── public/                    # 静态资源(Logo, favicon), Vite 原样拷贝到 dist
│   │   ├── logo.png               # 产品 Logo (1024x1024)
│   │   └── favicon.png            # 浏览器标签
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js             # 构建配置 + 开发代理
│   └── src/
│       ├── main.js / App.vue
│       ├── router/index.js        # 路由: /(选股) /login /history /invite /admin
│       ├── stores/                # Pinia: user(会话) stocks(选股/双模式) pool(股票池)
│       ├── api/                   # request 封装 + 各模块接口
│       ├── utils/                 # toast / 北京时间 / 通达信工具
│       ├── views/                 # LoginView / StockView(双模式Tab) / HistoryView(按批次/综合查询) / InviteView / AdminView
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

生产正式域名: **www.kuaixuangu.cn**（阿里云备案，Nginx `server_name` 绑定，裸域 kuaixuangu.cn 301 跳转 www）。
生产机 121.196.230.80 · 测试机 47.99.153.123（两台同版本部署）。

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

## 推送提醒（微信 / 飞书）

竞价锁定选股（action=lock）成功后，自动把当日 Top N 结果推送到已配置的渠道（后台线程，不影响选股响应；任一渠道失败不影响其他渠道与主流程；相同内容 120 秒内去重防刷屏）。

| 环境变量 | 说明 |
|---|---|
| NOTIFY_FEISHU_WEBHOOK | 飞书群机器人 webhook（可选） |
| NOTIFY_SERVERCHAN_KEY | Server酱 SendKey，推送到个人微信（可选） |
| NOTIFY_WECHAT_WEBHOOK | 企业微信群机器人 webhook（可选） |
| NOTIFY_TOP_N | 推送展示 Top N 只（默认 8） |
| NOTIFY_TIMEOUT | 单渠道请求超时秒数（默认 5） |
| NOTIFY_DEDUP_SECONDS | 相同内容去重窗口秒数（默认 120） |

任一渠道配置后即启用，全部未配置则推送自动跳过（不影响选股功能）。systemd 里用 `Environment=` 注入。

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
| /api/stocks | GET | 选股: action=lock(9:30前锁定)/filter(重算)/refresh(实时)/ping + mode=auction(默认)/spot(同一套逻辑) |
| /api/history, /api/history/query | GET | 历史批次(含按批次明细) / 条件分页查询 |
| /api/invite, /api/invite/refresh | GET/POST | 邀请码与名单 |
| /api/prefs | GET/POST | 账号级筛选偏好 |
| /api/prefs/defaults | GET | 全局默认筛选参数(所有用户, 管理员可调) |
| /api/admin/users | GET | 管理端用户列表(分页/搜索) + 统计(仅管理员) |
| /api/admin/scoring | GET/PUT | 管理端竞价评分权重 + 打分明细读写(仅管理员) |
| /api/admin/defaults | GET/PUT | 管理端全局默认筛选参数读写(仅管理员, 存 settings 表) |
| /api/kpl/sentiment | GET | 市场情绪(涨停家数/情绪值/连板高度/大幅回撤) |
| /api/kpl/bid-seal | GET | 竞价涨停委买额(涨停委买额/竞价净额/连板数) |
| /api/kpl/ladder | GET | 连板梯队(首板~五板+) |
| /api/kpl/zt-reason?code= | GET | 个股涨停原因(题材归因/龙一龙二) |
| /api/kpl/board-rank | GET | 板块强度排行 |
| /api/kpl/hot-rank | GET | 盘中人气热榜 |
| /api/kpl/lhb | GET | 龙虎榜上榜股票 |

鉴权：`Authorization: Bearer <token>`（或 ?token=），401 时前端自动跳登录。

数据源容灾：昨日成交额 = 东财 K 线（被封时）→ 自动切同花顺兜底；选股主接口 clist(push2dycalc) 正常；
全市场行情(spotMap) = clist 分页拉取（**按代码 f12 排序，30 页×200 只覆盖全 A + 北交所，30s 缓存**，任一页失败跳过不阻塞）。

**全局默认筛选参数**：管理员可在管理端调整（存 settings 表 `default_filters`），所有用户加载顺序 = 本地锁定 > 账号偏好 > **全局默认(管理员可调)** > 内置默认。默认竞价金额下限 1000 万、股票池 9:30 前自动收录前 5 名。

## 测试

后端 pytest（约定：每次改动必须配套测试用例全量绿才提交/部署）：

```bash
cd backend
python -m pytest tests/ -q     # 当前 131 个用例全绿
```

覆盖：选股接口(lock/filter/refresh/spot/权限)、评分与筛选算法、竞价/昨比窗口口径与日期错位回归、
昨日成交额 pair 解析、全市场分页拉取、历史批次、邀请裂变、管理后台（用户/评分/默认参数）等。

## 安全说明

- 密码 PBKDF2-SHA256 加盐存储；Token 12 小时有效、改密后全部失效
- 每 IP 每分钟限流（Nginx 反代后按 X-Real-IP 计）；注册防刷（同 IP 10 分钟 5 次）；重置邮件防轰炸
- 历史查询按 user_id 隔离
- 建议尽快上 HTTPS（当前 http 明文传输密码）

## 品牌与 Logo

- 中文品牌：**快选 Kuaixuan**（localStorage key `kuaixuan_*` 已统一）
- 完整 Logo：项目内 `frontend/public/logo.png` 或 https://<你的部署地址>/logo.png
- 浏览器标签：自动获取 `favicon.png`（刷新即生效）

## 历史里程碑

- v1: 单文件 server.py + index.html
- v2 (2026-08-09):
  - Python 3.11 + FastAPI 分层后端
  - Vue 3 工程化前端（Pinia / Vue Router / 组件化）
  - 同花顺数据源兜底（东财被封时仍能拿到竞价/昨比）
  - 统一日志体系（HTTP + 业务全覆盖，10MB 轮转）
  - 工程定名「快选 Kuaixuan」+ 内部标识全链路统一（目录/服务/数据库/Nginx 全部 kuaixuan）
  - Logo 集成 + 浏览器 favicon
- v3 (2026-08-11):
  - **盘中实时选股模式**：全市场分页拉取(5000只) + 涨停池封单/连板数据 + 盘中六因子评分，主页双模式 Tab 切换
  - **竞价锁定名单恒定**：9:30 后名单不漂移（后端 lock 批次为准），实时行情按 code 合并，跌出标记/自动恢复
  - **竞价/昨比口径修复**：分母恒取最近已收盘交易日（跳过今日未收盘K线，兼容东财/同花顺日期格式）
  - 历史页按批次分组视图（每次选股一组，可展开看明细）
  - 策略股票池按当前模式收录 + 表格每行手动入池按钮 + 加入全部
  - 管理后台（用户列表/重置密码/评分权重配置）
  - 飞书/Server酱推送去重防刷屏（竞价窗口放大）
  - pytest 自动化测试体系（127 用例）
- v3.1 (2026-08-12):
  - **盘中与竞价合并为同一套评分/筛选**（诗人需求：盘中 = 不锁定的竞价）；删除盘中独立六因子评分/过滤（DEFAULT_SCORING_SPOT/apply_spot_filters/process_spot_stocks），盘中 Tab 保留但逻辑与竞价一致
  - 管理端同步简化：盘中评分配置移除，仅保留竞价评分（权重/置信度/打分明细因子 Tab 可调）
  - **全市场分页修复**：改为按代码(f12)排序 + 30 页 6000 只，修复跌出票不在 spotMap 导致实时涨幅缺失（美利云等）；spotMap 补量比/换手字段
  - **锁定名单按当前筛选条件过滤**：被条件剔除的票直接移除（哈药"涨停却显示跌出"根因修复），条件放行但行情不在榜才标"已跌出"
  - **筛选重算不再套锁定名单**：改条件后按新条件全市场重筛（修复"改条件永远同一批票"）
  - **全局默认筛选参数后台可调**（settings 表 default_filters + 管理端配置卡片 + /api/prefs/defaults）；默认竞价金额下限 3000→1000 万
  - 策略股票池自动收录前三 → **前五**（含文案/manualAdd 同步）
  - 正式域名 www.kuaixuangu.cn 上线（阿里云备案 + Nginx server_name 绑定 + 裸域 301）
  - 生产/测试环境部署流程标准化（备份→上传→py_compile→重启→验证）；pytest 131 用例
- v3.2 (2026-08-13): **接入开盘啦数据源（对标短线侠）**
  - 新增 `services/kpl.py` 数据层 + `/api/kpl/*` 路由：竞价涨停委买额、市场情绪值、连板梯队、涨停原因、板块强度、人气热榜、龙虎榜、尾盘抢筹（均缓存 + 失败降级，Token 走 systemd drop-in 注入不进 git）
  - **首页市场情绪面板**（涨停家数/情绪值/连板高度/大幅回撤）+ **竞价表连板标签**（涨停委买额/连板数，替代 f103 概念标签判断）
  - **连板天梯页** `/ladder`（首板~五板+实时梯队 + 涨停原因题材归因查询）
  - **市场雷达页** `/market`（板块强度排行 + 盘中人气热榜 + 龙虎榜三 Tab，龙虎榜支持营业部明细弹窗）
  - **尾盘竞价抢筹推送**：工作日 14:57 自动拉取抢筹榜推送到飞书/微信
  - **昨日涨停今表现**：情绪面板展示涨停/连板/破板今日平均涨幅（策略验证）
  - pytest 142 用例

