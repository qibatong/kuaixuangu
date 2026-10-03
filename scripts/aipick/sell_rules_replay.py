# -*- coding: utf-8 -*-
"""卖出规则回放（交易层 A：量化"交易层能挽回多少"）。

主人 2026-10-03 拍板：4 种卖出规则做 134 天回放，输出可买组命中率/大面率/期望收益日序列 + 同日配对检验。

⚠️ 数据可得性（实测）：
  猫爪 minute **仅最新交易日**、fetcher 只有 minute(当日)/day/week/month ⇒ **分钟级历史不可得**。
  故按可回放性分层，结果里**逐条标注**：
    A 次日开盘卖   —— ✅ 精确（日K open）
    B 冲高卖       —— ⚠️ 乐观上界（用次日 high 代替"开盘后30分钟最高价"，真实值必然 ≤ 此上界）
    C 破板卖       —— ⚠️ 保守近似（次日 low ≤ 买入日收盘 ⇒ 视为破板，按买入日收盘价走）
    D 时间止损     —— ❌ 无法回放（需分钟；改为每日留存 minute，积累后可精确回放）
    E 次日收盘卖   —— ✅ 精确（对照）
  买入价 = pick_daily.auc_price（9:25 竞价价，已实证）。
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))
KDB = '/opt/kuaixuan/kuaixuan.db'
BF = -0.05          # 大面阈值（持有期亏损 ≥5%）


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def dash(d8):
    return '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--kdb', default=KDB)
    ap.add_argument('--topn', type=int, default=10, help='每条线每日取前 N（按名次）')
    ap.add_argument('--out', default='/tmp/kx_v3/sell_replay.csv')
    ap.add_argument('--daily-out', default='/tmp/kx_v3/sell_replay_daily.csv')
    a = ap.parse_args()
    t0 = time.time()
    c = sqlite3.connect(a.db)
    rows = c.execute(
        "SELECT trade_date, line, code, rank, auc_price, is_limit_up, "
        "COALESCE(fill_grade,'') FROM pick_daily WHERE rank<=? AND auc_price IS NOT NULL "
        "ORDER BY trade_date, line, rank", (a.topn,)).fetchall()
    print("名单 %d 行（top%d, 四线）" % (len(rows), a.topn), flush=True)

    # 内存有界：按需单票查 + LRU
    kc = sqlite3.connect(a.kdb)
    _cache, _order = {}, []

    def kl(code):
        if code in _cache:
            return _cache[code]
        r = kc.execute("SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
        m = {}
        if r and r[0]:
            try:
                o = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                t = [str(x)[:10] for x in (o.get('time') or [])]
                op, hi, lo, cl = (o.get('open') or [], o.get('high') or [],
                                  o.get('low') or [], o.get('close') or [])
                m = {t[i]: (op[i] if i < len(op) else None, hi[i] if i < len(hi) else None,
                            lo[i] if i < len(lo) else None, cl[i] if i < len(cl) else None)
                     for i in range(len(t)) if i < len(cl) and cl[i]}
            except Exception:
                m = {}
        _cache[code] = m
        _order.append(code)
        if len(_order) > 400:
            _cache.pop(_order.pop(0), None)
        return m

    RES = {'A_next_open': [], 'B_high_ub': [], 'C_break_zt': [], 'E_next_close': []}
    skipped = 0
    for td, line, code, rank, auc, zt, fg in rows:
        if fg == 'none':                       # 一字买不进 ⇒ 不入可买组
            continue
        code = str(code).zfill(6)
        d8 = norm8(td); d = dash(d8)
        m = kl(code)
        seq = sorted(m)
        if d not in m:
            skipped += 1
            continue
        i = seq.index(d)
        if i + 1 >= len(seq):
            skipped += 1
            continue
        nxt = m[seq[i + 1]]
        o_n, h_n, l_n, c_n = nxt
        buy_close = m[d][3]
        if not auc or not o_n or not buy_close:
            skipped += 1
            continue
        rec = {'trade_date': d8, 'line': line, 'code': code, 'rank': rank,
               'hit': int(zt or 0), 'auc': auc}
        # A 次日开盘卖
        rec['A_ret'] = (o_n / auc - 1) * 100
        # B 冲高卖（乐观上界：次日 high）
        rec['B_ret'] = (h_n / auc - 1) * 100 if h_n else None
        # C 破板卖（保守：次日 low ≤ 买入日收盘 ⇒ 按买入日收盘价走；否则收盘走）
        if l_n is not None and l_n <= buy_close + 1e-9:
            rec['C_ret'] = (buy_close / auc - 1) * 100
        else:
            rec['C_ret'] = (c_n / auc - 1) * 100 if c_n else None
        # E 次日收盘卖
        rec['E_ret'] = (c_n / auc - 1) * 100 if c_n else None
        for k, v in (('A_next_open', rec['A_ret']), ('B_high_ub', rec['B_ret']),
                     ('C_break_zt', rec['C_ret']), ('E_next_close', rec['E_ret'])):
            if v is not None:
                RES[k].append({**{kk: rec[kk] for kk in ('trade_date', 'line', 'code', 'rank', 'hit')}, 'ret': v})
    print("有效样本 %d（跳过 %d）%.0fs" % (sum(len(v) for v in RES.values()), skipped, time.time() - t0), flush=True)

    # 日序列 + 汇总
    daily_rows = []
    summ = {}
    for rule, rs in RES.items():
        dd = {}
        for r in rs:
            dd.setdefault(r['trade_date'], []).append(r)
        for d8, lst in sorted(dd.items()):
            rets = [x['ret'] for x in lst]
            daily_rows.append((d8, rule, len(lst), sum(rets) / len(rets),
                               100.0 * sum(x['hit'] for x in lst) / len(lst),
                               100.0 * sum(1 for x in rets if x <= BF * 100) / len(lst)))
        allr = [x['ret'] for x in rs]
        days = sorted({x['trade_date'] for x in rs})
        nav, peaks, mdd = 1.0, 1.0, 0.0
        for d8 in days:
            rets = [x['ret'] for x in dd[d8]]
            nav *= (1 + (sum(rets) / len(rets)) / 100.0)
            peaks = max(peaks, nav)
            mdd = min(mdd, nav / peaks - 1)
        summ[rule] = {
            'days': len(days), 'n': len(rs),
            'ret_mean': round(sum(allr) / len(allr), 3),
            'win': round(100.0 * sum(1 for x in allr if x > 0) / len(allr), 1),
            'bigface': round(100.0 * sum(1 for x in allr if x <= BF * 100) / len(allr), 1),
            'hit': round(100.0 * sum(x['hit'] for x in rs) / len(rs), 1),
            'mdd': round(100 * mdd, 2),
            'ret_median': round(sorted(allr)[len(allr) // 2], 3),
        }
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write('rule,days,n,ret_mean,win_rate,bigface_rate,hit_rate,max_drawdown,ret_median\n')
        for k, v in summ.items():
            f.write('%s,%d,%d,%.3f,%.1f,%.1f,%.1f,%.2f,%.3f\n' % (k, v['days'], v['n'], v['ret_mean'],
                                                                v['win'], v['bigface'], v['hit'], v['mdd'], v['ret_median']))
    with open(a.daily_out, 'w', encoding='utf-8') as f:
        f.write('trade_date,rule,n,ret_mean,hit_rate,bigface_rate\n')
        for r in daily_rows:
            f.write('%s,%s,%d,%.4f,%.2f,%.2f\n' % r)
    print("\n=== 卖出规则汇总（可买组, top%d, 买入价=9:25 竞价价）===" % a.topn)
    print("  %-14s %5s %6s %9s %8s %8s %8s %9s" % ('规则', '天数', 'n', '期望收益%', '胜率%', '大面率%', '命中率%', '最大回撤%'))
    for k, v in summ.items():
        print("  %-14s %5d %6d %9.3f %8.1f %8.1f %8.1f %9.2f"
              % (k, v['days'], v['n'], v['ret_mean'], v['win'], v['bigface'], v['hit'], v['mdd']))
    print("\n注: A/E 精确; B 为乐观上界(次日 high); C 为保守近似(破板按买入日收盘价走); D(时间止损)需分钟数据, 未回放")
    print("输出: %s / %s" % (a.out, a.daily_out))
    c.close(); kc.close()


if __name__ == '__main__':
    main()
