#!/bin/bash
# 生产后端部署 Step1(闸门 v2 前后端开关联动): 备份 -> 落盘 -> 哈希校验 -> 只读预检
#   **不重启**(预检不过即中止, 零用户影响)
# 改动面: 仅 app/api/stocks.py (tests/ 不部署 —— 生产 venv 无 pytest, 线上 tests 为陈货)
# 幂等: 可重复执行(每次新建时间戳备份)
set -u

BK=/opt/kuaixuan/backend
TS=$(date +%Y%m%d-%H%M%S)
BAK=/opt/kuaixuan/backend_bak_pickgatev2_$TS

echo "########## 1. 备份 ##########"
mkdir -p "$BAK/app/api"
if [ ! -f "$BK/app/api/stocks.py" ]; then echo "MISSING: $BK/app/api/stocks.py"; exit 1; fi
cp -p "$BK/app/api/stocks.py" "$BAK/app/api/stocks.py" || exit 1
( cd "$BAK" && md5sum app/api/stocks.py > MANIFEST.md5 )
echo "备份目录: $BAK"
cat "$BAK/MANIFEST.md5"
echo "$BAK" > /tmp/_pgv2_bakdir

echo
echo "########## 2. 落盘(从 /tmp 暂存) ##########"
if [ ! -s /tmp/pgv2_stocks.py ]; then echo "STAGE MISSING/EMPTY: /tmp/pgv2_stocks.py"; exit 1; fi
cp -p /tmp/pgv2_stocks.py "$BK/app/api/stocks.py" || exit 1
echo "installed -> $BK/app/api/stocks.py"

echo
echo "########## 3. 哈希校验(期望值来自本地 git 工作区) ##########"
WANT="06e8d5ff34f6c55c494525064c249ede"
GOT=$(md5sum "$BK/app/api/stocks.py" | cut -d' ' -f1)
if [ "$GOT" = "$WANT" ]; then
  echo "OK   $GOT  app/api/stocks.py"
else
  echo "FAIL got=$GOT want=$WANT"
  echo ">>> ABORT: 哈希不一致 —— 回滚: cp -p $BAK/app/api/stocks.py $BK/app/api/"
  exit 2
fi

echo
echo "########## 4. 语法编译检查 ##########"
/opt/kuaixuan-venv/bin/python -m py_compile "$BK/app/api/stocks.py" && echo "py_compile OK" || {
  echo ">>> ABORT: 语法错误 —— 回滚: cp -p $BAK/app/api/stocks.py $BK/app/api/"; exit 3; }

echo
echo "########## 5. 只读逻辑预检(不写库/不重启/不签发 token) ##########"
cd "$BK" && PYTHONPATH="$BK" /opt/kuaixuan-venv/bin/python /tmp/_pgv2_probe.py
RC=$?
echo "probe exit=$RC"
if [ "$RC" != "0" ]; then
  echo ">>> ABORT: 预检未通过, **未重启** —— 回滚: cp -p $BAK/app/api/stocks.py $BK/app/api/"
  exit 4
fi

echo
echo "########## STEP1 DONE — 预检通过, 等待重启 ##########"
echo "回滚: cp -p $BAK/app/api/stocks.py $BK/app/api/ && systemctl restart kuaixuan kx-worker"
echo "重启: systemctl restart kuaixuan kx-worker"
