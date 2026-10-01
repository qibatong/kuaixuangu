# -*- coding: utf-8 -*-
"""超智研判（一期）聚合服务 —— 原「AI预测」升级版聚合页的数据源。

主人 2026-10-01 拍板：按 `docs/超智研判-聚合页开发方案-20261001.md` §五「一期」实施
（得分卡 + 情绪/资金两条 10 日序列 + 双模型个股列表 + 风险三档 + 标签）。

设计红线（务必守住）
------------------
① **只读 + 零新增上游出网**：每个块都复用"别处已在调用、且带缓存"的函数，或**本地库/本地 json**：
     · 情绪温度   ← `kpl.fetch_sentiment()['strong']`（0~100，TTL `KPL_SENTI_TTL`）
     · 综合晋级率 ← `kpl.build_zt_echelon()['promote']['overall']`（60s 缓存）
     · 资金强度   ← `kpl.fetch_board_rank()`（60s 缓存）+ 猫爪成交额（30s 缓存）
     · 双模型分数 ← 直接读 aipick 输出目录的 `predictions_*.json`
                    （**只读文件 ⇒ 不触发推理、不吃 aipick 配额**）
     · 历史序列   ← 本地 SQLite（`limit_history` / `snapshot_bid`），每张表**一条聚合 SQL**
     · 风险档位   ← `dev_risk.load_warn_map()`（库内）
     · 承接强弱   ← `snapshot_bid` 当日 9_25 行 ∩ `limit_history` 昨日涨停（本地）
② **绝不复用 `/api/aipick/data`**：那个接口带 `quota_guard("aipick")`（免费用户 1 次/日），
   聚合页每次进来都拉 ⇒ 会把用户当天配额直接打光。
③ 任一子块失败**不得整页失败**：逐块 try/except，降级项写进 `meta.notes` 由前端如实展示
   （例如火眼 LGB 无 backfill ⇒ 某日缺 json，那时必须显示"火眼缺失"而不是 0 分）。

口径（方案 §四 A 案）
--------------------
· 资金强度 = `50 + 40·tanh(板块主力净额合计 ÷ 两市成交额 × 40)`，夹取 [5, 95]
  （k=40 的来历：全市场板块主力净额合计常在 ±200 亿、成交额 1.5 万亿量级 ⇒ 比值 ~0.013，
   tanh(0.52)≈0.48 ⇒ 中性偏强日落在 69 分附近，与参考图同量级）
· 情绪阶段 = 三段式：`转强`（昨日炸板股今日回封率 ≥40%）> `升温`（涨停数↑/炸板率↓/最高连板↑ 中 ≥2 项）
   > `分歧`（其余）
· 承接强弱 = 昨涨停股**今日竞价平均涨幅** ÷ `max(今日炸板率, 1)`
· 风险三档 = `red→high`、`yellow→mid`、`ST→high`、其余 `low`
· 标签     = `关注`（双模型都 ≥80 且风险低）/ `观察`（单模型 ≥80 或 风险中）
             / `待定`（双模型 60~80）/ `谨慎`（任一 <60 或 风险高）
"""
import json
import math
import os
import time

from ..core import config
from . import kpl
from . import cache_store
from . import dev_risk
from . import scorer
from ..db import database

log = kpl.log

# 序列窗口（交易日个数；本地库里有多少取多少，最多 10）
SERIES_N = 10
# 聚合结果缓存秒数（与 KPL_SENTI_TTL / KPL_BOARD_TTL 同量级）
OVERVIEW_TTL = 60
# 序列回溯的自然日窗口（够覆盖 10 个交易日 + 长假）
SERIES_LOOKBACK_DAYS = 30


def _today():
    return kpl._bj_today()


def _cutoff(days=SERIES_LOOKBACK_DAYS):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))


# ==================== ① 情绪：10 日序列（本地库） ====================
def load_emotion_series(limit=SERIES_N):
    """近 N 个**库内有数据**的交易日情绪序列。

    `limit_history` 每行 = 一只票某日的涨停情况（`is_limit=1` 封住 / `0` 炸板，`zt` = 连板数）。
    一条 SQL 取回区间内全部行，在 Python 里按日聚合（行数 ~3k/日，10 日 ≈ 3 万行，纯内存运算）。
    返回升序列表：[{date, zt, zb, zbRate, maxLb, fanbaoRate, phase}, ...]
    """
    conn = database.get_conn()
    try:
        cur = conn.execute(
            "SELECT date, code, is_limit, zt FROM limit_history WHERE date >= ? ORDER BY date",
            (_cutoff(),))
        rows = cur.fetchall()
    finally:
        conn.close()

    by_day = {}
    for r in rows:
        d = r[0]
        g = by_day.setdefault(d, {"zt": 0, "zb": 0, "maxLb": 0, "ztCodes": set(), "zbCodes": set()})
        is_limit = int(r[2] or 0)
        if is_limit == 1:
            g["zt"] += 1
            g["ztCodes"].add(str(r[1]))
            g["maxLb"] = max(g["maxLb"], int(r[3] or 0))
        else:
            g["zb"] += 1
            g["zbCodes"].add(str(r[1]))

    days = sorted(by_day.keys())[-limit:]
    out = []
    for i, d in enumerate(days):
        g = by_day[d]
        total = g["zt"] + g["zb"]
        # 回封率：昨日炸板股里今天封住的占比（"转强"的判据）
        prev = by_day[days[i - 1]] if i > 0 else None
        back = 0.0
        if prev and prev["zbCodes"]:
            back = len(prev["zbCodes"] & g["ztCodes"]) / float(len(prev["zbCodes"]))
        out.append({
            "date": d,
            "zt": g["zt"],
            "zb": g["zb"],
            "zbRate": round(g["zb"] / total * 100, 1) if total else 0.0,
            "maxLb": g["maxLb"],
            "fanbaoRate": round(back * 100, 1),
        })
    _assign_phase(out)
    return out


def _assign_phase(series):
    """按方案 §四.2 A 案给每一天打阶段标签（原地写入 `phase`）。"""
    for i, d in enumerate(series):
        if i == 0:
            d["phase"] = "分歧"
            continue
        p = series[i - 1]
        if d["fanbaoRate"] >= 40:
            d["phase"] = "转强"
            continue
        better = 0
        if d["zt"] > p["zt"]:
            better += 1
        if d["zbRate"] < p["zbRate"]:
            better += 1
        if d["maxLb"] > p["maxLb"]:
            better += 1
        d["phase"] = "升温" if better >= 2 else "分歧"


# ==================== ② 资金：10 日序列（本地库） ====================
def load_capital_series(limit=SERIES_N):
    """近 N 个**库内有数据**的交易日资金序列（`snapshot_bid` 9_25 行，一条聚合 SQL）。

    返回升序：[{date, bidAmt(元), mainNet(元), volRatio, boomRatio}]（`boomRatio` 用当日 9_25
    竞价额 ÷ 前一日，作为"竞价放量"的序列代理；盘中最新的"竞价放量榜"由 fetch_bid_boom 提供）。
    """
    conn = database.get_conn()
    try:
        cur = conn.execute(
            "SELECT date, SUM(bid_amt), SUM(auc_main_net), AVG(auc_vol_ratio) FROM snapshot_bid"
            " WHERE time_point='9_25' AND date >= ? GROUP BY date ORDER BY date",
            (_cutoff(),))
        rows = cur.fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        amt_wan = float(r[1] or 0)
        main_net = float(r[2] or 0)
        vol = float(r[3] or 0)
        out.append({
            "date": r[0],
            # 🔴 `snapshot_bid.bid_amt` 的单位是**万元**（见 services/auction_snapshot.py:511 注释），
            #    这里统一换算成**元**下发，与 mainNet / 前端 `yi()`（÷1e8 = 亿）同一口径。
            "bidAmt": amt_wan * 1e4,
            # 0 多为"当日该列未采集"（实测 2026-09-30 整列 0）⇒ 下发 None，前端显示 —，
            # 不能显示 0（会被读成"主力净额为 0"）
            "mainNet": main_net if main_net else None,
            "volRatio": round(vol, 2) if vol else None,
        })
    out = out[-limit:]
    for i, d in enumerate(out):
        prev = out[i - 1]["bidAmt"] if i > 0 else 0
        d["boomRatio"] = round(d["bidAmt"] / prev, 2) if prev > 0 else 0.0
    return out


# ==================== ③ 三个得分（复用已缓存函数） ====================
def score_emotion():
    """情绪温度 = 上游 `strong`（0~100，本身就是"情绪指标"）。"""
    s = kpl.fetch_sentiment() or {}
    return int(s.get("strong") or 0), s


def score_promote():
    """综合晋级率 = 连板天梯的 `promote.overall`（0~1 ⇒ ×100）。"""
    d = kpl.build_zt_echelon() or {}
    return round(float((d.get("promote") or {}).get("overall") or 0) * 100)


def score_capital():
    """资金强度 = 50 + 40·tanh(板块主力净额合计 ÷ 两市成交额 × 40)，夹取 [5,95]。

    全部取自已缓存函数（板块榜 60s / 猫爪成交额 30s）⇒ 零新增出网。
    上游全缺时返回 `(None, 说明)`，前端显示 `--` 而不是 0（0 会被误读成"极弱"）。
    """
    try:
        rank = kpl.fetch_board_rank() or []
        # ⚠️ 实测: `fetch_board_rank()` 返回的是**列表**（不是 {"list": [...]}）；
        #    这里两种形状都兼容，避免"形状猜错 ⇒ 资金强度永久降级"这种静默故障。
        boards = rank if isinstance(rank, list) else (rank.get("list") or [])
        main_net = sum(float(b.get("mainNet") or 0) for b in boards)
    except Exception as e:                                     # noqa: BLE001
        log.warning("chaozhi 资金强度: 板块榜失败 err=%s", str(e)[:120])
        return None, {"reason": "板块榜不可用"}
    try:
        from . import meoz_client
        emo = meoz_client.emo_daily() or {}
        amount = float(emo.get("am") or 0)                     # 三市成交额(元)
    except Exception:                                          # noqa: BLE001
        amount = 0.0
    if amount <= 0:
        try:
            from . import fetcher
            mb = fetcher.fetch_market_brief() or {}
            amount = float(mb.get("amount") or 0) * 1e8        # 该口径单位是亿元
        except Exception:                                      # noqa: BLE001
            amount = 0.0
    if amount <= 0:
        return None, {"reason": "成交额不可用"}
    v = 50 + 40 * math.tanh(main_net / amount * 40)
    return int(max(5, min(95, round(v)))), {"mainNet": main_net, "amount": amount}


def support_today(date):
    """承接强弱 = 昨涨停股今日竞价平均涨幅 ÷ max(今日炸板率, 1)。

    数据全在本地库：昨日涨停名单（`limit_history`）+ 今日 9_25 竞价涨幅（`snapshot_bid`）。
    ⚠️ 休市/盘前当日涨停池尚未落库 ⇒ 自动回退到**库内最近有数据的那一天**（否则该指标整段为空）。
    """
    conn = database.get_conn()
    try:
        # 回退：当日无行 ⇒ 取库内最近一天
        has = conn.execute("SELECT COUNT(*) FROM limit_history WHERE date=?", (date,)).fetchone()
        if not has or not int(has[0] or 0):
            r0 = conn.execute("SELECT MAX(date) FROM limit_history").fetchone()
            if r0 and r0[0]:
                date = r0[0]
        r = conn.execute("SELECT MAX(date) FROM limit_history WHERE date < ?", (date,)).fetchone()
        prev = r[0] if r else None
        if not prev:
            return None, {"reason": "无上一交易日涨停名单"}
        codes = [row[0] for row in conn.execute(
            "SELECT code FROM limit_history WHERE date=? AND is_limit=1", (prev,)).fetchall()]
        if not codes:
            return None, {"reason": "上一交易日无涨停"}
        q = ",".join("?" * len(codes))
        rows = conn.execute(
            "SELECT AVG(bid_change) FROM snapshot_bid WHERE date=? AND time_point='9_25'"
            " AND code IN (%s)" % q, tuple([date] + codes)).fetchall()
        avg_bid = float((rows[0][0] if rows and rows[0][0] is not None else 0) or 0)
        tot = conn.execute(
            "SELECT SUM(CASE WHEN is_limit=0 THEN 1 ELSE 0 END), COUNT(*) FROM limit_history WHERE date=?",
            (date,)).fetchone()
        zb = int(tot[0] or 0) if tot else 0
        alln = int(tot[1] or 0) if tot else 0
        zb_rate = (zb / alln * 100) if alln else 0.0
    finally:
        conn.close()
    if alln == 0:
        return None, {"reason": "当日涨停池未落库"}
    v = avg_bid / max(zb_rate, 1.0)
    return round(v, 2), {"avgBid": round(avg_bid, 2), "zbRate": round(zb_rate, 1), "prev": prev}


# ==================== ④ 双模型个股（只读 json，不吃配额） ====================
def _pick_date(models=("xgb", "lgb")):
    """最近的、**至少一个模型有 json** 的日期（从今天往前找 10 个自然日）。"""
    for i in range(10):
        d = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        for m in models:
            if os.path.isfile(os.path.join(_out_dir(m), "predictions_%s.json" % d)):
                return d
    return None


def _out_dir(model):
    return config.AIPICK_LGB_OUTPUT_DIR if model == "lgb" else config.AIPICK_OUTPUT_DIR


def _read_json(date, model):
    p = os.path.join(_out_dir(model), "predictions_%s.json" % date)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:                                     # noqa: BLE001
        log.warning("chaozhi 读模型 json 失败 %s/%s err=%s", date, model, str(e)[:100])
        return None


def _risk_map(date):
    """风险三档：red→high / yellow→mid / ST→high / 其余 low。返回 {code: 'low|mid|high'}。"""
    out = {}
    try:
        warn = dev_risk.load_warn_map(date) or {}
    except Exception:                                          # noqa: BLE001
        warn = {}
    for code, v in warn.items():
        lv = (v or {}).get("level") if isinstance(v, dict) else v
        out[str(code)] = "high" if lv == "red" else ("mid" if lv == "yellow" else "low")
    return out


def _tag(sx, sl, risk):
    """标签规则（方案 §四.4）。分数可能缺失（None）。

    🔴 优先级（方案原文四条有交叉 ⇒ 这里明确顺序）：
       谨慎（任一模型 <60 或 高风险） > 关注（双 ≥80 且低风险） > 观察（单 ≥80 或 风险中） > 待定。
       例：`(85, 50)` 既满足"单模型 ≥80"又满足"任一 <60" ⇒ 判**谨慎**（模型分歧大，不能算观察）。
    """
    lo = [v for v in (sx, sl) if v is not None]
    if risk == "high" or (lo and min(lo) < 60):
        return "谨慎"
    if sx is not None and sl is not None and sx >= 80 and sl >= 80 and risk == "low":
        return "关注"
    if (sx is not None and sx >= 80) or (sl is not None and sl >= 80) or risk == "mid":
        return "观察"
    return "待定"


def load_picks(top=60):
    """合并金睛(xgb) + 火眼(lgb) 两份 json（按 code 对齐），带风险档位与标签。

    返回 `(picks, meta)`；两者都缺 = `([], meta)`，`meta.notes` 里说明为什么（供前端如实展示）。
    """
    date = _pick_date()
    meta = {"date": date, "models": {}, "notes": []}
    if not date:
        meta["notes"].append("两个模型都没有可用预测文件")
        return [], meta

    data = {}
    for m in ("xgb", "lgb"):
        d = _read_json(date, m)
        data[m] = d
        meta["models"][m] = bool(d)
        if not d:
            # 文案口径（主人 2026-10-01）: 对外**只提金睛/火眼**，不提 XGB/LGB
            meta["notes"].append("%s当日无预测文件" % ("火眼" if m == "lgb" else "金睛"))

    rows = {}
    for m, key in (("xgb", "scoreXgb"), ("lgb", "scoreLgb")):
        d = data.get(m) or {}
        for r in (d.get("all") or d.get("top") or []):
            code = str(r.get("code") or "")
            if not code:
                continue
            item = rows.setdefault(code, {
                "code": code,
                "name": r.get("name") or "",
                "concept": r.get("concept") or "",
                "bidAmount": r.get("bid_amount"),
                "bidChange": r.get("bid_change"),
                # 🔴 两个模型的分数键**始终存在**：缺失= None（前端据此显示"火眼缺失"，
                #    而不是整个键不存在、或显示 0 分被误读）
                "scoreXgb": None,
                "scoreLgb": None,
                "change": None,
            })
            prob = r.get("ai_prob")
            item[key] = int(round(float(prob) * 100)) if prob is not None else None
            # 涨幅：优先实时（接口注入）→ 当日涨跌 → 竞价涨幅
            chg = r.get("realTime")
            if chg is None:
                chg = r.get("day_change")
            if chg is None:
                chg = r.get("bid_change")
            item["change"] = round(float(chg), 2) if chg is not None else None

    rmap = _risk_map(date)
    lut = []
    for it in rows.values():
        if it.get("scoreXgb") is None and it.get("scoreLgb") is None:
            continue
        it["risk"] = rmap.get(it["code"], "low")
        if scorer.is_st(it.get("name") or ""):
            it["risk"] = "high"
            it["st"] = True
        it["tag"] = _tag(it.get("scoreXgb"), it.get("scoreLgb"), it["risk"])
        # 排序：双模型均值降序（缺失的模型按另一个算，避免"只有火眼的票"被排到最后）
        vals = [v for v in (it.get("scoreXgb"), it.get("scoreLgb")) if v is not None]
        it["_avg"] = sum(vals) / len(vals) if vals else 0
        lut.append(it)
    lut.sort(key=lambda x: -x["_avg"])
    for it in lut:
        it.pop("_avg", None)
    if not lut:
        meta["notes"].append("合并后没有任何带分数的个股")
    return lut[:top], meta


# ==================== ⑤ 总装（带 60s 缓存） ====================
def _build():
    date = _today()
    notes = []

    emo_score, senti = score_emotion()
    if not emo_score:
        notes.append("情绪温度不可用（上游返回 0/空）")
    cap_score, cap_detail = score_capital()
    if cap_score is None:
        notes.append("资金强度不可用：%s" % (cap_detail or {}).get("reason", "未知"))
    try:
        pro_score = score_promote()
    except Exception:                                          # noqa: BLE001
        pro_score, notes = 0, notes + ["综合晋级率不可用"]

    try:
        emotion = load_emotion_series()
    except Exception as e:                                     # noqa: BLE001
        emotion, notes = [], notes + ["情绪序列读取失败：%s" % str(e)[:80]]
    try:
        capital = load_capital_series()
    except Exception as e:                                     # noqa: BLE001
        capital, notes = [], notes + ["资金序列读取失败：%s" % str(e)[:80]]
    try:
        support, sup_detail = support_today(date)
    except Exception as e:                                     # noqa: BLE001
        support, sup_detail = None, {"reason": str(e)[:80]}
    if support is None:
        notes.append("承接强弱不可用：%s" % (sup_detail or {}).get("reason", "未知"))

    try:
        picks, pmeta = load_picks()
    except Exception as e:                                     # noqa: BLE001
        picks, pmeta = [], {"notes": ["个股列表读取失败：%s" % str(e)[:80]]}
    notes += (pmeta.get("notes") or [])

    return {
        "date": date,
        "scores": {
            "emotion": emo_score or None,
            "capital": cap_score,
            "promote": pro_score,
            "support": support,
        },
        "emotion": {
            "series": emotion,
            "phases": sorted(set(d.get("phase") for d in emotion)) or ["升温", "分歧", "转强"],
            "latest": emotion[-1] if emotion else {},
        },
        "capital": {
            "series": capital,
            "latest": capital[-1] if capital else {},
        },
        "picks": picks,
        "senti": {"ztCount": (senti or {}).get("ztCount"), "lbgd": (senti or {}).get("lbgd")},
        "meta": {
            "pickDate": pmeta.get("date"),
            "models": pmeta.get("models") or {},
            "notes": notes,
            "periods": SERIES_N,
        },
    }


def build_overview():
    """聚合入口（跨进程 60s 缓存 + 单飞，避免并发重复计算）。"""
    try:
        return cache_store.cached_singleflight(
            cache_store.store, "chaozhi:overview", OVERVIEW_TTL, _build) or _build()
    except Exception as e:                                     # noqa: BLE001
        log.warning("chaozhi 聚合缓存失败, 直接计算 err=%s", str(e)[:120])
        return _build()
