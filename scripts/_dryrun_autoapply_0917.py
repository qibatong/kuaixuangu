# -*- coding: utf-8 -*-
"""扇出前干跑(只读): 确认 auto_apply 会推出的名单 == 用户已通过 src=auto 拿到的 9451。

为什么必须先干跑: `auto_apply.auto_apply_all_users()` 内部会**重新算一次**系统名单
(`_pick_result()` -> `plock.run_lock()`, 纯计算无写库)。若这份名单与 9451 不一致,
扇出就会造成「一部分用户看 A 名单、另一部分看 B 名单」的事实分裂。
本脚本只读: 比过滤条件 + 比名单, **不写任何批次**。
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import auto_apply as AA          # noqa: E402
from app.services import system_batch as SB        # noqa: E402
from app.core import config                        # noqa: E402

print("=== ① 两处「系统过滤」是否等价 ===")
f_sys = SB._system_filter()
f_auto = AA._get_system_filter()
print("  system_batch._system_filter  :", json.dumps(f_sys, ensure_ascii=False, sort_keys=True))
print("  auto_apply._get_system_filter:", json.dumps(f_auto, ensure_ascii=False, sort_keys=True))
same = f_sys == f_auto
print("  SAME =", same)
if not same:
    only_sys = {k: v for k, v in f_sys.items() if f_auto.get(k) != v}
    only_auto = {k: v for k, v in f_auto.items() if f_sys.get(k) != v}
    print("  system_batch 独有/不同:", json.dumps(only_sys, ensure_ascii=False, sort_keys=True))
    print("  auto_apply   独有/不同:", json.dumps(only_auto, ensure_ascii=False, sort_keys=True))

print("\n=== ② 干跑 auto_apply 会推出的名单(纯计算, 不写库) ===")
try:
    items, err = AA._pick_result()
    codes = [getattr(i, "code", None) or (i.get("code") if isinstance(i, dict) else None)
             for i in items]
    print("  err =", repr(err))
    print("  len =", len(items))
    print("  codes =", codes)
except Exception as e:                                     # noqa: BLE001
    import traceback
    traceback.print_exc()
    print("  干跑异常:", e)

print("\n=== ③ 9451(用户当前通过 src=auto 拿到的) 明细 ===")
c = sqlite3.connect("file:%s?mode=ro" % config.DB_FILE, uri=True)
rows = list(c.execute("select rank,code,name,probability from batch_stocks "
                      "where batch_id=9451 order by rank"))
for r in rows:
    print("   ", r)
b = c.execute("select id,batch_date,batch_time,stock_count,auto_applied from batches "
              "where id=9451").fetchone()
print("  批次头:", b)

print("\n=== ④ 结论 ===")
try:
    set_dry = set(codes)
    set_9451 = set(r[1] for r in rows)
    print("  干跑名单 == 9451 名单 ?", set_dry == set_9451)
    if set_dry != set_9451:
        print("  ⚠️ 仅干跑有:", sorted(set_dry - set_9451))
        print("  ⚠️ 仅 9451 有:", sorted(set_9451 - set_dry))
except Exception as e:                                     # noqa: BLE001
    print("  比较失败:", e)

print("\n=== ⑤ 今日 auto_applied 现状 ===")
print("  今日 auto_applied=1 批数 =",
      c.execute("select count(*) from batches where batch_date='2026-09-17' and auto_applied=1")
      .fetchone()[0])
print("  扇出候选(非管理员且未过期) =",
      c.execute("select count(*) from users where is_admin=0 and (expire_at=0 or expire_at>=?)",
                (int(__import__("time").time()),)).fetchone()[0])
c.close()
print("\nDONE (未写任何数据)")
