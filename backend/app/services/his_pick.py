# -*- coding: utf-8 -*-
"""《顺势而为竞价终极版》选股逻辑的**服务端移植版**（2026-10-03）。

🔴 原则：**不动他的模式与逻辑**。本文件的评分/过滤/排序三段，逐行等价于原件：
    docs/reference/his-pick-竞价终极版.html（md5 961edece9454fb6535c186e10344de24）
    对应原件函数：getBidChange / getYesterdayChange / getEntityChange / getBidVolumeRatio /
                 getBidTurnover / getWarnType / isFirstBoard / isST / isSuspended /
                 computeScore / applyFilters / processAllStocks / getMarketFs / getStockApiUrl
    **包含原件里既有的字段误用（f10 当市值等），一律照旧，不修** ✓

🔴 JS 语义必须保真的地方（照抄时最容易错，已逐条处理）：
    ① `parseFloat` / `parseInt` 的宽容解析（''、'-'、'12abc'）
    ② **NaN 是假值**（`s.f4 || 0`），而 Python 里 `bool(nan) is True` ⇒ 必须显式判 NaN
    ③ `Math.round` = floor(x+0.5)（**不是** Python 的银行家舍入 round(2.5)=2）
    ④ NaN 参与比较恒为 False（Python 与 JS 一致 ✓，无需额外处理）

本文件只做"移植 + 取数 + 快照"；**快照落我们自己的库**（不再额外打东财，避免被限流）。
"""
import math
import re
import sqlite3
import time

NaN = float('nan')


# ============================ JS 兼容小工具 ============================
def pf(v):
    """JS `parseFloat`：宽容解析；失败返回 NaN。"""
    if v is None:
        return NaN
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', str(v).strip())
    if not m:
        return NaN
    try:
        return float(m.group(0))
    except Exception:                                   # noqa: BLE001
        return NaN


def pint(v):
    """JS `parseInt`（十进制、向零截断）：失败返回 NaN。"""
    if v is None:
        return NaN
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return NaN
        return float(int(v))
    m = re.match(r'^[+-]?\d+', str(v).strip())
    return float(m.group(0)) if m else NaN


def or0(v):
    """JS `v || 0`：**NaN / '' / None / false / 0 都是假值** ⇒ 取 0（Python 里 bool(nan)=True ⇒ 必须显式判）。"""
    if v is None or v is False:
        return 0
    if isinstance(v, str) and v.strip() == '':
        return 0
    if isinstance(v, (int, float)) and (v == 0 or (isinstance(v, float) and math.isnan(v))):
        return 0
    return v


def jround(x):
    """JS `Math.round` = floor(x + 0.5)。"""
    if isinstance(x, float) and not math.isfinite(x):
        return 0.0
    return float(math.floor(float(x) + 0.5))


def jmax(a, b):
    if isinstance(a, float) and math.isnan(a):
        return NaN
    return a if a > b else b


def jmin(a, b):
    if isinstance(a, float) and math.isnan(a):
        return NaN
    return a if a < b else b


# ============================ 他的原件：字段派生（逐字等价） ============================
def getBidChange(s):
    """原件：`(s.f615 != null && !isNaN(s.f615)) ? parseFloat(s.f615) : parseFloat(s.f3||0)`"""
    v = s.get('f615')
    if v is not None and not (isinstance(v, float) and math.isnan(v)):
        x = pf(v)
        if not math.isnan(x):
            return x
    return pf(or0(s.get('f3')))


def getYesterdayChange(s):
    return pf(or0(s.get("f3")))


def getEntityChange(s):
    o, c = pf(or0(s.get('f17'))), pf(or0(s.get('f2')))
    if o == 0 or math.isnan(o):
        return 0.0
    return ((c - o) / o) * 100 if not math.isnan(c) else NaN


def getBidVolumeRatio(s):
    return 0.0                                          # 原件恒返回 0（死分支，照旧）


def getBidTurnover(s):
    bv, mv, p = pf(or0(s.get('f5'))), pf(or0(s.get('f10'))), pf(or0(s.get('f2')))
    if mv <= 0 or p <= 0 or bv <= 0:                     # NaN 参与比较 → False ⇒ 落到 0
        return 0.0
    t = (bv * p) / (mv * 100)
    return 0.0 if (math.isnan(t) or math.isinf(t)) else t


def getWarnType(s):
    return pint(or0(s.get('f630')))


def isFirstBoard(s):
    return getWarnType(s) >= 5


def isST(name):
    return ('ST' in name) or ('*ST' in name)


def isSuspended(s):
    f4, f5 = pf(or0(s.get('f4'))), pf(or0(s.get('f5')))
    return (f4 <= 0) or (f5 == 0)


# ============================ 他的原件：评分（逐字等价） ============================
def computeScore(stock):
    bidChange = getBidChange(stock)
    bidTurnover = getBidTurnover(stock)
    bidVolRatio = getBidVolumeRatio(stock)
    warnType = getWarnType(stock)
    circMV = pf(or0(stock.get('f10')))               # ⚠️ 原件把 f10(量比) 当"流通市值"，照旧
    yesterdayApprox = getYesterdayChange(stock)

    bidScore = 0.15
    if 3 <= bidChange <= 5.5:
        bidScore = 1
    elif bidChange >= 2:
        bidScore = 0.88
    elif bidChange >= 1.5:
        bidScore = 0.65
    elif bidChange > 0:
        bidScore = 0.4
    else:
        bidScore = 0.1

    activityScore = 0.2
    if bidTurnover >= 0.8:
        activityScore = 1
    elif bidTurnover >= 0.4:
        activityScore = 0.88
    elif bidTurnover >= 0.2:
        activityScore = 0.72
    elif bidTurnover >= 0.08:
        activityScore = 0.5
    elif bidTurnover > 0:
        activityScore = 0.3
    else:
        activityScore = 0.1
    if bidVolRatio >= 0.3:
        activityScore = jmin(1, activityScore + 0.1)

    warnScore = 1 if warnType == 5 else 0.85 if warnType == 4 else 0.6 if warnType == 3 else 0.18
    marketScore = 1 if circMV < 30 else 0.88 if circMV < 60 else 0.68 if circMV < 120 else 0.45 if circMV < 250 else 0.22
    yesterdayScore = 0.9 if (3 <= yesterdayApprox < 9.5) else 0.65 if yesterdayApprox >= 1 else 0.4 if yesterdayApprox >= 0 else 0.25 if yesterdayApprox > -3 else 0.15

    base = (bidScore * 0.34 + activityScore * 0.32 + warnScore * 0.17
            + marketScore * 0.11 + yesterdayScore * 0.06)
    prob = jmax(5, jmin(95, base * 100))
    conf = 65
    if warnType >= 4:
        conf += 10
    if bidTurnover >= 0.4:
        conf += 8
    if 2 <= bidChange <= 6.5:
        conf += 7
    conf = jmin(90, jmax(55, conf))
    return {'probability': int(jround(prob)), 'confidence': int(jround(conf)),
            'bidTurnover': bidTurnover, 'bidVolRatio': bidVolRatio}


# ============================ 他的原件：过滤与主流程（逐字等价） ============================
def applyFilters(scored_list, filterSettings):
    out = []
    for item in scored_list:
        raw = item.get('rawStock')
        name = item.get('name') or ''
        bidChg = item.get('bidChange')
        prob = item.get('probability')
        conf = item.get('confidence')
        mv = pf(or0((raw or {}).get('f10')))
        price = pf(or0((raw or {}).get('f2')))
        if filterSettings.get('stSuspend'):
            if isST(name):
                continue
            if raw is not None and isSuspended(raw):
                continue
        if filterSettings.get('limitUp') and raw is not None and isFirstBoard(raw):
            continue
        if bidChg > filterSettings.get('bidGt'):
            continue
        if prob < filterSettings.get('probLt') and conf < filterSettings.get('confLt'):
            continue
        if mv > filterSettings.get('floatMvGt'):
            continue
        if price > filterSettings.get('priceGt'):
            continue
        out.append(item)
    return out


def processAllStocks(rawStocks, filterSettings):
    scored = []
    for stock in rawStocks:
        sc = computeScore(stock)
        scored.append({
            'code': str(stock.get('f12') or '').zfill(6), 'name': stock.get('f14') or '',
            'probability': sc['probability'], 'confidence': sc['confidence'],
            'bidChange': getBidChange(stock), 'realChange': pf(or0(stock.get('f3'))),
            'entityChange': getEntityChange(stock), 'bidTurnover': sc['bidTurnover'],
            'bidVolRatio': sc['bidVolRatio'], 'speed': pf(or0(stock.get('f8'))),
            'warnType': getWarnType(stock), 'circulationMV': pf(or0(stock.get('f10'))),
            'industry': stock.get('f100') or '-', 'concept': stock.get('f103') or '-',
            'province': stock.get('f102') or '-', 'rawStock': stock})
    # 原件：`scored.sort((a,b) => b.probability - a.probability)` —— JS 的 sort 是**稳定**的
    scored.sort(key=lambda x: -x['probability'])
    return applyFilters(scored, filterSettings)


# ============================ 他的原件：数据源 URL / 板块 → fs ============================
DEFAULT_MARKETS = ['hs', 'cyb', 'kcb']                  # 原件 defaultFilterSettings.markets


def getMarketFs(checked=None):
    """原件 getMarketFs()：勾选 → 东财 fs 参数（逐字等价；浏览器勾选状态在服务端由入参提供）"""
    checks = list(checked if checked is not None else DEFAULT_MARKETS)
    if len(checks) == 0:
        return 'm:1+t:2,m:0+t:6'
    parts = []
    for v in checks:
        if v == 'hs':
            parts += ['m:1+t:2', 'm:0+t:6']
        elif v == 'cyb':
            parts.append('m:0+t:80')
        elif v == 'kcb':
            parts.append('m:1+t:23')
    seen, uniq = set(), []
    for x in parts:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return ','.join(uniq)


def getStockApiUrl(fs):
    """原件 getStockApiUrl()：URL 与参数**逐字**保留（含 pz=200 / fid=f3 / fields / ut）"""
    return ('https://push2dycalc.eastmoney.com/api/qt/clist/get?fs=%s&fltt=2&invt=2&fields='
            'f2,f3,f4,f5,f6,f8,f10,f12,f14,f17,f18,f20,f615,f630,f100,f102,f103&fid=f3&po=1'
            '&pn=1&pz=200&np=1&ut=c92c50e6b0fab2c17cd5e276e9a79c42' % fs)


DEFAULT_FILTERS = {                                     # 原件 defaultFilterSettings（逐项一致）
    'stSuspend': True, 'markets': DEFAULT_MARKETS, 'limitUp': True,
    'bidGt': 7, 'probLt': 65, 'confLt': 65, 'floatMvGt': 100, 'priceGt': 30,
}


# ============================ 取数（服务端代理）+ 快照（落我们自己的库） ============================
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0 Safari/537.36')
DB = '/opt/kuaixuan/aipick/scripts/data/aipick.db'


def snapshot_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS his_pick_daily(
        trade_date TEXT, fetched_at TEXT, code TEXT, name TEXT, rank INTEGER,
        probability REAL, confidence REAL, bid_change REAL, real_change REAL,
        entity_change REAL, warn_type REAL, industry TEXT, concept TEXT,
        bid_turnover REAL, PRIMARY KEY(trade_date, code))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS his_pick_meta(
        trade_date TEXT PRIMARY KEY, fetched_at TEXT, pool_size INTEGER, picked INTEGER, src TEXT)""")
    conn.commit()


def save_snapshot(items, day, meta):
    """把"他选出的名单"落**我们自己的库**（纯写入，不再打东财 ⇒ 不会加剧本就紧张的额度）

    🔴 2026-10-08 主人反馈「网页版只有 100 多只，我们有 300 多只」⇒ 根因 = **当日名单在累积**：
      原件语义是"**一次拉取 + 一次过滤 = 一份名单**"——浏览器端 `cachedStocks` 每次都被**整份替换**
      （9:30 前"重新选股"重算覆盖，9:30 后冻结、只刷现涨不重算）⇒ 名单**从不做并集**。
      而本表主键是 (trade_date, code)、写入用 INSERT OR REPLACE ⇒ 当天**不同筛选条件**
      （markets / 阈值 / limitUp 各改一次）各写一份，结果就**并进了同一天**：
      实测 2026-10-08 `his_pick_meta.pool_size=200`（200 只池子的上限**在生效**）但 `picked=336`、
      且含 **12 行 `warn_type≥5`**（当时勾着"剔除昨日涨停"，本不该出现）
      ⇒ **名单比池子还大**，这是并集的铁证（网页版 10-03 那天只有一次请求 ⇒ 113 只 ✓ 正常）。

    修法：**当日整批替换** —— 写入前先 `DELETE` 当日旧行（= 原件"覆盖"语义，不是并集）；
      历史日期不受影响（仍可按日回看）。同一份名单被 9:30 后的"刷现涨回写"再写一次时，
      也是整批替换（内容同集，仅现涨/实体新值）⇒ 不会重新长胖。
    """
    try:
        c = sqlite3.connect(DB, timeout=5)
        snapshot_table(c)
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        # 🔴 2026-10-08: 写入前**强制按评分降序**再枚举 rank。
        #   原先直接 enumerate(items) ⇒ rank 只是"调用方那份列表的下标"，而调用方是**带筛选条件**的
        #   （竞价时段不同 filters 各写一次）⇒ rank 会互相错位、不再是评分序（实测 2026-10-08 全表无序）。
        #   这里只重排**本次要写的顺序**，不改他原件的评分/过滤/排序逻辑（他的 processAllStocks 本就
        #   已按评分排好，此行为对正常输入是**无操作**，只对异常输入兜底）。
        items = sorted(items, key=lambda x: -(x.get('probability') or 0))
        # 🔴 2026-10-08 修复「名单累积」：当日整批替换（先清后写），与原件"覆盖"语义一致。
        #    不加这一行 ⇒ 本日不同筛选条件的结果会并集，实测 336 行 > 池子 200 行（见函数 docstring）。
        c.execute("DELETE FROM his_pick_daily WHERE trade_date=?", (day,))
        for i, it in enumerate(items, 1):
            c.execute("INSERT OR REPLACE INTO his_pick_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (day, ts, it['code'], it.get('name', ''), i, it.get('probability'),
                       it.get('confidence'), it.get('bidChange'), it.get('realChange'),
                       it.get('entityChange'), it.get('warnType'), it.get('industry'),
                       it.get('concept'), it.get('bidTurnover')))
        c.execute("INSERT OR REPLACE INTO his_pick_meta VALUES (?,?,?,?,?)",
                  (day, ts, int(meta.get('pool_size') or 0), len(items), 'eastmoney-clist'))
        c.commit()
        c.close()
        return True
    except Exception:                                   # noqa: BLE001
        return False
