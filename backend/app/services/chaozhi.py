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

# 综合分的百分位"候选池"大小（见 load_picks 里的 🔴 说明：池内排名才有区分度）
CANDIDATE_POOL = 120
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
def _pick_date(models=("xgb", "lgb"), prefer=None):
    """最近的、**至少一个模型有 json** 的日期（从今天往前找 10 个自然日）。

    `prefer` 指定日期时直接用它（供"回看某日研判"与效果图核对；不存在则回落自动探测）。
    """
    if prefer:
        for m in models:
            if os.path.isfile(os.path.join(_out_dir(m), "predictions_%s.json" % prefer)):
                return prefer
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


def _pct_rank(pairs):
    """{code: 当日百分位}（0~1；并列取平均名次；样本 <2 时给 0.5）。

    🔴 为什么用百分位而不是原始 `ai_prob`：两个模型**都没做概率校准**（训练 AUC 金睛 0.825 /
       火眼 0.836，但分数分布可能不同）⇒ 直接加权会让某个模型静默主导。百分位只表达
       "当日相对强弱"，与校准无关，天然可比（见方案 §10.2）。
    """
    items = sorted(pairs.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 0:
        return {}
    if n == 1:
        return {items[0][0]: 0.5}
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        pct = ((i + j) / 2.0) / (n - 1)
        for k in range(i, j + 1):
            out[items[k][0]] = pct
        i = j + 1
    return out


def fuse(rows):
    """就地给每行加 `rankXgb` / `rankLgb` / `scoreFused` / `divergence`（方案 §十 一期口径）。

    综合分 = 100 × Σ(wᵢ·rankᵢ) / Σwᵢ（只对**存在的模型**求和再归一 ⇒ 单模型时退化为该模型百分位）；
    分歧度 divergence = |rank金睛 − rank火眼|（单模型时 None）。权重来自 config（可热改）。
    """
    w1 = float(getattr(config, "CHAOZHI_FUSION_W_XGB", 0.5) or 0)
    w2 = float(getattr(config, "CHAOZHI_FUSION_W_LGB", 0.5) or 0)
    rx = _pct_rank({r["code"]: r["scoreXgb"] for r in rows if r.get("scoreXgb") is not None})
    rl = _pct_rank({r["code"]: r["scoreLgb"] for r in rows if r.get("scoreLgb") is not None})
    for r in rows:
        a, b = rx.get(r["code"]), rl.get(r["code"])
        r["rankXgb"] = round(a, 4) if a is not None else None
        r["rankLgb"] = round(b, 4) if b is not None else None
        num = (w1 * a if a is not None else 0) + (w2 * b if b is not None else 0)
        den = (w1 if a is not None else 0) + (w2 if b is not None else 0)
        r["scoreFused"] = int(round(100 * num / den)) if den > 0 else None
        r["divergence"] = round(abs(a - b), 4) if (a is not None and b is not None) else None
    return rows


def _tag(fused, sx, sl, div, risk):
    """标签规则（2026-10-02 升级为**按综合分 + 模型一致性**，见方案 §10.2）。

    优先级：
      谨慎（高风险 / 任一模型 <60 / **分歧 ≥0.5**）
      > 关注（综合 ≥80 **且** 分歧 <0.25 **且** 低风险）—— 🔴 必须**两个模型都有分**才算"一致"
      > 观察（综合 ≥70 / 单模型 ≥85 / 风险中）
      > 待定（综合 55~70）> 谨慎（综合 <55）
    """
    lo = [v for v in (sx, sl) if v is not None]
    if risk == "high" or (lo and min(lo) < 60) or (div is not None and div >= 0.5):
        return "谨慎"
    f = fused if fused is not None else 0
    # "关注"要求**两个模型都有分且一致**（单模型证据不足 ⇒ 最高只到观察）
    if f >= 80 and div is not None and div < 0.25 and risk == "low":
        return "关注"
    if f >= 70 or (lo and max(lo) >= 85) or risk == "mid":
        return "观察"
    if f >= 55:
        return "待定"
    return "谨慎"


def load_picks(top=60, pick_date=None):
    """合并金睛(xgb) + 火眼(lgb) 两份 json（按 code 对齐），带风险档位与标签。

    返回 `(picks, meta)`；两者都缺 = `([], meta)`，`meta.notes` 里说明为什么（供前端如实展示）。
    """
    date = _pick_date(prefer=pick_date)
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

    # 🔴 2026-10-03 主人反馈"名单里很多跌停的"：展示层加**竞价涨幅下界**（与 FILTER_DEFAULTS.bidLt
    #    同源，默认 2.0）。上游历史文件（旧口径产物）里已存在的低开票同样不再展示。
    # 🔴 2026-10-06 修正: 原写法直接读 `FILTER_DEFAULTS` **常量** ⇒ 管理员在 settings 里调的
    #    `bidLt` / `bidLtRatio` **在本页不生效**（本页始终按代码默认 2.0 / 0.8 拦票）。
    #    实测：2026-10-06 撤掉"涨停幅度×80%"阈值后，金睛/火眼两页已放行低开票，
    #    本页却仍把它们全部剔空 ⇒ 页面显示 0 只。
    #    改为 `resolved_defaults()`（= FILTER_DEFAULTS 与 settings 的合并，与 system_batch /
    #    auto_apply / 前端同源），口径才真正统一。
    try:
        from .filter_defaults import resolved_defaults as _resolve_defaults
        _FD = _resolve_defaults()
        _bid_floor = float(_FD.get("bidLt", 0.0) or 0.0)
        _bid_ratio = float(_FD.get("bidLtRatio", 0.0) or 0.0)
    except Exception:                                            # noqa: BLE001
        _bid_floor, _bid_ratio = 0.0, 0.0

    def _limit_pct(code):
        c = str(code)
        if c[:3] in ("300", "301", "688", "689"):
            return 20.0
        if c[:1] in ("8", "4") or c[:3] == "920":
            return 30.0
        return 10.0

    def _out_of_range(code, chg, name=None):
        """是否不达标：低于阈值 / 高于板块涨停幅度×1.05（剔脏行）/ 名称为 ST 退市（主人口径）"""
        if name and ("ST" in str(name) or "退" in str(name)):
            return True
        if chg is None:
            return False
        lp = _limit_pct(code)
        return chg < max(_bid_floor, _bid_ratio * lp) or chg > lp * 1.05
    _drop = set()
    rows = {}
    for m, key in (("xgb", "scoreXgb"), ("lgb", "scoreLgb")):
        d = data.get(m) or {}
        # 🔴 2026-10-02 核实修正（主人："数据读不对"）: 必须用**过滤后的 top**。
        #   生产脚本按 竞价额≥3000万 / 竞价涨幅≤7% / 涨停率≥50% 筛过，≤30 只，与「金睛/火眼」两页**同一份名单**。
        #   原写法 `d["all"]`（过滤前全量 5921 只，机器实测）会带来两处错：
        #     ① 把竞价涨幅 9.9%~10.9% 的票（**竞价就涨停、根本买不进**）排进前列 —— 全量里 >7% 的有 15 只；
        #     ② 两模型页显示 30 只、本页显示 60 只 ⇒ 名单对不上（用户会以为两套数据打架）。
        #   `all` 只在当日 top 缺失（异常文件）时兜底。
        for r in (d.get("top") or d.get("all") or []):
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
                # 涨幅**口径标记**（'bid' 竞价涨幅 / 'day' 当日已实现 / 'realtime' 盘中实时）
                # —— 前端据此在数字前加"竞价/当日/实时"，不许裸显示（否则会被读成"当前涨幅"）
                "changeKind": None,
            })
            prob = r.get("ai_prob")
            item[key] = int(round(float(prob) * 100)) if prob is not None else None
            # ★ 涨幅 + 口径（2026-10-02 机器核实）：
            #   本页读的是 predictions_*.json **原始文件** ⇒ 文件里只有 `bid_change`（9:25 集合竞价涨幅）；
            #   `realTime`/`day_change` 是 `/api/aipick/data` 在**接口层现算注入**的（实时行情 / 本地日K），
            #   文件里一行都没有（实测 09-28、09-30 各 0 行）⇒ 本页常态是 `bid`。
            #   🔴 绝不用 `yesterday_chg`：该字段名字骗人 —— 实测 09-28 版数值 == 竞价涨幅、
            #      09-30 版数值 == 当日收盘涨幅（半夜 backfill 跑出来的就是"未来数据"）；
            #      生产脚本 2026-09-25 已把它从模型特征里移除并注明"实为当日竞价涨幅"。
            if r.get("realTime") is not None:
                chg, kind = r.get("realTime"), "realtime"
            elif r.get("day_change") is not None:
                chg, kind = r.get("day_change"), "day"
            else:
                chg, kind = r.get("bid_change"), "bid"
            item["change"] = round(float(chg), 2) if chg is not None else None
            item["changeKind"] = kind
            # 下界过滤（只对**竞价口径**生效）：先记入待删集合，循环结束后统一剔除
            #   —— 若在此处 rows.pop()，下一个模型的同名票会把它加回来（两天前踩过同类坑）
            if kind == "bid" and _out_of_range(code, item["change"], item.get("name")):
                _drop.add(code)

    _dropped_n = 0
    for _c in _drop:
        rows.pop(_c, None)
        _dropped_n += 1
    # ===== 交易层与结果回填（2026-10-03 上线）=====
    # 来源：aipick.db 的 pick_daily（每日 9:28 落库，含可买性分级/是否一字/当日封板结果）
    #   + label_truth（涨停池真值，作为当日封板的兜底）
    # 只读、缺表/缺文件一律静默降级（本页原有的名单/概率展示不受影响）
    meta = _pick_meta(date)
    # 🔴 2026-10-06 修正: 上面这行**整体替换**了 meta —— 函数开头放进去的 `date` / `models`
    #    随之丢失（下方注释只注意到 notes 被写进了废弃的旧 dict，漏了 date）⇒ 本函数返回的
    #    meta 里没有"数据日期"，前端无法显示"这份名单是哪天的"。
    #    这里把日期补回；键名 `date` 与 6 位股票代码不会冲突。
    meta["date"] = date
    if _dropped_n:      # ⚠️ 必须放在 meta 重新赋值**之后**（原位置那句写进了被丢弃的旧 dict）
        meta.setdefault("notes", []).append(
            "已按阈值（竞价涨幅介于 板块涨停幅度×%.0f%% ~ ×105%%）剔除 %d 只不达标票（含 ST/脏数据）"
            % (_bid_ratio * 100, _dropped_n))

    def _fuse(a, b):
        """两模型概率的融合：都为真取均值，只有一个则有哪个用哪个"""
        vs = [v for v in (a, b) if v is not None]
        return int(round(sum(vs) / len(vs))) if vs else None

    for it in rows.values():
        px, pl = it.get("scoreXgb"), it.get("scoreLgb")
        it["probFused"] = _fuse(px, pl)
        # 双模型共识：两个模型都给出概率且都 ≥50%（"两个大模型都认为能涨停"）
        it["consensus"] = bool(px is not None and pl is not None and px >= 50 and pl >= 50)
        m = meta.get(it["code"]) or {}
        it["fillGrade"] = m.get("fillGrade")
        it["isYidzi"] = m.get("isYidzi")
        it["isLimitUp"] = m.get("isLimitUp")
        # ★ 2026-10-06 主人要求：名称/代码下方联动"几板情况"（只给原始值，文案在前端）
        it["ydayZt"] = m.get("ydayZt")
        it["ydayLb"] = m.get("ydayLb")
        it["dayChg"] = m.get("closeChg")      # 当日收盘涨幅(%)：实时行情取不到时的回退值

    rmap = _risk_map(date)
    lut = []
    for it in rows.values():
        if it.get("scoreXgb") is None and it.get("scoreLgb") is None:
            continue
        it["risk"] = rmap.get(it["code"], "low")
        if scorer.is_st(it.get("name") or ""):
            it["risk"] = "high"
            it["st"] = True
        lut.append(it)
    # ★ 双模型融合（综合分 + 分歧度）—— 排序与标签都改为按**综合分**（方案 §十）
    # 🔴 关键: 百分位必须在**展示候选池内**算。实测踩到过: 在全市场 ~5500 只里算百分位，
    #    前 60 名会全部挤在 99~100 分（综合分失去区分度）。故先按"单模型最好分"截出候选池，
    #    池内再算百分位 ⇒ 0~100 自然铺开（口径 = "相对当日**候选票**的强弱"）。
    #   2026-10-02 起数据源改为两模型**过滤后 top**（各 ≤30）⇒ 池子天然 ≤60 只，
    #    CANDIDATE_POOL=120 已不再是瓶颈（保留它无害，异常时 all 兜底仍需设上限）。
    def _best(x):
        vals = [v for v in (x.get("scoreXgb"), x.get("scoreLgb")) if v is not None]
        return max(vals) if vals else 0
    lut.sort(key=lambda x: -_best(x))
    pool = lut[:CANDIDATE_POOL]
    fuse(pool)
    for it in pool:
        it["tag"] = _tag(it.get("scoreFused"), it.get("scoreXgb"), it.get("scoreLgb"),
                         it.get("divergence"), it["risk"])
    # 排序: 综合分降序，同分再用"单模型最好分"稳定次序
    pool.sort(key=lambda x: (-(x.get("scoreFused") if x.get("scoreFused") is not None else -1), -_best(x)))
    if not pool:
        meta["notes"].append("合并后没有任何带分数的个股")
    return pool[:top], meta


# ==================== ④b 战绩回看（2026-10-06 上线） ====================
HIT_SERIES_DAYS = 5        # Hero 下方战绩条取最近几个**有真值**的交易日

def load_hit_series(days=HIT_SERIES_DAYS):
    """近 `days` 个交易日「线上模型融合 Top10」的**实际封板率**（战绩回看）。

    🔴 口径纪律（2026-10-06 主人：加战绩但必须可信）：
       · 与页面名单**同源**：必须走 `load_picks(pick_date=d)` 取当日融合排序后的 Top10，
         绝不另写一套排序 —— 否则会出现"战绩算的是 A 名单、页面展示的是 B 名单"，
         那比没有战绩更糟（数字看着漂亮但对不上屏）。
       · 真值只认 `_pick_meta(d)` 的三层兜底（pick_daily.is_limit_up → features
         is_limit_up_v3 → label_truth），与个股列表的"已封板/未封板"完全一致。
       · **不编数字**：某日没有真值（未回填）⇒ 该日**整日不入列**；全都没有 ⇒ 返回 []。
         ⇒ 当日盘中没真值是常态（9:25 出名单、收盘后才知封板），不能显示 0%。
       · 日期来源 = pick_daily 的 DISTINCT trade_date（有名单落库才算一个交易日），
         多取几天用于剔除无真值日，凑够 `days` 条即止。

    返回升序 `[{date, total, hit, rate}, ...]`（`rate` = 百分数，保留 1 位）。
    任何异常静默返回 [] —— 战绩是增强项，**不能因为它把整页拖挂**。
    """
    import os
    import sqlite3
    db = os.environ.get("AIPICK_DB_PATH", "/opt/kuaixuan/aipick/scripts/data/aipick.db")
    if not os.path.exists(db):
        return []
    dates = []
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=5)
        try:
            for (d,) in c.execute(
                    "SELECT DISTINCT trade_date FROM pick_daily "
                    "ORDER BY trade_date DESC LIMIT ?", (days + 4,)):
                dates.append(str(d))
        finally:
            c.close()
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for d in dates:
        try:
            pk, _m = load_picks(top=10, pick_date=d)
            if not pk:
                continue
            meta = _pick_meta(d)
            tot = hit = 0
            for it in pk[:10]:
                v = (meta.get(it["code"]) or {}).get("isLimitUp")
                if v is None:
                    continue                    # 无真值不计入分母（不能当成"未封板"）
                tot += 1
                hit += int(v)
            if not tot:
                continue
            out.append({"date": d, "total": tot, "hit": hit, "rate": round(100.0 * hit / tot, 1)})
        except Exception:                                      # noqa: BLE001
            continue
        if len(out) >= days:
            break
    out.sort(key=lambda x: x["date"])           # 升序：前端画 sparkline 从左往右是时间正序
    return out


def summarize_hits(series):
    """把 `load_hit_series` 的结果压成一条摘要：近 n 日合计、合计封板率。

    ⚠️ 只在**有真值**的日子上合计；没有任何真值 ⇒ rate=None（前端显示 —，不许显示 0%）。
    """
    tot = sum(d.get("total") or 0 for d in (series or []))
    hit = sum(d.get("hit") or 0 for d in (series or []))
    return {"days": len(series or []), "total": tot, "hit": hit,
            "rate": (round(100.0 * hit / tot, 1) if tot else None)}


# ==================== ⑤ 总装（带 60s 缓存） ====================
def _build(pick_date=None):
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
        picks, pmeta = load_picks(pick_date=pick_date)
    except Exception as e:                                     # noqa: BLE001
        picks, pmeta = [], {"notes": ["个股列表读取失败：%s" % str(e)[:80]]}
    notes += (pmeta.get("notes") or [])
    # 2026-10-06: 影子模型块已随影子系统整体下线（主人决定），此处不再读取 current.json /
    #   pick_daily_shadow / shadow_gate，响应里也不再带 "shadow" 字段。
    if picks and all(p.get("divergence") is None for p in picks):
        notes.append("当前只有单模型有产出 ⇒ 综合分 = 该模型的当日百分位（非双模型融合）")

    # 2026-10-06 主人：Hero 下方加「近 5 日战绩」。⚠️ 走 load_picks 逐日回算 ⇒ 比单点读文件重，
    #   但整体结果被 build_overview 的 60s 缓存兜住（每 60s 最多算一次），且失败静默降级为 []。
    try:
        hit_series = load_hit_series()
    except Exception as e:                                     # noqa: BLE001
        hit_series, notes = [], notes + ["战绩序列读取失败：%s" % str(e)[:80]]

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
        # 战绩回看（有真值的交易日；可能为空数组 ⇒ 前端不渲染该条，不得显示 0%）
        "hitSeries": hit_series,
        "hitSummary": summarize_hits(hit_series),
        "senti": {"ztCount": (senti or {}).get("ztCount"), "lbgd": (senti or {}).get("lbgd")},
        "meta": {
            "pickDate": pmeta.get("date"),
            "models": pmeta.get("models") or {},
            "notes": notes,
            "periods": SERIES_N,
        },
    }


def build_overview(pick_date=None):
    """聚合入口（跨进程 60s 缓存 + 单飞）。

    `pick_date` 非空时（回看某日研判）**绕过缓存**直接计算 —— 否则不同日期的请求会互相污染。
    """
    if pick_date:
        return _build(pick_date=pick_date)
    try:
        return cache_store.cached_singleflight(
            cache_store.store, "chaozhi:overview", OVERVIEW_TTL, _build) or _build()
    except Exception as e:                                     # noqa: BLE001
        log.warning("chaozhi 聚合缓存失败, 直接计算 err=%s", str(e)[:120])
        return _build()


# ==================== 聚合结果预热（2026-10-06） ====================
# 🔴 为什么需要：`_build()` 是 **9 个串行环节**（情绪分/资金分/晋级率/情绪序列/资金序列/承接强弱/
#    个股名单/影子块/战绩序列），实测**冷算 1.8~3.9s**（逐环节：score_promote 1.33s、
#    load_hit_series 0.91s、load_shadow 0.49s、load_picks 0.30s… 单看都不算慢，串起来就 2s+）。
#    而缓存 TTL 只有 60s ⇒ **每个 TTL 周期后的第一个请求都要重算**，日活不高时命中率极低。
#    生产 access log 实证：66 次请求里 **49 次 >2s（74%）**，这不是冷启动偶发，是**常态**。
#
# ⇒ 后台每 30s 走一次带缓存的入口（未过期则命中、过期则重算并回写），TTL 60s : 预热 30s = 2:1，
#   用户请求恒命中。与昨比/spotMap/KPL 预热同款契约：失败绝不抛出，只记日志。
OVERVIEW_PREWARM_SEC = 30
_overview_prewarm_started = False


def _overview_prewarm_loop():
    import time as _t
    while True:
        try:
            build_overview()          # 带缓存的入口：未过期→直接命中(几乎零成本)；已过期→重算并回写
        except Exception as e:                                     # noqa: BLE001
            log.warning("超智聚合预热失败(%ds 后重试) err=%s", OVERVIEW_PREWARM_SEC, str(e)[:120])
        _t.sleep(OVERVIEW_PREWARM_SEC)


def start_overview_prewarm():
    """启动聚合预热线程（幂等）。"""
    global _overview_prewarm_started
    if _overview_prewarm_started:
        return
    _overview_prewarm_started = True
    import threading
    threading.Thread(target=_overview_prewarm_loop, name="czh-overview-prewarm",
                     daemon=True).start()
    log.info("超智聚合预热线程已启动（缓存 TTL %ds / 预热间隔 %ds）",
             OVERVIEW_TTL, OVERVIEW_PREWARM_SEC)


# ==================== ⑦ 核按钮 / 大幅低开榜（2026-10-06 主人给定口径） ====================
# 三个判定输入（主人原话）：
#   ① 昨日是否涨停 —— 昨日**收盘封住**涨停为"是"（盘中触及但炸板不算），含连板数
#      ⇒ 对应 features.yday_zt / yday_lb（口径见 db.attach_derived）
#   ② 今日竞价涨幅 —— (竞价价 − 昨收) / 昨收 × 100%，取 **9:25 定格价** ⇒ 对应 features.bid_change
#   ③ 涨跌停限制 —— 主板 10% / 创业·科创 20% / ST 5% / 北交所 30%，**按个股实际限制判定**
#
# 分档（从上到下，命中即停）：
#   档1 跌停开·极端核按钮：竞价涨幅 ≤ −(本板限制 − 0.5%)，**无论昨日是否涨停**
#        （主人原文"≤ −9.5%（主板）"= 主板 10% 跌停留 0.5 容差；ST/创业板/北交所按各自限制同步缩放）
#   档2 核按钮：昨日涨停（含连板）+ 竞价涨幅 ≤ −5%
#   档3 大幅低开：（昨日涨停 或 昨日涨幅 > 5%）+ 竞价涨幅 ∈ (−5%, −3%]
#   档4 不入选
RISK_TIER_LABELS = {
    1: "跌停开 · 极端核按钮",
    2: "核按钮",
    3: "大幅低开",
}


def _limit_pct_of(code, name=None):
    """涨跌停限制(%)：创业·科创 20 / 北交所 30 / **主板 ST 5** / 其余主板 10。

    🔴 判定顺序必须**先板块、后 ST**：创业板/科创板的 ST 股涨跌停**仍是 20%**，
       ST 5% 只适用于主板（与 `api/stats._limit_pct` 同款修正，见其 docstring 的实测依据）。
    ⚠️ 与 `api/stats._limit_pct`、`load_picks` 内的 `_limit_pct` 同口径（三处各一份是现状，改动需同步）。
    """
    s = str(code or "").zfill(6)
    if s[:3] in ("300", "301", "688", "689"):
        return 20.0
    if s[:1] in ("8", "4") or s[:3] == "920":
        return 30.0
    if name and "ST" in str(name).upper():
        return 5.0
    return 10.0


def load_risk_list(date=None, per_tier=20):
    """核按钮 / 大幅低开榜。返回 `{date, prevDate, tiers:[{tier,label,items,total}], notes}`。

    🔴 口径纪律：只读 aipick 库的 features（9:25 定格快照 + 昨日侧标签），**不另算、不凑数**；
       某档为空 ⇒ total=0、items=[]（前端显示"无"而不是隐藏整块）。
    🔴 异常值保护：|竞价涨幅| > 31% ⇒ 超出任何板块的涨跌停幅度（无涨跌幅限制的新股/退市整理
       或脏数据），**剔除**。实测 09-30 存在 `*ST元道 -75.83%` 这类行，若不过滤会霸占档1 榜首。
    """
    import os
    import sqlite3
    out = {"date": "", "prevDate": "", "tiers": [], "notes": []}
    db = os.environ.get("AIPICK_DB_PATH", "/opt/kuaixuan/aipick/scripts/data/aipick.db")
    if not os.path.exists(db):
        out["notes"].append("aipick 库不存在")
        return out
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=5)
        try:
            if not date:
                row = c.execute("SELECT MAX(trade_date) FROM features").fetchone()
                date = row[0] if row else None
            if not date:
                out["notes"].append("库内无 features 数据")
                return out
            prev = c.execute("SELECT MAX(trade_date) FROM features WHERE trade_date<?",
                             (date,)).fetchone()[0]
            out["date"], out["prevDate"] = date, prev or ""
            rows = c.execute(
                "SELECT f.code, f.name, f.bid_change, COALESCE(f.yday_zt,0), COALESCE(f.yday_lb,0),"
                "       f.concept, p.close_chg "
                "FROM features f LEFT JOIN features p ON p.code=f.code AND p.trade_date=? "
                "WHERE f.trade_date=?", (prev, date)).fetchall()
        finally:
            c.close()
    except Exception as e:                                     # noqa: BLE001
        out["notes"].append("读库失败: %s" % str(e)[:80])
        return out

    buckets = {1: [], 2: [], 3: []}
    for code, name, bc, yzt, ylb, con, ychg in rows:
        if bc is None:
            continue
        if bc < -31 or bc > 31:            # 无涨跌幅限制股 / 脏数据
            continue
        yzt = int(yzt or 0)
        ychg = float(ychg) if ychg is not None else None
        lp = _limit_pct_of(code, name)
        if bc <= -(lp - 0.5):              # 档1：触及跌停价（不看昨日是否涨停）
            tier, reason = 1, "竞价触及跌停价（%s限制 %.0f%%）" % ("ST " if lp == 5 else "", lp)
        elif yzt and bc <= -5.0:           # 档2：昨日涨停 + 竞价 ≤ −5%
            tier, reason = 2, "昨日涨停，今日竞价 ≤ −5%"
        elif (yzt or (ychg is not None and ychg > 5.0)) and -5.0 < bc <= -3.0:   # 档3
            tier = 3
            reason = ("昨日涨停" if yzt else "昨日涨幅 %.2f%% > 5%%" % ychg) + "，今日竞价 −3%~−5%"
        else:
            continue
        buckets[tier].append({
            "code": str(code).zfill(6), "name": name or "",
            "bidChange": round(float(bc), 2),
            "ydayZt": yzt, "ydayLb": int(ylb or 0),
            "ydayChg": (round(ychg, 2) if ychg is not None else None),
            "concept": con or "", "limitPct": lp, "reason": reason,
        })
    for t in (1, 2, 3):
        buckets[t].sort(key=lambda x: x["bidChange"])           # 低开越深越靠前
    out["tiers"] = [
        {"tier": t, "label": RISK_TIER_LABELS[t],
         "items": buckets[t][:per_tier], "total": len(buckets[t])}
        for t in (1, 2, 3)
    ]
    return out


def _pick_meta(date):
    """读 pick_daily + label_truth，返回 {code: {fillGrade, isYidzi, isLimitUp}}。

    date: 'YYYY-MM-DD'（与 features.trade_date 同格式）。任何异常都返回 {}（静默降级）。
    """
    import os
    import sqlite3
    db = os.environ.get("AIPICK_DB_PATH", "/opt/kuaixuan/aipick/scripts/data/aipick.db")
    if not os.path.exists(db):
        return {}
    out = {}
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=5)
        try:
            # pick_daily 自带 is_limit_up（当日封板结果，官方涨停价口径）⇒ 优先用它，
            # 这样"未封板"也能明确显示 0（此前只有涨停池内的票有值，其余 None 显示不出"未封板"）
            for code, fg, yz, zt in c.execute(
                    "SELECT code, COALESCE(fill_grade,''), COALESCE(is_yidzi,0), is_limit_up "
                    "FROM pick_daily WHERE trade_date=?", (date,)):
                out[str(code).zfill(6)] = {"fillGrade": fg, "isYidzi": int(yz),
                                           "isLimitUp": (int(zt) if zt is not None else None)}
        except Exception:
            pass
        # 第三层：features 的封板标签（官方涨停价口径，覆盖 ~95% 行）⇒ 让复盘结果尽量完整
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(features)")}
            lab = 'is_limit_up_v3' if 'is_limit_up_v3' in cols else ('is_limit_up' if 'is_limit_up' in cols else None)
            if lab:
                for code, zt in c.execute("SELECT code, %s FROM features WHERE trade_date=?" % lab, (date,)):
                    if zt is None:
                        continue
                    k = str(code).zfill(6)
                    out.setdefault(k, {"fillGrade": None, "isYidzi": None, "isLimitUp": None})
                    if out[k]["isLimitUp"] is None:
                        out[k]["isLimitUp"] = int(zt)
        except Exception:
            pass
        try:
            for code, zt in c.execute("SELECT code, zt FROM label_truth WHERE trade_date=?", (date,)):
                k = str(code).zfill(6)
                out.setdefault(k, {"fillGrade": None, "isYidzi": None, "isLimitUp": None})
                if out[k]["isLimitUp"] is None and zt is not None:   # 池真值兜底（不覆盖 pick_daily 结果）
                    out[k]["isLimitUp"] = int(zt)
        except Exception:
            pass
        # 第四层（2026-10-06 主人要求"名称/代码下方联动几板情况"）：features 的昨日侧标签。
        #   ydayZt = 昨日是否涨停 / ydayLb = 截至昨日连板数（口径见 db.attach_derived）。
        #   这里**只给原始值**，"首板 / N连板"的文案交给前端（展示层不塞业务文案）。
        try:
            for code, yzt, ylb, cc in c.execute(
                    "SELECT code, COALESCE(yday_zt,0), COALESCE(yday_lb,0), close_chg "
                    "FROM features WHERE trade_date=?", (date,)):
                k = str(code).zfill(6)
                out.setdefault(k, {"fillGrade": None, "isYidzi": None, "isLimitUp": None})
                out[k]["ydayZt"] = int(yzt)
                out[k]["ydayLb"] = int(ylb)
                # closeChg = 当日收盘涨幅(%)：供前端"实时涨幅"列在**休市/回看**时回退显示，
                #   否则假期打开页面那一列会整片空白。
                out[k]["closeChg"] = (float(cc) if cc is not None else None)
        except Exception:
            pass
        c.close()
    except Exception:
        return out
    return out
