#!/usr/bin/env bash
# dist_gc.sh —— 线上 dist「历史 chunk」垃圾回收（可逆：mv 到 holding 目录，绝不删除）
#
# 来历（2026-09-27 实测）：并发会话用临时脚本把 tar 包 **解压进线上 dist**（而非整目录替换），
# 于是每次部署都留下上一版的文件。当日 8 次构建后 dist/assets 有 310 个 js/css，
# 而从 index.html 出发的引用图只可达 68 个 ⇒ 242 个（9.4MB）永不加载。
# 详见 AGENTS.md 发布纪律第 9 条。
#
# 用法：
#   bash dist_gc.sh report          # 只打印清单（默认）
#   bash dist_gc.sh apply           # 执行（mv 到 /opt/kuaixuan/_dist_garbage_<ts>/）
#   KX_DIST=/tmp/other bash dist_gc.sh report
#
# 判据：从 <dist>/index.html 出发，沿 js/css 文本里出现的文件名做**闭包遍历**；
#       可达集合之外的 js/css 视为垃圾（字体/图片不参与，天然不会被误删）。
# 安全闸：可达集合 < 40 时**中止**（防"解析写错 ⇒ 把真文件判成垃圾"）。
# 注：本脚本只处理 `assets/` 下的 .js/.css；`._*` AppleDouble 由 kx_purge_appledouble.sh 负责。

set -u
MODE="${1:-report}"
D="${KX_DIST:-/opt/kuaixuan/dist}"
TS=$(date +%Y%m%d-%H%M%S)
HOLD="$(dirname "$D")/_dist_garbage_$TS"

echo "########## dist_gc | mode=$MODE | dist=$D ##########"
python3 - "$MODE" "$D" "$HOLD" <<'PY'
import os, re, sys, shutil
mode, D, hold = sys.argv[1], sys.argv[2], sys.argv[3]
assets = os.path.join(D, "assets")
html_p = os.path.join(D, "index.html")
if not os.path.isdir(assets):
    print("ABORT: %s missing" % assets); sys.exit(9)
if not os.path.exists(html_p):
    print("ABORT: index.html missing"); sys.exit(9)

names = [n for n in os.listdir(assets) if n.endswith((".js", ".css"))]
seed = set(re.findall(r'assets/([A-Za-z0-9_.-]+\.(?:js|css))',
                      open(html_p, encoding="utf-8", errors="replace").read()))
seen, stack = set(), list(seed)
for s in seed:
    if s in names:
        seen.add(s)
while stack:
    n = stack.pop()
    p = os.path.join(assets, n)
    if not os.path.exists(p):
        continue
    try:
        t = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    for m in re.findall(r'([A-Za-z0-9_.-]+\.(?:js|css))', t):
        if m in names and m not in seen:
            seen.add(m); stack.append(m)

stale = sorted(set(names) - seen)
print("assets js/css total   : %d" % len(names))
print("reachable (live set)  : %d" % len(seen))
print("stale (unreachable)   : %d" % len(stale))
if len(seen) < 40:
    print("ABORT: live set too small (%d) -- reference graph may be mis-parsed" % len(seen))
    sys.exit(9)
if not stale:
    print("nothing to do"); sys.exit(0)
print("stale sample          : %s" % ", ".join(stale[:8]))
payload = sum(os.path.getsize(os.path.join(assets, s)) for s in stale)
print("stale payload         : %.1f MB" % (payload / 1048576.0))
ent = re.findall(r'assets/(index-[A-Za-z0-9_-]+\.js)', open(html_p, encoding="utf-8",
                                                           errors="replace").read())
print("live entry            : %s" % (ent[0] if ent else "?"))
if mode != "apply":
    print("[REPORT ONLY] rerun with: apply")
    sys.exit(0)

os.makedirs(os.path.join(hold, "assets"), exist_ok=True)
moved = 0
for s in stale:
    src = os.path.join(assets, s)
    if not os.path.abspath(src).startswith(os.path.abspath(assets) + os.sep):
        print("ABORT: path escape %s" % src); sys.exit(9)
    try:
        shutil.move(src, os.path.join(hold, "assets", s)); moved += 1
    except Exception as e:
        print("move failed: %s %s" % (s, e))
print("moved to holding      : %d" % moved)
print("HOLD=%s" % hold)
print("assets js/css after   : %d" % len([n for n in os.listdir(assets)
                                        if n.endswith((".js", ".css"))]))
print("rollback              : mv %s/assets/* %s/assets/" % (hold, D))
PY

echo "===== post-check ====="
echo -n "dist total files : "; find "$D" -type f | wc -l
echo -n "dot_under        : "; find "$D" -type f -name '._*' | wc -l
echo -n "index.html md5   : "; md5sum "$D/index.html" | cut -d' ' -f1
ENT=$(grep -oE 'assets/index-[A-Za-z0-9_-]+\.js' "$D/index.html" | head -1)
echo "live entry       : $ENT"
echo -n "http index       : "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
echo -n "http entry       : "; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1/$ENT"
echo "########## DONE ##########"
