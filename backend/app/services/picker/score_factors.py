# -*- coding: utf-8 -*-
"""
评分分档与取整工具 (重构 P2)
=================================================================================
从 scorer 抽出的**纯函数**部分(分档打分 / default 分 / JS 语义取整), 让 picker
评分层可独立复用同一份分档表, 又不必 import 整个 scorer(避免评分层与老链路
耦合、便于单测注入 cfg)。

行为与 scorer.get_factor_score / _factor_default / js_round **逐行等价**,
任何改动必须同步两边(scorer 侧仍是其它调用方的权威实现)。
"""
import math
from typing import Any, Optional


def factor_score(cfg: dict, factor: str, value: float) -> float:
    """按配置分档表打分: 命中 [下限, 上限) 返回得分, 未命中返回 default。

    与 scorer.get_factor_score 等价(含非法 bucket 行的 skip 语义)。
    """
    f = (cfg.get("factors") or {}).get(factor)
    if not f or not f.get("buckets"):
        return 0.1
    for b in f["buckets"]:
        try:
            lo, hi, sc = float(b[0]), float(b[1]), float(b[2])
        except (TypeError, ValueError, IndexError):
            continue
        if lo <= value < hi:
            return sc
    return factor_default(cfg, factor)


def factor_default(cfg: dict, key: str, dflt: float = 0.1) -> float:
    """某因子**数据缺失**时的中性分。

    契约铁律1: 缺失不得填 0 —— 0 会落进 e.g. yesterday 的 ["0","1"] 桶拿 0.4 分,
    等于凭空给"昨日涨幅未知"的票打了个"昨日微涨"的分; market 的 ["0","30"] 桶
    更是直接给满分 1.0(市值缺失 = 当成超小盘最优)。
    """
    f = (cfg.get("factors") or {}).get(key) or {}
    try:
        return float(f.get("default", dflt))
    except (TypeError, ValueError):
        return dflt


def js_round(x: float) -> int:
    """复刻 JS Math.round 语义(正数): 与老 scorer.js_round 一致"""
    return int(math.floor(x + 0.5))


def clamp_prob(base01: float) -> float:
    """概率钳制 [5, 95]"""
    return max(5.0, min(95.0, base01 * 100))
