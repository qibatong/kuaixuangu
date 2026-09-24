# -*- coding: utf-8 -*-
"""
竞价强度信号 (2026-09-08 新增 · 2026-09-19 两层化 · 2026-09-20 v5 重构 · v6 AI 层)
=================================================================================
承担评分 **异动分(w_warn, 17%)** 因子, 替代已失活的东财 f630 异动等级。

演进史
--------------------------------------------------------------------------------
* v1(2026-09-08): 三层 = 竞价量比 + 涨幅加速度 + 开盘啦抢筹加成。
* v2(2026-09-19): 主人拍板移除抢筹层(开盘啦退役) → 两层 = 量比 + 加速度修正。
* v3(2026-09-20): 主人拍板: 删除加速度修正 + 低开 gate, 换竞价主力净额层。
* v6(2026-09-20): **主人拍板: 加 AI 预测层**(aipick XGBoost 涨停概率, 见
  ai_predict.py) —— 三层 = 量比(0.45) + 净额(0.30) + AI(0.25)。
* v7(2026-09-23): **主人拍板: 去掉净额档, 其 0.30 权重并入量比档** ——
  实测(2026-09-23 全市场 5561 只, `_kx_probe_warn_layers.py`): 竞价主力净额
  **连续 12 个交易日非零 0 只** ⇒ 该层恒 = ff_default 0.35 = **常数项 0.105**,
  对排序零贡献、纯稀释量比。故 w_ff 置 0(等价移除), 合成简化为
  **0.75×量比分档 + 0.25×AI档**(量比权重 45%→75%)。
  🔴 连带影响: 盘中动态加分层(api/stocks `_apply_intraday_ff_bonus`)与净额档
  同源(`score_one_live_ff`) ⇒ w_ff=0 后 warn_live ≡ warn_static, bonus 恒 0,
  该功能**一并失效**(2026-09-23 实测: 原本 12 只被 +1~3 分, 归零后 Top5 边界
  换 1 只)。要恢复盘中加分, 需把 w_ff 调回 0.30(或另立独立加成通道)。

现状两层(净额层保留字段与分档表, 但权重 0 = 不参与合成)
--------------------------------------------------------------------------------
① 竞价量比     —— **标准口径: 竞价成交量 ÷ 近 5 日平均每分钟成交量**(2026-09-24 主人拍板换口径)。
                  数据源 = 猫爪官方成品 `auc_vol_ratio`, 随 9:25 快照落
                  `snapshot_bid.auc_vol_ratio`(采集侧 **daily_auc 唯一** —— screening 的
                  openapi 字段清单无此字段, 不请求)。实测非零率 98.4%、由该字段反推的
                  5 日日均量日间比值中位 0.9911(93.9% 落 [0.8,1.25]) ⇒ 字段自洽可信。
                  ★ 旧口径(今 9:25 竞价额 ÷ 昨 9:25 竞价额)是「竞价额同环比」, 现仅作
                  **降级回退**: 新列为 0 的历史行仍走它 —— 保证换口径当天名单不整体塌成
                  default(历史快照无新列, 否则全员落到量比档 default 0.22)。
                  为什么不用官方 `auc_to_pre_auc_vol_ratio`(竞昨量比): 该字段实测失真
                  (与真实竞昨量比 r≈0.19、max 691 倍), 2026-09-20 已回退。
② AI 预测概率  —— **aipick 模型内联推理**(ai_predict.ai_score_map): 全市场
                  Top30 ∩ p≥0.80 三档(0.90/0.85/0.80 → 1.0/0.85/0.70)。
                  不在榜是**常态**(全市场仅 30 只) → 走 ai_default 0.35 中性,
                  不当惩罚也**不标 missing**(与 ff 层不同: 缺列才是故障)。
(已移除) 竞价主力净额 —— 猫爪 fundflow_kp 落 snapshot_bid.auc_main_net ÷ 自由流通
                  市值 ×100。**2026-09-23 权重置 0**: 竞价主力净额是"有大单才有值",
                  且采集链路 9:25 定格早于猫爪生成(9:26 后才有) ⇒ 12 日全市场非零
                  0 只, 该层实际从未生效。字段/分档表保留在 `BidStrength.ff_pct` 与
                  `ff_buckets`, 仅作展示与日后恢复用。

两个实测踩过的坑(不可回退)
--------------------------------------------------------------------------------
* 回退路径(自算「今额/昨额」)**必须**过滤昨日竞价额 < 100 万: 昨额 1 万 → 量比 302 倍,
  严重失真。该过滤**仅对标准口径无值的历史行**生效(新口径直读官方成品, 无需门槛)。
* 加速度层曾要求以 9_24 为基准(9:20 前挂单可撤) —— 该层已删, 9_24 快照仅剩
  竞价异动页「加速度」展示列在用(与评分无关, 勿混)。

降级语义(契约铁律)
--------------------------------------------------------------------------------
**每层独立降级**: 任一层缺失只走该层 default, 不是整个因子归零。这直接解决
f630 那种"一个字段挂掉 → 全员 default → 天花板崩 14 分"的单点故障。
老库无 auc_main_net 列 → 净额层整体判不可用(走 default), 量比层不受影响。
AI 层模型/取数任一失败 → 整层走 default, 前两层不受影响。
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
    # ① 竞价量比(标准口径: 竞价成交量 ÷ 近 5 日平均每分钟成交量)
    bid_vol_ratio: Optional[float] = None  # 猫爪 auc_vol_ratio; 缺则回退「今额/昨额」
    # ② 竞价主力净额占自由流通市值(%; 猫爪 fundflow_kp, 采集链路落库)
    ff_pct: Optional[float] = None
    # ③ AI 预测档位分(aipick XGBoost, 全市场 Top30 ∩ p≥0.80 三档; None=不在榜=常态)
    ai: Optional[float] = None
    # 缺失的层(诊断/对拍可见; ③ 不在榜是常态, 不标)
    missing: List[str] = field(default_factory=list)
    # —— 内部中间值(仅 _fill_snapshot 内部传递, 不参与合成) ——
    _amt25: Optional[float] = field(default=None, repr=False, compare=False)
    # 自由流通市值(元; 2026-09-20 盘中动态净额层用: ff_live = 盘中主力净额 ÷ _free_mv ×100)
    _free_mv: Optional[float] = field(default=None, repr=False, compare=False)

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

    # ---- ① ② 量比与主力净额(快照表, 自给自足) ----
    filled_date: Optional[str] = None
    try:
        filled_date = _fill_snapshot(out, want, date)
    except Exception as e:                                        # noqa: BLE001
        log.warning("[竞价强度] 快照层加载失败 err=%s", e)

    # ---- ③ AI 预测层(aipick 内联推理, 独立降级) ----
    # 必须用快照**实际**交易日(节假日/盘前已回退), 与 ①② 同一天, 否则
    # 评分读 T-1 快照而 AI 查当日 → 周末/盘前 AI 层恒空。
    if out and filled_date:
        _fill_ai(out, filled_date)

    _tag_missing(out)
    return out


def _fill_snapshot(out: Dict[str, BidStrength], want, date: Optional[str]):
    from ..db import database

    conn = database.get_conn()
    try:
        cur = conn.cursor()
        # 老库兼容: auc_main_net 是 2026-09-20 新增列, 未迁移的库 SELECT 会直接报错
        # → 探测列存在性, 无则净额层整体判不可用(量比层照常, 独立降级)。
        cols = {r[1] for r in cur.execute("PRAGMA table_info(snapshot_bid)").fetchall()}
        has_ff = "auc_main_net" in cols and "free_mv" in cols
        # 老库兼容同理: auc_vol_ratio 是 2026-09-24 新增列(未迁移的库 SELECT 会直接报错)
        has_vr = "auc_vol_ratio" in cols
        sel_extra = (", auc_main_net, free_mv" if has_ff else ", NULL, NULL") \
            + (", auc_vol_ratio" if has_vr else ", NULL")

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

        # 今日 9:25 定格: 竞价额(自算量比兜底) + 竞价主力净额/自由流通市值(净额层)
        #                       + 竞昨量比(官方成品, 优先)
        for code, amt, auc_main_net, free_mv, vr in cur.execute(
                "SELECT code, bid_amt%s FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25'" % sel_extra, (date,)):
            code = str(code)
            if want is not None and code not in want:
                continue
            st = BidStrength(code=code, _amt25=amt)
            # 层① 竞价量比 = **标准口径**(竞价成交量 ÷ 近5日平均每分钟成交量; 2026-09-24):
            #   直读猫爪官方成品列 auc_vol_ratio。无值(历史行/采集缺) → 留 None,
            #   由下方「昨日 9:25 竞价额」自算做**降级回退**(旧口径), 保证不断层。
            if vr and float(vr) > 0:
                st.bid_vol_ratio = round(float(vr), 2)
            # 层② 净额占比(%): 0 = 竞价无大单(无信号) → None 走 default, 不当惩罚;
            #   分母必须用**自由流通市值**(与门槛/竞价换手同口径, 2026-09-20 铁律)。
            if auc_main_net and free_mv and float(free_mv) > 0:
                st.ff_pct = round(float(auc_main_net) / float(free_mv) * 100.0, 4)
            # 自由流通市值(元)顺带带上 —— 盘中动态净额层(stocks._apply_intraday_ff_bonus)
            # 用它做分母: ff_live = 盘中主力净额 ÷ free_mv ×100, 与竞价层同口径。
            if free_mv and float(free_mv) > 0:
                st._free_mv = float(free_mv)          # noqa: SLF001
            out[code] = st

        # 降级回退: 昨日 9:25 竞价额 → 自算「今额/昨额」(仅对标准口径无值的行)
        if yday:
            for code, yamt in cur.execute(
                    "SELECT code, bid_amt FROM snapshot_bid "
                    "WHERE date=? AND time_point='9_25'", (yday,)):
                st = out.get(str(code))
                if st is None or yamt is None:
                    continue
                if st.bid_vol_ratio is not None:     # 已有标准口径值 → 不回退(防两种口径混用)
                    continue
                if yamt < MIN_YDAY_BID_AMT_WAN:      # 昨额过小 → 量比失真, 判不可用
                    continue
                amt = st._amt25                       # noqa: SLF001
                if amt and amt > 0:
                    st.bid_vol_ratio = round(float(amt) / float(yamt), 2)
        return date                                   # 实际交易日(AI 层需同源日期)
    finally:
        conn.close()


def _fill_ai(out: Dict[str, BidStrength], date: str):
    """层③ AI 预测(主人拍板 2026-09-20): aipick XGBoost 全市场榜 Top30 ∩ p≥0.80 三档。

    独立降级契约: 模型缺失/collector 异常/推理失败 → 本层整体不填(走 default),
    量比/净额两层完全不受影响。配置(ai_topn/ai_buckets)从 scorer 读。
    """
    try:
        from . import scorer, ai_predict
        fac = (scorer.get_scoring_cfg().get("factors") or {}).get("bid_strength") or {}
        try:
            topn = int(fac.get("ai_topn", 30))
        except (TypeError, ValueError):
            topn = 30
        ai_map = ai_predict.ai_score_map(out.keys(), date, topn=topn,
                                         buckets=fac.get("ai_buckets"))
    except Exception as e:                                        # noqa: BLE001
        log.warning("[竞价强度] AI 层加载失败(独立降级) err=%s", str(e)[:150])
        return
    for code, sc in (ai_map or {}).items():
        st = out.get(code)
        if st is not None:
            st.ai = sc


def _tag_missing(out: Dict[str, BidStrength]):
    for st in out.values():
        if st.bid_vol_ratio is None and "bid_vol_ratio" not in st.missing:
            st.missing.append("bid_vol_ratio")
        if st.ff_pct is None and "ff_pct" not in st.missing:
            st.missing.append("ff_pct")


# ---------------------------------------------------------------- 合成打分
def _cfgf(fac: dict, key: str, default: float) -> float:
    """配置浮点读取兜底(配置写错类型时回默认, 防一个坏配置毒死整因子)。"""
    try:
        return float(fac.get(key, default))
    except (TypeError, ValueError):
        return default


def score_one(st: Optional["BidStrength"], cfg: Optional[dict] = None) -> Optional[float]:
    """子权重合成 → 0~1 分。返回 None = 所有层都缺(调用方走 factor default)。

    合成(2026-09-23 v7, 净额档已移除): score = w_vol×量比分档 + w_ai×AI档位
      * 每层缺失只走各自 default(量比→0.22 / AI→ai_default 0.35), 独立降级;
      * 子权重从配置读(w_vol_ratio/w_ff/w_ai), 自动归一化(防配置总和≠1);
      * 🔴 w_ff 现为 0(2026-09-23 主人拍板去掉净额档, 权重并入量比) —— 净额项
        即使有值也乘 0, 等价移除; 恢复只需把 w_ff 调回 0.30;
      * v6 AI 层: 不在榜(全市场仅 Top30)是常态 → ai_default 中性, 不当惩罚。
    """
    if st is None:
        return None
    if cfg is None:
        from . import scorer
        cfg = scorer.get_scoring_cfg()
    fac = (cfg.get("factors") or {}).get("bid_strength") or {}
    return _compose(st.bid_vol_ratio, st.ff_pct, st.ai, fac)


def score_one_live_ff(st: Optional["BidStrength"], ff_live: Optional[float],
                      cfg: Optional[dict] = None) -> Optional[float]:
    """与 score_one 同一合成, 净额层取 **max(竞价档, 盘中实时档)** —— 只加不减。

    2026-09-20 主人拍板「开盘后主力持续净流入的加分」。合成主体与 score_one
    共享 _compose, 权重/档位/归一化零复刻(防口径漂移)。

    🔴 **2026-09-23 起本函数与 score_one 等价**(净额档已移除, w_ff=0):
    `warn_live − warn_static ≡ 0` ⇒ 调用方 api/stocks `_apply_intraday_ff_bonus`
    的 bonus 恒为 0(该动态加分功能随之静默失效, 不报错、不影响名单可用性)。
    恢复路径 = 把 `w_ff` 调回 0.30(配置写入即可, 无需改代码)。
    """
    if st is None:
        return None
    if cfg is None:
        from . import scorer
        cfg = scorer.get_scoring_cfg()
    fac = (cfg.get("factors") or {}).get("bid_strength") or {}
    return _compose(st.bid_vol_ratio, st.ff_pct, st.ai, fac, ff_live=ff_live)


def _compose(vol: Optional[float], ff: Optional[float], ai: Optional[float],
             fac: dict, ff_live: Optional[float] = None) -> Optional[float]:
    """层合成主体(score_one / score_one_live_ff 共享; 任何一方都不得再复制权重逻辑)。

    2026-09-23 v7: 净额档权重 w_ff 默认 0(已移除) ⇒ ff/ff_live 项乘 0 不参与,
    但分支保留以便一键恢复(w_ff=0.30 即回到 v6 行为)。
    """
    if vol is None and ff is None and ai is None:
        return None                                  # 全层缺 → 交给 default

    # 子权重(自动归一)
    w_vol = _cfgf(fac, "w_vol_ratio", 0.75)
    w_ff = _cfgf(fac, "w_ff", 0.0)
    w_ai = _cfgf(fac, "w_ai", 0.25)
    tot = w_vol + w_ff + w_ai
    if tot <= 0:
        w_vol, w_ff, w_ai = 0.75, 0.0, 0.25
    else:
        w_vol, w_ff, w_ai = w_vol / tot, w_ff / tot, w_ai / tot

    # 层① 量比分档: 缺失 → default(不是 0!)
    vol_default = _cfgf(fac, "default", 0.22)
    if vol is None:
        vol_score = vol_default
    else:
        vol_score = _bucket(fac.get("buckets"), vol, vol_default)

    # 层② 竞价主力净额分档: **2026-09-23 起 w_ff=0 不参与**(保留以求可回退)
    #   ff_live(盘中实时占比)传入时取 max(竞价档, 盘中档) —— 只加不减
    #   (2026-09-20 主人拍板「主力持续净流入的加分」; 盘中流出不倒扣)。
    ff_default = _cfgf(fac, "ff_default", 0.35)
    if ff is None:
        ff_score = ff_default
    else:
        ff_score = _bucket(fac.get("ff_buckets"), ff, ff_default)
    if ff_live:
        ff_live_score = _bucket(fac.get("ff_buckets"), ff_live, ff_default)
        if ff_live_score > ff_score:
            ff_score = ff_live_score

    # 层③ AI 预测: 不在榜/未启用 → ai_default(中性, 不惩罚)
    ai_default = _cfgf(fac, "ai_default", 0.35)
    ai_score = ai if ai is not None else ai_default

    return max(0.05, min(1.0, w_vol * vol_score + w_ff * ff_score + w_ai * ai_score))


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
