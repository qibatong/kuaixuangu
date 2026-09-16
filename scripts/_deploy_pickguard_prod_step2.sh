#!/bin/bash
# 生产后端部署 Step2: 重启 + 运行态验证(仅探活, 不签发任何 token)
# 前置: /tmp/_d1.sh 已通过(落盘 + 哈希校验 + 预检)。本脚本不改任何文件。
BK=/opt/kuaixuan/backend
LOG=/opt/kuaixuan/logs/app.log

echo "########## 1. 重启前 MainPID ##########"
for u in kuaixuan kx-worker; do printf '%-14s MainPID=%s\n' "$u" "$(systemctl show $u -p MainPID --value)"; done

echo
echo "########## 2. 重启 ##########"
systemctl restart kuaixuan kx-worker; echo "restart rc=$?"
sleep 5

echo
echo "########## 3. 状态 ##########"
for u in kuaixuan kx-worker; do
  printf '%-14s is-active=%-8s MainPID=%s\n' "$u" "$(systemctl is-active $u)" "$(systemctl show $u -p MainPID --value)"
done
echo "--- 进程 ---"
ps -eo pid,etimes,cmd | grep -E 'uvicorn|app\.main' | grep -v grep

echo
echo "########## 4. 日志: 有无启动异常 ##########"
echo "--- journalctl 最近 20 行 ---"
journalctl -u kuaixuan -n 20 --no-pager 2>&1 | tail -20
echo "--- app.log 尾部 25 行 ---"
tail -25 "$LOG" 2>&1
echo -n "--- app.log 近 250 行内 Traceback 计数(期望 0) -> "
tail -250 "$LOG" 2>/dev/null | grep -c 'Traceback'
echo -n "--- app.log 近 250 行内 CRITICAL 计数(期望 0) -> "
tail -250 "$LOG" 2>/dev/null | grep -c 'CRITICAL'

echo
echo "########## 5. HTTP 探活(不鉴权) ##########"
printf 'uvicorn 直连 /api/health 期望 401 -> %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8010/api/health)"
printf 'uvicorn 直连 根路径   期望 404/200 -> %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8010/)"
printf 'nginx 首页            期望 200 -> %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1/)"
printf 'nginx 旧 entry chunk   期望 200(前端本步还没换) -> %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1/assets/index-CExq6Nrm.js)"
printf 'nginx 新 entry chunk   期望 404(前端本步还没换) -> %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1/assets/index-0LujzWyt.js)"

echo
echo "########## 6. 文件完整性(重启后) ##########"
md5sum "$BK/app/services/picker/mode.py" "$BK/app/services/auction_snapshot.py" "$BK/app/api/stocks.py"
echo "期望: 354fdcd5440aa178e69c58689668b850 / 7462ae63f7f1d17cebe039f169685ee8 / fe3ef4a6628def03f5d1f6e61fcda729"

echo
echo "########## STEP2 DONE ##########"
