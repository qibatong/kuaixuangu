#!/bin/bash
# 只读审计: 9/16-9/17 两日改动在 生产/测试 两机的「代码 + 开关」回退状态
# 用法: bash /tmp/_audit_rollback_0917.sh   (生产/测试同一脚本, venv 自动探测)
# 🔴 本脚本纯只读: 不写库、不重启、不改文件
set -u
APP=/opt/kuaixuan
if [ -x /opt/kuaixuan-venv/bin/python ]; then PY=/opt/kuaixuan-venv/bin/python
elif [ -x /opt/bid-venv/bin/python ]; then PY=/opt/bid-venv/bin/python
else PY=$(command -v python3 || command -v python); fi

echo "### HOST=$(hostname)  NOW=$(date '+%F %T %z')"
echo "### APP=$APP  PY=$PY"
echo "### dist_exists=$([ -d $APP/dist ] && echo yes || echo no)  db=$([ -f $APP/kuaixuan.db ] && echo yes || echo no)"

echo
echo "--- [1] 服务状态 ---"
for s in kuaixuan.service kx-worker.service; do
  printf '%-18s active=%-10s since=%s pid=%s\n' "$s" \
    "$(systemctl is-active $s 2>&1 | tr -d '\n')" \
    "$(systemctl show $s -p ActiveEnterTimestamp --value 2>/dev/null)" \
    "$(systemctl show $s -p MainPID --value 2>/dev/null)"
done

echo
echo "--- [2] 关键文件 md5 + mtime ---"
for f in backend/app/api/stocks.py \
         backend/app/services/history.py \
         backend/app/services/picker/mode.py \
         backend/app/services/picker/sources.py \
         backend/app/services/picker/score.py \
         backend/app/services/picker/lock.py \
         backend/app/services/auto_apply.py \
         backend/app/services/system_batch.py \
         backend/app/services/auction_snapshot.py ; do
  p="$APP/$f"
  if [ -f "$p" ]; then
    printf '%s  %s  %s\n' "$(md5sum "$p" | cut -d' ' -f1)" "$(date -r "$p" '+%m-%d_%H:%M' 2>/dev/null)" "$f"
  else
    printf '%-32s MISSING  %s\n' "---" "$f"
  fi
done

echo
echo "--- [3] 备份目录 (各取最新 6) ---"
for d in backend_bak_* dist_bak_* dist_old_* bak_*; do
  got=$(ls -d $APP/$d 2>/dev/null | tail -6)
  [ -n "$got" ] && echo "$got"
done
echo "备份目录总数: $(ls -d $APP/*bak_* $APP/*_old_* 2>/dev/null | wc -l)"

echo
echo "--- [4] 前端入口 hash ---"
if [ -f $APP/dist/index.html ]; then
  grep -o 'assets/index-[A-Za-z0-9_-]*\.js' $APP/dist/index.html | sort -u | head -3
  echo "index.html mtime: $(date -r $APP/dist/index.html '+%m-%d %H:%M')"
else
  echo "NO dist/index.html"
fi

echo
echo "--- [5] settings 关注开关 ---"
$PY - <<'PYEOF'
import sqlite3
db = "/opt/kuaixuan/kuaixuan.db"
WATCH = ["pick_window_guard", "use_bid_strength", "use_snapshot_pool", "score_floor",
         "use_tick_plus", "use_precompute", "pick_score_floor", "bid_strength_source"]
PAT = ("pick", "bid", "snapshot", "tick", "score", "auction", "precompute")
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.row_factory = sqlite3.Row
    tabs = sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    print("  has_settings_table=%s  total_tables=%d" % ("settings" in tabs, len(tabs)))
    if "settings" not in tabs:
        print("  !! 无 settings 表, 开关可能落在别处")
    else:
        cols = [r[1] for r in c.execute("PRAGMA table_info(settings)")]
        kcol = "key" if "key" in cols else cols[0]
        vcol = "value" if "value" in cols else cols[1]
        print("  settings_cols=%s" % cols)
        rows = c.execute("SELECT * FROM settings").fetchall()
        print("  settings_rows=%d" % len(rows))
        present = {str(r[kcol]): str(r[vcol]) for r in rows}
        print("  --- 关注键 ---")
        for k in WATCH:
            print("    %-22s -> %s" % (k, present.get(k, "<ABSENT>")))
        print("  --- 命中关键词的其它键 ---")
        other = [(k, v) for k, v in sorted(present.items())
                 if any(p in k.lower() for p in PAT) and k not in WATCH]
        for k, v in other:
            print("    %-22s = %s" % (k, v[:110]))
        if not other:
            print("    (none)")
except Exception as e:
    print("  settings read ERROR: %r" % (e,))

print("  --- auto_apply 当日幂等锁 ---")
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.row_factory = sqlite3.Row
    tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    if "kv_cache" not in tabs:
        print("    no kv_cache table")
    else:
        cols = [r[1] for r in c.execute("PRAGMA table_info(kv_cache)")]
        print("    kv_cache_cols=%s" % cols)
        rows = c.execute("SELECT * FROM kv_cache WHERE CAST(key AS TEXT) LIKE '%auto_apply%' "
                         "ORDER BY key DESC LIMIT 4").fetchall()
        for r in rows:
            d = dict(r)
            print("    %s -> val=%s expire_at=%s" % (d.get("key"), d.get("val"), d.get("expire_at")))
        if not rows:
            print("    (无 auto_apply 相关锁)")
except Exception as e:
    print("    kv_cache read ERROR: %r" % (e,))
PYEOF

echo
echo "--- [6] 今日批次 (auto_applied / 名单分裂) ---"
$PY - <<'PYEOF'
import sqlite3, time
db = "/opt/kuaixuan/kuaixuan.db"
g = time.gmtime(time.time() + 8 * 3600)
today = "%04d-%02d-%02d" % (g.tm_year, g.tm_mon, g.tm_mday)
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.row_factory = sqlite3.Row
    print("  today=%s" % today)
    for d in (today,):
        tot = c.execute("SELECT COUNT(*) FROM batches WHERE batch_date=?", (d,)).fetchone()[0]
        auto = c.execute("SELECT COUNT(*) FROM batches WHERE batch_date=? AND auto_applied=1",
                         (d,)).fetchone()[0]
        sc = c.execute("SELECT stock_count, COUNT(*) FROM batches WHERE batch_date=? AND auto_applied=1 "
                       "GROUP BY stock_count", (d,)).fetchall()
        print("  %s  total=%d  auto=%d  auto_stock_count分布=%s"
              % (d, tot, auto, [(r[0], r[1]) for r in sc]))
    print("  --- 近 5 个交易日 auto 覆盖 ---")
    for r in c.execute("SELECT batch_date, COUNT(*) t, COUNT(DISTINCT stock_count) dsc "
                       "FROM batches WHERE auto_applied=1 GROUP BY batch_date "
                       "ORDER BY batch_date DESC LIMIT 5"):
        print("    %s  auto=%d  distinct_stock_count=%d" % (r[0], r[1], r[2]))
except Exception as e:
    print("  batches read ERROR: %r" % (e,))
PYEOF

echo
echo "--- [7] 日志: 闸门拦截 / 跨日回退 / Traceback ---"
L=$APP/logs/app.log
if [ -f "$L" ]; then
  echo "app.log mtime=$(date -r $L '+%m-%d %H:%M')  size=$(stat -c%s $L)"
  echo "  闸门拦截            : $(grep -c '选股闸门拦截' $L)"
  echo "  回退最近交易日直读  : $(grep -c '回退最近交易日直读' $L)"
  echo "  回退当日系统统一名单: $(grep -c '回退当日系统统一名单' $L)"
  echo "  Traceback           : $(grep -c Traceback $L)"
  echo "  --- 最近 3 条选股refresh ---"
  grep '选股refresh' $L | tail -3 | sed 's/^/    /'
else
  echo "NO app.log at $L"
fi
