# -*- coding: utf-8 -*-
"""
板块轮动历史服务: 每日保存板块强度 Top10, 提供历史轮动查询(表格+趋势线+量能柱)
========================================================================
- 工作日 15:30 后由 scheduler 调 record_today_top(source=...) 落库 daily_sector_top
- 数据源(source): 'kpl' 开盘啦 / 'em' 东方财富 / 'ths' 同花顺(预留)
- query_rotation(days, source) 返回指定数据源最近 N 个交易日数据
"""
import json
import time
import urllib.parse
import urllib.request

from ..core import config, logger
from ..core import net as _net
from ..db import database
from . import kpl

log = logger.get_logger(__name__)

VALID_SOURCES = ("kpl", "em", "ths")

# 2026-08-30 可观测性(主人要求): 记录最近一次数据源失败, 供 API 透传 source_failed
_SRC_ERR = {"source": None, "msg": "", "ts": 0}
import threading
_SRC_LOCK = threading.Lock()


def last_source_error():
    """返回最近一次源失败 {source, msg, ts}; 无失败返回 None"""
    with _SRC_LOCK:
        return dict(_SRC_ERR) if _SRC_ERR["ts"] else None


def _mark_source_error(source, msg):
    with _SRC_LOCK:
        _SRC_ERR["source"] = source
        _SRC_ERR["msg"] = str(msg)[:200]
        _SRC_ERR["ts"] = time.time()


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_em_board_rank():
    """东方财富板块当日榜(概念+行业合并, 按涨跌幅排序前 60 条)
    返回 [{"boardCode": BKxxxx, "name": ..., "strength": 涨跌幅%×100, "change": 涨跌幅%, "amount": 成交额}, ...]"""
    out = []
    try:
        ctx = _ssl_ctx()
        for fs in ("m:90+t:3+f:!50", "m:90+t:2+f:!50"):
            qs = urllib.parse.urlencode({
                "pn": 1, "pz": 200, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": fs,
                "fields": "f12,f14,f3,f6,f62", "ut": config.EASTMONEY_UT,
            })
            req = urllib.request.Request(config.EASTMONEY_URL + "?" + qs, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
            })
            with _net.http_get(req, timeout=8, context=ctx) as r:
                data = json.loads(r.read().decode("utf-8"))
            diff = (data.get("data") or {}).get("diff") or []
            for it in diff:
                code, name = it.get("f12"), it.get("f14")
                if not code or not name:
                    continue
                # 过滤统计型指数(与回补脚本一致)
                if any(kw in name for kw in ("昨日", "新高", "打板", "连板", "涨停", "跌停", "炸板", "首板", "晋级", "破板")):
                    continue
                chg = it.get("f3") or 0
                out.append({
                    "boardCode": code,
                    "name": name,
                    "strength": round(float(chg) * 100, 1),
                    "change": round(float(chg), 2),
                    "amount": float(it.get("f6") or 0),
                    "mainNet": float(it.get("f62") or 0),
                    "volRatio": 0.0,
                    "floatMv": 0.0,
                })
    except Exception as e:
        log.warning("东财板块榜抓取失败 err=%s", e)
        _mark_source_error("em", e)
        return []
    # 概念+行业合并后去重(同名保留涨跌幅大者) + 按涨跌幅降序
    seen = {}
    for b in out:
        prev = seen.get(b["name"])
        if prev is None or b["change"] > prev["change"]:
            seen[b["name"]] = b
    out = sorted(seen.values(), key=lambda x: x["change"], reverse=True)
    return out


def _ths_get(url):
    """同花顺页面抓取(GBK)"""
    import gzip
    ctx = _ssl_ctx()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://q.10jqka.com.cn/",
        "Accept-Encoding": "gzip",
    })
    with _net.http_get(req, timeout=10, context=ctx) as r:
        data = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return data.decode("gbk", "ignore")


def fetch_ths_board_rank():
    """同花顺板块当日榜(行业 + 概念, 按涨跌幅降序, 取前 60 条)
    行业: https://q.10jqka.com.cn/thshy/ (GBK 表格, code/88xxxx)
    概念: https://q.10jqka.com.cn/gn/ 内嵌 gnSection JSON(platecode/platename/199112涨跌幅/zjjlr主力净流入/zfl涨停数)
    返回 [{"boardCode": 88xxxx, "name": ..., "strength": 涨跌幅%×100, "change": 涨跌幅%, "amount": 成交额(元)}, ...]"""
    import json
    import re
    out = []
    seen = set()
    try:
        # 1. 行业板块(表格)
        html = _ths_get("https://q.10jqka.com.cn/thshy/")
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)[1:]
        for tr in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if len(cells) < 5:
                continue
            code_m = re.search(r"code/(88\d{4})/", cells[1])
            name_m = re.search(r">([^<]+)</a>", cells[1])
            if not code_m or not name_m:
                continue
            try:
                chg = float(cells[2].strip())
                amount_yi = float(cells[4].strip())   # 总成交额(亿元)
            except (ValueError, IndexError):
                continue
            name = name_m.group(1).strip()
            seen.add(name)
            out.append({
                "boardCode": code_m.group(1),
                "name": name,
                "strength": round(chg * 100, 1),
                "change": round(chg, 2),
                "amount": round(amount_yi * 1e8, 2),   # 亿 -> 元
                "mainNet": 0.0,
                "volRatio": 0.0,
                "floatMv": 0.0,
            })
        # 2. 概念板块(gnSection JSON)
        html2 = _ths_get("https://q.10jqka.com.cn/gn/")
        m = re.search(r"id=\"gnSection\" value=['\"](\{.*?\})['\"]", html2, re.S)
        if m:
            data = json.loads(m.group(1))
            for it in data.values():
                name = it.get("platename") or ""
                if not name or name in seen:
                    continue
                try:
                    chg = float(it.get("199112", 0) or 0)
                    zjjlr = float(it.get("zjjlr", 0) or 0)   # 主力净流入(亿)
                except (ValueError, TypeError):
                    continue
                seen.add(name)
                out.append({
                    "boardCode": it.get("platecode", ""),
                    "name": name,
                    "strength": round(chg * 100, 1),
                    "change": round(chg, 2),
                    "amount": 0.0,   # 概念页无成交额
                    "mainNet": round(zjjlr * 1e8, 2),
                    "volRatio": 0.0,
                    "floatMv": 0.0,
                })
    except Exception as e:
        log.warning("同花顺板块榜抓取失败 err=%s", e)
        return []
    # 按涨跌幅降序
    out.sort(key=lambda x: x["change"], reverse=True)
    return out[:60]


def _fetch(source, top_n, date=None):
    """按数据源抓取 TopN 列表
    date: 指定日期(历史回看/补跑用); None=今天
    kpl 源: 今天/不传 → 实时接口(15:30后实时=收盘数据, 避开apphis当日1020)
           历史日期 → 历史接口fetch_board_rank_by_date(保留期≈最近3个交易日)"""
    import time as _t
    if source == "kpl":
        today = _t.strftime("%Y-%m-%d")
        if not date or date == today:
            return (kpl.fetch_board_rank() or [])[:top_n]
        return (kpl.fetch_board_rank_by_date(date) or [])[:top_n]
    if source == "em":
        return fetch_em_board_rank()[:top_n]
    if source == "ths":
        return fetch_ths_board_rank()[:top_n]
    log.warning("未知数据源 source=%s", source)
    return []


def record_today_top(top_n=10, date=None, source="kpl"):
    """抓取当日板块强度 TopN 落库 daily_sector_top(覆盖式)
    date: 落库日期(默认北京时间今天)
    source: 数据源 kpl/em/ths(预留)
    返回入库条数; 抓取失败返回 0"""
    if source not in VALID_SOURCES:
        log.warning("不支持的数据源 source=%s", source)
        return 0
    date = date or _bj_date()
    boards = _fetch(source, top_n, date=date)
    if not boards:
        log.warning("板块轮动抓取为空 source=%s date=%s", source, date)
        return 0
    payload = []
    for i, b in enumerate(boards, start=1):
        payload.append({
            "rank": i,
            "boardCode": b.get("boardCode") or "",
            "name": b.get("name") or "",
            "strength": float(b.get("strength") or 0),
            "change": float(b.get("change") or 0),
            "amount": float(b.get("amount") or 0),
            "mainNet": float(b.get("mainNet") or 0),
            "volRatio": float(b.get("volRatio") or 0),
            "floatMv": float(b.get("floatMv") or 0),
        })
    try:
        conn = database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (date, source, json.dumps(payload, ensure_ascii=False), int(time.time())))
        conn.commit()
        conn.close()
        log.info("板块轮动已存 source=%s date=%s 数量%d", source, date, len(payload))
        return len(payload)
    except Exception as e:
        log.error("板块轮动落库失败 err=%s", e)
        return 0


def query_rotation(days=10, source="kpl"):
    """返回指定数据源最近 N 个交易日(按日期降序)的板块 Top10 数据
    返回 {dates:[...], days: [{date, boards:[...]}], source}"""
    if source not in VALID_SOURCES:
        return {"dates": [], "days": [], "source": source}
    days = max(1, min(int(days or 10), 60))
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date, boards FROM daily_sector_top WHERE source=? ORDER BY date DESC LIMIT ?",
        (source, days)).fetchall()
    conn.close()
    out = []
    for d, raw in rows:
        try:
            boards = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            boards = []
        out.append({"date": d, "boards": boards})
    dates = [x["date"] for x in reversed(out)]
    return {"dates": dates, "days": out, "source": source}


def query_window_ranking(days_list=(10, 20, 30, 50), top_k=5, source="kpl"):
    """返回指定数据源多窗口排名"""
    if source not in VALID_SOURCES:
        return {"windows": [], "common_names": [], "source": source}
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date, boards FROM daily_sector_top WHERE source=? ORDER BY date DESC LIMIT ?",
        (source, max(days_list))).fetchall()
    conn.close()
    result = {"windows": [], "common_names": [], "source": source}
    name_pool = set()
    for win in days_list:
        win_rows = rows[:win]
        if not win_rows:
            result["windows"].append({"window": win, "top": []})
            continue
        agg = {}
        for _date, raw in win_rows:
            try:
                boards = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                continue
            for b in boards:
                name = b.get("name") or ""
                if not name:
                    continue
                rank = b.get("rank", 99)
                if rank > top_k:
                    continue
                # 排名权重(Top1=5, Top5=1, 排名越前权重越高)
                weight = top_k + 1 - rank
                strength = float(b.get("strength") or 0)
                if name not in agg:
                    agg[name] = {"sum": 0.0, "count": 0, "weighted_sum": 0.0}
                agg[name]["sum"] += strength * weight            # 累计强度
                agg[name]["count"] += 1                          # 进入 TopK 的天数
                agg[name]["weighted_sum"] += strength * weight   # 同 sum(用于平均)
        top = []
        for name, v in agg.items():
            weighted = v["weighted_sum"]
            avg = weighted / v["count"] if v["count"] else 0
            top.append({
                "name": name,
                "avgStrength": round(avg, 2),                       # 排名加权强度均值
                "strengthSum": round(v["sum"], 2),                  # 累计强度(用于多窗口排名折线, 明显区分窗口)
                "daysInTop": v["count"],
            })
            name_pool.add(name)
        top.sort(key=lambda x: x["strengthSum"], reverse=True)
        top = top[:top_k]
        result["windows"].append({"window": win, "top": top})
    result["common_names"] = sorted(name_pool)
    return result