# 版本历史

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
- **v4.0 (08-18)**：
  - **手机端用户名下拉菜单修复**（`6e43a8c`+`a1098c7`）：iOS Safari 滚动容器裁剪 → `position: fixed` + JS 锚点 → **Teleport 到 body**（脱离所有容器，z-index 99999 视图顶层），全平台可弹
  - **导航栏主题圆点**（`8486f49`）：主题设置移出下拉改为**导航栏快捷圆点**（⚫/⚪ 点击即切）；手机导航栏自动换行不横滑
  - **统一批次完整保留**（`e0f7edb`+`0ad0000`）：9:26 系统统一批次名单恒定完整（不在实时榜保留竞价值，不过滤）；跌出实时榜的股票**保留显示仅去标签**
  - **忘记密码邮件链接修复**（`48740eb`）：路由守卫跳 /login 保留原始 `reset` query → 点击邮件链接直达设置新密码
  - **AI 竞价迁移服务器**（`50a8727`~`3eb355a`）：采集/打标签/训练/预测定时任务从 WorkBuddy 本地自动化迁到 **kx-worker 调度**（`aipick_scheduler.py`，subprocess + setnx 去重）；预测挪到 **9:25 快照落库后立即触发**（事件驱动，9:30 前出 Top30）；aipick 复用快选 `snapshot_bid` 采集（不再自拉全市场）；预测报告浏览器入口 `/aipick/`（Nginx alias + latest.html）
  - **竞价异动数据缺口修复**：
    - 抢筹左表永远为空（`083a768`）：qcDelta 阈值 5% 实测强抢筹仅 0.4-3% → **0.5%**
    - 连板梯队实时涨幅 / 热点榜概念缺失（`776d682`）：merge 东财实时行情 + 概念覆盖
    - 三时点榜/抢筹右表字段补全（`c846e77`）：东财全市场实时涨幅 + 概念按股补齐
    - **竞价换手全 tab 覆盖**（`6d466e1`）：爆量/龙虎榜/三时点榜快照补算（修万元/元单位坑）
    - **竞价爆量加竞价量比 + 昨日竞价额**（`0a9754c`，今日/昨日竞价额比）
  - **9_25 快照延迟采集**（`ebfb986`）：9:25:10 后采集避开撮合瞬间中间态，多机数据一致
  - **竞价净额 tab 改 doc112**（`a97e938`~`d8348bb`）：开盘啦 Type=2 竞价>1000万全市场；换手/成交额快照补全
  - **昨涨停/昨断板语义修正**（`a416ad9`~`8f9683f`）：昨炸板读 prev 日 broken_today；**昨断板 = 前一日连板≥2 且昨日未涨停**（连板中断，flash 计算法）；昨涨停恢复 flash 昨日池（开盘啦 801900 Date 是指数日语义盘后取不到）；实时涨幅 merge 东财（`aafbd0e`）
  - **竞价异动页性能优化**（`68aca1f`）：概念 deep 结果**跨 tab 共享池**（1h）+ board_map 缓存 300s + 东财行情 120s；**前端按需加载**（切 tab 才拉，首屏 25s→4.2s）；轮询只刷当前 tab
  - **手机端抢筹表格精简列**（`9dc9c4e`+ 后续）：≤700px 隐藏次要列保留 8 核心列塞进视口，概念列固定窄宽省略号
  - **选股加载文案**（`132650d`）：统一改"后台正在计算选股中..."
  - 测试机 aipick 补齐（`594db4b`）：manylinux2014 老版本 wheel（CentOS7 glibc 兼容）+ 生产同步 aipick.db
- **v4.1 (08-19~08-21)**：
  - **首页拆分左右视图**（`63650f1`）：左=选股主流程（筛选/奖牌/自选池/单子），右=竞价异动；宽屏 50/50 并排，窄屏(<1100px)顶部"选股/竞价异动"切换栏单块显示；右视图各 tab 滚动时列表 sticky 固定
  - **图表弹窗**（`ed30498`+`59c3df3`）：选股表/自选股/查看页点击代码打开分时/日K/周K/月K 弹窗；多数据源 fallback（东财→同花顺→开盘啦→Tushare，K线用日线聚合）
  - **选股表布局优化**（`5d241da`~`cfe0781`）：名称列去首板/二连板标签、代码+名称合并、列宽紧凑；概念列每个概念独立换行(`concept-item`)；模块切换"竞价/盘中"选中态橙色实底高亮
  - **自选股清空修复**（`e089459`）：清空后不再自动恢复（锁定时间戳），通达信导入后自选池自动加股
  - **概念定时落库读库**（`6c2765c`~`5eaf5db`）：新增 `stock_concept` 概念映射表；`concept_refresh` 每半小时从**所有竞价/上榜实时接口**采集股票概念全量写库（覆盖盘中新增，如竞价爆量 407 只）；竞价各接口改从库读概念（`apply_board_concept_db`），不再每次请求实时逐股查开盘啦 → 全表格概念 100% 覆盖且低延迟
  - **竞价爆量改版**（`7b8df9c`~`8319ed3`）：按竞价量比(今日/昨日竞价额)排序 + 金额≥1000万+量比>2 双重过滤 + 实时涨幅全天有值（东财行情合并）+ 去掉连板列/净额列
  - **竞价页面概念列横屏适配**（`83fe1d9`~`c47fd84`）：各 tab 概念列按概念换行(`pre-line`)逐步加宽 56→90px；qc 表手机端整表横向滚动(不再隐藏次要列)
- **v4.2 (08-22~08-24)**：
  - **图表弹窗完善**（`24aba23`~`2737a09`）：修复分时/日K/周K/月K弹图问题；K线叠加 **MA5/10/20/30/60 均线**；周K/月K改用腾讯接口作主源（修复高价股数据错误）；分时也接入腾讯接口；弹窗股票名颜色修正
  - **独立自选股票池页面**（`898d3e4`）：新增 `/pool` 路由 + 导航栏入口，独立页面管理自选股
  - **异动监管模块**（`b2b780c`~`56dcb98`）：新增异动监管接口与页面；首页选股与竞价异动表格标记异动监管股票（按异动类型区分显示）；严重异动接口补充偏离值/天数/触发阈值字段；热门股偏离值后端接口与前端页签；异动实时页签改名为"严重异动"
  - **9:30 后竞价选股名单固定**（`659562b`）：手动锁定不被统一批次覆盖
  - **导航栏调整**（`e70f1d7`）：顺序改为 选股/自选/历史回看/市场雷达/连板天梯/邀请/管理；移除导航栏竞价异动入口（`6430f4c`）
  - **默认主题黑色 + 登录后主题修复**（`11ebaa0`）
  - **竞价爆量过滤 + 现涨竞涨区分 + 全局字体 LXGW 等宽**（`c9b935b`）：量比>2 + 金额≥1000万双重过滤
  - **三字体切换器**（`029c893`）：思源黑/思源宋/霞鹜，默认思源黑体
  - **周末非交易日数据缺失修复**（`753a91c`）：竞价/昨上榜自动回退最近交易日
  - **现涨口径统一与持久化**（`b101507`）：竞价异动全表 + 封单三时点榜 现涨(change) 统一（`close_change_history` 表 + 收盘自愈机制）
  - **全站股票图表弹窗 + 竞价爆量历史竞换修复**（`ab62065`）
  - **自选页复制/导出 + 最后一秒抢筹竞换兜底**（`85cd497`）
  - **盘中实时自动刷新**（`823de1d`）：盘中 30s 自动刷新现涨/实时涨幅（静默不弹 toast）；分时图未交易时段留白；竞价时段判定修复
  - **项目代理工作规则**（`10f2e7f`）：新增 `AGENTS.md`（git 操作规范、浅克隆补全、合并前备份等）
  - **yidong-realtime 偏离值补全**（`a6f4b57`）：异动监管接口补 change/days/deviation/target 字段
  - **现涨/竞涨相同修复**（`7b01425`+`c5bbc5e`）：`close_change_history` 表此前存储了盘中实时涨幅而非收盘涨幅 → 新增 `_close_chg_persist_allowed` 控制持久化时机（仅非交易日或 15:00 后写入）+ 收盘自愈机制；东财接口失败时自动切换腾讯/新浪备用数据源
  - **竞换精度4位 + 昨炸板口径修复**（`281936c`）：竞换 `round(...,2)` → `round(...,4)`（0.0017% 不再被四舍五入为 0.00%）；昨炸板 tab 的竞涨/竞换/竞额/流通市值/现涨全部改用**今日** 9:25 快照（股票池仍为 prev 日炸板）；前端竞换 0 值不再被三元判断显示为 `-`；轮询间隔 60s→30s
  - **首页表格固定表头**（`ed1d3a9`）：宽屏(≥1280px) `thead th` 用 `position: sticky` 固定在 filter/tabs 下方；JS 动态测量 sticky 元素高度写入 `--sticky-thead-top` CSS 变量
  - **三时点封单现涨回填**（`b38f610`）：`real_change` 口径回填 + 修复 3 个失败测试
- **v4.3 (08-25~08-30)**：
  - **短信验证码 + 找回密码双通道**（`9c7eddb`/`3646881`）：阿里云号码认证·短信（个人免资质）；注册保持关闭；找回密码双 tab（手机验证码主 / 邮箱重置备）；未绑定 404 省短信费；防重放 5min
  - **登录写 cookie**（`c1bc0e8` 后）：`/api/login` Set-Cookie `kx_token`（HttpOnly/SameSite=Lax），地址栏直接访问 `/aipick/*` 自动鉴权
  - **AI 预测 VIP 门禁**（`ffefcd9`）：`/aipick/*.html` 静态报告 Nginx auth_request → 后端 auth-check；匿名 401 / 免费 403 / VIP·管理员·分享 `?token=` 200；前端「完整报告」按钮
  - **aipick 默认过滤 30-100亿/≥3000万/≤7%**：后端 predict_daily.py 常量 + 前端规则条默认 + `HIST_DEFAULTS` 自动迁移
  - **aipick 历史兜底**（`345c092`）：backfill 按交易日历扫描缺失报告补生成 + 每日 15:07 自动补跑；修复调度器 `--label` 参数从未生效的隐藏 bug
  - **系统自动选股批次**（`9cc0552`）：9:25 后 worker 自动跑 system_batch（`user_id=0+auto_applied=1`）→ 历史回看所有用户可见；9:26 检查缺失补跑 + 飞书告警（`17b589f`）
  - **腾讯行情兜底源**（`2db0fea`）：东财被墙（生产机 IP 被封 `Remote end closed`）→ 自动切腾讯（5545 只全量，字段映射近似）；熔断器 60s 冷却 + `/api/health` serviceable
  - **兜底路径全覆盖**（`cb53533`）：修复 4 条漏网路径（fetch_market_brief / fetch_spot_quote_map / auction_snapshot._grab / fetch_yesterday_amounts 并发卡 504）+ concurrent import 缺失
  - **可观测性补全**（`5c4ccf4`）：熔断短路留日志 / 腾讯兜底成功留痕 / health serviceable 字段
  - **板块/热榜源故障前端警示**（`519395c`）：em 源失败显示「数据源故障，请切换源」琥珀色提示（后端 `_SRC_ERR` 标记 + `source_failed` 字段）
  - **登录页 UI 打磨**：忘记密码表单等高 43px/等距 gap:10px/防浏览器自动填充（`one-time-code`/`new-password`）
- **v4.4 (08-31~09-01)**：
  - **8/31 生产事故修复链**（`4aa4f35`）：东财/同花顺 K 线双源熔断抖动 → 三时点榜接口 K 线兜底并发**无整体超时**（实测单请求 693s 卡死）→ 全站（含 health）瘫痪 → K 线兜底加 **15s 整体超时** + shutdown(wait=False)、东财全市场分页加 **40s 整体超时**
  - **日志风暴修复**（`5ea0d76`）：双源熔断时昨比逐只打 WARNING **36804 条**拖死 worker（选股 674s）→ 批级短路一条聚合日志 + 线程池 `shutdown(wait=False)`
  - **昨比链腾讯日K第三源**（`5659cfd`）：`proxy.finance.qq.com/newfqkline` qfqday 第 8 字段=成交额(万元)，绕开东财 K 线被 IP 封禁；短路逻辑双源 → **三源**
  - **腾讯全市场兜底并发**（`340b1c4`）：19 批串行 → 5 并发，**10-18s → 831ms**（5545 只）；快照采集耗时独立日志
  - **量脉数据源接入**（`4c4922b`+`d487af2`）：liangmai.pro 第 N 源（688 元/年）——全市场行情兜底（东财→腾讯→量脉）/ 昨比兜底（第 4 源）/ **独立校验任务**（交易日 9:40+15:10：涨停池双源交叉比对 / 抢筹涨幅自检 / 行情健康自检 → 异常推飞书+Server酱+企微，手机微信可达）；`market_snapshot_all` 文件锁跨进程节流 65s（官方 1 次/分）
  - **aipick 三连修**（用户反馈"没以前准+竞价跌停混入"）：
    - **跌停股混入根因**：过滤只有 ≤7% 上限无下限 → 改**涨停率 ≥50% 剔除**（跌停股模型概率极低被自然过滤，更合理）
    - **打标签失效 8 天**（8/18-8/28 连续缺失，`--label` 参数从未生效）：close_change_history + 腾讯日K 补全 **28048+18851 条标签至 100%**（停牌股按历史口径补 0）→ 重训模型 **n_train=70680 / AUC≈0.70**
    - 前端加**涨停率筛选框**（默认 ≥50%，`8c31ac1`）
  - **评分构成不对用户暴露**（`a2cbb72`）：前端评分列悬浮五因子明细移除 + 后端 stocks API 移除 `factors` 字段（竞价/盘中两个返回点）
  - **system batch 修复**（`2a67e4d`）：自动批次 DEFAULT_FILTER 键名与 validate_filters 不匹配 → 静默落回后端默认（100亿/30元/含ST≈aipick 小票池）→ 改为**复用管理员全局默认**（1000亿/300元/剔ST），与首页左视图完全一致
  - **大V资讯页**（`3baf0f8`）：群总结按日期 + 4 时段展示、可回看往日（后端 summary API + 前端 SummaryNewsView）
  - **群总结 PDF 上传/预览/下载**（`2565f54`+`ae08957`+`51df786`）：在线预览服务 + 手机逐页图片适配 + 下载 PDF 按钮
  - **自动化测试**：全量 **420 passed**（新增量脉 15 用例 + system_batch 过滤 3 用例 + summary 用例）
- **v4.5 (09-01 下午~晚)**：线程守护 + 抢筹口径改版 + AI 预测重构 + 连板梯队 8 档 + 兜底加固
  - **生产线程爆炸根治**（`8d59c5d`）：东财全市场/昨比/K线填充三处由「每次请求新建 ThreadPoolExecutor + shutdown(wait=False)」改为**进程级常驻池**（`_EXECUTOR_CLIST`/`_EXECUTOR_YDAY`/`_EXECUTOR_FILL`，8+8+6 有界）——整体超时后线程滞留后台跑网络超时只增不减（实测 2 worker×2082 线程内存耗尽+负载 19 拖死全站）→ 线程 **4140→13**、负载 19.3→1.4、全市场行情 246ms
  - **线程守护 thread_guard**（`6f50bd9`+`59126a4`）：新增 `scripts/thread_guard.py`，systemd timer **每分钟**查 kuaixuan/kx-worker 全部进程（cgroup.procs 精确取 PID）线程数，单进程>150 或总>300 且**连续 2 次超阈才告警**（去抖），冷却 600s 防刷屏 → 告警飞书（复用 NOTIFY_FEISHU_WEBHOOK 支持加签）+ **短信强提醒**（18883856602，阿里云号码认证通道，`THREAD_GUARD_PHONE` 可覆盖）；服务 down 也告警；兼容 CentOS 7 systemd 219 + 自编译 Python 缺 CA 用系统证书
  - **昨比熔断根治**（`af59984`）：短路条件含量脉——**四源全挂才短路**（三源 down 但量脉可用走量脉兜底，修 9/1 短路 1207 次 vs 量脉兜底仅 21 次量脉正常时昨比全空）；熔断**指数退避** 60→120→240→480→600s（确定性故障不再空转探测刷 3316 条日志）；**抖动保护**（ths/tencent 连续 2 次失败才熔断、量脉 3 次防限流误伤）
  - **左视图抢筹口径改版**（`bc3dd73`）：新增 `get_qiangchou_codes()` 合并右视图竞价抢筹三表（list20 竞额强度/list20Chg 9:20→9:25 涨幅/listLast 最后一秒段）代码集（30s 缓存）→ **命中集合才打抢筹标**，集合为空/未传回退旧公式（涨幅≥2% 且 竞/昨≥20%）兜底；scorer/stocks/auto_apply/system_batch 四处传参；实测右视图并集 107 只 vs 左视图抢筹 5 只，左有右无=0 口径一致
  - **抢筹标记显示链路修复**（`d43259b`）：batch_stocks 表无 qiangchou 列 → 落库丢弃打标结论/读取不返回/前端不映射（四环全缺）→ database.py 老库迁移 ALTER 加列 + history.py 落库读回 + stocks.js 映射（9:30 后锁定名单回看可显示 🔥）；filter 行情缓存加 **TTL 30s**（原无过期时间，坏缓存污染整个下午）；腾讯兜底单批失败**重试 1 次** + 仍失败打缺票 error 告警（9/1 曾静默丢 8 只）
  - **AI 预测入口重构**（`92fb629`+`b5c003f`+`91ef526`）：左视图**盘中选股替换为 AI 预测**（embedded 嵌入，自带 VIP 门禁/日期回看/规则过滤/实时涨幅）；导航栏去掉 AI 预测独立入口；完整报告按钮移除；左视图 Tab 切换位置漂移修复（justify-content 覆盖 + margin-left:auto）
  - **AI 预测历史回看入导航**（`b1badf5`）：首页左视图 AI 预测只展示最新报告（`:show-date-picker=false`）；「历史回看」页新增第三个 tab「AI 预测」（全宽嵌入 AipickView，切 tab 才挂载加载，切走卸载停定时器）
  - **AI 预测表格改版**（`43efcb4`+`288ce1d`+`0903f5a`+`717f423`+`c31a301`）：去序号/代码列（代码并入名称下方两行结构）、概念列限宽 110px 单行省略、表头 sticky+blur 背景防数据透出、**操作列加自选**（usePoolStore.addStocks）、名称列点击弹**分时/日K/周K/月K** 图（复用全局事件委托+StockChartModal）、筛选输入框对齐竞价选股紧凑风
  - **连板天梯 8 档 + 断板反包区**（`e28769c`+`50f8cb5`）：开盘啦 pid 只到 5 → 按东财真实 limitUpDays 重分 **1~8 档**（`rebin_ladder`，≥8 归「八板+」，东财缺失保持 pid 档位不增删股）；天梯图 8 档阶梯条+chips（空档画短条暴露断层）、连板池只渲染非空档、summary chips 两行；梯队表格 tab 同步扩 8 档（第 5 档 label 动态「五板/五板+」兼容远古历史）；新增**断板反包区**（`fetch_fanbao_stocks`：今日涨停池 limitUpDays==1 + 昨日不在涨停池 + 近 5 日曾涨停，东财 flash 历史查询 5 次 30min 缓存，暖橙配色与连板红区分）
  - **腾讯兜底 f4/f5 修复**（`e9b4940`）：9/25 东财 clist 反复熔断（09:20-09:34）→ system_batch 走腾讯兜底，原映射缺 **f4 昨收/f5 成交量** → `is_suspended`（f4≤0 或 f5==0 判停牌）把 5545 只全误判停牌 → 过滤 0 只、system batch 为空 → 补 pre_close/f4/f5 字段 + 回归用例防未来误改
  - **UI 优化**（`778eacc`）：竞价选股去「竞额」「竞/昨」两列（13→11 列）；竞价异动 8 处竞换/竞价换手 `toFixed(4)→toFixed(2)`
  - **自动化测试**：全量 **453 passed / 4 skipped**（新增 thread/熔断退避/抢筹打标/落库回读/腾讯重试/缺票告警/filter TTL/腾讯 f4/f5 回归/rebin 3 用例/fanbao 渲染等）
- **aipick 模型脚本入库**（`5033666`）：审计发现核心脚本脱管（git 只有 8/27 旧快照且已分叉，5 个脚本从未入库）→ 拉线上最新版（predict_daily 376 行/collector/backfill/backtest/db/run/train_model + README）放入 `scripts/aipick/`，删除过期快照 `prod_predict_daily.py`/`prod_collector.py`/`_aipick_predict_daily.py`（8/31 涨停率过滤等改动已随本次入库留痕）
- **v4.6 (09-02~09-03)**：昨比异步化 + 9:05 预热 + lock 当日幂等 + 同参防刷 + 竞额定格 + 告警降噪（9/2 生产事故链完整闭环）
  - **昨比拉取异步化**（`ae7482f`，9/2 生产事故核心修复）：高并发选股下昨比 8 并发持续打爆东财/同花顺 K 线（2h 失败 3 万次、每请求重复拉 3950 只 → /api/stocks 20-43s 全站卡顿）→ `fetch_yesterday_amounts` 默认 **wait=False 异步**：有 need 时后台线程拉取、当前请求立即返回现有缓存（昨比部分缺失评分容忍）——**请求路径永不因昨比卡顿**；wait=True 保留给 auto_apply/system_batch 等后台任务（保证锁定名单昨比完整）；**失败也写当日缓存**（`YESTERDAY_RETRY_TTL=600s` 窗口内不重复拉，东财限流期 10 分钟才一次全量重试）；**批锁 `_yday_batch_lock`** 防并发同时进 need 判定各自全量拉（14s 内 5 次全量 5000 只打爆源）；超时后 **cancel 队列中未运行任务**（立即释放线程给后续拉取，原实现超时后任务滞留线程池队列拖死全站）；并发 8→4（`YESTERDAY_FETCH_WORKERS`）、整体超时 20→12s
  - **昨比 9:05 预热**（`8221ab9`）：新增 `services/yday_prewarm.py` —— 交易日 **9:05**(±20min 窗口) web 进程后台分批（200 只/批、wait=True）预热全市场昨比填缓存，最多 10 分钟（9:15 前收尾）；昨比缓存是 **web 进程级**（fetcher._yesterday_cache）必须挂 main.py startup，kx-worker 独立进程不共享；--workers 2 下两 worker 各自预热一次（早盘前压力小可接受）；东财限流期盘中不再冷拉全市场
  - **filter 60s 同参去重**（`9999231`）：`recent_same_filter` 规范化指纹（排除 markets 的 json + markets 排序拼接，兼容前端参数顺序差异）→ 同一用户 60s 内相同参数 filter 只落一条（防脚本/手滑反复点应用刷历史批次）；仅用户主动 filter，lock/system_batch/auto_apply 不受影响
  - **lock 当日幂等**（`8b1dc5e`，主人确认语义）：9:25 后 + 同筛选参数的手动 lock 自动直读当日批次（`find_today_lock_matching` gate=09:25:00，参数指纹同 filter 去重）→ **不再全量重拉行情/重复落库/重复推送**（解决每次重新登录都重算+堆 lock 历史）；9:25 前不幂等（竞价数据未定型，每次进入页面重算拿最新）；参数改过不幂等（正常重算落新批次）；幂等直读用 `get_batch_stocks_mapped` 映射回前端 list 结构（字段与 score_all_stocks 输出一致）；API 返回 `idempotent:true` 供前端识别
  - **前端 force 重锁**（`381fcd8`）：`fetchStocks(..., force)` 只在 action=lock 时传 force=1（9:30 前）→ 用户主动点「锁定」绕过当日幂等强制重算落新批次；自动 lock（页面加载）不带 force → 后端幂等直读；幂等命中 toast「✅ 已载入今日锁定名单」不误导
  - **竞额定格修复**（`45ad164`+`d8cd7c3`）：v4.5 误删「竞额」列恢复（万→亿折算 `bidAmtText`，≥1 亿显示 x.xx 亿）；东财封禁期全市场走腾讯兜底，腾讯无竞价额字段把**实时累计成交额**塞进 f616 近似 → 盘中「竞额」读到实时成交额失真 → 新增 `auction_snapshot.load_day_bid_amt`：从 snapshot_bid 读当日**9:25 定格竞价额**（时点优先级 9_25>9_24>9_20>9_15，越晚越接近定格），stocks.py/system_batch 评分时传入 process_all_stocks(day_bid_amt=...) 使 bidAmt/bidRatio 全天以定格竞价额为准
  - **腾讯缺票告警降噪**（`49f6e27`）：单批失败告警 WARNING 级 + 限频（防刷屏）
  - **自动化测试**：全量 **506 passed / 4 skipped**（新增 test_yesterday_cache 昨比失败缓存/批锁/异步路径、test_yday_prewarm 预热调度、test_filter_dedup 同参去重、test_lock_idempotent 当日幂等 305 行、test_bid_amt_fix 竞额定格、test_fetcher_miss_alert 告警降噪）
- **v4.7 (09-04)**：限流治理 + 首屏慢接口缓存优化（用户反馈"转圈/慢"，主人授权 RATE_LIMIT 200→400）
  - **限流旁路接线**（`5848b18`）：main.py 漏传 uid → 付费/VIP 的旁路此前形同虚设，全部用户都被限；接上后会员不再被 429
  - **限流 200→400 + 会员旁路**（`524d48a`）：普通用户 400/min、付费/VIP 直接旁路
  - **market-brief 缓存 30s + 两市概况跨进程**（`2e5eb86`）：1174ms → 6ms
  - **3points 缓存失效 bug / history 缓存 / auction-overview TTL / bid-seal 缓存**（`8e41226`）：956ms → 11ms（含 TTL 与 30s 轮询错峰、耗时主体必须进 loader、错误不写缓存）
  - **无批次用户 refresh 计算缓存 60s**（`fd238d1`）：3478ms → 82ms（价格仍由 spotMap 实时覆盖；参数指纹隔离）
  - **字体长缓存**（Nginx `/assets/` immutable、index.html no-store）：586 个 woff2 不再每次刷新全量重下
  - **自动化测试**：test_slow_api_cache_20260904.py（接口缓存命中/参数隔离/跨进程），全量 575 passed / 4 skipped（9/5 回退用例后为 575）
- **v4.8 (09-05)**：休市回退秒开 + 前端体验/五色收敛大改
  - **休市回退秒开**（`8ca56e4`，多用户反馈"关闭再打开首页转圈"）：`history.find_recent_reusable_batch` 14 天窗口回退最近交易日同参批次直读（lock→filter→auto 优先级、参数指纹不一致仍重算、空批次跳过、无手动批次才系统兜底）；API 响应新增 `reusedDate`；前端直接采用并 toast「已载入 X 的选股名单」→ 96ms vs 重算 2.6s（27 倍）
  - **usePolling 失败指数退避**（`f3a65d4`）：fn 可返回成败标志，连续失败间隔 ×2^n（上限 5min）；ensureTabData 失败保留旧数据 + 角落「稍后重试」+ 手动单 Tab/全局刷新按钮（静默 spinner 不遮表格）
  - **UI 微调**（`4772c7d`/`a704a94`/`e2600b4`/`a612691`）：去顶部提示文案、锁定按钮移除、刷新按钮下移并尺寸对齐、tab 放大、AI预测图标修复（FA5 fa-robot 在 FA4.7 不渲染 → fa-android）、竞价 tab 更名 **AI竞价** + 选中红色
  - **去排序箭头**（`7d5502d`）：全站表格 .sort-ind 隐藏（保留排序功能，active 列头高亮反馈）
  - **五色收敛**（`8434d03`+`5584624`）：用户反馈颜色过多 → 全站收敛 黑/白/红/绿/黄；清除蓝紫杂色 ~150 处（邀请页/按钮/徽章/蓝灰次要文字）；白主题重定义 --accent 深红 #c62828 保证对比度；三时点榜 9:15 列蓝→银白、跌色统一绿；图表内部（K 线均线/轮动图）豁免
  - **AGENTS.md 工作手册**（`67a8c69`）：可公开约定迁移进仓库（工作流/部署/缓存/前端约束/数据源/测试坑，脱敏）
  - **自动化测试**：test_stocks_refresh_fallback.py 8 用例，全量 **575 passed / 4 skipped**
- **v4.9 (09-06~09-09)**：picker 五段重构落地 + 9/9 事故链闭环 + 竞价语义正本清源
  - **picker 模块重构 P1~P5**（`d44b03c`→`4df47c0`）：拆成 `mode / contract / sources / score / policy` 五层——数据源适配层（4 个 adapter 统一输出 `QuoteRow`）、评分/过滤层、编排层（新老双跑灰度，老代码零改动）、**P5 切流**（首页选股默认走新链路）；配套修正收盘后语义（竞价结束后名单即定型）与预热补强
  - **评分因子语义修正**（`af79676`/`0fc8017`/`cfab537`/`c971986`/`f940375`）：昨日涨幅改用**真实值**（老逻辑拿当日 f3 冒充）；竞价换手改「竞价额 ÷ 流通市值」口径（不依赖快照没有的 bid_vol）；**竞价强度三层信号**替代已失活的 f630 异动等级；**竞价大跌票不再入选**（中石科技 300684 事故）；抢筹列区分竞额/涨幅/末秒并显示幅度
  - **9/9 生产事故：SQLite 连接泄漏打满 fd**（`cdeecdc`）：`with sqlite3.connect()` 只管事务不关连接 → 高频缓存路径 fd 累积到 997/1024 → 10 分钟内 10.2 万次 `unable to open database file`，批次全落库失败，表征极像"数据源故障"；修法：`get_conn()` 加 `timeout=10`、读连接 `close()` 进 `finally`、systemd `LimitNOFILE=65535`
  - **策略 / 时段消歧**（`e8d9df0`，主人拍板改名）：对外**策略** `strategy` = `auction`(竞价因子表) / `spot`(盘中因子表)，HTTP 参数 + `get_scoring_cfg(strategy=)` + 前端 store；内部**时段** `PickMode` = preopen/auction/locked/intraday/closed 由 `resolve_mode()` 按时间判定（午休与盘中都是 intraday）。旧名 `mode` 保留回退、出参双返防旧前端静默退化
  - **下线盘中实时选股 spot**（`4c56083`）：整链零调用、前端无入口
  - **竞价窗口语义修复 + 删双轨**（`867cd25`）：竞价期 `vol==0` 是"未撮合"不是停牌（`is_suspended` 窗口内只看昨收）、腾讯源窗口内才取 f615/f616 且窗口外清空（此前拿现价涨幅冒充竞价涨幅，9/7 全员 0 只）；AUCTION 名单源从单点 `[0]` 补成**真序列** `list_source_count=2`；删掉 api/stocks、auto_apply 的双轨回退与 parity.compare 死代码
  - **竞价额单位 bug**（`1470e44`）：KPL `bidAmt` 是**元**、`get_bid_amt()` 返回**万元**，补位处漏换算 → 快照竞价额放大 1e4 倍，三处统一 `/1e4`
  - **出口 IP 轮换下沉**（`54e6f60`）：轮换机制此前只有 fetcher 独享，kpl/hot_rank/sector_rotation 裸 `urlopen` 单 IP 裸奔 → 抽到 `app/core/net.py`（`IPRotator` 严格 RR + 失败惩罚、`ip_binding()` 上下文仅 with 块内绑定、`http_get`），覆盖全部对外源
  - **自动化测试**：新增 test_auction_window 12 例、test_snapshot_kpl_unit 2 例、test_outbound_ip 13 例
- **v4.10 (09-10)**：抢筹接口 504 止血 + 评分门槛 scoreFloor
  - **504 止血**（`0747561`，生产实况 08:57 `/api/kpl/bid-qiangcang?date=历史日` 单次 183~198s → nginx 60s 超时 504，两个 worker 被占满连累 `/api/stocks` 一起 504）：① `apply_board_concept` 新增 `time_budget=3.0`，逐股外网查询总耗时超预算即停（未查到的保留原值，下轮 1h 共享池命中自动补齐），传 0 = 不限留给离线回补；② 触发条件 `date or 竞价时段` 收紧为**仅竞价时段**——概念是静态属性，不随回看日期变化，盘后回看历史日不该付逐股查询代价。**实测 198s → 0.42s**
  - **评分门槛 scoreFloor = 80**（`9d290e4`，主人拍板全站默认、可配）：与既有 `probLt/confLt` **双低**剔除（高信心能救低概率）不同，`scoreFloor` 是**单阈值硬门槛**，不看信心，评分不够就是不够；只能放精筛（粗筛在评分前跑，拿不到 probability）。后端 scorer/filter/lock/admin 四处 + 前端 filters.js/FilterPanel/AdminView 同口径。**Felix518 原条件 14 只 → 6 只（评分 81~88，全部 ≥80）**
  - **自动化测试**：新增 test_score_floor 6 例、test_concept_budget 2 例；修 test_ip_rotator 导入失效（轮换下沉后遗留，曾阻断全量收集）
  - **全站无法登录 38 分钟 · 雪崩根因修复**（`30f224a` + `246f85b`，P0+P1）
    - **现象**：09:30 竞价结束集中刷新后，`/api/login` 排队 **2,321,182ms（38.7 分钟）**，连 `/api/prefs`、`/api/kpl/bid-seal` 同款；`database is locked` 1120 次。服务进程好好的、load 只有 0.20 —— 是**登录被慢请求挤在线程池里排队**，不是宕机
    - **根因 1 · 熔断形同虚设（横跳）**：全市场分页 30 页并发，每页各自 `_record()`。失败页刚设上 `down_since`，同批成功页紧接着清零 → 日志里「数据源故障…冷却60秒」与「恢复（故障**0秒**）」成对出现 66/65 次；下次 `_check_circuit()` 永远返回 False，于是完整重试 30 页 × 单页 15s 超时
    - **根因 2 · 分页无失败率判定**：整体超时 40s，故障态 30 页 8 并发最坏 ~56s；超时后剩余 future 不取消，线程滞留继续打外网。2 worker × 40 槽 = 80 个槽被占满 → 任何同步 def 路由（含登录）只能排队
    - **P0 修复**：① 新增 `_circuit_open()` 熔断"生效期"概念——冷却期内到达的成功只更新统计，**不解除熔断、不重置退避**，必须冷却结束后的半开探测成功才恢复；② 分页**整批只记一次**采样（原 30 个采样点各自判定，`down_threshold=1` 的源会被任一页抖动误熔断）；③ 快速失败：连续 5 页失败且无一成功 / 已完成 ≥8 页且失败过半 / 整体超时 12s（原 40s），触发即 `cancel` 剩余排队页并抛异常交给腾讯兜底。阈值依据：生产实测正常态 30 页并发 **0.4s（30/30 成功）**，12s 有 30x 余量
    - **P1 修复**：`api_login` / `api_verify_email` / `api_resend_verify` 改 `async def` + `await asyncio.to_thread(...)`，落到 event loop 默认 executor，**与 anyio 池物理隔离**（⚠️ 不能用 `run_in_threadpool`，那个正是会被占满的 anyio 池）；`/api/health` 探活同样改 async（同步时被堵会 504，让监控误判"服务挂了"）；anyio 线程池 40 → **120 槽**/worker
    - **踩坑（已写进代码注释）**：扩容必须在 **async 上下文**调用（模块导入期调 `current_default_thread_limiter()` 会抛 `Not currently running on any asynchronous event loop`，被 try 吞掉后静默不生效）；startup handler 按注册顺序执行，必须排在 `on_startup` 之后，否则 `setup_logging()` 还没跑、日志打不出来无从确认
    - **验证**：生产 venv 实测把 anyio 池 40/40 槽占满（`available_tokens=0`）后 `asyncio.to_thread` 仍 **0.001s** 返回；生产日志两 worker 均「anyio 线程池扩容: 40 → 120 槽/worker」；`/api/stocks` 26~36s → **0.033s**，历史回看 0.299s，登录 0.062s
    - **自动化测试**：新增 test_circuit_fastfail 8 例；`git stash` 回退改动后 4 条核心用例立刻变红（证明非空断言）
  - **批次落库全失败 · warn_type NOT NULL**（`94808ad`）：一个 `None` 值级联成全站故障——`item["warnType"]` 为 None（东财 f630 缺失，contract 语义上保留 None=未知）→ `executemany` 抛 `NOT NULL constraint failed: batch_stocks.warn_type` → **整个批次一条都存不下**；更糟的是 `except` 分支只 log 不关连接 → 连接泄漏持续持有写锁 → 后续任何写操作 `database is locked` 雪崩。修法：`_NOT_NULL_DEFAULTS` + `_safe_num()` 对 9 个 NOT NULL 无默认值列统一兜底（语义取舍：**"名单能落库"优先于"字段精确"**），`conn.close()` 移入 `finally`。**test_lock_idempotent 5 failed → 14 passed，耗时 46.9s → 5.67s（锁等待消失）**
  - **9_20/9_24 竞价额长期崩塌 · 单位防御 + 缺失质量门**（`1032b77`）
    - **实测规律**：9_25 定格稳定 **92-93%**，而 9_20/9_24 长期崩塌——9/4 与 9/7 为 **0%**，9/9 为 1.4%，9/10 为 0.7%（9/8 唯一正常 88.9%）。根因是竞价窗口内东财被限流 → 回退腾讯兜底，而腾讯无真实竞价额
    - **两个后果**：① `INSERT OR REPLACE` 把窗口内上一轮重采集到的好数据直接冲成 0；② KPL 补位曾按"元"写入万元列 → 放大 1e4 倍（9/9 9_20 中位 5,775,000"万元"、9/9 9_24 max 341,698,188），带歪评分与加速度
    - **修复**：`_fix_bid_amt_unit()` 单票 >10 亿判定单位错（实测 9_25 P99=4452 万、max=5.19 亿），`/1e4` 换算，换算后仍离谱（历史 4.9e15）→ 置 0 宁缺毋滥；`_guard_bid_amt_missing()` 全市场 ≥1000 只且竞价额覆盖率<30% 且涨幅覆盖率>50% → 判定源降级，捞回库内已有正值填回；`check_seal_quality` 纳入竞价额覆盖率进飞书告警
    - **历史数据**：生产修复 **901 行**（先备份到 `snapshot_bid_bak_20260910` 再改），9/9 9_20 max 6837 万、9/9 9_24 max 3.4 亿、9/8 9_24 max 1.7 亿，全库无残留
    - **影响面更正**：名单源固定 9_25（`load_snapshot_full`），回退是"换交易日"不是"换时点"，所以 **9_20 崩不影响名单**，只影响加速度与竞价额展示——此前"名单会塌成个位数"的判断有误
    - **自动化测试**：新增 test_snapshot_bid_amt_guard 7 例（单位换算/置0/不误伤正常值/质量门 4 种分支）；全量对照 **新增失败 0 条、净修复 6 条**
- **v4.11 (09-10 晚)**：**去兜底重构** —— 多源兜底链全部下线，改「单一东财 + 静态数据落库」
  - **主人拍板动机**：「各种兜底改来改去相互影响，导致各种 bug」。根因两条：① 兜底源**没有 f615/f616/f617 竞价字段**，只能用现价涨幅/成交额近似填充 → 竞价时段一旦切兜底，竞价数据即为编造值（用户反馈"选出来不对"）；② 熔断打开即整段冷却期"不敢试"，流量全推兜底源 → 主源/兜底源**横跳**，同一只票两次请求口径不同
  - **数据源侧**：全市场行情 `_fetch_market_with_fallback`/`_fetch_market_all_with_fallback` 改为**东财直通**（`ensure_spot_cache` 失败只沿用本地旧缓存，无则抛出）；`fetch_stock_chart_robust` 的 `sources` 砍到只剩 `["eastmoney"]`（原 东财→腾讯→tushare→ths→kpl→自聚合 下线）；昨比（昨日成交额）四级兜底链（东财→同花顺→腾讯→量脉）下线
  - **昨比改「收盘落库 + 全天读库」**（新机制）：每交易日 **15:10** `yday_prewarm(stage="close")` 批量拉全市场写 `yday_amount` 表（按 code 覆盖写，`INSERT OR REPLACE` 兼容 CentOS 7 SQLite 3.7），此后全天 `_yday_hydrate_from_db` **直接读库（零网络零兜底）**；只有库里没有的（新股/停牌/任务未跑）才实时拉东财日 K。**盘中预热(stage="open")不落库**（那时 T=前一交易日，写库会污染语义）。读库对 `tdate` 超 5 天（长假/停更）与 `amount` 为空的行做过滤，读库异常自动回落实时源
  - **熔断短路收窄**：昨比/昨涨的短路条件从「四源全熔断」收窄为「**东财日 K 单源熔断**」；`down_threshold` 去掉量脉。配合 v4.10 `30f224a` 的「生效期内成功不解除熔断」语义，避免 30 页并发下横跳
  - **量脉代码整体删除**：`services/liangmai.py`、`services/liangmai_check.py` 及其测试、worker 调度、`config.LIANGMAI_TOKEN`、conftest 的量脉缓存清理夹具一并删除。**腾讯保留但降级为功能源**（picker 竞价窗口名单源 `picker/sources/tencent.py` + 点查补丁源），不再是兜底
  - **代理方案结论**（同日实测，未采用）：东财 push2his 是**接口级全局时段性风控**，与出口 IP 无关——公司网/阿里云测试机/生产机/代理家宽**同一时刻全部 0~10%**，而同机 push2dycalc 100% 通；多米（节点可用率 ~45%，还把主力源拖到 62%）与快代理（主力源 100% 但 K 线仍 25%）两家独立测出同一结论 → **买代理解决不了，别买**。主力源 push2dycalc 实测盘中 11-14 时成功率 99-100%，本就健康，套代理只引入新的单点风险
  - **代码缺陷顺手修**：`fetch_yesterday_amounts` 短路后 `need=[]` 仍会走 `if wait:` 加锁重判 / `else` 起空线程 → 改为 `if need and _check_circuit(...)` 前置，短路即跳过整段
  - **测试**：新增 `test_yday_amount_db.py` 11 例（落库/读库往返、覆盖写、过期与空值过滤、hydrate 命中零网络与不覆盖实时缓存、读库异常回落、**只有 stage=close 落库**）；conftest 新增 `_isolate_fetcher_globals` 夹具（快照/还原 `_HEALTH`+`_broken_hosts`）消除"单文件绿、全量红"的顺序耦合；删除随功能下线的用例 3 条（K 线腾讯降级、东财+腾讯双源链、腾讯 qfqday 昨比），还原被 conftest session 桩整体替换的 11 条真实实现用例。**对照 HEAD 基线 27 红，改后 11 红（全部为基线既有历史债，新增失败 0）**
- **v4.11.1 (09-10 深夜) 二审修订：去兜底 ≠ 删同语义真实源（K 线回归修复）**
  - **触发**：一审把「K 线腾讯源」也一并删掉后，测试机上 **K 线功能整体瘫痪** ——
    `/api/stock/chart` 5/5 返回 502，日志 `chart[robust]全部数据源失败`，
    push2his 直连 000（接口级时段性风控窗口内）。而实测 chart 历史命中是
    **东财 27 / 腾讯 485 / ths 2**，即多源链长期由腾讯扛着，删掉等于删功能。
  - **判据（主人当场校准，写死进 AGENTS.md）**：
    - **必须删** —— 换源会**编造字段**的（腾讯无 f615/f616/f617 竞价字段，只能用现价涨幅/成交额
      近似填充 → 竞价时段切过去数据即为编造值）；
    - **可保留** —— 换源**不换数据**的**同语义真实源**（腾讯日/周/月 K 与 `qfqday` 成交额均为真实值，
      且经 `_validate_chart_data` + `_kline_amount_pair` 统一口径）。
  - **代码变更（仅 `app/services/fetcher.py`）**：
    - `fetch_stock_chart_robust`：`sources` 由 `["eastmoney"]` 恢复为 `["eastmoney", "tencent"]`；
      tushare/同花顺/kpl/自聚合 仍下线不变。
    - `_fetch_yesterday_amount_one`：东财日K 失败**或熔断**时改调新增的
      `_yday_fallback_tencent()`（腾讯 qfqday 第 9 列 = 真实成交额万元）；东财熔断不再"整只置空"。
    - `fetch_yesterday_amounts`：批级短路条件由「东财日K 单源 down」放宽为
      「东财日K **与** 腾讯K线**均** down」，否则风控期整批昨比恒空、落库机制空转。
    - 注：`_fetch_chart_from_tencent` **不进** `tencent_kline` 熔断统计（有意为之，
      避免 K 线失败连带掐掉昨比备源）。
  - **测试**：恢复 2 条随功能下线的用例（K 线东财→腾讯降级、腾讯 qfqday 自算涨跌幅）+
    新增 1 条「东财失败/熔断 → 切腾讯备源、双源皆挂才置空」；
    `test_short_circuit_skips_when_eastmoney_kline_down` 因短路口径按上述设计变更而重写为
    `test_short_circuit_requires_both_kline_sources_down`。**全量 935 例 / 10 失败 / 0 错误
    （HEAD 基线 27 红，零新增失败，剩余 10 条全为基线既有历史债）。**
  - **测试机实测（部署后）**：K 线 4/4 返回 **200**（600519/000001/300750/601127，各 200 根，
    最后一根为当日 2026-09-10），日志明确 `chart[robust]源=tencent` 接管；
    昨比 `_fetch_yesterday_amount_one('600519')` → `([242869.88, 416850.05], -0.45)`，
    耗时 0.25s（东财 fail=1 / 腾讯 ok=1）；`/api/health` `serviceable=true`；
    `/api/stocks?action=filter` 200 / 3.4s / 12 只；Traceback 计数 0。
- **v4.11.2 (09-10 深夜) 生产整包上线 + 收盘落库首次回填 + K线源内日志降噪**
  - **生产升级**（`922c312` v4.10 → `e930d1b`，只动 `backend/app` 5 改 + 2 删）：
    备份 → SFTP 上传 → md5 全一致 → **依赖齐套性预检**（`app/core/net.py` 在位、
    py_compile + `import app.main/app.worker` 整站导入 OK）→ 删量脉（liangmai×2 + pyc，
    生产无残留引用）→ 重启 `kuaixuan`/`kx-worker`（双 active）→ `yday_amount` 建表成功。
    **线上实测**：K线 3/3 → **HTTP 200**（600519/000001/300750 各 200 根，末根 09-10，
    东财 push2his 熔断 → **腾讯同语义备源接管**）；选股 `filter` 200 / 2.4s；
    `_fetch_yesterday_amount_one('600519')` → `([242869.88, 416850.05], -0.45)` **0.38s**；
    `/api/health` `serviceable=true`（`eastmoney_clist` ok / `eastmoney_kline` down /
    `tencent_kline` ok 63 次 —— 备源承压实证）；**Traceback 计数 0**。
  - **收盘落库首次回填（生产 + 测试机）**：手动触发一次
    `yday_prewarm._prewarm_once(stage="close")` → 两台均 **`yday_amount` 0 → 5550 行**
    （`tdate=20260910`，178s）。**这一步本身就是二审修复的最强证据**：日志里东财日 K
    **整轮熔断**（`eastmoney_kline 调用失败, 进入异常状态`），全靠 `_yday_fallback_tencent`
    把 5550 只全部拉回；没有备源，这次回填会写 **0 行**、机制空转（即"东财单源 = 收盘
    落库永远填不上"）。此机制从此进入**稳态**：每交易日 15:10 落库，此后全天零网络读库。
  - **顺手修正（本次改动引入的可观测性副作用）**：`_fetch_chart_from_eastmoney` 末尾
    `log.warning("K线拉取失败 … (东财全HOST熔断)")` → **降为 `log.debug`**。原因：恢复腾讯
    备源后，东财风控期失败是**已预期的常态**，原措辞会**误导运维**（以为 K 线整体挂了，
    实际腾讯已接管），且每只票一条 WARNING —— 生产实测 23:31 一分钟 12 条。
    外层已有权威表述：成功 `chart[robust]源=xxx`（INFO）/ 全源失败
    `chart[robust]全部数据源失败`（ERROR）。**腾讯源内失败日志保留 WARNING**（它是最后一源，
    失败即整体失败，且带 `err=` 有诊断价值）—— 差异化依据是"常态 ≠ 异常"。
  - **双跑灰度对拍差异排查**（部署日志里发现 `老12只/新4只, 仅老8`）：拉近 7 天全量
    `切流对拍差异` 日志比对 —— **部署前 23:22 就已是同形态**（`老7/新2`、`老6/新2`），
    部署后 23:29 为 `老12/新4`；7 天内 `仅新非空`（新链路**多选**）448 条，说明差异是
    **双向且长期存在**的 picker 灰度现象；危险形态 `新=0只`（新链路全空）**0 次**。
    → 结论：**非本次去兜底引入**，继续按灰度观察。
  - **测试**：全量 **935 例 / 10 失败 / 0 错误**（HEAD 基线 27 红 → 零新增失败，
    剩余 10 条全为基线历史债：history×3 / slow_api_cache×2 / auction_snap_pool_offhours×2 /
    snapshot_915×1 / auto_apply×1 / stats_api×1）。日志降级改动**无测试依赖**（grep 确认）。
- **v4.11.3 (09-11) 老链路彻底退役（主人拍板 C 方案：实现 + 用例全删）**
  - **背景**：picker 重构自 9/7 双轨灰度，观察期已满（生产日志显示差异是**双向长期存在**的
    picker 语义差异，危险形态 `新=0只` 7 天内 **0 次**）。主人拍板：**不再保留回滚保险**，
    老链路连同其对拍/灰度开关一起删除，回滚只靠 git 回版本。
  - **判据**（主人 09-10 原话）：**"不要为了让测试用例通过而去瞎改，要看是不是我让你去掉的
    功能所对应的测试用例，该删的还是要删"** —— 分类依据是「被测函数在生产代码还有没有引用」，
    不是"红了就删"。落在业务语义上的用例（accel / 竞昨比 / 昨日涨幅因子 / ST 过滤 / 抢筹打标 /
    腾讯兜底契约）**必须迁到新链路，不能顺手删掉防线**。
  - **生产代码删除**（净 -2090 行 / +396 行，43 文件）：
    - `scorer.py`：`compute_score` / `score_all_stocks` / `apply_filters` / `process_all_stocks` /
      `is_suspended` / `is_qiangchou` / `_qc_fields` / `get_entity_change` / `get_bid_turnover` /
      `get_warn_type` / `get_factor_score` / `_factor_default` / `js_round` / `is_first_board`
      + 常量 `_QC_LABEL`/`_QC_UNIT`。**保留**配置与共享工具：`get_scoring_cfg` / `reload_scoring_cfg` /
      `validate_filters` / `parse_float` / `in_auction_window` / `bj_now` / `market_fs` /
      `get_bid_change` / `get_bid_amt` / `is_st` / `is_yizi` / `limit_pct` / `_in_markets` /
      `_q_date` / `_opt_float` / `_clamp` / `_truthy`（stats / auction_snapshot / auto_apply / kpl /
      picker/filter 仍在用）。
    - `picker/parity.py` **整文件删除**；`picker/lock.py` 删 `compare_with_legacy` / `enabled` /
      `enabled_default_on`；`api/stocks.py` 删 `_load_strengths` / `_gray_enabled` / `_parity_reverse`
      + parity 导入 + 调用点；`system_batch.py` 删 `_run_legacy` / `_picker_lock_on` / `_load_strengths`，
      `_do_run` 内联为**恒走 picker**，`_run_new` 更名 `_run_picker`。
    - `scorer.get_scoring_cfg` 的 `strategy` 参数保留（spot 策略 9/09 已下线，仅剩 auction）。
  - **测试重构**（用例 935 → **874**，净 -61）：删整文件 `test_picker_parity.py` / `test_picker_gray.py` /
    `test_bid_amt_fix.py` / `test_bid_amt_offwindow_no_fallback.py` / `test_bid_chg_offwindow_uses_day_snapshot.py` /
    `test_zt_pool_filter.py` + 4 个诊断脚本（`_diag_chain_e2e` / `_diag_score_all` / `_diag_real_filters` /
    `_diagnose_tencent_data`）；`test_phase1.py` AST 删 13 条、`test_picker_lock.py` 删 4 条并把
    `test_system_batch_switches_by_setting` 重写为 `test_system_batch_always_uses_picker`
    （断言 `not hasattr(sb,"_run_legacy")` / `not hasattr(sb,"_picker_lock_on")`）。
  - **业务语义用例迁移**（防线不丢）：accel ×4 + 竞昨比 ×2 → `test_picker_pipeline.py`；
    昨日涨幅因子 ×3 → `picker.score.compute_score`；ADMIN 评分配置生效 → `QuoteRow.from_eastmoney`
    + `compute_score`；抢筹打标 ×3 → `pipeline._qc_of`（`formula_fallback_flagged` 反转为
    `test_qc_of_no_source_never_fabricates`，锁"无源不得捏造"）；腾讯兜底实体/停牌 →
    契约层 property（缺今开 `is None`、缺 f4/f5 `is_suspended is None`）；ST 过滤 → `picker.filter`。
  - **顺手修正的「假绿」桩**（原桩打在老链路 → 新链路下永不触发，用例表面绿实为空跑）：
    `test_auto_apply.py`（改打 `plock.run_lock`）、`test_slow_api_cache_20260904.py` 与
    `test_stocks_refresh_fallback.py`（改打 `pl.run`）——修完基线里 7 条红**自然转绿**
    （auto_apply×4 / slow_api_cache×2 / sqlite cache ttl×1）。
  - **文件级失效引用清理**：`picker/{__init__,pipeline,filter,score,score_factors,lock}.py` +
    `auction_snapshot` / `auto_apply` / `fetcher` / `history` / `conftest` 里"见 parity.compare"、
    "test_picker_parity 有对拍用例"、"选股**新**链路"等表述统一改写；`picker/__init__.py`
    模块说明由四层改五层（删 parity、加 lock）并补退役历史段。
  - **测试**：全量 **874 例 / 7 失败 / 0 错误 / 4 跳过**（基线 `935 / 14 / 0 / 4`）
    → **零新增失败**；剩余 7 条全为基线既有历史债（auction_snap_pool_offhours×2 =
    scoreFloor 真缺陷、history×3 = scoreFloor=80 砍 mock 300003、kpl 单位口径×1、
    stats_api 跨零点×1）。
  - **AGENTS.md 同步**：第七节新增「唯一链路 = `picker.pipeline.run()`」硬约定 —— 改选股只改
    `backend/app/services/picker/`、回滚只能 git 回版本、**测试必须打桩在唯一链路上**
    （打在 `scorer.process_all_stocks` 上的桩永远不被调用）。
  - **回滚点**：tag **`v4.11.2`** → `36ce505`（删除前最后提交），已推远端。线上已无
    `picker_lock` / `picker_gray` 开关，回滚命令：
    `cd /opt/kuaixuan && git fetch --all --tags && git checkout v4.11.2 -- backend/app && systemctl restart kuaixuan kx-worker`。
  - **双机部署（09-11 凌晨，非交易时段窗口）**：
    - **测试机 01:35 / 生产机 01:46**，流程统一：备份 tarball → 上传 13 文件 →
      **MD5 13/13 逐一校验** → 删 `parity.py`(+pyc) → **依赖齐套性预检**(重启前) → 重启双服务 → 全链路验证。
    - **预检内容**（正反双向 assert，不过就不重启）：`scorer` 14 个老符号必须 `hasattr == False`、
      `system_batch._run_legacy/_picker_lock_on == False`、`_run_picker/lock.run_lock/pipeline.run == True`，
      并核 **运行时字节码** `_do_run.__code__.co_names` 只含 `_run_picker`。
    - **部署前只读审计的意义**：先比生产 13 文件 MD5 vs 本地 `36ce505` / `HEAD`，确认
      「13/13 全部 == 删除前版本」才动手 → 本次是干净增量，无跨版本错配风险。
      审计同时暴露出两个此前不知道的事实：**生产 `parity.py` 确实存在（12753B）**、
      **生产 `settings.picker_gray = 1`（即双跑对拍一直在生产真跑）**，而 `picker_lock = None`
      说明老链路在生产**从未被执行**（默认走新链路）——这就是"删老链路零行为影响"的直接证据。
    - **验证结果（双机一致）**：`kuaixuan`/`kx-worker` **active**；
      `/api/login` 200（生产 `Felix518` is_admin=1 member_level=2）；`/api/stocks?action=filter`
      **200 / 0.59~0.69s / 4 只**；`/api/health` `overall=ok serviceable=true`；
      **Traceback / ImportError / ModuleNotFoundError 计数 = 0**；parity 残留 **NONE**。
    - **两机选出同一份名单**（`603162 海通发展 89 / 600121 郑州煤电 82 / 000759 中百集团 81 /
      002172 澳洋健康 81`，`bidChange / bidRatio / warnType` 全有值）—— 同一份数据下两机结果
      完全一致，是"链路确定性 + 部署正确"的交叉证据。
    - 前端 `dist` 未动（本次无前端改动），生产按 `umask=027` 对 13 个文件显式 `chmod 640`。
    - 备份留存：测试机 `/opt/kuaixuan/backup/app_bak_20260911-013545.tar.gz`（1.7M）、
      生产机 `app_bak_20260911-014628.tar.gz`（3.9M）。
  - **⚠️ 本次部署后发现的待办**：`backend/app/worker.py:74` 启动日志仍为
    `"...连板天梯盘后生成 + 量脉校验已启动"`，但量脉模块 v4.11 已整体删除 —— 纯文案、零行为影响，
    属"误导性可观测性"（运维 grep 量脉会以为还在跑）。双机日志均可见此串。
    → **已于 v4.11.4 修复**（见下）。

- **v4.11.4 (09-11) worker 启动日志去除已删模块残留（配套小补丁）**
  - **问题**：`app/worker.py:74` 启动日志 `"快照采集 + 尾盘推送 + AI竞价选股调度 + 盘中概念刷新 +
    连板天梯盘后生成 + 量脉校验已启动"` —— **量脉模块 v4.11 已整体删除**，但双机日志仍宣称
    "量脉校验已启动"，属误导性可观测性。
  - **改动（一行，但非纯删词）**：`+ 量脉校验已启动` → `+ 股性数据落库已启动`。
    原串还**漏列了 `stock_temper.start_scheduler()`**（交易日 15:30 盘后落库涨停/炸板，股性数据源），
    顺手补齐 → 日志与实际 6 个调度器对齐（`auction_snapshot` / `wpqc_push` / `aipick_scheduler` /
    `concept_refresh` / `ladder_daily` / `stock_temper`）。
  - **双机部署**：测试机 `01:58:34` / 生产机 `01:58:42` —— 备份 `worker_py_bak_<ts>` → 上传 →
    **MD5 一致** → 预检 `PRECHECK-OK`（断言源码含新文案、不含"量脉"）→ 重启双服务。
    - 验证：`active / active`；启动日志 **新文案命中 1 / "量脉" 残留 0**；
      `/api/stocks?action=filter` **200 / 4 只**，**两机名单完全一致**
      （`603162 海通发展 probability=89 confidence=90` / `600121 郑州煤电` / `000759 中百集团` /
      `002172 澳洋健康`）；`/api/health overall=ok serviceable=true`；
      **Traceback / ImportError / ERROR 计数 = 0**。
    - 回滚点不变（tag `v4.11.2` → `36ce505`），本补丁亦在该 tag 覆盖范围内。

- **v4.11.5 (09-11) 清理已删模块的注释残留（现行描述型）**
  - **背景**：v4.11 起「量脉」模块已整体删除、并已从 worker 启动日志摘除（v4.11.4），
    但仍有 **5 处"现行描述型"注释**在点名量脉 —— 运维/新人按注释理解现行源链会被误导。
    （带"已废弃/已删除/已下线"字样的**历史说明**按惯例保留，不计入。）
  - **改动（纯注释，零行为影响）**：
    | 位置 | 改前 | 改后 |
    |---|---|---|
    | `fetcher.py:231` | 抖动保护源清单 `(ths/tencent/量脉)` | `(ths/tencent)` |
    | `fetcher.py:337` | `东财/腾讯/量脉任一行都能判断昨日是否涨停` | `东财/腾讯任一行…` |
    | `picker/filter.py:12` `:55` | `腾讯/量脉行无 f103` | `腾讯行无 f103` |
    | `scorer.py:205` | `任何数据源(东财/腾讯/量脉)都生效` | `(东财/腾讯)` |
  - **✅ 一处事实纠正（重要）**：此前把 ths 与量脉并称"均已摘链"**不准确**。核实后：
    同花顺 **K 线兜底 `_fetch_kline_from_ths` 仍在生产被调用**（`fetcher.py:2601`），
    且 `_HEALTH["ths_kline"]` 条目（`down_threshold=2`）仍在 —— **只有"昨比源"那一路**
    随「去兜底」摘链（其唯一使用方 `_fetch_yesterday_amount_ths` 全仓 0 引用，成死函数）。
    → 故 `fetcher.py:231` **保留 ths**，只删确实已整体删除的量脉。
  - **双机部署**：测试机 `02:17:23` / 生产机 `02:17:33` —— 备份 `comments_bak_<ts>.tar.gz`
    （48K，3 文件）→ 上传 3 文件 → **MD5 3/3 一致** → 预检 `PRECHECK-OK` → 重启双服务。
    - 预检脚本额外做了**双向断言**：旧文案必须不存在 + `_fetch_kline_from_ths` 必须仍在
      （防止"顺手删过头"把在线的 ths K 线兜底一起清掉）。
    - 验证：`active / active`；`/api/stocks?action=filter` **200 / 4 只 / 0.56~0.62s**，
      **两机名单完全一致**；`/api/health overall=ok serviceable=true`；
      **Traceback / ImportError / ERROR 计数 = 0**。
    - 受影响用例 `test_circuit_breaker` / `test_health` / `test_tencent_fallback` /
      `test_picker_filter` → **64 passed**。
  - **遗留（已知、未动）**：`_fetch_yesterday_amount_ths`（死函数）+ `_HEALTH["ths_kline"]`
    条目仍保留 —— 后者被 `test_health.py` 断言依赖，删除属独立重构范围，未纳入本次注释清理。

- **v4.11.6 (09-11) 同花顺昨比死源清理（死函数 + `_HEALTH` 死条目，6 源→5 源）**
  - **判据**：仍沿用主人拍板的原话——「不要为了让测试用例通过而去瞎改，要看是不是我让你
    去掉的功能所对应的测试用例」。即先查「被测/被删对象在生产代码还有没有引用」。
  - **背景**：v4.11.5 报告遗留时确认 `_fetch_yesterday_amount_ths` 全仓 0 引用（死函数），
    它是**唯一**写 `_record("ths_kline")` 的生产者 → `_HEALTH["ths_kline"]` 是**死源**
    （从未有过计数），且被 `test_health.py` 断言依赖，故上一轮未动、单独收口。
  - **生产代码删除**（`backend/app/services/fetcher.py`，净 -50 行）：
    - `_fetch_yesterday_amount_ths`（41 行）—— 同花顺日 K 昨比源。
    - `_HEALTH["ths_kline"]` 条目 → **`_HEALTH` 由 6 源收敛为 5 源**：
      `eastmoney_clist` / `eastmoney_kline` / `eastmoney_zt_pool` / `tencent_market` / `tencent_kline`。
    - 注释同步：`down_threshold` 说明 `(ths/tencent=2)` → `(tencent=2)`；`_record` docstring
      抖动保护源清单；`_fetch_yesterday_amount_one` 备源说明；`_kline_amount_pair` docstring
      注明 `close_idx=4` 列序配置**已无生产调用者**（保留仅为记录列序语义与历史踩坑）。
  - **核实后保留（防「顺手删过头」）**：`_fetch_kline_from_ths`（K 线兜底链的 `elif` 分支引用）、
    `_kline_amount_pair`（腾讯昨比备源仍在用）、`_yday_fallback_tencent` /
    `_fetch_yesterday_amount_tencent`（现行昨比备源）。
  - **测试调整**：
    - `test_health.py`：`'ths_kline' in d["sources"]` → 断言**已消失**（锁「已收敛为 5 源」，防死源回归）。
    - `test_tencent_fallback.py`：两处 `_HEALTH` 打桩里的 `ths_kline` 条目删除（结构与真实脱节）。
    - `test_circuit_breaker.py` / `test_yday_chg_consistency.py`：历史表述收敛到位。
    - 受影响用例 **86 passed**；**全量 874 例 / 7 失败 / 0 错误 → 零新增失败**（7 条全为基线历史债）。
  - **双机部署**：测试机 `02:35:23` / 生产机 `02:35:34` —— 备份 `ths_deadsource_bak_<ts>.tar.gz`
    → 上传 `fetcher.py` → **MD5 一致** → 预检 `PRECHECK-OK` → 重启双服务。
    - 预检 6 组断言：① 死源条目已删且源数 == 5（并逐源点名核对）② 死函数已删
      ③ **在线的必须保留**（`_fetch_kline_from_ths` / `_kline_amount_pair` / 腾讯昨比两函数）
      ④ `tencent_kline.down_threshold == 2`（抖动保护仍由它承载）⑤ 唯一选股链路健全
      （`_run_picker` 在、`_run_legacy` 不在，防老链路回退）⑥ 旧文案注释不得残留。
    - 验证：`active / active`；`/api/health sources` = **5 源**、`ths_kline 残留 = False`、
      `overall=ok serviceable=true`；`/api/stocks?action=filter` **200 / 4 只 / 0.59~0.76s**，
      **两机名单完全一致**（603162/600121/000759/002172，`prob`/`conf`/`bidChange`/`bidRatio` 全有值）；
      `Traceback` / `ImportError` / `ModuleNotFoundError` **计数 = 0**。
    - 启动日志里 v4.11.4 的「股性数据落库已启动」文案已生效（交叉确认前序补丁在线）。
  - **回滚点不变**：tag `v4.11.2` → `36ce505`。
  - **⚠️ 新发现（本次范围外，未动，待主人定）**：K 线兜底链的 `sources = ["eastmoney", "tencent"]`，
    故 `elif src == "ths"` / `"kpl"` / `"tushare"` 三个分支**运行时不可达** →
    `_fetch_kline_from_ths` / `_fetch_chart_from_kpl` / `_fetch_chart_from_tushare` 属
    「仅源码可达」的死代码（改 `sources` 即复活）。属独立重构范围。

- **v4.11.7 (09-11) 基线红清零：scoreFloor 降级豁免（含一处真缺陷）+ 三类测试基线修正**
  - **背景**：老链路退役（v4.11.3）后全量 874 例仍留 **7 红**，逐条定性后本轮全部修完 → **全量 0 红**。
  - **① 真缺陷（产品行为修复）：补丁源全失败时 `scoreFloor` 把名单砍空**
    - 现象：`test_auction_snap_pool_offhours` ×2「点查失败 → 快照行直出保名单」实得**空名单**。
    - 根因链：补丁源（东财点查 `fetch_raw_by_codes` + 腾讯点查 `fetch_tencent_by_codes`）**全断**时，
      候选行只剩 9:25 定格字段（换手/量比/异动/昨日涨幅全缺）→ 评分只可能拿到
      **竞价涨幅 34% + 流通市值 11%** 两个因子（上限约 45 分）→ 被 v4.10 引入的全站默认
      `scoreFloor=80` 整批砍掉 → 方案 A「点查失败降级直出保名单」这条**降级保命路径彻底失效**
      （该路径的设计目标恰恰是"行情源全挂时名单也不为空"，结果比降级前更空）。
    - 修法：`picker/filter.py::FilterContext` 新增 `score_floor_exempt`；`picker/pipeline.py` 在
      `_fetch_patch` 返回 None（补丁源不可用）时置 True → 本次豁免评分门槛，
      **其余过滤项（板块/ST/竞涨/市值/竞额）一律不变**。
    - 语义判据：`scoreFloor` 砍的是"这只票评分低"，降级时算出来的是"没数据可算" —— 门槛不该
      作用于失真的占位分。`test_score_floor` 新增 2 例锁定（豁免生效 + 豁免不放行其它过滤）。
  - **② `test_history` ×3（分页 / 同参去重 / 战绩）**：MOCK_RAW 里 `300003` 实评分 **73 < 80**
    被默认门槛剔掉 → 断言 4 只实得 3 只。该文件测的是落库/分页/去重，与评分门槛**无关** →
    `run_filter` 显式传 `scoreFloor=0` 隔离该变量（**不改断言、不动共享 fixture**）。
  - **③ KPL 金额单位（`test_snapshot_915_timing` ×1）**：fixture `bidAmt=888.0` 是按**万元**填的
    过期数据，而 KPL 现行口径是**元**（`kpl.py`），落库 `/1e4` 后得 0.0888 万 → 改 `8_880_000`（元）。
  - **④ `test_stats_api::test_overview_latest_4_days`（跨日漂移）**：`auction-overview` 无 `date`
    时取 `SELECT DISTINCT date FROM snapshot_bid ORDER BY date DESC LIMIT 4`（**全表**最近 4 日）——
    别的用例写入的真实当天日期会排到 seed 日（2026-08-20）之前 → 断言随执行日期漂移
    （9/11 实测拿到 2026-09-11）。修法：**仅该用例** seed 远未来日期（2099-01-02）保证恒排第一
    + 用例内清理；`_seed_snapshot(monkeypatch, date=...)` 加默认参数，其余用例继续用 2026-08-20。
  - **⚠️ 本轮踩坑（已写进技能）**
    1. **共用 seed 助手改日期必须加参数、只改需要的调用点**：第一版把 `_seed_snapshot` 的日期
       **全局**改成 2099-01-02 → 同文件 4 条按 `date=2026-08-20` 查询的用例（auction-snapshot /
       bid-snapshot / 三时点榜 / seal-quality）一次性被打挂，**全量多出 4 个新 F**。
    2. **判定"是否回归"必须跑全量对照，不能只看单文件**：本仓库测试有大量 session 顺序依赖，
       单文件/子集跑本就必红（基线同样红），只看单文件会把"既有顺序依赖红"误判成"改动引入的红"。
    3. 全量 `-q` 跑的**进度条字符等价于用例**：对照基线与新版进度条的 F 位置，可快速判断
       "红是不是换了地方"；`safe-delete` 提示会吃掉最后的摘要行 → 权威计数用 `--junitxml` 解析。
  - **回滚点不变**：tag `v4.11.2` → `36ce505`。

- **v4.11.8 (09-11) K 线兜底链「仅源码可达」死代码清理（3 死函数 + 3 死分支 + 死配置）**
  - **背景**：v4.11.6 报告里明确留了一条**范围外**遗留——`fetch_stock_chart_robust` 的
    `sources = ["eastmoney", "tencent"]` 使 `elif src == "ths"/"kpl"/"tushare"` 三分支
    **运行时永不执行**；但 `grep _fetch_kline_from_ths` 会命中那行 `elif`，看起来"仍被使用"
    → 属**第三类死代码「仅源码可达」**（分派表屏蔽，grep 查不出，必须读分派表）。
  - **判据升级（已写进技能）**：对"在源码里有引用"的函数，还要再问一句
    **「引用它的那条分支，运行时到得了吗？」** 到不了 = 死代码。
  - **删除清单**（判据：`sources` 只有两源 → 三分支不可达 → 其被调函数 0 生产可达）
    1. `fetcher._fetch_kline_from_ths`（同花顺 K 线兜底，65 行）
    2. `fetcher._fetch_chart_from_kpl`（开盘啦 chart 兜底，87 行）
    3. `fetcher._fetch_chart_from_tushare`（Tushare 网关兜底，133 行）
    4. `fetch_stock_chart_robust` 内三个 `elif` 分支（`src == "ths"/"kpl"/"tushare"`）
    5. `config.TUSHARE_BASE_URL` / `TUSHARE_API_KEY` —— 全仓仅第 3 项使用，
       随之 `0 引用`；**顺带把公开仓库里硬编码的 API key 一起下线**（安全收益）。
    - 净 **-334 行**（fetcher.py 单文件 -337/+3）。
  - **核实后保留（防「顺手删过头」，三处都有明确理由）**
    - `_aggregate_kpl_daily_to_period` + `kpl.fetch_kpl_doc7`：**仍在生产可达**——
      周K/月K 两主源全失败时的最终兜底（`fetch_stock_chart_robust` 函数末 `if period in ("week","month")`）。
      故原文档"自聚合已下线"是**错的**，本轮一并修正（AGENTS.md / backend-architecture.md）。
    - `kpl.fetch_kpl_doc8`：删 `_fetch_chart_from_kpl` 后生产 0 引用，但它是 kpl 模块的
      **接口表 wrapper**（`docs/kpl-interfaces.md` + `tests/test_kpl_doc.py` 逐接口覆盖，
      同族 doc80~doc89 同样 0 引用）→ 属「接口面」而非死代码，**不动**。
    - `_validate_chart_data`：源链共用校验，仍在生产使用 → 保留（顺带把 docstring 里
      "如同花顺累积前复权价"的失效举例改为通用表述；其 `source` 形参早已未被函数体使用，
      属既有设计位，本次不动）。
  - **无效注释同步（现行描述型必改 / 历史说明型保留）**
    - 改：fetcher.py 节头（原"当东财熔断时按顺序 fallback: 同花顺→开盘啦→Tushare→日线聚合"）、
      `fetch_stock_chart_robust` docstring、`_validate_chart_data` docstring 与复权检测注释、
      `kpl.py:3535` docstring + `:3596` 注释、`stock_temper.py:121` 注释、
      `AGENTS.md` 源链硬约定、`docs/backend-architecture.md` 两行（数据源 / "5 源图表兜底"）、
      `docs/deploy.md` 两行（个股图表 5 源→2 源；**昨日成交额一行也顺带修正**——同花顺昨比源
      v4.11.6 已删，现行为"收盘落库 + 全天读库 → 东财日K → 腾讯 qfqday"）、
      `docs/data_sources_tushare.md` 加「已下线，仅作未来接入参考」状态行。
    - 留：`docs/history.md` 历史条目、`fetcher.py` 中"同花顺昨比源已整体删除""量脉已删除"等
      **带"已删除/已下线"的历史说明**（后人理解"为什么这块长这样"的唯一线索）。
  - **测试**：三个函数**无任何用例覆盖**（`grep` tests/ 0 命中）；`test_stocks.py` 的
    robust 用例是**源无关**的（桩打在 `urllib.request.urlopen`，只断言"东财失败→腾讯成功"/
    "全失败→{}"）→ **无需改任何测试**。
  - **验证**：`ast.parse` + `import app.main` / `app.worker` 双预检 OK；运行时字节码断言
    `fetch_stock_chart_robust.__code__.co_names` **已无 ths/kpl/tushare 任何符号**；
    全仓 `grep` 三分支名 + `TUSHARE_` 在 `backend/app/` **0 命中**。
    全量 **876 例 / 0 失败 / 0 错误 / 4 skip**，与基线 `_v4117.xml` 做集合差 →
    **新增失败 = 空集**（"已转绿"亦为空，即纯删除、零行为影响）。
  - **回滚点**：tag **`v4.11.7`** → `1224038`（本次删除前最后提交，已推远端）。
    更早的整体回滚点 `v4.11.2` → `36ce505` 仍有效。
    ```bash
    cd /opt/kuaixuan && git fetch --all --tags
    git checkout v4.11.7 -- backend/app && systemctl restart kuaixuan kx-worker
    ```
  - **⚠️ 环境踩坑**：带沙箱升级的命令在本环境可能**被执行两次**（沙箱内 + 升级后各一次）。
    本次 `git tag -a v4.11.7` 因此第二次报 `fatal: tag 'v4.11.7' already exists` ——
    **"already exists" 不等于失败**，必须先 `git cat-file -t <tag>` +
    `git rev-parse <tag>^{commit}` 核实 tag 指向是否正确，别直接重建。

- **v4.11.9 (09-11) 生产「选股现涨幅又为 0」事故：锁定期(9:25-9:30) 无补丁源**
  - **症状**：主人在 **9:29**（竞价窗口收尾）反映「生产环境选股现涨又为 0」。属**时段性**故障：
    9:25-9:30 显示 0.00%，9:30 后自愈（下方"为什么"）。
  - **取证（都是只读探针 + DB 直查，未改任何线上文件）**
    - 日志实锤：`选股 mode=locked 全市场=132 候选=5 入选=2 源=snapshot 降级=False`
      —— **只有 snapshot 一个源，一个补丁源都没跑**，而 `降级=False` 让人看不出异常。
    - 落库质量（`batch_stocks.real_change`）：
      | 日期 | 9:20-9:35 锁仓批次 | 其中 real_change **全 0** | 零行占比 |
      |---|---|---|---|
      | 09-07 | 118 | 0 | 4% |
      | 09-08 | 118 | 3 | 16% |
      | 09-09 / 09-10 | **0**（该窗口无批次 → bug 潜伏未暴露） | — | — |
      | **09-11** | **107** | **105** | **94%** |
    - 当日汇总：170 个批次中 **105 个 real_change 全 0**；明细 777 行里 **310 行为 0**。
    - 时间指纹：batch 8862~8881 全部落在 **同一秒 09:26:03**、不同 uid、各 3 行 →
      正是 **9:26 系统批次 / `auto_apply` 全员自动锁仓**。
    - 排除项：东财全市场 clist、腾讯、push2dycalc 均 200；`OUTBOUND_IPS=172.22.114.161`
      正确（9/10 那个 eth1 坑没复发）；fd=13/65535；`Traceback` 计数 = 0。
      即**不是数据源故障**，是**列缺值**。
  - **根因链（四步，环环可证）**
    1. `mode.POLICIES[LOCKED]`（9:25-9:30）原为 `source_priority=("snapshot",)` +
       `realtime_patch=False` → **补丁源为空** → `pipeline._fetch_patch` 直接返回 None。
    2. 定格快照行 `QuoteRow.from_snapshot` 无实时价 → `real_change = None`。
    3. 落库 `history._safe_num(None)` → `_NOT_NULL_DEFAULTS["realChange"] = 0.0`
       （`batch_stocks.real_change` 建表即 **NOT NULL**）→ **"未知"被写成 0**。
    4. 9:25-9:30 前端走 `action=lock`，命中**当日幂等直读**分支 → 该分支当时**没有实时行情覆盖**
       （只有 refresh 的两条分支有）→ 把 DB 里的 0 原样回吐 → 前端 `realChange=0` 渲染成
       **`0.00%`**（`null` 才会渲染 `-`）。
    - **为什么 9:30 后自愈**：refresh 直读分支有 `fetch_spot_quote_map` 覆盖，把 0 刷成真值；
      前端 `mergeSpotIntoLocked` 再用 `lt.realChange` 覆盖。**症状因此只在 9:25-9:30 出现**
      —— 恰好是主人每天看盘前的那个窗口。
    - **为什么今天才爆**：v4.11.3（09-11）老链路彻底退役后 picker 成为**唯一**链路，
      锁仓（system_batch / auto_apply）不再走"自己取实时行情"的老实现；9/9-9/10 该窗口
      没有批次落库（bug 潜伏），9/11 自动锁仓恢复 + 新链路 ⇒ 一次性全暴露。
  - **修复（三处，判据：只改"未知→0"的成因，不改任何过滤/名单语义）**
    - **① 根因**：`mode.py` LOCKED 对齐其余三种模式 →
      `source_priority=("snapshot","eastmoney_realtime","tencent_point")` + `realtime_patch=True`。
      **不破坏幂等**：`list_source_count` 仍为 1 → 名单只由 snapshot 定；补丁只补展示字段，
      且价格门槛按定格竞价价判定、不参与名单；`auction_window` 仍为 False → 竞价字段不取实时。
    - **② 降级可见**：`pipeline.run` 中补丁源不可用除 `errors` 外加置 `res.degraded = True`
      —— 原实现只 append errors、`degraded` 仍 False，日志「降级=False」直接误导排查（违背铁律 2）。
    - **③ 症状兜底**：`api/stocks.py` 抽出 `_fill_spot_fields(lst, fs)`，把原先**只**出现在
      refresh 两条分支的"实时行情覆盖展示字段"复用到 **lock 当日幂等直读**分支
      （另外两处同时收敛为调用，去掉重复代码）。覆盖仅在取到非 None 值时进行，不增删票、
      不改评分与排序。**这条对今天已落库的 105 个坏批次同样生效**（无需回填 DB）。
  - **测试**：新增 3 例回归锁 —— `test_picker_mode.py::test_locked_mode_has_patch_source`
    （LOCKED 必须有补丁源 **且** `list_sources` 仍唯一是 snapshot、`auction_window` 仍 False）、
    `test_picker_pipeline.py::test_patch_unavailable_marks_degraded`、
    `test_picker_pipeline.py::test_locked_mode_patch_fills_real_change`（9:26 跑 → 现涨由补丁补上）。
  - **验证**：全量 **879 例 / 0 失败 / 0 错误 / 4 skip**（876 + 新增 3），与基线 `_v4118.xml`
    集合差 → **新增失败 = 空集**。
  - **回滚点**：tag **`v4.11.8`** → 见下方 v4.11.8 条目末尾的提交号；整体回滚点
    `v4.11.2` → `36ce505` 仍有效。
  - **⚠️ 遗留（本次未动，待主人定）**：`history._safe_num` 把 `real_change` 的 **None 兜成 0**
    是"未知 = 0"的原始制造者（因列 NOT NULL）。彻底修需给 `batch_stocks` 加一列标记
    "现涨是否已知"（`ALTER TABLE ADD COLUMN` 可平滑加），读侧把未知还原成 `null` →
    前端显示 `-`。属独立小重构，本轮先用 ②③ 把可见性补上，未动表结构。

- **v4.11.10 (09-11 深夜) P0 止血三项（竞价采集时刻 / 盘点阈值 / 落库 None 语义）**
  - **背景**：9/11 东财熔断复盘出 9 条问题，主人拍板 P0 三项立即做（其余 P1~P3 分期）。
    三项均属"止损"性质 —— 不改口径、不改名单逻辑，只补**采集时刻抖动**与**故障不可见**。
  - **P0-1 9_25 定格采集时刻 10s → 20s**（`auction_snapshot.py`，commit `cd49e62`）
    生产库落库时刻实测：9/7=09:25:20 / 9/8=09:25:22 / 9/9=09:25:20 / 9/10=09:25:32，
    但熔断日 9/11=**09:25:12** —— 10s 轮询相位使落库在 10~40s 抖动，下限 10s 会踩到
    撮合中间态。改常量 `_BID25_MIN_SEC=20`，窗口内仍有 20/30/40/50 四次机会。
    新增保险丝 `_same_as_prev_rate`（9_25 vs 9_24 逐票竞价额相等率）：生产全库
    9/2~9/10 共 7 个交易日**恒为 0.0%** → 阈值 0.50，正常日绝不误触发；9:25:50 后
    不再回滚重采（宁可留中间值也不整点缺失）。校验点刻意放在 aipick / system_batch
    **触发之前** —— 不能用 9:24 残值去跑 AI 预测与锁仓名单。
  - **P0-2 9:31 盘点加「行数 + 有额率」双阈值**（同 commit）
    旧自检只查 store 时点标记，**9/11 熔断日 9_25 仅落 132 行（正常 5500+）仍打印
    "采集完整"**，故障完全静默。行数阈值 3000；有额率阈值 50% 但**只对 9_15/9_25 生效**
    —— 9_20/9_24 长期仅 0.7%~2.3%（东财限流→腾讯兜底，竞价期无额字段），属结构性缺口，
    一并告警会造成正常日天天误报。
  - **P0-3 落库 None 与 0 分离**（`history.py` + `database.py`，commit `dd70ad5`）
    **即 v4.11.9 末尾那条遗留**。`_safe_num` 把 None 兜成 0 是为绕过 NOT NULL，
    但 0 是真实业务值 → "未知"伪装成"实测 0"。新增 `miss_fields` 列记下被兜底的键，
    读侧 `get_batch_stocks_mapped` 据此还原 `null`。
    ⚠️ **读侧默认关闭**（`settings.history_null_restore`，默认 0）：还原后前端收到 null，
    需配套「—」容错（`StockTable.vue:58` / `HistoryView.vue:77` 是 `{{ x }}分`，
    null 渲染成"分"）。前端容错上线后置 1 即生效 —— **这是 P0-3 的收尾项**。
  - **测试**：新增 `tests/test_p0_20260911.py` **14 例**（commit `d58f23c`）。最易误伤点
    已锁：真实 0 值**不得**被打标（换手 0% 是实测值不是缺失）。
  - **验证**：全量 **893 例 / 6 失败 / 0 错误 / 4 skip**（879 + 新增 14），
    失败 6 例 = 部署前已存在的 kpl 历史债，**新增失败 = 空集**。
  - **部署**：测试机已上（23:43 重启，双服务 active）；**生产未部署**，待主人指令。
  - **回滚点**：`v4.11.9` → `08fa032`。测试机无 `.git`，用
    `backup/p0_bak_20260911-233920.tar.gz`。

- **v4.11.11 (09-12 凌晨) P1 全市场预计算 + `stock_score_daily` 物化表**
  - **为什么能预计算**：评分 5 因子（竞涨 34% / 竞价换手 32% / 竞价强度 17% /
    流通市值 11% / 昨涨 6%）的输入**全部在 9:25 定格，无一依赖实时行情**。
    筛选 8 步门槛同样全冻结（第 8 步价格上限用**定格竞价价** = 昨收×(1+竞涨)）。
    → 竞价结束后可对全市场一次性算好分数，用户改条件时只做一次 SELECT + 纯 CPU 过滤。
  - **P1-1 全市场预计算 + 物化表**（`picker/precompute.py` 新增，commit `910ed01`）
    * `build_universe` 装配全市场定格行：`snapshot_bid` 9_25 + `yday_amount` 昨涨；
      `precompute_all` 评分后**幂等 UPSERT**（`PRIMARY KEY(date,code)` +
      `INSERT OR REPLACE`，SQLite 3.7 不支持 ON CONFLICT）。
    * **三条铁律**：① 绝不因网络可用性漂移 —— 唯一例外是 `prev_close`（静态值，
      拉一次即缓存，失败降级 None，priceGt 只对当日放宽、不误杀）；
      ② 缺失写 **None 不写 0**（数值列一律可空，方案草稿的 NOT NULL 会逼着填 0，
      违背 P0-3）；③ 行数 < 500 **不落半张表**（宁缺，读路径自动回退）。
    * **实测（测试机 9/10 定格 5557 只）**：耗时 **1.5s**，落库 5557 行，
      昨收/昨涨/强度覆盖均 **99.9%**，幂等复核一致；probability 11~93、confidence 65~90。
  - **P1-2 查询接口只读改造**（`pipeline.py` + `system_batch.py`，commit `9925e3d`）
    * `run()` 名单源步骤：开关开且物化表有当日数据 → 直接读物化表（一次 SELECT，
      无网络、天然幂等）；表缺失/为空/读取抛异常 → **静默回退原路径，接口永不报错**。
    * 新增 `_refreeze_locked`：物化路径下补丁补完展示字段后，把参与评分与门槛的
      定格字段（`bid_change/bid_amt/bid_vol/float_mv/prev_close`）还原为物化值 ——
      否则"评分用物化值、门槛用补丁值"两套口径，名单会随行情源可用性漂移。
    * 物化路径下补丁失败**不豁免** `scoreFloor`（评分来自预计算，未失真；
      原路径的"占位分豁免"语义在此不适用）。
    * `system_batch` 在 9:26 批跑**之前**跑预计算（放在"今日已存则跳过"之前，
      否则批跑跳过的日子也不会预计算）；失败只记日志，不影响批跑。
  - **开关**：`settings.precompute_read` / `precompute_write` / `precompute_detail`
    **默认全关** → 上线零影响；回滚 `precompute_read=0` 即可，无需回滚代码。
  - **测试**：新增 `tests/test_precompute.py` **10 例**（commit `aff7e3e`），锁六条性质：
    往返一致 / 幂等 / 行数闸门 / 开关双向 / 容灾回退 / 冻结字段不被改写。
    全量 **903 例 / 6 失败 / 0 错误 / 4 skip**（893 + 新增 10），失败 6 例 = 已知 kpl 债，
    **新增失败 = 空集**。
  - **顺带修复**：`system_batch._do_run` 加 `now` 参数（可注入时间）。原写死
    `time.time()`，周末 `tm_wday>=5` 直接 return → `test_picker_lock` 两个用例
    在**周六周日恒红/假绿**（业务断言根本没执行）。2026-09-12 跨午夜后暴露。
  - **部署**：测试机已上（01:07 重启，双服务 active，启动错误 0）；**生产未部署**。
  - **回滚点**：`v4.11.10` → `9404816`；备份 `backup/p1_test_bak_20260912-010325.tar.gz`。
  - ⚠️ **待办**：同参对拍（物化路径 vs 原路径逐票 probability 一致）需等**下周一
    开盘前**（周末东财 push2 全挂，原路径补丁取不到数，对拍退化为同口径无意义）。
    对拍通过后方可考虑 `precompute_read=1` 灰度。
- **v4.11.12 (09-12 凌晨) P2 竞价双源并存 + 市值日频缓存**
  - **P2-1 竞价第二源 TickPlus**（`services/tickplus.py` 新增，commit `c57af5b`）
    * 动机：东财是竞价期唯一能给 f615/f616/f617 的源，9/11 熔断日 9_25 只落 132 行，
      名单直接塌掉。TickPlus `fullbid` 是唯一能给「量+额」且覆盖全市场的第二源。
    * **响应体是 ZIP**（内含 data.json）—— 不解压直接 `json.loads` 得 0 条
      （9/12 补齐脚本踩过）；`p<=0`（竞价无成交）必须过滤（其 `zf` 恒 -100%）；
      `|zf|>30%` 过滤（新股首日可达 188%）；`je` 单位元 → `bid_amt` 万元（/1e4）。
    * 合并纪律 `_merge_tickplus`：**只补缺，绝不覆盖** —— 东财缺失的 code 补票、
      `bid_change`/`bid_amt` 为 0 补值、已有正常值不动（两源口径不同，
      覆盖会让同一时点出现两套数）。仅 `full=True`（时点全市场快照）启用，
      秒级采样 18s 窗口不动（全推成本不划算）。
    * 三道闸门：开关 → 熔断（`fetcher._HEALTH.tickplus_fullbid`，抖动阈值 2 /
      冷却 120s）→ token。任何异常返回空，**绝不抛给采集主链路**。
    * **安全边界**：token 走 `env TICKPLUS_TOKEN` / `settings.tickplus_token`
      （仓库公开，绝不入库代码）；**未配 token 时该源完全不发请求**，等同关闭。
    * **实测（测试机单次全推）**：**5551 只 / 1845ms**，涨幅与竞价额覆盖 **100%**，
      定格时刻 `t=09:25:00`；模拟东财熔断 → 从 0 只补到 **5551 只**。
  - **P2-2 流通市值日频缓存**（`services/mv_cache.py` 新增，commit `781f588`）
    * 问题：`float_mv`/`name` 是静态基础数据，却只能从当时行情源碰运气拿 ——
      东财一挂全市场市值一起没；TickPlus 完全不给市值 → 补进来的 5000+ 只票
      `float_mv=0` 会被市值门槛当小盘股全误杀（补了等于白补）。
    * 新表 `stock_float_mv_daily`（PK date+code，数值列**可空**：未知写 NULL 不写 0）。
    * 三级兜底：① 当日行情源（东财 f21/f117，最准，已有值绝不覆盖）→
      ② 本表最近一日（≤15 天，**超窗拒绝**：股本可能已变）→
      ③ 腾讯 `qt.gtimg.cn` f44 批量补（单位**亿** → ×1e8 得元，单位错会放大 1e8 倍）。
    * 落库**之前**调用 `mv_cache.fill`：补到的市值要跟本次快照一起入库。
      东财正常日 **0 次腾讯请求**；熔断日只补缺失，上限 4000 只防打爆。
    * **实测（测试机）**：300 只全缺 → 腾讯补 **290 只 / 222ms**，落缓存 290 行。
  - **回归**：新增 20 例（`test_tickplus` 12 + `test_mv_cache` 8，commit `99ee622`），
    全量 **927 例 / 6 红（= 已知 kpl 历史债）/ 0 新增红 / 0 错误**。
  - **部署**：测试机已上（02:01 重启，双服务 active，启动错误 0）；
    漂移核对干净（database +18 / fetcher +4 / auction_snapshot +63-1，**零远端独有
    补丁被覆盖**）；**生产未部署**（需主人明确指令）。
  - **回滚点**：`v4.11.11` → `2a96e34`；备份 `backup/p2_test_bak_20260912-015756.tar.gz`。
  - ⚠️ **待办**：① 真实竞价时段验证（今天周六，TickPlus 返回的是 9/11 定格，
    9:15/9:20/9:24 三个时点的**实时**行为要等周一 9:15 看日志确认）；
    ② 生产部署需先配 `tickplus_token`（未配即天然关闭，可放心先上代码）。
- **v4.11.13 (09-12 上午) P0-3 前端容错收尾 + P3 前端本地筛选**
  - **P0-3 前端 null 容错**（`1ab0b74`）
    * 背景：落库侧 `_safe_num`/`_num_or_mark` 把 None 兜成 0 以保 NOT NULL 与排序，
      并把"被兜底的键"记入 `batch_stocks.miss_fields`；读侧 `history_null_restore=1`
      时还原为 `null`。**0 = 实测值，null = 未知** —— 但前端拿到 null 会崩或显示 0.00。
    * `utils/format.js`：新增 `fmtNum(v, digits, suffix)`；`signed`/`pct` 缺失返回
      从 `'-'` 改为 **`'—'`**（与"-"=不适用区分）。口径：**未知显示「—」，实测 0 仍显示 0**。
    * **修一个真实崩溃点**：`HistoryView.vue` 的 `{{ s.circulation_mv.toFixed(1) }}` ——
      null 上调用 `.toFixed` 直接抛错整页白屏；改走 `fmtNum`。
    * `StockTable.vue` / `HistoryView.vue`：涨跌三列统一走 `pct()` + `chgCls()`，
      评分/可信/流通走 `fmtNum`；竞额/比缺失 → `'—'`；`realCls` 加 null 守卫；
      删除组件内重复的 `pctText`/`fmtPct`（统一使用 format.js，防两套口径）。
    * **顺带修 `passLockedFilter` 语义 bug**：`floatMvGt`/`priceGt` 为 **0 表示不限**，
      旧实现无条件比较 → 用户把"流通≤"填 0 会把**所有票剔除**；已加 `> 0 &&` 与后端
      `picker.filter` 同语义（对齐 2026-09-11 后端的同一处修复）。
  - **P3-a 后端快照接口**（`9f19c87`）：`GET /api/picker/snapshot`
    * 一次性下发当日**全市场预计算评分**（物化表 `stock_score_daily`），前端据此在
      浏览器内完成筛选（改条件秒出，零网络往返），实时价仍走 `/api/quotes` 按需补。
      依据：评分 5 因子输入全部在 9:25 定格（P1 已论证），改条件只是对同一份结果换门槛。
    * **四条安全边界（缺一不可）**：① 开关 `frontend_local_filter` **默认 0**，关闭时
      只回 `enabled=false`，前端静默回退原路径 —— **天然灰度，不需前后端同时上线**；
      ② 门禁 `require_vip_or_paid`（与竞价异动同级）；③ **行数闸门** `< MIN_ROWS(500)`
      视为不可用，**绝不发半张表**；④ 同口径（前端逐条复刻后端过滤 + 抢筹标复用
      `pipeline.qc_fields`）。响应进程内缓存 60s（全市场 ~5500 行 ≈ 2MB，同日共享）。
    * `precompute.read_snapshot_rows()`：物化表 → 前端扁平行（`bidAmt` 万元 / `floatMv` 亿，
      与 filter 门槛同一单位口径）。
    * **`is_zt_yday` 同口径修正**：`zt_codes or set()` → 改回
      `is_first_board(r.code, r.concept, zt_codes)`，**保留 `zt_codes=None` 的 concept
      降级语义**（写成空集会让"降级"变成"无昨涨停"，判据整体反转）。
    * **`system_batch` 传昨涨停名单**：预计算调用改为显式取
      `_fetcher.get_yesterday_zt_codes()` → `precompute_all(today_str, zt_codes=zt)`。
      否则物化表 `is_zt_yday` **恒 0** → 前端"剔除昨涨停"在本地筛选里**整批失效**
      （用户从名单上看不出来）。实测验证：传池后 9/10 落库 `is_zt_yday=1` 共 **48 只**
      = 昨涨停池只数。
    * `pipeline.qc_fields()` 抽为公共函数，`_qc_of` 委托调用 —— 本地/后端抢筹标不分叉。
  - **P3-b 前端本地筛选**（`185b033`）
    * `utils/filters.js`：`COARSE_MAX=120`、`inMarkets`（北交所一律排除）、
      `_coarseOk`/`_refineOk`/`_frozenPrice`/`snapshotToRow`/`pickFromSnapshot`
      —— **逐条复刻**后端 `coarse_filter` + `apply_filters`（含"竞额降序取前 120"截断
      位置与"评分降序 + code 字典序"排序）。
    * 口径坑：`bidGt` 是**竞价涨幅上限且无 `>0` 判断**（0 = 上限 0% ≈ 全剔），
      而 `floatMvGt`/`priceGt` **有 `>0` 判断**（0 = 不限）—— 同后缀不同语义，必须逐字段照抄。
    * 契约版过滤只有 **8 步**（market / first_board / ST+停牌 / 竞价涨幅区间 /
      prob+conf 双低 / scoreFloor / 市值 / 竞额 / 价格）；`chgGt`/`volRatioFloor`/
      `turnoverFloor` 等实时门槛**契约链路已不再检查**，前端同样不复刻。
    * 价格门槛用**定格竞价价**（`auctionPrice`，缺失则 `昨收×(1+竞涨/100)` 派生），
      不用实时价 —— 否则盘中价格一漂名单就变。
    * `stores/stocks.js`：新增 `snapshot`/`loadSnapshot()`（懒加载 + 失败 60s 冷却）
      /`_attachQuotes()`（`/api/quotes` 补实时价，拿不到保留定格值**不写 0**）；
      `applyCustomFilter()` 开头加本地分支，成功即 `showToast('⚡ 本地筛选完成')` 并
      `return`，失败/未开启**静默回退后端路径**（行为不变）。
  - **测试**：前端 `node --test` **44 例全绿**；后端新增 `test_picker_snapshot.py`
    **17 例**；**前后端共用夹具** `tests/fixtures/picker_parity.json`（16 行 / 7 case），
    两侧各有一份对拍测试 —— 口径分叉的唯一防线。
  - **部署与实测（测试机）**：预检 12/12 → 全量 **944 例 / 6 红（= 已知 kpl 历史债）/
    0 新增红 / 0 错误** → 前端 dist 部署 → 08:42 重启（双服务 active，启动错误 0）。
    * **P0-3 实测**：旧数据（无 `miss_fields`）开关 0/1 读结果**完全一致**（开启对历史
      零影响）；构造样本（打标 `probability,realChange`）开关 1 时正确还原 `None`、
      未打标字段不受影响、行数不变。
    * **P3 实测**：用 9/10 完整快照（5557 只）+ 昨收（5555）+ 昨涨停池（48）**补跑
      预计算**（落库 5557 行 / 835ms，`auction_price>0` 5554 / `is_zt_yday=1` 48）；
      接口开关开 + VIP 返回 **5557 行 / 1.98MB / 522ms**（缓存命中 119ms）；四边界
      全部符合预期（关→`enabled=false`；无鉴权→401；空表→`enabled=false` 不 500）。
    * **真实数据前后端对拍：12/12 组条件名单逐票完全一致**（含 120 截断边界、
      市场范围、ST/昨涨停、价格/市值/竞额门槛、全放宽）。
  - **回滚点**：`v4.11.12` → `803ccbb`；备份
    `backup/p03p3_test_bak_20260912-083819.tar.gz` + `backup/dist_bak_20260912-083819.tar.gz`。
  - ⚠️ **上线纪律**：两个开关（`history_null_restore`、`frontend_local_filter`）**默认 0**，
    本次随代码上线不影响任何现有行为；**生产未部署**（等 P2+P0-3+P3 一次性推，需主人明确指令）。
- **v4.11.14 (09-12 上午) 生产全量部署：P0-3 + P1 + P2 + P3 一次性上线**
  - **部署前漂移地图**（生产无 git，先全量比对再动手；两侧剥 `\r` 后比 MD5）：
    共有文件 97 / **一致 70 / 不一致 27**；仅生产有 2（`scripts/backfill_kpl_seal.py`、
    `scripts/recalc_boom_history.py` —— 生产独有，**一律不动**）；仅本地有 55（其中 4 个是
    生产尚无的新源码，其余为测试文件 —— 生产无 pytest，**不上测试**）。
  - 🔴 **最重要的发现：生产仍带 9/11 事故的病**。`picker/mode.py` 与 `api/stocks.py`
    两处修复**此前从未上生产**：
    * `mode.py`：生产 `LOCKED` 仍是 `source_priority=("snapshot",)` + `realtime_patch=False`
      → **一个补丁源都没有** → 定格行无实时价 → `real_change` 恒 `None` → 落库被兜成 0；
    * `api/stocks.py`：生产 lock 当日幂等直读分支**没有** `_fill_spot_fields`。
    本次随推上线，**周一 9:25-9:30 锁定期生效**。
  - **推送清单（17 个文件：16 app + requirements.txt）**
    * 更新 12：`main.py`（注册 `picker.router`）、`core/config.py`（删 Tushare 死源配置）、
      `db/database.py`（新增 2 张物化表）、`api/stocks.py`（`_fill_spot_fields` 抽取 +
      lock 直读补行情）、`services/auction_snapshot.py`（TickPlus 双源合并 + 市值兜底）、
      `services/fetcher.py`（TickPlus 熔断项 + 删 ths/kpl/tushare 死源）、
      `services/kpl.py`、`services/stock_temper.py`（各 1~2 行注释收敛）、
      `picker/filter.py`（`score_floor_exempt`）、`picker/mode.py`（LOCKED 修复）、
      `picker/pipeline.py`（物化路径 + `_refreeze_locked` + degraded 修正）、
      `system_batch.py`（`now` 注入 + 预计算调用）。
    * 新增 4：`api/picker.py`、`services/mv_cache.py`、`services/tickplus.py`、
      `picker/precompute.py`。
  - **行尾策略**：实测生产是**混合行尾**（`main.py`/`kpl.py`/`stock_temper.py` 已是 LF，
    `config.py`/`database.py`/`mode.py`/`requirements.txt` 是 CRLF）→ 上传时
    **统一转 LF**（3 个保持原状、4 个归一，Linux 规范且 Python 完全兼容）。
  - **风险残留预检**（推前只读）：被删的 `TUSHARE_BASE_URL`/`TUSHARE_API_KEY` 与
    `_fetch_kline_from_ths`/`_fetch_chart_from_kpl`/`_fetch_chart_from_tushare`
    在生产**只被本次替换的 fetcher.py / config.py 自身引用**（外加不推的 tests）
    → 删改安全；`_aggregate_kpl_daily_to_period` 等仍在用的函数全部保留。
    生产 venv 已装 `alibabacloud-dypnsapi20170525 2.0.0`（requirements 无需实际安装）；
    TickPlus 只用标准库，无新依赖。
  - **重启前预检 38 项全 PASS**：`import app.main` + `openapi()` **96 条路径**（含
    `/api/picker/snapshot`）+ 正反断言（TUSHARE/死函数**已无**，自聚合/`fetch_spot_quote_map`
    **仍在**）+ 开关默认全关 + `LOCKED.realtime_patch=True` 且补丁源非空 + 两张新表已建。
    * 预检里一条自加的一致性断言首轮 FAIL（`auction` 时段 `realtime_patch=True` 却无补丁源）
      → 复核确认其**两个名单源本身就是全市场实时快照**（`eastmoney_market`/`tencent_market`，
      量额涨幅齐全），属合理设计，已把断言口径改为"无补丁源 **且** 名单源也非全市场实时源"。
  - **部署与验证**：dist 原子替换（`dist.new` → 校验 → `mv` 换入，避免 nginx 读到半份产物）
    → 双服务重启（`09:25:11` / `09:25:15`）→ 验证：
    * 启动致命错误 **0**（kuaixuan / kx-worker）；
    * 运行态 `openapi` 含 `/api/picker/snapshot`；快照接口无 token → **401/403**；
      `/api/stocks`、`/api/quotes` 无 token → **401**（非 500）；未知路径 → 404；
    * 登录接口正常（错密码 → 401 + 中文提示）；
    * 前端新 JS/CSS 资源 **200**、首页引用新 hash；`dist/assets` 1016 个；
    * 两张新表已建（`stock_score_daily`、`stock_float_mv_daily`）；
    * kx-worker 调度全绿（快照 6 时点 / 尾盘推送 / AI 竞价 / 概念刷新 / 连板天梯 / 股性存档）。
  - ⚠️ **开关全部保持默认（未开）**：`frontend_local_filter`、`history_null_restore`、
    `precompute_read/write/detail`、`tickplus_enabled`/`tickplus_token` 在生产 settings
    表**均无 key** → 走代码默认（全关）→ **行为与部署前一致**，仅两处 9/11 修复直接生效。
    启用需显式写 settings（P1 写物化表 → 开 `precompute_write`；P3 本地筛选 → 开
    `frontend_local_filter`；P2 第二源 → 配 `TICKPLUS_TOKEN` 并开 `tickplus_enabled`）。
  - **遗留（不影响运行）**：生产 settings 里有历史 key `picker_gray='1'`，而该灰度开关
    已在 v4.11.3 随老链路删除 → 现为**无效遗留 key**；systemd 里的
    `Environment=TUSHARE_API_KEY=...` 也随 config 清理成为无用变量，均可后续清理。
  - **回滚点**：备份 `backup/prod_p0123_bak_20260912-091953.tar.gz`（后端 3.3MB）+
    `backup/prod_dist_bak_20260912-091953.tar.gz`（前端 36.6MB）。
- **v4.11.15 (09-12 下午) 生产启用 TickPlus 第二源（运行时配置，无代码改动、无重启）**
  - **变更**：生产 settings 表写入 `tickplus_token`（凭证按安全要求不入库文档）+
    `tickplus_enabled=1`。写入走 `settings.set()`（JSON 序列化 + `INSERT OR REPLACE` 带
    `updated_at`）—— **不可裸 SQL 写纯文本**，否则读侧 `json.loads` 失败会静默回退默认值。
    settings 为每次读库（非启动缓存）→ **下一次采集自动生效，无需重启**。
  - **生产通路实测**（走真实入口 `tickplus.snapshot_map()`，含开关→熔断→token 三闸门）：
    全市场 **5551 只 / 917ms**（首次冷启动含 import 1992ms）；熔断器
    `_check_circuit(tickplus_fullbid)=False`。返回 `t` 全部落在 **09:25:00–09:25:05**
    （2947/1101/813/477/204/7 只分段），符合"9:25 定格 + 5~10s 发布延迟"特征 →
    确认是**当日最终定格值**而非 9:24 残值。
  - **零影响实证（核心）**：取生产库 9/11 `9_25` 真实落库（东财**正常日** 5552 只）构造
    东财侧数据，调用**生产同一份** `_merge_tickplus()` → **补票 0 只 / 补值 0 项 /
    东财已有值 0 处被改写 / 总只数 5552→5552**。→ 东财正常日该源对结果**完全无影响**，
    价值只在东财熔断时兑现（测试机 9/11 实测 143 行 → 补票 5408，覆盖率 2.6%→99.9%）。
  - **两处细节核验（避免误判为缺陷）**：
    * 东财 316 只"涨幅=0"的票 → 在 TickPlus 侧 **316/316 同为 0.0**（None 0 只、非 0 只）
      → 属**两源一致的平盘判定**，非漏补；`_merge_tickplus` 的 `and bc:` 判据在此场景正确。
    * 两源涨幅不同的 **8 只**（占 0.14%，全为沪市 600/603）→ **TP 竞价额系统性偏大
      （1.23~7.27 倍）、TP 涨幅系统性偏小**；竞价额单调累积 ⇒ 金额更大 = 时刻更靠后，
      价格回落而成交额放大 = **撮合完成**特征 → 判定**东财踩到撮合中间态、TP 记录最终定格**，
      与测试机 9/11 独立结论（东财统一 09:25:12 落库抓早）一致。合并不覆盖 ⇒ 不影响生产；
      且 P0-1 已把定格阈值提到 20s（`_BID25_MIN_SEC`），升级后差异预计消失。
  - **全市场竞价额吻合度**：两源**精确相等 5543 / 5552 只（99.86%）**，不等 8 只、缺失 1 只。
  - **回退**：`settings.set("tickplus_enabled", 0)` 立即回到单源东财；或
    `settings.set("tickplus_token", "")` 清空凭证（第三道闸门拦截，发不出请求）。
  - **观察点**：日志 `[快照采集] 双源合并 东财N只 + TickPlusM只 → 补票X只 补值Y项`；
    东财正常日应为 补票 0 / 补值 0。失败时 `[TickPlus] 采集失败(不影响东财链路)` 被吞。
  - **待周一验收**：9:15 首轮是否带第二源、9:20 耗时增幅（≈1s）、9:24/9:25 合并是否零改动、
    定格落库只数（新 20s 阈值首日生效）、9:25-9:30 锁定名单现涨非 0。

- **v4.11.16 (09-13) P1：修「采集时刻早于上游发布」根因 + 故障自愈 + 日K缓存 TTL（已部署测试机）**
  - **背景**：落库审计发现两个每日任务长期空转 —— `limit_history` 停更 8/26（缺 12 个
    交易日）、`lhb_history` 停更 8/14（缺 20 个）。根因**不是调度没跑**，而是
    **采集时刻早于上游发布时刻**，且失败后既不在窗口内重试、也无次日补救。
  - **P1-a 修时刻错配（止血）**：
    * `stock_temper.BACKFILL_AT` **15:30 → 18:30**（窗口 18:10~18:50）。依据：上游是
      **选股宝 flash 池**（原注释误写"东财"），当日数据到 **15:47 仍未发布**（8/27 当天
      15:25/15:37/15:47 三次尝试全空）；生产「涨停/炸板落库」成功日志计数 = **0**，
      即自上线起从未成功，表中 27010 行全靠一次性回补脚本填充。
    * `_fired` 改为**成功后才置位** + 窗口内**失败退避重试**（300s 起翻倍、上限 1800s），
      并加 `_running` 防重入（rebuild 耗时超过 30s 轮询间隔）。旧逻辑「起线程即置位」
      → 窗口内只试一次，上游未发布就当天彻底放弃。
    * 龙虎榜从 15:30 窗口**挪到 18:30~18:40 晚间窗口**（龙虎榜盘后公布，通常 18:00 后），
      并补**失败回滚** `store.delete`（旧逻辑 `setnx` 占锁后判空不释放 → 一次空即当天废弃；
      同窗口 `ladder` 子块本来就有回滚，lhb 是漏写）。
  - **P1-b 新增次日盘前补救（自愈）**：`stock_temper` 新增 **09:00~09:05 补救窗口**
    （避 9:15 竞价主流程），回溯最近 7 个自然日，补齐 `limit_history` / `lhb_history`
    缺失交易日。幂等（`INSERT OR REPLACE`）。意义：晚间窗口因重启/节假日错过时，
    次日开盘前自动兜住，不再累积成长缺口。
  - **P1-c 修日K缓存无 TTL（治本）**：`_kline()` 旧逻辑**命中缓存即永久返回、无过期判断**，
    而 `rebuild_profiles()` 用默认 `refresh_kline=False` → 日K底座永久冻结在首次写入时刻
    （生产实证 `stock_kline` 与 `stock_temper_profile` 的 ts **全部 = 8/27**，相差 11 个
    交易日）。新增：末根日期 < 期望最近交易日（收盘 15:05 前取上一工作日）→ **回源**；
    解析不出末根日期时按新鲜处理（避免异常形态下全量回源打爆上游）。
    ⚠️ 不修这项，只调时间窗**治不好**画像 —— 底座照样冻结。
  - **P2 代码卫生**：删 `save_day(force=)` 死参数（从未被函数体引用）；
    `kpl._flash_pool` 注释"东财"→**选股宝**（写错源名会直接误导排障）；
    `core/net.py` 头部"双网卡双出口"→**仅剩 eth0**（出口轮换已失效），并补记
    9/12 住宅代理实测结论（**同出口、不同接口、结果相反 ⇒ 东财封锁不看来源 IP**，
    代理路线已证伪）；`start_scheduler` 日志写死"15:30"改为动态引用常量。
  - **验证**：
    * 本地 pytest **961 passed / 4 skipped / 0 红**（基线 940 + 新增
      `tests/test_stock_temper_p1.py` **21 例**，覆盖时刻/退避/补救/TTL/窗口触发）。
    * 测试机部署：预检 12 项全 PASS 才重启；`_rescue_missing()` 实跑
      **补齐 lhb 5 个交易日（9/7~9/11）**，二次实跑返回"数据齐全"→ 幂等得证。
    * **日K TTL 真实回源验证**（测试机，真实上游数据）：新鲜缓存回源 **0 次**；
      把末根改成 2026-08-02 后回源 **1 次** 且缓存末根刷新为 2026-09-11。
    * 测试机全量 959 passed / 2 红 —— **回滚到改动前对照，同样这 2 红**
      （`test_login_routes_are_async` / `test_kpl_bid_qiangcang_fastpath...`）
      ⇒ **测试机基线固有，与本次改动无关**（测试机为补丁拼盘，非 git 基线）。
  - **回滚**：测试机 `/opt/kuaixuan/backup/p1_bak_20260913-014821.tar.gz`（**首次**部署前备份）。
    ⚠️ 坑记：重复部署时脚本备份的是**已改动**状态（第二次备份包 = P1 版本），
    真正的回滚点只有**首次**那个包。
  - **未做（等指令）**：画像链重建（`stock_temper_profile` 仍停 8/27）需在 P1-c 生效后
    刷日K + `rebuild_profiles()`；生产部署待主人确认。

- **v4.11.17 (09-13) P1 推生产（commit `ae779ea` 的落地）**
  - **部署前置 · 漂移审计**：生产 5 个目标文件与 git `ae779ea^` **逐字节一致（零漂移）**
    → 确认无生产独有补丁，可安全整文件覆盖（生产是补丁拼盘，此步不可省）。
  - **推送**：`stock_temper.py` / `auction_snapshot.py` / `kpl.py` / `core/net.py` / `worker.py`
    + `tests/test_stock_temper_p1.py`（行尾统一 LF）。
  - **预检 29/29 PASS 才重启**：新常量/新函数就位、`save_day` 已删 `force`、
    龙虎榜在 18:30 窗口且带 `store.delete` 回滚、14 个原有函数仍在（防覆盖丢东西）、
    `app.main` 可导入（96 路由）。
  - **重启** `kuaixuan` + `kx-worker` 双服务 active；调度日志确认新时刻生效：
    `股性调度已启动: 盘后存档 18:30(±20分) / 盘前补救 09:00-09:05`。
  - **验证**：`_rescue_missing(days=7)` 生产实跑 = **无（数据齐全）** → 幂等，
    且佐证 9/12 补采结果完好（limit 28069 行 / lhb 27 行，最新均 9/11）。
    日K缓存抽样末根 8/27 → 判定"会回源"，符合预期（TTL 已生效，存量待重建）。
  - **零影响佐证**：`stock_temper_profile` 3951 只 ts=8/27 16:59、`stock_kline` 3951 只
    ts=8/27 15:18（本次未触碰）；`tickplus_token/enabled` 仍在位；`integrity_check = ok`。
  - **回滚点**：代码 `/opt/kuaixuan/backup/p1_prod_bak_20260913-090538.tar.gz`、
    DB `/opt/kuaixuan/backup/kuaixuan.db.pre_p1_20260913-090538`（418 MB，SQLite 在线备份 API）。
  - ⚠️ 两点记录：① 生产 venv **无 pytest**，新守护用例无法远端跑（本地 961 全绿 + 预检覆盖运行时符号）；
    ② 首次部署因自加断言写了不存在的 `save_snapshot` 被预检拦下并自动回滚（**闸门生效**），
    修正为真实函数名（`snapshot_at` 等）后重跑成功。

- **v4.11.17.1 (09-13 下午~晚) 画像链重建 + 市值缓存回填 + 预计算写入开关（生产运维，无代码改动）**
  - **#92 画像链重建（12:43-12:58）**：P1-c 让 `_kline` TTL 生效后，一步
    `rebuild_profiles()` 即完成治本 —— 实测 **3978 只 / 861.6s（14.36 分钟）/ 单只 0.217s /
    失败 0**，`_kline` **回源 3951/3978**（TTL 前命中率 99.3% → 全量回源）。
    `stock_kline` 的 `ts` 从冻结 8/27 全部刷新，抽检陈旧 0；`stock_temper_profile` 同步重建
    —— 样本 **002790 瑞尔特 `zt_count` 4 → 8**（8/27 快照只含 2025-10-28~31 那组四板，
    9/12 补采进来的 2026-09-08~11 另一组四板此前未计），`max_zt` 仍为 **4**
    （连板高度未变，变量的是**累计涨停次数**）。其他表**行数零变化**、`integrity_check=ok`、
    双服务 active。备份 `backup/kuaixuan.db.pre_p92_20260913-124224`（399 MB）。
    ⚠️ **跨机外推必须乘 2 倍安全系数**：同一脚本测试机 0.105s/只（外推 7 分钟）→
    生产实际 **0.217s/只**（生产每只历史行数 7 vs 测试机 1.9）；生产 DB 是 **WAL**，
    在线大批量写不阻塞读侧，此类重建可放心在线跑。
  - **#93-1 市值缓存回填（14:23-14:25）**：源 `snapshot_bid`（带值 442577 行），
    同一 `(date, code)` 取 **`ts` 最大**那条，走 `mv_cache.save()` 写入
    `stock_float_mv_daily` → **117713 行 / 22 个交易日（2026-08-13~09-11）/ 13.3s**；
    抽 200 只与源值比对**不一致 0**；`mv_cache.lookup()` 5851 只 → **命中 5503（94.1%）/ 0.30s**。
    → 意义：东财熔断日腾讯兜底只需补 ~350 只（原需 ~1500 只），
    **不再触发 `MAX_FETCH=4000` 静默截断**（原会丢 ~1400 只市值 → 被市值门槛误杀）。
  - **#93-2 开启 `precompute_write`（19:55）**：`settings.set("precompute_write", 1)`，
    直读 DB 确认 `write_enabled()=True` 且 **`read_enabled()=False`**（读开关未动 → **零业务影响**）；
    settings 每次实时读库，**未重启**。`stock_score_daily` **首次写入要等下一个交易日 9:26**
    （`precompute_all()` 唯一调用点 `system_batch.py:108`，由 9:25 落库后触发）。
    回滚：`settings.set("precompute_write", 0)`。
  - 🔎 **可复用经验**：① 回填/重建类脚本**先纯计算、后写库**（本次市值回填曾因
    `best[k][5]` 越界崩在计算阶段 → **零副作用**，否则要回滚）；② 判"某修复在生产是否生效"
    要做**漂移地图**，别假设；③ 运维类改动**即使无 commit 也要留 history 条目** ——
    本次三件事一度只存在于日志/记忆中，文档成空白。

- **v4.11.18 (09-13 改 / 09-14 00:12 推生产并开启) 首页两市量能改取开盘啦实时接口**
  - **起因**：主人自查首页「市场情绪」并要求「先不要改代码」。只读审计发现三个问题 ——
    ① **真 bug**：`_close_chg_persist_allowed()` 只判「今天过没过 15:00」、**不判交易日**
      → 周日 15:47 照样触发，把实时涨幅（=9/11 值）写成 `date=2026-09-13`（53 行 vs 9/11 的 292 行）；
    ② 主人追问「开盘啦量能 19871，我们 19716」→ 追到 `market_fs()` **根本没有 `bj` 分支**
      （传参写了 4 个市场，函数体只有 hs/cyb/kcb 三个 `elif`，`"bj"` 被静默吞掉）；
    ③ 主人指出「相比昨日永远比的昨日总成交，不是同一时点」。
  - **③ 的核查结论**：`get_same_time_yesterday()` 在 **9/07 已修过**（原 `s['ts'] <= now` 条件恒真
    → 永远取昨日 15:00 收盘），**盘中实测已能对上同时点**（9/11 10:30 ↔ 9/10 10:28）。
    但残留三个缺陷：**09:30 `amount=0` 脏点**（早盘增量虚高 7.7 倍：9/11 10:00 显示
    +6631 亿，正确 +857 亿）、**快照回退**（累计量应单调增，实测 9/8 六处回退）、
    东财分页失败**静默少算**（9/8 少 1491 亿 = -7.6%）。
  - **② 的反转**：主人提示「开盘啦有市场量能接口，看 docs110/111」→ 实测 `MarketSCLNKLine`
    只给 125 天**日级**序列；再经主人纠正「实时接口能返回请求时刻 + 往日同时刻」，才找到
    **真·实时接口 `a=MarketSCLN` / 域名 `market` / `c=HomeDingPan`**（比 doc110 少 "KLine"，
    域名非 his）。它一次给 `last`(今日此刻) + `s_zrtj`(**昨日同一时点**) + `s_zrtj` 前3日同期
    + `ycln`(全天预测量能) + `trends`(分钟级序列)，**与自存快照逐点吻合 0.01~0.37%**。
    → **此前"补 bj 分支"的提议撤回**：接口默认口径就是两市（App 的 19871 = 接口 19719 + 北交所 153，
    `Type=4` 才是含北交所），补了反而与官方口径对不上。
  - **改动**：`kpl.py` 新增 `fetch_kpl_market_scln()` + `parse_market_volume_rt()`；
    `build_market_brief_payload()` 加实时量能分支（`amount` 与 `last_same_time` 双双覆盖）；
    `config.py` 加 `KPL_MARKET_SCLN_TTL=60`。**开关 `market_vol_rt` 默认 0（关）**，
    取不到/解析失败/抛异常**一律完全回退自算值**，`last<=0` 返回 None（0 绝不当实测值）。
  - **测试**：新增 `tests/test_market_vol_rt_20260913.py` 11 例（万元→亿元换算、6 种异常形状容错、
    开关开/关/失败回退/异常回退）；**全量 976 passed / 0 失败 / 0 错误 / 4 skip**（junitxml 权威计数）。
  - **测试机部署**：预检 12/12 PASS（含**真实网络**验证开盘啦 market 域名可达）后重启；
    双服务 active、HTTP 200；开关置 1 实测 `amount=19718.98 volSrc=kpl`、
    `last_same_time=16471.48 src=kpl`（周日"此刻"已过收盘 → 退化为昨日全天，属预期）。
  - **回滚**：代码 `/opt/kuaixuan/backup/volrt_bak_20260913-235308.tar.gz`（测试机）；
    功能回滚 `settings.set("market_vol_rt", 0)`，**实时生效不用重启**。
  - **生产部署（09-14 00:12，commit `c0a4609`）**：备份
    `/opt/kuaixuan/backup/volrt_bak_20260914-001213.tar.gz` → 预检 **11 PASS + RESULT=OK**
    （含真实网络验证开盘啦 market 域名可达）→ 重启 → 3 文件 MD5 全部 OK →
    开开关 `market_vol_rt=1` → 实测 `amount=19718.98 volSrc=kpl`、
    `last_same_time=16471.48 src=kpl`、`volForecast="19718亿(19.72%,增量3247亿)"`、**VERIFY=OK**；
    双服务 active，`https_root=200`（`http_root=301` 是 http→https 跳转，非故障），
    近 5 分钟 kuaixuan / kx-worker **均无 ERROR/Traceback**；TTL 缓存生效（0.15s→0.11s，两次一致）。
    生产回滚包同上，功能回滚 `settings.set("market_vol_rt", 0)`。
  - ⚠️ **验收时点**：周日请求"此刻"已过收盘 → `last` 与 `s_zrtj` 都等于各自全天
    （`prev_same_time == prev_full` 属预期）。**真实验证要等 9/14 盘中 10:00**，
    届时 `last` 应为截至 10:00 的累计、`s_zrtj` 为 9/11 10:00 的值（≈5750 亿，增量应 ≈+857 亿
    而非原算法的 +6631 亿）。
  - ⚠️ 部署当时生产东财全市场**仍在熔断**（`page=29/30 东方财富接口返回异常`）→ 自算口径会少算，
    实时接口不受影响，正体现本次改动价值。
  - ⚠️ 记录两点：① `fetch_kpl_doc110` 注释自称"实时接口"**名不符实**（只有日级 125 条，
    试遍 `st/Period/Type/apiv` 均无分时），已改注释；② 非交易日 `market.date` 仍标当天
    而 amount 是上一交易日的值（既有行为，非本次引入，未动）。

- **v4.11.19 (09-14) 首页筛选合并「涨停率/评分」为「分数」+ 隐藏抢筹列（09-14 01:10 已推生产）**

- **起因**：主人指出「首页 AI 选股筛选里的涨停率其实就是分数吧」—— 代码证实：
  `filters.js:117` 的 `probLt`(涨停率) 与 `:119` 的 `scoreFloor`(评分) **比的是同一个字段 `probability`**，
  属重复筛选项。
- **改动（纯前端）**：
  - `FilterPanel.vue`：原「涨停率 ≥□% 或 可信度 ≥□%」改为「**分数 ≥□ 分**」，绑定 `scoreFloor`
    （沿用原「评分」的**单阈值硬门槛**，默认 80，0=不限）；**删除原「评分 ≥□ 分」行**。
    → **选股名单与改动前完全一致**，只是界面从两项合并为一项，消除"两个数其实是同一个"的困惑。
  - `StockTable.vue`：抢筹列用 `SHOW_QC = false` 开关隐藏（colgroup / th / td **三处同源**），
    **保留全部实现**，改回 `true` 即恢复。
- **为什么「分数」接硬门槛而不是原 probLt**：主人二选一定为「80 分 / 沿用原评分硬门槛」。
  若改为 `probLt=80`（双低软门槛），"分数<80 但可信度高"的票会入选 → 名单变多；
  接 `scoreFloor` 则行为零变化。
- ⚠️ **遗留耦合（未动，需后端配合才能解）**：`probLt/confLt` 不再有 UI 入口，但仍按默认
  **65/65** 传给后端 → 用户把「分数」调到 **65 以下**时，`"分数<65 且 可信度<65"` 的
  **双低判据会额外生效**（既有行为，非本次引入）。彻底解耦需改后端 `scorer.py` 默认值，待主人发话。
- **验证**：前端单测 **44/44 全过**；构建 700 模块通过（新入口 `index-PlTDAlqZ.js`）；
  产物 grep：`分数 ≥` 在、`评分 ≥` 与 `涨停率 ≥` **均为 0**；测试机 dist 原子替换
  1021 文件、HTTP 200。
- **生产部署 (09-14 01:10, commit `a6fc909`)**：
  - 前置只读审计：`kuaixuan`/`kx-worker`/`nginx` 均 active；站点根 `/opt/kuaixuan/dist`；
    线上为**旧版**（`评分 ≥`/`涨停率 ≥` 各 1 处、`分数 ≥` 0 处）；磁盘余 11G。
  - 流程：本地打包 35M → 上传 `/tmp` → 解压暂存校验（1021 文件 / `分数 ≥`1 / 旧字段 0）
    → **同分区落地** `/opt/kuaixuan/dist_new_20260914` → `mv` 旧 dist 备份
    （`dist_bak_prod_20260914_0110`，1021 文件、`评分 ≥` 1 处可回退）
    → `mv` 原子换入 → `chmod -R a+rX`（生产 umask=027）。
  - 部署后校验：`nginx -t` OK；https 根 **200**、`/assets/index-PlTDAlqZ.js`+`index-4fv2yCHd.css`
    **均 200 且磁盘存在**；含「分数 ≥」的懒加载 chunk `StockView-4lDNxADR.js` **200**；
    `col-qc` **0 处**（抢筹 colgroup/表头已删）；三服务仍 active；/tmp 已清。
  - ⚠️ 生产**真浏览器验证未能执行**：本机到 `kuaixuangu.cn` 的 HTTP 出口不可达
    （DNS `ERR_NAME_NOT_RESOLVED`，走 127.0.0.1:18080 代理 `ERR_PROXY_CONNECTION_FAILED`）
    → 改用**远端 curl 全量资源校验**替代；界面行为由**测试机同 hash 产物真浏览器验证**背书
    （Edge + shiren 登录，筛选显示「…分数≥80分…」且表头无抢筹）。
- **回滚（生产）**：`mv /opt/kuaixuan/dist /opt/kuaixuan/dist.bad && mv /opt/kuaixuan/dist_bak_prod_20260914_0110 /opt/kuaixuan/dist`；
  或前端改回 `SHOW_QC = true`。
- ⚠️ 构建踩坑：本地 `vite build` 清空 dist 时触发**环境批量删除守卫**（1017 文件 > 阈值 50）
  → 先把旧 dist `mv` 改名再构建即可绕过（**700 模块已编译通过**，守卫只是清目录失败，非代码错误）。

- **v4.11.20 (09-14 盘中) 两市量能「较昨日」基准取错字段 —— 开盘啦字段语义纠正**
  （主人反馈「我看成交量还是对比的上个交易日收盘的量」→ 只读排查后定位）：
  - **症状**：首页「两市资金」显示「缩量 **9505** 亿」，正确应为「缩量 **1446** 亿」——
    基准被当成了**昨日收盘全天量**。
  - **根因**：9/13 接 `MarketSCLN` 时把两个字段**判反** —— 实际 `s_zrtj` = 昨日**全天**、
    `s_zrcs` = 昨日**同一时点**；而代码用 `s_zrtj` 当同期基准（`prev_full` 又错取了 `s_zrcs`）。
  - **铁证（9/14 11:05 生产实测，单位亿元）**：
    `last` = 10214.41（**== `trends` 末行[1] 今日累计**）、
    `s_zrcs` = 11660.49（**== `trends` 末行[2] 昨日同期**）、
    `s_zrtj` = **19718.98**（**恰等于 9/11 全天实测 19716.63**，取自 `settings.market_brief_last`）、
    `s3_zrtj` = 18248.84（≠ 前 3 日同期 10992.33 → 实为前 3 日**全天均值**）。
    `trends` 行 = `[时刻, 今日累计, 昨日同期, 前3日同期, 完成度%, 预测串, …]`（96 行 09:30→11:05）；
    09:30 首行「昨日同期」= 179.42 亿，远小于昨日全天 19719 亿 → 自洽。
  - **为什么 9/13 没发现**：在**周日**验证，收盘后「同期」退化为「全天」，
    两字段完全相等（当时均为 164714782）→ **看不出任何破绽**；错误理解还被写进了
    docstring / 测试 fixture / 文档，**11 条单测全绿也没兜住**。
  - **修复**（`backend/app/services/kpl.py`）：`prev_same_time ← s_zrcs`、`prev_full ← s_zrtj`；
    `prev3_same_time` 改从 **`trends` 末行[3]** 取（`s3_zrtj` 是全天均值，不可用）；
    新增**自检** —— 解析时若「同期 > 全天」（盘中不可能）**丢弃同期值并告警**；
    fixture 换成 **9/14 盘中真实返回**并加**语义方向断言** `prev_same_time < prev_full`（11 → **14 例**）。
  - **影响面（grep 全仓确认）**：仅 `kpl.py` 三行映射 + 测试 fixture；
    前端 `SentimentPanel.vue` 逻辑本身正确（`last_same_time || last`），是喂进去的数据错；
    **选股链路零影响**（今日量 `last` 一直取对）。
  - 文档同步：`features.md` / `api.md` / 本条目 / `kpl.py` docstring 全部纠正。
  - 🔑 **教训：字段语义只能在「活跃窗口」实测钉死** —— 非交易日/收盘后请求会让
    「同期」与「全天」**退化重合**（两值相等），此时下的任何语义结论都是赌。
  - **测试机部署（09-14 11:44，代码根 `/opt/kuaixuan/backend`）**：
    备份 `/opt/kuaixuan/backup/volfix_bak_20260914-114431.tar.gz`，MD5 双 OK；
    **重启前预检**（代码断言 + 真实接口数据）PASS ——
    `prev_same_time=12748.48 < prev_full=19718.98`（语义方向正确）；
    重启后三服务 active；**复验** `market.amount=11043.11` /
    `last_same_time.amount=12748.48` / `diffAmt=−1705.37 亿`（修复前为 −8675 亿）；
    **真浏览器（Playwright + Edge，shiren 登录）**实测面板显示
    「两市资金 11043亿 / **缩量 1705亿**」✓。全量 pytest **979 passed / 0 failed / 0 error / 4 skipped**（976 → 979）。
  - **生产部署（09-14 11:52，代码根 `/opt/kuaixuan/backend`，venv `/opt/kuaixuan-venv`）**：
    备份 `/opt/kuaixuan/backup/volfix_bak_prod_20260914-115235.tar.gz`（旧 `kpl.py` md5
    `b09ca8a2` / 新 `a2ac0955`），MD5 双 OK → **重启前预检** PASS
    （`prev_same_time=12748.48 < prev_full=19718.98`，语义方向正确；
    今日此刻 11043.11 亿，较昨日同时点 **−1705.37 亿**）→ 重启三服务 active →
    **复验**（`build_market_brief_payload` + 服务端直播缓存 `market_brief_payload`）
    `amount=11043.11 volSrc=kpl`、`last_same_time=12748.48 (src=kpl)`、
    `diff=−1705.37 亿`（修复前 −8675.87 亿，**虚高 5 倍**）。
    HTTP 探针：https 根 200 / `/api/kpl/market-brief` 401（未带 token，路由存活）。
    **选股链路零影响**（本次仅量能展示口径）。回滚：
    `cd /opt/kuaixuan/backend && tar xzf /opt/kuaixuan/backup/volfix_bak_prod_20260914-115235.tar.gz && systemctl restart kuaixuan kx-worker`。

- **v4.11.21 (09-14 午间) 首页筛选「流通市值」两格合并为区间一格 `xx ≤ 流通 ≤ yy`**
  （主人需求「现在流通的大于和小于筛选是分开的，能否改为 xx < 流通 < yy」）：
  - **改动**（纯前端，`frontend/src/components/FilterPanel.vue`）：原「流通 ≥□亿」+「流通 ≤□亿」
    两个 `.filter-cell` → 合并为一格：`<input 下限> <span class="mv-range-op">≤ 流通 ≤</span>
    <input 上限> 亿`。新增 `.mv-range-op` 样式（桌面 3px / ≤768px 2px / ≤480px 1px 边距）。
  - 🔴 **符号必须是 `≤` 不能是 `<`**：`utils/filters.js` 的判据是**非严格**
    （`mv < floor → 剔除` / `mv > ceil → 剔除`，等价于保留 `floor ≤ mv ≤ ceil`）。
    首版照需求字面写成严格 `< 流通 <` → **文案与真实行为不符**；主人当场指出
    「要是大于等于和小于等于」，同日修正为 `≤ 流通 ≤`。
    → **教训：UI 比较符号必须与判据的严格性一致，不能照抄需求里的字面写法**。
  - **零影响的硬保证**：绑定字段仍是 `filterSettings.floatMvFloor`（下限）与 `floatMvGt`（上限），
    **未新增/删除/改名任何字段** → `utils/filters.js` 过滤语义（两端 0=不限）、
    `buildFilterParams` 后端传参、偏好持久化与「历史条件自动应用」**全部不变**。
  - **验证**：前端单测 **46/46 通过**（44 + 新增 2 例**边界含**用例）；构建 700 模块通过
    （最终入口 `index-CExq6Nrm.js`）；产物 grep：`mv-range-op` 在、`≤ 流通 ≤` 在、
    `< 流通 <` **0**、`流通 ≥` **0**。
    ⚠️ `流通 ≤` 不能再当旧文案判据 —— 它是新文案 `≤ 流通 ≤` 的**子串**（易误报残留）。
  - **新增 2 例「边界含」单测（本次改动的核心证据）**：`passLockedFilter` 与
    `pickFromSnapshot` 是**两段重复判据**，各锁一条 —— `mv == 下限/上限` 必须**保留**，
    `29.99 / 200.01` 必须**剔除**。这样"面板显示 ≤"与"真实判据"被绑死，
    将来谁把判据改成严格不等号，测试立刻红，不会静默漂移。
  - **测试机部署（09-14 12:40 最终版，`/opt/kuaixuan/dist`）**：备份 `dist.bak.20260914-124040`
    （**改动前的旧版真身在 `dist.bak.20260914-115954`**，entry `index-PlTDAlqZ.js`）→ 暂存断言
    （`new_marker=1 / new_le=1 / old_lt=0 / old_ge=0 / css=1`，不通过不换）→ 同分区原子替换 →
    `files=1021 / root=200 / entry=200`；内容校验 `StockView-srOhE6pV.js 非严格=1 严格=0 旧下限=0`。
    首版合并版（12:15）另做过**全量 md5 逐文件比对**：本地 1021 vs 远端 1021 **逐字节一致**。
    ⚠️ 首版部署脚本被跑了两次（11:59:54 + 12:15:50）→ 因设计是"暂存断言 → 同分区 rename"，
    重放**幂等无副作用**，只多出一个备份目录；本轮已审计确认只跑一次。
  - **真浏览器实测**（Playwright + Edge，shiren 登录）：筛选第二行 **5 格**，第 2 格文本
    **`≤ 流通 ≤`**（不含 `<`）且含 2 个输入框（30 / 200，即该账号已存偏好，证明绑定+持久化正常）；
    `流通 ≥` 与 `< 流通` **均 0 处**；同行「分数 ≥ / 股价 ≤ / 竞额 ≥」符号未受影响
    （按格打印：竞涨 ≤7% / 分数 ≥80 分 / ≤流通≤ 30-200 亿 / 股价 ≤100 元 / 竞额 ≥2000 万）。
  - **功能实测**：`100 ≤ 流通 ≤ 300` 应用后命中 0 只（区间内无票，符合预期）；
    恢复 `30 ≤ 流通 ≤ 200` 后票（远望谷 53.50 / 众泰汽车 / 闽东电力 117.50）
    **全部落在区间内** → 上下限双向生效。
  - ⚠️ **UI 边界值未做真值实测**（如实说明）：表格显示值经格式化（2 位小数），
    拿它当区间端点无法区分"严格/非严格"（精度误差会掩盖结论）→ 边界含性改由
    **源码判据 + 上述 2 例单测**兜底，证据链更可靠。
  - ⚠️ **踩坑（自省）**：首次功能验证把 `.stock-table` 当成唯一表格 → 首页左右两栏各有表，
    列索引串位导致误报 FAIL；修正为 `.home-col-left .stock-table` 后通过。
    **教训：首页类页面的定位器必须带列作用域前缀（`.home-col-left` / `.home-col-right`）**。
  - ⚠️ **踩坑**：本机 bash 的 `PATH` 中途丢失（`dirname/ls/head` 全部 command not found），
    需显式 `export PATH=...PortableGit/.../usr/bin:/usr/bin:/bin:$PATH` 才能恢复。
  - **生产部署（09-14 13:09，`/opt/kuaixuan/dist`）**：备份 `dist_bak_prod_20260914-130851`
    （39M；改动前的旧版入口 `index-PlTDAlqZ.js` = v4.11.19）→ 打包 34.9MB 上传 `/tmp` →
    解压暂存**先断言**（`unpacked=1021 / new_marker=1 / new_le=1 / old_lt=0 / old_ge=0 / css=1`，
    不通过不换）→ 同分区 `mv` 原子替换 → `files=1021 / entry=index-CExq6Nrm.js`。
    **线上实取校验**：`https 根 200`、入口 js 200、css 200、懒加载 chunk `StockView-srOhE6pV.js` 200、
    远程 curl 取 `≤ 流通 ≤` 命中 / `< 流通 <` 为空；`nginx -t` ok；三服务 active。
    **全量 md5 逐文件比对**：本地 1021 vs 生产 1021，**仅本地 0 / 仅生产 0 / 内容不一致 0 → 逐字节一致**
    （即与测试机已验证产物等价，行为背书成立）。
  - 🔴 **踩坑（本轮新增）**：从 Windows 打包上传的 tar **带 666 权限**（`tarfile.add` 继承 Windows mode）
    → 生产 1021 个文件**全部世界可写**（旧版是 644）。`chmod -R a+rX` **只加读权、不会去掉写权**，
    所以 `a+rX` 挡不住这个 → 必须显式 `find -type d -exec chmod 755 {} +` /
    `find -type f -exec chmod 644 {} +` 规整。已修正，世界可写文件数 1021 → **0**。
  - **部署脚本已加幂等守卫**：若线上已含目标标记且入口相符 → 跳过备份/替换直接验证，
    避免重放时多出备份（上次测试机被跑两次的教训）。
  - **回滚**：`cd /opt/kuaixuan && mv dist dist.bad && mv dist_bak_prod_20260914-130851 dist && chmod -R a+rX dist`。
