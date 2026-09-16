#!/bin/bash
# 生产只读审计: 选股闸门上线前 (2026-09-16)
# 只读, 不写任何文件、不改任何服务。
echo "########## 0. 时刻 ##########"
date '+%F %T %Z'

echo
echo "########## 1. 服务 ##########"
for u in kuaixuan kx-worker; do
  printf '%-14s is-active=%s\n' "$u" "$(systemctl is-active $u 2>&1)"
done
echo "--- uvicorn 进程 ---"
ps -eo pid,etimes,cmd | grep -E 'uvicorn|app.main' | grep -v grep

echo
echo "########## 2. systemd env (关键: 有无 BID_DB_PATH) ##########"
systemctl show kuaixuan -p Environment 2>&1
systemctl show kx-worker -p Environment 2>&1
echo "--- unit 文件 ---"
systemctl cat kuaixuan 2>&1 | sed -n '1,20p'

echo
echo "########## 3. 后端待替换文件 md5 (对比本地 HEAD) ##########"
cd /opt/kuaixuan/backend || exit 1
for f in app/services/picker/mode.py app/services/auction_snapshot.py app/api/stocks.py tests/conftest.py; do
  if [ -f "$f" ]; then printf '%s  %s\n' "$(md5sum "$f" | cut -d' ' -f1)" "$f"; else printf '%-34s %s\n' MISSING "$f"; fi
done
echo "--- 是否已存在闸门关键字(应为 0) ---"
for f in app/services/picker/mode.py app/api/stocks.py; do
  printf '%-34s is_pick_open=%s pick_window_guard=%s\n' "$f" \
    "$(grep -c 'is_pick_open' "$f" 2>/dev/null)" "$(grep -c 'pick_window_guard' "$f" 2>/dev/null)"
done

echo
echo "########## 4. 库: settings / snapshot_bid ##########"
DB=/opt/kuaixuan/kuaixuan.db
ls -la "$DB"
if command -v sqlite3 >/dev/null 2>&1; then
  echo "--- settings 全量 ---"
  sqlite3 "$DB" "select key, substr(value,1,40) from settings order by key;" 2>&1
  echo "--- pick_window_guard 是否存在 ---"
  sqlite3 "$DB" "select count(*) from settings where key='pick_window_guard';" 2>&1
  echo "--- 最近 5 个有 9_25 的日期 ---"
  sqlite3 "$DB" "select date, count(*) from snapshot_bid where time_point='9_25' group by date order by date desc limit 5;" 2>&1
else
  echo "sqlite3 CLI 缺失, 跳过"
fi

echo
echo "########## 5. 前端产物 ##########"
echo "root 配置:"
nginx -T 2>/dev/null | grep -n 'root ' | head -10
ls -la /opt/kuaixuan/dist/index.html 2>&1
echo "assets 数量: $(ls -1 /opt/kuaixuan/dist/assets 2>/dev/null | wc -l)"
echo "--- 新 chunk 是否已存在(应为 0) ---"
ls -1 /opt/kuaixuan/dist/assets 2>/dev/null | grep -cE 'StockView-iJFUgQFB|stocks-DSXdwACC'
echo "--- 现有 StockView/stocks chunk ---"
ls -1 /opt/kuaixuan/dist/assets 2>/dev/null | grep -E 'StockView-|^stocks-' | head

echo
echo "########## 6. 备份目录约定 ##########"
ls -ldt /opt/kuaixuan/backups 2>/dev/null || echo "无 /opt/kuaixuan/backups"
ls -1dt /opt/kuaixuan/dist_bak_prod_* /opt/kuaixuan/dist.old* /opt/kuaixuan/backend_bak* 2>/dev/null | head -5

echo
echo "########## 7. venv / pytest ##########"
/opt/kuaixuan-venv/bin/python -V 2>&1
/opt/kuaixuan-venv/bin/python -c "import pytest, fastapi; print('pytest', pytest.__version__, '| fastapi', fastapi.__version__)" 2>&1
echo "tests 目录用例文件数: $(ls -1 /opt/kuaixuan/backend/tests/test_*.py 2>/dev/null | wc -l)"

echo
echo "########## DONE ##########"
