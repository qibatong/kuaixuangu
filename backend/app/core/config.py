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
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT", "60"))   # 每 IP 每分钟最大请求数
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

# ---------- 邮件重置密码 ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
RESET_TTL = int(os.environ.get("RESET_TTL", "1800"))
RESET_RATE_LIMIT = int(os.environ.get("RESET_RATE_LIMIT", "3"))

# ---------- 数据源(东方财富公开行情接口) ----------
EASTMONEY_URL = "https://push2dycalc.eastmoney.com/api/qt/clist/get"
EASTMONEY_UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
FIELDS = "f2,f3,f4,f5,f6,f8,f10,f12,f14,f17,f18,f20,f21,f615,f616,f617,f618,f630,f100,f102,f103"

# 盘中实时选股: 东财涨停池(封单/连板/炸板) + 涨停池缓存 TTL
EASTMONEY_ZT_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
EASTMONEY_ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"
ZT_CACHE_TTL = int(os.environ.get("ZT_CACHE_TTL", "15"))       # 涨停池缓存新鲜度(秒)
SPOT_CACHE_TTL = int(os.environ.get("SPOT_CACHE_TTL", "120"))  # 盘中实时行情缓存新鲜度(秒); 2026-08-18: 30→120 全市场20页太重, 竞价异动页多个tab共用一次拉取
SPOT_MAX_PAGES = int(os.environ.get("SPOT_MAX_PAGES", "30"))   # 盘中全市场分页拉取上限(每页200只; 30页=6000只覆盖全A+北交所)

# 昨日成交额(日K)抓取: 低并发 + 多域名轮询 + 熔断, 避免触发东财限流
YESTERDAY_FETCH_WORKERS = 8
YESTERDAY_FETCH_TIMEOUT = 20   # 批量并发整体超时上限(秒), 超时未完成跳过(昨比置空), 防阻塞
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
}
KPL_BID_TTL = int(os.environ.get("KPL_BID_TTL", "30"))        # 竞价委买额缓存新鲜度(秒)
KPL_BID_ST = os.environ.get("KPL_BID_ST", "200")              # Type4 涨停委买额榜条数(200=涨停榜, 实测更大值是否生效)
KPL_SENTI_TTL = int(os.environ.get("KPL_SENTI_TTL", "60"))    # 情绪值缓存新鲜度(秒)
KPL_LADDER_TTL = int(os.environ.get("KPL_LADDER_TTL", "60"))  # 连板梯队缓存新鲜度(秒)
KPL_BOARD_TTL = int(os.environ.get("KPL_BOARD_TTL", "30"))    # 板块强度缓存新鲜度(秒)

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
INVITE_REWARD_DAYS = int(os.environ.get("INVITE_REWARD_DAYS", "7"))          # 每成功邀请一个新用户, 邀请人 +N 天使用时间
NEW_USER_DAYS = int(os.environ.get("NEW_USER_DAYS", "7"))                    # 新用户注册即送 N 天试用(被邀请人同样得 N 天)
INVITE_SAME_IP_LIMIT = int(os.environ.get("INVITE_SAME_IP_LIMIT", "3"))       # 邀请人同 IP 被邀超过 N 人后不再发奖励(防同 IP 小号刷)
REG_IP_DAY_LIMIT = int(os.environ.get("REG_IP_DAY_LIMIT", "5"))               # 同 IP 24h 最多注册 N 个新账号(防批量刷号)

# ---------- 跨进程状态存储(CacheStore) ----------
# redis=Redis(生产多 worker 共享) / sqlite=SQLite 表 kv_cache(测试/兜底, 零依赖)
CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "sqlite")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
