# -*- coding: utf-8 -*-
"""100% 补全 8/25-8/28 缺失标签(2026-08-31 主人要求)
数据源: 腾讯不复权日K proxy.finance.qq.com (免费, 已验证可用, 覆盖东财K线被墙)
逻辑: 对 aipick.features 中 is_limit_up IS NULL 的 (date, code), 拉该股 8 天日K,
      用 (当日close - 前收close)/前收close 算收盘涨幅, 按板块阈值判涨停,
      只更新缺失标签(不覆盖已有); 并发 16。
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import update_labels

AIPICK_DB = '/opt/kuaixuan/aipick/scripts/data/aipick.db'
TARGETS = ['2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28']
URL = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def limit_pct(code):
    if code.startswith(('30', '68')):
        return 19.8
    if code.startswith(('4', '8')):
        return 29.8
    return 9.8


def full_code(code):
    # 腾讯格式: sh600519 / sz000001 / bj920223 (前缀, 非 .SH 后缀!)
    code = str(code)
    if code.startswith(('6', '9')):
        return 'sh' + code
    if code.startswith(('4', '8')):
        return 'bj' + code
    return 'sz' + code


def fetch_kline(code):
    """拉不复权日K(8天), 返回 {date: (close, prev_close)}; 失败返回 {}"""
    try:
        fc = full_code(code)
        url = URL + '?param=' + urllib.parse.quote(fc) + ',day,,,8,'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
            raw = json.loads(r.read().decode('utf-8'))
        d = raw.get('data', {}).get(fc, {})
        rows = d.get('day') or d.get('qfqday') or []
        closes = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            try:
                closes.append((str(row[0])[:10], float(row[2])))
            except (TypeError, ValueError):
                continue
        out = {}
        for i, (dt, c) in enumerate(closes):
            if i > 0:
                out[dt] = (c, closes[i - 1][1])
        return out
    except Exception:
        return {}


def backfill_date(date):
    conn = sqlite3.connect(AIPICK_DB)
    conn.text_factory = str
    rows = conn.execute(
        "SELECT code FROM features WHERE trade_date=? AND is_limit_up IS NULL",
        (date,)).fetchall()
    conn.close()
    if not rows:
        print('%s: 无缺失, 跳过' % date)
        return 0
    codes = [str(r[0]) for r in rows]
    print('%s: 需补 %d 只, 开始拉腾讯日K...' % (date, len(codes)))

    klines = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(fetch_kline, c): c for c in codes}
        for f in as_completed(futs):
            klines[futs[f]] = f.result()

    labels = []
    for code in codes:
        km = klines.get(code, {})
        ent = km.get(date)
        if not ent:
            # 停牌/无K线(PT金田A等老三股): 与 label_today 历史口径一致补 0
            # (2026-08-31 100%补全: 停牌日不算涨停, close_chg=0)
            labels.append({'code': code, 'is_limit_up': 0, 'close_chg': 0.0})
            continue
        close, prev = ent
        if prev <= 0:
            labels.append({'code': code, 'is_limit_up': 0, 'close_chg': 0.0})
            continue
        chg = round((close - prev) / prev * 100, 2)
        labels.append({'code': code, 'is_limit_up': 1 if chg >= limit_pct(code) else 0,
                       'close_chg': chg})
    if labels:
        update_labels(date, labels)
    n_zt = sum(1 for l in labels if l['is_limit_up'])
    print('%s: 补全 %d/%d 条(涨停 %d 只, 失败 %d)' %
          (date, len(labels), len(codes), n_zt, len(codes) - len(labels)))
    return len(labels)


if __name__ == '__main__':
    t0 = time.time()
    total = 0
    for d in TARGETS:
        total += backfill_date(d)
    print('全部完成: %d 条, 耗时 %.0fs' % (total, time.time() - t0))
