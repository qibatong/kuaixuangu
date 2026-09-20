# -*- coding: utf-8 -*-
"""
AI 预测概率层 (2026-09-20 新增 · aipick 融合)
=================================================================================
把 aipick 的 XGBoost 涨停概率模型(AUC 0.9279 / 回测 41 天 +19.38%)接入主评分
**异动分(w_warn, 17%) 第三层**。aipick 自身(独立报告/18:59 重训)不受影响。

主人拍板(2026-09-20)
--------------------------------------------------------------------------------
* **定格即含**: 评分计算时对全市场推理(6 特征全部来自竞价定格数据), 9:25:5x
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
6 特征铁律: bid_change / bid_amount(万元) / bid_turnover(自由流通口径) /
circ_mv(流通市值·亿) / yesterday_chg(前一交易日) / price。

降级语义(契约铁律)
--------------------------------------------------------------------------------
模型文件缺失 / collector 异常 / 推理失败 / 当日无快照 → 全市场 map 为空 →
AI 层整体走 ai_default —— 量比/净额两层完全不受影响(独立降级)。

模型热更新
--------------------------------------------------------------------------------
aipick 每日 18:59 重训覆盖 model_xgb.json, 按 mtime 检测自动换用新模型,
无需重启服务。读到半截文件的容错: load_model 异常 → 返回 None(降级)。
"""
import logging
import os
import sys
import threading
from typing import Dict, Optional, Sequence

log = logging.getLogger(__name__)

MODEL_PATH = "/opt/kuaixuan/aipick/models/model_xgb.json"
AIPICK_SCRIPTS = "/opt/kuaixuan/aipick/scripts"
FEATURES = ["bid_change", "bid_amount", "bid_turnover", "circ_mv",
            "yesterday_chg", "price"]          # ★ 必须与 train_model.py 逐字一致

_lock = threading.Lock()
_model_cache: Dict = {"mtime": None, "model": None}
_prob_cache: Dict[str, Dict[str, float]] = {}   # date -> 全市场 {code: prob}
_broken: set = set()                            # 当日失败标记(防重复尝试刷日志)


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
                _model_cache["mtime"], _model_cache["model"] = mt, model
                log.info("[AI预测] 模型已加载 %s", MODEL_PATH)
            except Exception as e:                              # noqa: BLE001
                log.warning("[AI预测] 模型加载失败(层降级) err=%s", str(e)[:150])
                return None
        return _model_cache["model"]


def _market_prob_map(date: str) -> Dict[str, float]:
    """全市场 {code: 涨停概率}。当日缓存一次推理; 任何失败 → {} (层降级)。"""
    date = str(date)
    with _lock:
        if date in _broken:
            return {}
        cached = _prob_cache.get(date)
    if cached is not None:
        return cached
    model = _load_model()
    if model is None:
        with _lock:
            _broken.add(date)
        return {}
    try:
        if AIPICK_SCRIPTS not in sys.path:
            sys.path.insert(0, AIPICK_SCRIPTS)
        import collector                       # aipick 同源取数(见模块 docstring)
        rows = collector.fetch_from_kuaixuan(date)
        if not rows:
            log.info("[AI预测] %s 无快照数据(非交易日?) → AI 层降级", date)
            with _lock:
                _broken.add(date)
            return {}
        import pandas as pd
        df = pd.DataFrame(rows)
        for c in FEATURES:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=FEATURES)
        if df.empty:
            with _lock:
                _broken.add(date)
            return {}
        proba = model.predict_proba(df[FEATURES].astype(float))[:, 1]
        out = {str(code): float(p) for code, p in zip(df["code"], proba)}
        with _lock:
            _prob_cache[date] = out
        log.info("[AI预测] %s 全市场推理完成 %d 只", date, len(out))
        return out
    except Exception as e:                                      # noqa: BLE001
        log.warning("[AI预测] 推理失败(层降级) err=%s", str(e)[:200])
        with _lock:
            _broken.add(date)
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
