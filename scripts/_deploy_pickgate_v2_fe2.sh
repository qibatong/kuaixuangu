#!/bin/bash
# 生产前端 Step F2(闸门 v2): 同分区原子 rename + 全量验证
set -u
TS=$(date +%Y%m%d-%H%M%S)
OLD=/opt/kuaixuan/dist
BAK=/opt/kuaixuan/dist_bak_prod_$TS
NEW=$(cat /tmp/_pg_newdir)

echo "########## 1. 换盘前快照 ##########"
ls -ld "$OLD"; echo "旧 inode: $(stat -c '%i' "$OLD")"
echo "暂存: $NEW (文件 $(find "$NEW" -type f | wc -l))"

echo
echo "########## 2. 同分区原子 rename ##########"
mv "$OLD" "$BAK" || { echo ">>> ABORT: 备份 rename 失败, 线上未变"; exit 1; }
mv "$NEW" "$OLD" || { echo ">>> 危险: 备份已建但新目录 rename 失败, 立刻回滚"; mv "$BAK" "$OLD"; exit 2; }
echo "旧 -> $BAK"
echo "新 -> $OLD"
echo "新 inode: $(stat -c '%i' "$OLD")"
ls -ld "$OLD"

echo
echo "########## 3. nginx 配置检查 ##########"
nginx -t 2>&1

echo
echo "########## 4. HTTP 验证(跟 301, 不缓存) ##########"
H='Cache-Control: no-cache'
code() { curl -sL -k -o /dev/null -w '%{http_code}' --max-time 10 -H "$H" "http://127.0.0.1$1"; }
printf '首页 index.html              期望 200 -> %s\n' "$(code /)"
printf '新 entry index-IiW_cnAG.js   期望 200 -> %s\n' "$(code /assets/index-IiW_cnAG.js)"
printf '新 StockView-CZwVt7Nf.js     期望 200 -> %s\n' "$(code /assets/StockView-CZwVt7Nf.js)"
printf '新 StockView-D-A6G8Km.css    期望 200 -> %s\n' "$(code /assets/StockView-D-A6G8Km.css)"
printf '新 stocks-CRjwpram.js        期望 200 -> %s\n' "$(code /assets/stocks-CRjwpram.js)"
printf '旧 entry index-0LujzWyt.js   期望 404 -> %s\n' "$(code /assets/index-0LujzWyt.js)"
printf '旧 StockView-iJFUgQFB.js     期望 404 -> %s\n' "$(code /assets/StockView-iJFUgQFB.js)"
printf '旧 stocks-DSXdwACC.js        期望 404 -> %s\n' "$(code /assets/stocks-DSXdwACC.js)"

echo
echo "########## 5. 服务端实际下发内容核验 ##########"
echo "--- 下发的 index.html 引用的 entry ---"
curl -sL -k --max-time 10 -H "$H" http://127.0.0.1/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.js' | head -3
echo "--- index.html 响应头 Cache-Control(期望 no-store) ---"
curl -sIL -k --max-time 10 http://127.0.0.1/ | grep -i 'cache-control' | head -2
echo "--- 下发 index.html 与磁盘是否同字节(md5) ---"
printf 'served: %s\n' "$(curl -sL -k --max-time 10 -H "$H" http://127.0.0.1/ | md5sum | cut -d' ' -f1)"
printf 'disk  : %s\n' "$(md5sum "$OLD/index.html" | cut -d' ' -f1)"
echo "--- 下发的 stocks chunk 是否含开关驱动标记 ---"
printf 'pickGateEnabled 命中: %s\n' "$(curl -sL -k --max-time 10 -H "$H" http://127.0.0.1/assets/stocks-CRjwpram.js | grep -c 'pickGateEnabled')"

echo
echo "########## 6. 后端开关探测连通性(模拟前端 ping) ##########"
printf 'GET /api/stocks?action=ping -> %s\n' "$(curl -sL -k -o /dev/null -w '%{http_code}' --max-time 10 -H "$H" 'http://127.0.0.1/api/stocks?action=ping&strategy=auction')"
echo "(401=需登录属正常, 只要不是 5xx)"
echo "--- 后端日志中 ping 的落痕 ---"
grep -a 'action=ping' /opt/kuaixuan/logs/app.log | tail -3 || echo "(无)"

echo
echo "########## 7. 服务状态 ##########"
for u in kuaixuan kx-worker; do printf '%-12s %s MainPID=%s\n' "$u" "$(systemctl is-active $u)" "$(systemctl show $u -p MainPID --value)"; done

echo
echo "########## 8. 日志新错误检查 ##########"
echo -n "近 120 行 Traceback 计数(期望 0) -> "; tail -120 /opt/kuaixuan/logs/app.log 2>/dev/null | grep -c 'Traceback'

echo
echo "备份: $BAK"
echo "回滚(同分区): mv $OLD /opt/kuaixuan/dist_failed_$(date +%s) && mv $BAK $OLD"
echo "########## F2 DONE ##########"
