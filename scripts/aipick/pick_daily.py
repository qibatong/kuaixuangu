# -*- coding: utf-8 -*-
"""pick_daily：四条线名单统一落库（幂等，可历史回填）。

四条线（主人 2026-10-02 方案）：
  xgb 金睛 / lgb 火眼  —— 用线上同一份模型 + aipick.db 11 维特征**重算**（历史 132 天可回填；
                          不依赖 predictions_*.json，那份只有最近几天）
  zh  竞价精选        —— 直接 import 生产 `services.picker.zh`.select()（同源）
  yj  竞价优选(一进二) —— 直接 import 生产 `services.yijiner`.run(date)（受 snapshot_bid 覆盖限制）

字段口径（方案第 4 条）：
  fill_grade  成交概率分级：none(一字,封死不成交) / low(高开≥9.8%排队打板) / mid(5~9.8%) / high(≤5%)
              判定用**官方涨停价** price_limit.up_limit 与竞价价(=pre_close×(1+bid_change)) 比较
  is_yidzi    竞价价 ≥ up_limit ⇒ 一字
  sim_filled  模拟成交：一字=0，其余=1（方案口径：封死不成交、炸板按成交计）
  fd_proxy    封单代理：同期 limit_event 最早一条的 fd_amount（仅近 60 交易日有）
  is_limit_up 当日封板（features.is_limit_up_v3，官方价口径）
  ret_close / ret_next  以**竞价价**买入的收益（%( 竞价→收盘 / 竞价→次日开盘 )）

用法：
    python pick_daily.py --db <aipick.db> --date 20260930            # 单日
    python pick_daily.py --db <aipick.db> --start 20260318 --end 20260930 --lines xgb,lgb,zh
"""
import argparse
import json as _j
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))
sys.path.insert(0, '/opt/kuaixuan/backend')
MODEL_DIR = '/opt/kuaixuan/aipick/models'
FEATURES = ['bid_change', 'bid_amount', 'bid_turnover', 'price', 'mv_rank', 'amt_rank',
            'rank_diff', 'price_inv', 'yday_zt', 'yday_lb', 'prev_mkt_zt']
FEAT_VER = 'v3_official_pricelimit_20261002'

SCHEMA = """
CREATE TABLE IF NOT EXISTS pick_daily (
  trade_date TEXT NOT NULL, line TEXT NOT NULL, code TEXT NOT NULL,
  name TEXT, rank INTEGER, score REAL,
  bid_change REAL, bid_amount REAL, bid_turnover REAL, price REAL,
  fill_grade TEXT, is_yidzi INTEGER, sim_filled INTEGER, fd_proxy REAL,
  up_limit REAL, auc_price REAL,
  is_limit_up INTEGER, ret_close REAL, ret_next REAL,
  model_ver TEXT, feat_ver TEXT, src TEXT,
  PRIMARY KEY (trade_date, line, code));
CREATE INDEX IF NOT EXISTS idx_pick_daily_date ON pick_daily(trade_date, line);
"""


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def _f(v):
    """数值容错：历史行里混有 '-' 等占位符（生产 predict_daily 靠 to_numeric 兜）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dash(d8):
    return '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:])


def load_models():
    import xgboost as xgb
    import lightgbm as lgb
    out = {}
    px = os.path.join(MODEL_DIR, 'model_xgb.json')
    pl = os.path.join(MODEL_DIR, 'model_lgb.txt')
    if os.path.exists(px):
        b = xgb.Booster(); b.load_model(px)
        out['xgb'] = ('xgb', b, b.feature_names)
    if os.path.exists(pl):
        b = lgb.Booster(model_file=pl)
        out['lgb'] = ('lgb', b, b.feature_name())
    return out


def grade(pre_close, up_limit, bid_change, auc_price):
    if auc_price is None or up_limit is None:
        # 降级（无官方涨停价）：用涨幅近似判"一字/封死"，**不可恒判为一字**
        #   （2026-10-03 测试机实测踩到：原写法 yidzi 恒 1，页面全标"买不进"）
        bc0 = bid_change or 0
        if bc0 >= 9.8:
            return 'none', 1, 0
        return ('mid' if bc0 >= 5 else 'high'), 0, 1
    yidzi = 1 if auc_price >= up_limit - 0.005 else 0
    if yidzi:
        return 'none', 1, 0
    bc = bid_change or 0
    if bc >= 9.8:
        return 'low', 0, 1
    if bc >= 5.0:
        return 'mid', 0, 1
    return 'high', 0, 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--date', default='')
    ap.add_argument('--start', default='')
    ap.add_argument('--end', default='')
    ap.add_argument('--lines', default='xgb,lgb,zh')
    ap.add_argument('--topn', type=int, default=60)
    ap.add_argument('--kdb', default='/opt/kuaixuan/kuaixuan.db')
    a = ap.parse_args()
    lines = [x.strip() for x in a.lines.split(',') if x.strip()]
    conn = sqlite3.connect(a.db)
    conn.executescript(SCHEMA)
    ds = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM features ORDER BY trade_date")]
    if a.date:
        ds = [r for r in ds if norm8(r) == norm8(a.date)]
    else:
        lo, hi = norm8(a.start), norm8(a.end)
        ds = [r for r in ds if (not lo or norm8(r) >= lo) and (not hi or norm8(r) <= hi)]
    print("pick_daily: %d 个交易日，线路 %s" % (len(ds), lines), flush=True)

    # 官方涨停价（竞价价口径）
    # 🔴 辅助表缺失要能降级（测试机/新库未必有 price_limit）—— 缺了就退回"按涨幅近似判一字"，
    #    绝不因辅助表不存在而整体失败（2026-10-03 测试机实测踩到）。
    pl = {}
    try:
        for td, code, pc, up in conn.execute("SELECT trade_date, code, pre_close, up_limit FROM price_limit "
                                             "WHERE up_limit IS NOT NULL"):
            pl[(td, str(code).zfill(6))] = (pc, up)
    except Exception as e:
        print("  [降级] price_limit 不可用(%s)，一字判定退回涨幅近似" % str(e)[:40])
    # 封单代理（近 60 天）
    fd = {}
    try:
        for td, code, amt in conn.execute("SELECT trade_date, code, MIN(fd_amount) FROM limit_event "
                                          "WHERE fd_amount IS NOT NULL GROUP BY trade_date, code"):
            fd[(td, str(code).zfill(6))] = amt
    except Exception:
        pass
    # 标签 + 次日收益基准
    # 标签列降级：新库用 is_limit_up_v3（官方涨停价口径），旧库退回 is_limit_up；next_* 缺失给 None
    lab = {}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    lab_col = 'is_limit_up_v3' if 'is_limit_up_v3' in cols else ('is_limit_up' if 'is_limit_up' in cols else None)
    if lab_col:
        sel = ["trade_date", "code", lab_col]
        sel += [c for c in ("next_open_chg", "next_close_chg", "close_chg") if c in cols]
        for row in conn.execute("SELECT %s FROM features" % ','.join(sel)):
            td, code = row[0], row[1]
            rest = list(row[2:]) + [None] * (4 - len(row[2:]))
            lab[(td, str(code).zfill(6))] = tuple(rest[:4])
    has_excl = 'exclude_v3' in cols
    # 次日开盘价（用 K 线算 ret_next：竞价价→次日开盘）
    # 🔴 内存有界设计（2026-10-03 生产事故整改）：
    #    原实现一次性把 5910 只 × 700 天 K 线读进内存 ⇒ 7.5GB 机器峰值数 GB。
    #    改为**按需单票查 + LRU 缓存**（上限 300 只），内存恒定在几十 MB。
    _kc = sqlite3.connect(a.kdb)
    _cache = {}
    _order = []

    def kline_of(code):
        """返回 (date->(open,close), 升序日期列表)；LRU 上限 300 只"""
        if code in _cache:
            return _cache[code]
        row = _kc.execute("SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
        if not row or not row[0]:
            v = ({}, [])
        else:
            try:
                o = _j.loads(row[0]) if isinstance(row[0], str) else row[0]
                t = [str(x)[:10] for x in (o.get('time') or [])]
                op, cl = o.get('open') or [], o.get('close') or []
                mp = {t[i]: (op[i] if i < len(op) else None, cl[i] if i < len(cl) else None)
                      for i in range(len(t)) if i < len(cl) and cl[i]}
                v = (mp, sorted(mp))
            except Exception:
                v = ({}, [])
        _cache[code] = v
        _order.append(code)
        if len(_order) > 300:
            _cache.pop(_order.pop(0), None)
        return v

    models = load_models() if any(x in lines for x in ('xgb', 'lgb')) else {}
    zh_mod = None
    if 'zh' in lines:
        from app.services.picker import zh as zh_mod  # noqa

    total = 0
    for i, td in enumerate(ds, 1):
        d8 = norm8(td); d = dash(d8)
        rows = conn.execute(
            "SELECT code, name, bid_change, bid_amount, bid_turnover, price, mv_rank, amt_rank, "
            "rank_diff, price_inv, yday_zt, yday_lb, prev_mkt_zt, circ_mv FROM features "
            "WHERE trade_date=?", (td,)).fetchall()
        # 排除 ST / 北交所 / 停牌 / 新股首日（方案 #0）
        clean = []
        for r in rows:
            code, name = str(r[0]).zfill(6), (r[1] or '')
            if 'ST' in name.upper().replace(' ', ''):
                continue
            if code.startswith(('8', '4', '920')):
                continue
            if has_excl:
                ex = conn.execute("SELECT exclude_v3 FROM features WHERE trade_date=? AND code=?",
                                  (td, code)).fetchone()
                if ex and ex[0] in ('suspend', 'new_listing', 'no_data'):
                    continue
            clean.append(tuple(r[:2]) + tuple(_f(x) for x in r[2:]))

        def emit(line, recs, model_ver=None):
            out = []
            for k, (code, score, r) in enumerate(recs):
                key = (td, code)
                pc, up = pl.get(key, (None, None))
                bc = _f(r[2])
                # 买入价 = 9:25 竞价价 = features.price（实测与 pre_close×(1+bid_change) 逐位一致）
                _pr = _f(r[5])
                auc = round(_pr, 3) if _pr else (round(pc * (1 + (bc or 0) / 100.0), 3) if pc else None)
                g, yidzi, sim = grade(pc, up, bc, auc)
                lb = lab.get(key, (None, None, None, None))
                m, seq = kline_of(code)
                cur = m.get(d) or (None, None)
                nxt_open = None
                if d in m and d in seq and seq.index(d) + 1 < len(seq):
                    nxt_open = (m.get(seq[seq.index(d) + 1]) or (None, None))[0]
                ret_close = round((cur[1] / auc - 1) * 100, 3) if (auc and cur[1]) else None
                ret_next = round((nxt_open / auc - 1) * 100, 3) if (auc and nxt_open) else None
                out.append((td, line, code, r[1], k + 1, float(score), bc, r[3], r[4], r[5],
                            g, yidzi, sim, fd.get(key), up, auc,
                            lb[0], ret_close, ret_next, model_ver, FEAT_VER, 'pick_daily'))
            if out:
                conn.executemany("INSERT OR REPLACE INTO pick_daily VALUES (%s)"
                                 % ','.join('?' * 22), out)
                conn.commit()
            return len(out)

        n_add = 0
        for algo in ('xgb', 'lgb'):
            if algo not in lines or algo not in models:
                continue
            kind, booster, names = models[algo]
            arr = []
            for r in clean:
                vals = []
                ok = True
                # r: code,name,bid_change,bid_amount,bid_turnover,price,mv_rank,amt_rank,rank_diff,price_inv,yday_zt,yday_lb,prev_mkt_zt,circ_mv
                mapping = {'bid_change': 2, 'bid_amount': 3, 'bid_turnover': 4, 'price': 5,
                           'mv_rank': 6, 'amt_rank': 7, 'rank_diff': 8, 'price_inv': 9,
                           'yday_zt': 10, 'yday_lb': 11, 'prev_mkt_zt': 12}
                for f in names:
                    v = _f(r[mapping[f]])
                    if v is None:
                        ok = False
                        break
                    vals.append(v)
                if ok:
                    arr.append((str(r[0]).zfill(6), vals, r))
            if not arr:
                continue
            import numpy as np
            M = np.array([x[1] for x in arr], dtype=float)
            if algo == 'xgb':
                import xgboost as xgb
                sc = booster.predict(xgb.DMatrix(M, feature_names=names))
            else:
                sc = booster.predict(M)
            recs = sorted([(arr[j][0], float(sc[j]), arr[j][2]) for j in range(len(arr))],
                          key=lambda x: -x[1])[:a.topn]
            n_add += emit(algo, recs, model_ver='%s_%s' % (algo, FEAT_VER))
        if 'zh' in lines:
            zrows = [{"code": str(r[0]).zfill(6), "name": r[1], "bid_change": r[2],
                      "bid_amt": r[3], "price": r[5], "float_mv": r[13]} for r in clean]
            try:
                picks, _st = zh_mod.select(zrows, lambda c: _kline_raw(c, a.kdb), today=d)
                recs = [(p.get('code'), float(p.get('volPct') or p.get('score') or 0),
                         next(r for r in clean if str(r[0]).zfill(6) == str(p.get('code')).zfill(6)))
                        for p in picks[:a.topn]]
                n_add += emit('zh', recs, model_ver='zh_rule_%s' % FEAT_VER)
            except Exception as e:
                import traceback
                print("  %s zh 失败: %r" % (d, e), flush=True)
                traceback.print_exc()
        total += n_add
        if i % 10 == 0 or i == len(ds):
            print("  %d/%d 累计写入 %d 行 (%.0fs)" % (i, len(ds), total, time.time()), flush=True)

    _kc.close()
    print("\npick_daily 共 %d 行" % conn.execute("SELECT COUNT(*) FROM pick_daily").fetchone()[0])
    for line, n, zt, fill_none in conn.execute(
            "SELECT line, COUNT(*), SUM(is_limit_up), SUM(fill_grade='none') FROM pick_daily GROUP BY 1"):
        print("  %-4s %6d 行 | 封板 %4d (%.1f%%) | 一字(买不进) %d" % (line, n, zt or 0, 100.0 * (zt or 0) / max(n, 1), fill_none or 0))
    conn.close()


_KR = {}
_KR_ORD = []


def _kline_raw(code, kdb):
    """zh 需要的**原始 day_data**（dict 形态）；同样按需查 + LRU(300)，不再全量预载。"""
    global _KR_ORD
    if code in _KR:
        return _KR[code]
    if not _KR:
        _KR['__conn__'] = sqlite3.connect(kdb)
    conn = _KR['__conn__']
    row = conn.execute("SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
    v = None
    if row and row[0]:
        try:
            v = _j.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            v = None
    _KR[code] = v
    _KR_ORD.append(code)
    if len(_KR_ORD) > 300:
        _KR.pop(_KR_ORD.pop(0), None)
    return v


if __name__ == '__main__':
    main()
