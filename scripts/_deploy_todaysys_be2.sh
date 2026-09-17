#!/bin/bash
# 生产后端部署 Step2(④ 当日名单优先于跨日回退): 重启 + 健康检查 + 运行态行为复核
#   注意: 不签发任何 token(单设备登录策略会把在用用户顶下线)
set -u

echo "########## 0. 重启前 ##########"
for u in kuaixuan kx-worker; do
  printf "  %-10s %-8s PID=%s\n" "$u" "$(systemctl is-active $u)" "$(systemctl show $u -p MainPID --value)"
done

echo
echo "########## 1. 重启 ##########"
systemctl restart kuaixuan kx-worker
sleep 7

echo "########## 2. 重启后 ##########"
for u in kuaixuan kx-worker; do
  printf "  %-10s %-8s PID=%s\n" "$u" "$(systemctl is-active $u)" "$(systemctl show $u -p MainPID --value)"
done

LOG=/opt/kuaixuan/logs/app.log
echo
echo "########## 3. 启动日志(尾 12) ##########"
tail -12 "$LOG"

echo
echo "########## 4. 健康计数(最近 400 行) ##########"
echo -n "  Traceback: "; tail -400 "$LOG" | grep -c "Traceback"
echo -n "  CRITICAL:  "; tail -400 "$LOG" | grep -c "CRITICAL"
printf "  首页 HTTP: %s\n" "$(curl -sL -k -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1/)"

echo
echo "########## 5. 运行态复核(只读) ##########"
cd /opt/kuaixuan/backend && PYTHONPATH=/opt/kuaixuan/backend /opt/kuaixuan-venv/bin/python -c "
from app.services import history as H
print('  find_today_system_batch() =', H.find_today_system_batch())
from app.services import settings as st
print('  pick_window_guard         =', repr(st.get('pick_window_guard')))
print('  use_bid_strength          =', repr(st.get('use_bid_strength')))
"

echo
echo "########## 6. 生效证据: 新日志行(重启后应开始出现) ##########"
echo -n "  「回退当日系统统一名单」计数: "; grep -ac "回退当日系统统一名单" "$LOG"
grep -a "回退当日系统统一名单" "$LOG" | tail -5
echo -n "  「回退最近交易日直读」计数(重启前累计, 作为对照): "
grep -ac "回退最近交易日直读" "$LOG"
echo -n "  最近 3 条「直读批次」: "; echo
grep -a "选股refresh直读批次" "$LOG" | tail -3

echo
echo "########## STEP2 DONE ##########"
