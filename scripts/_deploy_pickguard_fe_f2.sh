#!/bin/bash
# 生产前端 Step F2: 同分区原子 rename + 全量验证
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
printf '首页 index.html                期望 200 -> %s\n' "$(code /)"
printf '新 entry   index-0LujzWyt.js   期望 200 -> %s\n' "$(code /assets/index-0LujzWyt.js)"
printf '新 StockView-iJFUgQFB.js       期望 200 -> %s\n' "$(code /assets/StockView-iJFUgQFB.js)"
printf '新 StockView-CZmNoLaW.css      期望 200 -> %s\n' "$(code /assets/StockView-CZmNoLaW.css)"
printf '新 stocks-DSXdwACC.js          期望 200 -> %s\n' "$(code /assets/stocks-DSXdwACC.js)"
printf '旧 entry   index-CExq6Nrm.js   期望 404 -> %s\n' "$(code /assets/index-CExq6Nrm.js)"
printf '旧 StockView-srOhE6pV.js       期望 404 -> %s\n' "$(code /assets/StockView-srOhE6pV.js)"
printf '旧 stocks-DCIapwgN.js          期望 404 -> %s\n' "$(code /assets/stocks-DCIapwgN.js)"

echo
echo "########## 5. 服务端实际下发内容核验 ##########"
echo "--- 下发的 index.html 是否引用新 entry ---"
curl -sL -k --max-time 10 -H "$H" http://127.0.0.1/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.js' | head -3
echo "--- index.html 响应头 Cache-Control(期望 no-store) ---"
curl -sIL -k --max-time 10 http://127.0.0.1/ | grep -i 'cache-control' | head -2
echo "--- 下发 index.html 与暂存文件是否同字节(md5) ---"
printf 'served: %s\n' "$(curl -sL -k --max-time 10 -H "$H" http://127.0.0.1/ | md5sum | cut -d' ' -f1)"
printf 'disk  : %s\n' "$(md5sum "$OLD/index.html" | cut -d' ' -f1)"

echo
echo "########## 6. 闸门文案(UTF-8 直读, 避开 locale) ##########"
/opt/kuaixuan-venv/bin/python - <<'PY'
import io, glob, os
hits = []
for p in glob.glob('/opt/kuaixuan/dist/assets/*.js'):
    try:
        s = io.open(p, 'r', encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    for kw in ('9:26 后开放', 'pickBlocked'):
        if kw in s:
            hits.append((os.path.basename(p), kw))
for n, k in sorted(set(hits)):
    print("   命中 %-30s %s" % (n, k))
print("   命中文件数 =", len(set(n for n, _ in hits)))
PY

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
