# -*- coding: utf-8 -*-
"""ZH 竞价选股策略(2026-09-29 主人定的口径, 见优化清单「结论 13」)。

入选条件(全部满足):
  ① 高开 ≥ 3%              —— 竞价涨幅 `bid_change`(可配 `zhBidGt`)
  ② 占昨量 ∈ [5%, 10%]     —— **竞价成交量 ÷ 昨日成交量 ×100**(可配 `zhVolPctFloor/Gt`)

  🔴 口径订正(2026-09-29 15:1x): 首版我写成"竞价**额** ÷ 昨日**额**", 实测与 14:2x 的回放基线
     差 5~13%(高开票的竞价价高于昨均价 ⇒ 额比额系统性偏大) ⇒ 把 000678/603949 顶出 10% 上限。
     回放脚本(`zh_replay2.sh:120`)用的是「量/量」: `(竞价额/竞价价/100) / 昨量(手)` ——
     "放量占昨量"的原意也是**量**, 故默认改为 `VOL_MODE="vol"`; 需要额口径时传 `{"zhVolMode":"amt"}`。
     竞价价用 `昨收×(1+高开/100)`(9:25 撮合后竞价成交价=开盘价, 与回放同式)。
  ③ 涨停基因: 近 120 个交易日内 ≥1 次涨停(可配 `zhZtGeneDays/Min`)
  ④ 非 ST(名称含 ST 即剔除)
  ⑤ 板块自适应涨停幅度: 300/301/688/689 → 20%, 920/8/4 → 30%, 其余 10%

判涨停用「**四舍五入涨停价式**」(`auction_snapshot.is_zt_by_price`), 依据见结论 13 的三方对拍:
主人原式多判 28 次、项目旧阈值式**漏判 198 次(≈2.1%)**、四舍五入式最准 —— 判据真相源已上线
(git `23f53f0`), 本模块只调用, 不另写一份。

数据来源(**只读**):
  · 今日 9:25 定格: `snapshot_bid(date, time_point='9_25')`(bid_amt 单位**万元**)
  · 日K: `stock_kline.day_data` —— **直读缓存**, 刻意不走 `stock_temper._kline()`
    (它带"陈旧即回源"的新鲜度逻辑, 盘后跑全市场会为 4000+ 只逐只回源打爆上游)
    东财 amount 单位是**元** ⇒ 统一 /1e4 转万元, 与 bid_amt 同单位后再算占比。

设计: 纯函数 + 只读访问, 不碰采集/落库/评分 ⇒ 可回放、可单测、可被 pipeline/接口复用。
"""
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...core import logger
from ...db import database
from .. import auction_snapshot as A

log = logger.get_logger(__name__)

# 兜底默认(真相源在 filter_defaults.FILTER_DEFAULTS, 管理员可改; 此处仅防"键缺失")
DEFAULTS = {
    "zhBidGt": 3.0,         # 高开下限(%)
    "zhVolPctFloor": 5.0,   # 占昨量下限(%)
    "zhVolPctGt": 10.0,     # 占昨量上限(%)
    "zhZtGeneDays": 120,    # 涨停基因回看交易日数
    "zhZtGeneMin": 1,       # 窗口内最少涨停次数
}

# 占昨量的口径: "vol"=竞价量/昨量(手/手, **与 14:2x 回放基线一致, 默认**);
#               "amt"=竞价额/昨额(万元/万元, 保留可选)。
# 不放进 FILTER_DEFAULTS: 那是数值型参数表(lock.to_picker_filters 会按 _num 归一), 字符串会被吃掉。
VOL_MODE = "vol"

# K 线元组下标(见 ordered_klines)
_D, _O, _C, _H, _L, _V, _A = range(7)


# ---------------------------------------------------------------- 参数/工具
def _cfg(f: Optional[Dict] = None) -> Dict[str, float]:
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        v = (f or {}).get(k)
        if v is None or v == "":
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue          # 脏值 → 保留兜底, 不抛(与 lock.to_picker_filters 同纪律)
    return out


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f        # NaN → None


def board_of(code: str) -> str:
    """板块标签(展示用; 涨停幅度一律走 zt_limit_pct 单一真相源, 不在这里判)"""
    s = str(code or "").zfill(6)
    if s[:1] in ("4", "8") or s[:3] == "920":
        return "北交"
    if s[:3] in ("300", "301"):
        return "创业"
    if s[:3] in ("688", "689"):
        return "科创"
    return "主板"


def is_st(name: Optional[str]) -> bool:
    return "ST" in (name or "").upper()


def ordered_klines(day_data: Any) -> List[Tuple]:
    """把 `stock_kline.day_data` 标准化为按日期升序的
    [(date, open, close, high, low, volume, amount), ...]; 异常/空返回 []。"""
    if not day_data:
        return []
    if isinstance(day_data, (str, bytes)):
        try:
            day_data = json.loads(day_data)
        except (ValueError, TypeError):
            return []
    if not isinstance(day_data, dict):
        return []
    t = day_data.get("time") or []
    c = day_data.get("close") or []
    o = day_data.get("open") or []
    h = day_data.get("high") or []
    lo = day_data.get("low") or []
    v = day_data.get("volume") or []
    a = day_data.get("amount") or []
    n = min(len(t), len(c))
    rows = []
    for i in range(n):
        rows.append((str(t[i]),
                     o[i] if i < len(o) else None,
                     c[i],
                     h[i] if i < len(h) else None,
                     lo[i] if i < len(lo) else None,
                     v[i] if i < len(v) else None,
                     a[i] if i < len(a) else None))
    rows.sort(key=lambda r: r[_D])
    return rows


def yesterday_amount(klines: List[Tuple], today: Optional[str] = None) -> Optional[float]:
    """昨日全天成交额(**万元**)。

    取最后一根"已收盘"K 线(日期 < today, 缺 today 时取最后一根);
    东财 amount 单位是元 ⇒ /1e4 转万元(与 `snapshot_bid.bid_amt` 同单位, 避免 1e4 级错误)。
    """
    if not klines:
        return None
    if today:
        t = str(today)
        rows = [r for r in klines if r[_D] < t]
    else:
        rows = list(klines)
    if not rows:
        return None
    amt = _num(rows[-1][_A])
    if amt is None or amt <= 0:
        return None
    return amt / 1e4


def yday_volume(klines: List[Tuple], today: Optional[str] = None) -> Optional[float]:
    """昨日成交量(**手**)。取最后一根"已收盘"K 线(日期 < today); 东财 volume 单位是手。"""
    if not klines:
        return None
    rows = [r for r in klines if r[_D] < str(today)] if today else list(klines)
    if not rows:
        return None
    v = _num(rows[-1][_V])
    return v if (v is not None and v > 0) else None


def bid_volume_lots(amt_wan: Any, bid_change: Any, prev_close: Any) -> Optional[float]:
    """竞价成交量(手) = 竞价额(元) ÷ 竞价价 ÷ 100, 其中 竞价价 = 昨收 × (1 + 高开/100)。

    9:25 撮合后竞价成交价即开盘价, 与 14:2x 回放同式(`zh_replay2.sh:120`)。
    """
    a, pc, chg = _num(amt_wan), _num(prev_close), _num(bid_change)
    if a is None or pc is None or chg is None or pc <= 0:
        return None
    px = pc * (1 + chg / 100.0)
    if px <= 0:
        return None
    return (a * 1e4) / px / 100.0


def zt_gene(code: str, klines: List[Tuple], days: float = 120,
            min_cnt: float = 1, name: str = "") -> Tuple[int, bool]:
    """涨停基因: 近 `days` 个交易日内的涨停次数 ≥ `min_cnt` ⇒ 达标。

    判涨停 = 四舍五入涨停价式(**半数向下**, 见 `auction_snapshot.zt_price`) ——
    前收 = 上一根 K 线收盘价, 板块幅度自适应(ST 需传 `name`)。
    返回 (涨停次数, 是否达标); K 线不足 2 根 ⇒ (0, False)。
    """
    try:
        n = int(days)
    except (TypeError, ValueError):
        n = 120
    rows = klines[-(n + 1):] if n > 0 else klines      # 多取一根作前收
    cnt = 0
    for i in range(1, len(rows)):
        if A.is_zt_by_price(code, rows[i][_C], rows[i - 1][_C], name=name) is True:
            cnt += 1
    try:
        need = int(min_cnt)
    except (TypeError, ValueError):
        need = 1
    return cnt, cnt >= need


# ---------------------------------------------------------------- 选股
def select(rows: List[Dict], kline_of: Callable[[str], Any],
           f: Optional[Dict] = None, today: Optional[str] = None) -> Tuple[List[Dict], Dict]:
    """对 9:25 定格行做 ZH 筛选。

    rows:     [{"code","name","bid_change","bid_amt","float_mv", ...}]（bid_amt 单位万元）
    kline_of: code -> day_data(dict 或 JSON 串); 惰性调用(只在通过高开后取)
    返回 (picks, stats); picks 按**占昨量降序**(与结论 13 的回放同序)。
    """
    cfg = _cfg(f)
    picks: List[Dict] = []
    stats = {"total": len(rows), "drop_name": 0, "drop_chg": 0, "drop_nokline": 0,
             "drop_noamt": 0, "drop_vol": 0, "drop_gene": 0, "kept": 0}
    for r in rows:
        # 代码先做形状校验再补零: 空/非数字一律剔(否则 "" 会被 zfill 成 "000000" 混进名单 ✗);
        # 纯数字短码(上游可能给 int, 如 678)必须补成 000678, 不然整批带前导零的票会被漏选。
        raw = str(r.get("code") or "").strip()
        if not raw.isdigit() or not (1 <= len(raw) <= 6):
            stats["drop_name"] += 1
            continue
        code = raw.zfill(6)
        name = r.get("name") or ""
        if is_st(name):
            stats["drop_name"] += 1
            continue
        chg = _num(r.get("bid_change"))
        if chg is None or chg < cfg["zhBidGt"]:
            stats["drop_chg"] += 1
            continue
        kl = ordered_klines(kline_of(code))
        if len(kl) < 2:
            stats["drop_nokline"] += 1
            continue
        amt = _num(r.get("bid_amt"))
        if not amt or amt <= 0:
            stats["drop_noamt"] += 1
            continue
        mode = str((f or {}).get("zhVolMode") or VOL_MODE).lower()
        ya = yesterday_amount(kl, today)                     # 万元(展示用)
        yvol = yday_volume(kl, today)                        # 手
        if mode == "amt":
            if not ya:
                stats["drop_noamt"] += 1
                continue
            vp = amt / ya * 100.0
        else:
            bvol = bid_volume_lots(amt, chg, kl[-1][_C])
            if not yvol or bvol is None:
                stats["drop_noamt"] += 1
                continue
            vp = bvol / yvol * 100.0
        if not (cfg["zhVolPctFloor"] <= vp <= cfg["zhVolPctGt"]):    # 含边界(回放有恰好 5.00%)
            stats["drop_vol"] += 1
            continue
        cnt, ok = zt_gene(code, kl, cfg["zhZtGeneDays"], cfg["zhZtGeneMin"], name=name)
        if not ok:
            stats["drop_gene"] += 1
            continue
        picks.append({
            "code": code,
            "name": name,
            "board": board_of(code),
            "bidChange": round(chg, 2),
            "volPct": round(vp, 2),
            "volMode": mode,
            "bidAmt": round(amt, 1),
            "ydayAmt": round(ya, 1) if ya else None,
            "ydayVol": int(yvol) if yvol else None,
            "ztGene": int(cnt),
            "price": _num(r.get("price")),
            "prevClose": _num(kl[-1][_C]),
            "ydayDate": kl[-1][_D],
        })
    picks.sort(key=lambda x: -x["volPct"])
    stats["kept"] = len(picks)
    return picks, stats


def _kline_provider(conn) -> Callable[[str], Any]:
    """惰性 + 记忆化的 day_data 读取器(只读缓存, 不回源)"""
    memo: Dict[str, Any] = {}

    def get(code: str):
        if code not in memo:
            row = conn.execute("SELECT day_data FROM stock_kline WHERE code=?",
                               (code,)).fetchone()
            memo[code] = row[0] if row else None
        return memo[code]

    return get


def run(date: Optional[str] = None, f: Optional[Dict] = None,
        time_point: str = "9_25", conn=None) -> Tuple[List[Dict], Dict, Dict]:
    """只读跑一次 ZH 选股。返回 (picks, stats, meta)。"""

    own = conn is None
    if own:
        conn = database.get_conn()
    try:
        d = date or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        cur = conn.execute(
            "SELECT code, name, bid_change, bid_amt, float_mv, free_mv "
            "FROM snapshot_bid WHERE date=? AND time_point=?", (d, time_point))
        rows = [{"code": r[0], "name": r[1], "bid_change": r[2], "bid_amt": r[3],
                 "float_mv": r[4], "free_mv": r[5]} for r in cur.fetchall()]
        picks, stats = select(rows, _kline_provider(conn), f=f, today=d)
        meta = {"date": d, "time_point": time_point,
                "snapshot_rows": len(rows), "cfg": _cfg(f)}
        log.info("ZH 选股 date=%s tp=%s 快照%d行 → 入选%d只 %s",
                 d, time_point, len(rows), len(picks), stats)
        return picks, stats, meta
    finally:
        if own and conn is not None:
            conn.close()
