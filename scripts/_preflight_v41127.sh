#!/bin/bash
# v4.11.27 部署前**只读**预检 —— 选股闸门口径重做 + auto_apply 幂等锁 + ④ 保留项
#
# 用法: bash _preflight_v41127.sh
# 退出码: 0 = 全部通过; 1 = 有 FAIL(此时**不要**重启/换盘)
#
# 🔴 最有价值的一步是 P3「行为断言」: 直接 import 改动后的 mode.py, 用注入时刻验
#    9:15:00-09:24:59 必须放行。v4.11.22 的事故就是把这段封死, 符号检查查不出这种错。
set -u
APP="${KX_APP:-/opt/kuaixuan}"
BE="$APP/backend"
if [ -x "${KX_VENV:-/opt/kuaixuan-venv/bin/python}" ]; then PY="${KX_VENV:-/opt/kuaixuan-venv/bin/python}"
elif [ -x /opt/bid-venv/bin/python ]; then PY=/opt/bid-venv/bin/python
else PY=$(command -v python3 || command -v python); fi

FAIL=0
must_have() {   # must_have <相对路径> <模式> <描述>
  n=$(grep -c -- "$2" "$BE/$1" 2>/dev/null)
  if [ "${n:-0}" -gt 0 ]; then printf 'OK   %s\n' "$3"
  else printf 'FAIL %s  (%s 中未找到 %s)\n' "$3" "$1" "$2"; FAIL=1; fi
}
must_gone() {   # must_gone <相对路径> <模式> <描述>
  n=$(grep -c -- "$2" "$BE/$1" 2>/dev/null)
  if [ "${n:-0}" -eq 0 ]; then printf 'OK   %s\n' "$3"
  else printf 'FAIL %s  (%s 中仍有 %s 处 %s)\n' "$3" "$1" "${n:-0}" "$2"; FAIL=1; fi
}

echo "########## P0 环境 ##########"
echo "  APP=$APP"
echo "  PY=$PY"
"$PY" -V 2>&1 | sed 's/^/  /'

echo
echo "########## P1 新口径符号必须存在 ##########"
must_have app/services/picker/mode.py "T_PICK_BLOCK1_FROM"     "mode: 第一段起点常量"
must_have app/services/picker/mode.py "T_PICK_BLOCK1_TO"       "mode: 第一段终点常量"
must_have app/services/picker/mode.py "T_PICK_BLOCK2_FROM"     "mode: 第二段起点常量"
must_have app/services/picker/mode.py "T_PICK_BLOCK2_TO"       "mode: 第二段终点常量"
must_have app/services/picker/mode.py "def bj_secs"            "mode: 秒级时间函数"
must_have app/services/picker/mode.py "def pick_resume_at"     "mode: 放行时刻函数"
must_have app/services/picker/mode.py "9:15 后开放"             "mode: 新拦截文案"
must_have app/api/stocks.py "PICK_WINDOW_SWITCH"               "stocks: 闸门开关键"
must_have app/api/stocks.py "_pick_blocked_reason"             "stocks: 拦截判定接线"
must_have app/api/stocks.py "_pick_blocked_until"              "stocks: 放行时刻接线"
must_have app/api/stocks.py "pickGateEnabled"                  "stocks: ping 透出开关"
must_have app/api/stocks.py "选股闸门拦截"                        "stocks: 拦截日志"
must_have app/api/stocks.py "find_today_system_batch"          "stocks: ④ 当日系统名单优先(保留项)"
must_have app/services/auction_snapshot.py "def has_today_snapshot" "snapshot: 快照维"
must_have app/services/auto_apply.py "_AUTO_APPLY_DONE_KEY"    "auto_apply: done 键"
must_have app/services/auto_apply.py "def should_trigger"      "auto_apply: 触发判定"

echo
echo "########## P2 旧口径符号必须消失 ##########"
must_gone app/services/picker/mode.py "T_PICK_BLOCK_FROM"      "mode: 旧整段常量已移除"
must_gone app/services/picker/mode.py "9:26 后开放"             "mode: 旧文案已移除"

echo
echo "########## P3 行为断言(新口径边界 —— 本次最有价值的一步) ##########"
if "$PY" - <<PYEOF
import sys
sys.path.insert(0, "$BE")
from datetime import datetime, timedelta, timezone
from app.services.picker import mode as pm
BJ = timezone(timedelta(hours=8))
def ts(h, m, s=0):
    return datetime(2026, 9, 16, h, m, s, tzinfo=BJ).timestamp()
CASES = [((8, 59, 59), True),    # 盘前
         ((9, 0, 0), False),     # 第一段起
         ((9, 5, 0), False),
         ((9, 14, 59), False),   # 第一段末
         ((9, 15, 0), True),     # ★ 竞价窗口放行
         ((9, 19, 30), True),
         ((9, 24, 59), True),    # ★ 竞价窗口末
         ((9, 25, 0), False),    # 第二段起
         ((9, 25, 23), False),   # 实测落库点
         ((9, 25, 35), False),   # 第二段末
         ((9, 25, 36), True),    # ★ 放行
         ((9, 26, 0), True),
         ((15, 30, 0), True)]
bad = []
for (h, m, s), exp in CASES:
    got = pm.is_pick_open(ts(h, m, s))
    if got is not exp:
        bad.append("%02d:%02d:%02d got=%s want=%s" % (h, m, s, got, exp))
if bad:
    print("FAIL is_pick_open 边界: " + " | ".join(bad))
    raise SystemExit(1)
print("OK   is_pick_open %d 点边界全对(含 9:15-9:24:59 竞价窗口放行)" % len(CASES))
if pm.pick_resume_at(ts(9, 5)) != "09:15":
    print("FAIL pick_resume_at 第一段应为 09:15"); raise SystemExit(1)
if pm.pick_resume_at(ts(9, 25, 10)) != "09:25:36":
    print("FAIL pick_resume_at 第二段应为 09:25:36"); raise SystemExit(1)
if pm.pick_resume_at(ts(9, 19)) != "":
    print("FAIL pick_resume_at 竞价窗口内应为空"); raise SystemExit(1)
print("OK   pick_resume_at 两段放行点正确")
PYEOF
then :; else FAIL=1; fi

echo
echo "########## P4 模块可 import + 跨模块调用面(AST) + 路由数 ##########"
if "$PY" - <<PYEOF
import ast
import importlib
import io
import sys
sys.path.insert(0, "$BE")

from app.services.picker import mode, pipeline                       # noqa: F401
from app.services import auto_apply, auction_snapshot, settings       # noqa: F401
from app.api import stocks                                            # noqa: F401
from app.main import app

assert hasattr(mode, "is_pick_open") and hasattr(mode, "bj_secs")
assert hasattr(stocks, "_pick_blocked_reason") and hasattr(stocks, "_pick_blocked_until")
assert hasattr(auto_apply, "should_trigger") and hasattr(auto_apply, "mark_done")
assert hasattr(auction_snapshot, "has_today_snapshot")

# 🔴 跨模块调用面检查(2026-09-17 血泪: 只做符号 grep 会漏掉"调用方已更新、
#    被调方还没同步"的情形 —— stocks.py 里 grep 到 find_today_system_batch 就以为
#    没问题, 但 history.py 若还是旧版, 运行时会 AttributeError(而且 import 阶段
#    完全查不出来, 预检全绿、上线才崩)。这里用 AST 把调用面抠出来逐个 hasattr 验证。
MODS = {
    "history": "app.services.history",
    "auction_snapshot": "app.services.auction_snapshot",
    "settings": "app.services.settings",
    "auto_apply": "app.services.auto_apply",
    "bid_strength": "app.services.bid_strength",
}
SCAN = ["app/api/stocks.py", "app/services/auto_apply.py",
        "app/services/auction_snapshot.py", "app/services/picker/pipeline.py"]
loaded = {alias: importlib.import_module(path) for alias, path in MODS.items()}
calls = set()
for rel in SCAN:
    src = io.open("$BE/" + rel, encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \\
                and node.value.id in MODS:
            calls.add((node.value.id, node.attr))
missing = sorted("%s.%s" % (a, at) for a, at in calls
                 if not hasattr(loaded[a], at))
if missing:
    print("FAIL 跨模块调用面缺失 %d 个: %s" % (len(missing), " | ".join(missing)))
    raise SystemExit(1)
print("OK   跨模块调用面 %d 处全部存在(4 文件 x %d 模块)" % (len(calls), len(MODS)))

paths = app.openapi().get("paths") or {}
print("OK   import 全通; 路由数 %d" % len(paths))
PYEOF
then :; else FAIL=1; fi

echo
echo "########## P5 运行时状态(只读) ##########"
"$PY" - <<PYEOF
import sys, datetime
sys.path.insert(0, "$BE")
from app.services import settings as st
from app.services import auto_apply
from app.db import database
print("  pick_window_guard =", repr(st.get("pick_window_guard")))
print("  use_bid_strength  =", repr(st.get("use_bid_strength")))
print("  precompute_write  =", repr(st.get("precompute_write")))
d = datetime.datetime.now().strftime("%Y-%m-%d")
print("  auto_apply.already_done(%s) = %s" % (d, auto_apply.already_done(d)))
c = database.get_conn()
try:
    rows = c.execute("SELECT batch_time, action, stock_count, user_id, auto_applied "
                     "FROM batches WHERE batch_date=? ORDER BY batch_time DESC LIMIT 8",
                     (d,)).fetchall()
    print("  今日批次(%s) 最近 %d 条:" % (d, len(rows)))
    for r in rows:
        print("    ", r)
finally:
    c.close()
PYEOF

echo
if [ "$FAIL" = "1" ]; then echo "########## P FAILED ##########"; exit 1; fi
echo "########## P DONE — 预检全部通过 ##########"
exit 0
