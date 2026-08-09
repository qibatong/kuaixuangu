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
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT", "60"))   # 每 IP 每分钟最大请求数
PBKDF2_ITERS = int(os.environ.get("PBKDF2_ITERS", "50000"))    # 密码哈希迭代次数
CACHE_TTL = int(os.environ.get("CACHE_TTL", "30"))             # 行情缓存新鲜度(秒)

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

# 昨日成交额(日K)抓取: 低并发 + 多域名轮询 + 熔断, 避免触发东财限流
YESTERDAY_FETCH_WORKERS = 8
KLINE_TIMEOUT = 5
KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://33.push2his.eastmoney.com",
    "https://48.push2his.eastmoney.com",
    "https://92.push2his.eastmoney.com",
]
