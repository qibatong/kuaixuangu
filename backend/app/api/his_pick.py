# -*- coding: utf-8 -*-
"""《顺势而为竞价终极版》选股逻辑的**服务端接口**（2026-10-03 上线）。

· 逻辑：`app/services/his_pick.py` —— 逐行等价于桌面原件
        （docs/reference/his-pick-竞价终极版.html，md5 961edece9454fb6535c186e10344de24）
        等价性测试：同一份东财夹具，node 跑原码 vs 本移植版 ⇒ 200 行评分逐行一致、名单 113/113 完全一致 ✓
· 取数：**服务端代理东财**（URL 与参数逐字取自原件）
· 节奏（与原件语义一致）：
    竞价时段（9:15–9:30 交易日）→ 实时取数 + 跑他的逻辑 + **落快照**（TTL 20s）
    9:30 之后/非交易日        → **读快照冻结名单**，只用实时取数**刷新现涨**（TTL 30s / 盘后 300s）
                                —— 对应原件「9:30 前可重新选股 · 9:30 后仅更新实时涨幅」
· 快照：落**我们自己的库**（纯写入，不做任何额外东财请求）
· 降级：东财不可用 ⇒ 回退快照（零额外请求）
· 鉴权：登录即可（`get_uid`），**不消耗配额**
"""
import json
import re
import sqlite3
import time
import urllib.request

from fastapi import APIRouter, Depends, Query, Request

from ..core import logger, net
from ..core import trade_calendar as tc
from ..services import fetcher
from ..services import his_pick as H
from ..services import kpl as KPL          # 概念列改用**开盘啦**映射（库内 concept_refresh 已回写，零额外抓取）
from .deps import get_uid, jr

router = APIRouter(prefix='/api/his-pick', tags=['his-pick'])
log = logger.get_logger(__name__)


def _bj_now():
    return time.gmtime(time.time() + 8 * 3600)


def _phase():
    """返回 'auction'（9:15–9:30 交易日）/ 'intraday'（9:30–15:00 交易日）/ 'off'（其他）"""
    t = _bj_now()
    try:
        td = tc.is_trade_day_of(t)
    except Exception:                                   # noqa: BLE001
        td = t.tm_wday < 5
    if not td:
        return 'off'
    hm = t.tm_hour * 60 + t.tm_min
    if 9 * 60 + 15 <= hm <= 9 * 60 + 30:
        return 'auction'
    if 9 * 60 + 30 < hm <= 15 * 60:
        return 'intraday'
    return 'off'


def _fetch_em(fs):
    """服务端代理东财（URL 与参数逐字取自原件）"""
    url = H.getStockApiUrl(fs)
    req = urllib.request.Request(url, headers={'User-Agent': H.UA,
                                              'Referer': 'https://quote.eastmoney.com/'})
    resp = net.http_get(req, timeout=10)          # ⚠️ net.http_get 返回 urllib 的**响应对象**
    try:
        body = resp.read()
    finally:
        try:
            resp.close()
        except Exception:                             # noqa: BLE001
            pass
    if isinstance(body, bytes):
        body = body.decode('utf-8', 'ignore')
    d = json.loads(body)
    return (d.get('data') or {}).get('diff') or []


def _public(it):
    """只输出前端要渲染的字段（他表格用到的列）；不带 rawStock"""
    return {k: it.get(k) for k in ('code', 'name', 'probability', 'confidence', 'bidChange',
                                   'realChange', 'entityChange', 'warnType', 'industry', 'concept',
                                   'bidTurnover', 'speed')}


def _enrich_concept(items, date=None):
    """概念列改用**开盘啦**概念（原件用的是东财 f103，过长；按主人要求换源）。

    · 数据来自我们库里 concept_refresh 已回写的映射（**不出网** ✓）
    · truncate=2 ⇒ 最多两个概念（与站内其它列表一致）
    · blank_if_missing=False ⇒ 库内查不到的票保留原件东财概念（不清空）
    · 🔴 2026-10-05 修 bug：新增 `date` 参数并透传给 apply_board_concept_db。
      原实现**不传日期** ⇒ _load_board_map_db 会默认取**今天**；而本接口在
      「9:30 后 / 非交易日」走的是**快照分支**（快照日未必是今天 —— 例：10-05 国庆假期，
      快照日=09-30，概念表里根本没有 10-05 的行）⇒ 覆盖必然落空。
      因 blank_if_missing=False 会保留东财概念，所以症状不是"空白"而是
      **"概念不是开盘啦的"**——比空白更难发现，与 stats.py 竞价精选那处是同一类 bug。
    · 只影响展示列：他的评分/过滤/排序**完全不用** concept ⇒ 数值与名单零变化 ✓
    """
    if not items:
        return items
    try:
        KPL.apply_board_concept_db(items, log_tag='his-pick', field='concept',
                                   truncate=2, blank_if_missing=False, date=date)
    except Exception as e:                              # noqa: BLE001
        log.warning('his-pick 概念换源失败（保留东财概念）：%s', str(e)[:80])
    # 兜底：库里查不到开盘啦概念的票，把东财那串长概念也截成最多 2 个（只影响展示）
    for it in items:
        c = (it.get('concept') or '').strip()
        if c and ('、' not in c):
            parts = [x for x in c.replace('，', ',').split(',') if x]
            if len(parts) > 2:
                it['concept'] = '、'.join(parts[:2])
    return items


def _snapshot_items():
    """读我们库里的最近一次快照"""
    try:
        c = sqlite3.connect(H.DB, timeout=5)
        r = c.execute("SELECT MAX(trade_date) FROM his_pick_daily").fetchone()
        day = r[0] if r else None
        if not day:
            c.close()
            return [], ''
        # 🔴 2026-10-08 主人反馈「竞价选股排序不是按照评分的」——根因就在这里：
        #   原为 `ORDER BY rank`，而 `rank` 是 **save_snapshot 按"那一次请求过滤后的列表下标"** 写的
        #   ⇒ 只要同一天被**不同筛选条件**写过两次（竞价时段前端会带不同 markets/阈值各请求一次），
        #     两套枚举就会互相错位 ⇒ 表里的 rank 序 **不再等于评分序**（实测 2026-10-08：
        #     336 行、rank 连续且唯一，但评分序列是 48,95,48,48,95… ⇒ 完全无序；
        #     而 2026-10-03 那次 113 行是**有序**的 ⇒ 属"有时才坏"的静默脏数据）。
        #   修法：**一律按评分降序取数**（评分相同的再按 rank 稳定兜底）。
        #   三重收益：① 前端看到的顺序 = 他的原件语义（`sort((a,b)=>b.probability-a.probability)`）；
        #             ② 历史脏快照**无需改库即刻自愈**（读侧就已排好）；
        #             ③ 9:30 后的「读快照→刷现涨→回写」路径用的就是本函数的顺序 ⇒ 回写时顺便
        #                把库里的 rank 一并修正（自愈，见 services/his_pick.save_snapshot 的兜底排序）。
        rows = c.execute("""SELECT code, name, probability, confidence, bid_change, real_change,
                                   entity_change, warn_type, industry, concept, bid_turnover
                            FROM his_pick_daily WHERE trade_date=?
                            ORDER BY probability DESC, rank""", (day,)).fetchall()
        c.close()
        items = [{'code': x[0], 'name': x[1], 'probability': x[2], 'confidence': x[3],
                  'bidChange': x[4], 'realChange': x[5], 'entityChange': x[6], 'warnType': x[7],
                  'industry': x[8], 'concept': x[9], 'bidTurnover': x[10]} for x in rows]
        return items, day
    except Exception:                                   # noqa: BLE001
        return [], ''


def _refresh_quote(items, rows):
    """冻结名单下**只刷新现涨**（原件语义：9:30 后仅更新实时涨幅）

    🔴 2026-10-08 主人反馈「竞价选股有部分实时涨幅没有自动更新」—— 本函数是根因之一：
       `rows` 来自东财 clist，而那个 URL 带 `fid=f3&po=1&pz=200` ⇒ **只给「现涨前 200 名」**，
       再叠加 `fs` 只覆盖**当前勾选的板块** ⇒ 冻结名单里相当一部分票压根不在 rows 里；
       原逻辑对未命中的 code `continue`，**保留竞价时的旧值** ⇒ 用户看到"有些数字一直不动"。

    修法：对**未命中的代码按代码二次点查**（`fetch_spot_quote_map_by_codes` 走东财 ulist，
    51 只 = 1 个请求；逐 code 缓存 25s < 前端 30s 轮询 ⇒ 每轮都拿到新价），
    整体失败时仍回退"保留旧值"（可用性不降低，只是退回改动前的行为）。
    """
    idx = {}
    for s in rows:
        k = str(s.get('f12') or '').zfill(6)
        if k:
            idx[k] = s
    miss = [it.get('code') for it in items if it.get('code') and it.get('code') not in idx]
    extra = {}
    if miss:
        try:
            extra = fetcher.fetch_spot_quote_map_by_codes(miss) or {}
        except Exception as e:                                  # noqa: BLE001
            log.warning('his-pick 按代码补查现涨失败 %d只 err=%s', len(miss), str(e)[:120])
            extra = {}
    for it in items:
        code = it.get('code')
        s = idx.get(code)
        if s:
            it['realChange'] = H.pf(H.or0(s.get('f3')))
            it['entityChange'] = H.getEntityChange(s)
            continue
        q = extra.get(code)
        if q:
            # ulist 路径返回的是**已算好**的字段（与 clist 的 f3 同义）
            if q.get('realChange') is not None:
                it['realChange'] = q['realChange']
            if q.get('entityChange') is not None:
                it['entityChange'] = q['entityChange']
    if miss:
        log.info('his-pick 现涨补查: 名单%d只 clist未命中%d只 补到%d只',
                 len(items), len(miss), len(extra))
    return items


@router.get('')
def api_his_pick(request: Request,
                 markets: str = Query('', description='板块勾选：hs,cyb,kcb（空=原件默认三项）'),
                 bidGt: float = Query(7, description='竞价涨幅上限（原件默认 7）'),
                 probLt: float = Query(65), confLt: float = Query(65),
                 floatMvGt: float = Query(100), priceGt: float = Query(30),
                 stSuspend: bool = Query(True), limitUp: bool = Query(True),
                 force: bool = Query(False, description='绕过缓存（对应原件「重新锁定 / 刷新实时涨幅」）'),
                 uid: int = Depends(get_uid)):
    from ..services.cache_store import cached_singleflight, store

    checked = [m for m in re.split(r'[,\s]+', markets or '') if m in ('hs', 'cyb', 'kcb')] or list(H.DEFAULT_MARKETS)
    filters = dict(H.DEFAULT_FILTERS, markets=checked, bidGt=bidGt, probLt=probLt,
                   confLt=confLt, floatMvGt=floatMvGt, priceGt=priceGt,
                   stSuspend=stSuspend, limitUp=limitUp)
    phase = _phase()
    live = phase == 'auction'
    ttl = 20 if live else (30 if phase == 'intraday' else 300)
    sig = ','.join(checked) + '|%g|%g|%g|%g|%g|%d|%d' % (bidGt, probLt, confLt, floatMvGt, priceGt,
                                                         int(bool(stSuspend)), int(bool(limitUp)))
    ck = 'hispick:%s:%s' % (sig, phase) + (':force%d' % int(time.time()) if force else '')

    def load():
        fs = H.getMarketFs(checked)
        rows, err = None, ''
        try:
            rows = _fetch_em(fs)
        except Exception as e:                          # noqa: BLE001
            err = str(e)[:140]
            log.warning('his-pick 东财取数失败：%s', err)

        # ① 竞价时段：实时取数 → 跑他的逻辑 → 落快照（他的"9:30 前重新选股"语义）
        if live and rows:
            day = time.strftime('%Y-%m-%d')          # 2026-10-05: 提前取出, 供概念覆盖按日查库
            items = _enrich_concept(H.processAllStocks(rows, filters), day)
            ok = H.save_snapshot(items, day, {'pool_size': len(rows)})
            pub = [_public(x) for x in items]
            return {'ok': True, 'date': day, 'fetchedAt': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'src': 'eastmoney', 'phase': phase, 'poolSize': len(rows), 'picked': len(pub),
                    'medals': pub[:3], 'list': pub, 'filters': filters, 'snapshotSaved': bool(ok)}

        # ② 9:30 后 / 非交易日：读快照**冻结名单**，只用实时取数刷新现涨（原件语义）
        items, day = _snapshot_items()
        if items:
            if rows:
                items = _refresh_quote(items, rows)
                H.save_snapshot(items, day, {'pool_size': len(rows)})
            _enrich_concept(items, day)     # 2026-10-05: 必须用**快照日**（不一定是今天）查概念库
            pub = [_public(x) for x in items]
            return {'ok': True, 'date': day, 'fetchedAt': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'src': 'snapshot', 'phase': phase, 'poolSize': None, 'picked': len(pub),
                    'medals': pub[:3], 'list': pub, 'filters': filters}

        # ③ 无快照且取数失败：空态（带原因）
        return {'ok': False, 'err': err or '暂无数据', 'phase': phase, 'picked': 0,
                'medals': [], 'list': [], 'filters': filters}

    return jr(cached_singleflight(store, ck, ttl, load))
