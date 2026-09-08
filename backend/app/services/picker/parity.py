# -*- coding: utf-8 -*-
"""
新老选股链路**对拍**工具 (重构 P2)
=================================================================================
用途: 同一份行情 raw + 同一套筛选参数, 分别跑
      A. 老链路  scorer.score_all_stocks + scorer.apply_filters
      B. 新链路  contract.QuoteRow → picker.score.score_rows → picker.filter.apply_filters
      逐票比对 概率/置信度/竞价字段/名单, 产出差异报告。

判定标准(关键 — 否则对拍会变成"允许一切差异"的橡皮图章):
  * **字段完备**的输入(所有参与评分/过滤的字段都有值)下, 两条链路必须
    **逐票一致**(probability/confidence 与名单完全相同)。任何不一致 = 新链路
    存在计算错误, 测试必须红。
  * 字段缺失(竞价涨幅/市值/竞价额缺失等)时, 允许且**应当**出现差异 —— 那些
    差异就是本次重构要修的东西(老链路填 0 / 退化 f3), 由报告分类列出。

运行方式(线上灰度): pipeline 双跑时调用 run() 并记录 log, 不拦截返回。
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .contract import QuoteRow
from . import filter as pfilter
from .score import score_rows


@dataclass
class ParityReport:
    n_raw: int = 0
    legacy: List[dict] = field(default_factory=list)     # 老链路结果(过滤后)
    new: List[dict] = field(default_factory=list)        # 新链路结果(过滤后)
    only_legacy: List[str] = field(default_factory=list)  # 老有新无
    only_new: List[str] = field(default_factory=list)     # 新有老无
    score_diff: List[dict] = field(default_factory=list)  # 同 code 但分不同
    field_diff: List[dict] = field(default_factory=list)  # 同 code 关键字段不同
    new_stats: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)       # 新链路内部错误/降级
    elapsed_ms: int = 0

    @property
    def identical(self) -> bool:
        return not (self.only_legacy or self.only_new
                    or self.score_diff or self.field_diff)

    def summary(self) -> str:
        return ("对拍 raw=%d 老=%d只 新=%d只 仅老=%d 仅新=%d 分差=%d 字段差=%d 剔除=%s"
                % (self.n_raw, len(self.legacy), len(self.new),
                   len(self.only_legacy), len(self.only_new),
                   len(self.score_diff), len(self.field_diff), self.new_stats))


# ---------------------------------------------------------------- 输入构造
def build_quotes(raw: List[dict], *, auction_window: bool = False,
                 day_bid_change: Optional[Dict[str, float]] = None,
                 day_bid_amt_wan: Optional[Dict[str, float]] = None,
                 yesterday_chg: Optional[Dict[str, float]] = None,
                 day_bid_vol: Optional[Dict[str, float]] = None) -> List[QuoteRow]:
    """老链路 raw(dict 行情行) → 契约 QuoteRow 列表(新链路输入)。

    定格 map 由调用方注入(权威优先, 见 contract.FIELD_AUTHORITY), 与老链路
    score_all_stocks 传入的 day_bid_change/day_bid_amt 是同一份数据, 保证可比。
    day_bid_vol: 窗口外竞价量**只能**来自定格(老链路用 f5 当日累计量算竞价换手
    是错的), 对拍窗口外场景必须传, 否则新链路 bid_turnover=None 而老链路有值。
    """
    dc = day_bid_change or {}
    da = day_bid_amt_wan or {}
    yc = yesterday_chg or {}
    dv = day_bid_vol or {}
    rows: List[QuoteRow] = []
    for s in raw or []:
        code = str(s.get("f12") or "")
        if not code:
            continue
        rows.append(QuoteRow.from_eastmoney(
            s, auction_window=auction_window,
            day_bid_change=dc.get(code),
            day_bid_amt_wan=da.get(code),
            day_bid_vol=dv.get(code),
            yesterday_chg=yc.get(code),
        ))
    return rows


def _legacy_patch_zt(zt_codes: Optional[Set[str]]):
    """把老 is_first_board 内部的网络调用(fetcher.get_yesterday_zt_codes)替换成
    对拍给定的名单 —— 否则老链路会真发请求, 且网络抖动会让对拍结果不可复现。"""
    from .. import fetcher
    original = fetcher.get_yesterday_zt_codes

    def _fake():
        return set(zt_codes) if zt_codes is not None else None

    fetcher.get_yesterday_zt_codes = _fake
    return fetcher, original


def _legacy_patch_clock(scorer, auction_window: bool):
    """老链路的竞价额/竞涨来源由**真实时钟**决定(use_spot_bid = in_auction_window()
    and _bj_hm()<9:30), 不 patch 就无法对拍(凌晨跑与盘中跑结果不同)。
    auction_window=True → 伪装 9:20(窗口内且<9:30); False → 伪装 10:00(窗口外)。"""
    o_win, o_hm = scorer.in_auction_window, scorer._bj_hm
    scorer.in_auction_window = lambda: auction_window
    scorer._bj_hm = lambda: (9 * 60 + 20) if auction_window else (10 * 60)
    return o_win, o_hm


# ---------------------------------------------------------------- 对拍主入口
def compare(legacy_items: List[dict], raw: List[dict], f: Dict, *,
            auction_window: bool = False,
            day_bid_change: Optional[Dict[str, float]] = None,
            day_bid_amt_wan: Optional[Dict[str, float]] = None,
            yesterday_chg: Optional[Dict[str, float]] = None,
            zt_codes: Optional[Set[str]] = None,
            day_bid_vol: Optional[Dict[str, float]] = None,
            legacy_scored: Optional[List[dict]] = None) -> ParityReport:
    """**灰度专用**: 老链路结果已算出(legacy_items)时, 只跑新链路并比对,
    不重复跑老链路(线上双跑的成本减半)。

    legacy_scored: 老链路 score_all_stocks 的输出(过滤前, 含全部评分行)。给了就能
    比对"同为落选票的分数差异", 不给则只比对最终名单。
    """
    t0 = time.time()
    rep = ParityReport(n_raw=len(raw or []))
    dc = day_bid_change or {}
    da = day_bid_amt_wan or {}
    yc = yesterday_chg or {}
    dv = day_bid_vol or {}

    from ...core import logger
    log = logger.get_logger(__name__)
    try:
        from . import pipeline
        ctx = pipeline.PickContext(
            markets=f.get("markets"), zt_codes=zt_codes,
            day_bid_change=dc, day_bid_amt_wan=da, day_bid_vol=dv,
            yesterday_chg=yc)
        pres = pipeline.run(f, ctx=ctx, now=None)
    except Exception as e:                                   # noqa: BLE001
        log.warning("灰度新链路执行失败(不影响老链路返回) err=%s", e)
        rep.errors.append("新链路异常: %s" % e)
        rep.elapsed_ms = int((time.time() - t0) * 1000)
        return rep
    rep.errors.extend(pres.errors)
    rep.new_stats = dict(pres.stats)
    rep.new = list(pres.items)
    rep.legacy = [{k: v for k, v in it.items() if k != "_raw"}
                  for it in (legacy_items or [])]

    lg = {it["code"]: it for it in rep.legacy}
    nw = {it["code"]: it for it in rep.new}
    rep.only_legacy = sorted(set(lg) - set(nw))
    rep.only_new = sorted(set(nw) - set(lg))
    for code in sorted(set(lg) & set(nw)):
        a, b = lg[code], nw[code]
        if int(a.get("probability") or 0) != int(b.get("probability") or 0) or \
           int(a.get("confidence") or 0) != int(b.get("confidence") or 0):
            rep.score_diff.append({
                "code": code,
                "prob": (a.get("probability"), b.get("probability")),
                "conf": (a.get("confidence"), b.get("confidence")),
            })
        for key in ("bidChange", "bidAmt", "circulationMV", "bidTurnover"):
            av, bv = a.get(key), b.get(key)
            if av is None and bv is None:
                continue
            if av is None or bv is None:
                rep.field_diff.append({"code": code, "field": key,
                                       "legacy": av, "new": bv})
            elif abs(float(av) - float(bv)) > 1e-6:
                rep.field_diff.append({"code": code, "field": key,
                                       "legacy": av, "new": bv})
    rep.elapsed_ms = int((time.time() - t0) * 1000)
    return rep


def run(raw: List[dict], f: Dict, *, auction_window: bool = False,
        legacy_items: Optional[List[dict]] = None,
        day_bid_change: Optional[Dict[str, float]] = None,
        day_bid_amt_wan: Optional[Dict[str, float]] = None,
        yesterday_chg: Optional[Dict[str, float]] = None,
        zt_codes: Optional[Set[str]] = None,
        qiangchou_codes: Optional[Set[str]] = None,
        require_bid_change: bool = True,
        cfg: Optional[dict] = None,
        yesterday_map: Optional[Dict[str, list]] = None,
        snapshot_map: Optional[Dict[str, dict]] = None,
        day_bid_vol: Optional[Dict[str, float]] = None) -> ParityReport:
    """跑一次对拍。返回报告(不抛异常 — 对拍绝不能影响线上主流程)。

    raw: 东财行情行列表(含 f12/f14/f2/f3/f5/f6/f8/f10/f17/f18/f21/f100/f103/f615/f616/f617/f630)
    f:   scorer.validate_filters 的输出
    auction_window: 是否竞价窗口(决定链路吃不吃实时 f615/f616)。**两条链路都按此
        口径**, 老链路内部依赖真实时钟, 由 _legacy_patch_clock 伪装, 否则对拍不可复现。
    zt_codes: 昨涨停/连板代码集(老链路据此判断, None=名单不可用 → 老链路降级 concept)
    yesterday_map / snapshot_map: 老链路算 bidRatio/accel 用(不参与过滤, 可留空)
    """
    t0 = time.time()
    rep = ParityReport(n_raw=len(raw or []))
    dc = day_bid_change or {}
    da = day_bid_amt_wan or {}
    yc = yesterday_chg or {}

    # ---------- A. 老链路 ----------
    from .. import scorer
    # 老链路结果已由调用方算好(灰度场景) → 只跑新链路, 省一半开销
    if legacy_items is not None:
        return compare(legacy_items, raw, f, auction_window=auction_window,
                       day_bid_change=dc, day_bid_amt_wan=da,
                       yesterday_chg=yc, zt_codes=zt_codes,
                       day_bid_vol=day_bid_vol)
    fetcher, orig_zt = _legacy_patch_zt(zt_codes)
    o_win, o_hm = _legacy_patch_clock(scorer, auction_window)
    try:
        scored = scorer.score_all_stocks(
            raw, yesterday_map or {}, snapshot_map or {},
            qiangchou_codes=qiangchou_codes,
            day_bid_amt=da, day_bid_change=dc, yesterday_chg_map=yc)
        legacy_res = scorer.apply_filters(scored, f)
    finally:
        fetcher.get_yesterday_zt_codes = orig_zt
        scorer.in_auction_window, scorer._bj_hm = o_win, o_hm
    rep.legacy = [{k: v for k, v in it.items() if k != "_raw"} for it in legacy_res]

    # ---------- B. 新链路 ----------
    rows = build_quotes(raw, auction_window=auction_window, day_bid_change=dc,
                        day_bid_amt_wan=da, yesterday_chg=yc, day_bid_vol=day_bid_vol)
    srows = score_rows(rows, cfg)
    ctx = pfilter.FilterContext(markets=f.get("markets"), zt_codes=zt_codes,
                                require_bid_change=require_bid_change)
    outcome = pfilter.apply_filters(srows, f, ctx)
    rep.new_stats = dict(outcome.stats)
    rep.new = [it.to_dict() for it in outcome.kept]

    # ---------- C. 比对 ----------
    lg = {it["code"]: it for it in rep.legacy}
    nw = {it["code"]: it for it in rep.new}
    rep.only_legacy = sorted(set(lg) - set(nw))
    rep.only_new = sorted(set(nw) - set(lg))
    for code in sorted(set(lg) & set(nw)):
        a, b = lg[code], nw[code]
        if a.get("probability") != b.get("probability") or \
           a.get("confidence") != b.get("confidence"):
            rep.score_diff.append({
                "code": code,
                "prob": (a.get("probability"), b.get("probability")),
                "conf": (a.get("confidence"), b.get("confidence")),
            })
        # 关键字段比对(容忍浮点 1e-6)
        for key in ("bidChange", "bidAmt", "circulationMV", "bidTurnover"):
            av, bv = a.get(key), b.get(key)
            if av is None and bv is None:
                continue
            if av is None or bv is None:
                rep.field_diff.append({"code": code, "field": key,
                                       "legacy": av, "new": bv})
            elif abs(float(av) - float(bv)) > 1e-6:
                rep.field_diff.append({"code": code, "field": key,
                                       "legacy": av, "new": bv})
    rep.elapsed_ms = int((time.time() - t0) * 1000)
    return rep
