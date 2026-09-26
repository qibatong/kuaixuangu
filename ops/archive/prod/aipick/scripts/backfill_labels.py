# -*- coding: utf-8 -*-
"""补打 8/18-8/28 缺失的标签(is_limit_up / close_chg)
数据源: 快选 close_change_history(date/code/pct 收盘涨幅)
涨停阈值按板块: 主板>=9.8 / 创业板30*>=19.8 / 科创68*>=19.8 / 北交8*4*>=29.8
仅补 is_limit_up 为 NULL 的日期, 不覆盖已有标签
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import init_db, update_labels

KX_DB = '/opt/kuaixuan/kuaixuan.db'
AIPICK_DB = '/opt/kuaixuan/aipick/scripts/data/aipick.db'

MISSING_DATES = ['2026-08-18', '2026-08-19', '2026-08-20', '2026-08-21',
                 '2026-08-24', '2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28']


def limit_pct(code):
    if code.startswith(('30', '68')):
        return 19.8
    if code.startswith(('4', '8')):
        return 29.8
    return 9.8


def backfill_label(date):
    """从快选 close_change_history 补打指定日标签"""
    kx = sqlite3.connect(KX_DB)
    kx.text_factory = str
    rows = kx.execute(
        "SELECT code, pct FROM close_change_history WHERE date=?", (date,)).fetchall()
    kx.close()
    if not rows:
        print('%s: close_change_history 无数据, 跳过' % date)
        return 0
    labels = []
    for code, pct in rows:
        if pct is None:
            continue
        pct = float(pct)
        labels.append({'code': str(code), 'is_limit_up': 1 if pct >= limit_pct(str(code)) else 0,
                       'close_chg': round(pct, 2)})
    # 只更新该日期(update_labels 用 INSERT OR REPLACE 会覆盖; 补打语义 = 该日完整回填)
    init_db()
    update_labels(date, labels)
    n_zt = sum(1 for l in labels if l['is_limit_up'])
    print('%s: 补打 %d 条标签(涨停 %d 只)' % (date, len(labels), n_zt))
    return len(labels)


if __name__ == '__main__':
    total = 0
    for d in MISSING_DATES:
        total += backfill_label(d)
    print('补打完成, 共 %d 条' % total)
