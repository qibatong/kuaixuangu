#!/bin/bash
# 生产后端部署 Step1: 备份 -> 落盘 -> 哈希校验 -> 预检(不重启)
# 幂等: 可重复执行(每次新建时间戳备份)。失败即中止, 不重启服务。
set -u

BK=/opt/kuaixuan/backend
TS=$(date +%Y%m%d-%H%M%S)
BAK=/opt/kuaixuan/backend_bak_prod_$TS

echo "########## 1. 备份三个文件(保留相对路径) ##########"
mkdir -p "$BAK/app/services/picker" "$BAK/app/api"
for pair in \
  "app/services/picker/mode.py" \
  "app/services/auction_snapshot.py" \
  "app/api/stocks.py"; do
  if [ ! -f "$BK/$pair" ]; then echo "MISSING: $BK/$pair"; exit 1; fi
  cp -p "$BK/$pair" "$BAK/$pair" || exit 1
done
( cd "$BAK" && md5sum app/services/picker/mode.py app/services/auction_snapshot.py app/api/stocks.py > MANIFEST.md5 )
echo "备份目录: $BAK"
cat "$BAK/MANIFEST.md5"
echo "$BAK" > /tmp/_pg_bakdir

echo
echo "########## 2. 落盘(从 /tmp 暂存) ##########"
for pair in \
  "/tmp/pg_mode.py:app/services/picker/mode.py" \
  "/tmp/pg_snapshot.py:app/services/auction_snapshot.py" \
  "/tmp/pg_stocks.py:app/api/stocks.py"; do
  src="${pair%%:*}"; dst="$BK/${pair##*:}"
  if [ ! -s "$src" ]; then echo "STAGE MISSING/EMPTY: $src"; exit 1; fi
  cp -p "$src" "$dst" || exit 1
  echo "installed -> $dst"
done

echo
echo "########## 3. 哈希校验(期望值来自本地 git HEAD) ##########"
check_md5() {
  local want="$1" f="$BK/$2" got
  got=$(md5sum "$f" | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then printf 'OK   %s  %s\n' "$got" "$2"
  else printf 'FAIL %s (want %s)  %s\n' "$got" "$want" "$2"; return 1; fi
}
FAILED=0
check_md5 354fdcd5440aa178e69c58689668b850 app/services/picker/mode.py || FAILED=1
check_md5 7462ae63f7f1d17cebe039f169685ee8 app/services/auction_snapshot.py || FAILED=1
check_md5 fe3ef4a6628def03f5d1f6e61fcda729 app/api/stocks.py || FAILED=1
if [ "$FAILED" = "1" ]; then
  echo ">>> ABORT: 哈希不一致, 请回滚(未重启)"; exit 2
fi

echo
echo "########## 4. 语法编译检查 ##########"
/opt/kuaixuan-venv/bin/python -m py_compile \
  "$BK/app/services/picker/mode.py" \
  "$BK/app/services/auction_snapshot.py" \
  "$BK/app/api/stocks.py" && echo "py_compile OK" || {
  echo ">>> ABORT: 语法错误, 请回滚(未重启)"; exit 3; }

echo
echo "########## 5. 逻辑预检探针(只读, 打真库) ##########"
cd "$BK" && PYTHONPATH="$BK" /opt/kuaixuan-venv/bin/python /tmp/_probe_pg.py
RC=$?
echo "probe exit=$RC"
if [ "$RC" != "0" ]; then
  echo ">>> ABORT: 预检未通过, **未重启**, 请执行回滚: cp -p -r $BAK/* $BK/"
  exit 4
fi

echo
echo "########## STEP1 DONE — 预检通过, 等待人工确认后重启 ##########"
echo "回滚命令: cp -p -r $BAK/app $BK/"
echo "重启命令: systemctl restart kuaixuan kx-worker"
