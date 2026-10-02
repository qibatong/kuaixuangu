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
    bid_turnover REAL,               -- 竞价换手率 %(auc_turnover·自由流通·4位小数 见 meoz_source 铁律1)
    warn_type    INTEGER,            -- 异动类型 f630
    -- 基础特征
    price      REAL,                 -- 最新价
    circ_mv    REAL,                 -- 市值(亿元)=**自由流通市值**(2026-09-26 口径统一, 与线上 scorer 一致)
                                     --   列名沿用 circ_mv 不改 schema；旧值为流通市值(f21)口径，已回填
    yesterday_chg REAL,              -- ⚠️ 列名为历史遗留，**实为竞价涨幅**（实时）/ 前一交易日涨幅（历史回补）
                                     --    2026-09-25 起**不参与模型特征**（与 bid_change 同信息）；仅留痕/展示用
    industry   TEXT,                 -- 行业
    concept    TEXT,                 -- 概念
    -- 标签（15:00 后回填）
    is_limit_up   INTEGER,           -- 当日是否涨停 1/0
    close_chg     REAL,              -- 当日收盘涨幅 %
    next_open_chg REAL,              -- 次日竞价/开盘涨幅 %（次日溢价）
    next_close_chg REAL,             -- 次日收盘涨幅 %
    -- ★ 模型特征（2026-10-02 口径无关派生；由 attach_derived 写入，见 MODEL_FEATURES）
    mv_rank      REAL,              -- 当日市值分位 0~1
    amt_rank     REAL,              -- 当日竞价额分位 0~1
    rank_diff    REAL,              -- amt_rank - mv_rank
    price_inv    REAL,              -- 1/price
    yday_zt      INTEGER,           -- 昨日是否涨停
    yday_lb      INTEGER,           -- 截至昨日连板数
    prev_mkt_zt  INTEGER,           -- 昨日全市场涨停家数
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


DERIVED_COLS = [
    ("mv_rank", "REAL"), ("amt_rank", "REAL"), ("rank_diff", "REAL"),
    ("price_inv", "REAL"), ("yday_zt", "INTEGER"), ("yday_lb", "INTEGER"),
    ("prev_mkt_zt", "INTEGER"),
]


def ensure_columns(conn=None):
    """老库迁移：把 MODEL_FEATURES 的派生列 ALTER 进来（幂等，缺哪列补哪列）。

    ⚠️ 为什么必须显式做：`CREATE TABLE IF NOT EXISTS` 对已存在的表是**空操作**
    ⇒ 线上 aipick.db（已有 209 天数据）不会因为改了 SCHEMA 就多出列，
    collector 写库会报 `no such column: mv_rank`。2026-10-02 实测踩到。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(features)").fetchall()}
        for name, typ in DERIVED_COLS:
            if name not in have:
                conn.execute("ALTER TABLE features ADD COLUMN %s %s" % (name, typ))
                print("[db] features 迁移: +%s %s" % (name, typ))
        conn.commit()
    finally:
        if own:
            conn.close()


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    ensure_columns(conn)          # ★ 2026-10-02: 老库补派生列（幂等）
    conn.commit()
    conn.close()

# ==================== 模型特征（口径无关派生）====================
# 2026-10-02 主人指令（命中率优先）：线上 circ_mv 是**自由流通市值**、离线基座是**流通市值**
#   （实测 947 vs 2251 亿，比值因股而异 1.5~2.4 倍）⇒ 直接用 circ_mv 原始值必然 train-serve skew。
#   改为「分位 + 昨日侧」：两侧都能就地算出，与数据源口径无关。
#   三处消费者（collector 写库 / predict_daily 预测 / backend ai_predict 推理）共用
#   `collector.fetch_from_kuaixuan` ⇒ 派生只做一份，自动同步（见 ai_predict 模块 docstring 同源约定）。
MODEL_FEATURES = [
    "bid_change", "bid_amount", "bid_turnover", "price",
    "mv_rank", "amt_rank", "rank_diff", "price_inv",
    "yday_zt", "yday_lb", "prev_mkt_zt",
]
LB_LOOKBACK = 20            # 连板回看上限（与训练侧 clip(10) 一致，留冗余）


def _pct_rank(vals):
    """平均名次归一化到 0~1；None 不参与（返回 0.5 兜底）"""
    idx = [(float(v), i) for i, v in enumerate(vals) if v is not None]
    idx.sort(key=lambda x: x[0])
    out = {}
    n = len(idx)
    if not n:
        return out
    i = 0
    while i < n:
        j = i
        while j + 1 < n and idx[j + 1][0] == idx[i][0]:
            j += 1
        r = ((i + j) / 2.0 / (n - 1)) if n > 1 else 0.5
        for k in range(i, j + 1):
            out[idx[k][1]] = r
        i = j + 1
    return out


def attach_derived(rows, trade_date):
    """补齐 MODEL_FEATURES 的派生列（**无出网、无泄漏**）。

    · 日内分位（mv_rank/amt_rank/rank_diff/price_inv）：只用当日横截面
    · 昨日侧（yday_zt/yday_lb/prev_mkt_zt）：只读本表 < trade_date 的最后交易日
      （昨日标签在昨日 15:04 由 --label 回填；缺失容错为 0）
    """
    if not rows:
        return rows
    mv = _pct_rank([r.get("circ_mv") for r in rows])
    amt = _pct_rank([r.get("bid_amount") for r in rows])
    for i, r in enumerate(rows):
        a, m = amt.get(i, 0.5), mv.get(i, 0.5)
        r["mv_rank"], r["amt_rank"], r["rank_diff"] = m, a, a - m
        p = r.get("price")
        r["price_inv"] = (1.0 / float(p)) if p else None
    prev_date, zt_cnt, hist, dss = None, 0, {}, []
    try:
        conn = get_conn()
        row = conn.execute("SELECT MAX(trade_date) FROM features WHERE trade_date < ?",
                           (trade_date,)).fetchone()
        prev_date = row[0] if row else None
        if prev_date:
            zt_cnt = conn.execute("SELECT COUNT(*) FROM features WHERE trade_date=? AND is_limit_up=1",
                                  (prev_date,)).fetchone()[0] or 0
            dss = [x[0] for x in conn.execute(
                "SELECT DISTINCT trade_date FROM features WHERE trade_date<=? "
                "ORDER BY trade_date DESC LIMIT ?", (prev_date, LB_LOOKBACK + 1)).fetchall()]
            if dss:
                q = ",".join("?" * len(dss))
                for cd, dd, lim in conn.execute(
                        "SELECT code, trade_date, COALESCE(is_limit_up,0) FROM features "
                        "WHERE trade_date IN (%s)" % q, dss):
                    hist.setdefault(str(cd), {})[dd] = int(lim or 0)
        conn.close()
        for r in rows:
            h = hist.get(str(r.get("code")), {})
            lb = 0
            if prev_date and h.get(prev_date, 0):
                for dd in dss:
                    if h.get(dd, 0):
                        lb += 1
                    else:
                        break
            r["yday_zt"] = h.get(prev_date, 0)
            r["yday_lb"] = min(lb, 10)
            r["prev_mkt_zt"] = zt_cnt
    except Exception as e:                                     # noqa: BLE001
        print("⚠️ attach_derived 昨日侧失败(置 0 兜底): %s" % str(e)[:120])
        for r in rows:
            r.setdefault("yday_zt", 0)
            r.setdefault("yday_lb", 0)
            r.setdefault("prev_mkt_zt", 0)
    return rows



def upsert_features(rows):
    """rows: list of dict, 写入/更新竞价特征（9:25 快照）"""
    conn = get_conn()
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO features
            (trade_date, code, name, bid_change, bid_amount, bid_volume,
             bid_turnover, warn_type, price, circ_mv, yesterday_chg, industry, concept,
             mv_rank, amt_rank, rank_diff, price_inv, yday_zt, yday_lb, prev_mkt_zt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("trade_date"), r.get("code"), r.get("name"),
             r.get("bid_change"), r.get("bid_amount"), r.get("bid_volume"),
             r.get("bid_turnover"), r.get("warn_type"), r.get("price"),
             r.get("circ_mv"), r.get("yesterday_chg"), r.get("industry"), r.get("concept"),
             r.get("mv_rank"), r.get("amt_rank"), r.get("rank_diff"), r.get("price_inv"),
             r.get("yday_zt"), r.get("yday_lb"), r.get("prev_mkt_zt")),
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
    """加载带标签的特征数据（训练用）。

    ★ 2026-10-02 两处修正（实测踩到）：
      ① 列名改为**从游标动态取**：原实现写死 17 列名，加派生列后立刻
         `ValueError: 17 columns passed, passed data had 24 columns`。
      ② 新增 `limit_days`（**真正生效**，此前形参被忽略）：测试机只有 1.75GB 内存，
         106 万行 × 24 列直接 OOM（`Killed`）⇒ 可按最近 N 个交易日截断。
         调用方由 `AIPICK_TRAIN_DAYS` 环境变量控制（默认见 train_model.py 注释）。
      改用 `pd.read_sql_query` 也比重建 Python 对象省内存。
    """
    import pandas as pd
    conn = get_conn()
    sql = "SELECT * FROM features WHERE is_limit_up IS NOT NULL"
    params = []
    if limit_days:
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM features ORDER BY trade_date DESC LIMIT ?",
            (int(limit_days),)).fetchall()]
        if days:
            sql += " AND trade_date >= ?"
            params.append(min(days))
    sql += " ORDER BY trade_date"
    df = pd.read_sql_query(sql, conn, params=tuple(params))
    conn.close()
    return df


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
