# -*- coding: utf-8 -*-
"""AI 竞价选股 - 猫爪数据源（2026-09-20 改造：东财 → 猫爪）

主人指令：「东财数据换成猫爪数据」+「能通过猫爪获取的，都用猫爪的数据，自己不计算」。

本模块是 aipick **唯一**的行情取数入口，供 collector / predict_daily / backfill 三处复用。

======================================================================
数据源：猫爪 screening（实时选股），一接口顶五
======================================================================
* `apiname="screening"`，**不传 symbols = 全市场 5553 只**（实测）
* **支持 `tradedate` 参数回溯历史**（实测至少回溯到 2026-08 底，逐日 5546~5553 只）
* 返回结构 `{"data": {"fields": [...], "items": [[...]]}}` → 用 meoz_client._sym_rows 解析

字段映射（2026-09-20 实测对拍东财，5209 只交集逐字段核对）：

  aipick 特征      猫爪字段              单位换算        对拍结果(vs 东财)
  ─────────────────────────────────────────────────────────────────────
  bid_change      auc_pct_chg          已是 %          693 同 + 4516 近似(分位差)
  bid_amount      auc_amt              ÷ 1e4 → 万元    4567 同 + 591 近似 + 0 异
  bid_turnover    turnover_rate_f      已是 %          ★ 自由流通口径(系统性 > f8)
  price           close                已是元          5209/5209 完全一致
  circ_mv         circ_mv              ÷ 1e8 → 亿      1657 同 + 3536 近似(浮点)
  yesterday_chg   pct_chg              已是 %          5189 同 + 20 近似
  is_limit_up     pct_chg              阈值判定         主板≥9.8 / 创科≥19.8

🔴 铁律 1 —— `bid_turnover` 口径变更（主人已拍板）
  东财 `f8`   = 换手率（流通股本口径）
  猫爪 `turnover_rate_f` = **实际换手率（自由流通股本口径）**，名字里的 `_f` = free-float
  实测系统性更大（平安银行 0.44→1.05、万科A 4.54→6.81）。
  → 与本项目「所有流通市值改自由流通市值」的全局口径**一致**，故直接采用，不回算。

🔴 铁律 2 —— `pct_chg` vs `auc_pct_chg` 语义必须分清
  东财旧代码把 `f3` 一个字段当两个语义用（bid_change 回退 + yesterday_chg），是口径混淆。
  猫爪侧彻底分开：
    · 竞价涨幅 → `auc_pct_chg`（竞价时点相对昨收，实测 000001 = -0.1723%）
    · 最新涨幅 → `pct_chg`  （= close/pre_close-1，实测 000001 = 0.78%，已验算吻合）

🔴 铁律 3 —— 猫爪 auc_amt / circ_mv 单位都是**元**
  · `auc_amt`  元 → ÷1e4 得万元（aipick 全链路用万元）
  · `circ_mv`  元 → ÷1e8 得亿元（aipick 全链路用亿元）

🔴 铁律 4 —— `yesterday_chg` 必须取**前一交易日**的 pct_chg（2026-09-20 修标签泄漏）
  【问题】对**历史日期**回溯时，猫爪 `pct_chg` 返回的是**该日收盘涨跌幅**。
        若直接塞进 `yesterday_chg`，它会与标签 `is_limit_up`（同样由当日 pct_chg 判定）
        **完全同源** → 标签泄漏。实测：2026-09-18 全市场 5553 只中，
        `yesterday_chg == close_chg` 比例 **100%**，涨停样本 `yesterday_chg` 最小值恰为 9.80
        → 模型 AUC 虚高到 **0.9998**（明显失真）。
  【修正】回溯日 N 时，另拉 N-1 交易日的 `pct_chg` 作为 `yesterday_chg`
        —— 这是竞价时点**真实已知**的信息（昨日收盘涨幅），无泄漏。
        与旧东财语义一致（旧代码盘中 9:25 跑时 `f3` 就是"昨日/当时涨幅"）。
  【实现】`fetch_prev_trading_day()` 用回溯试错法找 N-1（猫爪无交易日历接口，
        用"逐日回退直到取到数据"探测，最多试 _PREV_MAX_TRIES 天）。

🔴 铁律 5 —— 范围过滤（脏值剔除）
  实测发现 `pct_chg` 存在异常值（max=1510.52）。`to_features` 内置范围校验，
  超出合理范围的整行丢弃（宁可少样本，不可污染训练）。
"""
import os
import sys

# 复用快选系统的猫爪客户端（apikey 读取 / 多线路重试 / 限流退避 / 缓存 全在那边）
_BACKEND = os.environ.get("KX_BACKEND_DIR", "/opt/kuaixuan/backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:
    from app.services import meoz_client as _M
except Exception as _e:                                       # pragma: no cover
    _M = None
    _IMPORT_ERR = _e
else:
    _IMPORT_ERR = None


# screening 需要的全量字段（一次拉全，避免二次请求）
SCREENING_FIELDS = (
    "tradedate,symbol,name,close,pre_close,pct_chg,turnover_rate_f,"
    "free_float_mv,circ_mv,auc_pct_chg,auc_amt,auc_turnover,is_st"
)

# 涨停判定阈值（主板 vs 创业板/科创板）
LIMIT_PCT_MAIN = 9.8
LIMIT_PCT_CYB_KCB = 19.8

# 涨跌幅合理范围（超出判为脏值 → 整行丢弃）。A股单日理论极值约 ±30%（新股/ST），放宽到 ±35。
CHG_MIN, CHG_MAX = -35.0, 35.0
# 竞价涨幅合理范围（竞价阶段幅度远小，但保留余量）
AUC_CHG_MIN, AUC_CHG_MAX = -35.0, 35.0
# 竞价金额上限（万元）：>1e7 万元(=1000亿) 显然为脏值
BID_AMT_MAX = 1e7

# 找前一交易日时最多回退的天数（覆盖长假）
_PREV_MAX_TRIES = 12


def available():
    """猫爪是否可用（未配 apikey 或模块导入失败 → False，调用方应回退）。"""
    if _M is None:
        return False
    try:
        return bool(_M.enabled())
    except Exception:
        return False


def unavailable_reason():
    if _M is None:
        return f"meoz_client 导入失败: {_IMPORT_ERR}"
    try:
        if not _M.enabled():
            return "猫爪未启用(无 apikey / use_meoz=0)"
    except Exception as e:
        return f"猫爪 enabled() 异常: {e}"
    return ""


def limit_pct(code):
    """按代码前缀返回涨停阈值：创业板(30)/科创板(68) 19.8%，其余 9.8%。"""
    c = str(code or "")
    if c.startswith("30") or c.startswith("68"):
        return LIMIT_PCT_CYB_KCB
    return LIMIT_PCT_MAIN


def _f(v, default=0.0):
    """安全转 float。"""
    if v is None or v in ("", "-"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fetch_market_raw(trade_date=None, timeout=30, quiet=False):
    """真正调猫爪 screening（**无缓存**，供回溯内部复用）。"""
    if not available():
        if not quiet:
            print(f"⚠️ 猫爪不可用({unavailable_reason()}) → 无法取数")
        return {}
    params = {}
    if trade_date:
        params["tradedate"] = str(trade_date).replace("-", "")
    try:
        data = _M.call("screening", params=params, fields=SCREENING_FIELDS, timeout=timeout)
    except Exception as e:
        if not quiet:
            print(f"⚠️ 猫爪 screening 调用失败(tradedate={trade_date}): {type(e).__name__} {e}")
        return {}
    rows = _M._sym_rows(data, key="symbol")
    if not rows and not quiet:
        code = (data or {}).get("code") if isinstance(data, dict) else None
        print(f"⚠️ 猫爪 screening 返回空(tradedate={trade_date}, code={code})")
    return rows


# 全市场快照缓存（进程内）：回溯时同一日会被 fetch_market / prev_chg_map 反复取，缓存避免重复调用
_MKT_CACHE = {}


def fetch_market(trade_date=None, timeout=30, quiet=False):
    """拉全市场竞价快照（猫爪 screening）。

    trade_date: None → 最新交易日；"YYYY-MM-DD" / "YYYYMMDD" → 回溯该日
    返回: {symbol: {字段: 值}} 映射（原始猫爪字段，未换算单位）；失败返回 {}。
    ★ 进程内缓存（含空结果），回溯场景避免重复调用。
    """
    key = str(trade_date or "__latest__")
    if key in _MKT_CACHE:
        return _MKT_CACHE[key]
    rows = _fetch_market_raw(trade_date, timeout=timeout, quiet=quiet)
    _MKT_CACHE[key] = rows
    return rows


def fetch_prev_trading_day(trade_date, max_tries=_PREV_MAX_TRIES):
    """找 trade_date 的**前一交易日**（返回 "YYYY-MM-DD"，找不到返回 None）。

    猫爪无交易日历接口 → 用回溯试错：从 N-1 起逐日往前试，第一个能取到数据的即为前一交易日。
    实测节假日/周末会自动返回空，故试错法可靠。最多试 max_tries 天（覆盖春节等长假）。
    """
    from datetime import datetime, timedelta
    try:
        cur = datetime.strptime(str(trade_date), "%Y-%m-%d")
    except ValueError:
        cur = datetime.strptime(str(trade_date).replace("-", ""), "%Y%m%d")
    for i in range(1, int(max_tries) + 1):
        d = (cur - timedelta(days=i)).strftime("%Y-%m-%d")
        if fetch_market(d, quiet=True):     # 空 = 非交易日
            return d
    return None


def _in_range(v, lo, hi):
    return v is not None and lo <= v <= hi


def to_features(rows, trade_date, prev_chg_map=None):
    """猫爪原始行映射 → aipick features 行（单位换算 + 语义映射 + 范围过滤）。

    rows: fetch_market() 的返回值 {symbol: {猫爪字段}}
    prev_chg_map: {symbol: 前一交易日收盘涨幅%} —— **用于 yesterday_chg，避免标签泄漏**。
                  为 None 时 yesterday_chg 置 0.0（宁可缺失，绝不用当日 pct_chg 造成泄漏）。
    返回: list[dict]，字段与 db.upsert_features 对齐。

    ★ 范围过滤：任意关键字段超出合理范围 → **整行丢弃**（防脏值污染训练）。
    """
    prev_chg_map = prev_chg_map or {}
    out = []
    n_dropped = 0
    for code, s in rows.items():
        auc_chg = _f(s.get("auc_pct_chg"), None) if s.get("auc_pct_chg") is not None else None
        pct = _f(s.get("pct_chg"), None) if s.get("pct_chg") is not None else None
        amt = _f(s.get("auc_amt"), None) if s.get("auc_amt") is not None else None
        px = _f(s.get("close"), None) if s.get("close") is not None else None

        # ---- 范围校验（脏值整行丢弃）----
        if not _in_range(pct, CHG_MIN, CHG_MAX):
            n_dropped += 1
            continue
        if auc_chg is not None and not _in_range(auc_chg, AUC_CHG_MIN, AUC_CHG_MAX):
            n_dropped += 1
            continue
        if amt is not None and not (0 <= amt <= BID_AMT_MAX):
            n_dropped += 1
            continue
        if px is None or px <= 0:
            n_dropped += 1
            continue

        # yesterday_chg 取**前一交易日**收盘涨幅（竞价时点已知，无泄漏）
        ychg = prev_chg_map.get(code)
        out.append({
            "trade_date": trade_date,
            "code": code,
            "name": s.get("name") or "",
            # 竞价涨幅：auc_pct_chg（竞价时点相对昨收，已是 %）
            "bid_change": round(_f(s.get("auc_pct_chg")), 2),
            # 竞价金额：auc_amt(元) → 万元
            "bid_amount": round(_f(s.get("auc_amt")) / 1e4, 1),
            "bid_volume": None,
            # 竞价换手率：turnover_rate_f（实际换手率，自由流通口径，已是 %）
            "bid_turnover": round(_f(s.get("turnover_rate_f")), 2),
            "warn_type": 0,
            "price": round(_f(s.get("close")), 2),
            # 流通市值：circ_mv(元) → 亿元
            "circ_mv": round(_f(s.get("circ_mv")) / 1e8, 2),
            # 昨日涨幅：**前一交易日**收盘涨幅（无泄漏）；缺失置 0
            "yesterday_chg": round(_f(ychg), 2),
            "industry": "",
            "concept": "",
        })
    if n_dropped:
        print(f"  [范围过滤] {trade_date}: 丢弃脏值行 {n_dropped}")
    return out


def prev_chg_map(trade_date, max_tries=_PREV_MAX_TRIES, quiet=False):
    """取 trade_date 前一交易日的 {symbol: pct_chg}，供 to_features 的 yesterday_chg 用。

    找不到前一交易日 → 返回 {}（调用方会把 yesterday_chg 置 0）。
    ★ fetch_market 有进程内缓存，回溯时同一日不会重复请求。
    """
    pday = fetch_prev_trading_day(trade_date, max_tries=max_tries)
    if not pday:
        if not quiet:
            print(f"  ⚠️ {trade_date}: 找不到前一交易日 → yesterday_chg 置 0")
        return {}
    rows = fetch_market(pday, quiet=True)
    out = {}
    for code, s in rows.items():
        v = _f(s.get("pct_chg"), None) if s.get("pct_chg") is not None else None
        if v is not None and CHG_MIN <= v <= CHG_MAX:
            out[code] = v
    if not quiet:
        print(f"  {trade_date}: 前一交易日 = {pday}（{len(out)} 只昨涨幅）")
    return out


def to_labels(rows, trade_date):
    """猫爪原始行映射 → 收盘标签行 {code, is_limit_up, close_chg}。

    is_limit_up: pct_chg >= 阈值（主板 9.8 / 创业板科创板 19.8）
    """
    out = []
    for code, s in rows.items():
        chg = _f(s.get("pct_chg"))
        out.append({
            "code": code,
            "is_limit_up": 1 if chg >= limit_pct(code) else 0,
            "close_chg": round(chg, 2),
        })
    return out
