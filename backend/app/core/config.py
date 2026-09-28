# -*- coding: utf-8 -*-
"""
全局配置
========
所有环境变量在此统一读取。部署时通过 systemd Environment= 注入。
"""
import os

# ---------- 服务 ----------
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8010"))
TOKEN_TTL = 12 * 3600                     # Token 有效期(秒), 默认 12 小时
TOKEN_TTL_REMEMBER = 30 * 24 * 3600       # 「记住我」Token 有效期(秒), 30 天免登录
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT", "400"))   # 每 IP 每分钟最大请求数 (2026-09-04 60→200→400: 首页 12-17 接口并发 + 30s 轮询 + 切 tab 重拉被 429 误伤; admin/VIP/付费账号见 security.rate_allow 旁路)
PBKDF2_ITERS = int(os.environ.get("PBKDF2_ITERS", "50000"))    # 密码哈希迭代次数
CACHE_TTL = int(os.environ.get("CACHE_TTL", "30"))             # 行情缓存新鲜度(秒)
# 管理员: 逗号分隔的用户名; 空则自动把 id 最小的用户设为管理员(种子账号)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")

# ---------- 路径 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # .../backend/app/core
BACKEND_DIR = os.path.dirname(os.path.dirname(BASE_DIR))       # .../backend
# 项目根 = backend/ 的上一级 (部署: /opt/bid-selector; 本地: bid-selector-server)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
# 数据库默认放在项目根, 可用 BID_DB_PATH 覆盖
DB_FILE = os.environ.get("BID_DB_PATH", os.path.join(PROJECT_ROOT, "kuaixuan.db"))
# 日志目录(默认项目根下 logs/), 可用 BID_LOG_DIR 覆盖
LOG_DIR = os.environ.get("BID_LOG_DIR", os.path.join(PROJECT_ROOT, "logs"))
# 连板天梯图片输出目录(每日盘后生成 PNG, 供 App 内查看/下载)
LADDER_IMG_DIR = os.environ.get("LADDER_IMG_DIR", os.path.join(PROJECT_ROOT, "ladder_images"))
# AI 竞价预测报告输出目录(独立项目 /opt/kuaixuan/aipick/output):
# 含 latest.html / predictions_YYYY-MM-DD.html 等, 原 Nginx 静态暴露, 现改为后端鉴权后经 App 内查看
AIPICK_OUTPUT_DIR = os.environ.get("AIPICK_OUTPUT_DIR", "/opt/kuaixuan/aipick/output")
# 2026-09-25: LightGBM 平行链路产物目录。两个模型的产物**必须**目录隔离 ——
# predict_daily.py 有"当日 json 已存在就不覆盖 + 每次覆盖 latest.html"两条语义,
# 共用目录会让它们互相判定"报告已存在"并互相覆盖首页报告。
AIPICK_LGB_OUTPUT_DIR = os.environ.get("AIPICK_LGB_OUTPUT_DIR", "/opt/kuaixuan/aipick/output/lgb")
# 飞书群总结 PDF 存储目录与上传密钥(2026-08-31): 定时任务上传总结 PDF, /s/<id> 公开预览
SUMMARY_DIR = os.environ.get("SUMMARY_DIR", os.path.join(PROJECT_ROOT, "data", "summaries"))
SUMMARY_UPLOAD_TOKEN = os.environ.get("SUMMARY_UPLOAD_TOKEN", "")

# ---------- 邮件重置密码 ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
RESET_TTL = int(os.environ.get("RESET_TTL", "1800"))
RESET_RATE_LIMIT = int(os.environ.get("RESET_RATE_LIMIT", "3"))

# ---------- 出站 IP 轮询池(2026-09-07 主人加辅助网卡防东财封单 IP) ----------
# 配置示例: OUTBOUND_IPS=121.196.230.80,101.37.204.78
# 空 = 走 OS 默认出站 IP(单 IP 场景, 如测试机)
# 配置在 /etc/kuaixuan/env.conf, 重启服务生效; fetcher._IPRotator 自动接管所有 urllib urlopen
OUTBOUND_IPS = [s.strip() for s in os.environ.get("OUTBOUND_IPS", "").split(",") if s.strip()]

# ---------- 阿里云短信验证码(号码认证·短信认证, 2026-08-30) ----------
# 个人开发者免资质; AccessKey 建议 RAM 子账号只授权 dypns; 走 systemd drop-in 注入
# 签名/模板为号码认证控制台「系统赠送」: 恒创联众 + 100001(赠送模板必须配赠送签名)
SMS_SIGN_NAME = os.environ.get("SMS_SIGN_NAME", "恒创联众")          # 控制台系统赠送签名名(不可自定义)
SMS_TEMPLATE_CODE = os.environ.get("SMS_TEMPLATE_CODE", "100001")    # 控制台系统赠送验证码模板编号
SMS_SEND_INTERVAL = int(os.environ.get("SMS_SEND_INTERVAL", "60"))   # 同号重发间隔(秒)
SMS_VALID_MIN = int(os.environ.get("SMS_VALID_MIN", "5"))            # 验证码有效期(分钟)

# ---------- 数据源(东方财富公开行情接口) ----------
EASTMONEY_URL = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
EASTMONEY_UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
FIELDS = "f2,f3,f4,f5,f6,f8,f10,f12,f14,f17,f18,f20,f21,f117,f615,f616,f617,f618,f630,f100,f102,f103"

# 盘中实时选股: 东财涨停池(封单/连板/炸板) + 涨停池缓存 TTL
EASTMONEY_ZT_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
EASTMONEY_ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"
ZT_CACHE_TTL = int(os.environ.get("ZT_CACHE_TTL", "15"))       # 涨停池缓存新鲜度(秒)
SPOT_CACHE_TTL = int(os.environ.get("SPOT_CACHE_TTL", "60"))  # 盘中实时行情缓存新鲜度(秒); 2026-08-18: 30→120; 2026-08-19: 120→300(分页并发后冷启动0.5s, 延长TTL减少冷启动频率, 与market-brief 5min一致); 2026-08-24: 300→60(主人要求盘中现涨刷新更快, 前端轮询同步 60s→30s)
SPOT_MAX_PAGES = int(os.environ.get("SPOT_MAX_PAGES", "30"))   # 盘中全市场分页拉取上限(每页200只; 30页=6000只覆盖全A+北交所)

# 昨日成交额(日K)抓取: 低并发 + 多域名轮询 + 熔断, 避免触发东财限流
YESTERDAY_FETCH_WORKERS = 4        # 2026-09-02 生产事故: 8 并发持续打爆源, 降 4
YESTERDAY_FETCH_TIMEOUT = 12   # 批量并发整体超时上限(秒), 超时未完成跳过(昨比置空), 防阻塞
                               # (2026-09-02: 20→12, 全量重试时卡顿减半)
YESTERDAY_RETRY_TTL = 600      # 昨比失败缓存重试窗口(秒): 失败也写当日缓存, 窗口内不重复拉取,
                               # 避免每请求重复拉全市场触发源限流(2026-09-02 生产事故根因;
                               # 180→600 降重试频率, 东财限流期 10 分钟才一次全量重试)
# ---------- 昨日涨幅(真实涨跌幅)一致性 (2026-09-08 修「top3 有时90有时93」漂移) ----------
# 漂移根因: 该因子权重 6%, 缺失走 default 0.15 / 命中走 0.4·0.65·0.9 → 单票概率差
# 1.5~4.5 分; 而命中率随"缓存回填进度 + 日K源熔断"在 0%~100% 之间跳, 同参数两次结果
# 分数不同(用户可见)。两条措施: ① 补齐重试 ② 批级一致性(宁可全缺, 不可半有半无)。
YDAY_CHG_RETRY_TTL = 1800      # 成交额对已成功但涨跌幅缺失时的**补齐**重试窗口(秒)。
                               # 比 YESTERDAY_RETRY_TTL 更懒: pair 已可用只差涨跌幅,
                               # 不必高频打扰数据源(预热若走了无涨幅的兜底源, 靠它回补)。
YDAY_CHG_MIN_COVERAGE = 0.6    # 批级一致性阈值: 本批涨跌幅命中率低于此值 → **整批按缺失
                               # 返回**(全部走 default), 杜绝同一份名单里部分票加分、
                               # 部分不加分 —— 名单内可比性优先于单票精度。设为 0 可关闭。
KLINE_TIMEOUT = 5
KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://33.push2his.eastmoney.com",
    "https://48.push2his.eastmoney.com",
    "https://92.push2his.eastmoney.com",
]

# ---------- 开盘啦(龙虎榜 App)数据源 ----------
# 付费接口(每日 80000 次), 用于竞价委买额/连板梯队/情绪值/涨停原因/板块强度等
# Token/UserID/DeviceID 通过 systemd Environment= 注入(不进代码库, 避免泄露)
KPL_TOKEN = os.environ.get("KPL_TOKEN", "")
KPL_USERID = os.environ.get("KPL_USERID", "")
KPL_DEVICEID = os.environ.get("KPL_DEVICEID", "")
KPL_UA = "Dalvik/2.1.0 (Linux; U; Android 14; V2178A Build/UP1A.231005.007)"
KPL_HOSTS = {
    "default": "apphwhq.longhuvip.com",       # 竞价委买额/连板梯队
    "market": "apphq.longhuvip.com",          # 情绪值/板块强度(实时)/热榜/涨停原因
    "his": "apphis.longhuvip.com",            # 板块强度(历史)+板块成分股
    "after": "apphwshhq.longhuvip.com",       # 板块强度(当天分时)+尾盘抢筹/竞价砸盘/竞价>2000万
    "lhb": "applhb.longhuvip.com",            # 龙虎榜
    # 2026-09-27 v4.11.59 补: 资讯域名。此前缺失 ⇒ `_call("article", …)` 静默回落 default
    #   (竞价域名 apphwhq), 实测返回**非 JSON** ⇒ json.loads 抛错被 _call 吞掉 → None。
    #   ⇒ doc95 头条 / doc96 7x24快讯 写了几个月却一次都没取到过数（"写进文档 ≠ 能力存在"）。
    "article": "apparticle.longhuvip.com",    # 资讯-头条(doc95)/新闻快讯(doc96)
    # 2026-09-27 同批补: 自动生成段(2026-08-13 那 87 个 fetch_kpl_docXX)里 7 处写成
    #   `_call("q", …)`, 而 "q" 从来不是合法键 ⇒ 同样静默回落 default。
    #   其 docstring 标注的真实域名是 apphq.longhuvip.com(与 market 同域) ⇒ 补别名而非改 7 处。
    "q": "apphq.longhuvip.com",               # 别名: 自动生成段用的 host_key
}
KPL_BID_TTL = int(os.environ.get("KPL_BID_TTL", "30"))        # 竞价委买额缓存新鲜度(秒)
KPL_BID_ST = os.environ.get("KPL_BID_ST", "200")              # Type4 涨停委买额榜条数(200=涨停榜, 实测更大值是否生效)
KPL_SENTI_TTL = int(os.environ.get("KPL_SENTI_TTL", "60"))    # 情绪值缓存新鲜度(秒)
KPL_LADDER_TTL = int(os.environ.get("KPL_LADDER_TTL", "60"))  # 连板梯队缓存新鲜度(秒)
KPL_BOARD_TTL = int(os.environ.get("KPL_BOARD_TTL", "30"))    # 板块强度缓存新鲜度(秒)
KPL_YIDONG_TTL = int(os.environ.get("KPL_YIDONG_TTL", "15"))  # 异动(偏离/重点监控/热门)缓存新鲜度(秒); 2026-09-04 加: 原无缓存每请求拉开盘啦 avg0.96s
KPL_MARKET_SCLN_TTL = int(os.environ.get("KPL_MARKET_SCLN_TTL", "60"))  # 实时市场量能缓存(秒); 2026-09-13 加: 盘中量能 60s 新鲜度足够, 且防打爆 8 万/日配额
# 2026-09-28 v4.11.79 加: 板块成分股缓存新鲜度(秒).
#   背景: fetch_board_stocks 原**无缓存**, 每次点板块/每次轮询都真打开盘啦;
#   前端本轮加轮询后, N 个客户端 × 盘中 ~330 次/客户端/日 会线性吃 8 万/日 配额。
#   加 30s 缓存后: 无论多少客户端, 单个板块每 30s 最多 1 次上游 ⇒ 一个交易日的
#   下游请求被压到 (240min×2) × 板块数 量级, 比"每客户端直打上游"省两个数量级。
#   30s 与 KPL_BOARD_TTL(板块强度) 对齐 —— 左右栏同频刷新, 不会出现"左边新右边旧"。
KPL_BOARD_STOCKS_TTL = int(os.environ.get("KPL_BOARD_STOCKS_TTL", "30"))
# 历史日成分股缓存: 历史数据**永不变化**, 给长 TTL(30 分钟)避免反复回读同一历史日。
KPL_BOARD_STOCKS_HIST_TTL = int(os.environ.get("KPL_BOARD_STOCKS_HIST_TTL", "1800"))

# ---------- 猫爪(meoz.cn)数据源 ----------
# 竞价数据新主源(2026-09-19 主人拍板全面替换开盘啦竞价依赖)。
# apikey 通过 systemd Environment= 注入(不进代码库, 避免泄露)。
MEOZ_APIKEY = os.environ.get("MEOZ_APIKEY", "")
# 专线(官方 SDK DEDICATED_API_URLS): sz/sh 双线互为备份。
# 公网 https://numcat.net/api 因 SSL 证书验证失败不可用, 不列入默认。
MEOZ_LINES = (
    os.environ.get("MEOZ_LINE_SZ", "http://sz.numcat.net:8866/api"),
    os.environ.get("MEOZ_LINE_SH", "http://sh.numcat.net:8866/api"),
)
# 竞价数据落库时刻(套餐表标注): 猫爪竞价数据 9:25:45 才更新。
# 选股闸门(见 picker/mode.T_PICK_BLOCK_TO, **现 09:26:30**)必须 ≥ 此值 + 缓冲, 否则会取到
# 上一交易日定格 —— 「拿到猫爪数据再定格」正是把闸门与定格枪一起推到 09:26:30 的依据。
MEOZ_AUC_READY = os.environ.get("MEOZ_AUC_READY", "092545")
MEOZ_BID_TTL = int(os.environ.get("MEOZ_BID_TTL", "30"))       # 竞价数据缓存新鲜度(秒)
MEOZ_AUC_TTL = int(os.environ.get("MEOZ_AUC_TTL", "6"))        # 竞价窗口内逐分钟数据缓存(秒)
MEOZ_TICK_TTL = int(os.environ.get("MEOZ_TICK_TTL", "10"))     # tick 数据缓存新鲜度(秒)

# ---------- 推送提醒(选股结果 → 微信/飞书) ----------
# 任一渠道配置后即启用; 全部未配置则推送自动跳过(不影响选股主流程)
NOTIFY_FEISHU_WEBHOOK = os.environ.get("NOTIFY_FEISHU_WEBHOOK", "")          # 飞书群机器人 webhook
NOTIFY_FEISHU_SECRET = os.environ.get("NOTIFY_FEISHU_SECRET", "")            # 飞书机器人加签 secret(可选, 配了才签名)
NOTIFY_SERVERCHAN_KEY = os.environ.get("NOTIFY_SERVERCHAN_KEY", "")          # Server酱 SendKey(推送个人微信)
NOTIFY_WECHAT_WEBHOOK = os.environ.get("NOTIFY_WECHAT_WEBHOOK", "")          # 企业微信群机器人 webhook
NOTIFY_TOP_N = int(os.environ.get("NOTIFY_TOP_N", "8"))                      # 推送展示 Top N 只
NOTIFY_TIMEOUT = float(os.environ.get("NOTIFY_TIMEOUT", "5"))                # 单渠道请求超时(秒)
NOTIFY_DEDUP_SECONDS = int(os.environ.get("NOTIFY_DEDUP_SECONDS", "120"))    # 相同内容去重窗口(秒)

# ---------- 会员/邀请 ----------
INVITE_REWARD_DAYS = int(os.environ.get("INVITE_REWARD_DAYS", "5"))          # 每成功邀请一个新用户, 邀请人 +N 天使用时间
NEW_USER_DAYS = int(os.environ.get("NEW_USER_DAYS", "5"))                    # 新用户注册即送 N 天完整体验(每个手机号仅限 1 次)
NEW_USER_MEMBER_LEVEL = int(os.environ.get("NEW_USER_MEMBER_LEVEL", "1"))     # 注册赠送期间的会员等级(1=付费会员完整体验)
INVITE_SAME_IP_LIMIT = int(os.environ.get("INVITE_SAME_IP_LIMIT", "3"))       # 邀请人同 IP 被邀超过 N 人后不再发奖励(防同 IP 小号刷)
REG_IP_DAY_LIMIT = int(os.environ.get("REG_IP_DAY_LIMIT", "5"))               # 同 IP 24h 最多注册 N 个新账号(防批量刷号)
REG_OPEN = os.environ.get("REG_OPEN", "1") == "1"                             # 是否开放注册(手机号+验证码)

# ---------- 免费用户每日配额(方案 B: 固定窗口原子自增) ----------
# 会员(member_level>=1)/管理员不受限; 免费用户每日可用次数, 签到可加额度
QUOTA_PICKER_DAILY = int(os.environ.get("QUOTA_PICKER_DAILY", "3"))           # 选股快照 次/日
QUOTA_AIPICK_DAILY = int(os.environ.get("QUOTA_AIPICK_DAILY", "1"))           # AI 预测数据 次/日
QUOTA_AUCTION_DAILY = int(os.environ.get("QUOTA_AUCTION_DAILY", "1"))         # 竞价异动 次/日
QUOTA_CHECKIN_BONUS = int(os.environ.get("QUOTA_CHECKIN_BONUS", "3"))         # 每日签到赠送选股额度
QUOTA_DEDUP_SECONDS = int(os.environ.get("QUOTA_DEDUP_SECONDS", "10"))        # 同用户同功能 N 秒内重复请求不重复计数

# ---------- 跨进程状态存储(CacheStore) ----------
# redis=Redis(生产多 worker 共享) / sqlite=SQLite 表 kv_cache(测试/兜底, 零依赖)
CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "sqlite")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
