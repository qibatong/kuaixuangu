# -*- coding: utf-8 -*-
"""交易日历桥接 —— **单一事实来源**是 `backend/app/core/trade_calendar.py`。

================================ 为什么需要桥接 ================================
节假日表逐年更新，两份副本必然漂移。这里按**文件路径**动态加载 backend 的那一份，
而**不** `import app`（那会连带拉起 fastapi / PIL 等重依赖，脚本侧没必要）。

============================== 为什么脚本也需要 ==============================
2026-09-25（中秋 · 星期五）第二轮复盘发现：
  删掉 `kuaixuan.db` 里的幽灵 `snapshot_bid` 行后，取数链变成

      快选快照空  →  猫爪 screening 空  →  **东财兜底**

  而东财在休市日**依旧返回上一交易日的陈旧价格**（`price` 非 0）⇒
  `predict_daily.py` 的「`price>0` 占比 <50% 硬拦」护栏**判不出来**（实测占比≈100%），
  2026-09-25 照样产出 9 只假名单、落库 5211 行。

  backend 侧 `aipick_scheduler._is_trade_day()` 的日历门禁能挡住**自动链路**，
  但手动 `predict_daily.py --date <休市日>` / `collector.py --date <休市日>` 仍会漏。
  故脚本侧也必须有一道**日历正门**，数据护栏留作第二道防线。

================================= fail-open =================================
找不到 backend 模块时退化为「只判周一~周五」（= 改造前行为），
**绝不因为"读不到日历"而把真实交易日拦掉**。`SOURCE` 记录了实际加载到哪一份，便于排查。
"""

from __future__ import annotations

import os
import time
from datetime import date as _date
from typing import Optional, Union

__all__ = ["is_holiday", "is_trade_day", "is_trade_day_of", "bj_date", "SOURCE"]

_HERE = os.path.dirname(os.path.abspath(__file__))


def _candidate_paths():
    """按优先级给出 backend 交易日历模块的候选路径。"""
    env = os.environ.get("KX_BACKEND_DIR")
    if env:
        yield os.path.join(env, "app", "core", "trade_calendar.py")
    # 仓库 / 生产机同布局: <root>/scripts/aipick  ↔  <root>/backend
    yield os.path.join(_HERE, "..", "..", "backend", "app", "core", "trade_calendar.py")
    # 生产机兜底绝对路径（aipick 独立部署在 /opt/kuaixuan/aipick）
    yield "/opt/kuaixuan/backend/app/core/trade_calendar.py"


def _load():
    """动态加载 backend 的 trade_calendar；失败返回 (None, None)。"""
    import importlib.util
    for p in _candidate_paths():
        p = os.path.normpath(p)
        if not os.path.isfile(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_kx_be_trade_calendar", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "is_trade_day") and hasattr(mod, "is_holiday"):
                return mod, p
        except Exception:                              # noqa: BLE001
            continue
    return None, None


_impl, SOURCE = _load()


def _norm(d) -> Optional[str]:
    """各种日期表示 → `YYYY-MM-DD`；无法识别返回 None。"""
    if d is None:
        return None
    if isinstance(d, _date):
        return d.isoformat()
    if isinstance(d, time.struct_time):
        return "%04d-%02d-%02d" % (d.tm_year, d.tm_mon, d.tm_mday)
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def bj_date(now_ts: Optional[float] = None) -> str:
    """当前北京时间日期 `YYYY-MM-DD`（UTC+8，与服务器时区无关）。"""
    g = time.gmtime((time.time() if now_ts is None else float(now_ts)) + 8 * 3600)
    return "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)


def is_holiday(d) -> bool:
    """是否**法定休市日**（不含周末）。日历不可用时返回 False（fail-open）。"""
    if _impl is None:
        return False
    try:
        return bool(_impl.is_holiday(_norm(d) or d))
    except Exception:                                  # noqa: BLE001
        return False


def is_trade_day(d) -> bool:
    """是否交易日 = 周一~周五 且非法定休市日。

    ★ 即便 backend 日历加载失败，**周末判定仍生效**（保留改造前行为）。
    """
    s = _norm(d)
    if s and _date.fromisoformat(s).weekday() >= 5:
        return False
    if _impl is None:
        return True                                    # fail-open: 退化为"只判周几"
    try:
        return bool(_impl.is_trade_day(s or d))
    except Exception:                                  # noqa: BLE001
        return True


def is_trade_day_of(g: time.struct_time) -> bool:
    """吃北京时间 struct_time（调用方已 `+8*3600`）。"""
    wday = getattr(g, "tm_wday", None)
    if isinstance(wday, int) and wday >= 5:
        return False
    if _impl is not None:
        try:
            return bool(_impl.is_trade_day_of(g))
        except Exception:                              # noqa: BLE001
            pass
    return is_trade_day(g)
