#!/bin/bash
# 后端文件部署(两阶段通用, 生产/测试机共用) —— md5 逐字节校验 + 先落盘后重启。
#
#   bash _deploy_be.sh 1    备份 -> 落盘 -> 回读 md5 -> py_compile -> 预检(**不重启**)
#   bash _deploy_be.sh 2    重启 -> 服务/HTTP/日志验证
#
# 前置: 待部署文件已上传到 $KX_STAGE_DIR, **保持远端相对路径**
#         /tmp/_kx_be/app/api/stocks.py
#         /tmp/_kx_be/app/services/picker/mode.py
#
# 环境变量:
#   KX_STAGE      1|2 (或 $1)
#   KX_APP        /opt/kuaixuan
#   KX_STAGE_DIR  /tmp/_kx_be
#   KX_FILES      空格分隔的"相对路径:期望md5"列表(相对 $KX_APP/backend)
#   KX_TAG        备份目录标记(默认 deploy)
#   KX_VENV       覆盖 venv python 路径(默认自动探测)
#   KX_PREFLIGHT  可选: 落盘后要执行的只读预检命令
#
# 🔴 设计约定(踩坑记录):
#   - **预检在前、重启在后**: 预检不过即中止, 零用户影响;
#   - md5 用"上传值 vs 期望值 vs 落盘后回读值"三方比对, 防 sftp 半截文件;
#   - 远端脚本里 `%d/%s` 不能 % 格式化 → 一律字符串拼接;
#   - 生产 stocks.py 是 CRLF / history.py 是 LF —— 上传保持原样, 不要 tr。
set -u
STAGE="${KX_STAGE:-${1:-}}"
APP="${KX_APP:-/opt/kuaixuan}"
SD="${KX_STAGE_DIR:-/tmp/_kx_be}"
FILES="${KX_FILES:-}"
TAG="${KX_TAG:-deploy}"
PREFLIGHT="${KX_PREFLIGHT:-}"
BE="$APP/backend"

if [ -x "${KX_VENV:-/opt/kuaixuan-venv/bin/python}" ]; then PY="${KX_VENV:-/opt/kuaixuan-venv/bin/python}"
elif [ -x /opt/bid-venv/bin/python ]; then PY=/opt/bid-venv/bin/python
else PY=$(command -v python3 || command -v python); fi

case "$STAGE" in
  1|2) ;;
  *) echo "用法: bash $0 1|2"; exit 64 ;;
esac
# KX_FILES 只在 stage 1 用到; stage 2 是纯重启+验证, 不要求
[ "$STAGE" = "1" ] && [ -z "$FILES" ] && { echo ">>> ABORT: stage1 需要 KX_FILES"; exit 64; }

FAIL=0
chk() { # chk <描述> <实际> <期望>
  if [ "$2" = "$3" ]; then printf 'OK   %-40s %s\n' "$1" "$2"
  else printf 'FAIL %-40s got=%s want=%s\n' "$1" "$2" "$3"; FAIL=1; fi
}

# ============================ STAGE 1: 备份 + 落盘 + 预检 ============================
if [ "$STAGE" = "1" ]; then
  TS=$(date +%Y%m%d-%H%M%S)
  BAK="$APP/backend_bak_${TAG}_$TS"
  echo "########## B1-1 上传文件校验(暂存区) ##########"
  for item in $FILES; do
    rel="${item%%:*}"; want="${item##*:}"
    f="$SD/$rel"
    if [ ! -f "$f" ]; then echo "FAIL 暂存缺文件 $f"; FAIL=1; continue; fi
    got=$(md5sum "$f" | cut -d' ' -f1)
    chk "暂存 $rel" "$got" "$want"
  done
  [ "$FAIL" = "1" ] && { echo ">>> ABORT: 暂存校验未过, 未动线上"; exit 2; }

  echo
  echo "########## B1-2 备份 ##########"
  cp -a "$BE" "$BAK" || { echo ">>> ABORT: 备份失败"; exit 1; }
  echo "备份: $BAK"
  echo "$BAK" > /tmp/_kx_be_bakdir

  echo
  echo "########## B1-3 落盘(逐文件 md5 三方比对) ##########"
  for item in $FILES; do
    rel="${item%%:*}"; want="${item##*:}"
    mkdir -p "$BE/$(dirname "$rel")"
    cp -p "$SD/$rel" "$BE/$rel" || { echo "FAIL 写入失败 $rel"; FAIL=1; continue; }
    got=$(md5sum "$BE/$rel" | cut -d' ' -f1)
    chk "落盘 $rel" "$got" "$want"
  done
  [ "$FAIL" = "1" ] && { echo ">>> ABORT: 落盘校验未过, 请从 $BAK 回滚(尚未重启)"; exit 2; }

  echo
  echo "########## B1-4 py_compile ##########"
  for item in $FILES; do
    rel="${item%%:*}"
    case "$rel" in
      *.py) if "$PY" -m py_compile "$BE/$rel" 2>&1; then echo "OK   编译 $rel"
            else echo "FAIL 编译 $rel"; FAIL=1; fi ;;
    esac
  done

  echo
  echo "########## B1-5 预检(只读) ##########"
  if [ -n "$PREFLIGHT" ]; then
    bash -c "$PREFLIGHT" || { echo "FAIL 预检未通过"; FAIL=1; }
  else
    echo "(未指定 KX_PREFLIGHT, 跳过)"
  fi

  echo
  if [ "$FAIL" = "1" ]; then
    echo ">>> ABORT: 预检失败, **服务未重启**, 回滚: cp -a $BAK/. $BE/"; exit 2
  fi
  echo "########## B1 DONE — 落盘 + 预检通过, 等待 B2 重启 ##########"
  exit 0
fi

# ============================ STAGE 2: 重启 + 验证 ============================
echo "########## B2-1 重启 ##########"
before_pid=$(systemctl show kuaixuan -p MainPID --value 2>/dev/null)
systemctl restart kuaixuan kx-worker || { echo ">>> ABORT: 重启失败"; exit 1; }
sleep 4
after_pid=$(systemctl show kuaixuan -p MainPID --value 2>/dev/null)
echo "PID $before_pid -> $after_pid"

echo
echo "########## B2-2 服务状态 ##########"
for s in kuaixuan kx-worker; do
  st=$(systemctl is-active $s 2>&1)
  printf '  %-12s %s\n' "$s" "$st"
  [ "$st" = "active" ] || FAIL=1
done
systemctl show kuaixuan -p ActiveEnterTimestamp --value

echo
echo "########## B2-3 HTTP 探测 ##########"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/docs)
printf '  %-40s %s\n' "127.0.0.1:8010/docs" "$code"
code2=$(curl -s -o /dev/null -w '%{http_code}' -k https://127.0.0.1/)
printf '  %-40s %s\n' "https://127.0.0.1/ (首页)" "$code2"

echo
echo "########## B2-4 日志 ##########"
L="$APP/logs/app.log"
if [ -f "$L" ]; then
  echo "Traceback 计数: $(grep -c Traceback $L)"
  echo "--- 最近 8 行 ---"; tail -8 "$L"
fi

echo
[ "$FAIL" = "1" ] && { echo "########## B2 FAILED ##########"; exit 3; }
echo "########## B2 DONE — 重启 + 验证通过 ##########"
