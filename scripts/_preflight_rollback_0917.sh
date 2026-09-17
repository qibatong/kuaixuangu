#!/bin/bash
# 只读预检: v4.11.26 回退闸门 落盘后 / 重启前 执行。
# 断言: 闸门符号已从线上代码消失 + 两个保留修复仍在 + 模块可 import。
# 用法: bash /tmp/_kx_preflight.sh
set -u
BE=/opt/kuaixuan/backend
PY=/opt/kuaixuan-venv/bin/python
[ -x "$PY" ] || PY=/opt/bid-venv/bin/python

FAIL=0
must_absent() { # must_absent <文件> <符号>
  if grep -q "$2" "$BE/$1"; then printf 'FAIL %-38s 仍含 %s\n' "$1" "$2"; FAIL=1
  else printf 'OK   %-38s 已无 %s\n' "$1" "$2"; fi
}
must_present() { # must_present <文件> <符号>
  if grep -q "$2" "$BE/$1"; then printf 'OK   %-38s 仍含 %s\n' "$1" "$2"
  else printf 'FAIL %-38s 缺失 %s\n' "$1" "$2"; FAIL=1; fi
}

echo "--- 1. 闸门符号必须消失 ---"
must_absent "app/api/stocks.py"                    "PICK_WINDOW_SWITCH"
must_absent "app/api/stocks.py"                    "_pick_window_guard_on"
must_absent "app/api/stocks.py"                    "_pick_blocked_reason"
must_absent "app/api/stocks.py"                    "pickGateEnabled"
must_absent "app/api/stocks.py"                    "选股闸门拦截"
must_absent "app/services/picker/mode.py"          "is_pick_open"
must_absent "app/services/picker/mode.py"          "PICK_BLOCK_MSG"
must_absent "app/services/auction_snapshot.py"     "has_today_snapshot"

echo
echo "--- 2. 两个保留修复必须还在 ---"
must_present "app/services/history.py"             "def find_today_system_batch"
must_present "app/api/stocks.py"                   "find_today_system_batch"
must_present "app/services/picker/pipeline.py"     "use_bid_strength"

echo
echo "--- 3. settings 导入已清理(无未用导入) ---"
# 注意: 生产 stocks.py 是 CRLF, 行尾有 \r → 行尾锚点必须容忍, 否则假告警
if grep -q "^from ..services import (auction_snapshot, fetcher, history, kpl, notify, scorer,\r*$" "$BE/app/api/stocks.py" \
   && grep -q "^                        stats)\r*$" "$BE/app/api/stocks.py"; then
  echo "OK   stocks.py import 行已回到无 settings 形态"
else
  echo "WARN stocks.py import 行形态与预期不符(非致命, 人工看一眼)"; grep -n "from ..services import" -A1 "$BE/app/api/stocks.py"
fi

echo
echo "--- 4. 模块可 import + 路由表正常(只读) ---"
cd "$BE" || exit 1
"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from app.main import app
paths = sorted(app.openapi()["paths"].keys())
print("  路由数 =", len(paths))
assert "/api/stocks" in paths, "缺 /api/stocks 路由!"
import app.api.stocks as s
assert not hasattr(s, "_pick_window_guard_on"), "闸门函数仍在!"
assert not hasattr(s, "_pick_blocked_reason"), "闸门函数仍在!"
from app.services.picker import mode
print("  mode.is_pick_open 存在? ->", hasattr(mode, "is_pick_open"), "(应为 False)")
assert not hasattr(mode, "is_pick_open"), "mode 闸门仍在!"
from app.services import auction_snapshot as sn
print("  has_today_snapshot 存在? ->", hasattr(sn, "has_today_snapshot"), "(应为 False)")
assert not hasattr(sn, "has_today_snapshot"), "快照闸门仍在!"
from app.services import history
assert hasattr(history, "find_today_system_batch"), "④ 修复丢失!"
print("  find_today_system_batch() =", history.find_today_system_batch())
print("  PREFLIGHT-IMPORT OK")
PYEOF
[ $? -ne 0 ] && FAIL=1

echo
echo "--- 5. 今日批次现状(只读, 供对照) ---"
"$PY" - <<'PYEOF'
import sqlite3, time
g = time.gmtime(time.time() + 8 * 3600)
d = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
c = sqlite3.connect("file:/opt/kuaixuan/kuaixuan.db?mode=ro", uri=True)
tot = c.execute("SELECT COUNT(*) FROM batches WHERE batch_date=?", (d,)).fetchone()[0]
auto = c.execute("SELECT COUNT(*) FROM batches WHERE batch_date=? AND auto_applied=1", (d,)).fetchone()[0]
print("  %s total=%d auto=%d" % (d, tot, auto))
PYEOF

echo
if [ "$FAIL" = "1" ]; then echo ">>> PREFLIGHT FAILED"; exit 2; fi
echo ">>> PREFLIGHT PASSED"
