# -*- coding: utf-8 -*-
"""
数据库层: 建表 + WAL + 老库自动迁移
===================================
所有表结构与历史版本的自动迁移逻辑集中在此。
"""
import sqlite3
import time

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
    if "is_admin" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "expire_at" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN expire_at INTEGER NOT NULL DEFAULT 0")
    if "member_level" not in ucols:
        # 会员等级: 0=免费试用 1=付费会员 2=VIP老师(管理后台指定)
        cur.execute("ALTER TABLE users ADD COLUMN member_level INTEGER NOT NULL DEFAULT 0")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    # 登录 Token 持久化表(进程重启不失效, 支持「记住我」30 天)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expire_ts INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_expire ON tokens(expire_ts)")
    # 启动顺手清一次过期 token
    cur.execute("DELETE FROM tokens WHERE expire_ts < ?", (int(time.time()),))
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    # 系统设置表(key-value, JSON 值): 评分权重等管理配置
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    # 每日一字涨停统计(竞价时段市场快照, 供趋势查看)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_yizi (
            date TEXT PRIMARY KEY,
            yizi_count INTEGER NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL
        )
    """)
    # 板块轮动历史快照: 每日收盘后保存板块强度 Top10, 形成轮动数据基础
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_sector_top (
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'kpl',
            boards TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, source)
        )
    """)
    # 人气热榜每日快照: date+source 唯一, 回看历史人气榜
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hot_rank_history (
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'kpl',
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, source)
        )
    """)
    # 龙虎榜每日快照: date 唯一, 回看历史龙虎榜
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lhb_history (
            date TEXT NOT NULL,
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date)
        )
    """)
    # 连板梯队每日快照: date+pid_type 唯一, 回看历史连板天梯
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ladder_history (
            date TEXT NOT NULL,
            pid_type INTEGER NOT NULL,
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, pid_type)
        )
    """)
    # 竞价异动日终快照: date+tab 唯一, 供竞价异动页按日期回看历史
    # tab: seal(竞价委买)/boom(竞价爆量)/qiangcang(竞价抢筹list20)/
    #      yest_zt(昨日涨停)/yest_broken(昨断板)/broken_yest(昨炸板)/broken_today(今炸板)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auction_daily_history (
            date TEXT NOT NULL,
            tab TEXT NOT NULL,
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, tab)
        )
    """)
    # 旧库升级: 已存在且无 source 列时补上(单字段主键), 历史数据默认 kpl
    try:
        cur.execute("ALTER TABLE daily_sector_top ADD COLUMN source TEXT NOT NULL DEFAULT 'kpl'")
    except Exception:
        pass   # 列已存在, ignore
    # 旧库主键检测: 早期版本 daily_sector_top 是单字段 date 主键, 加 source 列后主键仍为
    # (date), 会导致不同 source 同日期 INSERT OR REPLACE 互相覆盖 —— 必须重建为 (date, source)
    try:
        cols = cur.execute("PRAGMA table_info(daily_sector_top)").fetchall()
        pk_cols = [c[1] for c in cols if c[5]]
        if pk_cols != ["date", "source"]:
            cur.execute("DROP TABLE IF EXISTS daily_sector_top_migrate")
            cur.execute("""
                CREATE TABLE daily_sector_top_migrate (
                    date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'kpl',
                    boards TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    PRIMARY KEY (date, source)
                )
            """)
            cur.execute(
                "INSERT OR IGNORE INTO daily_sector_top_migrate (date, source, boards, ts) "
                "SELECT date, COALESCE(source, 'kpl'), boards, ts FROM daily_sector_top")
            cur.execute("DROP TABLE daily_sector_top")
            cur.execute("ALTER TABLE daily_sector_top_migrate RENAME TO daily_sector_top")
            log.info("daily_sector_top 主键迁移完成 (date,source)")
    except Exception:
        pass   # 兼容异常场景, 不阻塞启动
    # 9:20 竞价时点快照(全市场): 用于 9:25 计算涨幅加速度
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_920 (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            bid_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    # 多时点竞价快照归档(历史回放): 9:15/9:20/9:25 全市场快照, 每日积累形成回放库
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_bid (
            date TEXT NOT NULL,
            time_point TEXT NOT NULL,
            code TEXT NOT NULL,
            bid_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            name TEXT,
            bid_buy_amt REAL NOT NULL DEFAULT 0,
            float_mv REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (date, time_point, code)
        )
    """)
    # 老库迁移: snapshot_bid 增加 名称/委买额/流通市值 列(回放/抢筹计算用)
    bcols2 = [r[1] for r in cur.execute("PRAGMA table_info(snapshot_bid)").fetchall()]
    if "name" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN name TEXT")
    if "bid_buy_amt" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN bid_buy_amt REAL NOT NULL DEFAULT 0")
    if "float_mv" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN float_mv REAL NOT NULL DEFAULT 0")
    if "board" not in bcols2:
        # 概念/行业标签(f103概念优先, f100行业兜底), 供 昨日涨停/昨断板 等概念列补全
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN board TEXT")
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
    # 竞价抢筹结果快照(开盘啦 Type4 竞价时段抓取持久化, 非竞价时段读库展示)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qc_snapshot (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            real_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            qc_delta REAL NOT NULL DEFAULT 0,
            bid_turnover REAL NOT NULL DEFAULT 0,
            bid_change REAL NOT NULL DEFAULT 0,
            float_mv REAL NOT NULL DEFAULT 0,
            board TEXT,
            bid_ratio REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    # 老库迁移: qc_snapshot 增加 竞额/昨比 列(2026-08-14, 抢筹表"竞价换手"改为"竞额/昨比")
    qcols = [r[1] for r in cur.execute("PRAGMA table_info(qc_snapshot)").fetchall()]
    if "bid_ratio" not in qcols:
        cur.execute("ALTER TABLE qc_snapshot ADD COLUMN bid_ratio REAL NOT NULL DEFAULT 0")
    # 最后一秒抢筹高频采样(9:24:55-9:25:03 每秒一次, ts 记实际时刻;
    # 计算时用序列做"差值回退", 对抗接口延迟)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_lastsec (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            bid_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code, ts)
        )
    """)
    # 老库迁移: batches 增加 user_id 列(用户隔离)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(batches)").fetchall()]
    if "user_id" not in cols:
        cur.execute("ALTER TABLE batches ADD COLUMN user_id INTEGER")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_user ON batches(user_id)")
    conn.commit()
    conn.close()
    log.info("数据库初始化/迁移完成: %s", config.DB_FILE)
