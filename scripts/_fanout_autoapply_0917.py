# -*- coding: utf-8 -*-
"""9/17 扇出补跑(主人已批准): 给所有未过期非管理员用户各落一条当日 auto_applied 批次。

前置已干跑确认(_dryrun_autoapply_0917.py): auto_apply 会推出的名单 == 9451 的 3 只
(西陇科学/黑猫股份/芒果超媒) → **不会造成名单分裂**。

写操作边界: 只经 `history.save_batch(uid,'lock',result,f,auto_applied=True)` 落批;
`system_batch`/`auto_apply` 内**无推送逻辑**(已读源码确认)。已应用用户自动跳过(幂等)。
本脚本额外做**逐批次名单一致性校验** —— 不只数条数, 要比对每一批的票。
"""
import json
import sqlite3
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import auto_apply as AA          # noqa: E402
from app.core import config                        # noqa: E402

TODAY = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
EXPECT = ("002584", "002068", "300413")            # 9451 的名单(升序 rank)


def q(sql, p=()):
    c = sqlite3.connect("file:%s?mode=ro" % config.DB_FILE, uri=True)
    try:
        return list(c.execute(sql, p))
    finally:
        c.close()


def dump(tag):
    n = q("select count(*) from batches where batch_date=? and auto_applied=1", (TODAY,))[0][0]
    u = q("select count(distinct user_id) from batches where batch_date=? and auto_applied=1",
          (TODAY,))[0][0]
    print("  %-6s auto_applied 批数=%-4s 覆盖用户数=%s" % (tag, n, u))


print("today =", TODAY)
print("=== 扇出前 ===")
dump("BEFORE")

print("\n=== 执行 auto_apply_all_users() ===")
t0 = time.time()
res = AA.auto_apply_all_users()
print("  耗时 %.1fs" % (time.time() - t0))
print("  返回:", json.dumps(res, ensure_ascii=False))

print("\n=== 扇出后 ===")
dump("AFTER")

print("\n=== 一致性校验(不只数条数, 逐批次比名单) ===")
print("  stock_count 分布:",
      q("select stock_count, count(*) from batches where batch_date=? and auto_applied=1 "
        "group by stock_count", (TODAY,)))
ids = [r[0] for r in q("select id from batches where batch_date=? and auto_applied=1 "
                       "and user_id>0 order by id", (TODAY,))]
bad = []
for b in ids:
    codes = tuple(r[0] for r in q("select code from batch_stocks where batch_id=? order by rank", (b,)))
    if codes != EXPECT:
        bad.append((b, codes))
print("  新增批次数 =", len(ids))
print("  与 9451 名单不一致的批次数 =", len(bad), bad[:3] if bad else "")
print("  每批是否都是 3 只:", all(len(r[0]) == 3 for r in
      [(tuple(x[0] for x in q('select code from batch_stocks where batch_id=?', (b,))),) for b in ids]) if ids else "(无)")

print("\n=== 分布确认(users 表对齐) ===")
print("  非管理员且未过期 =",
      q("select count(*) from users where is_admin=0 and (expire_at=0 or expire_at>=?)",
        (int(time.time()),))[0][0])
print("  跳过原因抽样(应为已过期/管理员) —— 见上面返回的 skipped 数")
print("\nDONE")
