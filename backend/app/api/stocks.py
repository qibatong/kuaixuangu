# -*- coding: utf-8 -*-
"""
选股路由: GET /api/stocks?action=lock|filter|refresh|ping&strategy=auction&筛选参数...
===================================================================
strategy=auction  竞价选股(默认, 行为不变): lock 9:30 前唯一锁定, 评分=竞价涨幅/竞价换手/异动...
strategy=spot     盘中实时选股【2026-09-09 已下线, 前端无入口/后端零调用】: 随时 refresh, 评分=实时涨幅/量比/换手/封单强度..., 涨停留池接口
"""
import time

from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import (auction_snapshot, fetcher, history, kpl, notify, scorer,
                        meoz_client,
                        settings, stats)
from ..services.cache_store import store as _cstore   # 2026-09-04: refresh 计算缓存
from .deps import get_uid, jr, qs

log = logger.get_logger(__name__)

# ---- 选股闸门 v3(2026-09-17 主人拍板重做: 只挡「必然给出非当日定格名单」的两段) ----
# 口径本体在 picker/mode.is_pick_open(纯函数, 有单测覆盖边界), 此处只做**接线**:
#   - 时间维: ① [09:00:00,09:15:00) 盘前用上交易日定格;
#             ② [09:25:00,09:25:50] 当日 9_25 尚未落库(load_snapshot_full 静默回退昨日);
#             **09:15:00-09:24:59 放行** —— 竞价窗口, v4.11.22 曾把这段封死致 9/17 事故。
#   - 快照维: ≥09:25:51 时间维放行, 但当日 9_25 未落库时**继续拦**(防重采越过放行点
#     时闸门形同虚设; 重采窗口 _BID25_RETRY_UNTIL=09:26:00 距 09:25:51 留 9 秒余量)。
#   - 非交易日 / 盘前(<9:00) 一律放行 —— 回放最近交易日定格是既有功能。
# 命中即 **直接返回, 不跑 pipeline / 不落批次 / 不推送**(不给用户任何假名单)。
# 开关 pick_window_guard 默认 1, 出问题后台置 0 即时回滚(与 picker_lock /
# frontend_local_filter / precompute_* 同一模式); ping 会把开关状态透给前端。
# 2026-09-19: 末端 09:25:35 → 09:25:50(换猫爪源后 9:25 定格落库更晚)。
PICK_WINDOW_SWITCH = "pick_window_guard"


def _pick_window_guard_on() -> bool:
    """闸门开关是否启用 —— **单一口径处**(闸门分支与 ping 上报共用)。

    2026-09-17: 原写法 `bool(settings.get(k, 1))` 对字符串 "0" 会判成 True(非空串 truthy),
    「关开关」会静默失效; 这里显式解析常见假值形态(0 / "0" / "false" / "no" / "off" / 空串)。
    """
    v = settings.get(PICK_WINDOW_SWITCH, 1)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _pick_blocked_reason(now=None):
    """返回拦截原因文案(未拦截返回 None)。now 可注入(测试/排查用)。"""
    from ..services.picker import mode as pmode
    if not pmode.is_trading_day(now):
        return None
    if not pmode.is_pick_open(now):
        return pmode.PICK_BLOCK_MSG_TIME
    secs, _ = pmode.bj_secs(now)
    if secs >= pmode.T_PICK_OPEN and not auction_snapshot.has_today_snapshot(
            pmode.bj_date(now)):
        return pmode.PICK_BLOCK_MSG_SNAP
    return None


def _pick_blocked_until(now=None):
    """拦截段的放行时刻(前端提示用)。非拦截时刻回退到快照维放行点(现 09:25:51)。"""
    from ..services.picker import mode as pmode
    t = pmode.T_PICK_OPEN
    return pmode.pick_resume_at(now) or "%02d:%02d:%02d" % (t // 3600, (t % 3600) // 60, t % 60)


def _freeze_fields(now=None):
    """定格数据来源字段(v4.11.29, 2026-09-18) —— 供前端顶栏标注。

    盘前/非交易日按设计仍出名单(PREOPEN/CLOSED 用上一交易日 9:25 定格), 但必须让
    "用的是哪天的定格"对用户可见, 否则会被误当成当日名单(主人 9/18 反馈
    「刷出来是昨天的数据」即这类误解)。返回：
      {"freezeDate": "YYYY-MM-DD", "freezeIsToday": bool}
    查库异常 → 返回 {} (宁可少标, 也不误标成"当日")。
    """
    from ..services.picker import mode as pmode
    try:
        today = pmode.bj_date(now)
        d = auction_snapshot.freeze_source_date(today)
    except Exception as e:                                       # noqa: BLE001
        log.warning("定格来源日期取值失败(不影响返回) err=%s", e)
        return {}
    return {"freezeDate": d, "freezeIsToday": (d == today)}


def _run_new_pipeline(uid, action, f, *, yesterday_map, yesterday_chg_map,
                      snapshot_map, bid_amt_map, bid_chg_map):
    """跑新链路 picker.pipeline; 返回 (items, err) —— **二选一有值**。

    2026-09-09 起新链路是**唯一**选股链路: 此前"新链路不可用 → 静默回退老链路"
    的双轨已删除。理由: 双轨让新链路的缺陷永远暴露不出来(生产竞价窗口 25 次
    调用全部入选 0 只, 因为 n_universe>0 被判成功、回退根本没触发, 用户只看到
    空名单而日志一片 INFO)。现在失败就是失败 —— 明确报错, 降级**必须可见**。

    判定失败只看一件事: **名单源有没有给出全市场行**(n_universe>0)。
    - 名单源失败 → err(不提供名单: 竞价数据不可伪造)
    - 补丁源失败 / 强度缺失 → n_universe>0, 只是 degraded → **不算失败**
    - 入选 0 只是合法空名单, 同样不算失败
    异常一律转成 err, 绝不把堆栈抛给用户。
    """
    try:
        from ..services.picker import mode as pmode
        from ..services.picker import pipeline
        ctx = pipeline.PickContext(
            date=pmode.bj_date(),
            markets=f.get("markets"),
            zt_codes=(_safe_zt_codes() if not f.get("limitUp") else None),
            day_bid_change=bid_chg_map or {},
            day_bid_amt_wan=bid_amt_map or {},
            yesterday_chg=yesterday_chg_map or {},
            yesterday_map=yesterday_map or {},
            snapshot_map=snapshot_map or {},
            require_bid_change=True,
        )
        res = pipeline.run(f, ctx=ctx)
    except Exception as e:                                     # noqa: BLE001
        log.error("选股链路异常 uid=%s action=%s err=%s", uid, action, e, exc_info=True)
        return None, "选股服务异常: %s" % e
    if res.n_universe <= 0:
        # 降级必须可见(铁律2): 不提供名单 + 明示原因, 而不是静默返回空列表
        log.warning("名单源无数据 → 不提供名单 uid=%s action=%s pick_mode=%s errors=%s",
                    uid, action, res.mode, res.errors[:3])
        msg = "；".join(res.errors[:2]) or "名单源无数据"
        return None, "%s(%s)" % (res.mode_label or "当前时段", msg)
    log.info("选股 uid=%s action=%s pick_mode=%s 全市场%d 候选%d 入选%d 源=%s "
             "降级=%s 剔除=%s 耗时%dms",
             uid, action, res.mode, res.n_universe, res.n_candidate, len(res.items),
             ",".join(res.sources), res.degraded, res.stats, res.elapsed_ms)
    return res.items, None


def _safe_zt_codes():
    try:
        v = fetcher.get_yesterday_zt_codes()
        return set(v) if v else None
    except Exception:
        return None

router = APIRouter()


def _apply_kpl_board(result, log_tag=""):
    """用开盘啦概念覆盖选股结果 concept(统一走 kpl.apply_board_concept 双层覆盖)
    result: 选股结果名单, 每项含 code/concept"""
    kpl.apply_board_concept(result, log_tag)


# 快照候选池粗筛(2026-09-07 主人方案: 盘后 filter 不拉实时全市场, 直接用 9:25 定格快照表):
# 与 picker.filter.apply_filters 同标准的"快照字段可判定"部分(市值/竞价额/涨幅/板块/ST/昨涨停),
# 价格/停牌/概率等快照无字段的过滤留给点查行情后的 picker.filter.apply_filters(候选集小, 成本低)。
_SNAP_CANDIDATE_MAX = 200    # 候选上限: 点查一批够覆盖, 防极端参数下 URL 过长
# ★ 2026-09-23 主人指令: 120 → 200, 与 picker.filter.COARSE_MAX **必须同值同改**。
#   依据同 filter.py 的实测: 松参数下平均 143.6 只/日、20 日里 13 日触顶 ⇒ 120 是
#   真实瓶颈。两条链路(实时 /api/stocks 与盘后快照)任一单独改, 都会在触顶日产生
#   「同一票两个入口判出不同名单」的双口径漂移(2026-09-18 事故)。
#   ⚠️ 代价: 候选变多 ⇒ 点查批次的 URL 变长、请求数上升(这正是当初设 120 的原因),
#   200 是「批次数 / URL 长度」与「不截断」之间的取舍结果。


def _snapshot_candidate_codes(snap_rows, f, yzt_codes, cfg=None):
    """按快照字段粗筛 9:25 全市场快照 → 候选 code 列表(按**粗排分降序**)。
    snap_rows: auction_snapshot.load_snapshot_full() 返回 {code: {...}}
    yzt_codes: 昨日涨停/连板 code 集合(limitUp 未勾选时用于剔除, 替代快照缺的 concept)

    ── 排队键(2026-09-23 主人拍板, 已实测) ──────────────────────────────
    由「竞价额降序」改为「定格三因子粗排分降序」, 与 picker.filter.coarse_filter
    **同一把尺子**(score.coarse_rank_score) —— 两条链路任一单独改键, 就会重演
    2026-09-18「两个入口口径漂移 → 同一票在两处判出不同名单」的故障。
    同分按 code 升序(与 score.score_rows 并列规则一致)。名额 2026-09-23 由 120 上调为
    _SNAP_CANDIDATE_MAX=200(与 picker.filter.COARSE_MAX 同值, 见该常量注释)。
    """
    from ..services.picker.contract import QuoteRow
    from ..services.picker.score import coarse_rank_score, coarse_rank_key
    if cfg is None:
        cfg = scorer.get_scoring_cfg()
    codes = []
    for code, v in snap_rows.items():
        name = v.get("name") or ""
        # 板块(与 picker.filter.in_markets 同标准)
        if not scorer._in_markets(code, f.get("markets") or []):
            continue
        # ST 剔除(默认 stSuspend=False → 剔 ST, 语义同 picker.filter.apply_filters)
        if not f["stSuspend"] and scorer.is_st(name):
            continue
        # 昨涨停/连板剔除(limitUp 未勾)
        if not f["limitUp"] and (code in yzt_codes):
            continue
        # 竞价涨幅 > bidGt 剔除(与 picker.filter.apply_filters 同: 保留 ≤ bidGt)
        if (v.get("bid_change") or 0) > f["bidGt"]:
            continue
        # 市值门槛(2026-09-20 主人拍板: 所有流通市值改**自由流通市值**)
        #   取值口径 = picker.QuoteRow.mv —— free_mv 优先, 缺失回退 float_mv。
        #   与 picker.filter.apply_filters/coarse_filter 及 score.py 的 market 因子**完全同口径**,
        #   不再出现"评分用 free_mv、此处门槛用 float_mv"的双口径漂移。
        #   历史教训(曾用于反面参考): 2026-09-18 之前此处优先 free_mv 而 picker 用 float_mv
        #   口径相反 → 同一票在两个入口判出不同名单; 现两处统一走 mv, 该矛盾根除。
        #   市值未知(0/None) → **放行**给 downstream(点查补丁会补真值, 同 coarse_filter 纪律)。
        mv = float(v.get("free_mv") or v.get("float_mv") or 0.0) / 1e8
        if mv > 0:
            if mv < f["floatMvFloor"]:
                continue
            if f["floatMvGt"] > 0 and mv > f["floatMvGt"]:
                continue
        # 竞价额(9_25 定格, 万元; 与 day_bid_amt 同口径)
        if (v.get("bid_amt") or 0) < f["bidAmtFloor"]:
            continue
        # 排队分: 走契约行构造, 保证「竞价换手率回退口径 / 市值口径」与
        # picker 侧逐字相同(手写等价算式是漂移的温床)。
        codes.append((code,
                      coarse_rank_score(QuoteRow.from_snapshot(v), cfg)))
    codes.sort(key=lambda x: coarse_rank_key(x[1], x[0]))
    return [c for c, _ in codes[:_SNAP_CANDIDATE_MAX]]


def _snapshot_rows_to_raw(snap_rows, codes):
    """点查失败降级: 用 9:25 定格快照行直接构造 scorer 最小可行行情行(2026-09-08 方案 A)。

    背景: 快照候选池的点查(fetch_raw_by_codes, 东财 ulist)断连失败时, 原逻辑降级
    ensure_cache 实时拉全市场 → 双 worker 缓存不一致+28 页拉取 → 同条件名单波动、
    大跌票「有概率」混入(9/8 早 Felix518 #8395/#8396 即此降级所致, 日志实锤
    Remote end closed connection without response)。主人拍板方案 A: 失败不再拉
    实时全市场, 直接以 9:25 定格快照行出名单 — 候选固定 → 名单幂等干净。
    快照行字段(bid_change/bid_amt/float_mv)齐全, 足够 picker.filter.apply_filters 的过滤核心
    (竞涨/竞额/市值/板块/ST/昨涨停); 缺失的实时字段(现价/量比/异动)给保守默认,
    使 prob/conf 取保守档(不入双低剔除), 停牌判断不被误伤(9:25 有竞价额的行必非停牌)。
    """
    raw = []
    for code in codes:
        v = snap_rows.get(code)
        if not v:
            continue
        # f616: 行情口径为元(get_bid_amt 内部 /10000 → 万元), 快照 bid_amt 已是万元
        # (窗口外 bidAmt 一律以 9:25 定格 map 为准, 此字段仅窗口内路径兜底)
        raw.append({
            "f12": code,
            "f14": v.get("name") or "",
            "f615": v.get("bid_change") or 0.0,        # 竞价涨幅(9:25 定格)
            "f616": (v.get("bid_amt") or 0.0) * 10000.0,
            "f21": v.get("float_mv") or v.get("free_mv") or 0.0,   # 流通市值(元, 与行情 f21 同构)
            "f117": v.get("free_mv") or 0.0,           # 自由流通(元, f21 兜底列)
            "f2": 0.0,    # 现价未知 → priceGt(≤300)不过滤、竞价换手=0(保守)
            "f3": 0.0,    # 现涨未知 → 昨日涨幅分 0(保守)、realChange 展示 0
            "f4": 0.01,   # >0 防 is_suspended 误判(快照有 bid_amt 即非停牌)
            "f5": 1.0,    # >0 防 is_suspended 误判
            "f17": 0.0, "f18": 0.0,                    # 今开/昨收未知 → 一字/实体 0
            "f8": 0.0, "f10": 0.0, "f630": 0,          # 换手/量比/异动 保守
            "f100": "-", "f102": "-", "f103": "-",     # 概念由 _apply_kpl_board 覆盖
        })
    return raw


# ---- 盘中主力净额列(2026-09-20 主人拍板): 猫爪 fundflow_kp.main_net_amount ----
# 盘中每分钟更新(KPL 分钟 Worker) → 按 code 缓存 5 分钟(主人确认用量级 ≈48 次猫爪调用/日,
# 前端 30s 轮询在缓存窗口内零成本); **评分不用它**(9:25 定格时无此数据), 纯展示列。
_main_net_cache: dict = {}          # code -> (ts, 主力净额元 or None)
_MAIN_NET_TTL = 300.0


def _trading_hours_now() -> bool:
    """是否工作日盘中(9:30-15:00 北京时间): 非该时段不拉主力净额,
    避免最近交易日收盘值冒充"今日"误导(周末/盘后打开页面列显示 '-')。"""
    t = time.gmtime(time.time() + 8 * 3600)
    if t.tm_wday >= 5:
        return False
    hm = t.tm_hour * 60 + t.tm_min
    return 9 * 60 + 30 <= hm < 15 * 60


def _main_net_map(codes):
    """批量取盘中主力净额(元; 猫爪 fundflow_kp.main_net_amount), code 级 300s 缓存。

    _fill_main_net(展示列)与 _apply_intraday_ff_bonus(动态分)共用一条缓存 ——
    前端 30s 轮询两处取数合并, 缓存窗口内零额外猫爪调用。
    拉取失败返回已命中部分(不写负缓存, 下轮自然重试), 绝不抛异常。
    """
    now = time.time()
    out, need = {}, []
    for c in codes:
        c = str(c or "")
        if not c or c in out:
            continue
        hit = _main_net_cache.get(c)
        if hit is not None and now - hit[0] < _MAIN_NET_TTL:
            if hit[1] is not None:
                out[c] = hit[1]
        else:
            need.append(c)
    if not need:
        return out
    try:
        fmap = meoz_client.fundflow_map(need)
    except Exception as e:                                         # noqa: BLE001
        log.warning("主力净额拉取失败(本次返回已缓存部分, 5分钟后重试) err=%s", e)
        return out
    for c in need:
        r = fmap.get(c) or {}
        try:
            net = r.get("main_net_amount")
            val = None if net in (None, "", "-") else float(net)
        except (TypeError, ValueError):
            val = None
        _main_net_cache[c] = (now, val)
        if val is not None:
            out[c] = val
    return out


def _fill_main_net(lst):
    """给名单行补 mainNet(主力净额, 亿元, 猫爪 fundflow_kp.main_net_amount)。

    * 数据走 _main_net_map(code 级 300s 缓存): 不同用户名单差异不会互相打爆猫爪额度;
    * 独立降级: 拉取失败列留空(前端显示 '-'), 5 分钟后自然重试, 绝不影响行情与名单;
    * 仅盘中拉取(见 _trading_hours_now)。
    """
    if not lst or not _trading_hours_now():
        return
    nmap = _main_net_map([it.get("code") for it in lst])
    if not nmap:
        return
    n = 0
    for it in lst:
        val = nmap.get(str(it.get("code") or ""))
        if val is not None:
            it["mainNet"] = round(val / 1e8, 3)
            n += 1
    log.info("主力净额补齐 %d/%d 只", n, len(lst))


# ---- 盘中主力净流入动态异动分(2026-09-20 主人四连拍板) --------------------------
# 「开盘后主力持续净流入的加分 · AI竞价选股放开到10点 · 10点之前不再锁定」:
#   ① 9:30-10:00 评分动态: 用盘中实时主力净额(fundflow_kp.main_net_amount)重算
#      异动分 17% 的净额层, 档位取 **max(竞价档, 盘中档)** —— 只加不减(盘中流出
#      不倒扣, 保护竞价时的好分); 总分修正 = w_warn × Δwarn × 100, clamp(5,95)。
#   ② 只重排不增票: 名单成员保持 9:25 定格, 仅修正分数并按新分降序重排。
#   ③ 10:00 冻结: 动态分写 settings(intraday_ff_scores_{date}) 作为当日最终分;
#      10:00 后读缓存(10:00 前无人访问 → 首次请求现算并冻结, 净额取值与 10:00 时点
#      差异极小)。冻结缓存按日期隔离, 不跨日复用。
#   ④ 批次落库值**不动**(历史回看保持 9:25 定格分), 本层只改 **API 返回层**。
#   数据源: _main_net_map(code 级 300s 缓存, 与主力净额列共享) + bid_strength
#   (定格三层输入, AI 层当日推理缓存命中近零开销)。独立降级: 任何异常 → 原样返回。
_INTRA_DYN_FROM = 9 * 3600 + 30 * 60        # 09:30:00 动态窗口起(开盘)
_INTRA_DYN_TO = 10 * 3600                   # 10:00:00 动态窗口止(冻结点, 主人拍板)
_INTRA_FF_SWITCH = "intraday_ff_bonus"      # 开关(默认开; 异常时后台置 0 即时回滚)
_intra_ff_write_ts = 0.0                    # 动态窗口内冻结缓存写入节流(60s)


def _intraday_ff_on() -> bool:
    v = settings.get(_INTRA_FF_SWITCH, 1)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _bj_secs_wday():
    """北京当前 (当日秒, 星期 0=周一)。"""
    t = time.gmtime(time.time() + 8 * 3600)
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec, t.tm_wday


def _intraday_ff_active_window():
    """动态层是否应工作: 交易日 且 当日秒 ≥ 09:30:00。
    返回 (bool, 当日秒)。10:00 前实时跟随盘中净额, 10:00 后走冻结缓存。"""
    secs, wday = _bj_secs_wday()
    return (wday < 5 and secs >= _INTRA_DYN_FROM), secs


def _intraday_ff_compute(lst, today):
    """现算盘中动态分 → {code: 修正后总分(int)}。失败抛异常(调用方降级定格分)。

    算法(每票): warn_static = score_one(定格三层输入);
               warn_live   = score_one_live_ff(同输入 + 盘中实时净额占比)  # ff 档 max
               新分 = clamp(probability + w_warn × (warn_live − warn_static) × 100, 5, 95)
    只对「有快照强度信号 + 有盘中净额 + 自由流通市值已知」的票修正, 其余保持定格。
    """
    from ..services import bid_strength
    from ..services.picker import mode as pmode
    # 名单=当日 9:25 定格 → 当日快照必须存在; 缺失(异常日)不叠加, 防跨日错位打分
    if not auction_snapshot.has_today_snapshot(today):
        log.info("盘中动态分跳过: 当日 %s 无 9_25 快照(异常日/回退)", today)
        return {}
    codes = [str(it.get("code") or "") for it in lst if it.get("code")]
    strengths = bid_strength.load(codes, date=today)
    if not strengths:
        return {}
    nmap = _main_net_map(codes)          # 与主力净额列共享缓存(通常全命中)
    cfg = scorer.get_scoring_cfg()
    try:
        w_warn = float(cfg.get("w_warn") or 0.17)
    except (TypeError, ValueError):
        w_warn = 0.17
    out = {}
    for it in lst:
        code = str(it.get("code") or "")
        st = strengths.get(code)
        # base 必须取 **9:25 定格分**: 优先取行内定格基准(_prob_static, 首次应用时记录),
        # 防同一行被二次应用时把已加过分 probability 当 base → 分数逐轮爬升(非幂等)。
        base = it.get("_prob_static")
        if base is None:
            base = it.get("probability")
        if st is None or base is None:
            continue
        try:
            base = float(base)
        except (TypeError, ValueError):
            continue
        warn_static = bid_strength.score_one(st, cfg)
        if warn_static is None:
            continue
        ff_live = None
        net = nmap.get(code)
        if net is not None and st._free_mv:                            # noqa: SLF001
            ff_live = float(net) / float(st._free_mv) * 100.0
        warn_live = bid_strength.score_one_live_ff(st, ff_live, cfg)
        bonus = w_warn * (warn_live - warn_static) * 100.0             # ≥0(max 只加不减)
        if bonus <= 0:
            continue
        out[code] = int(max(5.0, min(95.0, base + bonus)) + 0.5)
    return out


def _apply_intraday_ff_bonus(lst):
    """把盘中动态分应用到名单(原地): 修正 probability 并按新分降序重排。

    9:30-10:00 实时跟随盘中净额(60s 节流写冻结缓存); ≥10:00 只读冻结缓存
    (缓存缺失 → 现算一次并冻结)。应用语义是**绝对赋值**(非增量) → 重复调用幂等。
    任何异常静默降级(名单保持定格分), 绝不影响名单可用性。
    """
    if not lst or not _intraday_ff_on():
        return
    active, secs = _intraday_ff_active_window()
    if not active:
        return
    from ..services.picker import mode as pmode
    today = pmode.bj_date()
    ck = "intraday_ff_scores_" + today
    global _intra_ff_write_ts
    frozen = None
    if secs >= _INTRA_DYN_TO:
        try:
            frozen = settings.get(ck)
        except Exception as e:                                     # noqa: BLE001
            log.warning("盘中动态分冻结缓存读取失败(现算) err=%s", e)
    if frozen is None:
        # 现算前记录定格分基准(_prob_static): 同一行重复应用时 base 恒为定格分 → 幂等
        for it in lst:
            if it.get("_prob_static") is None and it.get("probability") is not None:
                it["_prob_static"] = it["probability"]
        try:
            frozen = _intraday_ff_compute(lst, today)
        except Exception as e:                                     # noqa: BLE001
            log.warning("盘中净流入动态分计算失败(降级定格分) err=%s", str(e)[:200])
            return
        # 动态窗口内 60s 节流写; ≥10:00 首算必写(冻结语义, 之后恒读缓存)
        if secs >= _INTRA_DYN_TO or time.time() - _intra_ff_write_ts >= 60:
            try:
                settings.set(ck, frozen)
                _intra_ff_write_ts = time.time()
            except Exception as e:                                 # noqa: BLE001
                log.warning("盘中动态分冻结缓存写入失败(不影响本次返回) err=%s", e)
    n = 0
    for it in lst:
        # 注意: _prob_static 基准键**保留不删** —— 同一行被二次应用时(如 calc 缓存对象
        # 复用)base 仍取定格分; 前端按名取字段, 内部键自然忽略, 落库/缓存均在叠加前完成。
        v = (frozen or {}).get(str(it.get("code") or ""))
        if v is None:
            continue
        try:
            it["probability"] = int(v)
            n += 1
        except (TypeError, ValueError):
            pass
    if n:
        lst.sort(key=lambda x: -(x.get("probability") or 0))
        log.info("盘中净流入动态分应用 %d/%d 只(%s)", n, len(lst),
                 "10:00已冻结" if secs >= _INTRA_DYN_TO else "动态窗口内")


def _maybe_intraday_overlay(result):
    """重算路径的动态分叠加: 返回**新 list**(行浅拷贝后改), 原 result 不动 ——
    落库批次/refresh 计算缓存保持 9:25 定格分(历史回看与缓存语义不变)。"""
    try:
        if not result:
            return result
        active, _ = _intraday_ff_active_window()
        if not active:
            return result
        lst = [dict(it) for it in result]
        _apply_intraday_ff_bonus(lst)
        return lst
    except Exception as e:                                         # noqa: BLE001
        log.warning("盘中动态分叠加异常(返回定格分) err=%s", e)
        return result


# ---- 连板高度标签(2026-09-23 主人拍板 P0: **只展示**, 不参与筛选/排序/评分/落库) ----
# 口径 = 「买入前一日」该股的真实连板数: 0=前一日未涨停, 1=首板, 2=二板, ... 5=五板及以上。
#
# 数据源 `kpl.real_limit_days(上一交易日)`(东财 flash 涨停池 limit_up_days, 60s 缓存):
# 语义唯一、与当日无关, 盘中恒定。
#
# 🔴 为什么不用组件里**已经有**的 `limitBoards`(开盘啦竞价榜 row[16])省一次请求:
#    实测它 = 昨日连板 + (当日是否已封板 ? 1 : 0), 是**当前时点**连板数, 盘中随封板实时 +1。
#    2026-09-23 拿 28 只对拍, 差值只有 0 / +1: 差 +1 的 7 只**全部在当日涨停池**且数值与
#    当日连板数逐只相等, 差 0 的 9 只**全部不在**当日涨停池。
#    ⇒ 直接拿它当"昨日连板高度"渲染, 9:30 后会对当日封板的票**多报 1 板**(前视形态)。
#    若将来想省这次请求, 唯一合规做法是与名单**同一 9:25 时点**一起截取落库, 绝不盘中现读。
#
# 🔴 基准日取 `_freeze_fields().freezeDate`(该名单对应的**定格日**)的前一交易日, 不是"今天
#    的前一交易日": 盘前/非交易日/回退最近交易日时, 页面给的是**上一交易日**的名单
#    (见 StockView 的 freeze-notice), 标签必须跟着名单的日期走, 否则整列错一档。
def _prev_trade_day_before(d):
    """给定日期 YYYY-MM-DD 的**上一交易日**: snapshot_bid 里 < d 的最近一天(自动跳节假日);
    查不到则按日历跳周末降级 —— 与 kpl._prev_trade_day 同源同策略, 只是可指定基准日。"""
    if d:
        try:
            import sqlite3
            from ..core import config
            conn = sqlite3.connect(config.DB_FILE)
            row = conn.execute(
                "SELECT MAX(date) FROM snapshot_bid WHERE date < ?", (d,)).fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception as e:                                     # noqa: BLE001
            log.warning("上一交易日查询失败(降级日历) 基准日=%s err=%s", d, e)
    from datetime import datetime, timedelta
    try:
        cur = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1) if d else None
    except Exception:                                              # noqa: BLE001
        cur = None
    if cur is None:
        cur = datetime.now() - timedelta(days=1)
    while cur.weekday() >= 5:      # 跳周末(法定节假日由 snapshot_bid 路径覆盖)
        cur -= timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def _fill_lb(lst, ref_date=None):
    """给名单行补 `lb`(买入前一日真实连板数, int, 0=前一日未涨停) 与 `lbDate`(该数据取自哪天涨停池)。

    独立降级(与 _fill_main_net 同原则): 涨停池取不到 → 整列不打标 + WARNING 日志,
    **绝不影响名单本身**(前端 `lb` 缺失即不渲染标签, 名单照常可复现)。

    幂等: 同一天内 60s 缓存命中, 重复调用不会重复打网。
    """
    if not lst:
        return
    try:
        if not ref_date:
            ref_date = (_freeze_fields() or {}).get("freezeDate")
        prev = kpl._prev_trade_day() if not ref_date else _prev_trade_day_before(ref_date)
        m = kpl.real_limit_days(prev) or {}
    except Exception as e:                                         # noqa: BLE001
        log.warning("连板高度标签取数失败(名单照常返回, 标签留空) err=%s", e)
        return
    if not m:
        log.info("连板高度标签: 上一交易日 %s 涨停池为空/不可用, 本次不打标(不影响名单)", prev)
        return
    hit = 0
    for it in lst:
        v = m.get(str(it.get("code") or ""))
        it["lb"] = int(v or 0)
        it["lbDate"] = prev
        if v:
            hit += 1
    log.info("连板高度标签 %d/%d 只(名单定格日=%s → 取数日=%s)",
             hit, len(lst), ref_date or "-", prev)


def _fill_spot_fields(lst, fs):
    """用全市场实时行情覆盖名单的**展示字段**(现价/现涨/实体/量比/换手)。

    2026-09-11 抽取: 原先只有 refresh 的两条直读分支做这件事, lock 当日幂等直读分支
    漏了 → 「9:25-9:30 前端走 lock 直读」时原样回吐落库值, 把"未知"当 0 显示
    (batch_stocks.real_change NOT NULL, 落库 None 被 _safe_num 兜成 0)。

    语义边界(与模式层「9:30 后名单固定, 只更新实时行情」一致):
      * 只覆盖展示字段 —— 不增删票、不改 probability/confidence 与排序;
      * 覆盖值取不到(None)时保留原值, 不用 None 抹掉已有数据;
      * 行情拉取失败静默保留原值(下轮自愈), 不影响名单。
    """
    if not lst:
        return lst
    # 连板高度标签(2026-09-23 P0): 必须放在**行情拉取之前** —— 本函数在行情失败时有多个
    # 提前 return, 放后面会漏标。它与行情源完全独立(取涨停池), 互不影响。
    try:
        _fill_lb(lst)
    except Exception as e:                                         # noqa: BLE001
        log.warning("连板高度标签填充异常(已忽略) err=%s", e)
    # 主力净额列(2026-09-20): 独立降级 —— 在行情拉取**之前**处理, 行情源挂了不影响它
    try:
        _fill_main_net(lst)
    except Exception as e:                                         # noqa: BLE001
        log.warning("主力净额列填充异常(已忽略) err=%s", e)
    # 盘中净流入动态分(2026-09-20 主人拍板「10点前不再锁定」): 修正 9:30-10:00 的
    # 评分/排序(只加不减), ≥10:00 走冻结缓存。独立降级, 失败保持定格分。
    try:
        _apply_intraday_ff_bonus(lst)
    except Exception as e:                                         # noqa: BLE001
        log.warning("盘中动态分叠加异常(已忽略) err=%s", e)
    try:
        spot_map = fetcher.fetch_spot_quote_map(fs)
    except Exception as e:
        log.warning("实时行情覆盖失败(降级: 保留落库原值) fs=%s err=%s", fs, e)
        return lst
    if not spot_map:
        return lst
    n = 0
    for it in lst:
        rt = spot_map.get(it.get("code"))
        if not rt:
            continue
        for k in ("price", "realChange", "entityChange", "volRatio", "turnover"):
            v = rt.get(k)
            if v is not None:
                it[k] = v
        n += 1
    log.info("直读名单实时行情覆盖 %d/%d 只 fs=%s", n, len(lst), fs)
    return lst


@router.get("/api/stocks")
def api_stocks(request: Request, uid: int = Depends(get_uid)):
    q = qs(request)
    action = (q.get("action") or ["filter"])[0]
    # 2026-09-09 命名消歧(主人指示彻底改名): 策略参数 mode → strategy ——
    # 原 mode 与内部时段模式 PickMode(preopen/auction/locked/intraday/closed) 撞名,
    # 排查时极易误读(曾把回显的策略 mode=auction 当成"午休仍在竞价窗口")。
    # 语义: strategy=选股策略(2026-09-09 起仅 auction 竞价因子表; spot 盘中已下线)。
    # 旧参数 mode 保留为兼容别名(线上缓存前端/书签仍在传), 下版本移除。
    strategy = (q.get("strategy") or q.get("mode") or ["auction"])[0]
    force = (q.get("force") or ["0"])[0] in ("1", "true", "True")   # 主动重锁(绕过当日幂等)
    if action not in ("lock", "filter", "refresh", "ping"):
        log.warning("选股非法参数 action=%s uid=%s", action, uid)
        return jr({"ok": False, "msg": "非法参数"}, 400)
    if strategy != "auction":
        log.warning("选股非法参数 strategy=%s uid=%s(盘中实时选股已于 2026-09-09 下线)", strategy, uid)
        return jr({"ok": False, "msg": "非法参数 strategy(仅支持 auction)"}, 400)
    if action == "ping":
        _, _, before930 = scorer.bj_now()
        # 2026-09-17: 把开关状态透给前端 —— 此前前端置灰是**纯时间判断**、不看开关,
        #   于是 `pick_window_guard=0` 只关了后端, 前端依旧置灰且连自动加载都不发请求
        #   (9/17 该时段 0 请求的根因), 用户完全点不动。
        #   ping 在鉴权之后、闸门之前, 天然不受闸门影响, 适合做状态探测。
        return jr({"ok": True, "before930": before930,
                   "pickGateEnabled": _pick_window_guard_on()})

    # ---- 选股闸门 v3(2026-09-17 重做) ----
    # ping 在其之前 return(前端登录态/时段探测天然放行); 其余 action 一律过闸门。
    # ok=False + blocked=True 走 request.js 的既有错误透传(Object.assign(e, data)),
    # 前端据此显示"等待定格"提示而非"选股失败"。
    if _pick_window_guard_on():
        block_msg = _pick_blocked_reason()
        if block_msg:
            log.info("选股闸门拦截 uid=%s action=%s msg=%s", uid, action, block_msg)
            return jr({"ok": False, "blocked": True, "msg": block_msg,
                       "blockedUntil": _pick_blocked_until(),
                       "list": [], "count": 0})

    f = scorer.validate_filters(q)
    fs = scorer.market_fs(f["markets"])
    _, _, before930 = scorer.bj_now()
    t0 = time.time()

    try:
        # ---- 竞价模式 ----
        # 2026-09-02 当日幂等(主人确认): 9:30 前页面自动 lock 若当日已存在
        # "9:25 后落库 + 同筛选参数"的手动 lock 批次 → 直读批次返回,
        # 不再全量重拉行情/重复落库/重复推送(解决"每次重新登录都重算一次+堆 lock 历史")。
        # 仅拦自动 lock(force=0): 9:25 前数据未定型仍每次重算; 用户主动点「锁定」(force=1)
        # 或参数已改 → 正常重算。命中返回结构含 idempotent=True 供前端识别。
        if (action == "lock" and before930 and strategy == "auction" and not force):
            try:
                lock_b = history.find_today_lock_matching(uid, f)
                if lock_b:
                    lst = history.get_batch_stocks_mapped(lock_b["id"])
                    # 2026-09-11: 直读批次必须补实时行情覆盖(与下方 refresh 直读分支同口径)。
                    # 根因: batch_stocks.real_change 列是 NOT NULL, 落库时 history._safe_num 把
                    #   "未知"(None) 兜成 0 → 原样回吐会让前端把"未知"显示成 0.00%
                    #   (9/11 生产现涨全 0 事故)。这里用全市场实时行情把现存/现涨/实体/量比/换手
                    #   刷成真值; 只覆盖展示字段, 不增删票、不改评分 → 幂等语义不变。
                    _fill_spot_fields(lst, fs)
                    log.info("选股lock当日幂等 uid=%s batch=%s 直读%d只(跳重拉/落库/推送)",
                             uid, lock_b["id"], len(lst))
                    return jr({
                        "ok": True, "strategy": "auction", "mode": "auction", "list": lst,
                        "count": len(lst), "before930": True,
                        "dataTime": int(lock_b.get("ts") or time.time()),
                        "idempotent": True, "batch_id": lock_b["id"],
                        **_freeze_fields(),
                    })
            except Exception as e:
                log.warning("lock 当日幂等查询失败(降级正常重算) uid=%s err=%s", uid, e)
        # 2026-09-04 9:30 后直读(主人方案「打开/刷新直接取历史最新, 不再每次重算」):
        # 9:30 后名单定型(当日 lock / 9:26 系统批次已落库), 页面打开与 30s 轮询的 refresh
        # 若筛选参数未变 → 直读当日可复用批次(find_today_reusable_batch: lock→filter→auto)
        # + 全市场实时行情(spotMap, 60s TTL 独立缓存)覆盖现价/涨幅返回。
        # 跳过 ensure_cache 全市场拉取(30s TTL 恰与前端轮询同周期, 每次 miss 锁内拉腾讯
        # 5548只, 多用户排队 = 加载慢/长尾根因)与全市场评分/板块外网覆盖。
        # 名单冻结(评分用定格值, 不再盘中漂移), 行情照常实时; 参数已改/当日无批次才重算。
        if (action == "refresh" and not before930 and strategy == "auction"):
            try:
                reuse_bid, reuse_src = history.find_today_reusable_batch(uid, f)
                reuse_date = None
                if not reuse_bid:
                    # 2026-09-17 事故修复(主人拍板「先修④」): 当日名单全部 miss 时,
                    # **先试当日系统统一名单**, 再考虑跨日回退。
                    # 根因: 用户当日点过却拿到空名单批次(当日评分被压到 scoreFloor 之下 →
                    # 候选=7 入选=0)时, 当日版 ①② 因 stock_count=0 跳过、③ 被 `if not rows`
                    # 挡住 → 直落跨日回退 → **交易日却显示昨日名单**(9/17 实测累计 756 次,
                    # 383 次集中在 09:30-10:00, 正是主人反馈"9:30 后出来的像是昨天的")。
                    # 当日已有系统名单时它一定优于任何昨日名单; 语义是"当日兜底"(与参数指纹
                    # 无关), 故 reuse_date 保持 None → 前端不会误提示"这是历史名单"。
                    reuse_bid, reuse_src = history.find_today_system_batch()
                    if reuse_bid:
                        log.info("选股refresh当日无可用批次→回退当日系统统一名单 uid=%s batch=%s",
                                 uid, reuse_bid)
                if not reuse_bid:
                    # 2026-09-05 主人需求(多用户反馈): 休市时间/当日无批次时回退
                    # **最近交易日**同参批次直读 —— 关闭平台后再打开, 首页直接显示
                    # 关闭前选出的股, 不再全市场重算转圈。参数不一致(改过条件)仍重算。
                    reuse_bid, reuse_src, reuse_date = history.find_recent_reusable_batch(uid, f)
                    if reuse_bid:
                        log.info("选股refresh当日无批次→回退最近交易日直读 uid=%s src=%s "
                                 "batch=%s date=%s", uid, reuse_src, reuse_bid, reuse_date)
                if reuse_bid:
                    lst = history.get_batch_stocks_mapped(reuse_bid)
                    # 2026-09-08 双保险(主人反馈"刷新无数据"): 即便批次非空(stock_count>0),
                    # 明细仍可能为空(落库异常/明细被清理) → 空名单不得直读返回, 视为未命中
                    # 继续走下方重算, 避免页面空白(空名单 ≠ 有效定格名单)。
                    if not lst:
                        log.warning("选股refresh直读批次为空名单, 放弃直读改重算 uid=%s "
                                    "batch=%s src=%s", uid, reuse_bid, reuse_src)
                        reuse_bid = None
                    # 定格评分(probability/confidence/bidChange/bidAmt...) + 实时行情覆盖
                    # (price/realChange/entityChange/volRatio/turnover): 前端 merge 在榜分支
                    # 用 lt.price/realChange 覆盖, 评分定格不回拨
                    _fill_spot_fields(lst, fs)
                    log.info("选股refresh直读批次 uid=%s src=%s batch=%s 返回%d只(跳全市场重拉/评分)",
                             uid, reuse_src, reuse_bid, len(lst))
                    return jr({
                        "ok": True, "strategy": "auction", "mode": "auction", "list": lst,
                        "count": len(lst), "before930": False,
                        "dataTime": int(time.time()),
                        "reused": True, "source": reuse_src, "batch_id": reuse_bid,
                        "reusedDate": reuse_date,   # 回退最近交易日时非 None, 前端据此提示
                        **_freeze_fields(),
                    })
            except Exception as e:
                log.warning("refresh 直读批次失败(降级正常重算) uid=%s err=%s", uid, e)
        # 2026-09-04: refresh 全量重算**结果**缓存(只有无当日批次的用户才会走到这里 —
        # 有批次用户已在上面 115 行直读返回)。无批次场景(新号 / 当日系统批次为空)
        # 每次 ensure_cache 全市场 + 昨日K线 + 快照 + 全市场评分 ≈ 2.6s(测试机实测),
        # 前端 30s 轮询会反复命中这条慢路径。
        # 名单 + 评分按 (uid, fs, 参数指纹) 缓存 60s: 参数一改 key 就变 → 正常重算;
        # 实时价格由下方 spot_map 每次现拉、前端 merge 覆盖, 故缓存**不影响价格实时性**。
        # TTL 必须 > 前端 30s 轮询(TTL≤轮询周期 ⇒ 命中率≈0, auction-overview 已踩过)。
        ck_full = None
        if action == "refresh" and not before930 and strategy == "auction":
            ck_full = "stocks_refresh:%s:%s:%s" % (
                uid, fs, history._canon_filter_fingerprint(f))
            hit = _cstore.get(ck_full)
            if hit is not None:
                _fill_spot_fields(hit, fs)
                log.info("选股refresh命中计算缓存 uid=%s 返回%d只(跳全市场重拉/评分)",
                         uid, len(hit))
                return jr({
                    "ok": True, "strategy": "auction", "mode": "auction", "list": hit,
                    "count": len(hit), "before930": before930,
                    "dataTime": int(time.time()),
                    "reused": True, "source": "calc_cache",
                    **_freeze_fields(),
                })
        # 评分筛选: 沿用原逻辑(9:30 前 lock 强制, refresh/filter 走 TTL 缓存)
        # 2026-09-07 主人方案「候选池=全市场, 且直接读快照不实时拉」:
        #   窗口外(9:30 后/盘后/休市) filter 用当日 9:25 定格快照表(已自动采集)粗筛
        #   → 候选几十只按 code 点查完整行情(fetch_raw_by_codes, 东财 ulist)
        #   → 原 scorer 全流程评分过滤。替代原 ensure_cache: ①原 Top200 涨幅榜与
        #   「涨幅≤7%」反向错配(默认只出 5 只) ②实时拉全市场 28 页(打数据源)。
        #   2026-09-08 方案 A+: 东财点查失败**先切腾讯按 code 点查**(名单仍=快照池固定,
        #   实时字段补真实, 过滤不虚胖) — 腾讯也失败才降级 9:25 快照行直出(保名单非空);
        #   全程不再降级实时全市场(双 worker 缓存不一致+28 页拉取 = 名单波动+大跌票有
        #   概率混入的根因)。窗口内(9:15-9:30 实时竞价)/快照池整体不可用(空库/DB 故障)
        #   才降级 ensure_cache。
        raw = None
        err = None
        # 2026-09-08 主人核心诉求(同条件名单波动 + 加载慢): 快照候选池适用时段从
        # 「9:30 后(盘后)」扩展至「9:15 前(凌晨/盘前)」— 该时段当日既无 9_25 定格
        # 快照也无实时竞价(东财只回昨日收盘缓存), 原 ensure_cache 实时拉全市场 28 页:
        # ① 双 uvicorn worker 缓存不一致(raw 5548↔5556)+昨日额命中爬坡 → 同筛选参数
        # 名单波动(9/8 07:26-07:29 生产实测同 filters 交集仅 13/30), 边界票进出即
        # 「有概率」混入大跌票; ② 28 页拉取+全量昨日额 → 加载 5-15s。
        # load_snapshot_full 自动回退最近交易日(15 自然日) → 凌晨拿到最近交易日 9:25
        # 定格候选池: 快照不变 → 名单幂等稳定; 点查仅几十只 → 加载 <3s。
        # 9:15-9:30 竞价窗口保持实时: 当日动态竞价(9:25 前涨幅演进/9:25 定格)只能走
        # 实时源。lock 与 filter 同源(9:30 前 lock=研究锁定, 同样受益于幂等名单);
        # lock 仅限 hm<9:15 走快照池 — 9:30 后 lock 仍需 ensure_cache 的
        # 「9:30 后禁止重新选股」业务拒绝, 不得绕过。
        # 2026-09-08 同源修复(主人反馈"刷新无数据, 点应用才有"): 原 refresh 被排除在
        # 快照候选池之外(条件只认 filter/9:15前lock) → 9:30 后无批次可直读时, refresh 走
        # ensure_cache 实时全市场(盘中 f615 无竞价值 → 常被筛成 0 只), 而 filter 走定格
        # 快照池能出几十只 → 同一套筛选条件刷新与应用结果不一致。
        # 按模式层已定语义(INTRADAY: deterministic=True, source_priority 定格优先),
        # 9:30 后 refresh 必须与 filter 同源走快照池: 名单幂等固定 + 刷新也有数据。
        hm = scorer._bj_hm()
        # 2026-09-20 主人拍板「重新选股放开到10点」: 交易日 9:30-10:00 的主动 lock
        # 也走快照池重算(名单源=9:25 定格快照, 评分=物化定格 → 与当日批次同参同名单,
        # 幂等不破坏), raw 非空即不会触达 ensure_cache 的「9:30 后禁止重新选股」拒绝;
        # 2026-09-24 主人拍板「10 点以后也不要锁定」: lock 上限 10:00 → **15:00(收盘)**,
        # 即交易日 9:30-15:00 的主动 lock 均走快照池(名单仍幂等); 15:00 盘后才恢复原拒绝
        # (快照池条件不再命中 → raw=None → ensure_cache 拒绝)。
        use_snapshot_pool = strategy == "auction" and (
                (action == "filter" and (not before930 or hm < 9 * 60 + 15))
                or (action == "lock" and (hm < 9 * 60 + 15
                                          or (not before930 and hm < 15 * 60)))
                or (action == "refresh" and not before930))
        if use_snapshot_pool:
            try:
                snap_rows = auction_snapshot.load_snapshot_full()
                if snap_rows:
                    yzt = set()
                    if not f["limitUp"]:
                        try:
                            yzt = set(fetcher.get_yesterday_zt_codes())
                        except Exception as e:
                            log.warning("昨涨停集合拉取失败(不剔除昨涨停, 概念层兜底) err=%s", e)
                    snap_codes = _snapshot_candidate_codes(snap_rows, f, yzt)
                    if snap_codes:
                        try:
                            raw = fetcher.fetch_raw_by_codes(snap_codes)
                            log.info("选股快照候选池 uid=%s markets=%s 粗筛%d只 点查%d只 "
                                     "(快照表, 不拉全市场)", uid, ",".join(f["markets"]),
                                     len(snap_codes), len(raw))
                        except Exception as e:
                            # 2026-09-08 方案 A+(主人拍板, 接替方案 A): 东财 ulist 断连
                            # → **先切腾讯按 code 点查**(fetch_tencent_by_codes): 名单仍=
                            # 9:25 快照池粗筛候选(幂等固定), 腾讯只把现价/涨幅/今开/换手等
                            # 实时展示字段补真实, 评分字段(竞涨/竞额)窗口外被 9:25 定格
                            # day_bid_change/day_bid_amt map 覆写不受影响 → 过滤不再虚胖
                            # (9/8 早实测: 同参数点查成功 35 只 vs 快照行直出 70 只 —
                            # 直出行 price=0 让 priceGt 失效、confidence 偏高让双低剔除失效)。
                            # 腾讯也失败(网络全挂/腾讯熔断)才最后降级 9:25 快照行直出保名单。
                            try:
                                raw = fetcher.fetch_tencent_by_codes(snap_codes)
                                log.info("选股快照候选池东财点查失败→腾讯点查兜底成功 "
                                         "uid=%s markets=%s 返回%d只", uid,
                                         ",".join(f["markets"]), len(raw))
                            except Exception as e2:
                                log.warning("选股快照候选池东财+腾讯点查均失败, 降级快照行"
                                            "直出名单(不拉实时全市场) uid=%s err=%s",
                                            uid, str(e2)[:150])
                                raw = _snapshot_rows_to_raw(snap_rows, snap_codes)
            except Exception as e:
                # 快照池整体不可用(空库/DB 异常/粗筛异常)才兜底实时全市场 — 与点查网络
                # 抖动(方案 A 快照行直出)区分: 空库场景无快照行可用, 只能实时拉
                log.warning("快照候选池不可用(空库/异常)降级实时全市场 uid=%s err=%s",
                            uid, str(e)[:150])
                raw = None
        if raw is None:
            raw, err = fetcher.ensure_cache(action, fs, before930)
        if err:
            log.warning("选股被拒 uid=%s action=%s strategy=%s err=%s", uid, action, strategy, err)
            return jr({"ok": False, "msg": err}, 403)
        # 昨日成交额(并发拉日K, 当日缓存), 用于计算竞价/昨日成交占比
        yesterday_map = fetcher.fetch_yesterday_amounts([s.get("f12") for s in raw])
        # 2026-09-08 昨日涨幅真实化: 走同一份日K缓存(零额外请求), 供"昨日涨幅"因子评分
        yesterday_chg_map = fetcher.fetch_yesterday_changes([s.get("f12") for s in raw])
        # 9:20 快照(用于 9:25 涨幅加速度); 非竞价时段读库无数据返回空 map
        snapshot_map = auction_snapshot.load_snapshot()
        # 2026-09-03 竞额定格 map(9_25 快照): 竞价/落库 bidAmt 以当日定格竞价额为准,
        # 避免腾讯兜底期把实时成交额当竞额落库/展示
        bid_amt_map = auction_snapshot.load_day_bid_amt()
        # 2026-09-08 竞涨定格 map(9_25 快照): 东财行情 f615 收盘后返回 "-", 评分阶段
        # 窗口外 bidChange 以当日定格竞价涨幅为准(防退 f3 → 竞涨=现涨/涨幅过滤按现价)
        bid_chg_map = auction_snapshot.load_day_bid_change()
        # 竞价上下文日志(排查关键): 窗口状态/快照覆盖/昨日额命中
        auction_ok = scorer.in_auction_window()
        # 2026-09-08: 昨日涨幅命中率入日志 —— 该因子权重 6%, 缺失(default 0.15)与命中
        # (0.4/0.65/0.9)单票概率差 1.5~4.5 分, 是"同参数两次结果 top 票 90↔93"的漂移源。
        # 命中率低 = 本批整体降级, 必须可见(契约铁律2), 否则排查时只能看到分数变化。
        log.info("选股上下文 uid=%s action=%s auction_window=%s 9_20快照=%d只 昨日额命中=%d/%d "
                 "昨涨命中=%d/%d raw=%d只",
                 uid, action, auction_ok, len(snapshot_map), len(yesterday_map), len(raw),
                 len(yesterday_chg_map), len(raw), len(raw))
        # 竞价窗口内 lock 但当日 9:20 快照缺失 → 加速度无法计算, 必须告警(数据过了点无法补采)
        if action == "lock" and auction_ok and not snapshot_map:
            log.warning("9:25 lock 时当日 9:20 快照缺失! 加速度无法计算, 请检查9:20调度/东财接口 uid=%s", uid)
        # 评分计算不持锁: 多用户并发选股互不阻塞, 只共享只读的行情快照
        # (抢筹明细由 pipeline 的 PickContext 内部注入, 见 _run_new_pipeline)
        # 2026-09-09 起: picker.pipeline 是**唯一**选股链路, 不再回退老链路
        # (双轨的存在让新链路缺陷永远暴露不出来 —— 竞价窗口恒返回 0 只跑了一整天
        #  无人发现, 因为 n_universe>0 被判成功、回退根本没触发)。
        result, perr = _run_new_pipeline(uid, action, f, yesterday_map=yesterday_map,
                                         yesterday_chg_map=yesterday_chg_map,
                                         snapshot_map=snapshot_map,
                                         bid_amt_map=bid_amt_map,
                                         bid_chg_map=bid_chg_map)
        if perr:
            return jr({"ok": False, "msg": "选股数据不可用: %s" % perr,
                       "strategy": "auction", "mode": "auction", "list": [], "count": 0})
        # 概念用开盘啦覆盖(落库前覆盖: 页面/历史批次/推送全部统一开盘啦概念)
        _apply_kpl_board(result, "auction")
    except Exception as e:
        log.error("选股处理失败 uid=%s action=%s strategy=%s err=%s", uid, action, strategy, e, exc_info=True)
        return jr({"ok": False, "msg": "服务端处理失败: %s" % e}, 500)

    # 落库: 锁定选股与筛选重算都保存为该用户的历史批次, 实时刷新(refresh)不落库
    # 2026-09-02 同参去重: filter 60s 内相同筛选参数不重复落库(防脚本/手滑刷历史批次);
    # lock 保留原语义(9:30 前唯一锁定+推送), system_batch(auto)/auto_apply 直连不经过此路由
    if action in ("lock", "filter"):
        if action == "filter":
            dup = history.recent_same_filter(uid, f, window=60)
            if dup:
                log.info("选股同参去重 uid=%s markets=%s 60s内重复filter命中批次=%s 跳过落库",
                         uid, ",".join(f["markets"]), dup)
                batch_id = dup
            else:
                batch_id = history.save_batch(uid, action, result, f)
        else:
            batch_id = history.save_batch(uid, action, result, f)
        log.info("选股落库 uid=%s action=%s markets=%s 返回%d只 batch=%s 耗时%.0fms",
                 uid, action, ",".join(f["markets"]), len(result), batch_id, (time.time() - t0) * 1000)
    else:
        log.info("选股刷新 uid=%s action=%s markets=%s 返回%d只 耗时%.0fms",
                 uid, action, ",".join(f["markets"]), len(result), (time.time() - t0) * 1000)

    # 竞价锁定选股成功后, 后台推送结果到微信/飞书(失败不影响选股主流程)
    if action == "lock" and result:
        try:
            notify.push_result_async(result, f)
        except Exception as e:
            log.error("推送触发失败 err=%s", e)
        # 顺带统计当日一字涨停(数量+竞价总额), 失败不影响选股
        try:
            stats.record_daily_yizi(raw)
        except Exception as e:
            log.error("一字涨停统计失败 err=%s", e)

    # 写回 refresh 计算缓存(仅 9:30 后 auction refresh; lock/filter 有落库副作用不缓存)
    if ck_full and result:
        try:
            _cstore.set(ck_full, result, ttl=60)
        except Exception as e:
            log.warning("refresh计算缓存写入失败(不影响本次返回) err=%s", e)

    # 盘中净流入动态分(2026-09-20 主人拍板): 重算路径同样叠加 —— 只改**返回层**,
    # 落库批次与计算缓存保持 9:25 定格分(上面已完成)。返回新 list, 原 result 不动。
    result = _maybe_intraday_overlay(result)

    # 连板高度标签(2026-09-23 P0): 与 _maybe_intraday_overlay 同哲学 —— **只加在返回层**,
    # 落库批次与 refresh 计算缓存保持原样(lb 是展示字段, 不参与筛选/排序/评分/落库)。
    try:
        _fill_lb(result)
    except Exception as e:                                         # noqa: BLE001
        log.warning("连板高度标签填充异常(已忽略) err=%s", e)

    return jr({
        "ok": True,
        "strategy": "auction", "mode": "auction",
        "list": result,
        "count": len(result),
        "before930": before930,
        # 2026-09-08: 快照候选池路径不写 fetcher._cache → 硬取 [fs] 会 KeyError(凌晨
        # 全走快照池后必现)。dataTime 语义=行情数据时间, 兜底用当前时刻即可。
        "dataTime": int((fetcher._cache.get(fs) or {}).get("ts") or time.time()),
        **_freeze_fields(),
    })


@router.get("/api/quotes")
def api_quotes(request: Request, uid: int = Depends(get_uid)):
    """按需实时行情(2026-09-05 B 方案): 前端 9:30 后合并不在返回名单的锁定票时,
    对少量 code 拉实时价; /api/stocks 已不再下发全市场 spotMap, 响应因此大幅瘦身。
    codes = 逗号分隔的 6 位代码(取自名单 item.code, 如 600000,000001)"""
    q = qs(request)
    codes = [c.strip() for c in (q.get("codes") or [""])[0].split(",") if c.strip()]
    if not codes:
        return jr({"ok": False, "msg": "codes 必填"}, 400)
    # 去重 + 限长保护(单个请求避免恶意超长)
    codes = list(dict.fromkeys(codes))[:500]
    try:
        quotes = fetcher.fetch_spot_quotes_by_codes(codes)
    except Exception as e:
        log.warning("按需行情拉取失败 uid=%s err=%s", uid, str(e)[:200])
        quotes = {}
    return jr({"ok": True, "quotes": quotes, "count": len(quotes)})


@router.get("/api/stock/chart")
def api_stock_chart(request: Request, uid: int = Depends(get_uid),
                    code: str = "", period: str = "day"):
    """个股图表(分时/K线): 点击股票弹框用
    code: 股票代码 6位数字
    period: minute 当日分时(价格+均价+成交量) | day 日K(默认120根前复权)
            | week 周K(120根) | month 月K(60根)
    返回标准化 arrays 便于 ECharts 直接消费"""
    if not code:
        return jr({"ok": False, "msg": "缺少 code 参数"}, 400)
    period = (period or "day").lower()
    if period not in ("minute", "day", "week", "month"):
        return jr({"ok": False, "msg": "period 非法: 仅 minute/day/week/month"}, 400)
    data = fetcher.fetch_stock_chart_robust(code, period)
    if not data:
        return jr({"ok": False, "msg": "图表数据拉取失败(所有数据源均不可用)"}, 502)
    return jr({"ok": True, **data})
