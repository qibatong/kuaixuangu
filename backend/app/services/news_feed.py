# -*- coding: utf-8 -*-
"""
盘前资讯聚合层 (2026-09-27 v4.11.59 新增)
=========================================
「盘前资讯」页需要**两个**上游的资讯内容, 本模块把它们归一化成同一套结构, 前端只认一种。

上游一: 猫爪 apiname="news"
    返回 {view, count, fields:[13 列], items:[[...], ...]}
    fields = [published_at, display_at, time_label, source, source_name, title,
              summary, url, image, rank, source_id, item_type, updated_at]
    ★ 参数实测(2026-09-27 测试机): **只有 limit 生效** —— limit=20 取 20 条, 缺省 100;
      num / size / page / page_size / date / trademin / type **全部被忽略**(仍返 100 条)。
      ⇒ 「按日期取历史快讯」这个能力**不存在**, 只能取最近 N 条。
        想要历史累积必须自己落库(本期未做, 见文末 TODO)。
    ★ 内容源是「第一财经」等门户头条流(yicai_home), 偏宏观/产业, 非 7x24 电报式。

上游二: 开盘啦
    doc96 快讯(apparticle host) —— 财联社实时电报, 默认 **3 条**; st / Order 实测无效。
    doc95 头条(apparticle host) —— 每日 1 篇富文本(HTML)。
    doc97 明天炒什么(applhb host) —— 盘后选题榜, 带 HotVal / HotTag。
    doc99 文章正文(applhb host) —— 传 doc97 的 ID 取全文。

设计原则(与 kpl.py / meoz_client.py 一致):
  * 缓存 + 单飞(single-flight): 资讯页会被轮询, 不能每次都打上游;
  * **任一上游失败都不阻断** —— 返回剩下一半数据 + 在 degraded 里如实标注, 绝不静默丢字段;
  * 时间一律按 **UTC+8 固定偏移** 计算, 不依赖服务器 TZ(生产/测试机 TZ 不同会错行)。

TODO(未做, 明确记着): 猫爪 news 无日期参数 ⇒ 想要「按日回看资讯」必须
  盘前/盘后各落一次库(建议复用 `auction_snapshot` 的调度器加 phase="news")。
"""
import calendar
import time
from datetime import datetime

from ..core import logger
from . import kpl
from . import meoz_client as meoz
from .cache_store import store

log = logger.get_logger(__name__)

_FLASH_TTL = 60      # 快讯缓存 60s: 资讯不是行情, 分钟级新鲜度足够
_PREMARKET_TTL = 600  # 盘前精选 10min: 头条/选题一天只更新一两次

_MEOZ_NEWS_LIMIT = 60  # 猫爪默认 100 条偏多, 60 条足够一屏时间线


def _cached(key, ttl, loader):
    from .cache_store import cached_singleflight
    return cached_singleflight(store, "news:" + key, ttl, loader)


def _bj_now():
    """当前北京时间(UTC+8), 不依赖服务器时区"""
    return time.gmtime(time.time() + 8 * 3600)


def _hhmm(ts):
    """epoch -> 'HH:MM'(北京时间); ts<=0 返回空串"""
    if not ts:
        return ""
    try:
        return time.strftime("%H:%M", time.gmtime(int(ts) + 8 * 3600))
    except Exception:                                       # noqa: BLE001
        return ""


def _mmdd(ts):
    """epoch -> 'MM-DD'(北京时间)"""
    if not ts:
        return ""
    try:
        return time.strftime("%m-%d", time.gmtime(int(ts) + 8 * 3600))
    except Exception:                                       # noqa: BLE001
        return ""


def _iso_bj_epoch(s):
    """'2026-09-27T00:22:11'(北京时间) -> epoch; 解析失败返回 0"""
    if not s:
        return 0
    try:
        dt = datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")
        return int(calendar.timegm(dt.timetuple())) - 8 * 3600
    except Exception:                                       # noqa: BLE001
        return 0


def _dedupe_key(title):
    """去重键: 去空白/标点/【】后取前 24 字(两源都是财联社系, 同一条会重复)"""
    t = str(title or "")
    for ch in " 　\t\n\r【】[]()（）:：,，。.、!！?？\"'“”‘’-—_":
        t = t.replace(ch, "")
    return t[:24]


# ---------------- 上游一: 猫爪 news ----------------
def _meoz_rows(limit=_MEOZ_NEWS_LIMIT):
    """猫爪 news -> 归一化快讯列表; 失败返回 []"""
    try:
        body = meoz.call_cached("news", params={"limit": int(limit)}, ttl=_FLASH_TTL)
    except Exception as e:                                  # noqa: BLE001
        log.warning("猫爪 news 异常 err=%s", e)
        return []
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    if not isinstance(data, dict):
        return []
    fields = data.get("fields") or []
    items = data.get("items") or []
    if not isinstance(fields, list) or not isinstance(items, list):
        return []

    def col(row, name, idx=None):
        try:
            i = fields.index(name) if idx is None else idx
            return row[i]
        except Exception:                                   # noqa: BLE001
            return None

    out = []
    for row in items:
        if not isinstance(row, list):
            continue
        title = str(col(row, "title") or "").strip()
        if not title:
            continue
        display_at = col(row, "display_at")
        ts = _iso_bj_epoch(display_at)
        out.append({
            "id": "meoz:" + str(col(row, "source_id") or ts),
            "ts": ts,
            "time_label": str(col(row, "time_label") or "").strip() or _hhmm(ts),
            "date": _mmdd(ts),
            "source": str(col(row, "source_name") or col(row, "source") or "猫爪").strip(),
            "title": title,
            "summary": str(col(row, "summary") or "").strip(),
            "url": str(col(row, "url") or "").strip(),
            "kind": "meoz",
        })
    return out


# ---------------- 上游二: 开盘啦 doc96 ----------------
def _kpl_rows():
    """开盘啦 7x24 快讯(doc96) -> 归一化快讯列表; 失败返回 []"""
    try:
        rows = kpl.fetch_kpl_news_flash()
    except Exception as e:                                  # noqa: BLE001
        log.warning("开盘啦快讯异常 err=%s", e)
        return []
    out = []
    for r in rows or []:
        ts = int(r.get("ts") or 0)
        out.append({
            "id": "kpl:" + str(r.get("cid") or ts),
            "ts": ts,
            "time_label": _hhmm(ts),
            "date": _mmdd(ts),
            "source": str(r.get("source") or "开盘啦").strip() or "开盘啦",
            "title": str(r.get("title") or "").strip(),
            "summary": str(r.get("content") or "").strip(),
            "url": "",
            "kind": "kpl",
            "stocks": r.get("stocks") or [],
        })
    return out


def _flash_uncached(limit):
    meoz_rows = _meoz_rows(max(limit, _MEOZ_NEWS_LIMIT))
    kpl_rows = _kpl_rows()
    degraded = []
    if not meoz_rows:
        degraded.append("meoz")
    if not kpl_rows:
        degraded.append("kpl")

    merged = []
    seen = set()
    # 先开盘啦(电报, 更实时), 再猫爪; 命中重复键的丢弃
    for it in kpl_rows + meoz_rows:
        k = _dedupe_key(it.get("title"))
        if not k or k in seen:
            continue
        seen.add(k)
        merged.append(it)
    # 时间倒序; ts=0 的(解析失败)排到末尾而不是顶部
    merged.sort(key=lambda x: (x.get("ts") or 0), reverse=True)
    merged = merged[:max(1, int(limit))]
    return {
        "list": merged,
        "total": len(merged),
        "degraded": degraded,          # 前端可据此显示「某源暂缺」而不是假装完整
        "updated": time.strftime("%Y-%m-%d %H:%M:%S", _bj_now()),
        "counts": {"meoz": len(meoz_rows), "kpl": len(kpl_rows)},
    }


def flash(limit=80):
    """7x24 快讯: 猫爪 news + 开盘啦 doc96 合并去重, 按时间倒序"""
    limit = max(1, min(int(limit or 80), 200))
    return _cached("flash:%d" % limit, _FLASH_TTL, lambda: _flash_uncached(limit))


def premarket():
    """盘前精选: 开盘啦头条(doc95) + 明天炒什么(doc97)"""
    return _cached("premarket", _PREMARKET_TTL, _premarket_uncached)


def _premarket_uncached():
    tops, topics, degraded = [], {"day": "", "items": []}, []
    try:
        tops = kpl.fetch_kpl_top_news()
    except Exception as e:                                  # noqa: BLE001
        log.warning("开盘啦头条异常 err=%s", e)
    if not tops:
        degraded.append("kpl_top")
    try:
        tp = kpl.fetch_kpl_topic_list()
        if isinstance(tp, dict):
            topics = tp
    except Exception as e:                                  # noqa: BLE001
        log.warning("明天炒什么异常 err=%s", e)
    if not topics.get("items"):
        degraded.append("kpl_topic")
    return {
        "top": tops,
        "topics": topics,
        "bigv_url": "/bigv",        # 大V复盘直接复用现有页面, 不重复实现
        "degraded": degraded,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S", _bj_now()),
    }


def topic_detail(topic_id):
    """明天炒什么 正文(doc99); 未命中返回 {}"""
    tid = str(topic_id or "").strip()
    if not tid or not tid.isdigit():
        return {}
    return _cached("topic:" + tid, _PREMARKET_TTL,
                   lambda: kpl.fetch_kpl_topic_detail(tid))
