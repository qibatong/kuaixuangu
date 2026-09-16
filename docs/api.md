# 接口与数据表

鉴权：`Authorization: Bearer <token>`，401 前端自动跳登录。

## 选股 / 用户

`/api/stocks`（lock/filter/refresh/ping + mode=auction/spot；**v4.11.22 起交易日 9:00-9:26 一律返回 `{"ok":false,"blocked":true,"msg":"9:26 后开放 · 正在等待 9:25 竞价定格","blockedUntil":"09:26","list":[],"count":0}` 且不跑选股/不落批次/不推送** —— 时间维 + 当日 9_25 已落库双闸门，开关 `pick_window_guard`；`action=ping` 在闸门之前 return 不受影响，历史回看走 batches 接口同样不受影响）、`/api/login`（返回 expire_at/expired/member_level，未验证邮箱 401 `need_verify_email`；**成功 Set-Cookie `kx_token`**，地址栏直接访问 `/aipick/*` 自动鉴权）、`/api/register`（**邀请码非必填**，新用户默认 7 天；带邀请码则被邀人 +7 天、邀请人 +7 天）、`/api/verify-email` / `/api/resend-verify`（邮箱认证）、`/api/change-password`、`/api/forgot`（邮箱找回）、`/api/reset`、**`/api/sms/send` / `/api/sms/verify`**（阿里云短信验证码：无登录鉴权，防刷同号 60s + 同 IP 60s/10 次）、**`/api/forgot-phone/send`**（找回密码发码，仅已绑定手机号发送，未绑定 404 省短信费）、**`/api/reset-by-phone`**（短信找回：阿里云闭环校验 + 改密 + 踢下线 + 防重放 5min）、`/api/history`、`/api/invite`、`/api/prefs`（合并保存，不覆盖其他字段）、`/api/admin/*`（含 `/api/admin/users/expire` 续费、`/api/admin/users/expire-batch` 批量设到期、`/api/admin/users/member-level` 会员等级、`/api/admin/user-invites` 邀请关系、`/api/admin/users?keyword=` 支持用户名/手机/邮箱/微信名/备注/付款备注、`memberTab=all|member|paid|vip|normal|admin`）

**`GET /api/picker/snapshot`**（v4.11.13，VIP/付费门禁）：全市场预计算快照（物化表 `stock_score_daily` 读侧），
5557 行 ≈2MB、进程内缓存 60s，供前端浏览器内本地筛选；开关 `frontend_local_filter` 默认 0
（关时不返回数据，前端静默回退后端筛选路径）。

## AI 预测（/api/aipick/）

| 接口 | 说明 |
|---|---|
| latest | 最新预测报告 HTML（**仅 VIP/付费/管理员**，Bearer token / cookie / `?token=` 鉴权） |
| detail/{date} | 指定日期报告 HTML |
| dates | 历史报告日期列表 |
| data | 最新报告 JSON（`{date,count,top:[...]}`） |
| data/{date} | 指定日期 JSON（历史自动补当日涨跌幅 day_change） |
| **auth-check** | **Nginx auth_request 子请求校验**：静态 `/aipick/*.html` 放行仅 VIP/付费/管理员（匿名 401 / 免费 403），token 来源 X-Original-Authorization → X-Original-URI `?token=` → 自身 query/Authorization → `kx_token` cookie |

> 静态报告访问：`https://www.kuaixuangu.cn/aipick/latest.html`（登录 cookie 或 `?token=` 分享链接）

## 开盘啦 / 选股宝（/api/kpl/）

| 接口 | 说明 |
|---|---|
| sentiment | 市场情绪（涨停家数/**跌停家数**/情绪值/连板高度/大幅回撤） |
| market-brief | **市场概览**：两市成交额 + 涨跌家数分布（xuangubao）+ **较昨日同时刻对比**（last_same_time）。**v4.11.18 起成交额与同时点基准改取开盘啦实时接口 `MarketSCLN`**（开关 `market_vol_rt`，取不到完全回退自算；原自算口径含 09:30 零值脏点与东财分页失败静默少算两个硬伤）。🔴 **v4.11.20 纠正取数字段**：基准取 `s_zrcs`（昨日**同一时点**），`s_zrtj` 是昨日**全天**（9/13 初版判反 → 基准退化成全天量，虚高 6.6 倍）；解析时自检「同期 > 全天」即丢弃同期值并告警 |
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
| yidong-realtime / yidong-monitor / yidong-multi / yidong-hot | **异动监管**：异动实时（含偏离值 change/days/deviation/target）/ 重点监控 / 多次异动 / 热门股偏离值 |
| interfaces | 接口索引（运行时查询，返回全部接口 name/title/called） |

## 统计（/api/stats/）

`auction-overview`（多时点对比卡）、`auction-snapshot`（时点个股）、`performance`、`daily-yizi`、`bid-snapshot`、`bid-snapshot-3points`（三时点封单榜）、`seal-quality`（封单数据质量报表）

## 数据表

| 表 | 说明 |
|---|---|
| users / batches / batch_stocks | 用户（含 expire_at 到期时间戳、member_level 等级、**register_ip/register_ua 防刷、email_verified 邮箱认证、phone 手机号**）/ 选股批次（含 **auto_applied** 自动应用标记；**user_id=0 + auto_applied=1 = 系统自动批次**，9:25 后 worker 自动跑，历史回看对所有用户可见）/ 批次明细 |
| snapshot_bid | 四时点全市场快照（date+time_point+code，含 float_mv/board） |
| snapshot_lastsec | 最后一秒高频采样（date+code+ts，差值回退用） |
| qc_snapshot | 竞价抢筹结果快照（date+code，含 bid_ratio 竞额昨比，非竞价时段读库展示） |
| auction_daily_history | 竞价异动日终快照（date+tab+list：seal/boom/qiangcang/yest_zt/yest_broken/broken_yest/broken_today，历史回看数据源） |
| stock_concept | 概念映射表（concept_refresh 每半小时采集写库，竞价各接口读库取概念） |
| close_change_history | 收盘涨跌幅历史（现涨口径统一，收盘后/历史回看用） |
| lhb_history | 龙虎榜日终快照（date+list，接口不支持历史，必须落库） |
| ladder_history | 连板梯队日终快照（date+pid_type+list） |
| daily_yizi | 每日一字涨停汇总 |
| settings | 全局默认筛选参数 / 评分权重 / **两市分时快照（market_brief_intraday_{date}）** / 两市收盘快照（market_brief_last） |
| reset_tokens | 密码重置令牌 |
| tokens | 登录令牌（含 **revoked 踢出标记**，新登录踢旧会话） |
| sms_verify_codes / sms_verify_consumed | 短信验证码（阿里云发送记录 / 防重放消费标记，scene=register|login|forgot） |
| hot_rank_history | 人气热榜日终快照（date+source+list，3 源 kpl/em/ths 回看） |
| daily_sector_top | 板块轮动日终 TopN（date+source+boards，15:30 调度落库） |
| stock_score_daily | **全市场预计算物化表**（PK date+code，v4.11.11）：9:25 定格后一次性算好全市场评分与定格字段（竞涨/换手/强度/市值/昨涨），1.5s / 5557 只；缺失写 NULL 不写 0、< 500 行不落表；读写开关 `precompute_read`/`precompute_write` |
| stock_float_mv_daily | **流通市值日频缓存**（PK date+code，数值列可空，v4.11.12）：当日东财 f21/f117 → 本表 ≤15 天 → 腾讯 f44（亿→元）；9-13 已从历史快照回填 117713 行 / 22 个交易日 |
| kv_cache | **跨进程状态存储**（CacheStore：缓存/限流/调度去重/分布式信号量，`CACHE_BACKEND=sqlite` 时使用） |
| task_queue | 异步任务队列（worker 进程消费，Phase1 落库仍同步，框架就绪） |
