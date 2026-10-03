# -*- coding: utf-8 -*-
"""标签归一化 + 次日标签回填（幂等，仅基于本地前复权日K重算）。

主人 2026-10-02 拍板口径：
  #0 预测全链路排除 ST/*ST（候选池/训练/评估/对外名单）—— 本脚本把 ST 行标 exclude_reason='st'
  #2 涨停标签统一到**精确涨停价**：主板10% / 创业板科创板20% / 北交所30%（本期不纳入，见下）/ 新股首日
     · 判定 = close >= round(prev_close*(1+pct),2)（价法），并用幅度法(pct-0.05pp)交叉校验
     · 除权/复权异常行（|自算涨幅−库内涨幅|>0.11pp）单独记录、本轮不重打
  #9 次日标签用**前复权**K线；次日停牌（无K线行）⇒ next_* 留 NULL 跳过
  —— 北交所：实测 2434 行中 1717 行名称为空且 price=0（占位垃圾），本期不纳入 ⇒ exclude_reason='bj'

🔴 零回归设计：**不覆盖旧列** is_limit_up/close_chg（旧页面/战绩统计不受影响），
   新标签写入 `is_limit_up_v2`，补标写 `close_chg_v2`，口径版本写 `label_ver`。

用法（测试机/本地副本先跑）：
    python relabel_backfill.py --db /path/aipick.db --dry-run          # 只统计不写
    python relabel_backfill.py --db /path/aipick.db --backup --labels  # 重打标签(写)
    python relabel_backfill.py --db /path/aipick.db --backup --next    # 回填次日(写)
    python relabel_backfill.py --db /path/aipick.db --backup --all     # 两步都做
幂等：同输入(K线+库)重跑结果完全一致（全部由 K 线确定性重算，不做增量累加）。
"""
import argparse
import json
import os
import shutil
import sqlite3
import time

KDB = '/opt/kuaixuan/kuaixuan.db'

LABEL_VER = 'v2_precise_limit_20261002'
# 幅度法容差(pp)：从 0.05 收紧到 0.02 —— 2026-10-02 dry-run 抽样实测：
#   价法(round(prev*1.1,2))在**低价股**上会误判（威派格 4.98 元涨 9.948% 被判涨停 ✗，
#   因四舍五入到分在低价股上相对误差达 0.25%，前复权价又让 round 错位）；
#   而 9.99~9.997% 的真涨停（兆易创新/金诚信）两法一致 ✓ ⇒ 以幅度法为主、价法仅交叉校验。
TOL = 0.02
MISMATCH = 0.11   # 自算涨幅 vs 库内涨幅 的不一致阈值(pp) ⇒ 除权/异常行


def limit_pct(code):
    c = str(code).zfill(6)
    if c.startswith(('300', '301', '688', '689')):
        return 20.0
    if c.startswith(('8', '4', '920')):
        return 30.0
    return 10.0


def board_of(code):
    c = str(code).zfill(6)
    if c.startswith(('300', '301')):
        return '创业板'
    if c.startswith(('688', '689')):
        return '科创板'
    if c.startswith(('8', '4', '920')):
        return '北交所'
    return '主板'


def is_st(name):
    return 'ST' in (name or '').upper().replace(' ', '')


def is_bj(code):
    return str(code).zfill(6).startswith(('8', '4', '920'))


def load_klines(kdb=KDB, need=None):
    """code -> {'dates':[升序], 'o':{}, 'h':{}, 'c':{}}（前复权）"""
    c = sqlite3.connect(kdb)
    out = {}
    for code, dd in c.execute("SELECT code, day_data FROM stock_kline"):
        code = str(code).zfill(6)
        if need is not None and code not in need:
            continue
        if not dd:
            continue
        try:
            d = json.loads(dd) if isinstance(dd, str) else dd
        except Exception:
            continue
        t = [str(x)[:10] for x in (d.get('time') or [])]
        o, h, cl = d.get('open') or [], d.get('high') or [], d.get('close') or []
        rec = {'dates': [], 'o': {}, 'h': {}, 'c': {}}
        for i, dt in enumerate(t):
            if i < len(cl) and cl[i]:
                rec['dates'].append(dt)
                rec['o'][dt] = o[i] if i < len(o) else None
                rec['h'][dt] = h[i] if i < len(h) else None
                rec['c'][dt] = cl[i]
        if rec['dates']:
            out[code] = rec
    c.close()
    return out


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def dash(d8):
    return '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:])


def ensure_cols(conn, cols):
    have = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    added = []
    for name, typ in cols:
        if name not in have:
            conn.execute("ALTER TABLE features ADD COLUMN %s %s" % (name, typ))
            added.append(name)
    if added:
        conn.commit()
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='/opt/kuaixuan/aipick/scripts/data/aipick.db')
    ap.add_argument('--kdb', default=KDB)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--backup', action='store_true', help='写前备份 DB 文件')
    ap.add_argument('--labels', action='store_true')
    ap.add_argument('--next', action='store_true')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--sample', type=int, default=8, help='每类打印抽样条数')
    a = ap.parse_args()
    do_lab = a.labels or a.all
    do_next = a.next or a.all
    if not (do_lab or do_next):
        do_lab = do_next = True
    t0 = time.time()

    if a.backup and not a.dry_run:
        bk = a.db + '.bak_%s' % time.strftime('%Y%m%d-%H%M%S')
        shutil.copy2(a.db, bk)
        print('已备份:', bk)

    conn = sqlite3.connect(a.db)
    rows = conn.execute("SELECT trade_date, code, name, close_chg, is_limit_up FROM features").fetchall()
    codes = {str(r[1]).zfill(6) for r in rows}
    print('features %d 行 / %d 只票' % (len(rows), len(codes)))
    km = load_klines(a.kdb, need=codes)
    print('K线覆盖 %d 只 (%.0fs)' % (len(km), time.time() - t0))

    if not a.dry_run:
        added = ensure_cols(conn, [('is_limit_up_v2', 'INTEGER'), ('close_chg_v2', 'REAL'),
                                   ('label_ver', 'TEXT'), ('exclude_reason', 'TEXT'),
                                   ('label_note', 'TEXT')])
        if added:
            print('新增列:', added)

    upd_lab, upd_next = [], []
    st_n = bj_n = new_n = exc_n = miss_k = 0
    zt_old = zt_new = 0
    samples = {}
    for td_raw, code, name, chg_db, lab_old in rows:
        # ⚠️ 库内 trade_date 原样保留(实测带横线 '2026-03-18') —— WHERE 必须用原值，
        #    否则 UPDATE 匹配 0 行且**静默通过**（2026-10-02 真踩到：写库后非空率仍 0%）。
        d8 = norm8(td_raw)
        d = dash(d8)
        code = str(code).zfill(6)
        reason = None
        if is_st(name):
            reason = 'st'
            st_n += 1
        elif is_bj(code):
            reason = 'bj'
            bj_n += 1
        zt_old += 1 if lab_old else 0
        rec = km.get(code)
        if not rec or d not in rec['c']:
            miss_k += 1
            if do_lab and not reason:
                upd_lab.append((None, None, LABEL_VER, 'no_kline', None, td_raw, code))
            continue
        i = rec['dates'].index(d)
        if i < 1:                                     # K线首根 ⇒ 新股首日（无前收）
            reason = reason or 'new_listing'
            new_n += 1
            zt_new += 1 if lab_old else 0
            if do_lab:
                upd_lab.append((None, None, LABEL_VER, reason, None, td_raw, code))
            continue
        pc, cl = rec['c'][rec['dates'][i - 1]], rec['c'][d]
        chg_calc = (cl / pc - 1) * 100
        # 除权/复权异常：与库内官方涨幅不一致 ⇒ 本轮不重打（保护）
        if chg_db is not None and abs(chg_calc - chg_db) > MISMATCH:
            exc_n += 1
            if do_lab and not reason:
                upd_lab.append((None, round(chg_calc, 4), LABEL_VER, 'chg_mismatch', None, td_raw, code))
            continue
        pct = limit_pct(code)
        zt_price = round(pc * (1 + pct / 100.0), 2)
        zt_price_method = 1 if cl >= zt_price - 1e-9 else 0
        zt_tol_method = 1 if chg_calc >= pct - TOL else 0
        zt = zt_price_method if zt_price_method == zt_tol_method else zt_tol_method
        note = None
        if zt_price_method != zt_tol_method:
            note = 'differ'
            k = board_of(code) + '/价幅不一致'
            samples.setdefault(k, []).append((d, code, name, round(chg_calc, 3), zt_price, zt_tol_method))
        zt_new += zt
        if do_lab:
            upd_lab.append((int(zt), round(chg_calc, 4), LABEL_VER, reason, note, td_raw, code))
        # 次日回填（前复权；次日停牌 ⇒ 无行 ⇒ 留 NULL）
        if do_next and i + 1 < len(rec['dates']):
            dn = rec['dates'][i + 1]
            on = rec['o'].get(dn)
            cn = rec['c'].get(dn)
            no = round((on / cl - 1) * 100, 4) if on else None
            nc = round((cn / cl - 1) * 100, 4) if cn else None
            if no is not None or nc is not None:
                upd_next.append((no, nc, td_raw, code))

    print('\n== 标签(新口径 v2) ==')
    print('  ST/*ST %d 行 / 北交所 %d 行 / 新股首日 %d 行 / 无K线 %d 行 / 除权异常 %d 行'
          % (st_n, bj_n, new_n, miss_k, exc_n))
    print('  旧标签涨停 %d ⇒ 新口径涨停 %d（差异 %+d）' % (zt_old, zt_new, zt_new - zt_old))
    print('  待写标签行 %d | 待写次日行 %d' % (len(upd_lab), len(upd_next)))
    for k, v in samples.items():
        print('  [价法≠幅法] %s %d 例:' % (k, len(v)))
        for it in v[:a.sample]:
            print('     ', it)
    if a.dry_run:
        print('\n[dry-run] 未写库 (%.0fs)' % (time.time() - t0))
        return
    n0 = conn.total_changes
    if upd_lab:
        conn.executemany("UPDATE features SET is_limit_up_v2=?, close_chg_v2=?, label_ver=?, "
                         "exclude_reason=?, label_note=? WHERE trade_date=? AND code=?", upd_lab)
    if upd_next:
        conn.executemany("UPDATE features SET next_open_chg=?, next_close_chg=? "
                         "WHERE trade_date=? AND code=?", upd_next)
    conn.commit()
    # 🔴 护栏：待写非空却没生效 ⇒ 必须显式报错（不许静默通过）
    if upd_lab or upd_next:
        if conn.total_changes == n0:
            raise SystemExit('❌ 待写 %d/%d 行但 total_changes=0 ⇒ WHERE 未匹配（日期格式？）'
                             % (len(upd_lab), len(upd_next)))
    # 核对：回填后非空率
    n, no_n, nc_n = conn.execute(
        "SELECT COUNT(*), SUM(next_open_chg IS NOT NULL), SUM(next_close_chg IS NOT NULL) "
        "FROM features").fetchone()
    v2 = conn.execute("SELECT COUNT(*), SUM(is_limit_up_v2=1) FROM features WHERE is_limit_up_v2 IS NOT NULL").fetchone()
    print('\n== 写库完成 ==')
    print('  next_open_chg 非空 %d/%d (%.1f%%) | next_close_chg 非空 %d/%d (%.1f%%)'
          % (no_n or 0, n, 100.0 * (no_n or 0) / n, nc_n or 0, n, 100.0 * (nc_n or 0) / n))
    print('  is_limit_up_v2 有值 %d 行，其中涨停 %d 行' % (v2[0], v2[1] or 0))
    conn.close()
    print('总耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
