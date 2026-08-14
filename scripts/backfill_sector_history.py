# -*- coding: utf-8 -*-
"""
东财板块历史回补脚本(两阶段)
=============================
背景: 板块轮动调度器 8.14 才上线, 历史没有数据。开盘啦历史板块接口(apiv=w41)
实测 Index 日期参数不生效, 无法回补。改用东方财富板块日K回补。

注意: 测试机 IP 被东财 push2his(日K域名)风控, 而本地网络正常, 所以分两阶段:

    [本地]  python backfill_sector_history.py export 30 out.json
            - 拉东财概念板块(m:90+t:3) + 行业板块(m:90+t:2) 全列表 ~500 个
            - 并发拉每个板块近 N 个交易日日K(push2his)
            - 按每日涨跌幅排序取 Top10, 生成 {date: [board...]} JSON(不碰数据库)

    [测试机] /opt/bid-venv/bin/python /tmp/backfill_sector_history.py import /tmp/out.json
            - 读取 JSON 落库 daily_sector_top(与 record_today_top 字段一致)
            - 跳过 '2026-08-14'(保留开盘啦真实数据, 不覆盖)
"""
import datetime
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 东财接口与本地环境无关的常量(硬编码, 避免依赖 backend config)
EM_CLIST = "https://push2dycalc.eastmoney.com/api/qt/clist/get"   # 行情计算域名(测试机也通)
EM_KLINE_HOSTS = [                                                 # 日K多域名轮询
    "https://push2delay.eastmoney.com",                            # 延迟行情(实测可用, 风控宽松)
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://33.push2his.eastmoney.com",
    "https://48.push2his.eastmoney.com",
    "https://92.push2his.eastmoney.com",
]
UT = "c92c50e6b0fab2c17cd5e276e9a79c42"
HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}

# 测试机 python 环境缺 CA 证书, 与 kpl.py 一致跳过证书校验(本地无影响)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 保留开盘啦真实数据的日期(不覆盖)
KEEP_KPL = {"2026-08-14"}

# 东财板块列表中的"统计型指数"(昨日连板/历史新高/打板表现等), 非真实板块, 过滤掉
FILTER_KW = ("昨日", "新高", "打板", "连板", "涨停", "跌停", "炸板", "首板", "晋级", "破板")


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_board_list():
    """概念+行业板块列表 -> [(bk_code, name)]"""
    out = []
    for fs in ("m:90+t:3+f:!50", "m:90+t:2+f:!50"):
        qs = urllib.parse.urlencode({
            "pn": 1, "pz": 500, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f12", "fs": fs, "fields": "f12,f14", "ut": UT,
        })
        try:
            data = _get(EM_CLIST + "?" + qs)
        except Exception as e:
            print("板块列表拉取失败 fs=%s err=%s" % (fs, e), flush=True)
            continue
        diff = (data.get("data") or {}).get("diff") or []
        for it in diff:
            code, name = it.get("f12"), it.get("f14")
            if not code or not name:
                continue
            if any(kw in name for kw in FILTER_KW):
                continue
            out.append((code, name))
    return out


def fetch_kline(bk, beg, end, retries=2):
    """板块日K(多域名轮询+失败重试) -> {date: (change_pct, amount)}"""
    qs = urllib.parse.urlencode({
        "secid": "90." + bk,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f53,f57,f59",   # 日期/收盘/成交额/涨跌幅
        "klt": 101, "fqt": 1, "beg": beg, "end": end, "ut": UT,
    })
    for attempt in range(retries + 1):
        for host in EM_KLINE_HOSTS:
            try:
                data = _get(host + "/api/qt/stock/kline/get?" + qs, timeout=8)
            except Exception:
                continue
            klines = (data.get("data") or {}).get("klines") or []
            if not klines:
                continue
            out = {}
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 4:
                    out[parts[0]] = (float(parts[3]), float(parts[2]))  # 涨跌幅%, 成交额(元)
            return out
        time.sleep(0.5 * (attempt + 1))
    return {}


def build_daily_top(days):
    """拉取并聚合 -> {date: [top10 board dict]}(按日期升序)"""
    now = time.gmtime(time.time() + 8 * 3600)
    end = time.strftime("%Y%m%d", now)
    end_dt = datetime.date(now.tm_year, now.tm_mon, now.tm_mday)
    beg_dt = end_dt - datetime.timedelta(days=days * 2)   # 日历日裕量, 覆盖 N 个交易日
    beg = beg_dt.strftime("%Y%m%d")
    print("范围: %s -> %s (约%d个交易日)" % (beg, end, days), flush=True)

    boards = fetch_board_list()
    print("板块数: %d" % len(boards), flush=True)
    if not boards:
        print("板块列表为空, 退出", flush=True)
        return None

    agg = {}   # date -> {bk: (name, change, amount)}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for bk, name, klines in ex.map(lambda b: (b[0], b[1], fetch_kline(b[0], beg, end)), boards):
            for d, (chg, amt) in klines.items():
                agg.setdefault(d, {})[bk] = (name, chg, amt)

    dates = sorted(agg.keys())
    print("交易日数: %d (示例: %s ... %s)" % (len(dates), dates[0] if dates else "-", dates[-1] if dates else "-"), flush=True)
    if not dates:
        print("无任何日K数据, 退出", flush=True)
        return None

    out = {}
    for d in dates:
        if d in KEEP_KPL:
            continue
        items = sorted(agg[d].items(), key=lambda kv: kv[1][1], reverse=True)[:10]
        out[d] = [{
            "rank": i + 1,
            "boardCode": bk,
            "name": v[0],
            "strength": round(v[1] * 100, 1),   # 涨跌幅% * 100, 与开盘啦强度同量级
            "change": round(v[1], 2),
            "amount": v[2],
            "mainNet": 0.0,
            "volRatio": 0.0,
            "floatMv": 0.0,
        } for i, (bk, v) in enumerate(items)]
    return out


def do_import(path, source="em"):
    """读取 JSON 落库 daily_sector_top(source='em' for backfill data)"""
    sys.path.insert(0, "/opt/kuaixuan/backend")
    from app.db import database
    with open(path, "r", encoding="utf-8") as f:
        out = json.load(f)
    dates = sorted(out.keys())
    print("待落库交易日: %d source=%s (跳过保留: %s)" % (len(dates), source, sorted(KEEP_KPL & set(dates)) or "-"), flush=True)
    conn = database.get_conn()
    n = 0
    for d in dates:
        conn.execute(
            "INSERT OR REPLACE INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (d, source, json.dumps(out[d], ensure_ascii=False), int(time.time())))
        n += 1
    conn.commit()
    conn.close()
    print("已落库: %d 天" % n, flush=True)
    return n


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "export"
    if mode == "export":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        out_path = sys.argv[3] if len(sys.argv) > 3 else "sector_history.json"
        out = build_daily_top(days)
        if out is None:
            return 1
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print("已导出: %s (%d 天)" % (out_path, len(out)), flush=True)
        return 0
    elif mode == "import":
        path = sys.argv[2] if len(sys.argv) > 2 else "sector_history.json"
        src = sys.argv[3] if len(sys.argv) > 3 else "em"
        return 0 if do_import(path, source=src) else 1
    else:
        print("用法: export [天数] [out.json] | import [in.json] [source]", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
