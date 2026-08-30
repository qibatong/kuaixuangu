# 接口与数据表

鉴权：`Authorization: Bearer <token>`，401 前端自动跳登录。

## 选股 / 用户

`/api/stocks`（lock/filter/refresh/ping + mode=auction/spot）、`/api/login`（返回 expire_at/expired/member_level，未验证邮箱 401 `need_verify_email`）、`/api/register`（**邀请码非必填**，新用户默认 7 天；带邀请码则被邀人 +7 天、邀请人 +7 天）、`/api/verify-email` / `/api/resend-verify`（邮箱认证）、`/api/change-password`、`/api/forgot`、`/api/reset`、`/api/history`、`/api/invite`、`/api/prefs`（合并保存，不覆盖其他字段）、`/api/admin/*`（含 `/api/admin/users/expire` 续费、`/api/admin/users/expire-batch` 批量设到期、`/api/admin/users/member-level` 会员等级、`/api/admin/user-invites` 邀请关系、`/api/admin/users?keyword=` 支持用户名/手机/邮箱/微信名/备注/付款备注、`memberTab=all|member|paid|vip|normal|admin`）

## 开盘啦 / 选股宝（/api/kpl/）

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
| yidong-realtime / yidong-monitor / yidong-multi / yidong-hot | **异动监管**：异动实时（含偏离值 change/days/deviation/target）/ 重点监控 / 多次异动 / 热门股偏离值 |
| interfaces | 接口索引（运行时查询，返回全部接口 name/title/called） |

## 统计（/api/stats/）

`auction-overview`（多时点对比卡）、`auction-snapshot`（时点个股）、`performance`、`daily-yizi`、`bid-snapshot`、`bid-snapshot-3points`（三时点封单榜）、`seal-quality`（封单数据质量报表）

## 数据表

| 表 | 说明 |
|---|---|
| users / batches / batch_stocks | 用户（含 expire_at 到期时间戳、member_level 等级、**register_ip/register_ua 防刷、email_verified 邮箱认证**）/ 选股批次（含 **auto_applied** 自动应用标记）/ 批次明细 |
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
| kv_cache | **跨进程状态存储**（CacheStore：缓存/限流/调度去重/分布式信号量，`CACHE_BACKEND=sqlite` 时使用） |
| task_queue | 异步任务队列（worker 进程消费，Phase1 落库仍同步，框架就绪） |
