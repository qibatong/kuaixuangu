#!/bin/bash
# 生产只读审计 (第二批): 索引 / 文件 mtime / 前端产物清单 / nginx 备份点
echo "########## A. snapshot_bid 索引(闸门查询性能关键) ##########"
DB=/opt/kuaixuan/kuaixuan.db
sqlite3 "$DB" ".schema snapshot_bid" 2>&1 | head -20
echo "--- index_list ---"
sqlite3 "$DB" "PRAGMA index_list('snapshot_bid');" 2>&1
echo "--- EXPLAIN: 闸门实际会跑的那条 ---"
sqlite3 "$DB" "EXPLAIN QUERY PLAN SELECT 1 FROM snapshot_bid WHERE date='2026-09-16' AND time_point='9_25' LIMIT 1;" 2>&1
echo "--- 表行数 ---"
sqlite3 "$DB" "select count(*) from snapshot_bid;" 2>&1

echo
echo "########## B. 四个待替换文件 mtime / 大小 ##########"
cd /opt/kuaixuan/backend
ls -la app/services/picker/mode.py app/services/auction_snapshot.py app/api/stocks.py tests/conftest.py

echo
echo "########## C. 前端 assets 清单(仅名字, 供本地 diff) ##########"
ls -1 /opt/kuaixuan/dist/assets | sort
echo "########## C2. 非 assets 顶层文件 ##########"
find /opt/kuaixuan/dist -maxdepth 1 -type f | sort

echo
echo "########## D. 备份点与磁盘 ##########"
ls -1dt /opt/kuaixuan/dist_bak_prod_* 2>/dev/null | head -3
df -h /opt | tail -1

echo
echo "########## DONE ##########"
