# -*- coding: utf-8 -*-
"""异动 / 停牌风险引擎 —— 交易所「收盘价格涨跌幅偏离值累计」口径
================================================================
一句话：给定个股，算出它在交易所口径下距「异常波动 / 严重异常波动 / 停牌核查」
还有多远，以及**明天再涨停会不会触发**。

────────────────────────────────────────────────────────────────
一、计算口径（★ 以交易所原文为准，不采用工单正文的表述）
────────────────────────────────────────────────────────────────
上交所《交易规则》(2026 修订) 5.4.2(一) 原文：

    收盘价格涨跌幅偏离值累计值 = (单只证券期末收盘价 / 期初前收盘价 − 1) × 100%
                                − (对应指数期末收盘点数 / 期初前收盘点数 − 1) × 100%
    如期间内证券发生过除权除息，则对收盘价格做相应调整

⇒ 这是**区间首尾相减**（「区间涨幅差」），**不是**逐日偏离值求和。
  （工单《快选异动停牌风险功能工单》正文写「是每日偏离值求和，不是区间首尾涨跌幅相减」，
   与交易所原文相反；工单自带的 605058 样例数字 3日 +25.86 / 10日 +99.99 恰恰只有
   区间法能逐位复现，逐日累加只得 +24.29 / +73.25。2026-09-27 已请主人拍板取交易所口径。
   取证脚本见上层目录 `_probe/b1_probe*.sh`。）

「期初前收盘价」= 窗口首日**前一交易日**的收盘价。「N 个交易日」按**市场交易日**数
（指数每个市场交易日都有行情 ⇒ 用指数日期序列当交易日历），个股停牌只影响它自己的
行数，不影响窗口边界。

★ 除权除息的落地方式：**用官方「日涨跌幅」连乘**，而不是用收盘价比值。
  理由：本仓日K主源猫爪 `daily` 的 `close` 实测是**不复权**（见 meoz_client 说明），
  直接相除会在除权日产生虚假跳空；官方 `pct_chg` 已含除权调整，连乘即得复权区间涨幅。
  实测（605058, 窗口跨 2026-06-30 除权的 30 日）：
      腾讯前复权（真值）−29.426 | 猫爪涨跌幅连乘 −29.307 | 不复权 close 比 −30.285
  ⇒ 连乘误差 0.12pp，远优于不复权 close 比的 0.86pp；无除权窗口两法完全相等。

────────────────────────────────────────────────────────────────
二、阈值（现行规则；工单表有 4 处不符，此处按原文修正）
────────────────────────────────────────────────────────────────
* 沪深主板  3日 ±20% ；10日 +100%/−50% ；30日 +200%/−70% ；涨跌幅 10%
* 创业板    3日 ±30%（基准 **399102 创业板综指**，非 399006）
* 科创板    3日 ±30%（基准 000688 科创50，上交所 6.12 明文）
* 北交所    3日 ±40% ；基准 899050 北证50 ；涨跌幅 30%
* ST：**2026-07-06 起沪深主板 ST 涨跌幅 5%→10%、异动阈值 ±12%→±20%（与主板一致）**，
  创业板/科创板 ST 仍 20%、北交所 ST 仍 30% ⇒ **ST 不再需要独立阈值行**，只做展示标注。
* 沪深主板对应指数：沪 = 000002 上证A指（★ 不是 000001 上证指数）；深 = 399107 深证A指。
* 未实现（本轮刻意不做，避免半吊子）：5.4.3(一) 的「连续10个交易日内 4 次（科创板/创业板
  3 次）同向异常波动」——它需要「异常波动公告日」这一外部事件流，本仓没有该数据源。

────────────────────────────────────────────────────────────────
三、数据源与「静默」防线
────────────────────────────────────────────────────────────────
* 个股：猫爪 `daily`（批量，前复权口径靠 pct_chg 连乘）。🔴 实测**单次请求总行数硬上限
  6000**（n=100 时 45 行/只；n=200 掉到 30 行/只、n=500 掉到 12 行/只）⇒ 超限**静默截断**。
  本模块的 `_meoz_daily_adaptive()` 会**检测到 6000 就二分缩批重取**，绝不接受被截断的数据。
* 指数：腾讯 `kline/kline`（无复权问题，close 比即区间涨幅）→ **新浪** `getKLineData` 兜底。
  ★ 北证50 腾讯只回 1 根 ⇒ 靠「行数不足即视为失败」触发降级到新浪，而不是拿 1 根硬算。
* 指数序列落库 `index_daily`（日更），个股不落原始日K（只落结果表 `dev_risk_daily`）。
* **一切失败都返回 None 并记日志**：缺失的指数日、行数不足、上游全挂 —— 绝不返回 0 冒充「无偏离」。
  0 与「算不出」在本模块是两种不同的返回值。
"""
import json
import threading
import time
import urllib.parse
import urllib.request

from ..core import logger, trade_calendar
from ..db import database
from . import meoz_client
from .cache_store import store as _cstore

log = logger.get_logger(__name__)

# ---------------- 板块与阈值表 ----------------

BOARD_MAIN_SH = "main_sh"
BOARD_MAIN_SZ = "main_sz"
BOARD_GEM = "gem"          # 创业板 300/301/302
BOARD_STAR = "star"        # 科创板 688/689
BOARD_BSE = "bse"          # 北交所 920xxx / 8xxxxx / 43xxxx

# key -> {name, index(对应指数代码), index_name, dev3, dev10_up, dev10_dn,
#         dev30_up, dev30_dn, limit(涨停幅度%)}
_BOARDS = {
    BOARD_MAIN_SH: {"name": "沪深主板", "index": "000002", "index_name": "上证A指",
                    "dev3": 20.0, "dev10_up": 100.0, "dev10_dn": -50.0,
                    "dev30_up": 200.0, "dev30_dn": -70.0, "limit": 10.0},
    BOARD_MAIN_SZ: {"name": "沪深主板", "index": "399107", "index_name": "深证A指",
                    "dev3": 20.0, "dev10_up": 100.0, "dev10_dn": -50.0,
                    "dev30_up": 200.0, "dev30_dn": -70.0, "limit": 10.0},
    BOARD_GEM: {"name": "创业板", "index": "399102", "index_name": "创业板综指",
                "dev3": 30.0, "dev10_up": 100.0, "dev10_dn": -50.0,
                "dev30_up": 200.0, "dev30_dn": -70.0, "limit": 20.0},
    BOARD_STAR: {"name": "科创板", "index": "000688", "index_name": "科创50",
                 "dev3": 30.0, "dev10_up": 100.0, "dev10_dn": -50.0,
                 "dev30_up": 200.0, "dev30_dn": -70.0, "limit": 20.0},
    BOARD_BSE: {"name": "北交所", "index": "899050", "index_name": "北证50",
                "dev3": 40.0, "dev10_up": 100.0, "dev10_dn": -50.0,
                "dev30_up": 200.0, "dev30_dn": -70.0, "limit": 30.0},
}

# 三个观察窗口（交易所口径固定为 3 / 10 / 30 个交易日）
WINDOWS = (3, 10, 30)

# 「临近」缓冲区（占阈值百分点数）：dev >= thr - WARN_BUFFER 判临近
WARN_BUFFER = 5.0

# 拉取天数：30 日窗口需 31 根（含期初前收盘价），再为「明日触发」的 (n-1) 日窗口留余量
FETCH_DAYS = 45

# 指数落库最少根数：少于该值视为该源不可用（★ 北证50 腾讯只回 1 根，靠这条触发降级）
IDX_MIN_BARS = 40
# 指数落库保留根数（够 30 日窗口 + 余量即可，不必存全年）
IDX_KEEP_BARS = 120

# 猫爪 daily 单请求总行数硬上限（实测值，超限即静默截断）
_MEOZ_ROW_CAP = 6000

# 指数内存缓存（避免每请求读库）
_IDX_CACHE = {}
_IDX_LOCK = threading.Lock()

_IDX_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120 Safari/537.36")


# ---------------- 板块判定 ----------------

def board_of(code):
    """代码 -> 板块 key；未知返回 None（不猜板块，猜错阈值全错）。"""
    c = str(code or "").strip()
    if len(c) != 6 or not c.isdigit():
        return None
    p3, p2 = c[:3], c[:2]
    if p2 in ("60", "90") or p3 in ("601", "603", "605"):
        return BOARD_MAIN_SH
    if p3 in ("000", "001", "002", "003", "004") or p2 == "00":
        return BOARD_MAIN_SZ
    if p3 in ("300", "301", "302"):
        return BOARD_GEM
    if p3 in ("688", "689"):
        return BOARD_STAR
    if p3 in ("920", "430", "830", "831", "832", "833", "834", "835", "836",
              "837", "838", "839", "870", "871", "872", "873", "920") or p2 == "92":
        return BOARD_BSE
    return None


def spec_of(code):
    """代码 -> 板块规格 dict（含 index / 阈值 / limit）；未知返回 None。"""
    b = board_of(code)
    return dict(_BOARDS[b], key=b) if b else None


def _idx_symbol(index_code):
    """指数代码 -> 行情商用的带市场前缀符号。

    规则：000xxx/000688 等沪市指数 → sh；399xxx → sz；899xxx（北证50）→ bj。
    ★ 与 fetcher._secid 不同：那个按**个股**前缀判，会把 000002 判成深市个股万科A。
    """
    c = str(index_code or "")
    if c.startswith(("399",)):
        return "sz" + c
    if c.startswith(("899", "8", "9")):
        return "bj" + c
    return "sh" + c


# ---------------- 指数日线（腾讯 → 新浪，落库 + 内存缓存） ----------------

def _http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": _IDX_UA,
                                              "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _fetch_idx_tencent(index_code, bars):
    """腾讯日K：row = [date, open, close, high, low, volume]（**无 pct_chg**，指数无需复权）。"""
    sym = _idx_symbol(index_code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param="
           + urllib.parse.quote(sym) + ",day,,," + str(int(bars)))
    j = _http_json(url)
    d = (j.get("data") or {}).get(sym) or {}
    rows = d.get("day") or []
    out = []
    for r in rows:
        try:
            out.append((str(r[0]), float(r[2])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _fetch_idx_sina(index_code, bars):
    """新浪日K（北证50 的可用源）：[{day, open, high, low, close, volume}, ...]。"""
    sym = _idx_symbol(index_code)
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d"
           % (sym, int(bars)))
    j = _http_json(url)
    if not isinstance(j, list):
        return []
    out = []
    for r in j:
        try:
            out.append((str(r["day"]), float(r["close"])))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _expected_last_trade_date():
    """应当已收盘的最近一个交易日（北京时区）。

    15:05 前当天指数尚未定格 ⇒ 期望上一个交易日；15:05 后（且当天是交易日）期望当天。
    """
    now = time.time()
    today = trade_calendar.bj_date(now)
    hhmm = time.strftime("%H:%M", time.gmtime(now + 8 * 3600))
    if trade_calendar.is_trade_day(today) and hhmm >= "15:05":
        return today
    return trade_calendar.prev_trade_date(today)


def _idx_load_db(index_code):
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT date, close FROM index_daily WHERE index_code=? ORDER BY date",
            (index_code,)).fetchall()
    finally:
        conn.close()
    return [(r[0], float(r[1])) for r in rows]


def _idx_save_db(index_code, rows):
    conn = database.get_conn()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO index_daily (index_code, date, close, ts) VALUES (?,?,?,?)",
            [(index_code, d, c, int(time.time())) for d, c in rows])
        # 只保留最近 IDX_KEEP_BARS 根，避免无界增长
        conn.execute(
            "DELETE FROM index_daily WHERE index_code=? AND date NOT IN ("
            "SELECT date FROM index_daily WHERE index_code=? ORDER BY date DESC LIMIT ?)",
            (index_code, index_code, IDX_KEEP_BARS))
        conn.commit()
    except Exception as e:                       # noqa: BLE001
        log.warning("[dev_risk] 指数落库失败 index=%s err=%s", index_code, e)
    finally:
        conn.close()


def index_series(index_code, force=False):
    """某指数按日期升序的 [(YYYY-MM-DD, close)]；取不到返回 []（**不猜**）。"""
    want = _expected_last_trade_date()
    with _IDX_LOCK:
        ent = _IDX_CACHE.get(index_code)
        if ent and not force and ent["last"] == want:
            return ent["rows"]
    rows = _idx_load_db(index_code)
    need_fetch = force or not rows or (want and rows[-1][0] < want)
    if need_fetch:
        bars = max(IDX_KEEP_BARS, FETCH_DAYS + 5)
        got = []
        for tag, fn in (("tencent", _fetch_idx_tencent), ("sina", _fetch_idx_sina)):
            try:
                got = fn(index_code, bars)
            except Exception as e:               # noqa: BLE001
                log.warning("[dev_risk] 指数源异常 index=%s src=%s err=%s", index_code, tag, e)
                got = []
            if len(got) >= IDX_MIN_BARS:
                log.info("[dev_risk] 指数取数 index=%s src=%s 根数=%d 末根=%s",
                         index_code, tag, len(got), got[-1][0])
                break
            # ★ 行数不足**不静默接受**：北证50 腾讯只回 1 根，必须走到新浪
            log.warning("[dev_risk] 指数源行数不足 index=%s src=%s 根数=%d < %d → 降级下一源",
                        index_code, tag, len(got), IDX_MIN_BARS)
            got = []
        if got:
            _idx_save_db(index_code, got)
            rows = _idx_load_db(index_code)
        elif not rows:
            log.error("[dev_risk] 指数全源失败 index=%s ⇒ 该板块本日不可算", index_code)
    if rows:
        with _IDX_LOCK:
            _IDX_CACHE[index_code] = {"rows": rows, "last": rows[-1][0]}
    return rows


def clear_cache():
    """清空指数内存缓存（后台改指数数据后强制重取）。"""
    with _IDX_LOCK:
        _IDX_CACHE.clear()


# ---------------- 个股日线（猫爪批量，自适应缩批） ----------------

def _meoz_daily_adaptive(codes, days=FETCH_DAYS):
    """猫爪 `daily` 批量取多日 —— **带 6000 行硬上限的自适应缩批**。

    返回 {code: [ {tradedate, close, pct_chg, name}, ... ]}（**已按日期升序**）。
    ★ 猫爪返回是「最新在前」，本函数统一反转成升序，调用方不必关心。
    ★ 检测到单次总行数触顶 6000 即**丢弃该批**并二分重取 —— 被截断的数据比没有数据更危险。
    """
    out = {}
    todo = [list(codes)]
    guard = 0
    while todo:
        guard += 1
        if guard > 64:
            log.error("[dev_risk] 自适应缩批层数过深(>64) ⇒ 中止，已取 %d 只", len(out))
            break
        chunk = todo.pop()
        if not chunk:
            continue
        try:
            m = meoz_client.daily_history_map(chunk, days=days)
        except Exception as e:                   # noqa: BLE001
            log.warning("[dev_risk] 猫爪 daily 异常 n=%d err=%s", len(chunk), e)
            continue
        nrows = sum(len(v) for v in (m or {}).values())
        if nrows >= _MEOZ_ROW_CAP and len(chunk) > 1:
            mid = len(chunk) // 2
            log.warning("[dev_risk] 猫爪 daily 触顶 %d 行(请求 %d 只) → 二分重取 %d+%d",
                        nrows, len(chunk), mid, len(chunk) - mid)
            todo.append(chunk[mid:])
            todo.append(chunk[:mid])
            continue
        for code, rows in (m or {}).items():
            out[code] = sorted(rows, key=lambda r: str(r.get("tradedate") or ""))
    return out


def stock_series(code, days=FETCH_DAYS):
    """单只个股按日期升序的日行（猫爪）。取不到返回 []。"""
    m = _meoz_daily_adaptive([code], days=days)
    return m.get(code) or []


# ---------------- 核心计算（交易所口径） ----------------

def _to_iso(tradedate):
    s = str(tradedate or "")
    return s[:4] + "-" + s[4:6] + "-" + s[6:8] if len(s) >= 8 else ""


def _range_pct(stock_rows, idx_rows, n):
    """区间首尾相减的两个分量。

    返回 (个股区间涨幅%, 指数区间涨幅%, 窗口首日, 期初前收盘日)；指数日缺失返回 None。

    * 窗口 = 指数日期序列的**最后 n 个交易日**（指数每市场交易日都有行情 ⇒ 用它当交易日历）。
    * 期初前收盘日 = 窗口首日的**前一交易日**。
    * 个股区间涨幅 = 该股在窗口内各日官方涨跌幅连乘（免疫除权；停牌日天然缺席）。
    * 若个股窗口内无任何行情（整段停牌）⇒ 返回 0.0 并在 detail 里标 suspended。
    """
    idates = [d for d, _ in idx_rows]
    if len(idates) < n + 1:
        log.warning("[dev_risk] 指数根数不足 需 %d 得 %d ⇒ 弃权", n + 1, len(idates))
        return None
    ic = dict(idx_rows)
    w = idates[-n:]
    base = idates[-(n + 1)]
    if base not in ic or w[-1] not in ic:
        log.warning("[dev_risk] 指数日缺失 base=%s end=%s ⇒ 弃权", base, w[-1])
        return None
    i_pct = (ic[w[-1]] / ic[base] - 1) * 100 if ic[base] else None
    if i_pct is None:
        return None
    w0, w1 = w[0].replace("-", ""), w[-1].replace("-", "")
    sub = [r for r in stock_rows if w0 <= str(r.get("tradedate") or "") <= w1]
    g = 1.0
    used = 0
    for r in sub:
        try:
            g *= (1 + float(r.get("pct_chg") or 0) / 100.0)
            used += 1
        except (TypeError, ValueError):
            continue
    if not used:
        return (0.0 - i_pct, i_pct, w[0], base)   # 整段停牌：个股区间计 0（交易所同样无价可比）
    return ((g - 1) * 100 - i_pct, i_pct, w[0], base)


def _status(value, threshold):
    """value 相对 threshold 的状态：触发 / 临近 / 安全（正负向都走这里）。"""
    if threshold >= 0:
        if value >= threshold:
            return "触发"
        return "临近" if value >= threshold - WARN_BUFFER else "安全"
    if value <= threshold:
        return "触发"
    return "临近" if value <= threshold + WARN_BUFFER else "安全"


def compute(code, name=None, days=FETCH_DAYS, srows=None, idx_rows=None):
    """单只个股的异动风险全量结果。

    `srows` / `idx_rows` 可由调用方注入（批量场景复用同一份数据，**避免每票一次网络请求**）。
    返回 dict；**算不出时 ok=False 并带 reason**（绝不返回一堆 0 冒充安全）。
    """
    spec = spec_of(code)
    if not spec:
        return {"ok": False, "code": code, "reason": "unknown_board",
                "msg": "无法识别板块（非沪深/创业板/科创板/北交所代码）"}
    if idx_rows is None:
        idx_rows = index_series(spec["index"])
    if len(idx_rows) < WINDOWS[-1] + 1:
        return {"ok": False, "code": code, "reason": "index_unavailable",
                "msg": "对应指数（%s %s）数据不足" % (spec["index"], spec["index_name"]),
                "board": spec["name"], "index": spec["index"]}
    if srows is None:
        srows = stock_series(code, days=days)
    if not srows:
        return {"ok": False, "code": code, "reason": "stock_unavailable",
                "msg": "个股日线取不到（可能长期停牌或代码不存在）",
                "board": spec["name"], "index": spec["index"]}
    nm = name or (srows[-1].get("name") or "")
    date = _to_iso(srows[-1].get("tradedate"))
    try:
        close = float(srows[-1].get("close"))
    except (TypeError, ValueError):
        close = None

    dev = {}
    win_txt = {}
    for n in WINDOWS:
        r = _range_pct(srows, idx_rows, n)
        if r is None:
            return {"ok": False, "code": code, "reason": "window_incomplete",
                    "msg": "%d 日窗口无法计算" % n}
        dev_n, i_pct, w0, base = r
        win_txt[n] = "%s → %s（期初前 %s）" % (w0, date, base)
        dev[n] = {"stock_pct": round(dev_n + i_pct, 2), "idx_pct": round(i_pct, 2),
                  "value": round(dev_n, 2), "window": "%s→%s" % (w0, date),
                  "base_date": base}

    today_dev = _today_dev(srows, idx_rows, date)

    lines = {
        "d3": {"value": dev[3]["value"], "thresh": spec["dev3"], "status": _status(dev[3]["value"], spec["dev3"])},
        "d10": {"value": dev[10]["value"], "thresh": spec["dev10_up"], "status": _status(dev[10]["value"], spec["dev10_up"])},
        "d30": {"value": dev[30]["value"], "thresh": spec["dev30_up"], "status": _status(dev[30]["value"], spec["dev30_up"])},
    }

    room = _next_trigger(spec, srows, idx_rows)
    proj = project_next_10_days(code, spec=spec, srows=srows, idx_rows=idx_rows, close=close)

    res = {
        "ok": True, "code": code, "name": nm, "board": spec["name"],
        "board_key": spec["key"], "index": spec["index"], "index_name": spec["index_name"],
        "price": close, "date": date, "limit_up_pct": spec["limit"],
        "today_dev": today_dev, "dev": lines, "detail": dev, "window": win_txt,
        "room": room, "project10": proj,
    }
    res["warn"] = warn_of(res)
    return res


def _today_dev(srows, idx_rows, date):
    """当日偏离 = 个股当日涨跌幅 − 对应指数当日涨跌幅。"""
    ic = dict(idx_rows)
    cur = None
    prev = None
    ds = [d for d, _ in idx_rows]
    if date in ic:
        cur = date
        i = ds.index(date)
        if i > 0:
            prev = ds[i - 1]
    if cur is None or prev is None:
        log.warning("[dev_risk] 当日指数日缺失 date=%s ⇒ 当日偏离弃权", date)
        return None
    try:
        ip = (ic[cur] / ic[prev] - 1) * 100
        sp = float(srows[-1].get("pct_chg"))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(sp - ip, 2)


def _next_trigger(spec, srows, idx_rows):
    """「明天个股再涨多少 % 会触发下一条（未触发的）上行线」——指数按 0 计。

    推导：明日窗口相对今日**滚动一天**（丢掉最老一天、加入明天），故
      明日 n 日窗口的个股比 = (1 + s(n−1)) × (1 + x)，指数比 = (1 + i(n−1))（未来指数按 0 计）
    令   (1+s(n−1))(1+x) − 1 − i(n−1)  =  thr
    ⇒    x = (1 + (thr + i(n−1))/100) / (1 + s(n−1)/100) − 1

    返回 {next_trigger_pct, trigger_price, rule, reachable(明日涨停能否触发), hit[]}
    """
    if not srows:
        return None
    try:
        close = float(srows[-1].get("close"))
    except (TypeError, ValueError):
        return None
    cands = []
    for n, thr in ((3, spec["dev3"]), (10, spec["dev10_up"]), (30, spec["dev30_up"])):
        cur = _range_pct(srows, idx_rows, n)
        if cur is None:
            continue
        cur_dev = cur[0]
        if _status(cur_dev, thr) == "触发":
            continue                                # 已触发的不算「下一条」
        prev = _range_pct(srows, idx_rows, n - 1)
        if prev is None:
            continue
        s_prev = prev[0] + prev[1]                  # (n−1) 日个股区间涨幅
        i_prev = prev[1]
        denom = 1 + s_prev / 100.0
        if denom <= 0:
            continue
        x = ((1 + (thr + i_prev) / 100.0) / denom - 1) * 100
        rule = {3: "3日±%g%%" % spec["dev3"],
                10: "10日+%g%%" % spec["dev10_up"],
                30: "30日+%g%%" % spec["dev30_up"]}[n]
        cands.append({"n": n, "pct": round(x, 2), "rule": rule})
    if not cands:
        return {"next_trigger_pct": None, "trigger_price": None, "rule": "无（全部窗口已触发）",
                "reachable": False, "hit": []}
    cands.sort(key=lambda c: c["pct"])
    best = cands[0]
    return {
        "next_trigger_pct": best["pct"],
        "trigger_price": round(close * (1 + best["pct"] / 100.0), 2) if close else None,
        "rule": best["rule"],
        "reachable": bool(best["pct"] <= spec["limit"] + 1e-9),
        "limit_up_pct": spec["limit"],
        "hit": [c["rule"] for c in cands if c["pct"] <= spec["limit"] + 1e-9],
        "all": cands,
    }


def _next_trade_days(last_date, k):
    """last_date 之后的 k 个交易日（YYYY-MM-DD 升序）；日历算不出返回 []。"""
    import datetime as _dt
    try:
        cur = _dt.date.fromisoformat(last_date)
    except (TypeError, ValueError):
        return []
    out = []
    for _ in range(max(1, k * 3)):
        cur += _dt.timedelta(days=1)
        s = cur.isoformat()
        if trade_calendar.is_trade_day(s):
            out.append(s)
            if len(out) >= k:
                break
    return out

def _real_axis(srows, idates, n):
    """把 srows 的真实日涨幅连乘成「个股/期初前收盘」轴，下标 = 相对今日的偏移 d。

    ★ 下标约定（全模块统一）：**今日 = 0**，历史日 = 负偏移，未来日 = 正偏移。
      `out[d]` = 偏移 d 当日收盘价 / **偏移 −n 当日**（= 该 n 日窗口的**期初前收盘日**）的收盘价。
      ⇒ 真实 n 日窗口的个股比就是 `out[0]`（期初前收盘在 −n，即窗口首日 (1−n) 的前一日）。

    返回 (out, im)：`im` = 下标 0 那一天相对「前一日」的比（= 1 + pct_chg[0]/100），
    用于把任意 d 的比换算成「相对前一日」的比（未来段外推时要用）。

    取不到足够历史时返回 ({}, 0.0) ⇒ 调用方整体弃权（绝不返回半截数据）。
    """
    # ★ 索引 key 统一成 ISO（与 idates 一致）—— srows 的 tradedate 是 `YYYYMMDD`，
    #   而 idates 是 `YYYY-MM-DD`，两边直接对键会**恒不命中**（首次实测即踩）。
    rows = {}
    for r in srows or []:
        d = str(r.get("tradedate") or "").strip()
        if not d:
            continue
        if len(d) == 8 and d.isdigit():
            d = d[:4] + "-" + d[4:6] + "-" + d[6:8]
        try:
            rows[d] = float(r.get("pct_chg") or 0) / 100.0
        except (TypeError, ValueError):
            continue
    if not rows:
        return {}, 0.0
    # 只保留指数交易日历覆盖到的历史日（指数日历是真日历；个股停牌日天然缺席）
    hist = [d for d in idates if d in rows]
    if len(hist) < n + 1:
        log.warning("[dev_risk] 个股日涨幅不足 n 需 %d 得 %d ⇒ 投影弃权", n + 1, len(hist))
        return {}, 0.0
    # 期初前收盘日 = 窗口首日的前一日 = 下标 −n；窗口首日 = 下标 (1−n)
    # 轴先归一到「窗口首日收盘」= 1.0，最后统一换算成「相对期初前收盘」。
    last = len(idates) - 1
    w_start = last - (n - 1)                 # 下标 (1−n) 对应的 index
    if w_start < 0 or idates[last] not in rows:
        return {}, 0.0
    im0 = 1.0 + rows[idates[last]]            # 今日相对前一日的比（供未来段外推用）
    out = {0: 1.0}
    v = 1.0
    for off in range(1, n + 1):              # 向过去：下标 −1 … −n（连乘真实日涨幅的倒数）
        # ★ off=1 要退掉的是**今日**的涨幅（today = 1/(1+r_today)）⇒ 索引是 last 本身。
        #   2026-09-28 修正 off-by-one：原写 `i = last - off` 会从**昨日**涨幅起退，
        #   整个窗口左移一天（多退一个丢失的近端日、少退一个远端日），
        #   使 out[0] 算成「倒数第 n+1 日→倒数第 1 日」的涨幅，偏离真值。
        #   该 bug 长期未被发现，只因既有用例全用「每日恒定涨幅」，
        #   窗口整体平移一天在恒定序列上**完全不可见**（见 test_real_axis_matches_close_ratio）。
        i = last - off + 1
        if i < 0 or idates[i] not in rows:
            break
        v = v / (1.0 + rows[idates[i]])
        out[-off] = v
    if -n not in out:
        log.warning("[dev_risk] 个股历史不足到 −%d ⇒ 投影弃权", n)
        return {}, 0.0
    # 换算到「相对期初前收盘（下标 −n）」：out[d] ← out[d] / out[−n]
    # ⇒ out[−n] == 1.0，而真实 n 日窗口的个股比 = out[0] = 1/∏(1+r_{−n+1..0})
    base = out[-n]
    if not base:
        return {}, 0.0
    out = {k: v2 / base for k, v2 in out.items()}
    return out, im0


# 求根区间（**日涨幅**，非百分比）。上界 3.0（+300%）故意宽于任何涨停板：
# 除权/极端组合下窗口内可能出现远超单日涨停的累计，区间不足会把「不可达」误当「不触发」。
_GAIN_LO, _GAIN_HI = -0.99, 3.0
_GAIN_ITER = 80                      # 区间宽 3.99，2^80 远超 double 有效位


def project_next_10_days(code, spec=None, srows=None, idx_rows=None, close=None):
    """未来十日投影 —— **以今日真实行情为起点逐个交易日倒推**（不再假设天天涨停）。

    ★ 2026-09-28 口径改写（主人：「异动计算器下面未来10日的都是虚值，按照实际的计算」）：
      旧实现假设「个股**每日 +涨停**、指数持平」逐日推演 ⇒ 第 2 天起就走完 3 日线、
      长窗口也迅速越线，10 行**全部**「已触发」——
      那是**情景假设**，用户会读成「预测未来 10 天会触发」（主人截图即此误读）。
      现改为**真实起点 + 反解**，每一行回答一个**具体且可验证**的问题：

        「若从今日收盘起，每个交易日都涨 g，第 k 日收盘时这条线会不会越线？
          最小需要的 g 是多少？」

      该问题**不需要任何预测**（未来行情无法预知），它是**给定均匀涨幅假设下的反解**：
        · g ≤ 一个涨停 ⇒ 该日**有真实可能**触发（列「涨停」显示「会触发」）；
        · g > 一个涨停 ⇒ 该日**单日不可能**触发。
      随 k 增大，窗口越长、越难越线 ⇒ g 随 k **单调不减**，全表不会一律「已触发」。

    ★ 关键实现：轴由 **srows 的真实日涨幅连乘**得出（与 `_range_pct` 逐位同源，
      故对除权天然免疫），未来段才续上 `(1+g)`。指数历史段用**真实**收盘序列
      （不假设持平），仅未来段按持平外推（唯一无信息时的中性假设）。
      触板截断：未来日按 `min(g, 涨停幅度)` 复利 —— **不再出现凭空的连板价**。

    ★ 退化自证：k=1 时，窗口 [1−n … 1] 的期初与今日真实窗口 [1−n … 0] 完全相同，
      仅末点由「今日收盘」换成「第 1 日收盘」⇒ g(k=1) 与 `_next_trigger()` 的单日
      闭式解**数值全等**（回归用例 `test_project_day1_matches_next_trigger` 钉死）。

    ⚠️ 兼容：字段名与旧版一致（前端 `DevRiskDetail.vue` 逐字消费），**语义按下表重定义**：
        · `safe_gain_pct`  自今日收盘到该日为止的**累计安全涨幅上限**（= 首次触发所需累计涨幅%）
                           ★ 用**复利**（(1+g)^k−1）而非简单累加；`None` = 该日不触发
        · `price`          该日触发所需达到的**目标价**（None = 不触发）
        · `trigger_rule`   该日首次触发的线（**不含 3 日线**，主人 2026-09-27 要求）
        · `zt_trigger`     该日所需涨幅是否 ≤ 一个涨停（即「一个涨停内即触发」）
        · `left10/left30`  距该线触发的**剩余交易日**（0 = 该日即触发；None = 10 日内不触发）
        · `dev10/dev30`    该日收盘时的 10/30 日偏离值（按「该日所需涨幅」情景估值）
        · `need10/need30`  该日触发 10/30 日线的最小所需**累计**涨幅%（None = 10 日内不可能）
    """
    spec = spec or spec_of(code)
    if not spec:
        return []
    srows = srows if srows is not None else stock_series(code)
    idx_rows = idx_rows if idx_rows is not None else index_series(spec["index"])
    if not srows or not idx_rows:
        return []
    idates = [d for d, _ in idx_rows]
    ic = dict(idx_rows)
    last = idates[-1]
    if close is None:
        try:
            close = float(srows[-1].get("close"))
        except (TypeError, ValueError):
            return []
    if not close:
        return []
    fut_days = _next_trade_days(last, 10)
    if not fut_days:
        log.warning("[dev_risk] 未来交易日算不出（日历缺口?） code=%s", code)
        return []

    # 三条线（窗口 n, 阈值, 标签）。投影表的触发判定**剔除 3 日线**（v4.11.69 主人要求）。
    _rules = ((3, spec["dev3"], "3日±%g%%" % spec["dev3"]),
              (10, spec["dev10_up"], "10日+%g%%" % spec["dev10_up"]),
              (30, spec["dev30_up"], "30日+%g%%" % spec["dev30_up"]))
    _hit_rules = tuple(r for r in _rules if r[0] != 3)

    # ---- 真实个股轴（各线所需历史长度不同 ⇒ 逐线造；归一化到「期初前收盘」----
    axes = {}
    for n, _thr, _lab in _rules:
        ax, _im = _real_axis(srows, idates, n)
        if not ax:
            log.warning("[dev_risk] 真实个股轴造不出 n=%d code=%s ⇒ 整体弃权", n, code)
            return []
        axes[n] = ax

    # 指数轴：历史用真实收盘，未来按持平（= 今日值）。比值按下标对齐（轴不归一，
    # 因为 `_dev_n_at` 只用到**比值** i1/i0，绝对值无所谓）。
    idx_now = ic.get(last)
    if not idx_now:
        return []
    last_i = len(idates) - 1
    idx_ax = {}
    for off in range(-(WINDOWS[-1] + 2), 1):
        j = last_i + off
        if 0 <= j < len(idates) and ic.get(idates[j]):
            idx_ax[off] = ic[idates[j]]
    for kk in range(1, len(fut_days) + 1):
        idx_ax[kk] = idx_now

    cap = spec["limit"] / 100.0

    def _axis_g(ax, g, k):
        """把真实轴续上未来段：`ax_g[k] = ax[0] × (1 + min(g, cap))^k`。

        ★ 未来第 d 日个股涨幅 = min(g, 涨停幅度/100) —— 真实市场不可能连续涨停超过 cap。
        ★ g < 0（下跌情景）**不截断**：本模型只模拟「涨到触发」，负 g 仅在「远未触发」
          时用于求根，且区间下界 −99% 已足够宽。
        """
        step = 1.0 + (min(g, cap) if g > 0 else g)
        if step <= 0:
            return None
        return ax[0] * (step ** k)

    def _cum_of(g, k):
        """该日所需的**累计**涨幅（比）：(1 + min(g, 涨停))^k − 1。"""
        step = 1.0 + (min(g, cap) if g > 0 else g)
        return step ** k - 1 if step > 0 else None

    def _dev_n_at(n, k, g):
        """n 日线在第 k 天收盘（未来每日 +g）时的偏离值%。"""
        ax = axes[n]
        d_start = k - n
        a_start = ax.get(d_start)
        if a_start is None:
            # 期初落在未来区（k > n 时 d_start ≥ 1）：用 g 外推补上
            if d_start >= 1:
                a_start = _axis_g(ax, g, d_start)
            else:
                return None
        a_end = _axis_g(ax, g, k) if k >= 1 else ax.get(k)
        if a_start is None or a_end is None or not a_start:
            return None
        i0, i1 = idx_ax.get(d_start), idx_ax.get(k)
        if not i0 or not i1:
            return None
        return (a_end / a_start) / (i1 / i0) * 100 - 100

    def _solve(n):
        """逐日解出「第 k 天首次越线所需的最小日涨幅 g」；不可达/已越线分别返回 None/0.0。"""
        thr = dict((r[0], r[1]) for r in _rules)[n]
        out = {}
        for k in range(1, len(fut_days) + 1):
            v0 = _dev_n_at(n, k, 0.0)              # g=0（未来持平）时的偏离值
            if v0 is None:
                out[k] = None
                continue
            if v0 >= thr:                          # 现在就已越线 ⇒ 该日「无需再涨」
                out[k] = 0.0
                continue
            v_hi = _dev_n_at(n, k, _GAIN_HI)
            if v_hi is None or v_hi < thr:         # 区间上界仍不够 ⇒ 10 日内不可能
                out[k] = None
                continue
            lo, hi = _GAIN_LO, _GAIN_HI
            for _ in range(_GAIN_ITER):            # 偏离值对 g 单调不减 ⇒ 二分收敛
                mid = (lo + hi) / 2.0
                vm = _dev_n_at(n, k, mid)
                if vm is not None and vm >= thr:
                    hi = mid
                else:
                    lo = mid
            out[k] = hi
        return out

    need = {n: _solve(n) for n, _t, _l in _rules}

    # ---- 逐日组装 ----
    # ★ `leftN` 的「首次可达日」= 第一个满足 **g ≤ 一个涨停**（即靠连板真能做到）的 k。
    #   🔴 判据必须是 `g <= cap`，**不能**用「`_solve` 返回了非 None」——后者只说明 g 落在
    #      求根区间 [−99%, +300%] 内，远松于涨停 ⇒ 会把「56 天才能到、10 天内根本不可能」
    #      误报成「剩 6 日」（本实现首版即踩，被 left30 独立复算抓出）。
    first_hit = {}
    for n in (10, 30):
        off = None
        for kk in range(1, len(fut_days) + 1):
            g = need[n].get(kk)
            if g is not None and g <= cap + 1e-9:
                off = kk
                break
        first_hit[n] = off

    rows = []
    for k, d in enumerate(fut_days, 1):
        rec = {"day": k, "date": d, "limit_up_pct": spec["limit"]}
        # needN：该日触发该线所需的**累计**涨幅%（复利，自今日收盘起）；None = 求根区间外
        for n, _thr, _lab in _rules:
            g = need[n].get(k)
            rec["need%d" % n] = (None if g is None else round(_cum_of(g, k) * 100, 2))
        # 该日「触发的线」= 所需日涨幅 g **最小**、且 **g ≤ 一个涨停** 的那条（不含 3 日线）
        # 🔴 判据统一为 `g <= cap`（「靠连板真能做到」），**不要**改用累计涨幅互比 ——
        #    两者在 k>1 时会背离（累计口径会把「56 天才到」的线也算进来），首版即因此误报。
        best = None
        hit = []
        for n, _thr, label in _hit_rules:
            g = need[n].get(k)
            if g is None:
                continue
            if best is None or g < best[0]:
                best = (g, label)
            if g <= cap + 1e-9:
                hit.append((g, label))
        hit.sort()
        rec["trigger"] = " / ".join(lb for _g, lb in hit) if hit else "不触发"
        rec["trigger_rule"] = hit[0][1] if hit else "无"
        rec["zt_trigger"] = bool(hit)
        # safe_gain_pct = 该日触发线的**累计安全涨幅上限**（= 最小所需日涨幅复利到该日）
        # ★ 语义：从今日收盘起，涨到**这个幅度**就会首次触发该线 ⇒ 之前的涨幅都「安全」
        # ★ 只在「一个涨停连板可做到」时给出；否则 None（该日不触发）
        rec["safe_gain_pct"] = (None if best is None or best[0] > cap + 1e-9
                                else round(_cum_of(best[0], k) * 100, 2))
        # price = 该日触发所需的目标价（真实倒推价；不触发为 None）
        rec["price"] = (round(close * (1 + _cum_of(best[0], k)), 2)
                        if best is not None and best[0] <= cap + 1e-9 else None)
        # devN = 该日收盘时的 10/30 日偏离值（按该日所需涨幅情景；不触发时按 k 个涨停情景估趋势）
        for n in (10, 30):
            g = need[n].get(k)
            gg = cap if (g is None or g > cap) else g
            v = _dev_n_at(n, k, gg)
            rec["dev%d" % n] = None if v is None else round(v, 2)
        # leftN = 距该线触发的剩余交易日（0 = 该日即触发；None = 10 日内不触发）
        for n in (10, 30):
            off = first_hit[n]
            rec["left%d" % n] = None if off is None else max(0, off - k)
        rows.append(rec)
    return rows


def warn_of(res):
    """把结果映射成选股名单用的风险标签（工单 §六）。

    ★ 分级必须**互斥且两级都可达**：
        red    = 今天**已经**越线（任一条线 status == 触发）⇒ 风险已落地
        yellow = 尚未越线，但**明日涨停（或所需涨幅 ≤ 涨停）即首次触发**，
                 或已进入「临近」带 ⇒ 明日需盯盘
        None   = 安全

    ★★ 首版曾把「明日涨停即触发」也判 red ⇒ **整个临近带被 red 吞掉、yellow 变成
    不可达分支**（`test_warn_levels` 抓到）。原因是数学上必然：
      「临近」= 距阈值 5pp 内；而明日只需补上这 ≤5pp / 剩余天数 的涨幅即可越线
      （3 日线约 4~7%、10/30 日线约 0.5~2%），几乎必然 ≤ 一个涨停(10%/20%/30%)
      ⇒ `room.hit` 与「临近」高度重叠。唯一例外是**近 2 日涨幅很小而窗口起点大涨**时，
      3 日线所需涨幅可能超过涨停（如 3 日窗口 = [+10%, +2.76%, +2.76%] ⇒ d3 = 16.16%
      已临近，但明日需 +13.63% > 涨停 10%），那才是真正只有 yellow 的情形。
    故现按「**已越线 / 将越线**」这条轴分级 —— 与工单「明日涨停即触发」的语义词一致，
    且不再有互斥冲突。
    """
    if not res or not res.get("ok"):
        return None
    room = res.get("room") or {}
    dev = res.get("dev") or {}
    names = {"d3": "3日", "d10": "10日", "d30": "30日"}
    order = ("d3", "d10", "d30")
    triggered = [k for k in order if (dev.get(k) or {}).get("status") == "触发"]
    near = [k for k in order if (dev.get(k) or {}).get("status") == "临近"]
    if triggered:
        return {"level": "red",
                "msg": "已触发%s偏离值异动线，注意异常波动核查风险" % "、".join(names[k] for k in triggered)}
    if room.get("hit"):
        pct = room.get("next_trigger_pct")
        lim = res.get("limit_up_pct") or 10.0
        if pct is None:
            act = "明日再上涨"
        elif pct >= lim - 0.005:
            act = "明日涨停"
        else:
            act = "明日涨 %.2f%%" % pct
        msg = "%s即触发%s（异常波动核查）" % (act, "、".join(room["hit"]))
        if near:
            msg += "；当前已临近"
        return {"level": "yellow", "msg": msg}
    if near:
        return {"level": "yellow", "msg": "%s偏离值临近阈值" % "、".join(names[k] for k in near)}
    return None


# ---------------- 全市场盘后批处理 ----------------

def scan(codes, names=None, days=FETCH_DAYS):
    """全市场批处理：返回 (结果行 list, 统计 dict)。

    rows 每项可直接写入 dev_risk_daily；算不出的票**不落库**并计入 stat['skipped']。
    """
    names = names or {}
    t0 = time.time()
    series = _meoz_daily_adaptive(codes, days=days)
    stat = {"total": len(codes), "series": len(series), "ok": 0, "skipped": 0,
            "reasons": {}, "index_cache": {}}
    rows = []
    for code in codes:
        spec = spec_of(code)
        if not spec:
            stat["skipped"] += 1
            stat["reasons"]["unknown_board"] = stat["reasons"].get("unknown_board", 0) + 1
            continue
        srows = series.get(code) or []
        if not srows:
            stat["skipped"] += 1
            stat["reasons"]["stock_unavailable"] = stat["reasons"].get("stock_unavailable", 0) + 1
            continue
        if spec["index"] not in stat["index_cache"]:
            stat["index_cache"][spec["index"]] = index_series(spec["index"])
        idx_rows = stat["index_cache"][spec["index"]]
        if len(idx_rows) < WINDOWS[-1] + 1:
            stat["skipped"] += 1
            stat["reasons"]["index_unavailable"] = stat["reasons"].get("index_unavailable", 0) + 1
            continue
        res = compute(code, name=names.get(code), days=days,
                      srows=srows, idx_rows=idx_rows)
        if not res.get("ok"):
            stat["skipped"] += 1
            r = res.get("reason") or "unknown"
            stat["reasons"][r] = stat["reasons"].get(r, 0) + 1
            continue
        stat["ok"] += 1
        rows.append(_row_of(res))
    stat["elapsed"] = round(time.time() - t0, 2)
    return rows, stat


def _row_of(res):
    """把 compute 的结果压成 dev_risk_daily 一行。"""
    dev = res.get("dev") or {}
    room = res.get("room") or {}
    near = []
    for k, label in (("d3", "3日"), ("d10", "10日"), ("d30", "30日")):
        v = dev.get(k) or {}
        if v.get("thresh"):
            try:
                near.append((abs(float(v["value"])) / abs(float(v["thresh"])), label, v))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
    max_range = ""
    if near:
        near.sort(key=lambda x: -x[0])
        max_range = "%s %g%% / %g%%" % (near[0][1], near[0][2]["value"], near[0][2]["thresh"])
    w = res.get("warn") or {}
    d = res.get("detail") or {}
    return {
        "date": res.get("date"), "code": res.get("code"), "name": res.get("name") or "",
        "board": res.get("board") or "", "board_key": res.get("board_key") or "",
        "price": res.get("price"), "today_dev": res.get("today_dev"),
        "d3": (dev.get("d3") or {}).get("value"), "d3_status": (dev.get("d3") or {}).get("status"),
        "d10": (dev.get("d10") or {}).get("value"), "d10_status": (dev.get("d10") or {}).get("status"),
        "d30": (dev.get("d30") or {}).get("value"), "d30_status": (dev.get("d30") or {}).get("status"),
        "next_trigger_pct": room.get("next_trigger_pct"), "trigger_price": room.get("trigger_price"),
        "rule": room.get("rule") or "", "reachable": 1 if room.get("reachable") else 0,
        "max_range": max_range,
        "warn_level": w.get("level") or "", "warn_msg": w.get("msg") or "",
        "base_dates": json.dumps({str(n): (d.get(n) or {}).get("base_date")
                                  for n in WINDOWS}, ensure_ascii=False),
    }


def save_rows(rows, keep_days=60):
    """结果落库 dev_risk_daily（按 (date, code) 覆盖写）+ 清理过期。返回写入行数。"""
    if not rows:
        return 0
    conn = database.get_conn()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO dev_risk_daily ("
            "date, code, name, board, board_key, price, today_dev,"
            " d3, d3_status, d10, d10_status, d30, d30_status,"
            " next_trigger_pct, trigger_price, rule, reachable, max_range,"
            " warn_level, warn_msg, base_dates, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["date"], r["code"], r["name"], r["board"], r["board_key"], r["price"],
              r["today_dev"], r["d3"], r["d3_status"], r["d10"], r["d10_status"],
              r["d30"], r["d30_status"], r["next_trigger_pct"], r["trigger_price"],
              r["rule"], r["reachable"], r["max_range"], r["warn_level"], r["warn_msg"],
              r["base_dates"], int(time.time())) for r in rows if r.get("date") and r.get("code")])
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - keep_days * 86400))
        conn.execute("DELETE FROM dev_risk_daily WHERE date < ?", (cutoff,))
        conn.commit()
    except Exception as e:                       # noqa: BLE001
        log.error("[dev_risk] 结果落库失败 err=%s", e)
        return 0
    finally:
        conn.close()
    return len(rows)


def load_daily(date=None):
    """读 dev_risk_daily（date=None 取库中最新日期）。返回 {date, rows}。"""
    conn = database.get_conn()
    try:
        if not date:
            r = conn.execute("SELECT MAX(date) FROM dev_risk_daily").fetchone()
            date = r[0] if r else None
        if not date:
            return {"date": None, "rows": []}
        cur = conn.execute(
            "SELECT code, name, board, board_key, price, today_dev, d3, d3_status,"
            " d10, d10_status, d30, d30_status, next_trigger_pct, trigger_price,"
            " rule, reachable, max_range, warn_level, warn_msg "
            "FROM dev_risk_daily WHERE date=?", (date,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"date": date, "rows": rows}


def load_one(code, date=None):
    """读某票在 dev_risk_daily 的一行（date=None 取该票最新日期）。无则 None。"""
    conn = database.get_conn()
    try:
        if date:
            cur = conn.execute("SELECT * FROM dev_risk_daily WHERE date=? AND code=?",
                               (date, code))
        else:
            cur = conn.execute("SELECT * FROM dev_risk_daily WHERE code=? "
                               "ORDER BY date DESC LIMIT 1", (code,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def load_warn_map(date=None):
    """选股名单用：{code: {level, msg}}（只含**有标签**的票，无标签不出现在字典里）。

    ★ 刻意不返回「安全」占位 —— 名单行只渲染有风险的票，其余交给前端默认样式。
    """
    d = load_daily(date)
    out = {}
    for r in d.get("rows") or []:
        lvl = (r.get("warn_level") or "").strip()
        if lvl in ("red", "yellow"):
            out[str(r.get("code"))] = {"level": lvl, "msg": r.get("warn_msg") or ""}
    return out


# ---------------- 全市场扫描任务 / 盘后调度（worker.py 调用） ----------------

SCAN_KEY = "dev_risk:scan"


def acquire_scan_lock(ttl=1800):
    """跨进程扫描互斥（CacheStore.setnx）。拿到 True。"""
    try:
        return bool(_cstore.setnx(SCAN_KEY, 1, ttl=ttl))
    except Exception as e:                       # noqa: BLE001
        log.warning("[dev_risk] 扫描锁异常(视作拿到) err=%s", e)
        return True


def release_scan_lock():
    try:
        _cstore.delete(SCAN_KEY)
    except Exception as e:                       # noqa: BLE001
        log.warning("[dev_risk] 释放扫描锁失败 err=%s", e)


def universe():
    """全市场代码 -> 名称。取自 snapshot_bid 最新 9_25 定格（该表含全市场 5900+ 只）。"""
    conn = database.get_conn()
    try:
        r = conn.execute("SELECT MAX(date) FROM snapshot_bid WHERE time_point='9_25'").fetchone()
        date = r[0] if r else None
        if not date:
            return {}, None
        rows = conn.execute(
            "SELECT code, MAX(name) FROM snapshot_bid WHERE time_point='9_25' AND date=? "
            "GROUP BY code", (date,)).fetchall()
    finally:
        conn.close()
    return {str(c): (n or "") for c, n in rows}, date


def run_scan_job(date=None):
    """跑一次全市场批处理并落库。返回 stat（ok/reason 都在里面，不抛异常）。"""
    t0 = time.time()
    names, uni_date = universe()
    if not names:
        log.error("[dev_risk] 扫描中止：snapshot_bid 无 9_25 定格数据（近期没跑过采集?）")
        return {"ok": False, "reason": "no_universe", "msg": "取不到全市场名单"}
    log.info("[dev_risk] ========== 异动风险扫描 开始 名单日=%s 只数=%d ==========",
             uni_date, len(names))
    codes = sorted(names.keys())
    rows, stat = scan(codes, names=names)
    if not rows:
        log.error("[dev_risk] 扫描无任何结果（stat=%s）⇒ **不落库**，避免覆盖上一日结果",
                  stat)
        return {"ok": False, "reason": "no_rows", "stat": stat, "universe_date": uni_date}
    n = save_rows(rows)
    stat["saved"] = n
    stat["universe_date"] = uni_date
    stat["date"] = rows[0].get("date")            # 实际结果日（= 上游日K 的最后一根日期）
    stat["elapsed_total"] = round(time.time() - t0, 2)
    log.info("[dev_risk] ========== 扫描完成 结果日=%s 落库=%d 跳过=%d 耗时=%.1fs ==========",
             stat["date"], n, stat.get("skipped", 0), stat["elapsed_total"])
    return {"ok": True, **stat}


# ---------- 调度（后台线程，由 worker.py 启动）----------
# 盘后 15:45 —— 猫爪日K 收盘后当日行可读；比 15:30 收盘快照稍晚，避开与
#   连板天梯(15:30)/股性落库(18:30) 抢上游。WINDOW 给足 25 分钟容错（worker 重启/上游抖动）。
# 盘前 09:00 补救一次 —— 若盘后那次因上游异常没落库，次日开盘前补上，
#   保证 /api/dev/tomorrow 与选股名单 dev_warn 不断档（与 stock_temper 的 09:00 补救同思路）。
GEN_AT = 15 * 60 + 45        # 15:45
GEN_WINDOW = 25
REPAIR_AT = 9 * 60           # 09:00
REPAIR_WINDOW = 10
_fired = {}


def _bj():
    g = time.gmtime(time.time() + 8 * 3600)
    return g, g.tm_hour * 60 + g.tm_min


def _scan_has_data(date):
    """结果表是否已有 date 当天的数据（补救调度据此判断要不要重跑）。"""
    conn = database.get_conn()
    try:
        r = conn.execute("SELECT COUNT(1) FROM dev_risk_daily WHERE date=?", (date,)).fetchone()
        return int(r[0] or 0)
    except Exception:                                # noqa: BLE001
        return 0
    finally:
        conn.close()


def _scheduler_loop():
    """交易日 15:45 跑一次全市场扫描；09:00 做一次缺失补救。"""
    log.info("异动风险扫描 调度已启动（交易日 15:45 / 09:00 补救）")
    while True:
        try:
            g, hm = _bj()
            date = time.strftime("%Y-%m-%d", g)
            if trade_calendar.is_trade_day(date):
                # 期望的结果日 = 最近一个已收盘交易日（09:00 补救时它就是上一交易日）
                exp = _expected_last_trade_date()
                if exp and _fired.get("post") != exp and abs(hm - GEN_AT) <= GEN_WINDOW:
                    _fired["post"] = exp
                    threading.Thread(target=_run_guarded, args=(exp, "盘后"),
                                     daemon=True, name="dev-risk-scan").start()
                elif exp and _fired.get("repair") != exp and abs(hm - REPAIR_AT) <= REPAIR_WINDOW:
                    _fired["repair"] = exp          # 先标记，防 30s 轮询重复发起
                    if _scan_has_data(exp) == 0:
                        threading.Thread(target=_run_guarded, args=(exp, "盘前补救"),
                                         daemon=True, name="dev-risk-repair").start()
                    else:
                        log.debug("[dev_risk] 结果日 %s 已有数据，跳过盘前补救", exp)
        except Exception as e:                           # noqa: BLE001
            log.warning("异动风险扫描调度异常 err=%s", e)
        time.sleep(30)


def _run_guarded(expect_date, tag):
    if not acquire_scan_lock():
        log.info("[dev_risk] %s 扫描已在别处进行，本次跳过", tag)
        return
    try:
        stat = run_scan_job()
        got = stat.get("date") if isinstance(stat.get("date"), str) else None
        log.info("[dev_risk] %s 完成 expect=%s stat=%s", tag, expect_date, stat)
        if got and expect_date and got != expect_date:
            log.warning("[dev_risk] %s 落库日期 %s 与期望 %s 不一致 —— 上游日K可能还没出当日行",
                        tag, got, expect_date)
    except Exception as e:                               # noqa: BLE001
        log.error("[dev_risk] %s 扫描异常 err=%s", tag, e)
    finally:
        release_scan_lock()


def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="dev-risk-sched")
    t.start()
    log.info("异动风险扫描 调度线程已启动（交易日 15:45 盘后 + 09:00 补救）")
