# -*- coding: utf-8 -*-
"""
同花顺板块历史回补脚本(两阶段: 本地导出 + 远端导入)
====================================================
数据源: 同花顺行业板块(88 开头, 约 140 个)
- 板块列表: https://q.10jqka.com.cn/thshy/ (GBK HTML)
- 板块日K:  https://d.10jqka.com.cn/v4/line/bk_{code}/01/last.js (JSONP, 最近 140 条)
  字段: 日期,开,高,低,收,量,额 (YYYYMMDD 格式)
  涨跌幅 = (今收-昨收)/昨收*100 (自算)

用法:
  [本地]   python backfill_ths_history.py export 30 out.json
  [测试机] /opt/bid-venv/bin/python /tmp/backfill_ths_history.py import /tmp/out.json
"""
import datetime
import gzip
import json
import re
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LIST_URL = "https://q.10jqka.com.cn/thshy/"
KLINE_HOSTS = ["https://d.10jqka.com.cn", "https://d.10jqka.com.cn"]
HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://q.10jqka.com.cn/",
    "Accept-Encoding": "gzip",
}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 注: ths 是独立 source(不影响 kpl 数据), 不需要保留任何日期


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        data = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return data


def fetch_board_list():
    """行业板块列表 -> [(code, name)] (GBK 页面)"""
    html = _get(LIST_URL).decode("gbk", "ignore")
    pairs = re.findall(r'code/(88\d{4})/"[^>]*>([^<]{1,12})</a>', html)
    return [(c, n.strip()) for c, n in pairs]


def fetch_kline(code, days=35):
    """板块日K(最近 N 条) -> {date(YYYY-MM-DD): (close, amount)}"""
    for host in KLINE_HOSTS:
        try:
            body = _get(host + "/v4/line/bk_%s/01/last.js" % code).decode("utf-8", "ignore")
        except Exception:
            continue
        m = re.search(r'\((.*)\)\s*$', body, re.S)
        if not m:
            continue
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        data = d.get("data") or ""
        rows = data.split(";")
        out = {}
        for row in rows[-days:]:
            parts = row.split(",")
            if len(parts) < 7 or not parts[0]:
                continue
            dt = parts[0]
            # YYYYMMDD -> YYYY-MM-DD (与 daily_sector_top 其它源格式一致)
            if len(dt) == 8 and dt.isdigit():
                dt = "%s-%s-%s" % (dt[:4], dt[4:6], dt[6:])
            try:
                close = float(parts[4])   # 收盘
                amount = float(parts[6])  # 成交额(元)
            except (ValueError, IndexError):
                continue
            out[dt] = (close, amount)
        if out:
            return out
    return {}


def build_daily_top(days=30):
    """拉取 -> {date: [top10]}, 按日期升序"""
    boards = fetch_board_list()
    print("行业板块数: %d" % len(boards), flush=True)
    if not boards:
        return None

    agg = {}   # date -> {code: (name, close, amount)}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for code, name, klines in ex.map(lambda b: (b[0], b[1], fetch_kline(b[0], days + 5)), boards):
            for dt, (close, amount) in klines.items():
                agg.setdefault(dt, {})[code] = (name, close, amount)

    dates = sorted(agg.keys())
    print("交易日数: %d (示例: %s ... %s)" % (len(dates), dates[0] if dates else "-", dates[-1] if dates else "-"), flush=True)
    if not dates:
        return None

    out = {}
    for d in dates:
        # 计算涨跌幅: (今收-昨收)/昨收*100
        items = []
        for code, (name, close, amount) in agg[d].items():
            prev_close = agg[d].get(code)
            # 找前一交易日收盘
            prev = None
            for pd in dates:
                if pd >= d:
                    break
                if code in agg[pd]:
                    prev = agg[pd][code][1]
            if prev is None or prev <= 0:
                continue
            chg = (close - prev) / prev * 100
            items.append((code, name, chg, amount))
        items.sort(key=lambda x: x[2], reverse=True)
        items = items[:10]
        out[d] = [{
            "rank": i + 1,
            "boardCode": code,
            "name": name,
            "strength": round(chg * 100, 1),
            "change": round(chg, 2),
            "amount": amount,
            "mainNet": 0.0,
            "volRatio": 0.0,
            "floatMv": 0.0,
        } for i, (code, name, chg, amount) in enumerate(items)]
    return out


def do_import(path):
    """读取 JSON 落库 daily_sector_top(source='ths')"""
    sys.path.insert(0, "/opt/kuaixuan/backend")
    from app.db import database
    with open(path, "r", encoding="utf-8") as f:
        out = json.load(f)
    dates = sorted(out.keys())
    print("待落库交易日: %d source=ths" % len(dates), flush=True)
    conn = database.get_conn()
    n = 0
    for d in dates:
        conn.execute(
            "INSERT OR REPLACE INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, "ths", json.dumps(out[d], ensure_ascii=False), int(time.time())))
        n += 1
    conn.commit()
    conn.close()
    print("已落库: %d 天" % n, flush=True)
    return n


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "export"
    if mode == "export":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        out_path = sys.argv[3] if len(sys.argv) > 3 else "ths_history.json"
        out = build_daily_top(days)
        if out is None:
            return 1
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print("已导出: %s (%d 天)" % (out_path, len(out)), flush=True)
        return 0
    elif mode == "import":
        path = sys.argv[2] if len(sys.argv) > 2 else "ths_history.json"
        return 0 if do_import(path) else 1
    else:
        print("用法: export [天数] [out.json] | import [in.json]", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())