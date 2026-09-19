# -*- coding: utf-8 -*-
"""v4.11.33 只读验证: 选股补丁源是否已从 tencent_point 换成 eastmoney_realtime。

背景: `eastmoney_realtime` 是 CLOSED/LOCKED/INTRADAY 的 `source_priority[1]`(补丁源首选),
但它走 `fetch_raw_by_codes` → 写死被封域名 → **必然失败** → 每轮都退 `tencent_point`,
而腾讯**无 f630**。修完域名后, 补丁源应当变成 `eastmoney_realtime`。

只读; 不改配置、不落库、不写批次。运行:
  cd /opt/kuaixuan/backend && PYTHONPATH=/opt/kuaixuan/backend /opt/bid-venv/bin/python <本文件>
"""
import json
import logging
import sqlite3
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

from app.services import bid_strength, scorer                  # noqa: E402
from app.services.picker import pipeline                       # noqa: E402

DB = "/opt/kuaixuan/kuaixuan.db"
KEYS = ("scoreFloor", "probLt", "confLt", "bidAmtFloor", "floatMvFloor", "floatMvGt",
        "priceGt", "bidGt", "bidLt", "limitUp", "stSuspend")


def prefs_of(uid):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT username, filter_prefs FROM users WHERE id=?", (uid,)).fetchone()
        raw = r["filter_prefs"] if r else None
        try:
            return (r["username"] if r else None), (json.loads(raw) if raw else None)
        except Exception:                                      # noqa: BLE001
            return (r["username"] if r else None), None
    finally:
        c.close()


def main():
    print("=" * 78)
    print("异动口径开关: use_bid_strength enabled() = %s" % bid_strength.enabled())
    print("=" * 78)

    ctx = pipeline.load_context()
    print("上下文: date=%s  定格=%d只" % (ctx.date, len(ctx.day_bid_change)))
    print()

    for uid in (49, 6):
        name, p = prefs_of(uid)
        q = {"markets": ["hs,cyb,kcb"]}
        for k in KEYS:
            if p and p.get(k) is not None:
                q[k] = [str(p[k])]
        f = scorer.validate_filters(q)
        t0 = time.time()
        res = pipeline.run(f, ctx=ctx)
        dt = (time.time() - t0) * 1000
        print("uid=%-3s %-12s scoreFloor=%-5s → 全市场=%-5d 候选=%-4d 入选=%-3d 降级=%s  (%.0fms)"
              % (uid, name, f.get("scoreFloor"), res.n_universe, res.n_candidate,
                 len(res.items), res.degraded, dt))
        print("      🔑 数据源 = %s" % (",".join(res.sources) or "(无)"))
        if res.errors:
            print("      ⚠️ errors = %s" % json.dumps(res.errors, ensure_ascii=False))
        # 关键判定: 补丁源是东财还是腾讯
        if "eastmoney_realtime" in res.sources:
            print("      ✅ 补丁源 = eastmoney_realtime（东财点查**成功**, 带 f630）")
        elif "tencent_point" in res.sources:
            print("      🔴 补丁源 = tencent_point（东财点查仍失败, 无 f630 —— 未修好）")
        else:
            print("      ⚠️ 补丁源缺失（名单仅由定格源构成）")
        print("      剔除统计 = %s" % json.dumps(res.stats, ensure_ascii=False))
        if res.items:
            print("      名单:")
            for it in res.items[:10]:
                print("        %-7s %-7s 分=%-5s 异动档=%-3s 竞额=%-9s 现涨=%s"
                      % (it.get("code"), it.get("name"), it.get("probability"),
                         it.get("warnType"), it.get("bidAmt"), it.get("realChange")))
        print()


if __name__ == "__main__":
    main()
