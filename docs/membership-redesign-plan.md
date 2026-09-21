# 会员体系重构方案（待主人确认后实施）

> 2026-09-21 起草。**尚未实施**——本文是供主人审阅的实施方案，确认后才动手。
> 所有「现状」判断均基于源码核实，标注了文件与行号；「建议」与「现状」严格分开。

---

## 0. 主人已确认的决策

| # | 决策项 | 结论 |
|---|---|---|
| 1 | 注册方式 | 手机号 + 短信验证码；**放弃邮箱验证** |
| 2 | 新用户赠送 | **5 天 `member_level=1` 完整体验**（不是空壳试用） |
| 3 | 邀请奖励 | 邀请人得 **5 天**；被邀请人同样得 5 天 |
| 4 | 「只能 1 次」 | **A+B+C 全防**（见 §2.3 手机号防刷） |
| 5 | 范围 | 第一批（注册邀请）+ 第二批（会员体验）**一起做** |
| 6 | 配额模式 | **方案 B**：配额 + 签到/邀请送额度 |
| 7 | 部署 | 先测试机验证，通过后由主人决定是否上生产 |

---

## 1. 现状核实（源码事实，非推测）

### 1.1 已具备的基础（零成本复用）

| 能力 | 位置 | 说明 |
|---|---|---|
| `phone` 字段 + 唯一索引 | `database.py:99,133` | 手机号唯一性已保证 |
| 短信服务 | `services/sms_verify.py` | 阿里云号码认证·短信认证，`send_code/check_code` 已跑通（找回密码在用） |
| 短信场景参数 | `sms_verify.py:53,88` | `scene` 参数已支持，注册场景可直接用 |
| 用户创建（带赠送天数） | `users.py:362` `create_user(expire_days=N)` | 逻辑现成，只需改配置值 |
| 邀请奖励函数 | `users.py:157` `grant_invite_reward(days=5)` | **已写好，默认就是 5 天** |
| 邀请防刷判断 | `users.py:178` `invite_reward_blocked` | 同 IP 自邀拦截 + 同 IP 邀 ≥3 人拦截 |
| 邀请码生成 | `users.py:353` `gen_unique_invite_code` | — |
| 计数原语 | `cache_store.py:58,158` `store.incr(key, ttl)` | **固定窗口原子自增**，跨进程共享 |
| 会员识别（带缓存） | `security.py:134` `_is_privileged_user(uid)` | 识别 管理员/会员/VIP，60s 缓存 |
| 选股使用记录 | `database.py:416-423` | `batches` 表**已有 `user_id` 列 + 索引** |
| 配置读写封装 | `services/settings.py` `get/set` | settings 表 JSON key-value |
| 鉴权依赖注入 | `deps.py:39,64` | `get_uid` / `require_vip_or_paid` 范式 |
| 北京时间惯例 | `kpl.py:26` | `time.gmtime(time.time() + 8*3600)` |
| 前端会员 store | `stores/user.js:44-52` | `isMember/isVipOrPaid/memberLabel` |
| 门禁组件 | `components/VipGate.vue` | 已有，可复用做引导 |

### 1.2 关键缺陷（本次要修的）

| 缺陷 | 事实 |
|---|---|
| **注册关闭** | 后端 `/api/register` 恒返 403（`auth.py:80-85`）；**前端压根没有注册 UI**（不是注释，是不存在） |
| **邮箱验证拦截** | `auth.py:47-52` 未验证邮箱直接 401，挡住登录 |
| **邀请奖励从未被调用** | `grant_invite_reward` / `invite_reward_blocked` 全库仅定义、**零调用点**。`docs/features.md:265` 声称「+7 天」与实际不符 |
| **赠送天数配置是 7** | `config.py:149-150`：`INVITE_REWARD_DAYS=7`、`NEW_USER_DAYS=7`，要改 5 |
| **无支付模块** | 全项目无订单/支付代码，`admin.py` 的 `pay_remark` 只是备注字段 |
| **无删号重注册防护** | `users` 删号后 `phone` 唯一索引释放 → 可反复注册刷 VIP |
| **后台偏薄** | 13 个端点，缺数据看板 / 到期预警 / 风控视图 / 审计日志 |
| **无配额体系** | 免费用户被 `VipGate` 一刀切挡住，无「尝鲜」路径 |

---

## 2. 功能设计

### 2.1 手机号验证码注册

**后端**：重写 `auth.py:80` `/api/register`
- 入参：`phone` / `code`（短信验证码）/ `password` / `invite_code`（可选）
- 校验：`sms_verify.check_code(phone, code, scene="register")`
- 手机号格式：复用 `users.py:289` `_is_phone`（`^1[3-9]\d{9}$`）
- 防刷：复用 `security.register_allowed(ip)`（1 小时 10 次）+ `register_ip_day_allowed(ip)`（24h 5 个）
- 创建：`users.create_user(..., phone=phone, email=None, expire_days=5)`，并**显式置 `member_level=1`**
- 赠送前查 `phone_claims`（见 §2.3）

**前端**：`LoginView.vue` 新增注册模式（现只有 `login|forgot|reset|verify`）

### 2.2 赠送 5 天 level=1 完整体验

- `config.py:150` `NEW_USER_DAYS` → **5**
- `create_user` 后**显式 `member_level=1`**（关键：`VipGate` 卡的是 `level>=1`，只延长 `expire_at` 不生效）
- 注册响应返回 `expire_at` + `member_level`，前端直接展示「已赠送 5 天会员」

### 2.3 手机号防刷（「只能 1 次」的完整落地）🔴

**新增表 `phone_claims`**：

```sql
CREATE TABLE IF NOT EXISTS phone_claims (
    phone         TEXT PRIMARY KEY,   -- 手机号
    first_uid     INTEGER,            -- 首次领取的 uid
    first_claim   INTEGER,            -- 首次领取时间戳
    claim_count   INTEGER DEFAULT 1,  -- 累计领取次数
    last_claim    INTEGER             -- 最近领取时间戳
)
```

**规则**：
- 注册赠送 VIP 前查 `phone_claims`：**已存在则不赠送**（仍允许注册使用，只是无 VIP）
- 不存在则写入并在 `claim_count` +1
- **删号不删此表** → 彻底封堵「注册→领 VIP→删号→再注册」

**三层防护对应关系**：
| 「只能 1 次」 | 落地机制 |
|---|---|
| A 每个新用户领 1 次 | 注册唯一性天然保证 |
| B 邀请人从同一人只拿 1 次 | `invited_by` 唯一 A 关系天然保证 |
| C 防同手机号反复刷 VIP | **`phone_claims` 表（本次新增）** |

### 2.4 邀请奖励（接上断掉的链路）

- `config.py:149` `INVITE_REWARD_DAYS` → **5**
- **新增调用点**：注册成功后，若带 `invite_code` 且解析出 `inviter_id`：
  1. 先 `users.invite_reward_blocked(inviter_id, invitee_ip)` 判断防刷
  2. 未被拦截则 `users.grant_invite_reward(inviter_id, days=5)`
- 前端新增**用户端邀请页**：邀请码、复制、二维码、战绩（已邀 N 人 / 已获 N 天）

### 2.5 删除邮箱验证

| 要删/改 | 位置 |
|---|---|
| 登录拦截 | `auth.py:47-52` |
| `/api/verify-email` | `auth.py:88` |
| `/api/resend-verify` | `auth.py:116` |
| 前端 verify 分支 | `LoginView.vue:31-42`、`189-223` |
| `api/auth.js:49-55` | 前端 API 封装 |

`email_verified` 等列**保留**（SQLite 老版本不支持 DROP COLUMN，见 `database.py:349` 注释）。

⚠️ **兼容性**：存量用户中若有 `email_verified=0` 被挡住的，删除拦截后即可正常登录。

### 2.6 功能配额（方案 B）

**计数器**：
```python
key = "quota:%s:%s:%s" % (uid, feature, bj_date())   # bj_date 用北京时间
n = store.incr(key, ttl=86400)                       # 固定窗口，跨日归零
```

**门禁依赖**（与现有 `require_vip_or_paid` 同构）：
```python
def quota_guard(feature, default_limit):
    def _guard(request: Request):
        uid = get_uid(request)
        if security._is_privileged_user(uid):     # 会员/VIP/管理员直接放行（不计数）
            return uid
        # 去重：同一功能 10s 内重复请求不重复计数（防 30s 轮询/切 tab 重放）
        if store.setnx("quota:dedup:%s:%s" % (uid, feature), 1, ttl=10):
            n = store.incr(quota_key(uid, feature), ttl=86400)
        else:
            n = current_count(uid, feature)
        if n > current_limit(uid, feature):
            raise HTTPException(429, {"ok": False, "code": "quota_exceeded",
                                      "feature": feature, "msg": "今日免费额度已用完，会员不限次"})
        return uid
    return _guard
```

**建议默认额度**（待主人确认，全部走配置可改）：

| 功能 | 免费用户 | 会员 |
|---|---|---|
| 选股快照 | **3 次/日** | 不限 |
| AI 竞价预测 | **1 次/日** | 不限 |
| 竞价异动页 | **1 次/日** | 不限 |
| 题材异动 / 涨停梯队 / 历史回看 | 不限（引流） | 不限 |

**额度加成（方案 B 核心）**：
```python
used  = store.get("quota:%s:%s:%s" % (uid, feature, bj_date())) or 0
bonus = store.get("quota_bonus:%s:%s" % (uid, feature)) or 0     # 签到/邀请累计
if used >= limit + bonus: 拒绝
```

| 行为 | 奖励 |
|---|---|
| 每日签到 | **+3 次选股额度** |
| 邀请新人成功 | **+5 天 VIP**（见 §2.4）/ 或 +10 次额度 |

> ⚠️ **签到送「额度」而非「VIP 天数」**——送天数会稀释会员价值，送额度既不稀释又形成「来→用→不够→签到」循环。

### 2.7 到期提醒 + 我的会员页

- 页面顶部横幅：剩余 ≤3 天时提示「会员还剩 N 天」
- 「我的会员」页：剩余天数 / 到期日 / 邀请战绩 / 续费联系入口（无支付，先做「加微信」落地）

---

## 3. 后台会员管理增强

### 现有 13 个端点（`admin.py`）
用户列表（含会员筛选 tab / 邀请列 / 选股次数）/ 改到期 / 批量改到期 / 改资料 / 建号 / 删号 / 改等级 / 重置密码 / 邀请关系 / 评分配置 / 默认值

### 建议新增

| 优先级 | 功能 | 说明 |
|---|---|---|
| 🔴 P0 | **数据看板** | 6 个数字：总用户 / 活跃 / 付费 / 今日新增 / 今日注册 / 今日到期 |
| 🔴 P0 | **到期预警筛选** | 「3 天内到期」「已过期」——催续费主要抓手 |
| 🔴 P0 | **注册风控视图** | 同 IP 注册数、同手机号重复领取、疑似自邀 |
| 🔴 P0 | **操作审计日志** | 谁改了谁的到期/等级、谁删了号 |
| 🟡 P1 | 一键 +30 天续费 | 现在只能填具体日期 |
| 🟡 P1 | 导出 CSV | 盘点与私域运营 |
| 🟡 P1 | 邀请奖励战绩 | 谁邀了几人、发了几次奖、被防刷拦了几次 |
| 🟡 P1 | 短信用量统计 | 短信是花钱的，注册开放后必须能看 |
| 🟢 P2 | 批量导入用户（CSV） | — |
| 🟢 P2 | 会员权益配置页 | 配额/赠送天数后台可改 |
| 🟢 P2 | 用户详情抽屉 | 注册 IP / 邀请链 / 选股记录 / 到期史 |

---

## 4. 数据变更清单

| 变更 | 类型 | 风险 |
|---|---|---|
| `phone_claims` 表 | 新增 | 无（新表） |
| `admin_audit_log` 表 | 新增 | 无 |
| `checkin` 表（签到） | 新增 | 无 |
| `users.member_level` 置 1 | 逻辑 | 中（注册流程） |
| 删邮箱验证拦截 | 逻辑 | **中**（恢复存量用户登录） |
| `email_verified` 等列 | **保留** | 无（SQLite 不支持 DROP COLUMN） |

所有迁移走 `database.py` 的 `init_db()` 幂等范式（`CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`）。

---

## 5. 实施顺序（建议）

1. **后端**：`phone_claims` 表 + 注册端点重写 + 短信 scene + 邀请奖励接线 + 删邮箱验证
2. **后端**：配额核心 `quota_guard` + 接入 3 端点 + 配置项
3. **后端**：签到 + 额度加成 + 审计日志 + 后台新端点
4. **前端**：注册 UI + 邀请页 + 配额弹窗 + 到期提醒 + 我的会员页
5. **前端**：后台看板 / 预警筛选 / 风控视图 / 配置页
6. **测试机验证** → 主人决定是否上生产

---

## 6. 待主人确认的开放项

1. **配额数值**：暂定「选股 3 次/日、AI 1 次/日、竞价异动 1 次/日」——是否认可？
2. **签到奖励**：+3 次选股额度 / 天，是否认可？（还是想换其他数值或形式）
3. **后台 P2 三项**：做还是砍？
4. **实施节奏**：一次性全做完再上测试机，还是分两批验证？
5. 存量用户处理：删除邮箱验证后，**是否需要给存量 `email_verified=0` 的用户补发 5 天**？

---

## 7. 明确不做（避免误解）

- ❌ 不做支付/订单系统（无支付牌照与对公账户，超出本项目范围）
- ❌ 不删 `email_verified` 列（SQLite 技术限制）
- ❌ 不做签到送 VIP 天数（会稀释会员价值）
- ❌ 不动存量用户的 `member_level` 与 `expire_at`（除非主人在 §6.5 明确要求）
