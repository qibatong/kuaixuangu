# 接口与数据表

鉴权：`Authorization: Bearer <token>`，401 前端自动跳登录。

## 会员 / 配额（/api/member/，2026-09-21 新增）

| 接口 | 说明 |
|---|---|
| **overview** | **「我的会员页」一次性拿全**：`member`（等级/到期/剩余天数/是否特权）+ `quota`（三功能剩余次数数组）+ `checkin`（今日是否已签/奖励/连续天数）+ `invite`（邀请码/已邀人数/累计获得天数/被邀人前 20） |
| **quota** | 配额状态（**不消耗**）：`quota` 三功能的 `{feature,label,limit,used,remain,bonus,privileged}` + `checkin`。供页面顶部**常驻展示剩余次数** |
| **checkin**（GET） | 签到状态（今日是否已签 + 连续天数） |
| **checkin**（POST） | 执行签到，送 `QUOTA_CHECKIN_BONUS`（默认 3）次选股额度；`(uid,date)` 唯一 → **天然防重**，重复签返回 `already` 不报错 |
| **plans** | 会员套餐价目表（**无鉴权**，登录页/开通弹窗可读） |

**配额体系**（`services/quota.py`，方案 B：CacheStore 固定窗口原子自增）
- key `quota:{feature}:{uid}:{date}`（北京日期，每日 0 点自然重置），TTL `86400+3600` 冗余
- 三功能基础额度：`picker` 3 次/日、`aipick` 1 次/日、`auction` 1 次/日（`config.QUOTA_*_DAILY`）
- **会员（`member_level>=1`）/ 管理员直接放行不计数**（`privileged=True`，`limit=-1`）
- **签到加成** 另存 `quota:bonus:{feature}:{uid}:{date}`，`今日额度 = 基础 + 加成`（`limit_of`）
- 🔴 **10 秒去重**（`QUOTA_DEDUP_SECONDS`）：同用户同 feature 窗口内重复请求**只计一次**——前端一次页面加载会并发打多接口（快照+列表），不去重会瞬间烧光配额（"只有 3 次却马上用完"的常见投诉来源）
- 🔴 **不在业务里抛 429**，统一由依赖 `deps.quota_guard(feature)` 抛，保证响应结构一致：`429 {"ok":false,"code":"quota_exceeded","feature","feature_label","limit","used","msg"}`，前端据 `code` 弹「开通会员」引导
- **存储故障时保守处置**：`_incr` 失败返回 `10**9`（**不放行但也不计入正常值**）—— 宁可短暂拦住免费用户，也不能因存储故障把配额体系彻底放开

## 选股 / 用户

`/api/stocks`（lock/filter/refresh/ping + mode=auction/spot；🔴 **v4.11.26 起本闸门已整体回退并从代码摘除**（`mode.is_pick_open` / `auction_snapshot.has_today_snapshot` / `stocks._pick_window_guard_on` / `ping.pickGateEnabled` 与前端置灰全部移除，**交易日 9:00-9:26 恢复可选股**，见 history v4.11.26；新口径重做见 v4.11.27）—— **以下 v4.11.22/v4.11.24 的描述为存档**：**v4.11.22 起交易日 9:00-9:26 一律返回 `{"ok":false,"blocked":true,"msg":"9:26 后开放 · 正在等待 9:25 竞价定格","blockedUntil":"09:26","list":[],"count":0}` 且不跑选股/不落批次/不推送** —— 时间维 + 当日 9_25 已落库双闸门，开关 `pick_window_guard`（**09-17 事故后生产已置 0**，见 history v4.11.24）；`action=ping` 在闸门之前 return 不受影响，历史回看走 batches 接口同样不受影响。**v4.11.24 起 `action=ping` 额外返回 `{"before930":bool,"pickGateEnabled":bool}`** —— `pickGateEnabled` 是后端开关的单一口径投影（由 `stocks._pick_window_guard_on()` 计算，显式解析 `0/"0"/"false"/"no"/"off"/""` 等字符串假值），**前端据此决定是否置灰**，不再自行判时间。**v4.11.25 起 `action=refresh`（9:30 后）的直读优先级为：用户当日同参 lock → 同参 filter → 当日系统统一批次（仅当用户当日无任何手动批次）→ 当日系统统一批次（`history.find_today_system_batch()`，**无视是否有手动批次**）→ 跨日 14 天窗口内最近同参批次**；命中倒数第二步时 `reusedDate` 为 `null`（它本就是当日名单，前端**不提示**"这是历史名单"），日志为「选股refresh当日无可用批次→回退当日系统统一名单」。该步的存在是为了不让「当日点过筛选但落了空名单批次」的用户在交易日看到昨日名单（见 history v4.11.25））、`/api/login`（返回 expire_at/expired/member_level，未验证邮箱 401 `need_verify_email`；**成功 Set-Cookie `kx_token`**，地址栏直接访问 `/aipick/*` 自动鉴权）、`/api/register/send`（注册发码，scene=register；**注册前即可调**，已注册手机号直接 400 省短信费）、`/api/register/config`（注册页配置：是否需要邀请码/协议文案）、`/api/register`（🔴 **2026-09-21 起改为手机号注册**：`{phone, code, password, invite_code?}`，短信验证码必过 → 送 **5 天 level=1 完整体验**；**邀请码非必填**，填写则**双方各 +5 天**；🔴 **每个手机号只能领 1 次新用户 VIP**（`phone_claims` 台账，**不随 users 删除而清理** → 删号重注册也刷不到））、`/api/verify-email` / `/api/resend-verify`（邮箱认证）、`/api/change-password`、`/api/forgot`（邮箱找回）、`/api/reset`、**`/api/sms/send` / `/api/sms/verify`**（阿里云短信验证码：无登录鉴权，防刷同号 60s + 同 IP 60s/10 次）、**`/api/forgot-phone/send`**（找回密码发码，仅已绑定手机号发送，未绑定 404 省短信费）、**`/api/reset-by-phone`**（短信找回：阿里云闭环校验 + 改密 + 踢下线 + 防重放 5min）、`/api/history`、`/api/invite`、`/api/prefs`（合并保存，不覆盖其他字段）、`/api/admin/*`（含 `/api/admin/users/expire` 续费、`/api/admin/users/expire-batch` 批量设到期、`/api/admin/users/member-level` 会员等级、`/api/admin/user-invites` 邀请关系、`/api/admin/users?keyword=` 支持用户名/手机/邮箱/微信名/备注/付款备注、`memberTab=all|member|paid|vip|normal|admin`）

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

## 管理端运营中心（/api/admin/*，2026-09-21 扩至 26 端点）

**前端入口**：`/admin` 页 → `MemberAdminPanel.vue`（7 Tab：概览 / 用户 / 风控 / 邀请 / 短信 / 到期 / 审计）
+ `UserDetailDrawer.vue`（用户详情抽屉）。

| 接口 | 说明 |
|---|---|
| **dashboard** | 运营概览卡：总用户 / 今日新增 / 会员分布 / 到期预警计数等 |
| **expiring** | 即将到期用户列表（按剩余天数升序） |
| **risk** | **风控**：同 IP 多账号 / 自邀嫌疑 / 异常注册密度等**风险用户聚合**（🔴 本页曾因 `dict(tuple)` 全线 500） |
| **invite-rank** | **邀请榜**：按成功邀请人数降序（🔴 同上，曾 500） |
| **sms-usage** | **短信用量**：按 scene 统计发送量 / 成功失败率 / 当日余量估算（成本跟踪） |
| **audit** | **审计日志列表**（`admin_audit` 表，支持按 action / target_uid 过滤） |
| **audit/actions** | 审计动作枚举 + 各动作计数（供前端下拉筛选） |
| **user-detail** | **用户详情抽屉**：单用户全量信息（会员 / 配额 / 邀请关系 / 批次 / 签到） |
| **users/export** | **导出用户 CSV**（带 UTF-8 BOM，Excel 直开不乱码；大量 `r["列名"]` 取值） |
| **users/import** | 批量导入用户（CSV） |
| **users/reset-quota** | **重置用户当日配额**（`quota.reset_user(uid, feature?)`，`bonus=False` 只清用量不动加成） |
| **users/extend-plus** | **加时+**（在现有到期时间上顺延，与「设到期」的绝对设定语义区分） |
| **member-conf**（GET/PUT） | **会员配置**：新用户赠送天数 / 邀请奖励天数 / 各功能每日额度 等运行时配置 |

> 🔴 **本文件的连接约定**：`admin.py` 内部统一用**自建 `_conn()`**（`sqlite3.Row`），
> **不能用 `database.get_conn()`** —— 后者不设 row_factory、返回 tuple，而本文件大量
> `dict(r)` / `r["列名"]`，会抛
> `TypeError: cannot convert dictionary update sequence element #0 to a sequence`（2026-09-21 T175 回归实测）。
> 同理适用于 `users.py` / `stats.py` / `history.py`（各自都有 `_conn()`）。

## 统计（/api/stats/）

`auction-overview`（多时点对比卡）、`auction-snapshot`（时点个股）、`performance`、`daily-yizi`、`bid-snapshot`、`bid-snapshot-3points`（三时点封单榜）、`seal-quality`（封单数据质量报表）

## 数据表

| 表 | 说明 |
|---|---|
| users / batches / batch_stocks | 用户（含 expire_at 到期时间戳、member_level 等级、**register_ip/register_ua 防刷、email_verified 邮箱认证、phone 手机号**）/ 选股批次（含 **auto_applied** 自动应用标记；**user_id=0 + auto_applied=1 = 系统自动批次**，9:25 后 worker 自动跑，历史回看对所有用户可见）/ 批次明细 |
| **phone_claims** | **注册手机号领取台账（2026-09-21）**：phone 主键 / first_uid / first_claim / claim_count / last_claim / last_ip —— 保证「每个手机号只领 1 次新用户 5 天 VIP」；🔴 **该表不随 users 删除而清理**（否则删号重注册 = 无限刷 VIP） |
| **user_checkin** | **每日签到（2026-09-21）**：PK (uid,date) 天然防重 / reward / created_at；索引 on date。免费用户签到送选股额度 |
| **admin_audit** | **后台操作审计（2026-09-21）**：管理员对用户的关键操作留痕（加时/改等级/删号/重置密码/重置配额等），含 target_uid / detail(JSON) / ip；索引 on created_at、target_uid |
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

**v4.11.27（09-17）闸门口径重做后的取值变化**：`blocked` 响应的 `blockedUntil` 不再是固定的 `"09:26"`，
而是按当前拦截段动态给出 —— 第一段给 `"09:15"`、第二段给 `"09:25:36"`（`mode.pick_resume_at()`）。
拦截文案同步更新为 `9:15 后开放 · 正在等待 9:25 竞价定格`（快照维仍是 `9:25 竞价定格尚未落库 · 稍后自动恢复`）。
时间段：`[09:00:00,09:15:00)` 与 `[09:25:00,09:25:35]` 拦截，**`09:15:00-09:24:59` 放行**。
`GET /api/stocks?action=ping` 的 `pickGateEnabled` 语义不变（=`settings.pick_window_guard`，默认 1）。
