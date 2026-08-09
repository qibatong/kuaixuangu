# -*- coding: utf-8 -*-
"""
数据库层: 建表 + WAL + 老库自动迁移
===================================
所有表结构与历史版本的自动迁移逻辑集中在此。
"""
import sqlite3

from ..core import config, logger

log = logger.get_logger(__name__)


def get_conn():
    conn = sqlite3.connect(config.DB_FILE)
    return conn


def init_db():
    """建表与老库迁移, 幂等, 服务启动时调用一次"""
    conn = sqlite3.connect(config.DB_FILE)
    cur = conn.cursor()
    # WAL 模式(持久化): 读写并发不互锁; busy 超时避免锁等待报错
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_date TEXT NOT NULL,
            batch_time TEXT NOT NULL,
            ts INTEGER NOT NULL,
            action TEXT NOT NULL,
            markets TEXT NOT NULL,
            filters TEXT NOT NULL,
            stock_count INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS batch_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            probability INTEGER NOT NULL,
            confidence INTEGER NOT NULL,
            bid_change REAL NOT NULL,
            real_change REAL NOT NULL,
            entity_change REAL NOT NULL,
            bid_turnover REAL NOT NULL,
            warn_type INTEGER NOT NULL,
            circulation_mv REAL NOT NULL,
            industry TEXT,
            concept TEXT,
            bid_amt REAL NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batch_stocks_batch ON batch_stocks(batch_id)")
    # 老库迁移: 明细增加竞价/昨日成交占比列
    bcols = [r[1] for r in cur.execute("PRAGMA table_info(batch_stocks)").fetchall()]
    if "bid_ratio" not in bcols:
        cur.execute("ALTER TABLE batch_stocks ADD COLUMN bid_ratio REAL")
    # 用户表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    # 老库迁移: users 增加邀请码/邀请关系列
    ucols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "invite_code" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN invite_code TEXT")
    if "invited_by" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER")
    if "phone" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if "email" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "filter_prefs" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN filter_prefs TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    # 密码重置令牌
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reset_tokens_token ON reset_tokens(token)")
    # 老库迁移: batches 增加 user_id 列(用户隔离)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(batches)").fetchall()]
    if "user_id" not in cols:
        cur.execute("ALTER TABLE batches ADD COLUMN user_id INTEGER")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_user ON batches(user_id)")
    conn.commit()
    conn.close()
    log.info("数据库初始化/迁移完成: %s", config.DB_FILE)
