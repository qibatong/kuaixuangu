# -*- coding: utf-8 -*-
"""
人气热榜多数据源服务
====================
- 'kpl' 开盘啦: GetHotPHB (w29) — 见 kpl.fetch_hot_rank
- 'em'  东方财富: emappdata 人气榜(股票关注度) + push2dycalc ulist 拼行情
- 'ths' 同花顺: dq.10jqka.com.cn 热榜(大家都在看)
统一返回 [{code, name, change, rank}, ...]
"""
import json
import time
import urllib.parse
import urllib.request

from ..core import config, logger
from . import kpl

log = logger.get_logger(__name__)

VALID_SOURCES = ("kpl", "em", "ths")


def _ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get_json(url, data=None, headers=None, timeout=10):
    """GET/POST 拉 JSON"""
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Encoding": "gzip"}
    if headers:
        hdr.update(headers)
    body = data.encode("utf-8") if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, headers=hdr)
    import gzip
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "ignore"))


def fetch_ths_hot_rank(top_n=50):
    """同花顺热榜(大家都在看-小时榜): [{code,name,change,rank,rate}]"""
    try:
        d = _get_json(
            "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?"
            + urllib.parse.urlencode({"stock_type": "a", "type": "hour", "list_type": "normal"}),
            headers={"Referer": "https://eq.10jqka.com.cn/"})
    except Exception as e:
        log.warning("同花顺热榜抓取失败 err=%s", e)
        return []
    lst = (d.get("data") or {}).get("stock_list") or []
    out = []
    for i, it in enumerate(lst, start=1):
        if i > top_n:
            break
        try:
            out.append({
                "code": str(it.get("code") or ""),
                "name": str(it.get("name") or ""),
                "change": round(float(it.get("rise_and_fall") or 0), 2),
                "rank": i,
                "rate": it.get("rate") or "",
            })
        except (ValueError, TypeError):
            continue
    return out


def fetch_em_hot_rank(top_n=50):
    """东方财富人气榜: emappdata 关注度排名 + ulist 批量行情拼名称/涨跌幅"""
    try:
        d = _get_json(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
            data=json.dumps({
                "appId": "appId01",
                "globalId": "786e4c21-70dc-435a-93bb-38",
                "marketType": "",
                "pageNo": 1,
                "pageSize": top_n,
            }),
            headers={"Content-Type": "application/json"},
            timeout=10)
    except Exception as e:
        log.warning("东财人气榜抓取失败 err=%s", e)
        return []
    rows = d.get("data") or []
    if not rows:
        return []
    # 拼行情: secids = 1.600487,0.300017...
    secids = []
    for it in rows:
        sc = str(it.get("sc") or "")
        if sc.startswith("SH"):
            secids.append("1." + sc[2:])
        elif sc.startswith("SZ"):
            secids.append("0." + sc[2:])
        elif sc.startswith("BJ"):
            secids.append("0." + sc[2:])
    quotes = _fetch_em_quotes(secids)
    out = []
    for i, it in enumerate(rows, start=1):
        sc = str(it.get("sc") or "")
        code = sc[2:] if len(sc) > 2 else sc
        q = quotes.get(code)
        out.append({
            "code": code,
            "name": (q or {}).get("name") or "",
            "change": (q or {}).get("change") or 0,
            "rank": int(it.get("rk") or i),
            "hisRankChange": int(it.get("rc") or 0),
        })
    return out


def _fetch_em_quotes(secids):
    """东财批量行情: secids -> {code: {name, change}}"""
    if not secids:
        return {}
    qs = urllib.parse.urlencode({
        "fltt": 2, "invt": 2, "fields": "f12,f14,f3",
        "secids": ",".join(secids), "ut": config.EASTMONEY_UT,
    })
    # push2dycalc(与 fetch_em_board_rank 同域名, 测试机可用)
    hosts = [
        "https://push2dycalc.eastmoney.com/api/qt/ulist.np/get",
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
    ]
    for host in hosts:
        try:
            d = _get_json(host + "?" + qs, headers={"Referer": "https://quote.eastmoney.com/"})
            diff = (d.get("data") or {}).get("diff") or []
            return {str(x.get("f12") or ""): {
                "name": str(x.get("f14") or ""),
                "change": round(float(x.get("f3") or 0), 2),
            } for x in diff}
        except Exception as e:
            log.warning("东财批量行情失败 host=%s err=%s", host, e)
            continue
    return {}


def fetch_hot_rank(source="kpl", top_n=50):
    """按数据源抓人气榜, 统一返回 [{code,name,change,rank}, ...]"""
    source = (source or "kpl").lower()
    if source == "kpl":
        return (kpl.fetch_hot_rank() or [])[:top_n]
    if source == "em":
        return fetch_em_hot_rank(top_n)
    if source == "ths":
        return fetch_ths_hot_rank(top_n)
    log.warning("未知人气榜数据源 source=%s", source)
    return []