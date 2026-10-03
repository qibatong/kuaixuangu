# -*- coding: utf-8 -*-
"""涨停标签真值回补（权威源 = 猫爪涨停池，132 天可回溯）。

为什么需要它（2026-10-02 实测）：
  自算标签（前复权价 + 幅度容差）在**低价股**上有盲区 —— 涨停价四舍五入到分，
  prev=3.33 元时涨停价 3.66 ⇒ 真实涨停涨幅只有 +9.91%，低于幅度阈值 9.98%
  ⇒ 对拍发现 411 行落在 9.95~9.98 灰区，无法自证。与其调参猜，不如用**官方涨停池**裁决。

数据源（实测均可回溯到 2026-03-18）：
  limit_pool_map(date)      → type/is_break(炸板)/limit_times(连板)/open_times(开板次数)/
                              fd_amount(封单)/first_time(首封)/last_time/pct_chg/close/amount
  limit_pool_yes_map(date)  → 追加 pre_* 一族（昨日连板/涨幅/封单）+ auc_vol_ratio

落库：新表 label_truth（主键 date+code，幂等 INSERT OR REPLACE），**不动 features 任何列**。
配额：每日 2 次请求（一次返回全市场涨停池）⇒ 132 天 ≈ 264 次，限速 0.3s ≈ 2 分钟。

用法：
    python label_truth_from_meoz.py --db ... --dates 20260318,20260319      # 小样本验证
    python label_truth_from_meoz.py --db ... --all                          # 全量 132 天
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS label_truth (
  trade_date TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT,
  zt INTEGER,            -- 1=涨停（出现在涨停池，type='u'）
  type TEXT,             -- 上游 type（u/d）
  is_break INTEGER,      -- 1=炸板（曾封后开）
  limit_times INTEGER,   -- 连板数（1=首板）
  open_times INTEGER,    -- 开板次数
  fd_amount REAL,        -- 封单额
  first_time TEXT,       -- 首次封板时间(HHMMSS)
  last_time TEXT,
  pct_chg REAL,
  close REAL,
  amount REAL,
  pre_limit_times INTEGER,  -- 昨日连板数（来自 limit_pool_yes）
  pre_pct_chg REAL,         -- 昨日涨幅
  pre_fd_amount REAL,       -- 昨日封单
  auc_vol_ratio REAL,       -- 竞价量比
  src TEXT,
  PRIMARY KEY (trade_date, code)
);
"""

COLS = ("trade_date,code,name,zt,type,is_break,limit_times,open_times,fd_amount,first_time,"
        "last_time,pct_chg,close,amount,pre_limit_times,pre_pct_chg,pre_fd_amount,auc_vol_ratio,src")


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='/opt/kuaixuan/aipick/scripts/data/aipick.db')
    ap.add_argument('--dates', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=0.3)
    a = ap.parse_args()

    from app.services import meoz_client as M
    conn = sqlite3.connect(a.db)
    conn.executescript(SCHEMA)
    # 🔴 库内 trade_date 格式实测为 '2026-03-18'(带横线) ⇒ 入库存**原格式**(保证 JOIN 可直接用),
    #    调猫爪时再 norm8 成 8 位（2026-10-02 就是这里对不上导致"交集 0"）。
    # 🔴 日期格式必须**跟随 features 库内格式**（实测 '2026-03-18'；显式 --dates 传 8 位也要转过来，
    #    否则 label_truth 与 features 的 JOIN 永远 0 行 —— 2026-10-02 连踩两次）。
    _sample = conn.execute("SELECT trade_date FROM features LIMIT 1").fetchone()
    _dash = bool(_sample) and '-' in str(_sample[0])

    def to_db(x):
        y = norm8(x)
        return '%s-%s-%s' % (y[:4], y[4:6], y[6:]) if _dash else y

    if a.dates:
        dates = [to_db(x) for x in a.dates.split(',') if x.strip()]
    else:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM features ORDER BY trade_date")]
    if a.limit:
        dates = dates[:a.limit]
    print("回补 %d 个交易日（配额≈%d 次请求: 每日 u/ub/bu + yes）" % (len(dates), len(dates) * 4), flush=True)

    t0, tot, brk = time.time(), 0, 0
    for i, d in enumerate(dates, 1):
        d_api = norm8(d)
        # 🔴 2026-10-02 实测: 官方 `type` 仅支持 u/d/ub/db/bu/bd —— 普通池(=u)只回收**封住**的票，
        #    炸板票在 ub/bu 里(is_break 恒 False 是假象) ⇒ 必须显式多取两档，否则"炸板"永久为 0。
        pool = {}
        for lt in ('u', 'ub', 'bu'):
            try:
                got = M.limit_pool_map(date=d_api, limit_type=lt) or {}
            except Exception as e:
                if lt == 'u':
                    print("  %s limit_pool(u) 失败: %s" % (d, str(e)[:70]), flush=True)
                got = {}
            for code, v in got.items():
                v = dict(v)
                v['_type_called'] = lt
                pool.setdefault(str(code).zfill(6), v)
        try:
            yes = M.limit_pool_yes_map(date=d_api) or {}
        except Exception:
            yes = {}
        rows = []
        for code, v in pool.items():
            tp = str(v.get('type') or v.get('_type_called') or '').lower()
            if not tp.startswith('u'):
                continue                                   # 跌停(d*)不入涨停真值
            brk = 1 if (tp != 'u' or v.get('is_break') in (True, 1, '1', 'true', 'True', 'Y', 'y')) else 0
            # 🔴 主口径 = **收盘封板**：炸板(ub/bu)收盘未封 ⇒ zt=0，只作"盘中触板未封"的 0.5 档软标签
            #    （2026-10-02 实测踩到：把它们算成 zt=1 会让自算标签准确率假跌到 67.9%）
            zt = 0 if brk else 1
            y = yes.get(code) or {}
            rows.append((
                d, str(code).zfill(6), v.get('name'), zt, tp, brk,
                v.get('limit_times'), v.get('open_times'), v.get('fd_amount'),
                v.get('first_time'), v.get('last_time'), v.get('pct_chg'),
                v.get('close'), v.get('amount'),
                y.get('pre_limit_times'), y.get('pre_pct_chg'), y.get('pre_fd_amount'),
                y.get('auc_vol_ratio'), 'meoz_limit_pool'))
        if rows:
            conn.executemany("INSERT OR REPLACE INTO label_truth (%s) VALUES (%s)"
                             % (COLS, ','.join('?' * len(COLS.split(',')))), rows)
            conn.commit()
        tot += len(rows)
        brk += sum(1 for r in rows if r[5] == 1)
        if i % 10 == 0 or i == len(dates):
            print("  %d/%d 累计涨停 %d 只（其中炸板 %d）%.0fs"
                  % (i, len(dates), tot, brk, time.time() - t0), flush=True)
        time.sleep(a.sleep)
    n, ds, de = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM label_truth").fetchone()
    print("\nlabel_truth: %d 行，%s ~ %s" % (n, ds, de))
    # 与自算标签对拍（若 is_limit_up_v2 已写）
    try:
        inj = conn.execute("SELECT COUNT(*) FROM label_truth t JOIN features f "
                           "ON f.trade_date=t.trade_date AND f.code=t.code WHERE t.zt=1").fetchone()[0]
        hit = conn.execute("SELECT COUNT(*) FROM label_truth t JOIN features f "
                           "ON f.trade_date=t.trade_date AND f.code=t.code "
                           "WHERE t.zt=1 AND f.is_limit_up_v2=1").fetchone()[0]
        zt_n = conn.execute("SELECT COUNT(*) FROM label_truth WHERE zt=1").fetchone()[0]
        print("真值封板 %d 只（另有炸板 %d）；样本内 %d，自算命中 %d ⇒ 准确率 %.1f%%"
              % (zt_n, n - zt_n, inj, hit, 100.0 * hit / max(inj, 1)))
    except Exception:
        pass
    conn.close()


if __name__ == '__main__':
    main()
