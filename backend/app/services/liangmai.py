# -*- coding: utf-8 -*-
"""
量脉金融数据平台接入层 (liangmai.pro, 2026-08-31 主人选型)
==================================================================
定位: 第 N 数据源, 只做三件事(强依赖开盘啦的净额/封单/概念不碰):
  1. 全市场行情兜底: market_snapshot_all (5901 只, 现价/涨幅/换手/量比/市值) — 东财被墙/腾讯也失败时用
  2. 昨比兜底: kline_vip_history (日K含成交额 a, 2001 至今) — 东财/同花顺/腾讯后第4源
  3. 抢筹/涨停池独立校验: auction_morning_grab_amount / stockpool_limit_up / stockpool_broken_board

统一网关: POST https://liangmai.pro/api/gateway?token=&api=&业务参数
限流: 120 次/分(688元/年套餐), 5 IP 绑定; 本地 60s 缓存减少调用
配置: 环境变量 LIANGMAI_TOKEN (走 systemd drop-in, 不进 git)
"""
import json
import threading
import time
import urllib.parse
import urllib.request
import ssl

from ..core import config, logger

log = logger.get_logger(__name__)

GATEWAY = "https://liangmai.pro/api/gateway"
_TIMEOUT = 20
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 简单缓存: key -> (ts, data); TTL 60s (限流友好)
_CACHE = {}
_CACHE_TTL = 60
_LOCK = threading.Lock()

# 全市场代码->名称 缓存(基础列表接口拉一次, 1 天 TTL)
_NAME_MAP = {}
_NAME_MAP_TS = 0
_NAME_MAP_TTL = 86400


def _token():
    return config.LIANGMAI_TOKEN or ""


def _cached(key, ttl=_CACHE_TTL):
    with _LOCK:
        ent = _CACHE.get(key)
        if ent and time.time() - ent[0] < ttl:
            return ent[1]
    return None


def _store(key, data):
    with _LOCK:
        _CACHE[key] = (time.time(), data)


def call(api, timeout=_TIMEOUT, **params):
    """调用量脉网关统一入口; 返回 data 部分(业务数据); 失败抛异常
    token 未配置时抛 RuntimeError(调用方走下一兜底)"""
    tok = _token()
    if not tok:
        raise RuntimeError("量脉未配置 LIANGMAI_TOKEN")
    qs = urllib.parse.urlencode({"token": tok, "api": api, **params})
    url = GATEWAY + "?" + qs
    req = urllib.request.Request(url, method="POST", headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError("量脉 %s 调用失败: %s" % (api, str(e)[:120]))
    if body.get("code") != 0:
        raise RuntimeError("量脉 %s 返回异常: %s" % (api, str(body.get("msg"))[:120]))
    log.info("量脉 %s 耗时%.0fms", api, (time.time() - t0) * 1000)
    return body.get("data")


def _name_map():
    """全市场代码->名称(缓存 1 天); 供 market_snapshot_all 补 f14 名称
    basic_stock_list 分页拉全市场(每页?), 返回 {code: name}"""
    global _NAME_MAP, _NAME_MAP_TS
    now = time.time()
    if _NAME_MAP and now - _NAME_MAP_TS < _NAME_MAP_TTL:
        return _NAME_MAP
    try:
        # 先试单次全量; 若接口分页则循环
        d = call("basic_stock_list", timeout=40)
        m = {}
        if isinstance(d, list):
            for s in d:
                dm = s.get("dm") or s.get("code")
                nm = s.get("mc") or s.get("name")
                if dm and nm:
                    m[str(dm)] = nm
        elif isinstance(d, dict):
            arr = d.get("data") or d.get("list") or []
            for s in arr:
                dm = s.get("dm") or s.get("code")
                nm = s.get("mc") or s.get("name")
                if dm and nm:
                    m[str(dm)] = nm
        if m:
            _NAME_MAP = m
            _NAME_MAP_TS = now
            log.info("量脉股票名称表加载 %d 只", len(m))
        return _NAME_MAP
    except Exception as e:
        log.warning("量脉名称表加载失败(用缓存/空): %s", str(e)[:100])
        return _NAME_MAP


def fetch_market_all():
    """全市场行情(量脉 market_snapshot_all) → 东财 diff 格式列表
    映射: dm→f12, p→f2, zf→f3, ud→f4, hs→f8, lb→f10, cje→f616(近似),
          o→f17(今开), yc→f18(昨收), sz→f20(总市值), lt→f21(流通市值),
          f615=zf(竞价涨幅近似), f617=v(量近似), f14=名称(名称表补)
    返回 [] 表示无数据(调用方兜底下一源)"""
    cached = _cached("lm_market_all")
    if cached is not None:
        return cached
    d = call("market_snapshot_all", timeout=40)
    rows = d.get("data") if isinstance(d, dict) else d
    if not isinstance(rows, list) or not rows:
        return []
    names = _name_map()
    out = []
    for s in rows:
        try:
            dm = str(s.get("dm") or "")
            if not dm:
                continue
            out.append({
                "f2": float(s.get("p") or 0),
                "f3": float(s.get("zf") or 0),
                "f4": float(s.get("ud") or 0),
                "f8": float(s.get("hs") or 0),
                "f10": float(s.get("lb") or 0),
                "f12": dm,
                "f14": names.get(dm) or s.get("mc") or "",
                "f17": float(s.get("o") or 0),
                "f18": float(s.get("yc") or 0),
                "f20": float(s.get("sz") or 0),
                "f21": float(s.get("lt") or 0),
                "f615": float(s.get("zf") or 0),        # 竞价涨幅(近似现价涨幅)
                "f616": float(s.get("cje") or 0),        # 竞价金额(近似成交额, 元)
                "f617": float(s.get("v") or 0),          # 竞价量(近似成交量)
                "f630": 0,
            })
        except (TypeError, ValueError):
            continue
    _store("lm_market_all", out)
    return out


def fetch_yesterday_amounts_map(codes):
    """批量拉一批股票的昨两日成交额(万元): {code: [T万元, T-1万元]}
    kline_vip_history 返回 a(成交额元) → 万元; 单只逐查(有缓存限频, 只用于兜底路径)
    单只失败跳过(返回缺少该 code), 不阻塞"""
    today = time.strftime("%Y%m%d", time.gmtime(time.time() + 8 * 3600))
    out = {}
    for code in codes:
        ck = "lm_amt_%s" % code
        cached = _cached(ck, ttl=86400)   # 昨比当日有效, 缓存 1 天
        if cached is not None:
            out[code] = cached
            continue
        try:
            pairs = _fetch_yesterday_amount_one(code, today)
            if pairs:
                _store(ck, pairs)
                out[code] = pairs
        except Exception:
            continue
    return out


def _fetch_yesterday_amount_one(code, today_yyyymmdd):
    """拉单只最近两已收盘交易日成交额 [T万元, T-1万元]; 失败返回 None
    kline_vip_history: full_code 自动补后缀; 跳过今天(未收盘)"""
    full = _full_code(code)
    d = call("kline_vip_history", timeout=15, full_code=full, interval="d", lt="10")
    rows = d.get("data") if isinstance(d, dict) else d
    if not isinstance(rows, list) or not rows:
        return None
    pairs = []
    for row in rows:
        t = str(row.get("t") or "")[:10].replace("-", "")
        if t == today_yyyymmdd:
            continue  # 今天未收盘跳过
        try:
            amt = float(row.get("a") or 0) / 10000.0   # 元 → 万元
        except (TypeError, ValueError):
            continue
        if amt > 0:
            pairs.append((t, amt))
    if len(pairs) >= 2:
        return [pairs[-1][1], pairs[-2][1]]
    return None


def _full_code(code):
    """6位代码 → 量脉 full_code 格式 (600519→600519.SH / 000001→000001.SZ / 8/4开头→BJ?)"""
    code = str(code)
    if code.startswith(("6", "9")):
        return code + ".SH"
    if code.startswith(("4", "8")):
        return code + ".BJ"
    return code + ".SZ"


def fetch_grab_amount(trade_date, period="0", gtype="1"):
    """早盘/尾盘抢筹排序(独立校验源): period 0=早盘 1=尾盘; type 1=委托 2=成交 3=金额 4=涨幅
    返回 list[ {name, code, openAmt, qczf, qccje, qcwtje} ]"""
    d = call("auction_morning_grab_amount", timeout=20,
             tradeDate=trade_date, period=period, type=gtype)
    inner = d.get("data") if isinstance(d, dict) else d
    return inner if isinstance(inner, list) else []


def fetch_limit_up(trade_date):
    """涨停股池(独立校验源): 返回 list[ {dm, mc, zf, lbc(连板), fbt(封板时间), hy(行业), lt} ]"""
    d = call("stockpool_limit_up", timeout=20, trade_date=trade_date)
    inner = d.get("data") if isinstance(d, dict) else d
    return inner if isinstance(inner, list) else []
