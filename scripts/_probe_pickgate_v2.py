# -*- coding: utf-8 -*-
"""选股闸门 v2(前后端开关联动) 部署预检 —— **只读**: 不写库、不重启、不签发 token。

验证 4 件事:
  ① `_pick_window_guard_on()` 存在(单一口径处)
  ② 开关解析口径正确 —— 含字符串假值 "0"/"false"/"off"/"" (原 bool() 的坑)
     用 mock.patch 替换 settings.get, **不碰数据库**
  ③ ping 分支已透出 pickGateEnabled(前端据此决定置灰)
  ④ 闸门分支已改用统一 helper(与 ping 同口径, 防漂移)
最后打印库内两个开关的真实取值, 供人工比对。
"""
import inspect
import sys
from unittest import mock

sys.path.insert(0, "/opt/kuaixuan/backend")

import app.api.stocks as S                      # noqa: E402
from app.services import settings as st         # noqa: E402

fails = []

# ① ---------------------------------------------------------------
if hasattr(S, "_pick_window_guard_on"):
    print("OK    _pick_window_guard_on 存在")
else:
    print("FAIL  缺少 _pick_window_guard_on(部署未生效?)")
    sys.exit(1)

# ② ---------------------------------------------------------------
print("---- 开关解析口径(mock settings.get, 不写库) ----")
cases = [(0, False), ("0", False), ("false", False), ("off", False), ("", False),
         (1, True), ("1", True), (True, True), (2, True)]
for v, exp in cases:
    with mock.patch.object(st, "get", lambda k, d=None, _v=v: _v):
        got = S._pick_window_guard_on()
    ok = got is exp
    print("  %s  get()=%-8r -> %s   (期望 %s)" % ("OK  " if ok else "FAIL", v, got, exp))
    if not ok:
        fails.append(v)

# ③ ④ ------------------------------------------------------------
src = inspect.getsource(S)
if "pickGateEnabled" in src:
    print("OK    ping 分支含 pickGateEnabled")
else:
    print("FAIL  ping 分支未透出 pickGateEnabled")
    fails.append("ping")

if "if _pick_window_guard_on():" in src:
    print("OK    闸门分支使用统一 helper")
else:
    print("FAIL  闸门分支未使用 helper(口径可能漂移)")
    fails.append("branch")

# ⑤ ---------------------------------------------------------------
print("---- 库内真实取值(只读) ----")
v = st.get(S.PICK_WINDOW_SWITCH, "(无键->默认1=开)")
print("  pick_window_guard = %r  -> 闸门生效=%s" % (v, S._pick_window_guard_on()))
print("  use_bid_strength  = %r" % (st.get("use_bid_strength"),))
try:
    from app.services import bid_strength as B
    print("  bid_strength.enabled() = %s" % (B.enabled(),))
except Exception as e:                                   # noqa: BLE001
    print("  bid_strength.enabled() 读取失败: %s" % e)

if fails:
    print("\n>>> PROBE FAILED: %s" % (fails,))
    sys.exit(1)
print("\n>>> PROBE ALL PASS")
