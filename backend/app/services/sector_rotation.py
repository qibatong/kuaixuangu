# -*- coding: utf-8 -*-
"""
板块轮动历史服务: 每日保存板块强度 Top10, 提供历史轮动查询(表格+趋势线+量能柱)
========================================================================
- 工作日 15:30 后由 scheduler 调 record_today_top(source=...) 落库 daily_sector_top
- 数据源(source): 'kpl' 开盘啦 / 'em' 东方财富 / 'ths' 同花顺(预留)
- query_rotation(days, source) 返回指定数据源最近 N 个交易日数据
"""
import json
import time
import urllib.parse
import urllib.request

from ..core import config, logger
from ..core import net as _net
from ..db import database
from . import kpl
from . import meoz_client as meoz
from . import scorer

log = logger.get_logger(__name__)

VALID_SOURCES = ("kpl", "em", "ths")

# 2026-08-30 可观测性(主人要求): 记录最近一次数据源失败, 供 API 透传 source_failed
_SRC_ERR = {"source": None, "msg": "", "ts": 0}
import threading
_SRC_LOCK = threading.Lock()


def last_source_error():
    """返回最近一次源失败 {source, msg, ts}; 无失败返回 None"""
    with _SRC_LOCK:
        return dict(_SRC_ERR) if _SRC_ERR["ts"] else None


def _mark_source_error(source, msg):
    with _SRC_LOCK:
        _SRC_ERR["source"] = source
        _SRC_ERR["msg"] = str(msg)[:200]
        _SRC_ERR["ts"] = time.time()


def _bj_date():
    g = time.gmtime(time.time() + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def _ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_em_board_rank():
    """东方财富板块当日榜(概念+行业合并, 按涨跌幅排序前 60 条)
    返回 [{"boardCode": BKxxxx, "name": ..., "strength": 涨跌幅%×100, "change": 涨跌幅%, "amount": 成交额}, ...]"""
    out = []
    try:
        ctx = _ssl_ctx()
        for fs in ("m:90+t:3+f:!50", "m:90+t:2+f:!50"):
            qs = urllib.parse.urlencode({
                "pn": 1, "pz": 200, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": fs,
                "fields": "f12,f14,f3,f6,f62", "ut": config.EASTMONEY_UT,
            })
            req = urllib.request.Request(config.EASTMONEY_URL + "?" + qs, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
            })
            with _net.http_get(req, timeout=8, context=ctx) as r:
                data = json.loads(r.read().decode("utf-8"))
            diff = (data.get("data") or {}).get("diff") or []
            for it in diff:
                code, name = it.get("f12"), it.get("f14")
                if not code or not name:
                    continue
                # 过滤统计型指数(与回补脚本一致)
                if any(kw in name for kw in ("昨日", "新高", "打板", "连板", "涨停", "跌停", "炸板", "首板", "晋级", "破板")):
                    continue
                chg = it.get("f3") or 0
                out.append({
                    "boardCode": code,
                    "name": name,
                    "strength": round(float(chg) * 100, 1),
                    "change": round(float(chg), 2),
                    "amount": float(it.get("f6") or 0),
                    "mainNet": float(it.get("f62") or 0),
                    "volRatio": 0.0,
                    "floatMv": 0.0,
                })
    except Exception as e:
        log.warning("东财板块榜抓取失败 err=%s", e)
        _mark_source_error("em", e)
        return []
    # 概念+行业合并后去重(同名保留涨跌幅大者) + 按涨跌幅降序
    seen = {}
    for b in out:
        prev = seen.get(b["name"])
        if prev is None or b["change"] > prev["change"]:
            seen[b["name"]] = b
    out = sorted(seen.values(), key=lambda x: x["change"], reverse=True)
    return out


# 东财概念板块榜单字段(涨速/领涨股/涨跌家数/主力净额) —— 题材异动榜数据源
_EM_CONCEPT_FIELDS = "f12,f14,f3,f6,f62,f104,f105,f128,f136,f140,f222"
# 东财板块成分股字段(涨速/主力净流入/市值/换手/成交额)
_EM_MEMBER_FIELDS = "f2,f3,f6,f8,f11,f12,f13,f14,f20,f21,f62"

# 统计型指数板块名关键词(与回补/fetch_em_board_rank 一致, 题材榜剔除)
_EM_STAT_KW = ("昨日", "新高", "打板", "连板", "涨停", "跌停", "炸板", "首板", "晋级", "破板")


def _em_clist(fs, fields, pz=200, fid="f3", pages=1):
    """拉取东财 clist(push2dycalc 域名)指定分区, 返回 diff 列表(可多页)。

    复用 config.EASTMONEY_URL / EASTMONEY_UT, 与 fetcher._fetch_clist_page 同源同参数。
    仅本服务内部使用, 失败抛异常由调用方兜底。"""
    ctx = _ssl_ctx()
    out = []
    for pn in range(1, pages + 1):
        qs = urllib.parse.urlencode({
            "pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": fid, "fs": fs, "fields": fields, "ut": config.EASTMONEY_UT,
        })
        req = urllib.request.Request(config.EASTMONEY_URL + "?" + qs, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
        })
        with _net.http_get(req, timeout=8, context=ctx) as r:
            data = json.loads(r.read().decode("utf-8"))
        diff = (data.get("data") or {}).get("diff") or []
        out.extend(diff)
        if len(diff) < pz:
            break
    return out


def fetch_em_concept_rank():
    """东财概念板块异动榜(题材异动榜左栏): 按涨幅降序返回全部概念板块。

    每项: {boardCode, name, change, speed, mainNet, amount, upCount, downCount,
           leaderName, leaderCode, leaderChange}
    speed=板块 5 分钟涨速(f222); leader*=领涨股(f128/f140/f136)。
    剔除「昨日连板/打板」等统计型指数板块。数据源 push2dycalc 域名(测试/生产可达)。"""
    out = []
    try:
        for it in _em_clist("m:90+t:3+f:!50", _EM_CONCEPT_FIELDS, fid="f3", pages=3):
            code, name = it.get("f12"), it.get("f14")
            if not code or not name:
                continue
            if any(kw in name for kw in _EM_STAT_KW):
                continue
            chg = it.get("f3") or 0
            out.append({
                "boardCode": code,
                "name": name,
                "change": round(float(chg), 2),
                "speed": round(float(it.get("f222") or 0), 2),
                "mainNet": float(it.get("f62") or 0),
                "amount": float(it.get("f6") or 0),
                "upCount": int(it.get("f104") or 0),
                "downCount": int(it.get("f105") or 0),
                "leaderName": it.get("f128") or "",
                "leaderCode": it.get("f140") or "",
                "leaderChange": round(float(it.get("f136") or 0), 2),
            })
    except Exception as e:
        log.warning("东财概念板块异动榜抓取失败 err=%s", e)
        _mark_source_error("em", e)
        return []
    out.sort(key=lambda x: x["change"], reverse=True)
    return out


def fetch_em_board_members(code):
    """东财概念板块成分股(题材异动榜右栏): 按涨幅降序返回全部成分股。

    每项: {code, name, price, change, speed, turnover, amount, mainNet, totalMv, floatMv}
    speed=个股 5 分钟涨速(f11); mainNet=主力净流入(元, f62); 市值 totalMv/floatMv(元)。"""
    if not code:
        return []
    out = []
    try:
        for it in _em_clist("b:" + str(code), _EM_MEMBER_FIELDS, fid="f3", pages=3):
            c, name = it.get("f12"), it.get("f14")
            if not c or not name:
                continue
            if scorer.is_bse(c):
                continue    # 系统不需要北交所数据(全链路过滤, 题材异动榜成分股同样剔除)
            chg = it.get("f3") or 0
            out.append({
                "code": c,
                "name": name,
                "price": float(it.get("f2") or 0),
                "change": round(float(chg), 2),
                "speed": round(float(it.get("f11") or 0), 2),
                "turnover": round(float(it.get("f8") or 0), 2),
                "amount": float(it.get("f6") or 0),
                "mainNet": float(it.get("f62") or 0),
                "totalMv": float(it.get("f20") or 0),
                "floatMv": float(it.get("f21") or 0),
            })
    except Exception as e:
        log.warning("东财板块成分股抓取失败 code=%s err=%s", code, e)
        return []
    out.sort(key=lambda x: x["change"], reverse=True)
    return out


# =====================================================================
# 猫爪板块指数(题材异动榜数据源, 2026-09-21 主人: 东财的不行, 换猫爪板块指数)
#   * 左栏板块榜 ← theme_daily(type=gn 概念 269 个 / hy 行业 145 个, 实测 2026-09-21)
#     最新交易日自动合并实时缓存; fields 只传文档列出的字段(422 铁律: 多传全挂)
#   * 右栏成分股 ← theme_members(theme_symbol=板块代码, 返回 [theme_symbol, [股票代码数组]])
#     行情用 screening(symbols=逗号分隔) 补全: name/close/pct_chg/amount/turnover_rate_f/free_float_mv
#   * 板块代码体系 880xxx(猫爪/同花顺口径), 与东财 BKxxxx 不同; 成分股含北交所 920 段须 is_bse 过滤
# =====================================================================
_MEOZ_BOARD_TTL = 30       # 板块榜缓存: 异动榜 30s 刷新节奏对齐前端
_MEOZ_MEMBER_TTL = 60      # 成分股(成员池+行情)缓存: 按需点击+30s 轮询, 控制猫爪用量
_MEOZ_SCREEN_BATCH = 2000  # screening symbols 分片大小(与 fundflow_map 同风格, 保守分片)


def fetch_meoz_board_rank(btype="gn"):
    """猫爪板块指数榜(题材异动榜左栏): theme_daily 按涨跌幅降序返回全部板块。

    btype: gn=概念(默认, 269 个) / hy=行业(145 个)。
    每项: {boardCode(880xxx), name, change, amount, close}
    猫爪无涨速/主力净额/领涨股字段, 返回置 0/空以兼容前端列结构(前端已同步删列)。"""
    try:
        data = meoz.call_cached(
            "theme_daily", params={"type": btype}, ttl=_MEOZ_BOARD_TTL,
            fields="tradedate,symbol,name,type,close,pct_chg,amount")
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪板块指数榜抓取失败 type=%s err=%s", btype, e)
        _mark_source_error("meoz", e)
        return []
    dd = data.get("data") if isinstance(data, dict) else None
    cols = (dd or {}).get("fields") or []
    items = (dd or {}).get("items") or []
    if not cols or not items:
        return []
    idx = {c: i for i, c in enumerate(cols)}
    out = []
    for row in items:
        if not isinstance(row, (list, tuple)):
            continue
        try:
            def _g(k):
                i = idx.get(k)
                return row[i] if i is not None and len(row) > i else None
            sym, name = _g("symbol"), _g("name")
            if not sym or not name:
                continue
            out.append({
                "boardCode": str(sym),
                "name": str(name),
                "change": round(float(_g("pct_chg") or 0), 2),
                "amount": float(_g("amount") or 0),
                "close": float(_g("close") or 0),
                "speed": 0.0,
                "mainNet": 0.0,
                "upCount": 0,
                "downCount": 0,
                "leaderName": "",
                "leaderCode": "",
                "leaderChange": 0.0,
            })
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["change"], reverse=True)
    return out


def fetch_meoz_board_members(code):
    """猫爪板块成分股(题材异动榜右栏): theme_members 拿成员池 + screening 补行情。

    每项: {code, name, price, change, turnover, amount, floatMv}
    floatMv=自由流通市值(元, screening.free_float_mv, 全站口径铁律);
    北交所(is_bse)过滤; 无行情的成员(停牌等)跳过; 按涨幅降序。"""
    code = str(code or "").strip()
    if not code:
        return []
    # 1) 成员池
    try:
        data = meoz.call_cached("theme_members", params={"theme_symbol": code},
                                ttl=_MEOZ_MEMBER_TTL)
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪板块成员抓取失败 code=%s err=%s", code, e)
        _mark_source_error("meoz", e)
        return []
    dd = data.get("data") if isinstance(data, dict) else None
    items = (dd or {}).get("items") or []
    codes = []
    for row in items:
        if isinstance(row, (list, tuple)) and len(row) >= 2 and str(row[0]) == code:
            codes = [str(s) for s in (row[1] or []) if s]
            break
    if not codes:
        return []
    codes = [c for c in codes if not scorer.is_bse(c)]         # 系统不需要北交所数据
    if not codes:
        return []
    # 2) screening 分片拉行情
    members_map = {}
    try:
        for i in range(0, len(codes), _MEOZ_SCREEN_BATCH):
            chunk = codes[i:i + _MEOZ_SCREEN_BATCH]
            d2 = meoz.call_cached(
                "screening", params={"symbols": ",".join(chunk)}, ttl=_MEOZ_MEMBER_TTL,
                fields="symbol,name,close,pct_chg,amount,turnover_rate_f,free_float_mv")
            members_map.update(meoz._sym_rows(d2))
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪板块成员行情抓取失败 code=%s err=%s", code, e)
        return []
    out = []
    for c in codes:
        r = members_map.get(c)
        if not r:
            continue
        try:
            out.append({
                "code": c,
                "name": str(r.get("name") or ""),
                "price": float(r.get("close") or 0),
                "change": round(float(r.get("pct_chg") or 0), 2),
                "turnover": round(float(r.get("turnover_rate_f") or 0), 2),
                "amount": float(r.get("amount") or 0),
                "floatMv": float(r.get("free_float_mv") or 0),
                "speed": 0.0,
                "mainNet": 0.0,
            })
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["change"], reverse=True)
    return out


# =====================================================================
# 猫爪精选板块 jx(题材异动榜数据源, 2026-09-21 主人: 先换左栏为精选板块)
#   * 左栏板块榜 ← themedaily_jx(level=parent 一级精选板块 267 个, 801xxxk 带 k)
#     字段全有值(实测 267/267): strength强度/pct_chg涨幅/chg_speed涨速/amount成交额/
#     main_net_amount主力净额/turnover_rate换手/volume_ratio量比/circ_mv流通市值/prev_pct_chg昨日涨幅
#   * 右栏成分股 ← thememembers_jx(level=parent + theme_symbols, 不传 tag=股票池) + screening 补行情
#   * 精选板块 801xxxk 与概念板块 880xxx/竞价 801xxx 不互通, 各拉各的
# =====================================================================
_JX_BOARD_TTL = 30     # 精选板块榜缓存(前端 30s 轮询对齐)
_JX_MEMBER_TTL = 60    # 精选板块股票池+行情缓存
_JX_AUC_TTL = 30       # 板块竞价异动缓存(09:28 定格, 30s 对齐前端)


def fetch_meoz_auc_kp():
    """猫爪板块竞价异动(theme_auc_kp): 交易日 09:28 更新, 按来源分组返回当日竞价异动板块。

    返回 {theme_symbol(801xxx 不带 k): {group, rank, burst, abnormal, net, name}}
    group: List1=今日新增竞价异动 / List2=昨日爆发延续 / List3=其它异动;
    burst=竞价爆量(bid_volume_burst); abnormal=异动金额(元); net=竞价主力净额(元, main_net_amount)。
    失败返回空 dict(merge 层静默降级, 不阻断精选板块榜)。"""
    try:
        data = meoz.call_cached(
            "theme_auc_kp", ttl=_JX_AUC_TTL,
            fields="source_day,group,group_rank,theme_symbol,theme_name,bid_volume_burst,abnormal_amount,main_net_amount")
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪板块竞价异动抓取失败 err=%s", e)
        _mark_source_error("meoz", e)
        return {}
    dd = data.get("data") if isinstance(data, dict) else None
    cols = (dd or {}).get("fields") or []
    items = (dd or {}).get("items") or []
    if not cols or not items:
        return {}
    idx = {c: i for i, c in enumerate(cols)}
    out = {}
    for row in items:
        if not isinstance(row, (list, tuple)):
            continue
        try:
            def _g(k):
                i = idx.get(k)
                return row[i] if i is not None and len(row) > i else None
            sym = _g("theme_symbol")
            if not sym:
                continue
            out[str(sym)] = {
                "group": str(_g("group") or ""),
                "rank": int(_g("group_rank") or 0),
                "burst": float(_g("bid_volume_burst") or 0),
                "abnormal": float(_g("abnormal_amount") or 0),
                "net": float(_g("main_net_amount") or 0),
                "name": str(_g("theme_name") or ""),
            }
        except (ValueError, TypeError):
            continue
    return out


def fetch_meoz_jx_rank():
    """猫爪精选板块异动榜(题材异动榜左栏): themedaily_jx level=parent 按涨速降序返回一级精选板块。

    每项: {boardCode(801xxxk), name, change, speed, mainNet, amount,
           strength, turnover, volRatio, floatMv, prevChg,
           aucGroup, aucRank, aucBurst, aucAbnormal, aucNet}
    后 5 个为板块竞价异动(theme_auc_kp)按 竞价代码+'k' 融合进来的字段:
    aucGroup=List1今日新增/List2昨日延续/List3其它(空串=无竞价异动);
    aucBurst=竞价爆量; aucAbnormal=异动金额(元); aucNet=竞价主力净额(元)。
    竞价失败或未命中时这些字段置 0/空串(不阻断精选板块榜)。"""
    try:
        data = meoz.call_cached(
            "themedaily_jx", params={"level": "parent"}, ttl=_JX_BOARD_TTL,
            fields="tradedate,theme_symbol,theme_name,strength,pct_chg,chg_speed,amount,main_net_amount,turnover_rate,volume_ratio,circ_mv,prev_pct_chg")
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪精选板块榜抓取失败 err=%s", e)
        _mark_source_error("meoz", e)
        return []
    dd = data.get("data") if isinstance(data, dict) else None
    cols = (dd or {}).get("fields") or []
    items = (dd or {}).get("items") or []
    if not cols or not items:
        return []
    idx = {c: i for i, c in enumerate(cols)}
    # 板块竞价异动: 竞价代码 801xxx(不带 k) + 'k' = 精选板块代码 801xxxk, 按此 merge
    auc_map = fetch_meoz_auc_kp()
    # 领涨股: thememembers_jx tag=authentic 每板块正宗成分股里涨幅最高的一只
    leader_map = fetch_meoz_jx_leaders()
    out = []
    for row in items:
        if not isinstance(row, (list, tuple)):
            continue
        try:
            def _g(k):
                i = idx.get(k)
                return row[i] if i is not None and len(row) > i else None
            sym, name = _g("theme_symbol"), _g("theme_name")
            if not sym or not name:
                continue
            auc = auc_map.get(str(sym)[:-1]) if str(sym).endswith("k") else None
            leader = leader_map.get(str(sym), {})
            out.append({
                "boardCode": str(sym),
                "name": str(name),
                "change": round(float(_g("pct_chg") or 0), 2),
                "speed": round(float(_g("chg_speed") or 0), 2),
                "mainNet": float(_g("main_net_amount") or 0),
                "amount": float(_g("amount") or 0),
                "strength": float(_g("strength") or 0),
                "turnover": round(float(_g("turnover_rate") or 0), 2),
                "volRatio": round(float(_g("volume_ratio") or 0), 2),
                "floatMv": float(_g("circ_mv") or 0),
                "prevChg": round(float(_g("prev_pct_chg") or 0), 2),
                "aucGroup": (auc or {}).get("group", ""),
                "aucRank": (auc or {}).get("rank", 0),
                "aucBurst": (auc or {}).get("burst", 0.0),
                "aucAbnormal": (auc or {}).get("abnormal", 0.0),
                "aucNet": (auc or {}).get("net", 0.0),
                "leaderCode": leader.get("code", ""),
                "leaderName": leader.get("name", ""),
                "leaderChange": leader.get("change", 0.0),
            })
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["speed"], reverse=True)   # 异动榜默认按涨速降序
    return out


def fetch_meoz_jx_leaders():
    """猫爪精选板块领涨股: thememembers_jx tag=authentic 拿每板块「最正宗」成分股,
    再 screening 批量补行情, 每板块取涨幅最高的一只作为领涨股。

    返回 {boardCode(801xxxk): {code, name, change}}; 失败返回空 dict(静默降级,
    不阻断精选板块榜)。北交所过滤; 成分股全市场去重后分片拉行情(通常 1~2 次调用)。"""
    try:
        data = meoz.call_cached("thememembers_jx", params={"tag": "authentic"}, ttl=_JX_MEMBER_TTL)
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪精选板块正宗成分股抓取失败 err=%s", e)
        _mark_source_error("meoz", e)
        return {}
    dd = data.get("data") if isinstance(data, dict) else None
    items = (dd or {}).get("items") or []
    board_codes = {}
    for row in items:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            sym = str(row[0])
            codes = [str(s) for s in (row[1] or []) if s]
            if sym and codes:
                board_codes[sym] = [c for c in codes if not scorer.is_bse(c)]
    if not board_codes:
        return {}
    # 全市场去重后批量拉行情(只取 name/pct_chg 两个字段)
    all_codes, seen = [], set()
    for codes in board_codes.values():
        for c in codes:
            if c not in seen:
                seen.add(c)
                all_codes.append(c)
    members_map = {}
    try:
        for i in range(0, len(all_codes), _MEOZ_SCREEN_BATCH):
            chunk = all_codes[i:i + _MEOZ_SCREEN_BATCH]
            d2 = meoz.call_cached(
                "screening", params={"symbols": ",".join(chunk)}, ttl=_JX_MEMBER_TTL,
                fields="symbol,name,pct_chg")
            members_map.update(meoz._sym_rows(d2))
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪精选板块领涨股行情抓取失败 err=%s", e)
        return {}
    out = {}
    for sym, codes in board_codes.items():
        best = None
        for c in codes:
            r = members_map.get(c)
            if not r:
                continue
            try:
                chg = float(r.get("pct_chg") or 0)
            except (ValueError, TypeError):
                continue
            if best is None or chg > best[1]:
                best = (c, chg, str(r.get("name") or ""))
        if best:
            out[sym] = {"code": best[0], "change": round(best[1], 2), "name": best[2]}
    return out


def fetch_meoz_jx_members(code):
    """猫爪精选板块成分股(题材异动榜右栏): thememembers_jx 拿股票池 + screening 补行情。

    每项: {code, name, price, change, turnover, amount, floatMv}
    floatMv=自由流通市值(元, screening.free_float_mv, 全站口径铁律);
    北交所(is_bse)过滤; 按涨幅降序。精选板块代码 801xxxk(带 k)。"""
    code = str(code or "").strip()
    if not code:
        return []
    # 1) 股票池(thememembers_jx: level=parent + theme_symbols, 不传 tag)
    try:
        data = meoz.call_cached("thememembers_jx",
                                params={"level": "parent", "theme_symbols": code},
                                ttl=_JX_MEMBER_TTL)
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪精选板块股票池抓取失败 code=%s err=%s", code, e)
        _mark_source_error("meoz", e)
        return []
    dd = data.get("data") if isinstance(data, dict) else None
    items = (dd or {}).get("items") or []
    codes = []
    for row in items:
        if isinstance(row, (list, tuple)) and len(row) >= 2 and str(row[0]) == code:
            codes = [str(s) for s in (row[1] or []) if s]
            break
    if not codes:
        return []
    codes = [c for c in codes if not scorer.is_bse(c)]         # 系统不需要北交所数据
    if not codes:
        return []
    # 2) screening 分片拉行情(复用现有成员行情逻辑)
    members_map = {}
    try:
        for i in range(0, len(codes), _MEOZ_SCREEN_BATCH):
            chunk = codes[i:i + _MEOZ_SCREEN_BATCH]
            d2 = meoz.call_cached(
                "screening", params={"symbols": ",".join(chunk)}, ttl=_JX_MEMBER_TTL,
                fields="symbol,name,close,pct_chg,amount,turnover_rate_f,free_float_mv")
            members_map.update(meoz._sym_rows(d2))
    except Exception as e:                                     # noqa: BLE001
        log.warning("猫爪精选板块成员行情抓取失败 code=%s err=%s", code, e)
        return []
    out = []
    for c in codes:
        r = members_map.get(c)
        if not r:
            continue
        try:
            out.append({
                "code": c,
                "name": str(r.get("name") or ""),
                "price": float(r.get("close") or 0),
                "change": round(float(r.get("pct_chg") or 0), 2),
                "turnover": round(float(r.get("turnover_rate_f") or 0), 2),
                "amount": float(r.get("amount") or 0),
                "floatMv": float(r.get("free_float_mv") or 0),
                "speed": 0.0,
                "mainNet": 0.0,
            })
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["change"], reverse=True)
    return out


def _ths_get(url):
    """同花顺页面抓取(GBK)"""
    import gzip
    ctx = _ssl_ctx()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://q.10jqka.com.cn/",
        "Accept-Encoding": "gzip",
    })
    with _net.http_get(req, timeout=10, context=ctx) as r:
        data = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return data.decode("gbk", "ignore")


def fetch_ths_board_rank():
    """同花顺板块当日榜(行业 + 概念, 按涨跌幅降序, 取前 60 条)
    行业: https://q.10jqka.com.cn/thshy/ (GBK 表格, code/88xxxx)
    概念: https://q.10jqka.com.cn/gn/ 内嵌 gnSection JSON(platecode/platename/199112涨跌幅/zjjlr主力净流入/zfl涨停数)
    返回 [{"boardCode": 88xxxx, "name": ..., "strength": 涨跌幅%×100, "change": 涨跌幅%, "amount": 成交额(元)}, ...]"""
    import json
    import re
    out = []
    seen = set()
    try:
        # 1. 行业板块(表格)
        html = _ths_get("https://q.10jqka.com.cn/thshy/")
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)[1:]
        for tr in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if len(cells) < 5:
                continue
            code_m = re.search(r"code/(88\d{4})/", cells[1])
            name_m = re.search(r">([^<]+)</a>", cells[1])
            if not code_m or not name_m:
                continue
            try:
                chg = float(cells[2].strip())
                amount_yi = float(cells[4].strip())   # 总成交额(亿元)
            except (ValueError, IndexError):
                continue
            name = name_m.group(1).strip()
            seen.add(name)
            out.append({
                "boardCode": code_m.group(1),
                "name": name,
                "strength": round(chg * 100, 1),
                "change": round(chg, 2),
                "amount": round(amount_yi * 1e8, 2),   # 亿 -> 元
                "mainNet": 0.0,
                "volRatio": 0.0,
                "floatMv": 0.0,
            })
        # 2. 概念板块(gnSection JSON)
        html2 = _ths_get("https://q.10jqka.com.cn/gn/")
        m = re.search(r"id=\"gnSection\" value=['\"](\{.*?\})['\"]", html2, re.S)
        if m:
            data = json.loads(m.group(1))
            for it in data.values():
                name = it.get("platename") or ""
                if not name or name in seen:
                    continue
                try:
                    chg = float(it.get("199112", 0) or 0)
                    zjjlr = float(it.get("zjjlr", 0) or 0)   # 主力净流入(亿)
                except (ValueError, TypeError):
                    continue
                seen.add(name)
                out.append({
                    "boardCode": it.get("platecode", ""),
                    "name": name,
                    "strength": round(chg * 100, 1),
                    "change": round(chg, 2),
                    "amount": 0.0,   # 概念页无成交额
                    "mainNet": round(zjjlr * 1e8, 2),
                    "volRatio": 0.0,
                    "floatMv": 0.0,
                })
    except Exception as e:
        log.warning("同花顺板块榜抓取失败 err=%s", e)
        return []
    # 按涨跌幅降序
    out.sort(key=lambda x: x["change"], reverse=True)
    return out[:60]


def _fetch(source, top_n, date=None):
    """按数据源抓取 TopN 列表
    date: 指定日期(历史回看/补跑用); None=今天
    kpl 源: 今天/不传 → 实时接口(15:30后实时=收盘数据, 避开apphis当日1020)
           历史日期 → 历史接口fetch_board_rank_by_date(保留期≈最近3个交易日)"""
    import time as _t
    if source == "kpl":
        today = _t.strftime("%Y-%m-%d")
        if not date or date == today:
            return (kpl.fetch_board_rank() or [])[:top_n]
        return (kpl.fetch_board_rank_by_date(date) or [])[:top_n]
    if source == "em":
        return fetch_em_board_rank()[:top_n]
    if source == "ths":
        return fetch_ths_board_rank()[:top_n]
    log.warning("未知数据源 source=%s", source)
    return []


def record_today_top(top_n=10, date=None, source="kpl"):
    """抓取当日板块强度 TopN 落库 daily_sector_top(覆盖式)
    date: 落库日期(默认北京时间今天)
    source: 数据源 kpl/em/ths(预留)
    返回入库条数; 抓取失败返回 0"""
    if source not in VALID_SOURCES:
        log.warning("不支持的数据源 source=%s", source)
        return 0
    date = date or _bj_date()
    boards = _fetch(source, top_n, date=date)
    if not boards:
        log.warning("板块轮动抓取为空 source=%s date=%s", source, date)
        return 0
    payload = []
    for i, b in enumerate(boards, start=1):
        payload.append({
            "rank": i,
            "boardCode": b.get("boardCode") or "",
            "name": b.get("name") or "",
            "strength": float(b.get("strength") or 0),
            "change": float(b.get("change") or 0),
            "amount": float(b.get("amount") or 0),
            "mainNet": float(b.get("mainNet") or 0),
            "volRatio": float(b.get("volRatio") or 0),
            "floatMv": float(b.get("floatMv") or 0),
        })
    try:
        conn = database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO daily_sector_top (date, source, boards, ts) VALUES (?,?,?,?)",
            (date, source, json.dumps(payload, ensure_ascii=False), int(time.time())))
        conn.commit()
        conn.close()
        log.info("板块轮动已存 source=%s date=%s 数量%d", source, date, len(payload))
        return len(payload)
    except Exception as e:
        log.error("板块轮动落库失败 err=%s", e)
        return 0


def query_rotation(days=10, source="kpl"):
    """返回指定数据源最近 N 个交易日(按日期降序)的板块 Top10 数据
    返回 {dates:[...], days: [{date, boards:[...]}], source}"""
    if source not in VALID_SOURCES:
        return {"dates": [], "days": [], "source": source}
    days = max(1, min(int(days or 10), 60))
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date, boards FROM daily_sector_top WHERE source=? ORDER BY date DESC LIMIT ?",
        (source, days)).fetchall()
    conn.close()
    out = []
    for d, raw in rows:
        try:
            boards = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            boards = []
        out.append({"date": d, "boards": boards})
    dates = [x["date"] for x in reversed(out)]
    return {"dates": dates, "days": out, "source": source}


def query_window_ranking(days_list=(10, 20, 30, 50), top_k=5, source="kpl"):
    """返回指定数据源多窗口排名"""
    if source not in VALID_SOURCES:
        return {"windows": [], "common_names": [], "source": source}
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT date, boards FROM daily_sector_top WHERE source=? ORDER BY date DESC LIMIT ?",
        (source, max(days_list))).fetchall()
    conn.close()
    result = {"windows": [], "common_names": [], "source": source}
    name_pool = set()
    for win in days_list:
        win_rows = rows[:win]
        if not win_rows:
            result["windows"].append({"window": win, "top": []})
            continue
        agg = {}
        for _date, raw in win_rows:
            try:
                boards = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                continue
            for b in boards:
                name = b.get("name") or ""
                if not name:
                    continue
                rank = b.get("rank", 99)
                if rank > top_k:
                    continue
                # 排名权重(Top1=5, Top5=1, 排名越前权重越高)
                weight = top_k + 1 - rank
                strength = float(b.get("strength") or 0)
                if name not in agg:
                    agg[name] = {"sum": 0.0, "count": 0, "weighted_sum": 0.0}
                agg[name]["sum"] += strength * weight            # 累计强度
                agg[name]["count"] += 1                          # 进入 TopK 的天数
                agg[name]["weighted_sum"] += strength * weight   # 同 sum(用于平均)
        top = []
        for name, v in agg.items():
            weighted = v["weighted_sum"]
            avg = weighted / v["count"] if v["count"] else 0
            top.append({
                "name": name,
                "avgStrength": round(avg, 2),                       # 排名加权强度均值
                "strengthSum": round(v["sum"], 2),                  # 累计强度(用于多窗口排名折线, 明显区分窗口)
                "daysInTop": v["count"],
            })
            name_pool.add(name)
        top.sort(key=lambda x: x["strengthSum"], reverse=True)
        top = top[:top_k]
        result["windows"].append({"window": win, "top": top})
    result["common_names"] = sorted(name_pool)
    return result