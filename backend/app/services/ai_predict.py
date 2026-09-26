# -*- coding: utf-8 -*-
"""
AI 预测概率层 (2026-09-20 新增 · aipick 融合)
=================================================================================
把 aipick 的 XGBoost 涨停概率模型(AUC 0.9279 / 回测 41 天 +19.38%)接入主评分
**异动分(w_warn, 17%) 第三层**。aipick 自身(独立报告/18:59 重训)不受影响。

主人拍板(2026-09-20)
--------------------------------------------------------------------------------
* **定格即含**: 评分计算时对全市场推理(6 特征全部来自竞价定格数据), 9:26:3x
  评分直接带 AI 分 —— 不用等 aipick 9:27 的独立预测进程。
* **标准 = AI 全市场榜 Top30 ∩ p≥0.80 三档**(实测 12 交易日分布: p≥0.8 日均
  25~40 只, 极端日 63; p≥0.5 有 50~90 只 —— 门槛定 0.5 会大面积加分, 无区分度):
    p≥0.90 → 1.0 / p≥0.85 → 0.85 / p≥0.80 → 0.70; 低于门槛/不在榜 → 不进
    返回 dict(调用方走 ai_default 0.35 中性, 与净额层同哲学: 不当惩罚)。
* 子权重: 量比 0.45 / 净额 0.30 / AI 0.25(草案, 跑几天校准)。

同源零漂移(关键设计)
--------------------------------------------------------------------------------
特征构造**直接 import aipick/scripts/collector.py 的 fetch_from_kuaixuan** ——
与每日预测报告同一份代码同一份数据:
  快选 snapshot_bid(9_25, 自身库零外调) + 猫爪 screening 补 price/竞价换手/昨涨
  (评分时点与主采集共享 30s call_cached 缓存, ≈零额外用量)。
aipick 训练侧若改特征口径, 这里自动跟随, 永不失配。
5 特征铁律: bid_change / bid_amount(万元) / bid_turnover(竞价换手率·自由流通=auc_turnover) /
circ_mv(流通市值·亿) / price。
(2026-09-25 起由 6 特征降为 5 特征: 移除 `yesterday_chg` —— 实时链路里它是竞价涨幅、与
 bid_change 同信息, 历史基座里却是前一交易日涨幅, 同名两义; 详见 FEATURES 处注释。)

降级语义(契约铁律)
--------------------------------------------------------------------------------
模型文件缺失 / collector 异常 / 推理失败 / 当日无快照 → 全市场 map 为空 →
AI 层整体走 ai_default —— 量比/净额两层完全不受影响(独立降级)。

🔴 2026-09-25 加装「降级可见性」(计划第一层第3点)
--------------------------------------------------------------------------------
**降级策略本身不动**(仍不中断服务、仍走 ai_default)，但降级必须**被看见**：
  ① 所有降级点统一走 `_mark_broken()`，日志级别 **warning → ERROR**，并带 `reason` 码；
  ② 进程内计数指标 `_stats`（累计降级次数 / 按 reason 分布 / 逐日明细），
     由 `degrade_stats()` 导出，可挂到健康检查接口或运维脚本；
  ③ **连续 N 个交易日降级**（默认 3，见 `CONSECUTIVE_DAYS_ALERT`）→ 走 `notify.send_text`
     推送一次（当日去重，不刷屏）；单日反复失败不推。
  ④ **入库前宽度断言**：`predict_proba` 之前校验「模型期望特征宽度 == FEATURES 长度」，
     不符则按 `WIDTH_MISMATCH` 记 ERROR + 指标 + 告警，**不再静默 fail-open**。
     —— 这正是 MEMORY 铁律 15 / 风险 E-2 描述的失败模式（5 维化只推一半时的静默降级）。
     ⚠️ 刻意**不抛异常**：抛异常会让每次评分请求 500，违背"服务不中断"的既有契约；
        这里用「ERROR + 指标 + 告警」达到同等可见性，且当日只探测一次(记入 `_broken`)。

模型热更新
--------------------------------------------------------------------------------
aipick 每日 18:59 重训覆盖 model_xgb.json, 按 mtime 检测自动换用新模型,
无需重启服务。读到半截文件的容错: load_model 异常 → 返回 None(降级)。
"""
import logging
import os
import sys
import threading
import time
from datetime import date as _date, timedelta as _timedelta
from typing import Dict, Optional, Sequence

log = logging.getLogger(__name__)

MODEL_PATH = "/opt/kuaixuan/aipick/models/model_xgb.json"
AIPICK_SCRIPTS = "/opt/kuaixuan/aipick/scripts"
FEATURES = ["bid_change", "bid_amount", "bid_turnover", "circ_mv",
            "price"]          # ★ 必须与 train_model.py 逐字一致
# ★ 2026-09-25：移除 `yesterday_chg`（6 维 → 5 维）。
#   线上 `collector.fetch_from_kuaixuan` 实时模式喂的 `yesterday_chg` 实为**当日竞价涨幅**
#   （9:25 集合竞价已撮合出开盘价，此刻唯一价格就是开盘价 ⇒ 读到的 pct_chg ≡ 竞价涨幅），
#   与 `bid_change` 同信息；而历史基座里那一列是**前一交易日涨幅** ⇒ 同名两义。
#   500 天样本外代价 −0.0015 池化AUC；换掉的是「同名两义 + 换基座必 skew」两个结构性风险。
#   ⚠️ 本列表与 `/opt/kuaixuan/aipick/scripts/*.py` 的 FEATURES、以及线上模型文件**必须同批发布**：
#     若只改脚本，18:59 重训会产出 5 维模型，而本文件仍按 6 维喂 → `predict_proba` 失败
#     → `_market_prob_map` 返回 {} → AI 层 fail-open 静默走 ai_default（**不报错、不告警**）。
#     ⇒ 2026-09-25 起上面的 ④ 宽度断言会把这种失配打成 ERROR + 告警，不再静默。

# ---------------------------------------------------------------- 降级可见性(2026-09-25)
CONSECUTIVE_DAYS_ALERT = 3      # 连续这么多**个交易日**都整层降级 → 推送告警
MAX_STAT_DAYS = 60              # 逐日明细只留最近 N 天(防长跑内存无界)

_lock = threading.Lock()
_model_cache: Dict = {"mtime": None, "model": None, "width": None}
_prob_cache: Dict[str, Dict[str, float]] = {}   # date -> 全市场 {code: prob}
_broken: set = set()                            # 当日失败标记(防重复尝试刷日志)

# 降级计数指标: 可被 health / 运维脚本读取(见 degrade_stats)
_stats: Dict = {
    "total": 0,                     # 累计降级次数
    "by_reason": {},                # reason -> 次数
    "by_date": {},                  # "YYYY-MM-DD" -> reason(当日最后一次)
    "recent_dates": [],             # 最近 MAX_STAT_DAYS 个降级过的交易日(升序去重, 用于展示)
    "run_dates": [],                # ★ 尾部**严格连续**的降级日序列(逐日 +1), 用于连续判定
    "last_reason": None,
    "last_detail": None,
    "last_ts": None,
    "alerts_sent": 0,
    "last_alert_date": None,
}

REASON_LABEL = {
    "MODEL_MISSING": "模型文件缺失/加载失败",
    "NO_SNAPSHOT": "当日无 9_25 快照(非交易日 / 定格未落库)",
    "EMPTY_FEATURES": "取到的特征全为空(清洗后 0 行)",
    "WIDTH_MISMATCH": "特征宽度与模型期望不符(★ 只推了一半的模型/脚本)",
    "INFER_FAILED": "推理抛异常(取数/列名/依赖)",
    "STRICT_WIDTH_SKIP": "宽度断言失败后当日不再重试",
}


def _mark_broken(date: str, reason: str, detail: str = "") -> None:
    """统一的降级落点: 计指标 + ERROR 日志 + 连续 N 日告警。**不抛异常**。

    Args:
        date:   交易日(字符串)。
        reason: REASON_LABEL 里的码。
        detail: 附加信息(异常截断串 / 宽度对比等), 会进日志与指标。
    """
    reason = str(reason)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    should_alert = False
    with _lock:
        _broken.add(str(date))
        _stats["total"] += 1
        _stats["by_reason"][reason] = _stats["by_reason"].get(reason, 0) + 1
        _stats["by_date"][str(date)] = reason
        ds = _stats["by_date"]
        if len(ds) > MAX_STAT_DAYS * 2:               # 防长跑无界
            for k in sorted(ds)[:-MAX_STAT_DAYS]:
                ds.pop(k, None)
        rd = _stats["recent_dates"]
        if str(date) not in rd:
            rd.append(str(date))
            rd.sort()
            del rd[:-MAX_STAT_DAYS]
        _stats["last_reason"] = reason
        _stats["last_detail"] = detail
        _stats["last_ts"] = now
        # ---- 连续判定: 只维护**尾部严格连续**的那一段 ----
        # 判据刻意用「自然日 +1」而不是「上一交易日」: 本层降级本就只发生在交易日,
        # 若中间夹了周末, 说明中间那天没有评分请求(或没降级) ⇒ 不算"连续"。
        # (宁可漏报也不误报 —— 误报会把告警变成噪音, 最终没人看。)
        run = _stats["run_dates"]
        try:
            cur = _date(*[int(x) for x in str(date).split("-")])
            if run and run[-1] == str(date):
                pass                                           # 同日重复: 不动连续段
            elif run and _date(*[int(x) for x in run[-1].split("-")]) == cur - _timedelta(days=1):
                run.append(str(date))
            else:
                run[:] = [str(date)]
        except ValueError:
            run[:] = [str(date)]
        del run[:-CONSECUTIVE_DAYS_ALERT * 2]                 # 只留尾部两轮阈值
        n = len(run)
        # 在第 3、6、9… 个连续降级日各推一次(阈值整数倍), 不刷屏也不"推一次就不管了"
        if n >= CONSECUTIVE_DAYS_ALERT and n % CONSECUTIVE_DAYS_ALERT == 0 \
                and _stats["last_alert_date"] != run[-1]:
            should_alert = True
            _stats["last_alert_date"] = run[-1]
            _stats["alerts_sent"] += 1

    log.error("[AI预测] AI 层降级 date=%s reason=%s(%s) detail=%s 累计降级=%d 连续降级=%d 日",
              date, reason, REASON_LABEL.get(reason, "?"), detail or "-",
              _stats["total"], len(_stats["run_dates"]))

    if should_alert:
        try:
            from . import notify
            notify.send_text(
                "🔴 快选·AI 预测层已连续 %d 个交易日降级（%s）。\n"
                "含义：异动分的 AI 档位全部走中性默认值 %.2f，AI 层对评分**零贡献**，"
                "页面/名单不会报错、看不出异常。\n"
                "最近原因：%s（%s）\n"
                "排查：① 模型文件是否存在且维度与 ai_predict.FEATURES 一致；"
                "② 9:25 定格是否落库；③ 猫爪 screening 取数是否正常。"
                % (CONSECUTIVE_DAYS_ALERT, "、".join(_stats["run_dates"][-CONSECUTIVE_DAYS_ALERT:]),
                   0.35, reason, REASON_LABEL.get(reason, "?")),
                title="快选·AI预测层连续降级")
        except Exception as e:                                  # noqa: BLE001
            log.error("[AI预测] 连续降级告警推送失败 err=%s", e)


def degrade_stats() -> Dict:
    """降级指标快照(供 health 接口 / 运维脚本读取)。

    `consecutive_days` = 尾部**严格连续**(自然日逐日 +1)的降级日数；
    `recent_dates`     = 最近降级过的交易日(可能有间断, 仅供展示)。
    """
    with _lock:
        return {
            "total": _stats["total"],
            "by_reason": dict(_stats["by_reason"]),
            "consecutive_days": len(_stats["run_dates"]),
            "consecutive_dates": list(_stats["run_dates"]),
            "recent_dates": list(_stats["recent_dates"]),
            "last_reason": _stats["last_reason"],
            "last_detail": _stats["last_detail"],
            "last_ts": _stats["last_ts"],
            "alerts_sent": _stats["alerts_sent"],
            "threshold": CONSECUTIVE_DAYS_ALERT,
            "features": list(FEATURES),
        }


def _model_width(model) -> Optional[int]:
    """模型期望的特征宽度; 读不到 → None(不做断言, 保持旧行为)。"""
    try:
        booster = model.get_booster()
        names = booster.feature_names
        return len(names) if names else int(booster.num_features())
    except Exception:                                           # noqa: BLE001
        try:
            return int(model.n_features_in_)
        except Exception:                                       # noqa: BLE001
            return None


def _load_model():
    """懒加载 + mtime 热更新。文件缺失/加载失败 → None(调用方降级)。"""
    try:
        mt = os.path.getmtime(MODEL_PATH)
    except OSError:
        return None
    with _lock:
        if _model_cache["model"] is None or mt != _model_cache["mtime"]:
            try:
                import xgboost as xgb
                model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
                model.load_model(MODEL_PATH)
                width = _model_width(model)
                # ★ ④ 宽度断言: 只推了一半(脚本 5 维 / 模型 6 维, 或反之)是最危险的静默失效
                if width is not None and width != len(FEATURES):
                    _model_cache["mtime"], _model_cache["model"] = mt, model
                    _model_cache["width"] = width
                    log.error("[AI预测] ★ 模型宽度失配: 模型 %d 维 vs FEATURES %d 维 %s "
                              "→ 本层当日降级(WIDTH_MISMATCH)。脚本与模型必须同批发布!",
                              width, len(FEATURES), FEATURES)
                    return None
                _model_cache["mtime"], _model_cache["model"] = mt, model
                _model_cache["width"] = width
                log.info("[AI预测] 模型已加载 %s (宽度 %s)", MODEL_PATH, width)
            except Exception as e:                              # noqa: BLE001
                log.error("[AI预测] 模型加载失败(层降级) err=%s", str(e)[:150])
                return None
        return _model_cache["model"]


def _market_prob_map(date: str) -> Dict[str, float]:
    """全市场 {code: 涨停概率}。当日缓存一次推理; 任何失败 → {} (层降级)。

    2026-09-25: 每个失败出口都带 reason 走 `_mark_broken()`(ERROR + 指标 + 连续告警)。
    """
    date = str(date)
    with _lock:
        if date in _broken:
            return {}
        cached = _prob_cache.get(date)
    if cached is not None:
        return cached
    model = _load_model()
    if model is None:
        w = _model_cache.get("width")
        if w is not None and w != len(FEATURES):
            _mark_broken(date, "WIDTH_MISMATCH",
                         "模型 %d 维 vs FEATURES %d 维 %s" % (w, len(FEATURES), FEATURES))
        else:
            _mark_broken(date, "MODEL_MISSING", MODEL_PATH)
        return {}
    try:
        # ★ ④ 入库前宽度断言(再做一次, 防「模型加载后 FEATURES 被热改」的极端情形)
        w = _model_width(model)
        if w is not None and w != len(FEATURES):
            _mark_broken(date, "WIDTH_MISMATCH",
                         "推理前复核: 模型 %d 维 vs FEATURES %d 维" % (w, len(FEATURES)))
            return {}
        if AIPICK_SCRIPTS not in sys.path:
            sys.path.insert(0, AIPICK_SCRIPTS)
        import collector                       # aipick 同源取数(见模块 docstring)
        rows = collector.fetch_from_kuaixuan(date)
        if not rows:
            _mark_broken(date, "NO_SNAPSHOT", "fetch_from_kuaixuan 返回空")
            return {}
        import pandas as pd
        df = pd.DataFrame(rows)
        for c in FEATURES:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=FEATURES)
        if df.empty:
            _mark_broken(date, "EMPTY_FEATURES", "清洗后 0 行")
            return {}
        proba = model.predict_proba(df[FEATURES].astype(float))[:, 1]
        out = {str(code): float(p) for code, p in zip(df["code"], proba)}
        with _lock:
            _prob_cache[date] = out
        log.info("[AI预测] %s 全市场推理完成 %d 只", date, len(out))
        return out
    except Exception as e:                                      # noqa: BLE001
        _mark_broken(date, "INFER_FAILED", str(e)[:200])
        return {}


def ai_score_map(codes, date, topn: int = 30, buckets=None) -> Dict[str, float]:
    """主人拍板标准: **AI 全市场榜 TopN ∩ p≥0.80 三档** → {code: 档位分}。

    * 全市场按概率降序取前 topn(封顶, 防极端日大面积加分);
    * 档位由 buckets 配置(scorer bid_strength.ai_buckets), 低于所有档位下限
      → 不进返回 dict(语义: 不在 AI 榜, 调用方走 ai_default);
    * codes 只做交集裁剪(只返回调用方需要的)。
    """
    full = _market_prob_map(date)
    if not full:
        return {}
    try:
        topn = max(1, int(topn))
    except (TypeError, ValueError):
        topn = 30
    want = {str(c) for c in (codes or [])}
    out: Dict[str, float] = {}
    for code, p in sorted(full.items(), key=lambda kv: kv[1], reverse=True)[:topn]:
        if code not in want:
            continue
        sc = _bucket(p, buckets)
        if sc is not None:
            out[code] = sc
    return out


def _bucket(p: float, buckets) -> Optional[float]:
    """命中档位返回档位分; 低于所有档位下限 → None(=不在 AI 榜)。"""
    for b in buckets or []:
        try:
            lo, hi, sc = float(b[0]), float(b[1]), float(b[2])
        except (TypeError, ValueError, IndexError):
            continue
        if lo <= p < hi:
            return sc
    return None
