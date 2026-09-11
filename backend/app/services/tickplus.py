# -*- coding: utf-8 -*-
"""
TickPlus 竞价源(plus/expert/fullbid)客户端 —— P2-1 双源并存的第二源
=================================================================================
为什么需要第二源(2026-09-11 事故实证):
    东财 push2dycalc 是竞价期唯一能给「竞价涨幅 f615 / 竞价额 f616 / 竞价量 f617」
    的源, 但它**会被接口级风控**(与出口 IP 无关, 换 IP 无用)。9/11 熔断日:
    9_25 只落 132 行(正常 5557), 选股从 132 只里选 —— 名单直接塌掉。

    TickPlus fullbid 是**竞价期唯一能给「量+额」且覆盖全市场(5562)**的第二源
    (腾讯/同花顺在竞价期量额恒 0)。两源同时采集、互补合并 → 任一源挂掉仍有全市场。

接口事实(2026-09-10/11 实测, 与文档无关, 全是实测):
  * URL: http://api.tickplus.org/plus/expert/fullbid?code=&token=xxx
  * 响应体是 **ZIP**(PK\\x03\\x04), 内含 data.json —— 不解压直接 json.loads 会得到 0 条
    (2026-09-12 补齐脚本踩过: bid_0925.json 0 条)
  * 字段: code,t(时刻),p(竞价价),pc(昨收),zf(竞价涨幅%),jv(竞价量股),je(竞价额元),
          nv,ne,bs(买卖方向)
  * **p==0 = 竞价无成交**(其 zf 会算成 -100%) → 必须过滤
  * **9:25 定格有 5~10s 发布延迟** → <09:25:05 拉取只得 9:24 残值(见 auction_snapshot
    _BID25_MIN_SEC, 两源同此约束)
  * 盘后调用返回**当日 9:25 定格**(定格快照), 故只能补历史不能当实时源

缺失字段(必须靠别的源补, 这也是双源而非替换的原因):
    name / float_mv / free_mv / board / bid_buy_amt(封单)  —— 全是静态基础数据,
    由 mv_cache(东财 f21 优先 + 腾讯 f44 兜底, P2-2)与开盘啦补。
"""
import io
import json
import os
import time
import urllib.parse
import urllib.request
import zipfile

from ..core import logger
from . import fetcher, scorer, settings

log = logger.get_logger(__name__)

BASE = "http://api.tickplus.org"
PATH = "/plus/expert/fullbid"
SRC = "tickplus_fullbid"          # 熔断器/健康监控的源名(注册于 fetcher._HEALTH)

DEFAULT_TIMEOUT = 10              # 单次全推超时(秒): 竞价窗口 10s 轮询, 不得拖慢采集
ENABLE_SWITCH = "tickplus_enabled"   # settings 开关, 默认开(双源并存是本模块唯一价值)
TOKEN_KEY = "tickplus_token"         # settings 键: token 不入库到代码(仓库公开)
TOKEN_ENV = "TICKPLUS_TOKEN"         # 环境变量优先(便于部署时不落库)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KuaixuanAuction/1.0"


def enabled():
    """是否启用 TickPlus(默认开; 关闭即完全回到单源东财, 无需改代码)"""
    return bool(settings.get(ENABLE_SWITCH, 1))


def token():
    """token 读取顺序: 环境变量 → settings 表。都为空 → 视为不可用(不发无意义请求)"""
    v = os.environ.get(TOKEN_ENV) or ""
    if not v:
        try:
            v = settings.get(TOKEN_KEY, "") or ""
        except Exception:                                     # noqa: BLE001
            v = ""
    return str(v).strip()


def _num(v):
    """任意类型 → float/None(非数值统一 None, 与 scorer.parse_float 同语义)"""
    try:
        if v is None or v == "":
            return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                      # NaN
        return None
    return f


def decode_payload(raw):
    """响应体 → list[dict]。两种形态都支持:
    ① ZIP(实测形态, 内含 data.json) ② 裸 JSON(list 或 {data:[...]})
    失败返回 []。"""
    if not raw:
        return []
    txt = None
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = z.namelist()
                txt = z.read(names[0]).decode("utf-8", "replace")
        except Exception as e:                                # noqa: BLE001
            log.warning("[TickPlus] ZIP 解压失败: %s", e)
            return []
    else:
        txt = raw.decode("utf-8", "replace")
    try:
        obj = json.loads(txt)
    except Exception:                                         # noqa: BLE001
        # 少数情况下是 NDJSON(每行一条)
        out = []
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:                                 # noqa: BLE001
                pass
        return out
    if isinstance(obj, dict):
        for k in ("data", "list", "result", "rows"):
            if isinstance(obj.get(k), list):
                return obj[k]
        return []
    return obj if isinstance(obj, list) else []


def normalize(rows):
    """原始行 → {code: {bid_change, bid_amt, bid_vol, price, t}}
    口径对齐 snapshot_bid: bid_amt **万元**(东财 f616 元 → /1e4), bid_vol 股。
    过滤: ①code 空 ②p<=0(竞价无成交, zf 恒 -100) ③涨幅绝对值 >30%(异常/新股首日)。"""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        p = _num(r.get("p"))
        if p is None or p <= 0:
            continue
        zf = _num(r.get("zf"))
        if zf is not None and (zf > 30 or zf < -30):
            continue
        je = _num(r.get("je")) or 0.0
        jv = _num(r.get("jv")) or 0.0
        out[code] = {
            "bid_change": zf,
            "bid_amt": round(je / 1e4, 4),      # 元 → 万元(与 scorer.get_bid_amt 一致)
            "bid_vol": jv,                       # 股
            "price": p,
            "t": r.get("t") or "",
        }
    return out


def fetch_fullbid(code="", timeout=DEFAULT_TIMEOUT):
    """拉一次 fullbid(全推 code 为空, 或单只)。返回 normalize 后的 map, 任何失败返回 {}。
    调用方**必须先判熔断**(fetcher._check_circuit(SRC)) —— 本函数不重复判, 便于单测注入。"""
    tok = token()
    if not tok:
        return {}
    url = BASE + PATH + "?" + urllib.parse.urlencode({"code": code, "token": tok})
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception as e:                                     # noqa: BLE001
        raise RuntimeError("TickPlus 请求失败: %s %s" % (type(e).__name__, e))
    return normalize(decode_payload(raw))


# 采集侧唯一入口: 带熔断 + 健康记录 + 计时, 任何异常都吞掉(第二源不得影响主链路)
_STATE = {"last_warn": 0.0}


def snapshot_map(timeout=DEFAULT_TIMEOUT, now=None):
    """竞价采集用的全推快照: {code: {...}}; 不可用/失败返回 {}。
    三道闸门: 开关 → 熔断 → token。任何异常返回空(绝不抛给采集主链路)。"""
    if not enabled():
        return {}
    if fetcher._check_circuit(SRC):
        return {}
    if not token():
        now = now if now is not None else time.time()
        if now - _STATE["last_warn"] > 3600:      # 每小时最多提醒一次
            _STATE["last_warn"] = now
            log.warning("[TickPlus] 未配置 token(env %s / settings %s), 跳过该源",
                        TOKEN_ENV, TOKEN_KEY)
        return {}
    t0 = time.time()
    try:
        rows = fetch_fullbid(timeout=timeout)
    except Exception as e:                                     # noqa: BLE001
        fetcher._record(SRC, False, int((time.time() - t0) * 1000))
        log.warning("[TickPlus] 采集失败(不影响东财链路): %s", e)
        return {}
    ms = int((time.time() - t0) * 1000)
    fetcher._record(SRC, True, ms)
    if not rows:
        log.warning("[TickPlus] 返回 0 条(耗时%dms): 非竞价窗口或接口异常", ms)
    return rows
