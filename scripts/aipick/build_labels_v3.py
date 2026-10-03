# -*- coding: utf-8 -*-
"""标签 v3：官方涨停价口径（主）+ 停牌/新股/ST/北交所 排除（幂等）。

口径（主人 2026-10-02 拍板）：
  涨停 = 前复权收盘 >= 官方涨停价 `price_limit.up_limit`（实测与涨停池真值一致率 98.69%，
        13 例差异全为"开过板但收盘回封"，故以收盘价为准；is_break 另作质量标签）
  排除 = ST/*ST(name) / 北交所 / 新股首日(listed_date==交易日) / 停牌(suspend_type=S 全天) / 无数据
  🔴 旧列 is_limit_up / is_limit_up_v2 一律不动（零回归）；v3 写入 `is_limit_up_v3`。

用法:
    python build_labels_v3.py --db /tmp/x.db --dry-run
    python build_labels_v3.py --db /tmp/x.db --backup
"""
import argparse
import json
import shutil
import sqlite3
import time

KDB = '/opt/kuaixuan/kuaixuan.db'
VER = 'v3_official_pricelimit_20261002'


def board_bj(code):
    return str(code).zfill(6).startswith(('8', '4', '920'))


def is_st(name):
    return 'ST' in (name or '').upper().replace(' ', '')


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--kdb', default=KDB)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--backup', action='store_true')
    a = ap.parse_args()
    if a.backup and not a.dry_run:
        bk = a.db + '.bak_%s' % time.strftime('%Y%m%d-%H%M%S')
        shutil.copy2(a.db, bk)
        print('已备份:', bk)
    t0 = time.time()
    c = sqlite3.connect(a.db)
    have = {r[1] for r in c.execute("PRAGMA table_info(features)")}
    if not a.dry_run:
        for col in ('is_limit_up_v3', 'label_src_v3', 'exclude_v3'):
            if col not in have:
                c.execute("ALTER TABLE features ADD COLUMN %s %s"
                          % (col, 'INTEGER' if col.startswith('is_limit') else 'TEXT'))
        c.commit()

    # 官方涨停价
    pl = {}
    try:
        for td, code, up in c.execute("SELECT trade_date, code, up_limit FROM price_limit "
                                      "WHERE up_limit IS NOT NULL"):
            pl[(td, str(code).zfill(6))] = up
    except Exception as e:
        print('price_limit 不可用:', str(e)[:80])
    # 新股上市日（首日排除）
    listed = {}
    try:
        for code, ld in c.execute("SELECT code, listed_date FROM new_share_tbl"):
            listed[str(code).zfill(6)] = norm8(ld)
    except Exception:
        pass
    # 停牌（S 且覆盖全天/主要时段 ⇒ 该日排除）
    susp = set()
    try:
        for td, code, ty, tm in c.execute("SELECT trade_date, code, suspend_type, suspend_timing "
                                          "FROM suspend_tbl"):
            if str(ty).upper().startswith('S'):
                susp.add((td, str(code).zfill(6)))
    except Exception:
        pass
    print('官方涨停价 %d 条 / 新股 %d / 停牌 %d | %.0fs'
          % (len(pl), len(listed), len(susp), time.time() - t0), flush=True)

    # K 线（只取需要的票）
    rows = c.execute("SELECT trade_date, code, name, close_chg FROM features").fetchall()
    need = {str(r[1]).zfill(6) for r in rows}
    km = {}
    k = sqlite3.connect(a.kdb)
    for code, dd in k.execute("SELECT code, day_data FROM stock_kline"):
        code = str(code).zfill(6)
        if code not in need or not dd:
            continue
        try:
            o = json.loads(dd) if isinstance(dd, str) else dd
        except Exception:
            continue
        t = [str(x)[:10] for x in (o.get('time') or [])]
        cl = o.get('close') or []
        km[code] = {t[i]: cl[i] for i in range(len(t)) if i < len(cl) and cl[i]}
    k.close()
    print('K线 %d 只 | %.0fs' % (len(km), time.time() - t0), flush=True)

    upd, cnt = [], {'official': 0, 'selfcalc': 0, 'zt_off': 0}
    ex_n = {'st': 0, 'bj': 0, 'new_listing': 0, 'suspend': 0, 'no_data': 0}
    for td, code, name, chg_db in rows:
        code = str(code).zfill(6)
        d8 = norm8(td)
        d = '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:])
        ex = None
        if is_st(name):
            ex = 'st'
        elif board_bj(code):
            ex = 'bj'
        elif listed.get(code) and listed[code] == d8:
            ex = 'new_listing'
        elif (td, code) in susp:
            ex = 'suspend'
        if ex:
            ex_n[ex] += 1
        m = km.get(code)
        if not m or d not in m:
            ex = ex or 'no_data'
            if not ex_n.get('no_data') or ex == 'no_data':
                ex_n['no_data'] += 1
            upd.append((None, None, ex, td, code))
            continue
        cl = m[d]
        up = pl.get((td, code))
        if up is not None:
            zt = 1 if cl >= up - 0.005 else 0
            src = 'official'
            cnt['official'] += 1
            cnt['zt_off'] += zt
        else:                                       # 回退：自算（涨幅幅法）
            ds = sorted(m)
            i = ds.index(d)
            if i < 1:
                ex = ex or 'new_listing'
                upd.append((None, None, ex, td, code))
                continue
            chg = (cl / m[ds[i - 1]] - 1) * 100
            lim = 20.0 if code.startswith(('300', '301', '688', '689')) else 10.0
            zt = 1 if chg >= lim - 0.02 else 0
            src = 'selfcalc'
            cnt['selfcalc'] += 1
        upd.append((int(zt), src, ex, td, code))
    print('官方价口径 %d 行（其中涨停 %d）| 自算回退 %d 行 | 排除 %s'
          % (cnt['official'], cnt['zt_off'], cnt['selfcalc'], ex_n))
    if a.dry_run:
        print('[dry-run] 未写库 %.0fs' % (time.time() - t0))
        return
    n0 = c.total_changes
    c.executemany("UPDATE features SET is_limit_up_v3=?, label_src_v3=?, exclude_v3=? "
                  "WHERE trade_date=? AND code=?", upd)
    c.commit()
    if c.total_changes == n0 and upd:
        raise SystemExit('❌ 待写 %d 行但 total_changes=0（日期格式？）' % len(upd))
    q = c.execute("SELECT COUNT(*), SUM(is_limit_up_v3), SUM(label_src_v3='official'), "
                  "SUM(label_src_v3='selfcalc'), SUM(exclude_v3 IS NOT NULL) FROM features").fetchone()
    print('写库完成：v3 有值 %d（涨停 %s）| official %s | selfcalc %s | 排除 %s'
          % (q[0], q[1], q[2], q[3], q[4]))
    print('总耗时 %.0fs' % (time.time() - t0))
    c.close()


if __name__ == '__main__':
    main()
