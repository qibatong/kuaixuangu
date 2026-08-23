#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史日现涨(当日收盘涨幅)回填: 把 auction_daily_history 各日出现的股票,
用**新浪日K**(独立域名, 不受东财限流)算当日收盘涨跌幅, 写入 close_change_history 表。
此后竞价异动页历史回看直接读库, 不再请求外部K线接口。幂等, 可重复运行。
用法: 在服务器 backend 目录下用虚拟环境执行:
    /opt/bid-venv/bin/python scripts/backfill_close_change.py
"""
import sys, os, json, sqlite3, threading, time
import urllib.request, ssl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.core import config          # noqa: E402

CONCURRENCY = 3       # 并发数(温柔)
DELAY = 0.10          # 每次请求间隔秒
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

_kline_cache = {}
_lock = threading.Lock()


def sina_kline(code, datalen=160):
    """新浪日K → {date: pct} (收盘涨跌幅%); 失败返回 {}"""
    sym = "sh" + code if code[0] == "6" else ("bj" + code if code[0] in "48" else "sz" + code)
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={datalen}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    try:
        with urllib.request.urlopen(req, timeout=12, context=SSL_CTX) as r:
            arr = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return {}
    out = {}
    prev = None
    for row in arr:
        try:
            d = str(row["day"])[:10]
            c = float(row["close"])
        except Exception:
            continue
        if prev:
            out[d] = round((c - prev) / prev * 100, 2)
        prev = c
    return out


def fetch(code):
    if code in _kline_cache:
        return
    with _lock:
        time.sleep(DELAY)
        _kline_cache[code] = sina_kline(code)


def gather_codes():
    conn = sqlite3.connect(config.DB_FILE)
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM auction_daily_history ORDER BY date")]
    by_date = {}
    all_codes = set()
    for date in dates:
        codes = set()
        for (tab, list_json) in conn.execute(
                "SELECT tab, list FROM auction_daily_history WHERE date=?", (date,)):
            try:
                arr = json.loads(list_json) if list_json else []
            except Exception:
                arr = []
            for it in arr:
                c = it.get("code") if isinstance(it, dict) else None
                if c:
                    codes.add(c)
        if codes:
            by_date[date] = codes
            all_codes |= codes
    conn.close()
    return by_date, all_codes


def main():
    by_date, all_codes = gather_codes()
    if not by_date:
        print("auction_daily_history 无历史数据, 无需回填")
        return
    print(f"共 {len(by_date)} 个历史交易日, 去重后 {len(all_codes)} 只股票")
    clist = sorted(all_codes)
    wpos = [0]

    def worker():
        while True:
            with _lock:
                if wpos[0] >= len(clist):
                    return
                idx = wpos[0]
                wpos[0] += 1
            fetch(clist[idx])
            if (idx + 1) % 100 == 0:
                print(f"  进度 {idx+1}/{len(clist)}", flush=True)

    threads = [threading.Thread(target=worker) for _ in range(CONCURRENCY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = sqlite3.connect(config.DB_FILE)
    total = 0
    for date, codes in sorted(by_date.items()):
        rows = []
        for code in codes:
            pct = _kline_cache.get(code, {}).get(date)
            if pct is None:
                continue
            rows.append((date, code, pct))
        conn.executemany(
            "INSERT OR REPLACE INTO close_change_history(date,code,pct) VALUES(?,?,?)", rows)
        conn.commit()
        total += len(rows)
        print(f"{date}: {len(codes)} 只, 已写入 {len(rows)} 只")
    conn.close()
    print(f"完成, 共写入 {total} 条(日,股)")

if __name__ == "__main__":
    main()