# -*- coding: utf-8 -*-
"""pick_daily 读取接口（2026-10-03 上线）。

用途：把每日四线名单的**结果信息**暴露给前端（产品定位 = 涨停评分排序，**不是交易策略引擎**）
  · 当日是否封板 is_limit_up（官方涨停价口径）—— **本接口只暴露这一项"结果"**
  · rank / score / 竞价与行情字段（排序与展示所需）
  · 🔴 2026-10-03 主人指令：以下一律**不进展示层**，接口不再返回：
      fillGrade / fillGradeText / isYidzi / simFilled / buyable_only（可买性分级）
      retClose / retNext（按 9:25 竞价价买入的持有期收益）、sell_advice（建议卖出规则）
    依据：① 实测 93% 的"竞价一字"当天会开板，标"买不进"或剔除 = 藏起最强的票；
          ② 可买性/收益/卖出规则属交易策略口径，不属"评分排序名单"。
    相关 DB 列保留，仅供内部统计与诊断。

🔴 只读、无出网；表不存在/文件缺失一律返回空结构（绝不 500）。
"""
import os
import sqlite3

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/pick", tags=["pick"])

DB = os.environ.get("AIPICK_DB_PATH", "/opt/kuaixuan/aipick/scripts/data/aipick.db")

def _conn():
    if not os.path.exists(DB):
        return None
    try:
        c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=5)
        return c
    except Exception:
        return None


def _has_table(c, name):
    try:
        return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception:
        return False


@router.get("/daily")
def daily(date: str = Query('', description='交易日 YYYYMMDD 或 YYYY-MM-DD，空=最新'),
          line: str = Query('', description='xgb/lgb/zh/yj，空=全部'),
          top: int = Query(50, ge=1, le=200),
          ):
    c = _conn()
    if c is None or not _has_table(c, 'pick_daily'):
        return {"ok": False, "reason": "pick_daily 不可用", "dates": [], "items": []}
    try:
        days = [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM pick_daily ORDER BY trade_date DESC LIMIT 60")]
        d = date.strip()
        if d:
            d8 = ''.join(ch for ch in d if ch.isdigit())[:8]
            hit = next((x for x in days if ''.join(ch for ch in str(x) if ch.isdigit())[:8] == d8), None)
            if hit is None:
                # 🔴 指定日无数据 → 明确返回空，**绝不回退到最新日**（否则标签会张冠李戴到别的交易日）
                return {"ok": False, "reason": "该交易日无 pick_daily 数据", "date": '',
                        "dates": days, "count": 0, "items": []}
            d = hit
        else:
            d = days[0] if days else ''
        q = ("SELECT line, code, name, rank, score, bid_change, bid_amount, bid_turnover, price, "
             "up_limit, auc_price, is_limit_up "
             "FROM pick_daily WHERE trade_date=? ")
        args = [d]
        if line:
            q += "AND line=? "
            args.append(line.strip())
        q += "ORDER BY line, rank LIMIT ?"
        args.append(top * 8 if not line else top)     # 先多取，再按线路裁剪到各自 top
        per_line = {}
        items = []
        for r in c.execute(q, args):
            if per_line.get(r[0], 0) >= top:
                continue
            per_line[r[0]] = per_line.get(r[0], 0) + 1
            items.append({
                "line": r[0], "code": r[1], "name": r[2], "rank": r[3], "score": r[4],
                "bidChange": r[5], "bidAmount": r[6], "bidTurnover": r[7], "price": r[8],
                "upLimit": r[9], "aucPrice": r[10],
                "isLimitUp": None if r[11] is None else bool(r[11]),
            })
        return {"ok": True, "date": d, "dates": days, "count": len(items),
                "items": items}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:120], "dates": [], "items": []}
    finally:
        try:
            c.close()
        except Exception:
            pass
