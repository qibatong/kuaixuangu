# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 数据层
SQLite 存储：每日竞价特征 + 收盘标签
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "aipick.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    trade_date TEXT NOT NULL,        -- 交易日 YYYY-MM-DD
    code       TEXT NOT NULL,        -- 股票代码
    name       TEXT,
    -- 竞价特征（9:25 快照）
    bid_change   REAL,               -- 竞价涨幅 %
    bid_amount   REAL,               -- 竞价金额(万元) f616
    bid_volume   REAL,               -- 竞价成交量(手) f617
    bid_turnover REAL,               -- 竞价换手率 %
    warn_type    INTEGER,            -- 异动类型 f630
    -- 基础特征
    price      REAL,                 -- 最新价
    circ_mv    REAL,                 -- 流通市值(亿元) f21
    yesterday_chg REAL,              -- 昨日涨幅 %
    industry   TEXT,                 -- 行业
    concept    TEXT,                 -- 概念
    -- 标签（15:00 后回填）
    is_limit_up   INTEGER,           -- 当日是否涨停 1/0
    close_chg     REAL,              -- 当日收盘涨幅 %
    next_open_chg REAL,              -- 次日竞价/开盘涨幅 %（次日溢价）
    next_close_chg REAL,             -- 次日收盘涨幅 %
    PRIMARY KEY (trade_date, code)
);
CREATE TABLE IF NOT EXISTS train_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trained_at TEXT,
    model_path TEXT,
    n_samples INTEGER,
    auc REAL,
    feature_imp TEXT
);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def upsert_features(rows):
    """rows: list of dict, 写入/更新竞价特征（9:25 快照）"""
    conn = get_conn()
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO features
            (trade_date, code, name, bid_change, bid_amount, bid_volume,
             bid_turnover, warn_type, price, circ_mv, yesterday_chg, industry, concept)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("trade_date"), r.get("code"), r.get("name"),
             r.get("bid_change"), r.get("bid_amount"), r.get("bid_volume"),
             r.get("bid_turnover"), r.get("warn_type"), r.get("price"),
             r.get("circ_mv"), r.get("yesterday_chg"), r.get("industry"), r.get("concept")),
        )
    conn.commit()
    conn.close()


def update_labels(trade_date, rows):
    """rows: list of dict {code, is_limit_up, close_chg} 收盘后回填标签"""
    conn = get_conn()
    for r in rows:
        conn.execute(
            """UPDATE features SET is_limit_up=?, close_chg=?
            WHERE trade_date=? AND code=?""",
            (r.get("is_limit_up"), r.get("close_chg"), trade_date, r.get("code")),
        )
    conn.commit()
    conn.close()


def update_next_day(trade_date, rows):
    """回填次日溢价：rows = [{code, next_open_chg, next_close_chg}]"""
    conn = get_conn()
    for r in rows:
        conn.execute(
            """UPDATE features SET next_open_chg=?, next_close_chg=?
            WHERE trade_date=? AND code=?""",
            (r.get("next_open_chg"), r.get("next_close_chg"), trade_date, r.get("code")),
        )
    conn.commit()
    conn.close()


def load_features(limit_days=None):
    """加载全部带标签的特征数据（训练用）"""
    conn = get_conn()
    df = conn.execute(
        """SELECT * FROM features
        WHERE is_limit_up IS NOT NULL
        ORDER BY trade_date"""
    ).fetchall()
    conn.close()
    cols = ["trade_date", "code", "name", "bid_change", "bid_amount", "bid_volume",
            "bid_turnover", "warn_type", "price", "circ_mv", "yesterday_chg",
            "industry", "concept", "is_limit_up", "close_chg",
            "next_open_chg", "next_close_chg"]
    import pandas as pd
    return pd.DataFrame(df, columns=cols)


def count_rows():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    n_labeled = conn.execute("SELECT COUNT(*) FROM features WHERE is_limit_up IS NOT NULL").fetchone()[0]
    conn.close()
    return n, n_labeled


def today():
    return datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    init_db()
    print("数据库初始化完成:", DB_PATH)
    n, nl = count_rows()
    print(f"总记录 {n} 条，已打标签 {nl} 条")
