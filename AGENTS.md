# 快选 Kuaixuan · Agent 工作手册

> 本文件是仓库内给 AI 代理 / 协作者的项目约定速查。
> 规则在每次改动前必须遵守；改动落地流程见「工作流」章节。
> ⚠️ 本仓库公开可见：**严禁写入任何服务器地址、账号、密码、token、邀请码等敏感信息**。
> 部署所需的凭据以本机私有记忆（.workbuddy/memory）为准，不在仓库内复制。

## 一、项目简介

快选 Kuaixuan · 竞价 AI 选股系统（A 股竞价/盘中选股）。
- **后端** FastAPI + uvicorn（8010），核心模块 `backend/app/services/kpl.py`（竞价异动约 2700 行）、`scorer.py`（全市场评分）、`fetcher.py`（多数据源）、`history.py`（批次/历史回看/直读）、`api/` 下按页面对应路由。
- **前端** Vue 3 + Vite + Pinia + Vue Router；核心页面 `views/AuctionView.vue`（竞价异动 10 个 tab）、`views/StockView.vue`（首页左视图：AI竞价 / AI预测）、`stores/stocks.js`（选股状态机）、`composables/usePolling.js`（轮询）。
- **架构** Nginx 反代（/ 服务 dist 静态、/api 反代 8010）+ systemd kuaixuan + kx-worker。
- 功能面：竞价选股（9:25 锁定名单语义）、竞价异动 10 tab、AI 竞价预测报告、自选池、历史回看/回放、用户/会员体系、连板天梯、板块轮动、规则转发等。
- 两套部署环境：**测试机**（日常迭代验证）与**生产机**（公开服务 kuaixuangu.cn），约定见「部署」。

## 二、工作流硬性规则

1. **代码改动一律先上测试环境验证**，不在本地起服务验证；验证通过才算完成一步。
2. **生产环境必须等主人明确指令**才更新；测试通过后默认只推测试机 + commit/push。
3. **提交约定**：测试机验证通过 → 自动 `git add` + commit（按逻辑分组，message 用中文如实描述）→ push。
4. **push 规范**（Windows 浅克隆后遗症，详见下文）：不要依赖 `git branch -vv`/`git status -sb` 的提示判断是否同步——**以 `git ls-remote origin main` 与本地 HEAD 是否一致为准**；推送用显式 refspec：`git push origin HEAD:main`。
5. **避免 rebase**：Windows 环境下 git rebase 曾反复损坏 `.git`（refs/pack 丢失）；一律 fetch 后快进 push。
6. **改动前先同步远程**：`git fetch origin main`，确认不基于旧代码。
7. **commit 前清理临时脚本**（`.deploy_*.py` / `.shot*.py` / `.scan*.py` 等不入库；前端临时构建包同样清理）。
8. **新增功能配套 pytest 全量绿才提交**；工程化/UI 大改需真实浏览器/全流程验证，不接受仅接口测试。
9. **⛔ 协作红线**：每完成一项必须停下汇报、等主人明确指示，**不得自行扩大改动范围**；发现可优化点只提建议（含收益/风险/工作量），不动手。例外仅限主人已授权同一改动内的必要修正。
10. UI 改动汇报用「改动项 / 验证 / 待确认」+ 前后对比，直给结论不铺垫。

## 三、Git 浅克隆后遗症（已知、不影响使用）

- `git status -sb` 显示 `## main...origin/main [gone]`、`git branch -vv` 显示 `[origin/main: gone]`：**元数据丢失的假象**，commit/push 都正常。
- `git branch --set-upstream-to=origin/main` 会报 `not a valid branch point`——修不好，别浪费时间。
- `git rev-parse HEAD~1` 报 unknown revision（浅克隆无 parent）。
- 判断代码是否已同步：`git ls-remote origin main` 对比本地 `git rev-parse HEAD`。
- 仓库损坏恢复步骤（如 `fatal: bad object HEAD`）：
  1. `git fetch --depth 1 origin main`（避免全量慢速中断）
  2. `git update-ref refs/heads/main $(git rev-parse origin/main)`（失败用 `git ls-remote` 手动指）
  3. `git reset --mixed main`（**绝不用 --hard**，会覆盖工作区改动）

## 四、部署约定

| 维度 | 测试机 | 生产机 |
|---|---|---|
| 后端 venv | `/opt/bid-venv` | **`/opt/kuaixuan-venv`**（不同！部署脚本按环境切换） |
| 服务 | kuaixuan(uvicorn 8010) + kx-worker | 同左（生产另有 smtp 等 drop-in） |
| HTTPS | 无（http 明文 80） | 有（80 → 301 → https://www.kuaixuangu.cn） |
| 前端 root | `/opt/kuaixuan/dist` | `/opt/kuaixuan/dist`（非 frontend/dist！排查先看时间戳/assets） |
| umask | 022 | **027 → 前端部署后必须 `chmod -R a+rX dist`** |
| 备份习惯 | dist.bak.N | backend.bak.N / dist.bak.N |

- 后端同步：sftp 单文件 + 本地/远端 **md5 对比**（Windows 坑：md5sum 输出带前导反斜杠 `\2a16…`，须正则提取 32 位 hex 比对）→ `py_compile` → `systemctl restart kuaixuan`。
- 前端发布：`npm run build` 前若 dist 存在，用 **python shutil.rmtree** 清理（`rm -rf` 会被 node safe-delete shim 拦截）；上传用 **tar.gz 打包整个 dist 单文件 sftp**（34MB）；远端 `cp -a` 备份 → `rm -rf dist` → `tar -xzf` → chmod。
- 远程基准测试脚本：服务器 venv 无 httpx2 → 不能用 FastAPI TestClient，改走 `127.0.0.1:8010` 真实 HTTP；上传 /tmp 执行须 `PYTHONPATH=/opt/kuaixuan/backend`。

## 五、缓存与性能硬约定（血泪教训）

1. **结果缓存的耗时主体必须在 loader 内部**——曾把查询写在 `_compute()` 外面、缓存只包住装饰逻辑，命中缓存仍每次查库（生产 956ms 全是它）。加缓存后逐行确认。
2. **TTL 必须明显大于前端轮询周期**（TTL≤轮询 ⇒ 命中率≈0）。当前后端 TTL：3points 15s / market-brief·history 30s / auction-overview·stocks 60s → 前端轮询 **30s（默认）**；**不要做 5s/10s 短轮询**（摧毁缓存 + 请求翻 6 倍 + 重新限流）。
3. 错误响应（400/404）不写缓存，否则错误被固化。
4. **并发线程池**：禁止「每次请求新建 ThreadPoolExecutor + shutdown(wait=False)」（9/1 生产 2 worker × 2082 线程拖死全站）；一律进程级常驻共享池（fetcher `_EXECUTOR_CLIST(8)`/`_EXECUTOR_YDAY(8)`、kpl `_EXECUTOR_FILL(6)`）。数线程：`grep Threads /proc/<pid>/status`。
5. **前端静态资源缓存（Nginx）**：`/assets/` 必须 `Cache-Control: public, max-age=31536000, immutable`（Vite 文件名带 hash 可安全 immutable）；`/`(index.html) 必须保持 `no-store`（否则发版后用户拿旧入口白屏）。两者曾都被设 no-store → 586 个字体每次全量重下。
6. woff2 已是压缩格式，**不要对 font 开 gzip**。

## 六、前端设计约束

1. **五色原则**：平台只用 黑/白/红/绿/黄；**新增/改动颜色前先查是否引入蓝/紫/青等杂色**（2026-09-05 全站收敛完成）。图表内部（K 线均线、轮动图系列色）豁免多色。
2. **涨跌配色**：A 股惯例 红涨绿跌；`--accent` 主红（深色 `#ff5c5c`；浅色主题重定义为深红 `#c62828` 保证白底对比），`var()` 引用自动适配双主题。
3. **浅色主题对比度**：白底上不用亮黄/亮红当文字，一律深变体（`#8a5500` 深金 / `#b83010` 深红）；"灰"也必须是中性灰（R≈G≈B，`#9a9a9a` 而非 `#9aa0aa` 这类带蓝调的灰）。
4. **字体合规**：系统字体（微软雅黑/PingFang）无商业授权，项目用 SIL OFL 的 Noto Sans/Serif SC（main.js 引入 @fontsource 4 包）；「改回系统字体」方案不可行。
5. **FontAwesome 4.7.0**：`fa-robot`/`fa-microchip`/`fa-brain` 是 FA5 图标**不渲染**；机器人语义用 `fa-android`。新增图标先确认 FA4.7 支持。
6. **同类按钮组尺寸显式对齐**：各按钮有独立 padding（mode-tab-compact / filter-apply·reset·lock 均不同），新增同类按钮必须补对应样式，否则回落基础类与邻居不一致。
7. **轮询/刷新 UX**：数据加载函数返回成败标志，失败不清空已有数据（保留上次成功结果 + 角落「稍后重试」）；首屏 loading 大块占位仅首次/切日，轮询刷新用角落静默 spinner（`silentRefreshing`）；`usePolling(fn, ms, {backoff:true})` 失败指数退避（×2^n 上限 5min，成功重置；fn 返回 undefined 视为成功向后兼容）。
8. 大块面板可折叠/默认收起；移动端（≤768px）按钮宽度/间距/padding 精细。

## 七、选股核心语义（stocks store / 后端批次）

- **9:30 前**：action=lock，全市场锁定 + 当日幂等落库（`batch_date`=当日）。
- **9:30 后**：action=refresh → 后端直读当日批次（优先级：用户手动 lock → 手动 filter → 9:26 系统统一批次 auto），仅实时行情覆盖，名单定格。
- **当日无批次/休市**：自动回退 14 天窗口内**最近交易日**同参批次直读（`find_recent_reusable_batch`），响应带 `reusedDate`，前端直接采用并提示「已载入 X 的选股名单」——解决「关闭后再打开首页转圈」。
- 参数指纹（`_canon_filter_fingerprint`）不一致 = 用户改过条件 → 必须重算，不回退不直读；空名单批次（stock_count=0）无直读价值。
- 筛选权重：竞价涨幅 34% / 换手率 32% / 异动等级 17% / 流通市值 11% / 昨日涨幅 6%（配套大票策略 ≤300 元、≤1000 亿）。

## 八、数据源与熔断（fetcher）

> **2026-09-10 去兜底重构（主人拍板）**：原「东财→腾讯→量脉→同花顺」多级兜底链**全部下线**。
> 理由：① 兜底源没有 f615/f616/f617 竞价字段，只能用现价涨幅/成交额**近似填充** →
> 竞价时段一旦切兜底，竞价数据即为编造值，选股结果失真；② 熔断一旦打开即整段冷却期
> "不敢试"，流量全推给兜底源 → 主源与兜底源**横跳**，同一只票两次请求口径不同；
> ③ 实测主力源 push2dycalc 盘中 11-14 时成功率 99-100%，兜底只集中在事故时段救场。
> 现行为：**拿不到就如实失败/置空**，由上层沿用旧缓存或置空（评分层容忍缺失）。
> 各兜底函数体**保留未删**（便于回滚），但主链不再调用。量脉（liangmai）**代码已整体删除**。

- **全市场行情**：**单一东财** `push2dycalc`（clist 30 页并发 40s 超时，全市场 5558 只）。
  `_fetch_market_with_fallback` / `_fetch_market_all_with_fallback` 现为东财直通；
  `ensure_spot_cache` 失败时**只沿用本地旧缓存**，无旧缓存则抛出。
- **K 线 chart**：**单一东财** `push2his`（`KLINE_HOSTS` 多域名轮询）。`fetch_stock_chart_robust`
  的 `sources` 只剩 `["eastmoney"]`（原 东财→腾讯→tushare→ths→kpl→自聚合 链已下线）。
- **昨比（昨日成交额）= 收盘落库 + 全天读库**（2026-09-10 新增，替代原四级兜底链）：
  每交易日 **15:10** `yday_prewarm._prewarm_once(stage="close")` 批量拉全市场写入
  `yday_amount` 表（**按 code 覆盖写**，见 `database.init_db` 建表注释），此后全天
  `_yday_hydrate_from_db` 直接读库（**零网络零兜底**）；只有库里没有的（新股/停牌/任务未跑）
  才实时拉东财日 K。**盘中预热(stage="open")不落库**（那时 T=前一交易日，写进去会污染语义）。
  → 运维注意：机制**从首次收盘刷新（15:10）起才生效**，此前 `yday_amount` 为空 = 走实时源。
- **腾讯并未废弃**，但它现在是**功能源不是兜底**：`picker/sources/tencent.py`（竞价窗口名单源）、
  `fetch_tencent_by_codes`（点查补丁源）、`fetch_tencent_market`（picker 用）。
  **f4=昨收 / f5=成交量 必补**（缺这两个字段 `is_suspended` 会把数据误判停牌 → 选股 0 只，
  生产 7052 事故）；**f17=今开必补**（缺则实体列恒 0%）。
- **熔断器**：`fetcher._check_circuit(src)/_record(src, ok, ms)`；**昨比/昨涨的短路 = 东财日 K
  单源 down**（原「四源全 down」随兜底链删除而收窄）；每源独立 `down_threshold`
  （ths/tencent=2、其余=1），指数退避 cooldown 60→…→600s。**熔断生效期内的成功不解除熔断**
  （2026-09-10 commit 30f224a：防全市场 30 页并发时失败页刚置 down、成功页立刻清零 →
  同一调用内反复横跳、下次仍完整重试 30 页）。
- **东财 push2his（历史 K 线）自 8/30 起为接口级全局时段性风控**，与出口 IP 无关
  （公司网/阿里云测试/生产/代理家宽同一时刻全部 0~10%，而同机 push2dycalc 100% 通）；
  多米 + 快代理两家独立测出同一结论 → **买代理解决不了，别买**。等自愈（熔断半开探测）。
- 健康接口 `/api/health`：overall / serviceable / sources；数据源故障前端琥珀提示。

## 九、测试约定与 pytest 坑

- 接口加结果缓存后，原「monkeypatch 子函数」用例模式会失效：接口可能命中上一用例写入的缓存 → 脏数据。**修法：用例开头清缓存** `store.delete(<接口缓存 key>)`；排查特征：单独跑通过、整文件跑失败 = 文件内缓存污染。
- `Monkeypatch.setattr` 新增属性必须 `raising=False`（否则给对象设原本不存在的属性抛 AttributeError，被 try/except 吞掉则表现为"属性神秘失踪"）。
- conftest 会桩掉预热函数（`kpl.start_kpl_prewarm`/`_kpl_prewarm_once` 为空 lambda）；测预热行为调留存版本 `kpl._kpl_prewarm_once_real()`。
- 已知 flake（非回归）：`test_stocks.py::test_fetch_eastmoney_all_paginates` 等全量偶发失败，单独重跑必绿（东财网络抖动），判定回归先单独复现。
- 测试批次/指纹类用例：插入数据的 filters 必须用 `scorer.validate_filters()` 的**完整输出**构造（后端会补默认值，残缺参数指纹必然不匹配）。
- 共享测试库下用例间数据隔离：批次等表跨用例污染 → 用独立用户（create_user_token）或清表。
- **fetcher 全局状态隔离（2026-09-10 新增）**：`conftest._isolate_fetcher_globals` 每个用例前后
  快照/还原 `fetcher._HEALTH` + `_broken_hosts`。原因：去兜底后短路条件收窄为「东财日 K 单源」，
  任何把 `eastmoney_kline` 打进熔断的用例（如 test_stocks 的 K 线失败用例）都会让**后续用例**
  期望"数据源健康"的断言集体失败 —— 表征为**单文件绿、全量红**（顺序耦合）。
  → 新增用例若需改熔断状态，靠这个夹具自动隔离，不要自己 clear。
- **conftest 会整体替换若干函数**：`fetch_tencent_market`、`fetch_yesterday_changes`、
  `fetch_yesterday_amounts`、`ensure_cache`、`load_snapshot_full`。想测**真实实现**的文件必须在
  import 期留下 `_ORIG_xxx = fetcher.xxx` 再用 autouse fixture 还原（见 test_yesterday_cache /
  test_tencent_fallback），否则测到的是恒返回假数据的桩（曾导致 11 条用例长期假红）。
- 基线认知：**HEAD 全量约 27 红**（多为历史债：test_history / test_slow_api_cache /
  test_auction_snap_pool_offhours / test_snapshot_915_timing + 少量顺序污染）。
  判定本次改动是否引入回归，**必须与 `_backend_bak/`（HEAD 物理备份）对比**，不要只看红数。

## 十、可复用工具脚本（scripts/）

- 后端同步部署 + 幂等/直读/缓存校验模板：`.deploy_*.py`（paramiko 直连 22 + 密码，md5 正则比对，py_compile + restart + journalctl 看 Traceback）。
- 前端 dist 发布：build(shutil.rmtree 清 dist) → tar.gz → sftp → 备份/解压/chmod → curl 入口 chunk 验证。
- 颜色审计：`python scripts/.color_audit.py` 按 5 色分类统计全站 hex（加新色前跑一遍看是否引入杂色）。
