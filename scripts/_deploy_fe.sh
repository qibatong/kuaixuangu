#!/bin/bash
# 前端 dist 部署(两阶段通用, 生产/测试机共用) —— 取代此前一版一写的硬编码脚本。
#
#   bash _deploy_fe.sh 1    解压到暂存目录 + 断言(**不换盘**); 失败即清理并中止
#   bash _deploy_fe.sh 2    同分区原子 rename 换盘 + 全量验证
#
# 环境变量(全部可选, 有默认值):
#   KX_STAGE      1|2            (也可用 $1)
#   KX_APP        /opt/kuaixuan
#   KX_TAR        /tmp/_kx_fe.tar.gz
#   KX_ENTRY      index-XXXX.js        期望的入口 chunk 名(不含 assets/ 前缀)
#   KX_OLD_ENTRY  index-YYYY.js        换盘后**不应**再出现的旧入口
#   KX_EXPECT_FILES / KX_EXPECT_ASSETS 产物规模断言(留空则跳过)
#   KX_NOT_PATTERNS  空格分隔, 整个产物**不得**出现的字符串
#   KX_HAVE_PATTERNS 空格分隔, 整个产物**必须**出现的字符串
#   KX_DIST_MARK     换盘后写入 /tmp 的标记文件名(默认 _kx_fe_newdir)
#
# 🔴 关键约定(踩坑记录):
#   - Windows 侧 tar 会带 666 权限 → nginx 拒绝服务, 这里强制 dir 755 / file 644;
#   - 必须**同分区 rename** 才原子(不要 cp -r 到线上目录);
#   - 脚本**容忍重放**: 解压→断言→rename 幂等, 中断后重跑不残留半张 dist;
#   - `curl -w '%{http_code}'` 在远端脚本里不能 % 格式化 → 一律字符串拼接。
set -u
STAGE="${KX_STAGE:-${1:-}}"
APP="${KX_APP:-/opt/kuaixuan}"
TAR="${KX_TAR:-/tmp/_kx_fe.tar.gz}"
ENTRY="${KX_ENTRY:-}"
OLD_ENTRY="${KX_OLD_ENTRY:-}"
EXP_FILES="${KX_EXPECT_FILES:-}"
EXP_ASSETS="${KX_EXPECT_ASSETS:-}"
NOT_PATTERNS="${KX_NOT_PATTERNS:-}"
HAVE_PATTERNS="${KX_HAVE_PATTERNS:-}"
MARK="${KX_DIST_MARK:-/tmp/_kx_fe_newdir}"

case "$STAGE" in
  1|2) ;;
  *) echo "用法: bash $0 1|2"; exit 64 ;;
esac

FAIL=0
chk() { # chk <描述> <实际> <期望>
  if [ "$2" = "$3" ]; then printf 'OK   %-30s %s\n' "$1" "$2"
  else printf 'FAIL %-30s got=%s want=%s\n' "$1" "$2" "$3"; FAIL=1; fi
}

# ============================ STAGE 1: 暂存 + 断言 ============================
if [ "$STAGE" = "1" ]; then
  TS=$(date +%Y%m%d-%H%M%S)
  NEW="$APP/dist.new_$TS"
  echo "########## S1-1 解压到暂存 ##########"
  rm -rf "$NEW"; mkdir -p "$NEW" || exit 1
  tar xzf "$TAR" -C "$NEW" --strip-components=1 || { echo ">>> ABORT: 解压失败"; rm -rf "$NEW"; exit 1; }
  echo "$NEW" > "$MARK"
  echo "暂存目录: $NEW"

  echo
  echo "########## S1-2 规模断言 ##########"
  [ -n "$EXP_FILES" ]  && chk "文件数" "$(find "$NEW" -type f | wc -l)" "$EXP_FILES"
  [ -n "$EXP_ASSETS" ] && chk "assets 数" "$(ls -1 "$NEW/assets" | wc -l)" "$EXP_ASSETS"

  echo
  echo "########## S1-3 入口断言 ##########"
  NEW_ENTRY=$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$NEW/index.html" | head -1 | sed 's#assets/##')
  echo "产物入口: $NEW_ENTRY"
  if [ -n "$ENTRY" ]; then
    chk "入口 hash" "$NEW_ENTRY" "$ENTRY"
    if [ -f "$NEW/assets/$ENTRY" ]; then echo "OK   入口文件存在 assets/$ENTRY"
    else echo "FAIL 入口文件缺失 assets/$ENTRY"; FAIL=1; fi
  fi
  for f in index.html favicon.ico; do
    [ -f "$NEW/$f" ] && echo "OK   存在 $f" || { echo "FAIL 缺失 $f"; FAIL=1; }
  done
  if [ -n "$OLD_ENTRY" ]; then
    if grep -q "$OLD_ENTRY" "$NEW/index.html"; then echo "FAIL index.html 仍引用旧入口 $OLD_ENTRY"; FAIL=1
    else echo "OK   index.html 不含旧入口"; fi
  fi

  echo
  echo "########## S1-4 内容断言(禁含 / 必备) ##########"
  NP=$(mktemp); HP=$(mktemp)
  [ -n "$NOT_PATTERNS" ] && printf '%s\n' $NOT_PATTERNS > "$NP"
  [ -n "$HAVE_PATTERNS" ] && printf '%s\n' $HAVE_PATTERNS > "$HP"
  if [ -s "$NP" ]; then
    HIT=$(grep -rlF -f "$NP" "$NEW" | wc -l)
    chk "禁含模式命中文件数" "$HIT" "0"
    [ "$HIT" != "0" ] && grep -rlF -f "$NP" "$NEW" | head -5 | sed 's/^/      命中: /'
  fi
  if [ -s "$HP" ]; then
    HIT2=$(grep -rlF -f "$HP" "$NEW" | wc -l)
    if [ "$HIT2" -gt 0 ]; then echo "OK   必备模式命中 $HIT2 个文件"
    else echo "FAIL 必备模式一个都没命中"; FAIL=1; fi
  fi
  rm -f "$NP" "$HP"

  echo
  echo "########## S1-5 权限修正(Windows tar 带 666) ##########"
  find "$NEW" -type d -exec chmod 755 {} +
  find "$NEW" -type f -exec chmod 644 {} +
  chk "目录权限样本" "$(stat -c '%a' "$NEW/assets")" "755"
  chk "文件权限样本" "$(stat -c '%a' "$NEW/index.html")" "644"

  echo
  echo "########## S1-6 与线上现产物差异 ##########"
  diff <(ls -1 "$APP/dist/assets" 2>/dev/null | sort) <(ls -1 "$NEW/assets" | sort) \
    | grep -c '^[<>]' | xargs echo "资产名差异行数:"
  diff <(ls -1 "$APP/dist/assets" 2>/dev/null | sort) <(ls -1 "$NEW/assets" | sort) | head -12

  echo
  if [ "$FAIL" = "1" ]; then
    echo ">>> ABORT: 断言未通过, 清理 $NEW, **线上 dist 未动**"; rm -rf "$NEW"; rm -f "$MARK"; exit 2
  fi
  echo "########## S1 DONE — 断言全通过, 等待 S2 原子换盘 ##########"
  exit 0
fi

# ============================ STAGE 2: 原子换盘 + 验证 ============================
if [ ! -f "$MARK" ]; then echo ">>> ABORT: 找不到暂存标记 $MARK, 请先跑 stage 1"; exit 1; fi
NEW=$(cat "$MARK")
[ -d "$NEW" ] || { echo ">>> ABORT: 暂存目录不存在 $NEW"; exit 1; }

echo "########## S2-1 备份线上 dist ##########"
TS=$(date +%Y%m%d-%H%M%S)
BAK="$APP/dist_bak_$(date +%Y%m%d-%H%M%S)"
if [ -d "$APP/dist" ]; then
  cp -a "$APP/dist" "$BAK" || { echo ">>> ABORT: 备份失败"; exit 1; }
  echo "备份: $BAK"
  echo "$BAK" > /tmp/_kx_fe_bakdir
else
  echo "线上无 dist(首次部署)"
fi

echo
echo "########## S2-2 同分区原子 rename ##########"
# 必须同分区: dist.new_xxx 与 dist 都在 $APP 下 → rename 原子
mv "$APP/dist" "$APP/dist.old_$TS" 2>/dev/null || true
mv "$NEW" "$APP/dist" || { echo ">>> ABORT: rename 失败, 尝试回滚"; mv "$APP/dist.old_$TS" "$APP/dist" 2>/dev/null; exit 1; }
rmdir "$APP/dist.old_$TS" 2>/dev/null || rm -rf "$APP/dist.old_$TS"
rm -f "$MARK"
echo "换盘完成 -> $APP/dist"

echo
echo "########## S2-3 换盘后验证 ##########"
FAIL=0
chk "文件数" "$(find "$APP/dist" -type f | wc -l)" "${EXP_FILES:-$(find "$APP/dist" -type f | wc -l)}"
ACT=$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$APP/dist/index.html" | head -1 | sed 's#assets/##')
[ -n "$ENTRY" ] && chk "线上入口 hash" "$ACT" "$ENTRY"
chk "index.html 权限" "$(stat -c '%a' "$APP/dist/index.html")" "644"

echo
echo "--- nginx 配置检查 ---"
nginx -t 2>&1 | tail -2

echo
echo "--- HTTP 探测(本机) ---"
# ⚠️ 探测基础地址按**实际监听端口**选: 测试机 nginx **只监听 80**(无 443) →
#    默认走 https 会全部拿 000 假失败(2026-09-20 踩坑)。生产机有 443 时再覆盖 KX_PROBE_BASE=https://127.0.0.1
#    另: nginx 对 80 做 301 跳转的部署, 探测 80 会得 301 → 那种机器才需要 https。
BASE="${KX_PROBE_BASE:-http://127.0.0.1}"
for u in / /index.html; do
  code=$(curl -sk -o /dev/null -w '%{http_code}' "$BASE$u")
  printf '  %-40s %s\n' "$u" "$code"
done
if [ -n "$ACT" ]; then
  code=$(curl -sk -o /dev/null -w '%{http_code}' "$BASE/assets/$ACT")
  printf '  %-40s %s  (新入口, 期望 200)\n' "/assets/$ACT" "$code"
  [ "$code" = "200" ] || FAIL=1
fi
if [ -n "$OLD_ENTRY" ]; then
  code=$(curl -sk -o /dev/null -w '%{http_code}' "$BASE/assets/$OLD_ENTRY")
  printf '  %-40s %s  (旧入口, 期望 404)\n' "/assets/$OLD_ENTRY" "$code"
  [ "$code" = "404" ] || echo "   ⚠️ 旧入口非 404(非致命: 可能被 nginx/浏览器缓存)"
fi

echo
echo "--- 服务状态 ---"
for s in kuaixuan kx-worker; do printf '  %-12s %s\n' "$s" "$(systemctl is-active $s 2>&1)"; done

echo
[ "$FAIL" = "1" ] && { echo "########## S2 FAILED ##########"; exit 3; }
echo "########## S2 DONE — 换盘 + 验证通过 ##########"
