# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 每日预测 (增强版 2026-08-27)
9:25-9:30 运行：拉取当日竞价快照 → 模型预测涨停概率 → 生成 HTML 预测报告。

本版改动:
  1) 过滤规则参数化(predict 可传 mv_min/mv_max/bid_amt_min/bid_chg_max);
     **2026-09-26 主人指令: 默认值中取消市值门槛**(市值仅作前端/App 自筛维度, 训练与预测侧均不设门槛)
  2) json 同时保存"过滤前全量候选集" all(含 ai_prob/circ_mv/bid_amount/bid_change/...),
     供 App 前端按用户自定义规则实时过滤并放宽/收紧; 默认规则的 top(≤30) 与 HTML 仍保持
  3) 新增 backfill(): 遍历历史上已生成的 predictions_*.json, 凡缺 all 的重新调用 predict(d)
     重算全量候选(从快选 9_25 竞价快照库读 stock), 兼容 8-14 起的全市场快照

2026-09-20: 数据源 **东财 → 猫爪**（主人指令）。
  · collector.fetch_market(d) 现返回**特征行**(走猫爪 screening, 已单位换算),
    故此处不再套 to_features(); fetch_market(d) 为空才回退东财(to_features(fetch_market_eastmoney())).
  · FEATURES 6 项必须与 train_model.py 逐字一致（否则预测崩）。

输出：output/predictions_YYYY-MM-DD.html / .json（直接浏览器打开）

2026-09-25 双模型改造（主人指令：LightGBM 上生产机 + 新建展示页）
--------------------------------------------------------------------------------
新增 --algo {xgb,lgbm} / --out-dir / --model-path / --date / --force，
**默认值与改造前逐字一致**（algo=xgb、out-dir=../output）⇒ 既有调度任务行为零变化。

🔴 两条必须遵守的语义（否则两模型互相踩）：
  1) 产物目录必须隔离：LGB 走 ../output/lgb。本脚本"当日 json 已存在就不覆盖（只写
     _rerun）"与"每次主输出都覆盖 latest.html"两条语义，会让共用目录的两个模型互相
     判定"报告已存在"并互相覆盖首页报告。
  2) 概率取值不能想当然：XGB 用 predict_proba(X)[:,1]；LightGBM 的 Booster.predict(X)
     **本身已是正类概率**，再套 [:,1] 会取到错位/不存在的维度 —— 静默产出全错分数而
     页面看着完全正常。统一走 _predict_proba()。
"""
import argparse
import json
import re
import os
import sys
import glob
import sqlite3 as _sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import today  # noqa E402
from collector import (fetch_from_kuaixuan, fetch_market,  # noqa E402
                       fetch_market_eastmoney, to_features)
import trade_calendar as _tc  # noqa E402  交易日历桥接(事实来源 = backend/app/core/trade_calendar.py)
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "models"))
OUT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "output"))

# 运行期配置（由 _configure() 落地；未传参时 = 改造前默认行为）
ALGO = "xgb"          # xgb | lgbm
MODEL_PATH = None     # None → 按 ALGO 推默认模型文件

# 各算法默认模型文件名（相对 MODEL_DIR）
_DEFAULT_MODEL = {"xgb": "model_xgb.json", "lgbm": "model_lgb.txt"}
# 各算法训练脚本名（仅用于报错提示）
_TRAIN_SCRIPT = {"xgb": "train_model.py", "lgbm": "train_lgbm.py"}
# 展示用模型名
_MODEL_LABEL = {"xgb": "快选・金睛 XGBoost", "lgbm": "快选・金睛 LightGBM"}

# ★ 2026-09-25：移除 `yesterday_chg`（6 维 → 5 维）。线上它实为**当日竞价涨幅**（9:25 撮合出开盘价，
#   此刻唯一价格就是开盘价 ⇒ pct_chg ≡ 竞价涨幅 ≡ bid_change），与 `bid_change` 同信息；
#   训练基座里那一列却是**前一交易日涨幅** ⇒ 同名两义，换基座即 train/serve skew。
#   500 天样本外代价 −0.0015 池化AUC，详见 train_model.py 顶部注释 / docs/BACKLOG-特征集5维化.md。
#   ⚠️ 必须与 backend/app/services/ai_predict.py 及模型文件**同批发布**。
FEATURES = [
    "bid_change",    # 竞价涨幅
    "bid_amount",    # 竞价金额(万元)
    "bid_turnover",   # 竞价换手率
    "price",         # 价格（9:25 竞价价）
    # ★ 2026-10-02 主人指令(命中率优先)：改用**口径无关**派生特征。
    #   动因：线上 circ_mv 是**自由流通市值**、离线基座是**流通市值**（实测 947 vs 2251 亿，
    #   比值因股而异 1.5~2.4 倍）⇒ 直接用 circ_mv 原始值必然 train-serve skew。
    #   改分位/昨日侧后两侧都能就地算出，与数据源口径无关。实测(1620 天基座, ≤10% 口径):
    #   top3 命中 72.9% → 75.7%、top5 67.9% → 70.2%、池化 AUC 0.8422 → 0.8489。
    #   ⚠️ 与 db.py 的 MODEL_FEATURES 及线上模型文件**必须同批发布**（宽度失配会静默降级）。
    "mv_rank",       # 当日市值分位（口径无关，替代 circ_mv 原始值）
    "amt_rank",      # 当日竞价额分位
    "rank_diff",     # amt_rank − mv_rank（相对市值的热度）
    "price_inv",     # 1/价格（低价股偏好）
    "yday_zt",       # 昨日是否涨停
    "yday_lb",       # 截至昨日连板数
    "prev_mkt_zt",    # 昨日全市场涨停家数（情绪）
]


def _normalize_algo(v):
    """算法名归一；非法值回退 xgb（绝不因拼错参数就把产物写到别的目录）"""
    s = str(v or "").strip().lower()
    if s in ("lgbm", "lgb", "lightgbm"):
        return "lgbm"
    return "xgb"


def _configure(args):
    """把命令行参数落到模块级全局（predict/_gen_html/backfill 都读全局）"""
    global ALGO, OUT_DIR, MODEL_PATH
    ALGO = _normalize_algo(getattr(args, "algo", None))
    od = getattr(args, "out_dir", None)
    OUT_DIR = os.path.abspath(os.path.expanduser(od)) if od else \
        os.path.normpath(os.path.join(SCRIPT_DIR, "..", "output"))
    mp = getattr(args, "model_path", None)
    MODEL_PATH = os.path.abspath(os.path.expanduser(mp)) if mp else None


def _build_parser():
    p = argparse.ArgumentParser(description="AI 竞价选股 - 每日预测（默认行为与旧版一致）")
    p.add_argument("--algo", default="xgb", help="模型算法: xgb(默认) | lgbm")
    p.add_argument("--out-dir", default=None,
                   help="产物目录(默认 ../output; LightGBM 建议 ../output/lgb, 必须与 XGB 隔离)")
    p.add_argument("--model-path", default=None, help="显式模型文件路径(默认按 algo 推)")
    p.add_argument("--date", default=None, help="指定交易日 YYYY-MM-DD(默认今天)")
    p.add_argument("--force", action="store_true",
                   help="当日报告已存在时强制覆盖主文件(默认不覆盖, 只写 _rerun 对照版)")
    return p


def _load_model(algo, model_path):
    """按算法加载模型对象。lightgbm 延迟导入：XGB 链路不需要装它。"""
    if algo == "lgbm":
        import lightgbm as lgb
        return lgb.Booster(model_file=model_path)
    import xgboost as xgb
    m = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
    m.load_model(model_path)
    return m


def _apply_calib(proba, algo):
    """2026-10-06 概率校准：模型原始概率 → Isotonic 映射（ECE 0.13~0.16 → ~0）。
    校准器缺失/损坏时原样返回（兼容旧模型与旧预测文件）。"""
    try:
        import pickle
        p = os.path.join(MODEL_DIR, "calib_%s.pkl" % algo)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                iso = pickle.load(f)
            return np.asarray(iso.predict(np.asarray(proba, dtype=float)), dtype=float)
    except Exception:
        pass
    return proba


def _predict_proba(model, X, algo):
    """★ 唯一的概率出口。见模块 docstring 第 2 条：两个算法的正类概率取法不同。"""
    if algo == "lgbm":
        # LightGBM Booster.predict 返回的是正类概率本身（不是两列矩阵）
        proba = np.asarray(model.predict(X), dtype=float).reshape(-1)
    else:
        proba = model.predict_proba(X)[:, 1]
    return _apply_calib(proba, algo)


def _model_meta(algo, model_path):
    """供前端展示"这是哪个模型 / 何时训练 / AUC 多少"的元信息（缺失字段为 None）。"""
    meta = {"algo": algo, "model_file": os.path.basename(model_path),
            "n_features": len(FEATURES), "trained_at": None, "auc": None}
    try:
        meta["model_mtime"] = datetime.fromtimestamp(
            os.path.getmtime(model_path)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        meta["model_mtime"] = None
    try:
        if algo == "lgbm":
            mp = os.path.join(MODEL_DIR, "model_meta_lgb.json")
            if os.path.isfile(mp):
                with open(mp, "r", encoding="utf-8") as f:
                    mj = json.load(f) or {}
                meta["trained_at"] = mj.get("trained_at")
                meta["auc"] = mj.get("auc")
                meta["algo_version"] = mj.get("lightgbm_version")
        else:
            rp = os.path.join(OUT_DIR, "train_report.json")
            if os.path.isfile(rp):
                with open(rp, "r", encoding="utf-8") as f:
                    rj = json.load(f) or {}
                meta["trained_at"] = rj.get("trained_at")
                meta["auc"] = rj.get("auc")
    except Exception as e:
        print(f"[meta] 模型元信息读取失败(忽略, 不影响预测): {e}")
    if not meta.get("trained_at"):
        meta["trained_at"] = meta.get("model_mtime")
    return meta

# 默认过滤规则
# ★ 2026-09-26 主人指令: **取消 AI 层市值门槛**(原 30~100 亿)。
#   理由三条:
#     ① 市值只是**前端筛选维度** —— App 拿 `all`(过滤前全量候选)按用户自定义规则自筛,
#        历史综合查询另有 mv_min/mv_max 输入框; AI 层再默认硬卡是多此一举。
#     ② **训练侧本就不设门槛** —— db.load_features() 只按 `is_limit_up IS NOT NULL` 取样本,
#        市值(circ_mv)在训练里是**特征列**、不是过滤条件 ⇒ 预测侧跟着对齐才对。
#     ③ 2026-09-26 口径由流通市值 → **自由流通市值**后, 同一个数值 30~100 的语义整体漂移
#        (实测等效区间已变成 16~56 亿), 继续硬卡只会误伤中大盘。
#   ⇒ 默认 None = 不过滤; **显式传值仍生效**(保留参数化能力, 供前端/历史回放使用)。
DEFAULT_MV_MIN, DEFAULT_MV_MAX = None, None
DEFAULT_BID_AMT_MIN = 3000
# 2026-10-06 主人要「榜单完整度」⇒ 竞价涨幅上界改为**按板块自适应**（= 该板块涨停幅度 × 1.04）。
#   🔴 为什么不能用统一上界：20% / 30% 板会被误伤 —— 实测 09-30 善水科技(301190，创业板)
#      竞价涨幅 +10.89%、模型概率 0.977、当日实际涨停，却被统一 10.4% 挡在名单外（10.89 > 10.4）。
#   自适应后：主板 10×1.04 = **10.4%**（与主人给的 10.4% 一致）· 创业/科创 20×1.04 = 20.8% ·
#      北交所 30×1.04 = 31.2%。chaozhi 展示层的上界（涨停幅度×1.05）比本层略宽，不冲突。
#   显式传 bid_chg_max 时仍按该固定值（保留参数化能力，不破坏既有调用方）。
DEFAULT_BID_CHG_MAX = None
BID_CHG_MAX_RATIO = 1.04     # 上界系数：板块涨停幅度 × 本系数（主板 10 × 1.04 = 10.4%）


def _limit_pct(code):
    """板块涨停幅度（%）：主板 10 / 创业科创 20 / 北交所 30"""
    c = str(code)
    if c[:3] in ("300", "301", "688", "689"):
        return 20.0
    if c[:1] in ("8", "4") or c[:3] == "920":
        return 30.0
    return 10.0


def _chg_ceiling(code):
    """该票的竞价涨幅上界（%）= 板块涨停幅度 × 1.04"""
    return _limit_pct(code) * BID_CHG_MAX_RATIO
# 2026-08-31 主人指令: 竞价涨幅下限方案废弃, 改为剔除涨停率(ai_prob) < 50% 的候选(见过滤处)
MIN_PROB = 0.5

# 后端概念库(开盘啦概念映射): 由 concept_refresh 每日采集, 存 code -> 全量概念(board_full)
CONCEPT_DB = "/opt/kuaixuan/kuaixuan.db"


def _concept_map():
    """返回 {code: [全部概念]}。取概念库最近已采集日期的全量概念(board_full); 兼容旧库仅有前N(board)。
    概念本质按日归属, 用最近可用日期作为当日概念; 失败返回空。"""
    try:
        conn = _sqlite3.connect(CONCEPT_DB)
        conn.text_factory = str
        row = conn.execute(
            "SELECT MAX(date) FROM stock_concept WHERE board_full IS NOT NULL AND board_full<>''"
        ).fetchone()
        if not row or not row[0]:
            row = conn.execute("SELECT MAX(date) FROM stock_concept").fetchone()
        if not row or not row[0]:
            conn.close()
            return {}
        d = row[0]
        out = {}
        for code, joined in conn.execute(
                "SELECT code, COALESCE(board_full, board) FROM stock_concept "
                "WHERE date=? AND COALESCE(board_full, board) IS NOT NULL "
                "AND COALESCE(board_full, board)<>''", (d,)):
            parts = [p.strip() for p in str(joined).split("\u3001") if p.strip()]
            if parts:
                out[str(code)] = parts
        conn.close()
        return out
    except Exception:
        return {}


def predict(trade_date=None, mv_min=DEFAULT_MV_MIN, mv_max=DEFAULT_MV_MAX,
            bid_amt_min=DEFAULT_BID_AMT_MIN, bid_chg_max=DEFAULT_BID_CHG_MAX,
            force=False):
    """生成某日预测报告。
    - 保护规则(2026-08-27): 若当日 predictions_{d}.json 已存在且非 force=True，
      主文件(前端展示的那一份)不覆盖；新结果另存 predictions_{d}_rerun.json + _rerun.html
      作为模型/参数对比参照，避免覆盖上午 9:30 竞价结束时的报告。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    d = trade_date or today()

    # === 日期正门 (2026-09-25 第二轮复盘新增) ===
    # 为什么下面的"数据侧护栏"不够:
    #   删掉幽灵 snapshot_bid 行后, 取数链变成
    #     `快选快照空 → 猫爪 screening 空 → **东财兜底**`,
    #   而东财在休市日**仍返回上一交易日的陈旧价格**(price 非 0)
    #   ⇒ `price>0` 占比护栏判不出来(实测 2026-09-25 占比≈100%, 仍产出 9 只假名单)。
    #   backend 调度器的日历门禁只挡自动链路, 手动 `--date <休市日>` 重算仍会漏。
    # ⇒ 预测前先过**日历正门**; 数据护栏保留为第二道防线。日历见 trade_calendar.py。
    # force 只表示"覆盖已存在的报告", **不**表示"假装今天是交易日", 故此处不看 force。
    if not _tc.is_trade_day(d):
        print(f"⚠️ {d} 非交易日(周末/法定休市) → 跳过预测, 不写任何报告")
        return None

    model_path = MODEL_PATH or os.path.join(MODEL_DIR, _DEFAULT_MODEL[ALGO])
    if not os.path.exists(model_path):
        # 优雅降级（2026-09-25）：模型缺失时**不写半成品、不抛异常**，只提示并返回 None，
        # 否则调度器日志会天天刷 ERROR（LGB 首次上线当晚才有模型，9:27 预测可能早于它）。
        print(f"⚠️ {ALGO} 模型不存在，请先运行 {_TRAIN_SCRIPT[ALGO]}: {model_path} → 跳过本次预测")
        return None

    model = _load_model(ALGO, model_path)

    # 取数: 快选快照(权威同源) → 猫爪自拉(collector.fetch_market 已返回特征行) → 东财兜底
    stocks = fetch_from_kuaixuan(d) or fetch_market(d) or to_features(fetch_market_eastmoney(), d)
    df = pd.DataFrame(stocks or [])
    # 2026-09-20 健壮性修复: 取数为空(非交易日无当日 9_25 快照 / 数据源全挂)时
    #   旧代码会在下一行 `df[col]` 直接 KeyError: 'bid_change' 崩栈 —— 空 DF 无列。
    #   这里显式兜底: 无数据就明确提示并返回, 不抛异常(调度器按 180s 超时容错, 但崩栈
    #   会污染日志、且 backfill/定时任务拿不到可读原因)。
    if df.empty:
        print(f"⚠️ {d} 无可用行情数据(非交易日无快照, 或数据源取数失败) → 跳过预测")
        return None
    for col in FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=FEATURES)
    if df.empty:
        print(f"⚠️ {d} 行情数据清洗后为空(关键特征全缺) → 跳过预测")
        return None

    # === 非交易日护栏 · **第二道** (2026-09-25 主人反馈「页面数据不对」) ===
    # 症状: 2026-09-25(中秋节, 休市) 仍生成了 predictions_2026-09-25.json ——
    #   快选快照当天返回的是上一交易日的**复制行**(bid_change/bid_amount 与 09-24 逐位
    #   相同), 而 price / bid_turnover 全为 0; 模型拿到 price=0 这种越界输入后概率被顶到
    #   0.94~0.96, 页面默认(最新)报告显示「30 只、95% 涨停概率」的假名单。
    # 为何现有空壳检测漏了: 它只看 `all.len < 5400`, 而幽灵行有 5561 只 → 判为正常。
    # 根因: aipick_scheduler._is_trade_day() 只判「周一~周五」, **没有节假日日历**。
    #
    # ★ 分层(2026-09-25 第二轮校正):
    #   第一道 = 文件开头的**日历正门**(`_tc.is_trade_day`)—— 权威、免维护、指哪打哪;
    #   第二道 = 本段**数据侧护栏** —— 只看数据特征, 用于兜"日历漏配"和"快照残缺"。
    #   ⚠️ 为什么数据护栏**不能单独当正门**: 休市日若快选与猫爪同时为空, 取数会落到
    #      **东财兜底**, 而东财休市日返回的是上一交易日的陈旧价格(price 非 0) ⇒
    #      本护栏的判据(price>0 占比)会**几乎 100% 通过**, 完全失效。
    #      实测: 清掉幽灵行后 `--date 2026-09-25` 曾据此产出 9 只假名单。
    # 判据口径: 真实交易日 price>0 占比 ≈93%(38 份报告实测最低 87.8%); 幽灵快照为 0%
    #   ⇒ 阈值 50% / 5% 余量充足, 绝不误伤真实交易日(已用全部 38 份报告回归验证)。
    # 硬拦: 价格大面积缺失 ⇒ 不是交易日(或快照未就绪) → 一个文件都不写。
    # 软警: 仅竞价换手率全缺 ⇒ 疑似残缺快照(如 2026-08-10~12 只有 600 只的样本日),
    #   按既有约定仍出报告, 但日志醒目提示, 便于事后识别可信度。
    _price_ok = float((df["price"] > 0).mean())
    _tov_ok = float((df["bid_turnover"] > 0).mean())
    if _price_ok < 0.5:
        print(f"⚠️ {d} price>0 占比仅 {_price_ok * 100:.1f}%(共 {len(df)} 行) → "
              f"判定非交易日/快照未就绪, 跳过预测, 不写任何报告")
        return None
    if _tov_ok < 0.05:
        print(f"⚠️ {d} 竞价换手率(bid_turnover)>0 占比仅 {_tov_ok * 100:.1f}% → "
              f"疑似残缺快照, 报告的换手率维度不可信, 请人工确认后再对外使用")

    X = df[FEATURES].astype(float)
    proba = _predict_proba(model, X, ALGO)
    df["ai_prob"] = np.round(proba, 4)

    # 开盘啦全量概念(供 App 展示: 默认前2 + 悬浮显示全部)
    cmap = _concept_map()

    def _attach(r):
        r = dict(r)
        cl = cmap.get(str(r.get("code", "")))
        r["concepts"] = cl or []
        if not r.get("concept") and cl:
            r["concept"] = "、".join(cl[:2])
        return r

    # 过滤前全量候选(按概率降序), 供 App 自定义规则过滤
    full = df.sort_values("ai_prob", ascending=False)
    all_rows = [_attach(r) for r in full.to_dict(orient="records")]

    # 按(可配置)规则过滤 → 默认结果 top(≤30) 与 HTML
    # ★ 2026-09-26 主人指令: 取消 AI 层市值门槛 —— 默认 mv_min/mv_max 均为 None ⇒ 此处不按市值筛。
    #   市值维度交给前端(App 从 all 自筛 / 历史综合查询 mv_min·mv_max); 训练侧同样无门槛。
    #   显式传值仍生效(保留参数化能力, 不破坏既有调用方)。
    if mv_min is not None:
        df = df[df["circ_mv"] >= mv_min]
    if mv_max is not None:
        df = df[df["circ_mv"] <= mv_max]
    df = df[(df["bid_amount"] >= bid_amt_min)]
    # 2026-10-06 主人要「榜单完整度」：上界**按板块自适应**（见文件头 _chg_ceiling 注释）。
    #   显式传 bid_chg_max 时仍用固定值（保留参数化能力）。
    if bid_chg_max is not None:
        df = df[(df["bid_change"] <= bid_chg_max)]
    else:
        df = df[df["bid_change"] <= df["code"].map(_chg_ceiling)]
    # 2026-08-31 主人指令: 取消竞价涨幅下限过滤, 改为剔除涨停率(ai_prob) < 50% 的候选
    # 2026-10-06 概率校准后语义变更: ai_prob 已是**真实封板率**(全市场均值≈1%)，绝对阈值
    #   0.5 会把全部候选几乎剔光(实测重训后金睛 top 仅剩 2 只)。改为双通道:
    #   · 市场仍有 ≥5 只 ai_prob≥0.5 的高置信候选(极端一字板行情) → 沿用绝对门槛(原主人逻辑)
    #   · 否则 → 保持业务过滤后按概率降序取 Top 30(校准后的正确做法)
    _over05 = float((df["ai_prob"] >= 0.5).sum())
    if _over05 >= 5:
        df = df[(df["ai_prob"] >= 0.5)]
    else:
        df = df.sort_values("ai_prob", ascending=False).head(30)
    result = df.sort_values("ai_prob", ascending=False).head(30)
    result_rows = [_attach(r) for r in result.to_dict(orient="records")]

    payload = {
        "date": d,
        "count": len(result_rows),
        "top": result_rows,
        "all": all_rows,
        # 2026-09-25: 元信息供页面头部展示"这是哪个模型 / 何时训练 / AUC 多少"，
        # 让用户一眼看出 LGB 页不是 XGB 页的重复。老前端读不到该字段也不会报错。
        "meta": _model_meta(ALGO, model_path),
    }
    json_path = os.path.join(OUT_DIR, f"predictions_{d}.json")

    # === 上午版报告保护(2026-08-28 加空壳检测) ===
    existing = os.path.exists(json_path) and os.path.getsize(json_path) > 0
    shell_empty = False  # 是否午夜空壳(半夜 backfill/worker 启动 predict 时 snapshot 未就绪，fallback 东财自拉写入了"昨日收盘涨幅"等假数据)
    if existing and not force:
        try:
            with open(json_path, "r", encoding="utf-8") as _fh:
                _old = json.load(_fh)
            _all_len = len(_old.get("all") or [])
            _top = _old.get("top") or []
            _first = _top[0] if _top else {}
            # 1) all < 5400: 快选 snapshot_bid 正常≈5550 只；fallback 东财自拉≈5209(空壳典型值)
            #    → 或 snapshot 缺失时写的空壳(含 ST/停牌过滤后也应在 5400 左右)
            # 2) 首行 trade_date 与文件 date 不符 → 跨日写串
            # 3) 首行 bid_turnover 为 22.53(前一日 f8 误用历史换手)或 ai_prob 全 None → 坏数据
            if _all_len < 5400:
                shell_empty = True
                print(f"[空壳检测] all.len={_all_len}<5400，判定为午夜空壳/旧 fallback 数据，允许覆盖")
            elif _first.get("trade_date") and _first.get("trade_date") != d:
                shell_empty = True
                print(f"[空壳检测] 首行 trade_date={_first.get('trade_date')} != 文件日期={d}，判定为空壳，允许覆盖")
            else:
                # 用"最新跑出来的前 5 只 top 代码对比主文件 top5 代码": 完全一致概率极低 → 若 5/5 完全相同，可能是主文件正常（跳过空壳误判保护）
                _old_top5 = set()
                for _r in _top[:5]:
                    _old_top5.add(str(_r.get("code")))
                _new_top5 = set()
                for _r in (result_rows or [])[:5]:
                    _new_top5.add(str(_r.get("code")))
                if len(_old_top5) >= 3 and len(_new_top5) >= 3 and len(_old_top5 & _new_top5) == 0 and len(payload.get("all") or []) > len(_old.get("all") or []):
                    # 前5只代码完全不重 & 本次 all 数明显 > 主文件 all → 本次是快照全量版本，主文件是 fallback 空壳 → 覆盖
                    shell_empty = True
                    print(f"[空壳检测] top5 代码完全错位 + all.len {len(_old.get('all') or [])}→{len(payload.get('all') or [])}，判定为午夜空壳，允许覆盖")
        except Exception as _e:
            print(f"[空壳检测] 异常(按安全跳过覆盖): {_e}")
    write_main = force or (not existing) or shell_empty
    # 下午重跑版无论如何都写到 _rerun 文件，做历史对照
    rerun_json = os.path.join(OUT_DIR, f"predictions_{d}_rerun.json")
    rerun_html = os.path.join(OUT_DIR, f"predictions_{d}_rerun.html")

    def _dump_payload(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, 0o644)
        except Exception:
            pass

    # 始终写一份 rerun 对照(含模型哈希、时间戳等元信息可选)
    try:
        _dump_payload(rerun_json)
        print(f"[rerun] 对照版 → {rerun_json} (主文件写={write_main}, force={force}, existing={existing}, shell_empty={shell_empty})")
    except Exception as e:
        print(f"⚠️ 写入 rerun 对照失败: {e}")

    # 主文件按规则决定是否覆盖:
    #   - 上午 9:27 首次 → 写 (existing=False)
    #   - 午夜空壳(00:00~9:24 之间误写) → 允许覆盖 (shell_empty=True)
    #   - 19:00/其他时段正常二次重跑 → 不覆盖 (force=False 且主文件正常)
    if write_main:
        _dump_payload(json_path)
        gen_html(result, d)
        print(f"预测完成: {len(result)} 只 → {json_path} (全量候选 {len(all_rows)} 只)")
    else:
        # 只写 _rerun.html 主 HTML 不动
        _gen_html(result, d, rerun_html)
        print(f"[保护] 主文件已存在，跳过覆盖 {json_path}；新结果仅保留对照版 _rerun.*")

    return result


def _gen_html(df, d, path=None):
    """生成预测 HTML。path=None 时写入 predictions_{d}.html 并同步 latest.html。"""
    rows = ""
    for i, (_, r) in enumerate(df.iterrows(), 1):
        prob = r["ai_prob"] * 100
        bg = f"rgba(200,40,40,{0.08 + prob/100*0.35:.2f})"
        rows += f"""<tr style="background:{bg}">
<td>{i}</td><td class="code">{r['code']}</td><td><b>{r['name']}</b></td>
<td class="prob">{prob:.1f}%</td>
<td>{r['bid_change']:+.2f}%</td>
<td>{r['bid_amount']:.0f}万</td>
<td>{r['circ_mv']:.1f}亿</td>
<td>{r['bid_turnover']:.2f}%</td>
</tr>"""

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>AI竞价预测 {d}</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#0f1219;color:#eef2ff;margin:0;padding:24px}}
h1{{font-size:22px;margin:0}} .sub{{color:#8899bb;font-size:13px;margin:8px 0 20px}}
.card{{background:#181c28;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #2a3040}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#2a3040;color:#ffbcbc;padding:9px;text-align:left;position:sticky;top:0}}
td{{padding:8px 9px;border-bottom:1px solid #242a38}}
.code{{font-family:Consolas,monospace;color:#ffd700}}
.prob{{font-weight:700;font-size:15px;color:#ff6b5e}}
.note{{color:#7788aa;font-size:12px;line-height:1.8;margin-top:14px}}
.tag{{display:inline-block;background:#c0392b;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;margin-left:10px}}
.warn{{background:#3d2a10;border:1px solid #a07020;border-radius:10px;padding:14px 18px;font-size:12px;color:#e0b060;margin-bottom:16px;line-height:1.8}}
</style></head><body>
<div class="card"><h1>AI 竞价选股 · 涨停概率预测 <span class="tag">{d}</span></h1>
<div class="sub">模型：{_MODEL_LABEL[ALGO]} · 预测当日涨停概率 · 默认过滤（竞价金额≥{DEFAULT_BID_AMT_MIN}万 / 竞价涨幅≤该板块涨停幅度×{BID_CHG_MAX_RATIO}（主板 {10 * BID_CHG_MAX_RATIO:.1f}%）/ 涨停率≥{MIN_PROB*100:.0f}%）· 市值不设门槛（由前端自筛）· 供研究参考</div>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>AI涨停概率</th><th>竞价涨幅</th><th>竞价金额</th><th title="自由流通市值（2026-09-26 口径统一）">市值(亿)</th><th>换手率</th></tr></thead>
<tbody>{rows}</tbody></table>
</div>
<div class="warn">⚠️ 免责声明：AI 预测基于历史统计规律，不构成投资建议。竞价打板风险极高，请严格控制仓位。模型每周自动重训练，数据积累越多预测越准。</div>
</body></html>"""
    if path is None:
        path = os.path.join(OUT_DIR, f"predictions_{d}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        os.chmod(path, 0o644)
    except Exception:
        pass
    # 主输出时同时刷新 latest.html
    if path == os.path.join(OUT_DIR, f"predictions_{d}.html"):
        try:
            with open(os.path.join(OUT_DIR, "latest.html"), "w", encoding="utf-8") as f:
                f.write(html)
            try:
                os.chmod(os.path.join(OUT_DIR, "latest.html"), 0o644)
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ latest.html 写入失败: {e}")
    print(f"预测报告已生成: {path}")


def gen_html(df, d):
    """兼容旧调用: 写到 predictions_{d}.html 并刷新 latest.html"""
    _gen_html(df, d, None)


def backfill(days=30):
    """补全历史预测报告 (2026-08-30 增强: 按交易日历扫描, 解决"当天没跑9:27预测→无法回看")
    ======================================================================================
    原实现只遍历已存在的 predictions_*.json, 若某天 9:27 预测任务未执行(平台没开/脚本异常),
    当天连文件都不存在 → 历史回看缺失。
    本版: 从快选 snapshot_bid 表取最近 days 个有 9_25 快照的交易日,
         凡 predictions_{d}.json 缺失 → predict(d, force=True) 直接补生成;
         已存在空壳 → predict(d) 内部空壳检测覆盖; 已存在正常 → 仅补概念字段(不重算)。
    9_25 快照是权威采集, 只要快选系统正常, 事后任何时间都能补生成, 保证历史回看完整。
    保护: 当日已生成且正常的报告不覆盖(保留 9:27 上午版), 缺失/空壳才补。
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    today_str = today()
    cmap = _concept_map()

    ARRAY_KEYS = ("top", "all", "result", "rows")

    def _inject_concepts_only(jp):
        """只注入 concepts/concept 两字段，其他不动(避免复算导致 ai_prob 漂移)"""
        try:
            with open(jp, "r", encoding="utf-8") as f:
                cur = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读取失败: {e}", flush=True)
            return
        changed = False

        def _att(r):
            """原地修改 r，返回是否做了改动"""
            if not isinstance(r, dict):
                return False
            cl = cmap.get(str(r.get("code", "")))
            had = isinstance(r.get("concepts"), list) and r["concepts"]
            if not had:
                r["concepts"] = cl or []
                if not r.get("concept") and cl:
                    r["concept"] = "、".join(cl[:2])
                return bool(cl) or "concepts" not in r
            return False

        for k in ARRAY_KEYS:
            arr = cur.get(k)
            if isinstance(arr, list) and arr:
                for r in arr:
                    if _att(r):
                        changed = True
        if changed:
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=None, separators=(",", ":"))
            print(f"  [概念注入] → {os.path.basename(jp)}", flush=True)
        else:
            print(f"跳过 {os.path.basename(jp)}: 概念/数据齐全", flush=True)

    # 交易日历: 从快照库取最近 days 个有 9_25 快照的交易日(降序)
    try:
        conn = _sqlite3.connect(CONCEPT_DB)
        rows = conn.execute(
            "SELECT DISTINCT date FROM snapshot_bid WHERE time_point='9_25' "
            "ORDER BY date DESC LIMIT ?", (int(days),)).fetchall()
        conn.close()
        dates = [str(r[0]) for r in rows if r[0]]
        dates = [d for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    except Exception as e:
        print(f"⚠️ 读取快照交易日历失败: {e}", flush=True)
        dates = []

    if not dates:
        print("没有可补的交易日(快照库无 9_25 数据)", flush=True)
        return

    print(f"[backfill] 最近 {len(dates)} 个交易日: {dates}", flush=True)
    n_gen, n_skip, n_fail = 0, 0, 0
    for d in dates:
        jp = os.path.join(OUT_DIR, f"predictions_{d}.json")
        exists = os.path.isfile(jp) and os.path.getsize(jp) > 0
        if not exists:
            # 文件不存在 → 直接补生成(当天/历史都行, 快照同源)
            print(f"[补生成] {d} 报告缺失, 用 9_25 快照补生成...", flush=True)
            try:
                predict(d, force=True)
                # 2026-09-25: predict 返回 None = 非交易日/数据无效(一个文件都没写)
                #   → 不能计入"已补生成", 否则日志谎报成功、把非交易日掩盖过去。
                if os.path.isfile(jp) and os.path.getsize(jp) > 0:
                    n_gen += 1
                else:
                    print(f"  [跳过] {d}: 无有效数据(非交易日/快照缺失), 未生成报告", flush=True)
                    n_fail += 1
            except Exception as e:
                print(f"  ⚠️ {d} 补生成失败: {e}", flush=True)
                n_fail += 1
            continue
        # 文件已存在 → 一律保留(2026-08-30 修正: 不 force 覆盖, 防止误伤历史快照残缺日
        # 如 08-10/11/12 只有 600 只样本的报告)。仅当文件损坏(JSON 解析失败且无任何 top)才重算。
        try:
            with open(jp, "r", encoding="utf-8") as f:
                cur = json.load(f)
            top_ok = isinstance(cur.get("top"), list) and len(cur.get("top") or []) > 0
            if not top_ok:
                # top 完全为空且文件陈旧 → 可能是坏数据, 但保守起见仍保留(不覆盖)
                print(f"[保留] {d}: 报告存在但 top 为空(快照残缺日?), 保留原样仅补概念", flush=True)
        except Exception as e:
            print(f"  ⚠️ {d}: 报告 JSON 损坏({e}), 保留原样", flush=True)
        # 仅补概念字段, 不重算
        _inject_concepts_only(jp)
        n_skip += 1

    print(f"[backfill] 完成: 补生成 {n_gen} / 跳过 {n_skip} / 失败 {n_fail}", flush=True)


if __name__ == "__main__":
    _argv = sys.argv[1:]
    if _argv and _argv[0] == "backfill":
        # 兼容旧调用 `predict_daily.py backfill 30`（位置参数），并支持
        # `predict_daily.py backfill 30 --algo lgbm --out-dir ../output/lgb`。
        _args, _rest = _build_parser().parse_known_args(_argv[1:])
        _configure(_args)
        backfill(int(_rest[0]) if _rest and str(_rest[0]).isdigit() else 30)
    else:
        _a = _build_parser().parse_args(_argv)
        _configure(_a)
        if ALGO != "xgb" or OUT_DIR != os.path.normpath(os.path.join(SCRIPT_DIR, "..", "output")):
            print(f"[配置] algo={ALGO} out_dir={OUT_DIR} force={_a.force}")
        predict(trade_date=_a.date, force=_a.force)