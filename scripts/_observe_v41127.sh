#!/bin/bash
# v4.11.27 早盘观测(**只读**) —— 测试机用, 生产同样可跑(只加 KX_APP)
#
# 用法: bash _observe_v41127.sh [YYYY-MM-DD]      # 默认今天
#
# ============ 判定标准(2026-09-17 口径重做, 权威实现 = picker/mode.is_pick_open) ============
#   09:00:00-09:14:59   拦截   盘前 PREOPEN: 名单来自**上交易日**定格(用户会误认当日)
#   09:15:00-09:24:59  **放行** 竞价主窗口 —— 🔴 v4.11.22 曾把这段封死, 正是 9/17 事故根因
#   09:25:00-09:25:35   拦截   当日 9_25 尚未落库(采集下限 20s, 实测落库 09:25:23~32)
#                             → 期间 load_snapshot_full 会**静默回退昨日**
#   ≥09:25:36          放行   时间维放行; 还须过**快照维**(当日 9_25 已落库)
#   非交易日 / <09:00   放行   回放最近交易日定格是既有功能(用户知情)
#   09:26-09:30         auto_apply 最多每 60s 尝试一次; **成功才置 done**(当日不再重试)
#                       —— 旧实现在成功前就烧掉一次性锁, 无票时当日永不重试(已修)
#
# 退出码: 0 = 口径自证通过; 1 = 口径不符(必须立刻查)
set -u
APP="${KX_APP:-/opt/kuaixuan}"
BE="$APP/backend"
D="${1:-$(date +%Y-%m-%d)}"
LOG="$APP/logs/app.log"
NGX="${KX_NGX_LOG:-/var/log/nginx/access.log}"
if [ -x "${KX_VENV:-/opt/kuaixuan-venv/bin/python}" ]; then PY="${KX_VENV:-/opt/kuaixuan-venv/bin/python}"
elif [ -x /opt/bid-venv/bin/python ]; then PY=/opt/bid-venv/bin/python
else PY=$(command -v python3); fi

echo "=============== v4.11.27 早盘观测   $D   ==============="
date '+现在: %Y-%m-%d %H:%M:%S %A'

echo
echo "########## 0. 口径自证(不依赖用户行为 —— 最可靠的一节) ##########"
"$PY" - <<PYEOF
import sys
sys.path.insert(0, "$BE")
import datetime as _dt
from datetime import timezone, timedelta
from app.services.picker import mode as pm
BJ = timezone(timedelta(hours=8))
try:
    base = _dt.datetime.strptime("$D", "%Y-%m-%d")
except Exception:
    base = _dt.datetime.now()
bad = []
print("  --- 逐分钟(09:00-09:32) ---")
for mins in range(9 * 60, 9 * 60 + 33):
    h, m = divmod(mins, 60)
    t = base.replace(hour=h, minute=m, second=0, tzinfo=BJ).timestamp()
    ok = pm.is_pick_open(t)
    if mins < 9 * 60 + 15:
        want = False
    elif mins < 9 * 60 + 25:
        want = True
    elif mins == 9 * 60 + 25:
        want = None            # 该分钟内秒级分支: 00-35 拦, 36 起放行 → 整分点应为拦
    else:
        want = True
    tag = ""
    if want is not None and ok is not want:
        tag = "   <<< 不符!"
        bad.append("%02d:%02d" % (h, m))
    print("    %02d:%02d   %s%s" % (h, m, "放行" if ok else "拦截", tag))
print("  --- 秒级关键点 ---")
def ts(h, m, s=0):
    return base.replace(hour=h, minute=m, second=s, tzinfo=BJ).timestamp()
for (h, m, s), exp in [((9, 0, 0), False), ((9, 14, 59), False), ((9, 15, 0), True),
                       ((9, 19, 30), True), ((9, 24, 59), True), ((9, 25, 0), False),
                       ((9, 25, 35), False), ((9, 25, 36), True)]:
    got = pm.is_pick_open(ts(h, m, s))
    okk = (got is exp)
    if not okk:
        bad.append("%02d:%02d:%02d" % (h, m, s))
    print("      %02d:%02d:%02d   期望=%-5s 实测=%-5s  %s"
          % (h, m, s, exp, got, "OK" if okk else "FAIL <<<"))
try:
    r1 = pm.pick_resume_at(ts(9, 5))
    r2 = pm.pick_resume_at(ts(9, 25, 10))
    print("    pick_resume_at: 09:05 -> %r (期望 '09:15'); 09:25:10 -> %r (期望 '09:25:36')"
          % (r1, r2))
    if r1 != "09:15" or r2 != "09:25:36":
        bad.append("pick_resume_at")
except Exception as e:
    print("    pick_resume_at 异常:", e)
    bad.append("pick_resume_at-exc")
print("  ======== 口径自证结论: " + ("全部符合预期 ✓" if not bad else
      "有 %d 处不符 <<< %s" % (len(bad), bad)) + " ========")
raise SystemExit(0 if not bad else 1)
PYEOF
CAL=$?

echo
echo "########## 1. 开关与运行态 ##########"
"$PY" - <<PYEOF
import sys, datetime
sys.path.insert(0, "$BE")
from app.services import settings as st, auto_apply
from app.db import database
d = "$D"
print("  pick_window_guard   = %r   (须为 1 才算真在测闸门; 0 = 前后端同时放行)" % (st.get("pick_window_guard"),))
print("  use_bid_strength    = %r" % (st.get("use_bid_strength"),))
print("  precompute_write    = %r" % (st.get("precompute_write"),))
print("  /api/stocks?action=ping 的 pickGateEnabled 取值 =", "true" if st.get("pick_window_guard") else "false")
print("  auto_apply.already_done(%s) = %s   (True = 当日已成功, 不再重试)" % (d, auto_apply.already_done(d)))
c = database.get_conn()
try:
    r = c.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM snapshot_bid WHERE date=? AND time_point='9_25'", (d,)).fetchone()
    print("  当日 9_25 定格: 行数=%s  ts范围 %s ~ %s" % (r[2], r[0], r[1]))
    r2 = c.execute("SELECT batch_time, action, stock_count, user_id, auto_applied FROM batches "
                   "WHERE batch_date=? ORDER BY batch_time", (d,)).fetchall()
    print("  当日批次 %d 条:" % len(r2))
    for x in r2[:14]:
        print("      ", x)
    if len(r2) > 14:
        print("       ... 省略 %d 条" % (len(r2) - 14))
    sysid = c.execute("SELECT id, batch_time, stock_count, auto_applied FROM batches "
                      "WHERE batch_date=? AND user_id=0 ORDER BY batch_time DESC LIMIT 3", (d,)).fetchall()
    print("  当日**系统批次**(user_id=0) 最近 3 条: %s" % (sysid,))
finally:
    c.close()
PYEOF

echo
echo "########## 2. 闸门拦截日志(app.log) ##########"
if [ -f "$LOG" ]; then
  printf '  全天累计: %s 条\n' "$(grep -c "选股闸门拦截" "$LOG")"
  echo "  --- 按分钟分桶 ---"
  grep "选股闸门拦截" "$LOG" | awk '{print substr($2,1,5)}' | sort | uniq -c | sed 's/^/    /'
  echo "  --- 最近 6 条 ---"
  grep "选股闸门拦截" "$LOG" | tail -6 | sed 's/^/    /'
else
  echo "  (无日志 $LOG)"
fi

echo
echo "########## 3. /api/stocks 请求量(nginx) ##########"
if [ -f "$NGX" ]; then
  printf '  全天 /api/stocks 请求数: %s\n' "$(grep -c '/api/stocks' "$NGX")"
  echo "  --- 按分钟分桶 ---"
  grep "$D" "$NGX" | grep "/api/stocks" | awk '{print substr($4,2,17)}' | sort | uniq -c | sed 's/^/    /'
  printf '  🔴 09:15-09:24 竞价窗口请求数(本次核心指标, 必须非零): %s\n' \
         "$(grep "$D" "$NGX" | grep "/api/stocks" | grep -cE ':09:(1[5-9]|2[0-4])')"
  printf '  09:00-09:14 请求数(拦截段, 有则应有拦截日志配对): %s\n' \
         "$(grep "$D" "$NGX" | grep "/api/stocks" | grep -cE ':09:(0[0-9]|1[0-4])')"
else
  echo "  (无日志 $NGX)"
fi

echo
echo "########## 4. auto_apply / 名单回退 相关日志 ##########"
if [ -f "$LOG" ]; then
  echo "  --- auto_apply 触发/结果 ---"
  grep -iE "auto_apply|自动锁仓|should_trigger" "$LOG" | tail -10 | sed 's/^/    /'
  echo "  --- 名单回退(应只见「当日系统统一名单」, 罕见「跨日回退」) ---"
  grep -E "回退当日系统统一名单|回退最近交易日|跨日回退" "$LOG" | tail -10 | sed 's/^/    /'
fi

echo
echo "########## 5. Traceback ##########"
if [ -f "$LOG" ]; then
  printf '  计数: %s\n' "$(grep -c Traceback "$LOG")"
  grep -A3 "Traceback" "$LOG" | tail -14 | sed 's/^/    /'
fi

echo
echo "########## 6. 前端产物自证 ##########"
printf '  线上入口: %s\n' "$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$APP/dist/index.html" 2>/dev/null | head -1)"
printf '  含新文案(9:15 后开放): %s 个文件\n' "$(grep -rlF '9:15 后开放' "$APP/dist/assets" 2>/dev/null | wc -l)"
printf '  含旧文案(9:26 后开放): %s 个文件(应为 0)\n' "$(grep -rlF '9:26 后开放' "$APP/dist/assets" 2>/dev/null | wc -l)"

echo
echo "=============== 观测结束 (口径自证 exit=$CAL) ==============="
exit "$CAL"
