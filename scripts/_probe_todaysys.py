# -*- coding: utf-8 -*-
"""④「当日名单优先于跨日回退」部署预检 —— **只读**: 不写库、不重启、不签发 token。

验证 4 件事:
  ① `history.find_today_system_batch` 存在
  ② **实盘功能值**: 拿真库当前值 —— 今日有系统批次时须返回 (batch_id, 'auto')
  ③ `api/stocks.py` 的回退链里**确实**插入了这一步
  ④ **顺序正确**: 它必须出现在 `find_recent_reusable_batch` **之前**
     (插反了等于没修 —— 仍会先回退昨日名单)
"""
import inspect
import sys

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import history as H          # noqa: E402
from app.api import stocks as S                # noqa: E402

fails = []

# ① ---------------------------------------------------------------
if hasattr(H, "find_today_system_batch"):
    print("OK    history.find_today_system_batch 存在")
else:
    print("FAIL  缺少 find_today_system_batch(部署未生效?)")
    sys.exit(1)

# ② 实盘值(只读) ---------------------------------------------------
try:
    bid, src = H.find_today_system_batch()
    print("OK    实盘调用 -> 今日系统批次 = %r  源 = %r" % (bid, src))
    if bid is None:
        print("      (今日尚无系统批次 —— 非失败, 仅说明当前无当日兜底可给)")
    elif src != "auto":
        print("FAIL  源应为 'auto', 实得 %r" % (src,))
        fails.append("source")
except Exception as e:                                  # noqa: BLE001
    print("FAIL  实盘调用异常: %s" % e)
    fails.append("call")

# ③④ 源码断言 ------------------------------------------------------
src = inspect.getsource(S)
if "find_today_system_batch" in src:
    print("OK    api/stocks.py 已调用 find_today_system_batch")
else:
    print("FAIL  api/stocks.py 未调用 find_today_system_batch")
    fails.append("call-site")

try:
    i_new = src.index("history.find_today_system_batch()")
    i_old = src.index("history.find_recent_reusable_batch(")
    if i_new < i_old:
        print("OK    顺序正确: 当日系统名单(偏移%d) 在 跨日回退(偏移%d) **之前**" % (i_new, i_old))
    else:
        print("FAIL  顺序颠倒! 当日系统名单(%d) 在跨日回退(%d) 之后 → 等于没修" % (i_new, i_old))
        fails.append("order")
except ValueError as e:
    print("FAIL  源码定位失败(方法名变了?): %s" % e)
    fails.append("locate")

# 附带: 当日复用 ③ 的 `if not rows` 仍在(修复方式是"另加一步", 不是改 ③) ----------
t = inspect.getsource(H.find_today_reusable_batch)
if "if not rows:" in t:
    print("OK    当日版 ③ 的 `if not rows:` 保持不变(修复是另加一步, 未改动 ③ 语义)")
else:
    print("WARN  当日版 ③ 的 `if not rows:` 不在了 —— 请确认是否有意改动(会破坏'改条件必须重算')")

if fails:
    print("\n>>> PROBE FAILED: %s" % (fails,))
    sys.exit(1)
print("\n>>> PROBE ALL PASS")
