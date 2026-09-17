#!/bin/bash
# 生产后端部署 Step1(④ 当日名单优先于跨日回退): 备份 -> 落盘 -> 哈希校验 -> 只读预检
#   **不重启**(预检不过即中止, 零用户影响)
# 改动面: app/api/stocks.py + app/services/history.py  (tests/ 不部署 —— 生产 venv 无 pytest)
# 幂等: 可重复执行(每次新建时间戳备份)
set -u

BK=/opt/kuaixuan/backend
TS=$(date +%Y%m%d-%H%M%S)
BAK=/opt/kuaixuan/backend_bak_todaysys_$TS

echo "########## 1. 备份 ##########"
mkdir -p "$BAK/app/api" "$BAK/app/services"
for f in app/api/stocks.py app/services/history.py; do
  if [ ! -f "$BK/$f" ]; then echo "MISSING: $BK/$f"; exit 1; fi
  cp -p "$BK/$f" "$BAK/$f" || exit 1
done
( cd "$BAK" && md5sum app/api/stocks.py app/services/history.py > MANIFEST.md5 )
echo "备份目录: $BAK"
cat "$BAK/MANIFEST.md5"
echo "$BAK" > /tmp/_tsys_bakdir

echo
echo "########## 2. 落盘(从 /tmp 暂存) ##########"
for pair in "tsys_stocks.py:app/api/stocks.py" "tsys_history.py:app/services/history.py"; do
  SRC="/tmp/${pair%%:*}"; DST="$BK/${pair#*:}"
  if [ ! -s "$SRC" ]; then echo "STAGE MISSING/EMPTY: $SRC"; exit 1; fi
  cp -p "$SRC" "$DST" || exit 1
  echo "installed -> $DST"
done

echo
echo "########## 3. 哈希校验(期望值来自本地 git 工作区) ##########"
FAIL=0
chk() { # chk <路径> <期望 md5>
  GOT=$(md5sum "$1" | cut -d' ' -f1)
  if [ "$GOT" = "$2" ]; then echo "OK   $GOT  $1"
  else echo "FAIL got=$GOT want=$2  $1"; FAIL=1; fi
}
chk "$BK/app/api/stocks.py"       "333951014728954c7c2562eebc4369c4"
chk "$BK/app/services/history.py" "e020b214f35c34a55056705aef4c1e10"
if [ "$FAIL" = "1" ]; then
  echo ">>> ABORT: 哈希不一致 —— 回滚: cp -p -r $BAK/app $BK/ && systemctl restart kuaixuan kx-worker"
  exit 2
fi

echo
echo "########## 4. 语法编译检查 ##########"
for f in app/api/stocks.py app/services/history.py; do
  /opt/kuaixuan-venv/bin/python -m py_compile "$BK/$f" && echo "py_compile OK  $f" || {
    echo ">>> ABORT: 语法错误 $f —— 回滚: cp -p -r $BAK/app $BK/ && systemctl restart kuaixuan kx-worker"; exit 3; }
done

echo
echo "########## 5. 只读逻辑预检(不写库/不重启/不签发 token) ##########"
cd "$BK" && PYTHONPATH="$BK" /opt/kuaixuan-venv/bin/python /tmp/_tsys_probe.py
RC=$?
echo "probe exit=$RC"
if [ "$RC" != "0" ]; then
  echo ">>> ABORT: 预检未通过, **未重启** —— 回滚: cp -p -r $BAK/app $BK/ && systemctl restart kuaixuan kx-worker"
  exit 4
fi

echo
echo "########## STEP1 DONE — 预检通过, 等待重启 ##########"
echo "回滚: cp -p -r $BAK/app $BK/ && systemctl restart kuaixuan kx-worker"
echo "重启: systemctl restart kuaixuan kx-worker"
