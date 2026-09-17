#!/bin/bash
# 生产前端 Step F1(闸门 v2: 置灰改由后端开关驱动): 暂存解压 + 断言(**不换** dist)
# 通过后由 F2 做同分区原子 rename。失败即清理暂存并中止。
set -u
TS=$(date +%Y%m%d-%H%M%S)
NEW=/opt/kuaixuan/dist.new_$TS
TAR=/tmp/_dist_pg.tar.gz

echo "########## 1. 解压到暂存 ##########"
rm -rf "$NEW"; mkdir -p "$NEW" || exit 1
tar xzf "$TAR" -C "$NEW" --strip-components=1 || { echo ">>> ABORT: 解压失败"; rm -rf "$NEW"; exit 1; }
echo "$NEW" > /tmp/_pg_newdir
echo "暂存目录: $NEW"

echo
echo "########## 2. 断言 ##########"
FAIL=0
chk() { # chk <描述> <实际> <期望>
  if [ "$2" = "$3" ]; then printf 'OK   %-34s %s\n' "$1" "$2"
  else printf 'FAIL %-34s got=%s want=%s\n' "$1" "$2" "$3"; FAIL=1; fi
}
chk "文件数"    "$(find "$NEW" -type f | wc -l)" "1021"
chk "目录数"    "$(find "$NEW" -type d | wc -l)" "2"
chk "assets 数" "$(ls -1 "$NEW/assets" | wc -l)" "1016"
for f in index.html favicon.ico favicon.png logo.jpg logo.png \
         assets/index-IiW_cnAG.js assets/StockView-CZwVt7Nf.js \
         assets/StockView-D-A6G8Km.css assets/stocks-CRjwpram.js; do
  if [ -f "$NEW/$f" ]; then printf 'OK   存在 %s\n' "$f"; else printf 'FAIL 缺失 %s\n' "$f"; FAIL=1; fi
done
# index.html 必须引用新 entry
if grep -q 'index-IiW_cnAG.js' "$NEW/index.html"; then echo "OK   index.html 引用新 entry"
else echo "FAIL index.html 未引用新 entry"; FAIL=1; fi
if grep -q 'index-0LujzWyt.js' "$NEW/index.html"; then echo "FAIL index.html 仍引用旧 entry"; FAIL=1
else echo "OK   index.html 不含旧 entry"; fi

# ---- 核心: 开关驱动标记(9/17 事故的修复本体) ----
if grep -q 'pickGateEnabled' "$NEW/assets/stocks-CRjwpram.js"; then
  echo "OK   stocks chunk 含 pickGateEnabled(读后端开关)"
else echo "FAIL stocks chunk 缺 pickGateEnabled —— 前端仍是写死时间闸门!"; FAIL=1; fi
if grep -q 'action:"ping"' "$NEW/assets/index-IiW_cnAG.js"; then
  echo "OK   index chunk 含 action:ping(开关探测)"
else echo "FAIL index chunk 缺 action:ping —— 探测未打进产物"; FAIL=1; fi
# 置灰文案仍在(开关开启时才有用)
for f in assets/StockView-CZwVt7Nf.js assets/stocks-CRjwpram.js; do
  if grep -q 'pickBlocked' "$NEW/$f"; then echo "OK   $f 含 pickBlocked"
  else echo "FAIL $f 不含 pickBlocked"; FAIL=1; fi
done

echo
echo "########## 3. 权限修正(Windows tar 风险) ##########"
find "$NEW" -type d -exec chmod 755 {} +
find "$NEW" -type f -exec chmod 644 {} +
chk "目录权限样本" "$(stat -c '%a' "$NEW/assets")" "755"
chk "文件权限样本" "$(stat -c '%a' "$NEW/index.html")" "644"
chk "属主样本"     "$(stat -c '%U:%G' "$NEW/index.html")" "root:root"

echo
echo "########## 4. 与线上现产物差异 ##########"
OLD=/opt/kuaixuan/dist
diff <(ls -1 "$OLD/assets" | sort) <(ls -1 "$NEW/assets" | sort) | grep -c '^[<>]' | xargs echo "资产名差异行数:"
echo "--- 差异明细(最多 20 行) ---"
diff <(ls -1 "$OLD/assets" | sort) <(ls -1 "$NEW/assets" | sort) | head -20

echo
if [ "$FAIL" = "1" ]; then
  echo ">>> ABORT: 断言未通过, 清理暂存 $NEW, **线上 dist 未动**"
  rm -rf "$NEW"; exit 2
fi
echo "########## F1 DONE — 断言全通过, 等待 F2 原子换 ##########"
