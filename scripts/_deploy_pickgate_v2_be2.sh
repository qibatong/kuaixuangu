#!/bin/bash
# 生产后端部署 Step2(闸门 v2): 重启 + 健康检查 + 开关运行态复核
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
echo "########## 3. 启动日志(尾 15) ##########"
tail -15 "$LOG"

echo
echo "########## 4. 健康计数(最近 400 行) ##########"
echo -n "  Traceback: "; tail -400 "$LOG" | grep -c "Traceback"
echo -n "  ERROR:     "; tail -400 "$LOG" | grep -c "ERROR"
echo -n "  CRITICAL:  "; tail -400 "$LOG" | grep -c "CRITICAL"
printf "  首页 HTTP: %s\n" "$(curl -sL -k -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1/)"

echo
echo "########## 5. 运行态开关复核(只读) ##########"
cd /opt/kuaixuan/backend && PYTHONPATH=/opt/kuaixuan/backend /opt/kuaixuan-venv/bin/python -c "
from app.api import stocks as S
from app.services import settings as st
print('  pick_window_guard        =', repr(st.get('pick_window_guard')))
print('  _pick_window_guard_on()  =', S._pick_window_guard_on())
print('  use_bid_strength         =', repr(st.get('use_bid_strength')))
from app.services import bid_strength as B
print('  bid_strength.enabled()   =', B.enabled())
from app.services import auction_snapshot as A
print('  has_today_snapshot(today)=', A.has_today_snapshot())
"

echo
echo "########## STEP2 DONE ##########"
