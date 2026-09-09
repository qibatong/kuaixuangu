# -*- coding: utf-8 -*-
"""
竞价强度信号 (2026-09-08 新增)
=================================================================================
替代 **已失活的 f630 异动等级** 因子(权重 17%)。

为什么必须换
--------------------------------------------------------------------------------
f630 只有东财点查(ulist/clist)才返回真实异动等级。腾讯兜底行与快照行转 raw 时
f630 **恒填 0**(fetcher.py 构造兜底行处硬编码)。于是只要东财点查不通, 全市场
warn_type=0 → 落 default 0.18 → 17% × 0.82 = **13.9 分凭空蒸发**, 概率天花板从
99.4 掉到 85.5(实测 2026-09-08 批次 #1585 全部 39 只 warn_type=0)。

三层信号(全部**对东财免疫**)
--------------------------------------------------------------------------------
① 抢筹名单   —— 开盘啦 kpl `fetch_bid_qiangcang`: list20(竞价净额/自由流通市值,
                 开盘啦自家"抢筹强度" qcDelta, 连续值) + listLast(9_24→9_25 最后一秒)
② 竞价量比   —— **快照表自算**: 今日 9:25 竞价额 ÷ 昨日 9:25 竞价额
③ 涨幅加速度 —— **快照表自算**: 9:25 涨幅 − 9:24 涨幅

两个实测踩到的坑(不可回退)
--------------------------------------------------------------------------------
* 加速度**必须**以 9_24 为基准, 不能用 9_20: 9:20 前的挂单可撤, 用 9_20 会算出
  +19.7 的假信号(科创/创业 20% 跌停价试盘, 9:25 回到 0%)。9:20-9:25 不可撤,
  9_24→9_25 极值仅在 ±8 以内, 分布健康(22% 拉升 / 12% 跳水 / 66% 平稳)。
* 竞价量比**必须**过滤昨日竞价额 < 100 万: 昨额 1 万 → 量比 302 倍, 严重失真。
  过滤后覆盖从 5139 只降到 1803 只, 但中位数 0.56、≥3倍占 5%, 区分度反而更好。

降级语义(契约铁律)
--------------------------------------------------------------------------------
**每层独立降级**: 任一层缺失只走该层 default, 不是整个因子归零。这直接解决
f630 那种"一个字段挂掉 → 全员 default → 天花板崩 14 分"的单点故障。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

# 昨日竞价额下限(万元): 低于此值量比失真(昨额1万 → 量比302倍), 判为不可用
MIN_YDAY_BID_AMT_WAN = 100.0


@dataclass
class BidStrength:
    """单票竞价强度信号。所有字段 None = 该层数据不可用(不是 0)。"""
    code: str
    # ① 抢筹(开盘啦)
    qc_delta: Optional[float] = None      # 抢筹强度% (竞价净额/自由流通市值, 开盘啦口径)
    qc_last: bool = False                 # 命中最后一秒抢筹(9_24→9_25 拉升段)
    # ② 竞价量比(快照自算)
    bid_vol_ratio: Optional[float] = None  # 今日9:25竞价额 / 昨日9:25竞价额(倍)
    # ③ 加速度(快照自算)
    accel: Optional[float] = None          # 9:25涨幅 − 9:24涨幅 (百分点)
    # 缺失的层(诊断/对拍可见)
    missing: List[str] = field(default_factory=list)
    # —— 内部中间值(仅 _fill_snapshot 内部传递, 不参与合成) ——
    _chg25: Optional[float] = field(default=None, repr=False, compare=False)
    _amt25: Optional[float] = field(default=None, repr=False, compare=False)

    @property
    def complete(self) -> bool:
        return not self.missing


# ---------------------------------------------------------------- 数据加载
def load(codes: Optional[Sequence[str]] = None,
         date: Optional[str] = None) -> Dict[str, "BidStrength"]:
    """批量加载竞价强度信号。

    codes: 需要的代码(空/None = 全市场)。
    date:  交易日(空 = 自动取最近有快照的交易日, 与老链路定格回退一致)。

    永不抛异常: 任一层取不到就标记 missing, 由调用方走 default 分。
    """
    out: Dict[str, BidStrength] = {}
    want = set(str(c) for c in codes) if codes else None

    # ---- ② ③ 量比与加速度(快照表, 自给自足) ----
    try:
        _fill_snapshot(out, want, date)
    except Exception as e:                                        # noqa: BLE001
        log.warning("[竞价强度] 快照层加载失败 err=%s", e)
        for st in out.values():
            st.missing.extend(["bid_vol_ratio", "accel"])

    # ---- ① 抢筹名单(开盘啦 kpl; 失败只是不加成, 不拖垮整体) ----
    try:
        _fill_qiangchou(out, want, date)
    except Exception as e:                                        # noqa: BLE001
        log.warning("[竞价强度] 抢筹层加载失败(仅不加成) err=%s", e)
        for st in out.values():
            if "qc" not in st.missing:
                st.missing.append("qc")

    _tag_missing(out)
    return out


def _fill_snapshot(out: Dict[str, BidStrength], want, date: Optional[str]):
    from ..db import database

    conn = database.get_conn()
    try:
        cur = conn.cursor()
        # 目标交易日: 未指定 → 最近有 9:25 快照的交易日(节假日/盘前自动回退)
        # 2026-09-09 修正: 传入了 date 但当天还没 9:25 快照(如 9/9 凌晨传 date='2026-09-09'),
        # 也不能让它空跑 —— 一样回退到 MAX(date) <= 传入 date 的最近交易日,
        # 否则 strength 全空 → 异动列变成 0(主反馈 9/9 0:37 异动全空真因)
        if not date or not cur.execute(
                "SELECT 1 FROM snapshot_bid WHERE date=? AND time_point='9_25' LIMIT 1",
                (date,)).fetchone():
            row = cur.execute("SELECT MAX(date) FROM snapshot_bid "
                              "WHERE time_point='9_25'").fetchone()
            date = str(row[0]) if row and row[0] else None
        if not date:
            return
        # 昨日(严格小于)用于算竞价量比
        row = cur.execute("SELECT MAX(date) FROM snapshot_bid "
                          "WHERE date < ? AND time_point='9_25'", (date,)).fetchone()
        yday = str(row[0]) if row and row[0] else None

        # 今日 9:25 定格: 竞价涨幅 + 竞价额
        for code, chg, amt in cur.execute(
                "SELECT code, bid_change, bid_amt FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'", (date,)):
            code = str(code)
            if want is not None and code not in want:
                continue
            out[code] = BidStrength(code=code, _chg25=chg, _amt25=amt)

        # 今日 9:24: 加速度基准(不可撤单阶段, 见模块注释)
        for code, chg24 in cur.execute(
                "SELECT code, bid_change FROM snapshot_bid "
                "WHERE date=? AND time_point='9_24'", (date,)):
            st = out.get(str(code))
            if st is not None and chg24 is not None and st._chg25 is not None:  # noqa: SLF001
                st.accel = round(float(st._chg25) - float(chg24), 2)  # noqa: SLF001

        # 昨日 9:25 竞价额 → 竞价量比
        if yday:
            for code, yamt in cur.execute(
                    "SELECT code, bid_amt FROM snapshot_bid "
                    "WHERE date=? AND time_point='9_25'", (yday,)):
                st = out.get(str(code))
                if st is None or yamt is None:
                    continue
                if yamt < MIN_YDAY_BID_AMT_WAN:      # 昨额过小 → 量比失真, 判不可用
                    continue
                amt = st._amt25                       # noqa: SLF001
                if amt and amt > 0:
                    st.bid_vol_ratio = round(float(amt) / float(yamt), 2)
    finally:
        conn.close()


def _fill_qiangchou(out: Dict[str, BidStrength], want, date: Optional[str]):
    from . import kpl

    d = kpl.fetch_bid_qiangcang(date) or {}
    # list20: 开盘啦竞价异动(含 qcDelta 抢筹强度连续值)
    for it in (d.get("list20") or []):
        code = str(it.get("code") or "")
        if not code:
            continue
        if want is not None and code not in want:
            continue
        st = out.get(code)
        if st is None:
            continue
        try:
            st.qc_delta = float(it.get("qcDelta") or 0) or None
        except (TypeError, ValueError):
            st.qc_delta = None
    # listLast: 最后一秒抢筹(9_24 → 9_25 段)
    for it in (d.get("listLast") or []):
        code = str(it.get("code") or "")
        if not code:
            continue
        if want is not None and code not in want:
            continue
        st = out.get(code)
        if st is not None:
            st.qc_last = True
    # 兼容: 部分版本返回 list20Chg(9_20→9_25 涨幅榜)
    for it in (d.get("list20Chg") or []):
        code = str(it.get("code") or "")
        if code and (want is None or code in want):
            st = out.get(code)
            if st is not None:
                st.qc_last = True


def _tag_missing(out: Dict[str, BidStrength]):
    for st in out.values():
        if st.bid_vol_ratio is None and "bid_vol_ratio" not in st.missing:
            st.missing.append("bid_vol_ratio")
        if st.accel is None and "accel" not in st.missing:
            st.missing.append("accel")
        if st.qc_delta is None and not st.qc_last and "qc" not in st.missing:
            st.missing.append("qc")


# ---------------------------------------------------------------- 合成打分
def score_one(st: Optional["BidStrength"], cfg: Optional[dict] = None) -> Optional[float]:
    """三层合成 → 0~1 分。返回 None = 三层全缺(调用方走 factor default)。

    合成: 量比分档(主) + 抢筹加成 + 加速度修正, 每层缺失只走各自 default。
    """
    if st is None:
        return None
    if cfg is None:
        from . import scorer
        cfg = scorer.get_scoring_cfg()

    fac = (cfg.get("factors") or {}).get("bid_strength") or {}
    vol, accel = st.bid_vol_ratio, st.accel
    hit_qc = st.qc_delta is not None or st.qc_last
    if vol is None and accel is None and not hit_qc:
        return None                                  # 三层全缺 → 交给 default

    # 主分: 竞价量比分档; 缺失 → default(不是 0!)
    if vol is None:
        try:
            base = float(fac.get("default", 0.22))
        except (TypeError, ValueError):
            base = 0.22
    else:
        base = _bucket(fac.get("buckets"), vol, float(fac.get("default", 0.22)))

    score = base
    # 抢筹加成: 命中开盘啦抢筹强度榜 / 最后一秒抢筹(两者可叠加)
    if st.qc_delta is not None:
        score += float(fac.get("qc_bonus", 0.15))
    if st.qc_last:
        score += float(fac.get("qc_last_bonus", 0.10))
    # 加速度修正: 竞价末段拉升加分、跳水减分(中性区间不修正)
    # 2026-09-09 方向 gate: "拉升"只在**竞价翻红**(chg25>0)背景才成立 —
    # 低开背景下跌幅收窄(-8.97→-8.01)是"跌势放缓"不是抢筹拉升, 不给加分;
    # 跳水减分(含低开续跌)保持; chg25 未知(None)按旧行为放行(不误伤)。
    chg25 = st._chg25
    if accel is not None:
        up = float(fac.get("accel_up", 0.08))
        down = float(fac.get("accel_down", -0.08))
        if accel > 0.5:
            if chg25 is None or chg25 > 0:
                score += up
        elif accel < -0.5:
            score += down
    # 2026-09-09 低开 gate(中石科技 300684 事故): 竞价定格涨幅<=0(低开/平开)时,
    # "放量"是**出货**不是**抢筹** —— 量比/抢筹/加速度合成后整体封顶 0.40(弱档),
    # 防止高位放量低开被标成"强5"还加置信度(实测 9/8: -8.01% 竞涨 → warn 5 / conf 83)。
    if chg25 is not None and chg25 <= 0:
        score = min(score, 0.40)
    return max(0.05, min(1.0, score))


def _bucket(buckets, value, default: float) -> float:
    for b in buckets or []:
        try:
            lo, hi, sc = float(b[0]), float(b[1]), float(b[2])
        except (TypeError, ValueError, IndexError):
            continue
        if lo <= value < hi:
            return sc
    return default


def score_map(strengths: Dict[str, "BidStrength"],
              cfg: Optional[dict] = None) -> Dict[str, Optional[float]]:
    return {c: score_one(s, cfg) for c, s in (strengths or {}).items()}


# ---------------------------------------------------------------- 调用方入口
def enabled() -> bool:
    """settings `use_bid_strength=1` 才启用(默认关 → 老链路零变化)。

    各调用方(api/stocks · auto_apply · system_batch)统一走这里, 避免各处各写
    一遍字符串比较导致口径漂移。
    """
    try:
        from . import settings
        return str(settings.get("use_bid_strength") or "0") in ("1", "true", "True")
    except Exception:                                          # noqa: BLE001
        return False


def load_scores(codes, date: Optional[str] = None) -> Dict[str, float]:
    """一步到位: 加载 + 合成 + 过滤 None。未启用/异常一律返回空 dict。

    返回 {} 时调用方退回 f630 异动等级(老行为), 绝不阻塞选股。
    """
    if not enabled():
        return {}
    codes = [str(c) for c in (codes or []) if c]
    if not codes:
        return {}
    try:
        st = load(codes, date=date)
        return {c: v for c, v in score_map(st).items() if v is not None}
    except Exception as e:                                     # noqa: BLE001
        log.warning("[竞价强度] 加载失败(退回 f630 异动等级) err=%s", e)
        return {}
