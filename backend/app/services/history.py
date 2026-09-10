# -*- coding: utf-8 -*-
"""
历史批次服务: 选股结果落库 / 批次列表 / 批次明细 / 条件分页查询
==============================================================
"""
import json
import sqlite3
import time

from ..core import config, logger
from ..db import database
from . import scorer

log = logger.get_logger(__name__)


def _qc_pack(s):
    """实时评分 item → 抢筹明细 JSON(落库, 2026-09-09)

    历史批次/9:30 后锁定名单回看时, 抢筹数据源已切到最新交易日, 重新拉会串味 ——
    故落库时把"类型+幅度+摘要"一起存下, 回看直接读库还原。
    """
    if not s.get("qiangchou") or not s.get("qcType"):
        return None
    return json.dumps({"t": s.get("qcType") or "", "a": s.get("qcAmt"),
                       "c": s.get("qcChg"), "l": s.get("qcLast"),
                       "x": s.get("qcText") or "", "f": 1 if s.get("qcFallback") else 0},
                      ensure_ascii=False)


def _qc_unpack(d):
    """落库 JSON → 输出字段(回看还原细分; 无明细=旧批次, 退化为只打 🔥)"""
    out = {"qcType": "", "qcAmt": None, "qcChg": None,
           "qcLast": None, "qcText": "", "qcFallback": 0}
    if not d:
        return out
    try:
        j = json.loads(d) if isinstance(d, str) else (d or {})
    except (TypeError, ValueError):
        return out
    if isinstance(j, dict):
        out.update({"qcType": j.get("t") or "", "qcAmt": j.get("a"),
                    "qcChg": j.get("c"), "qcLast": j.get("l"),
                    "qcText": j.get("x") or "", "qcFallback": j.get("f") or 0})
    return out


def _conn():
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


# batch_stocks 里 NOT NULL 且无默认值的列 → 落库兜底值。
# 2026-09-10 生产事故: item["warnType"] 为 None(东财 f630 字段缺失, contract 语义上
# 保留 None=未知) → executemany 抛 "NOT NULL constraint failed: batch_stocks.warn_type"
# → 整个批次落库失败(名单一条都存不下), 且 except 分支没关连接 → 连接泄漏持有写锁,
# 后续任何写操作报 "database is locked"。一个 None 值级联成全站故障。
# 兜底语义: 未知按 0 存(异动等级 0 级), 保证"名单能落库"优先于"字段精确"。
_NOT_NULL_DEFAULTS = {
    "probability": 0.0, "confidence": 0.0, "bidChange": 0.0, "realChange": 0.0,
    "entityChange": 0.0, "bidTurnover": 0.0, "warnType": 0,
    "circulationMV": 0.0, "bidAmt": 0.0,
}


def _safe_num(s, key):
    """取落库数值: None/非数值 → 该列兜底值(防 NOT NULL 约束炸掉整批)"""
    v = s.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return _NOT_NULL_DEFAULTS.get(key, 0.0)
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):  # NaN/inf
        return _NOT_NULL_DEFAULTS.get(key, 0.0)
    return v


def save_batch(user_id, action, result, f, auto_applied=False):
    """把一次选股结果存为一个历史批次(归属指定用户), 返回批次 id; 失败返回 None
    auto_applied=True 用于 9:26 系统自动应用 (区别用户主动 lock/filter)

    2026-09-08 主人拍板: **空名单(stock_count=0)不落库** — 故障期不产生空批次。
    背景: 数据源故障/竞价窗口未到/降级路径异常时会产出 0 只结果并落库, 这些空批次
    会污染历史列表, 且此前被 refresh 当作"可复用批次"直读 → 页面刷新空白
    (uid=211 连续 64 次直读 0 只批次 1574)。读取侧虽已加 stock_count>0 过滤,
    写入侧仍会持续制造垃圾 → 源头拦截: 空结果直接返回 None, 不产生批次记录。
    """
    if not result:
        log.info("空名单不落库(不产生空批次) user_id=%s action=%s auto=%s",
                 user_id, action, 1 if auto_applied else 0)
        return None
    t = time.time()
    g = time.gmtime(t + 8 * 3600)   # 北京时间
    bdate = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    btime = "%02d:%02d:%02d" % (g.tm_hour, g.tm_min, g.tm_sec)
    filters_json = json.dumps({k: v for k, v in f.items() if k != "markets"}, ensure_ascii=False)
    markets = ",".join(f["markets"])
    conn = None
    try:
        conn = database.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO batches (batch_date, batch_time, ts, action, markets, filters, stock_count, user_id, auto_applied) VALUES (?,?,?,?,?,?,?,?,?)",
            (bdate, btime, int(t), action, markets, filters_json, len(result), user_id,
             1 if auto_applied else 0))
        batch_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO batch_stocks (batch_id, rank, code, name, probability, confidence, bid_change, real_change, entity_change, bid_turnover, warn_type, circulation_mv, industry, concept, bid_amt, bid_ratio, qiangchou, qc_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(batch_id, i + 1, s.get("code") or "", s.get("name") or "",
              _safe_num(s, "probability"), _safe_num(s, "confidence"),
              _safe_num(s, "bidChange"), _safe_num(s, "realChange"),
              _safe_num(s, "entityChange"), _safe_num(s, "bidTurnover"),
              _safe_num(s, "warnType"), _safe_num(s, "circulationMV"),
              s.get("industry"), s.get("concept"), _safe_num(s, "bidAmt"),
              s.get("bidRatio"), 1 if s.get("qiangchou") else 0, _qc_pack(s))
             for i, s in enumerate(result)])
        conn.commit()
        return batch_id
    except Exception as e:
        log.error("批次落库失败 user_id=%s action=%s 数量%d err=%s",
                  user_id, action, len(result), e)
        return None
    finally:
        # ⚠️ 必须关: 上面任一步抛异常(如 NOT NULL 约束)走 except 后若连接不关,
        # 写锁一直持有 → 后续所有写操作 "database is locked" 雪崩(2026-09-10 实测)。
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _canon_filter_fingerprint(f):
    """规范化筛选参数指纹: 排除 markets, 键排序(兼容 dict 插入顺序差异)"""
    return json.dumps({k: v for k, v in f.items() if k != "markets"},
                      ensure_ascii=False, sort_keys=True)


def _canon_markets_key(f):
    """规范化 markets 集合键: 排序后拼接(兼容前端传参顺序差异: hs,cyb == cyb,hs)"""
    return ",".join(sorted(f.get("markets") or []))


def recent_same_filter(user_id, f, window=60):
    """同一用户 window 秒内是否已存在相同参数(action=filter + 筛选参数 + markets 集合)的批次。
    2026-09-02 防刷: 脚本/自动化/手滑在窗口内反复点"应用", 历史只落一条。
    返回命中的最近批次 id, 无则 None(调用方正常落库)。
    注: 仅用于用户主动 filter; lock / system_batch(auto) / auto_apply 不受影响。"""
    fk = _canon_filter_fingerprint(f)
    mk = _canon_markets_key(f)
    t = int(time.time())
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, filters, markets FROM batches "
            "WHERE user_id=? AND action='filter' AND ts>=? ORDER BY ts DESC LIMIT 50",
            (user_id, t - window)).fetchall()
    finally:
        conn.close()
    for r in rows:
        try:
            old_fk = _canon_filter_fingerprint(json.loads(r["filters"] or "{}"))
        except Exception:
            old_fk = None
        old_mk = ",".join(sorted((r["markets"] or "").split(",")))
        if old_fk == fk and old_mk == mk:
            return r["id"]
    return None


def find_today_lock(user_id, now_ts=None):
    """查该用户"今日(北京时间)"最近一次手动 lock 批次(action='lock' AND auto_applied=0)。
    2026-09-02 当日幂等: 9:25 后重新进入页面自动 lock 直接读库返回, 不再全量重拉/重复落库。
    - now_ts 可注入(测试用), 默认当前时间; 返回该批次 dict(batches 行)或 None。
    - 只认手动 lock(auto_applied=0): 9:26 系统统一批次(auto_applied=1, user_id=0)不算用户锁定。"""
    t = now_ts if now_ts is not None else time.time()
    g = time.gmtime(t + 8 * 3600)            # 北京时间
    bdate = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM batches WHERE user_id=? AND action='lock' AND auto_applied=0 "
            "AND batch_date=? ORDER BY ts DESC LIMIT 1",
            (user_id, bdate)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def find_today_lock_matching(user_id, f, now_ts=None):
    """当日幂等命中判定: 是否已有"9:25 后落库 + 同筛选参数"的手动 lock 批次。

    语义(2026-09-02 主人确认):
    - 9:25 前竞价数据未定型, 每次进入页面都应重算拿最新 → 早于 9:25 的 lock 不幂等
      (否则 9:15 早进锁了不准名单, 9:29 刷新页面拿不到新名单)。
    - 9:25 后名单定型, 自动 lock(页面刷新/重新登录)若与已锁参数相同 → 直读该批次返回,
      不再全量重拉行情 + 不再堆 lock 历史; 用户主动点「锁定」带 force=1 强制重算。
    - 参数不同(用户改过筛选条件再重锁) → 不幂等, 正常重算落新批次。
    返回命中的批次 dict 或 None。"""
    t = now_ts if now_ts is not None else time.time()
    g = time.gmtime(t + 8 * 3600)
    bdate = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    gate = "09:25:00"                        # 9:25 后落库才算名单定型
    fk = _canon_filter_fingerprint(f)
    mk = _canon_markets_key(f)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM batches WHERE user_id=? AND action='lock' AND auto_applied=0 "
            "AND batch_date=? AND batch_time>=? ORDER BY ts DESC LIMIT 10",
            (user_id, bdate, gate)).fetchall()
    finally:
        conn.close()
    for r in rows:
        try:
            old_fk = _canon_filter_fingerprint(json.loads(r["filters"] or "{}"))
        except Exception:
            old_fk = None
        old_mk = ",".join(sorted((r["markets"] or "").split(",")))
        if old_fk == fk and old_mk == mk:
            return dict(r)
    return None


def _batch_matches_fingerprint(r, fk, mk):
    """批次行的筛选参数指纹 + markets 集合是否与请求一致(幂等/直读共用判定)"""
    try:
        old_fk = _canon_filter_fingerprint(json.loads(r["filters"] or "{}"))
    except Exception:
        return False
    old_mk = ",".join(sorted((r["markets"] or "").split(",")))
    return old_fk == fk and old_mk == mk


def find_today_reusable_batch(user_id, f, now_ts=None):
    """9:30 后 refresh 直读选批(2026-09-04 主人方案「打开/刷新直接从历史回看取最新」)。

    9:30 后名单已定型(lock / 9:26 system 批次落库), 页面打开与 30s 轮询的 refresh 若筛选
    参数未变 → 直读当日批次返回定格名单 + 实时行情覆盖, 不再全市场重拉 + 全量重评分
    (修复: 缓存 TTL=30s 恰与前端 30s 轮询同周期 → 每次 refresh 锁内全市场拉取, 多用户
    排队长尾, 生产实测最慢 57.97s)。

    选批优先级(参数指纹/markets 集合同幂等口径, 不一致视为"用户改过条件需重算"):
      ① 用户当日手动 lock(auto_applied=0)同参 — 9:25 定型权威名单(与前端 merge 的
         loadLockedBatchFromServer 同源, 展示一致)
      ② 无 → 用户当日手动 filter(auto_applied=0)同参 — 改条件应用后的最新筛选名单
      ③ 用户当日无任何手动批次时 → 当日 9:26 系统统一批次(user_id=0, auto_applied=1)
         — 全用户一致兜底名单(无需参数匹配, 与前端"无 lock/filter 展示统一名单"一致)
    返回 (batch_id, source) 或 (None, None); source ∈ lock|filter|auto。
    now_ts 可注入(测试用); 查询只读, 无副作用。"""
    t = now_ts if now_ts is not None else time.time()
    g = time.gmtime(t + 8 * 3600)
    bdate = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    fk = _canon_filter_fingerprint(f)
    mk = _canon_markets_key(f)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM batches WHERE user_id=? AND action IN ('lock','filter') "
            "AND auto_applied=0 AND batch_date=? ORDER BY ts DESC LIMIT 100",
            (user_id, bdate)).fetchall()
    finally:
        conn.close()
    # ① lock 同参(当日最近, 按 ts desc 首个命中即权威锁定名单)
    # 2026-09-08 修复(主人反馈"页面刷新没有选股数据, 点击应用后才有"): 本函数注释早就
    # 声明"空名单批次(stock_count=0)无直读价值, 跳过", 但 ①② 循环**从未实现该过滤**
    # (仅 ③ 系统兜底做了) → 当日若存在 0 只的 lock 批次(如早盘数据源故障期间落的空批次),
    # 每次 refresh 都直读它返回空名单, 页面一片空白; 而点「应用」走 filter 现算才有数据。
    # 实测 uid=211 今日: 批次 1574/1575/1576 均 0 只, refresh 连续 64 次直读 1574 返回 0。
    for r in rows:
        if r["action"] == "lock" and (r["stock_count"] or 0) > 0 \
                and _batch_matches_fingerprint(r, fk, mk):
            return r["id"], "lock"
    # ② filter 同参(当日最近)
    for r in rows:
        if r["action"] == "filter" and (r["stock_count"] or 0) > 0 \
                and _batch_matches_fingerprint(r, fk, mk):
            return r["id"], "filter"
    # ③ 用户当日无任何手动批次才允许系统统一批次兜底(有手动批次但参数已改 → 必须重算,
    #    否则改条件后刷新会错误直读系统名单); 空名单批次(stock_count=0)无直读价值,
    #    跳过走原重算(与现状一致, 避免直读空名单导致页面空白)
    if not rows:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT id FROM batches WHERE user_id=0 AND auto_applied=1 "
                "AND batch_date=? AND stock_count>0 ORDER BY ts DESC LIMIT 1",
                (bdate,)).fetchone()
        finally:
            conn.close()
        if row:
            return row["id"], "auto"
    return None, None


def find_recent_reusable_batch(user_id, f, now_ts=None, lookback_days=14):
    """休市/当日无批次时的**回退直读**(2026-09-05 主人需求:
    「多个用户反馈平台关闭后再打开首页就能显示关闭前选出的股, 别一进来就转圈」)。

    场景: 开盘日 9:30 后(当日系统批次为空/参数变更前的旧名单)以及休市时间,
    用户首屏 refresh 原先走 find_today_reusable_batch 只找**当日**批次 →
    非交易日/当日无批次必然 miss → 全市场重算 2.6s+(转圈)。
    本函数按同优先级口径(lock→filter→auto)在 lookback_days 窗口内取
    **最近日期**的同参批次 → 直读"关闭前选出的名单", 秒开。

    语义与当日版一致:
      · 参数指纹/markets 不一致(用户改过条件) → 不回退, 走重算
      · 空名单批次(stock_count=0)无直读价值, 跳过
      · 窗口内用户无任何手动批次 → 最近系统统一批次兜底
    返回 (batch_id, source, batch_date) 或 (None, None, None)。查询只读。"""
    t = now_ts if now_ts is not None else time.time()
    fk = _canon_filter_fingerprint(f)
    mk = _canon_markets_key(f)
    g = time.gmtime(t + 8 * 3600 - lookback_days * 86400)
    since = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
    conn = _conn()
    try:
        # 注意: 主查询**不过滤** stock_count —— 与当日版语义一致(空批次也算
        # "用户有手动批次", 不触发 ③ 系统兜底), 空批次仅在 ①② 命中时跳过
        rows = conn.execute(
            "SELECT * FROM batches WHERE user_id=? AND action IN ('lock','filter') "
            "AND auto_applied=0 AND batch_date>=? "
            "ORDER BY ts DESC LIMIT 200",
            (user_id, since)).fetchall()
        # ① 窗口内最近同参 lock(权威锁定名单); 空名单批次无直读价值, 跳过
        for r in rows:
            if r["action"] == "lock" and (r["stock_count"] or 0) > 0 \
                    and _batch_matches_fingerprint(r, fk, mk):
                return r["id"], "lock", r["batch_date"]
        # ② 窗口内最近同参 filter
        for r in rows:
            if r["action"] == "filter" and (r["stock_count"] or 0) > 0 \
                    and _batch_matches_fingerprint(r, fk, mk):
                return r["id"], "filter", r["batch_date"]
        # ③ 窗口内用户无任何手动批次 → 最近系统统一批次
        if not rows:
            row = conn.execute(
                "SELECT id, batch_date FROM batches WHERE user_id=0 AND auto_applied=1 "
                "AND batch_date>=? AND stock_count>0 ORDER BY ts DESC LIMIT 1",
                (since,)).fetchone()
            if row:
                return row["id"], "auto", row["batch_date"]
    finally:
        conn.close()
    return None, None, None


def get_batch_stocks_mapped(batch_id):
    """读批次明细并映射为前端 list 结构(与选股 item 同 camelCase 字段)。
    幂等直读批次时返回给前端, 保证字段与正常选股结果一致(不缺失 price 等实时字段为 None 由前端容错)。"""
    conn = _conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM batch_stocks WHERE batch_id=? ORDER BY rank", (batch_id,)).fetchall()]
    finally:
        conn.close()
    out = []
    for s in rows:
        out.append({
            "code": s["code"], "name": s["name"],
            "probability": s["probability"], "confidence": s["confidence"],
            "bidChange": s["bid_change"], "realChange": s["real_change"],
            "entityChange": s["entity_change"], "bidTurnover": s["bid_turnover"],
            "warnType": s["warn_type"], "circulationMV": s["circulation_mv"],
            "industry": s["industry"], "concept": s["concept"],
            "bidAmt": s["bid_amt"],
            "bidRatio": s.get("bid_ratio"),
            "qiangchou": 1 if s.get("qiangchou") else 0,
            **_qc_unpack(s.get("qc_detail")),
        })
    return out


def list_batches(user_id, limit=200):
    """历史批次列表(2026-08-30 主人需求: 即使用户没点选股, 也要看 system 自动存的批次)
    合并返回: 当前用户自己的批次 + system 公共批次(user_id=0, auto_applied=1)
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM batches WHERE user_id=? OR (user_id=0 AND auto_applied=1) "
        "ORDER BY ts DESC LIMIT ?", (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_batch(batch_id, user_id):
    """获取批次明细: 允许查看自己的或 system 公共批次(user_id=0)
    注: get_batch 旧实现只允许 user_id 匹配, 这里扩展支持 system 批次
    """
    conn = _conn()
    b = conn.execute(
        "SELECT * FROM batches WHERE id=? AND (user_id=? OR (user_id=0 AND auto_applied=1))",
        (batch_id, user_id)).fetchone()
    if not b:
        conn.close()
        return None, None
    stocks = conn.execute("SELECT * FROM batch_stocks WHERE batch_id=? ORDER BY rank", (batch_id,)).fetchall()
    conn.close()
    return dict(b), [dict(s) for s in stocks]


def query_history(uid, q):
    """历史条件查询(分页)。
    q: {k: [v,...]} 形式的 parse_qs 结果, 支持 date_from/date_to/bid_min/bid_max/
       mv_min/mv_max/prob_min/conf_min/action/page/pageSize
    返回 {total, page, pageSize, rows}
    """
    date_from = scorer._q_date((q.get("date_from") or [None])[0], "2000-01-01")
    date_to = scorer._q_date((q.get("date_to") or [None])[0], "2100-12-31")
    bid_min = scorer._opt_float(q, "bid_min")
    bid_max = scorer._opt_float(q, "bid_max")
    mv_min = scorer._opt_float(q, "mv_min")
    mv_max = scorer._opt_float(q, "mv_max")
    prob_min = scorer._opt_float(q, "prob_min")
    conf_min = scorer._opt_float(q, "conf_min")
    action = (q.get("action") or [""])[0]
    if action not in ("lock", "filter"):
        action = ""
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    conds = []
    params = []
    if bid_min is not None:
        conds.append("s.bid_change >= ?"); params.append(bid_min)
    if bid_max is not None:
        conds.append("s.bid_change <= ?"); params.append(bid_max)
    if mv_min is not None:
        conds.append("s.circulation_mv >= ?"); params.append(mv_min)
    if mv_max is not None:
        conds.append("s.circulation_mv <= ?"); params.append(mv_max)
    if prob_min is not None:
        conds.append("s.probability >= ?"); params.append(prob_min)
    if conf_min is not None:
        conds.append("s.confidence >= ?"); params.append(conf_min)
    if action:
        conds.append("b.action = ?"); params.append(action)
    cond_sql = " AND ".join(conds) if conds else "1=1"

    try:
        page = max(1, int((q.get("page") or [1])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int((q.get("pageSize") or [100])[0])))
    except (TypeError, ValueError):
        page_size = 100

    # 同一日期内, 同一股票 + 相同评分 视为"重复入选", 只保留一条(去重)
    # 2026-08-30 主人需求: 查询条件页面也合并 system 公共批次(user_id=0, auto_applied=1)
    base_sql = """
        FROM batch_stocks s
        JOIN batches b ON b.id = s.batch_id
        WHERE (b.user_id = ? OR (b.user_id = 0 AND b.auto_applied = 1))
              AND b.batch_date BETWEEN ? AND ? AND %s
        GROUP BY b.batch_date, s.code, s.probability
    """ % cond_sql
    base_params = [uid, date_from, date_to] + params

    sql = """
        SELECT b.batch_date, b.batch_time, b.action,
               s.code, s.name, s.probability, s.confidence,
               s.bid_change, s.real_change, s.entity_change,
               s.bid_amt, s.circulation_mv, s.industry, s.concept, s.warn_type,
               s.bid_ratio, s.qiangchou
        %s
        ORDER BY b.batch_date DESC, s.probability DESC, s.code
        LIMIT ? OFFSET ?
    """ % base_sql

    conn = _conn()
    try:
        # 注意: GROUP BY 后 COUNT(*) 是每组行数, 必须子查询包裹才算组数
        total = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 " + base_sql + ")", base_params).fetchone()[0]
        rows = conn.execute(sql, base_params + [page_size, (page - 1) * page_size]).fetchall()
    finally:
        conn.close()
    out_rows = []
    for r in rows:
        d = dict(r)
        d.update(_qc_unpack(d.pop("qc_detail", None)))   # JSON → 抢筹细分字段
        out_rows.append(d)
    return {"total": total, "page": page, "pageSize": page_size, "rows": out_rows}
